"""Unit tests for ``FormulaInjectionDetector`` — driven through a real ``Guard``."""

from __future__ import annotations

import pytest

from aegis import Guard
from aegis.validators.base import ContentItem, ContentType, ContentView
from aegis.validators.detection import FormulaInjectionDetector


def _guard(**kwargs) -> Guard:
    return Guard("test").use(FormulaInjectionDetector(**kwargs))


def _view(*values: str) -> ContentView:
    return ContentView(
        items=tuple(ContentItem(id=f"p{i}", type=ContentType.TEXT, value=v) for i, v in enumerate(values))
    )


async def test_clean_text_passes():
    result = await _guard().validate("Please update my shipping address to 42 Main Street.")
    assert not result.has_failures


async def test_passing_inputs_have_no_leading_trigger():
    # Prose, JSON, numbers, and non-leading initiators all start with an ordinary character, so none
    # is a cell that would begin a formula.
    for benign in (
        "",
        "hello world",
        '{"name": "Ada", "age": 36}',
        "42",
        "3.14",
        "a=b",
        "2+2",
        "contact me at user@example.com",
        "Prix: 10€ — déjà vu 🎉",
    ):
        assert not (await _guard().validate(benign)).has_failures, benign


async def test_all_padding_or_whitespace_passes():
    # A candidate that is only padding (spaces, NBSP, zero-width, …) has no effective start to check.
    # Note: a leading TAB/CR/LF is NOT padding — it is a control trigger (see the control tests).
    for padding in ("   ", "  ", "​﻿", "  　"):
        assert not (await _guard().validate(padding)).has_failures, repr(padding)


async def test_ascii_triggers_fail_with_expected_trigger_enum():
    # Every ASCII initiator flags at the effective start, whatever the suffix — there is no numeric or
    # handle exception. -5, a phone number, and an @handle are the accepted false-positive cost.
    cases = {
        "=cmd": "EQUALS",
        "+cmd": "PLUS",
        "-5": "MINUS",
        "@handle": "AT",
        "+1 650 555 0100": "PLUS",
        "-2+3": "MINUS",
        "@SUM(A1:A2)": "AT",
    }
    for payload, trigger in cases.items():
        result = await _guard().validate(payload)
        f = result.failures[0]
        assert f.error_code == FormulaInjectionDetector.FORMULA_INJECTION_DETECTED, payload
        assert f.details["trigger_class"] == "ascii", payload
        assert f.details["trigger"] == trigger, payload


async def test_fullwidth_triggers_fail():
    # Preprocessing does not NFKC-fold, so the full-width forms are matched explicitly (class F).
    cases = {
        "＝cmd": "FULLWIDTH_EQUALS",
        "＋cmd": "FULLWIDTH_PLUS",
        "－cmd": "FULLWIDTH_MINUS",
        "＠cmd": "FULLWIDTH_AT",
    }
    for payload, trigger in cases.items():
        f = (await _guard().validate(payload)).failures[0]
        assert f.details["trigger_class"] == "fullwidth", repr(payload)
        assert f.details["trigger"] == trigger, repr(payload)


async def test_control_prefix_triggers_fail():
    # A leading TAB/CR/LF is itself a trigger (class C), alone or after padding — never skipped.
    cases = {"\tcmd": "TAB", "\rcmd": "CR", "\ncmd": "LF", "  \tcmd": "TAB"}
    for payload, trigger in cases.items():
        f = (await _guard().validate(payload)).failures[0]
        assert f.details["trigger_class"] == "control", repr(payload)
        assert f.details["trigger"] == trigger, repr(payload)


async def test_formula_shape_marks_dangerous_command_shapes():
    # The formula_shape marker tiers severity: the high-impact DDE / HYPERLINK shapes are named.
    cases = {
        "=cmd|'/C calc'!A0": "dde_pipe_bang",
        '=HYPERLINK("http://x","click")': "hyperlink",
        '=DDE("cmd";"arg")': "dde",
    }
    for payload, shape in cases.items():
        f = (await _guard().validate(payload)).failures[0]
        assert f.details["formula_shape"] == shape, payload


async def test_plain_formula_without_dangerous_shape_still_fails_with_shape_none():
    # A leading = fails even when the formula is not one of the recognized shapes; the marker is
    # "unknown". "unknown" is NOT a safety signal — WEBSERVICE/IMPORTXML here are exfiltration-capable yet
    # report "unknown", so a caller must raise severity on a recognized shape, never down-rank "unknown".
    for payload in ("=1+1", '=WEBSERVICE("http://x")', "=IMPORTXML(1,2)"):
        f = (await _guard().validate(payload)).failures[0]
        assert f.error_code == FormulaInjectionDetector.FORMULA_INJECTION_DETECTED, payload
        assert f.details["formula_shape"] == "unknown", payload


async def test_formula_shape_is_a_lexical_heuristic_that_can_over_classify():
    # formula_shape is a best-effort lexical marker, not a formula parser: a dangerous-looking token
    # inside a string literal still classifies. This only over-reports severity (the verdict is
    # already FAIL and unchanged) and never down-ranks a real payload. Documented, accepted.
    f = (await _guard().validate('="not really HYPERLINK("')).failures[0]
    assert f.error_code == FormulaInjectionDetector.FORMULA_INJECTION_DETECTED
    assert f.details["formula_shape"] == "hyperlink"


async def test_leading_padding_is_skipped_to_the_effective_start():
    # CVE-2026-9673: leading spaces hid a trigger from a remove-based tool. We skip padding and flag
    # at the effective start, reporting how much was skipped.
    result = await _guard().validate("   =SUM(B1:B2)")
    d = result.failures[0].details
    assert d["match_offset"] == 3
    assert d["padding_skipped"] == 3
    assert d["trigger"] == "EQUALS"


async def test_non_breaking_space_padding_is_skipped():
    # NBSP (U+00A0) is White_Space but not stripped by preprocessing, so it is skipped as padding.
    d = (await _guard().validate(" =cmd")).failures[0].details
    assert d["match_offset"] == 1
    assert d["padding_skipped"] == 1


async def test_stripped_invisible_prefix_is_not_counted_as_padding():
    # NUL / ZWSP / BOM are removed by preprocessing before the scan, so the effective start is the
    # trigger with no padding skipped.
    for payload in ("\x00=cmd", "​=cmd", "﻿=cmd"):
        d = (await _guard().validate(payload)).failures[0].details
        assert d["trigger"] == "EQUALS", repr(payload)
        assert d["padding_skipped"] == 0, repr(payload)


async def test_leading_structural_quote_is_skipped():
    # A serialized "=cmd... still evaluates, so one leading " is skipped (quote-aware detection).
    d = (await _guard().validate('"=1+1')).failures[0].details
    assert d["match_offset"] == 1
    assert d["trigger"] == "EQUALS"


async def test_leading_single_quote_passes_as_neutralized():
    # A leading ' is the spreadsheet neutralizer — the cell renders as text, so it passes.
    assert not (await _guard().validate("'=cmd")).has_failures


async def test_second_quote_as_effective_start_passes():
    # Only ONE leading " is skipped. A doubled "" leaves the second " as the effective start, which
    # is not a trigger, so the value passes.
    assert not (await _guard().validate('""=x')).has_failures


async def test_encoded_triggers_are_caught_after_decoding():
    # The leading initiator is hidden behind an encoding layer; preprocessing decodes it first.
    for payload in (
        "%3Dcmd",  # URL percent
        "&#61;cmd",  # HTML decimal entity
        "&#x3D;cmd",  # HTML hex entity
        "\\x3dcmd",  # JavaScript hex escape
        "\\3D cmd",  # CSS hex escape (space-delimited)
        "%253Dcmd",  # double-encoded: %25 -> %, then %3D -> =
        "​=cmd",  # zero-width space before the trigger
    ):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == FormulaInjectionDetector.FORMULA_INJECTION_DETECTED, repr(payload)


async def test_decode_toggle_off_lets_encoded_payload_pass():
    result = await _guard(decode_obfuscation=False, strip_invisible_chars=False).validate("%3Dcmd")
    assert not result.has_failures


async def test_interior_trigger_within_one_item_passes():
    # The embedded newline is interior to the cell; the cell starts with "text", not "=".
    assert not (await _guard().validate("text\n=1+1")).has_failures


async def test_trigger_at_start_of_a_later_item_is_caught():
    # Each item is its own cell candidate; a trigger at the start of any item fails on that item.
    result = await _guard().validate(_view("harmless text", "=1+1"))
    f = result.failures[0]
    assert f.error_code == FormulaInjectionDetector.FORMULA_INJECTION_DETECTED
    assert f.details["item_index"] == 1


async def test_earliest_offending_item_is_reported():
    # Two offending items; the lowest item_index wins.
    result = await _guard().validate(_view("ok", "=a", "=b"))
    assert result.failures[0].details["item_index"] == 1


async def test_false_positive_cost_is_accepted_by_default():
    # The strict policy flags ordinary prose that leads with -, +, or @. Documented, accepted cost.
    for payload in ("-5", "- a bullet", "+ another bullet", "@user", "+1 650 555 0100"):
        assert (await _guard().validate(payload)).has_failures, payload


async def test_narrowing_ascii_triggers_reduces_false_positives():
    # ascii_triggers="=" turns -, +, @ prose into passes while a real formula (=) still fails.
    narrow = _guard(ascii_triggers="=")
    for benign in ("-5", "+ bullet", "@user"):
        assert not (await narrow.validate(benign)).has_failures, benign
    assert (await _guard(ascii_triggers="=").validate("=cmd")).has_failures


async def test_class_toggles_behave_as_configured():
    assert not (await _guard(detect_fullwidth=False).validate("＝cmd")).has_failures
    assert not (await _guard(detect_control_prefix=False).validate("\tcmd")).has_failures


async def test_skip_leading_padding_toggle_off_lets_padded_trigger_pass():
    # With padding-skip disabled the effective start is the literal padding character, not the
    # trigger it hides, so a padded trigger passes — while an unpadded one still fails.
    guard = _guard(skip_leading_padding=False)
    assert not (await guard.validate("   =SUM(B1:B2)")).has_failures
    assert (await guard.validate("=SUM(B1:B2)")).has_failures


async def test_skip_leading_quote_toggle_off_lets_quoted_trigger_pass():
    # With quote-skip disabled the leading " is the effective start (not a trigger), so a quoted
    # payload passes — while the unquoted form still fails.
    guard = _guard(skip_leading_quote=False)
    assert not (await guard.validate('"=1+1')).has_failures
    assert (await guard.validate("=1+1")).has_failures


async def test_empty_view_passes():
    # A view with no items (distinct from a single empty item) has nothing to scan — the loop never
    # runs, so it passes.
    assert not (await _guard().validate(_view())).has_failures


async def test_quote_then_padding_then_trigger_is_caught():
    # One structural " is skipped, then leading padding, then the effective start is the trigger.
    # match_offset counts the skipped quote and padding; padding_skipped counts only the padding.
    d = (await _guard().validate('"  =cmd')).failures[0].details
    assert d["trigger"] == "EQUALS"
    assert d["match_offset"] == 3
    assert d["padding_skipped"] == 2


async def test_whitespace_then_tab_reports_the_control_trigger():
    # Spaces are padding and skipped; the TAB they precede is the effective start (control trigger).
    f = (await _guard().validate("   \t")).failures[0]
    assert f.details["trigger"] == "TAB"
    assert f.details["trigger_class"] == "control"


async def test_real_minus_sign_passes_as_a_documented_gap():
    # U+2212 (MINUS SIGN) is not in the finite trigger set, so it passes — a documented coverage gap.
    assert not (await _guard().validate("−=x")).has_failures


async def test_max_input_chars_rejects_oversized_input():
    result = await _guard(max_input_chars=5).validate("x" * 9)
    assert result.failures[0].error_code == FormulaInjectionDetector.FORMULA_INJECTION_INPUT_TOO_LARGE
    assert result.failures[0].details == {"length": 9, "limit": 5}


async def test_resource_valve_boundary():
    # At the limit passes the valve (then the scan runs); one over is rejected.
    assert not (await _guard(max_input_chars=5).validate("hello")).has_failures  # exactly 5
    assert (await _guard(max_input_chars=5).validate("hello!")).failures[0].error_code == (
        FormulaInjectionDetector.FORMULA_INJECTION_INPUT_TOO_LARGE
    )


async def test_default_max_input_chars_caps_at_one_mib():
    result = await _guard().validate("a" * (1024 * 1024 + 1))
    assert result.failures[0].error_code == FormulaInjectionDetector.FORMULA_INJECTION_INPUT_TOO_LARGE


async def test_max_input_chars_none_lifts_the_cap():
    assert not (await _guard(max_input_chars=None).validate("a" * (1024 * 1024 + 1))).has_failures


async def test_details_carry_only_safe_summaries():
    # Redaction: the raw input / matched cell / formula text must never appear in details or message.
    payload = "=cmd|'/C calc'!A0"
    f = (await _guard().validate(payload)).failures[0]
    assert set(f.details) == {
        "trigger_class",
        "trigger",
        "match_offset",
        "padding_skipped",
        "item_index",
        "formula_shape",
    }
    assert "cmd" not in f.error_message
    assert "calc" not in f.error_message


async def test_failure_message_reports_trigger_class_and_offset():
    # Consistent with the XSS/SQLi siblings: the message names the trigger class and the offset
    # (safe summaries only — never the raw input), while details carries the full breakdown.
    f = (await _guard().validate("   =cmd")).failures[0]
    assert "ascii" in f.error_message
    assert "offset 3" in f.error_message


async def test_detection_is_deterministic():
    payload = "   =HYPERLINK(1,2)"
    first = (await _guard().validate(payload)).failures[0].details
    second = (await _guard().validate(payload)).failures[0].details
    assert first == second


async def test_empty_ascii_triggers_rejected_at_construction():
    with pytest.raises(ValueError):
        FormulaInjectionDetector(ascii_triggers="")


async def test_invalid_ascii_triggers_rejected_at_construction():
    with pytest.raises(ValueError):
        FormulaInjectionDetector(ascii_triggers="=x")


def test_negative_max_input_chars_rejected_at_construction():
    with pytest.raises(ValueError):
        FormulaInjectionDetector(max_input_chars=-1)
