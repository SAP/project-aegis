"""Default definitions, entity types, and regex patterns for :class:`PiiSanitizer`."""

from __future__ import annotations

import enum
import re
from typing import Final


class PiiEntity(enum.StrEnum):
    """Supported Personally Identifiable Information (PII) entity types."""

    EMAIL = "EMAIL"
    PHONE = "PHONE"
    CREDIT_CARD = "CREDIT_CARD"
    IBAN = "IBAN"
    SSN = "SSN"
    IPV4 = "IPV4"

    def __str__(self) -> str:
        return self.value


DEFAULT_PII_ENTITIES: Final[frozenset[PiiEntity]] = frozenset(
    {
        PiiEntity.EMAIL,
        PiiEntity.PHONE,
        PiiEntity.CREDIT_CARD,
        PiiEntity.IBAN,
        PiiEntity.SSN,
        PiiEntity.IPV4,
    }
)

# Email pattern: Standard RFC-compliant address matching with domain label constraints.
EMAIL_REGEX: Final[re.Pattern[str]] = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Phone pattern: Covers international E.164 and common national telephone formats
# (e.g. +1-555-0199, +359 889 700 004, (555) 123-4567, 555-123-4567, 555.123.4567).
# Disallows general dotted quadruples to avoid false matches on IP/version strings.
PHONE_REGEX: Final[re.Pattern[str]] = re.compile(
    r"(?:\+\d{1,3}[-\s]?)?(?:\(\d{2,4}\)[-\s]?|\d{2,4}[-\s])?\d{3,4}[-\s]\d{3,4}\b"
    r"|\b\d{3}[-.]\d{3}[-.]\d{4}\b"
    r"|\+\d{1,3}[-\s]?\d{1,4}[-\s]?\d{3,4}[-\s]?\d{3,4}\b"
)

# Credit card candidate pattern: 13 to 19 digits formatted with spaces or hyphens.
# Validated strictly through the Luhn checksum to prevent false positives on general numbers.
CREDIT_CARD_CANDIDATE_REGEX: Final[re.Pattern[str]] = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{1,4}\b|\b\d{13,19}\b")

# IBAN pattern: 2 letter country code, 2 check digits, followed by up to 30 alphanumeric characters.
IBAN_REGEX: Final[re.Pattern[str]] = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b")

# US SSN format: 3-2-4 digits separated by hyphens or spaces.
SSN_REGEX: Final[re.Pattern[str]] = re.compile(r"\b(?!000|666|9\d{2})\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}\b")

# IPv4 address: Standard dotted decimal notation (0.0.0.0 to 255.255.255.255).
IPV4_REGEX: Final[re.Pattern[str]] = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)


def is_luhn_valid(candidate: str) -> bool:
    """Verify numeric string passes the Luhn algorithm (mod 10 checksum).

    Ensures credit card candidate strings are authentic card numbers and not
    random serial or order IDs.
    """
    digits = [int(c) for c in candidate if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0
