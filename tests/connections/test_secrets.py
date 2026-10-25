"""Connection credential encryption at rest (Fernet, key from ROOTCAUSE_SECRET_KEY) (RED)."""

import os
import re
import tomllib
import traceback
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from rootcause.connections.secrets import SecretCipher, SecretDecryptionError, cipher_from_settings
from rootcause.errors import ConfigurationError, RootCauseError
from rootcause.settings import Settings, clear_settings_cache, get_settings, secret_values

pytestmark = pytest.mark.unit

KEY_VARIABLE = "ROOTCAUSE_SECRET_KEY"
PASSWORD = "SENTINEL-pw-123"  # gitleaks:allow
BAD_KEY = "SENTINEL-not-a-fernet-key"  # gitleaks:allow
DATABASE_URL = "postgresql://u:SENTINEL-db-pw@127.0.0.1:1/db"  # gitleaks:allow
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in [key for key in os.environ if key.upper().startswith("ROOTCAUSE_")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", DATABASE_URL)
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode("ascii")


def _formatted(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc))


def test_encrypt_then_decrypt_round_trips(key: str) -> None:
    cipher = SecretCipher(key)

    token = cipher.encrypt(PASSWORD)

    assert isinstance(token, bytes)
    assert cipher.decrypt(token) == PASSWORD


@pytest.mark.parametrize("plaintext", ["", "ü-🔑-'; DROP TABLE x; --", "p" * 4096])
def test_round_trip_handles_edge_case_plaintexts(key: str, plaintext: str) -> None:
    cipher = SecretCipher(key)

    assert cipher.decrypt(cipher.encrypt(plaintext)) == plaintext


def test_ciphertext_never_contains_the_plaintext_and_is_standard_fernet(key: str) -> None:
    token = SecretCipher(key).encrypt(PASSWORD)

    assert PASSWORD.encode() not in token
    assert Fernet(key.encode()).decrypt(token).decode() == PASSWORD


def test_encrypting_twice_gives_different_tokens(key: str) -> None:
    cipher = SecretCipher(key)

    assert cipher.encrypt(PASSWORD) != cipher.encrypt(PASSWORD)


def test_decrypt_with_another_key_raises_without_leaking(key: str) -> None:
    token = SecretCipher(key).encrypt(PASSWORD)
    other = SecretCipher(Fernet.generate_key().decode("ascii"))

    with pytest.raises(SecretDecryptionError) as caught:
        other.decrypt(token)

    assert isinstance(caught.value, RootCauseError)
    assert PASSWORD not in _formatted(caught.value)
    assert key not in _formatted(caught.value)


@pytest.mark.parametrize("token", [b"", b"garbage", b"gAAAAA" + b"x" * 80])
def test_decrypt_of_malformed_token_raises_secret_decryption_error(key: str, token: bytes) -> None:
    with pytest.raises(SecretDecryptionError):
        SecretCipher(key).decrypt(token)


@pytest.mark.parametrize("bad", [BAD_KEY, "", "c2hvcnQ="])
def test_cipher_rejects_an_invalid_key_naming_the_variable_only(bad: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        SecretCipher(bad)

    assert KEY_VARIABLE in str(caught.value)
    assert KEY_VARIABLE in caught.value.variables
    if bad:
        assert bad not in _formatted(caught.value)


def test_cipher_does_not_expose_the_key_in_repr(key: str) -> None:
    assert key not in repr(SecretCipher(key))


def test_settings_reads_secret_key_as_a_secret(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setenv(KEY_VARIABLE, key)

    settings = get_settings()

    assert isinstance(settings.secret_key, SecretStr)
    assert settings.secret_key.get_secret_value() == key
    assert key not in repr(settings)
    assert key in secret_values(settings)


def test_settings_secret_key_defaults_to_none() -> None:
    assert get_settings().secret_key is None


def test_settings_reject_an_invalid_secret_key_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_VARIABLE, BAD_KEY)

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert KEY_VARIABLE in str(caught.value)
    assert BAD_KEY not in _formatted(caught.value)


def test_cipher_from_settings_uses_the_configured_key(key: str) -> None:
    settings = Settings(database_url=SecretStr(DATABASE_URL), secret_key=SecretStr(key))

    token = cipher_from_settings(settings).encrypt(PASSWORD)

    assert Fernet(key.encode()).decrypt(token).decode() == PASSWORD


def test_cipher_from_settings_without_key_raises_configuration_error_naming_variable() -> None:
    settings = Settings(database_url=SecretStr(DATABASE_URL))

    with pytest.raises(ConfigurationError) as caught:
        cipher_from_settings(settings)

    assert KEY_VARIABLE in str(caught.value)
    assert caught.value.variables == (KEY_VARIABLE,)


def test_cryptography_is_a_direct_runtime_dependency() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    names = [
        re.split(r"[\s\[<>=!~;]", dep, maxsplit=1)[0].lower()
        for dep in project["project"]["dependencies"]
    ]

    assert "cryptography" in names


def _is_fernet_key(value: str) -> bool:
    try:
        Fernet(value.encode())
    except ValueError:
        return False
    return True


def test_env_example_documents_the_secret_key_without_a_usable_key() -> None:
    text = (REPO_ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")

    assert KEY_VARIABLE in text
    for line in text.splitlines():
        name, _, value = line.lstrip("# ").partition("=")
        if name == KEY_VARIABLE:
            assert not _is_fernet_key(value.strip())
