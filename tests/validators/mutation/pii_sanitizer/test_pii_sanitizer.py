"""Tests for :class:`PiiSanitizer`.

Runs with ``asyncio_mode = "auto"`` and exercises both detect-only (:class:`Guard`)
and detect-and-fix (:class:`MutationGuard`) lifecycles.
"""

from __future__ import annotations

from aegis import ContentItem, ContentType, ContentView, Guard, MutationGuard
from aegis.validators.mutation import PiiEntity, PiiSanitizer


def _view(*values: str) -> ContentView:
    """Build a multi-part view with stable ids ``p0``, ``p1``, ..."""
    return ContentView(
        items=tuple(ContentItem(id=f"p{i}", type=ContentType.TEXT, value=v) for i, v in enumerate(values))
    )


# ---------------------------------------------------------------------------
# Happy path — clean content passes untouched
# ---------------------------------------------------------------------------


async def test_clean_text_passes_unchanged():
    text = "The quick brown fox jumps over the lazy dog."
    result = await MutationGuard("t").use(PiiSanitizer()).validate(text)
    assert result.was_mutated is False
    assert result.output == text


async def test_empty_string_and_whitespace():
    guard = MutationGuard("t").use(PiiSanitizer())
    res1 = await guard.validate("")
    assert res1.was_mutated is False
    assert res1.output == ""

    res2 = await guard.validate("   \n\t  ")
    assert res2.was_mutated is False
    assert res2.output == "   \n\t  "


async def test_empty_view():
    result = await MutationGuard("t").use(PiiSanitizer()).validate(ContentView(items=()))
    assert result.was_mutated is False


# ---------------------------------------------------------------------------
# Entity-specific redactions
# ---------------------------------------------------------------------------


async def test_email_redaction():
    text = "Send reports to user.name+tag@sub.domain.org or contact@example.com."
    result = await MutationGuard("t").use(PiiSanitizer()).validate(text)
    assert result.was_mutated is True
    assert result.output == "Send reports to <EMAIL> or <EMAIL>."


async def test_ipv4_redaction():
    text = "Connecting to 192.168.1.1 and 10.0.0.254 for cluster nodes."
    result = await MutationGuard("t").use(PiiSanitizer()).validate(text)
    assert result.was_mutated is True
    assert result.output == "Connecting to <IPV4> and <IPV4> for cluster nodes."


async def test_invalid_ip_not_redacted():
    text = "Version 999.999.999.999 is invalid."
    result = await MutationGuard("t").use(PiiSanitizer()).validate(text)
    assert result.was_mutated is False
    assert result.output == text


async def test_ssn_redaction():
    text = "Taxpayer SSN is 123-45-6789 on the file."
    result = await MutationGuard("t").use(PiiSanitizer()).validate(text)
    assert result.was_mutated is True
    assert result.output == "Taxpayer SSN is <SSN> on the file."


async def test_credit_card_luhn_validation():
    # Valid Visa card passes Luhn algorithm checksum.
    # Standard test Visa: 4111 1111 1111 1111 (Luhn valid)
    valid_cc = "Payment with 4111-1111-1111-1111 processed."
    result = await MutationGuard("t").use(PiiSanitizer()).validate(valid_cc)
    assert result.was_mutated is True
    assert result.output == "Payment with <CREDIT_CARD> processed."


async def test_credit_card_invalid_luhn_not_redacted():
    # 16 digits that fail Luhn check should NOT be classified as credit cards
    invalid_cc = "Reference order number 1234-5678-9012-3456 completed."
    result = await MutationGuard("t").use(PiiSanitizer()).validate(invalid_cc)
    assert "<CREDIT_CARD>" not in result.output


async def test_iban_redaction():
    text = "Wire funds to BG80BNBG91651111111111 or DE89370400440532013000."
    result = await MutationGuard("t").use(PiiSanitizer()).validate(text)
    assert result.was_mutated is True
    assert result.output == "Wire funds to <IBAN> or <IBAN>."


async def test_phone_number_redaction():
    text = "Call +359 889 700 004 or +1-555-0199 for assistance."
    result = await MutationGuard("t").use(PiiSanitizer()).validate(text)
    assert result.was_mutated is True
    assert result.output == "Call <PHONE> or <PHONE> for assistance."


# ---------------------------------------------------------------------------
# Multiple entities and configuration options
# ---------------------------------------------------------------------------


async def test_mixed_entities_in_single_content():
    text = "Contact Alice at alice@acme.com or +1-555-0199. Server is 10.0.0.1."
    result = await MutationGuard("t").use(PiiSanitizer()).validate(text)
    assert result.was_mutated is True
    assert result.output == "Contact Alice at <EMAIL> or <PHONE>. Server is <IPV4>."


async def test_selective_entities_filtering():
    # Only redact emails, leave IP addresses intact
    text = "User bob@corp.local deployed to 192.168.1.10."
    sanitizer = PiiSanitizer(entities={PiiEntity.EMAIL})
    result = await MutationGuard("t").use(sanitizer).validate(text)
    assert result.was_mutated is True
    assert result.output == "User <EMAIL> deployed to 192.168.1.10."


async def test_custom_mask_format():
    text = "Email dev@test.io or call +1-555-0199."
    sanitizer = PiiSanitizer(mask_format="[REDACTED_{entity}]")
    result = await MutationGuard("t").use(sanitizer).validate(text)
    assert result.was_mutated is True
    assert result.output == "Email [REDACTED_EMAIL] or call [REDACTED_PHONE]."


# ---------------------------------------------------------------------------
# Guard detect-only mode
# ---------------------------------------------------------------------------


async def test_guard_detect_only_catches_pii():
    guard = Guard("input").use(PiiSanitizer())
    res_clean = await guard.validate("Everything is clean.")
    assert res_clean.has_failures is False

    res_dirty = await guard.validate("My email is secret@secret.org.")
    assert res_dirty.has_failures is True
    assert len(res_dirty.failures) == 1
    failure = res_dirty.failures[0]
    assert failure.error_code == PiiSanitizer.PII_DETECTED
    assert "EMAIL" in failure.details["detected_entities"]
    assert failure.details["total_matches"] == 1


# ---------------------------------------------------------------------------
# Fixed-point property and multi-part handling
# ---------------------------------------------------------------------------


async def test_fixed_point_property():
    text = "Contact alice@test.com, phone +1-555-0199, host 192.168.1.5."
    guard = MutationGuard("t").use(PiiSanitizer())
    first_pass = await guard.validate(text)
    assert first_pass.was_mutated is True

    # Re-running already-sanitized text must be a no-op
    second_pass = await guard.validate(first_pass.output)
    assert second_pass.was_mutated is False
    assert second_pass.output == first_pass.output


async def test_multipart_content_view():
    view = _view("Clean intro", "User email: admin@corp.com", "Clean outro")
    result = await MutationGuard("t").use(PiiSanitizer()).validate(view)
    assert result.was_mutated is True
    # Ensure only item p1 was edited
    assert len(result.output.items) == 3
    assert result.output.items[0].value == "Clean intro"
    assert result.output.items[1].value == "User email: <EMAIL>"
    assert result.output.items[2].value == "Clean outro"
