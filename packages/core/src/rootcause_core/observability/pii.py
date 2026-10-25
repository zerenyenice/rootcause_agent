"""Regex pass replacing common personal data in free text before it reaches a trace.

Covers e-mail addresses, Turkish and US phone numbers, IBANs and 11-digit Turkish
national ids. It is a best-effort net for model and user text (PROJECT.md
section 7); it cannot catch paraphrased or unstructured personal data, which is
why tool outputs are denied by default in :mod:`rootcause_core.observability.masking`.

False-positive guard: every pattern is anchored on non-word boundaries, so ids,
hex trace ids, UUIDs, dates (``2026-10-14``), versions and ordinary counts are
left alone. A national id is exactly 11 digits, not starting with ``0``, and not
part of a longer number or a decimal.
"""

import re
from typing import Final

EMAIL_MARKER: Final = "<email>"
PHONE_MARKER: Final = "<phone>"
IBAN_MARKER: Final = "<iban>"
NATIONAL_ID_MARKER: Final = "<national-id>"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Two-letter country code, two check digits, then 11..30 alphanumerics in groups of
# four (optionally space separated) with a short tail.
_IBAN = re.compile(r"(?<!\w)[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?(?!\w)")
# Turkish mobile numbers: optional +90 / 0 prefix, 5xx, then 3-2-2 digits.
_PHONE_TR = re.compile(
    r"(?<![\w+])(?:\+90[\s-]?|0)?\(?5\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?![\w])"
)
# North American numbers: optional +1, (area) or area, then 3-4 with a separator.
_PHONE_US = re.compile(
    r"(?<![\w+])(?:\+1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]\d{4}(?![\w])"
)
_NATIONAL_ID = re.compile(r"(?<![\w.])[1-9]\d{10}(?!\w)(?!\.\d)")

_PATTERNS: Final = (
    (_EMAIL, EMAIL_MARKER),
    (_IBAN, IBAN_MARKER),
    (_PHONE_TR, PHONE_MARKER),
    (_PHONE_US, PHONE_MARKER),
    (_NATIONAL_ID, NATIONAL_ID_MARKER),
)


def redact_pii(text: str) -> str:
    """Return ``text`` with e-mails, phone numbers, IBANs and national ids replaced."""
    cleaned = text
    for pattern, marker in _PATTERNS:
        cleaned = pattern.sub(marker, cleaned)
    return cleaned
