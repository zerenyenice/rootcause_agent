"""Regex pass replacing common personal data in free text before it reaches a trace.

Covers e-mail addresses, Turkish and US phone numbers, IBANs and 11-digit Turkish
national ids. It is a best-effort net for model and user text (PROJECT.md
section 7); it cannot catch paraphrased or unstructured personal data, which is
why tool outputs are denied by default in :mod:`rootcause.observability.masking`.

False-positive guard: every pattern is anchored on non-word boundaries, so ids,
hex trace ids, UUIDs, dates (``2026-10-14``), versions and ordinary counts are
left alone. A national id is exactly 11 digits, not starting with ``0``, and not
part of a longer number or a decimal (deliberately no TCKN checksum: a mistyped
id is still personal data). IBANs must pass the mod-97 check.

Every pattern is bounded, so the pass is linear in the text length; the mask
additionally drops strings over 32 KiB before they reach this pass.
"""

import re
from collections.abc import Callable
from typing import Final

EMAIL_MARKER: Final = "<email>"
PHONE_MARKER: Final = "<phone>"
IBAN_MARKER: Final = "<iban>"
NATIONAL_ID_MARKER: Final = "<national-id>"

# Bounded and Unicode-aware (``\w`` matches Turkish letters such as ``ş``/``ö``):
# every repetition has an upper bound, so a long run of e-mail characters without
# an ``@`` costs linear time instead of quadratic backtracking.
_EMAIL = re.compile(r"[\w.%+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){0,8}\.[^\W\d_]{2,63}")
# Two-letter country code, two check digits, then 11..30 alphanumerics in groups of
# four (optionally space separated) with a short tail; kept only when the ISO 13616
# mod-97 check holds, so SQL literals such as 'TR34 ABCD 1234' are left alone.
_IBAN = re.compile(r"(?<!\w)[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?(?!\w)")
# Turkish mobile numbers: optional +90 / 0 prefix, 5xx (parenthesised only as a
# balanced pair, so a lone "(" before the number is never consumed), then 3-2-2.
_PHONE_TR = re.compile(
    r"(?<![\w+])(?:\+90[\s-]?)?(?:\(0?5\d{2}\)|0?5\d{2})[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\w)"
)
# North American numbers: optional +1, (area) or area, then 3-4 with a separator.
_PHONE_US = re.compile(r"(?<![\w+])(?:\+1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]\d{4}(?!\w)")
_NATIONAL_ID = re.compile(r"(?<![\w.])[1-9]\d{10}(?!\w)(?!\.\d)")
IBAN_MODULUS: Final = 97
IBAN_REMAINDER: Final = 1
IBAN_HEADER_LENGTH: Final = 4
# The shortest national IBAN format (Norway) has 15 characters.
IBAN_MIN_LENGTH: Final = 15


def _is_valid_iban(candidate: str) -> bool:
    """ISO 13616 check: minimum length, header moved to the end, letters as 10..35, mod 97."""
    compact = candidate.replace(" ", "")
    if len(compact) < IBAN_MIN_LENGTH:
        return False
    rearranged = compact[IBAN_HEADER_LENGTH:] + compact[:IBAN_HEADER_LENGTH]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % IBAN_MODULUS == IBAN_REMAINDER


def _iban_marker(match: re.Match[str]) -> str:
    """Mask the match when it, or a prefix ending at a space, is a valid IBAN.

    The prefix check covers a spaced IBAN followed by an unrelated short group.
    """
    groups = match.group(0).split(" ")
    prefixes = (" ".join(groups[:end]) for end in range(len(groups), 0, -1))
    return IBAN_MARKER if any(map(_is_valid_iban, prefixes)) else match.group(0)


Replacement = str | Callable[[re.Match[str]], str]

# The e-mail pattern runs separately (only when the text contains an "@").
_PATTERNS: Final[tuple[tuple[re.Pattern[str], Replacement], ...]] = (
    (_IBAN, _iban_marker),
    (_PHONE_TR, PHONE_MARKER),
    (_PHONE_US, PHONE_MARKER),
    (_NATIONAL_ID, NATIONAL_ID_MARKER),
)


def redact_pii(text: str) -> str:
    """Return ``text`` with e-mails, phone numbers, IBANs and national ids replaced."""
    cleaned = _EMAIL.sub(EMAIL_MARKER, text) if "@" in text else text
    for pattern, replacement in _PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned
