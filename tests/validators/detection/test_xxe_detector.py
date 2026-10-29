"""Unit tests for ``XxeDetector`` — driven through a real ``Guard``."""

from __future__ import annotations

import base64
import time

import pytest

from aegis import Guard
from aegis.validators.base import ContentItem, ContentType, ContentView
from aegis.validators.detection import XxeDetector


def _guard(**kwargs) -> Guard:
    return Guard("test").use(XxeDetector(**kwargs))


def _view(*values: str) -> ContentView:
    return ContentView(
        items=tuple(ContentItem(id=f"p{i}", type=ContentType.TEXT, value=v) for i, v in enumerate(values))
    )


async def test_clean_text_passes():
    result = await _guard().validate("Please update my shipping address to 42 Main Street.")
    assert not result.has_failures


async def test_failure_reports_position_and_construct():
    # A failure locates the first construct: offset (into the scanned text), length, and class.
    result = await _guard().validate('header <!DOCTYPE foo SYSTEM "file:///etc/passwd">')
    f = result.failures[0]
    assert f.details == {"match_offset": 7, "match_length": len("<!DOCTYPE foo SYSTEM"), "construct": "external_dtd"}
    assert "offset 7" in f.error_message


async def test_failure_position_points_at_earliest_construct():
    # Internal subset opens at offset 0, the external entity inside it starts later; the earliest
    # construct is reported, so the enclosing DTD wins.
    result = await _guard().validate('<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "http://host/"> ]>')
    f = result.failures[0]
    assert f.details["match_offset"] == 0
    assert f.details["construct"] == "dtd_internal_subset"


async def test_xxe_constructs_fail():
    for payload in (
        '<!ENTITY xxe SYSTEM "file:///etc/passwd">',
        '<!ENTITY xxe PUBLIC "id" "http://host/">',
        '<!DOCTYPE foo SYSTEM "foo.dtd">',
        '<!DOCTYPE foo [ <!ENTITY x "y"> ]>',
        '<!ENTITY % pe SYSTEM "http://host/">',
        '<!ENTITY % pe "value">',
        '<!ENTITY lol "ha">',
        '<xi:include href="file:///etc/passwd"/>',
        '<foo xmlns:xi="http://www.w3.org/2001/XInclude"/>',
    ):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == XxeDetector.XXE_DETECTED, payload


async def test_each_construct_reports_its_label():
    # The construct label names the kind of match; it is the one sub-construct detail we report.
    cases = {
        '<!ENTITY xxe SYSTEM "file:///x">': "external_entity",
        '<!ENTITY p PUBLIC "a" "b">': "external_entity",
        '<!ENTITY % pe SYSTEM "http://host/">': "external_entity",
        '<!DOCTYPE foo SYSTEM "x.dtd">': "external_dtd",
        '<!DOCTYPE foo [ <!ENTITY x "y"> ]>': "dtd_internal_subset",
        '<!ENTITY % pe "v">': "parameter_entity",
        '<!ENTITY lol "ha">': "entity_declaration",
        '<xi:include href="file:///x"/>': "xinclude",
    }
    for payload, construct in cases.items():
        result = await _guard().validate(payload)
        assert result.failures[0].details["construct"] == construct, payload


async def test_construct_inside_complete_comment_passes():
    # A declaration inside a complete comment/CDATA/PI is inert — an XML processor never acts on it.
    for payload in (
        '<!-- <!DOCTYPE evil SYSTEM "file:///etc/passwd"> -->',
        '<![CDATA[ <!ENTITY x SYSTEM "http://host/"> ]]>',
        '<?pi <!ENTITY x SYSTEM "http://host/"> ?>',
    ):
        assert not (await _guard().validate(payload)).has_failures, payload


async def test_construct_inside_unterminated_region_is_flagged():
    # Fail toward detection: an unterminated comment/CDATA cannot make what follows inert.
    for payload in (
        '<!-- not closed <!DOCTYPE foo SYSTEM "file:///etc/passwd">',
        '<![CDATA[ not closed <!ENTITY x SYSTEM "http://host/">',
    ):
        assert (await _guard().validate(payload)).has_failures, payload


async def test_benign_text_with_xml_vocabulary_passes():
    # The construct patterns anchor on ``<!`` / the XInclude namespace, so prose that merely uses
    # the words, an ordinary ``%``, or a lone inequality is not flagged.
    for benign in (
        "The system entity doctype is a public document.",
        "50% off; discount applies at checkout.",
        "a < b and c > d in the inequality.",
        "See the XML spec for the full grammar.",
        '{"doctype": "html", "entity": "none"}',
        "<note><to>Bob</to><from>Alice</from></note>",
    ):
        assert not (await _guard().validate(benign)).has_failures, benign


async def test_bare_doctype_passes_by_default_and_fails_when_flagged():
    # Capability-based policy: a bare HTML5 doctype carries no XXE capability and passes by default.
    assert not (await _guard().validate("<!DOCTYPE html>")).has_failures
    result = await _guard(flag_bare_doctype=True).validate("<!DOCTYPE html>")
    assert result.failures[0].details["construct"] == "bare_doctype"


async def test_external_doctype_fails_regardless_of_bare_flag():
    # A capability-bearing DOCTYPE is flagged either way, under the more specific external_dtd label.
    for flag in (False, True):
        result = await _guard(flag_bare_doctype=flag).validate('<!DOCTYPE foo SYSTEM "x.dtd">')
        assert result.failures[0].details["construct"] == "external_dtd", flag


async def test_url_encoded_payload_is_caught():
    # %3C → '<', %20 → space; the construct only appears after decoding.
    result = await _guard().validate("%3C!DOCTYPE%20foo%20SYSTEM%20%22x.dtd%22%3E")
    assert result.failures[0].error_code == XxeDetector.XXE_DETECTED


async def test_html_entity_encoded_payload_is_caught():
    # &#x3C; → '<', &#34; → '"'; numeric char-ref obfuscation.
    result = await _guard().validate("&#x3C;!ENTITY xxe SYSTEM &#34;http://host/&#34;>")
    assert result.failures[0].error_code == XxeDetector.XXE_DETECTED


async def test_double_encoded_payload_is_caught():
    # Double URL-encoding (%253C → %3C → '<'), decoded to a fixed point.
    result = await _guard().validate("%253C!DOCTYPE%2520foo%2520SYSTEM%2520%2522x%2522%253E")
    assert result.failures[0].error_code == XxeDetector.XXE_DETECTED


async def test_zero_width_split_keyword_is_caught():
    # A zero-width space inside the keyword must not hide <!DOCTYPE.
    result = await _guard().validate('<!DOC​TYPE foo SYSTEM "x.dtd">')
    assert result.failures[0].error_code == XxeDetector.XXE_DETECTED


async def test_encoded_zero_width_split_keyword_is_caught():
    # The zero-width space is URL-encoded (%E2%80%8B), so it only becomes an invisible char after
    # decoding. Stripping must run again after each decode pass or the revealed separator hides it.
    result = await _guard().validate('<!DOC%E2%80%8BTYPE foo SYSTEM "x.dtd">')
    assert result.failures[0].error_code == XxeDetector.XXE_DETECTED


async def test_decode_toggle_off_lets_encoded_payload_pass():
    result = await _guard(decode_obfuscation=False, strip_invisible_chars=False).validate(
        "%3C!DOCTYPE%20foo%20SYSTEM%20%22x%22%3E"
    )
    assert not result.has_failures


async def test_strip_invisible_toggle_off_lets_split_keyword_pass():
    result = await _guard(strip_invisible_chars=False, decode_obfuscation=False).validate(
        '<!DOC​TYPE foo SYSTEM "x.dtd">'
    )
    assert not result.has_failures


async def test_empty_and_whitespace_pass():
    assert not (await _guard().validate("")).has_failures
    assert not (await _guard().validate("   \t\n")).has_failures


async def test_multi_item_view_with_a_construct_fails():
    view = _view("hello there", '<!DOCTYPE foo SYSTEM "x.dtd">', "goodbye")
    result = await _guard().validate(view)
    assert result.failures[0].error_code == XxeDetector.XXE_DETECTED


async def test_construct_split_across_items_is_caught():
    # Detection runs on view.render() (items joined by newline, matched by \\s), so a construct
    # split across an item boundary is still caught — not evaluated per item in isolation.
    view = _view("<!DOCTYPE foo", 'SYSTEM "x.dtd">')
    result = await _guard().validate(view)
    assert result.failures[0].error_code == XxeDetector.XXE_DETECTED


async def test_inert_region_cannot_span_items():
    # Security: masking is per item. An inert-region opener and closer in *separate* items must not
    # comment out an active construct in a third item — the items can be processed independently
    # downstream, so one part is never trusted to make another inert (fail toward detection).
    view = _view("<!--", '<!DOCTYPE foo SYSTEM "evil.dtd">', "-->")
    result = await _guard().validate(view)
    assert result.failures[0].details["construct"] == "external_dtd"


async def test_inert_region_within_a_single_item_still_passes():
    # The per-item rule does not weaken real masking: a complete comment contained in one item is
    # still inert, so the construct inside it is not flagged.
    view = _view("intro", '<!-- <!DOCTYPE evil SYSTEM "x"> -->', "outro")
    assert not (await _guard().validate(view)).has_failures


async def test_details_never_echo_raw_input():
    # Only offsets, length, and the fixed label leave the validator — never the path, URL, or name.
    result = await _guard().validate('<!ENTITY xxe SYSTEM "file:///etc/passwd">')
    f = result.failures[0]
    assert set(f.details) == {"match_offset", "match_length", "construct"}
    assert "passwd" not in f.error_message and "file://" not in f.error_message
    for value in f.details.values():
        assert "passwd" not in str(value) and "file://" not in str(value)


async def test_detection_is_deterministic():
    # Pure-regex, no clock/network: identical input yields identical details across runs.
    payload = '<!DOCTYPE foo [ <!ENTITY a SYSTEM "http://host/"> ]>'
    first = (await _guard().validate(payload)).failures[0].details
    for _ in range(3):
        assert (await _guard().validate(payload)).failures[0].details == first


async def test_old_regex_payloads_still_caught():
    # Parity with the retired InputSanitizer regex (general entity + SYSTEM): every payload it
    # caught is still caught, now reported as external_entity (which also covers PUBLIC/parameter).
    for payload in (
        '<!ENTITY foo SYSTEM "file:///etc/passwd">',
        '<!entity bar SYSTEM "http://host/">',
        '<!ENTITY   baz   SYSTEM "x">',
    ):
        result = await _guard().validate(payload)
        assert result.failures[0].details["construct"] == "external_entity", payload


async def test_base64_wrapped_payload_is_a_documented_gap():
    # Out of scope by design: byte-level transport encodings (base64, archives) belong to an
    # upstream decode/extraction stage. The validator sees decoded text, so a base64-wrapped payload
    # passes here — asserted so the documented limitation is intentional, not an accident.
    payload = base64.b64encode(b'<!DOCTYPE foo SYSTEM "x.dtd">').decode()
    assert not (await _guard().validate(payload)).has_failures


async def test_max_input_chars_rejects_oversized_input():
    result = await _guard(max_input_chars=10).validate("x" * 20)
    assert result.failures[0].error_code == XxeDetector.XXE_INPUT_TOO_LARGE
    assert result.failures[0].details == {"length": 20, "limit": 10}


async def test_max_input_chars_boundary():
    # Under and at the limit pass; one over is rejected before any decode/scan.
    assert not (await _guard(max_input_chars=5).validate("xxxx")).has_failures
    assert not (await _guard(max_input_chars=5).validate("xxxxx")).has_failures
    over = await _guard(max_input_chars=5).validate("xxxxxx")
    assert over.failures[0].error_code == XxeDetector.XXE_INPUT_TOO_LARGE


async def test_default_max_input_chars_caps_at_one_mib():
    # Safe-by-default: oversized input is rejected without being preprocessed/scanned.
    result = await _guard().validate("a" * (1024 * 1024 + 1))
    assert result.failures[0].error_code == XxeDetector.XXE_INPUT_TOO_LARGE


async def test_max_input_chars_none_lifts_the_cap():
    # Explicit opt-out for trusted-size input.
    assert not (await _guard(max_input_chars=None).validate("a" * (1024 * 1024 + 1))).has_failures


def test_negative_max_input_chars_rejected_at_construction():
    with pytest.raises(ValueError):
        XxeDetector(max_input_chars=-1)


async def test_internal_subset_scan_is_not_quadratic():
    # Regression: the dtd_internal_subset body excludes '<', so re.search cannot restart a
    # full-length scan at every repeated '<!DOCTYPE'. This input (~256 KB, well under the 1 MiB cap
    # so it is actually scanned, and with no '[' or '>' so nothing matches) took tens of seconds
    # with the earlier '[^>\\[]*' body; bounded, it is milliseconds.
    payload = "<!DOCTYPE " * (256 * 1024 // len("<!DOCTYPE "))
    start = time.perf_counter()
    result = await _guard().validate(payload)
    elapsed = time.perf_counter() - start
    assert not result.has_failures
    assert elapsed < 2.0, f"scan took {elapsed:.2f}s — possible quadratic regression"


async def test_xinclude_lookalike_element_passes():
    # '<xi:include-extra>' is a different element whose name only starts with 'include'; it is not
    # the XInclude 'include' element, so it must not be flagged. (No namespace URI present either.)
    assert not (await _guard().validate("<xi:include-extra/>")).has_failures


async def test_unterminated_xinclude_at_eof_is_flagged():
    # Fail toward detection: '<xi:include' with nothing after it is still flagged — the name-char
    # lookahead ends the match at end-of-input, unlike a '(?=[\\s/>])' terminator lookahead.
    result = await _guard().validate("fragment <xi:include")
    assert result.failures[0].details["construct"] == "xinclude"
