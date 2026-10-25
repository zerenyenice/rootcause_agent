"""Encryption at rest for connection credentials (Fernet, key from ``ROOTCAUSE_SECRET_KEY``).

Neither the key nor a plaintext ever appears in an exception raised here: failures
name the environment variable only, and the cryptography exceptions (whose context
could carry inputs) are never chained.
"""

from cryptography.fernet import Fernet, InvalidToken

from rootcause.errors import ConfigurationError, RootCauseError
from rootcause.settings import ENV_PREFIX, Settings, is_fernet_key

SECRET_KEY_VARIABLE = f"{ENV_PREFIX}SECRET_KEY"
_ENCODING = "utf-8"


class SecretDecryptionError(RootCauseError):
    """A stored secret could not be decrypted (wrong key, or a corrupted token)."""

    code = "secret_decryption_failed"
    public_message = "A stored credential could not be decrypted."

    def __init__(self) -> None:
        super().__init__(
            f"A stored credential could not be decrypted with {SECRET_KEY_VARIABLE}; "
            "the key changed or the token is corrupted."
        )


def _invalid_key() -> ConfigurationError:
    return ConfigurationError(
        f"{SECRET_KEY_VARIABLE} must be a Fernet key (32 url-safe base64-encoded bytes).",
        variables=(SECRET_KEY_VARIABLE,),
    )


class SecretCipher:
    """Encrypts and decrypts short secrets (passwords) with one Fernet key."""

    __slots__ = ("_fernet",)

    def __init__(self, key: str) -> None:
        if not is_fernet_key(key):
            raise _invalid_key()
        self._fernet = Fernet(key.encode(_ENCODING))

    def __repr__(self) -> str:
        return "SecretCipher(key=<redacted>)"

    def encrypt(self, plaintext: str) -> bytes:
        """A fresh Fernet token for ``plaintext`` (random IV: never the same twice)."""
        return self._fernet.encrypt(plaintext.encode(_ENCODING))

    def decrypt(self, token: bytes) -> str:
        """The plaintext of ``token``.

        Raises:
            SecretDecryptionError: the token is malformed or was made with another key.
        """
        try:
            return self._fernet.decrypt(token).decode(_ENCODING)
        except (InvalidToken, ValueError, TypeError):
            pass
        # Raised outside the handler so no cryptography exception rides along as context.
        raise SecretDecryptionError()


def cipher_from_settings(settings: Settings) -> SecretCipher:
    """The cipher for the configured ``ROOTCAUSE_SECRET_KEY``.

    Raises:
        ConfigurationError: the key is not configured (or not a Fernet key).
    """
    if settings.secret_key is None:
        raise ConfigurationError(
            f"{SECRET_KEY_VARIABLE} is not set; it is required to store and use "
            "connection credentials.",
            variables=(SECRET_KEY_VARIABLE,),
        )
    return SecretCipher(settings.secret_key.get_secret_value())
