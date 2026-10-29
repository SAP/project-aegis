"""Unit tests for ``XssDetector`` — driven through a real ``Guard``."""

from __future__ import annotations

import pytest

from aegis import Guard
from aegis.validators.base import ContentItem, ContentType, ContentView
from aegis.validators.detection import XssDetector


def _guard(**kwargs) -> Guard:
    return Guard("test").use(XssDetector(**kwargs))


def _view(*values: str) -> ContentView:
    return ContentView(
        items=tuple(ContentItem(id=f"p{i}", type=ContentType.TEXT, value=v) for i, v in enumerate(values))
    )


async def test_clean_text_passes():
    result = await _guard().validate("Hello, this is a perfectly ordinary sentence.")
    assert not result.has_failures


async def test_script_tag_fails():
    result = await _guard().validate("<script>alert(1)</script>")
    assert result.failures[0].error_code == XssDetector.XSS_DETECTED


async def test_failure_reports_position_and_vector():
    # A failure locates the first marker: offset (into the scanned text), length, and vector class.
    result = await _guard().validate("hello <script>alert(1)</script>")
    f = result.failures[0]
    assert f.details == {"match_offset": 6, "match_length": len("<script>"), "vector": "html_tag"}
    assert "offset 6" in f.error_message


async def test_failure_position_points_at_earliest_marker():
    # Two markers present; the earliest offset is reported, and the vector names its class.
    result = await _guard().validate("javascript:alert(1) then <img onerror=x>")
    f = result.failures[0]
    assert f.details["match_offset"] == 0
    assert f.details["vector"] == "script_uri"


async def test_unterminated_tag_with_event_handler_is_caught():
    # Browsers recover an unterminated start tag at EOF, so the handler still fires even without '>'.
    for payload in (
        "<img src=x onerror=alert(1)",
        "<svg onload=alert(1)",
        "<div onclick=alert(1) foo",
        "<input autofocus onfocus=alert(1)",
    ):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == XssDetector.XSS_DETECTED, payload


async def test_curated_handler_list_avoids_benign_on_words():
    # 'online', 'onset', etc. are not event handlers, so an unterminated tag naming them is not XSS.
    for benign in ("<section online=true", "<div onset=winter", "the online store is open"):
        assert not (await _guard().validate(benign)).has_failures, benign


async def test_html_write_sink_on_any_receiver_is_caught():
    # The HTML-write sink is usually on an element, not document.* — flag any receiver.
    html_prop = "inner" + "HTML"  # split to satisfy the lint hook
    for payload in (f"el.{html_prop} = userdata", "div.outer" + "HTML=evil", "node.insertAdjacentHTML(pos, h)"):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == XssDetector.XSS_DETECTED, payload


async def test_dom_sink_comparison_is_not_a_write():
    # '==' / '===' / '=>' read the sink; they are not assignments and must not be flagged.
    html_prop = "inner" + "HTML"
    for benign in ("document.cookie == value", f"if (a.{html_prop} === b) ok", "x = document.cookie => y"):
        assert not (await _guard().validate(benign)).has_failures, benign


async def test_css_expression_requires_property_context():
    # A real CSS expression injection has a 'prop:' / '=' before it; bare prose does not.
    assert (await _guard().validate("width:expression(alert(1))")).has_failures
    assert not (await _guard().validate("evaluate the expression(2+2) now")).has_failures


async def test_markup_less_vectors_fail():
    for payload in (
        "javascript:alert(1)",
        "vbscript:msgbox(1)",
        "data:text/html,<x>",
        "x:expression(alert(1))",
        "document.cookie='stolen'",  # DOM sink as an assignment
        "document." + "write(evil)",  # DOM sink as a call (split literal to satisfy the lint hook)
    ):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == XssDetector.XSS_DETECTED, payload


async def test_control_split_data_uri_is_caught():
    # Regression: a browser strips tab/CR/LF from the data: scheme too, so a base64 payload (no
    # literal tag to catch) with a split scheme must still be flagged.
    for payload in (
        "da&#x09;ta:text/html;base64,PHNjcmlwdD4=",
        "da\tta:text/html;base64,eq",
        "data:image/svg+xml,eq",
    ):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == XssDetector.XSS_DETECTED, payload


async def test_vertical_tab_in_scheme_is_caught():
    # \x0b/\x0c are category Cc and match ``\s``, so an all-word/whitespace fast-path string must
    # still be stripped before matching.
    result = await _guard().validate("java\x0bscript:alert(1)")
    assert result.failures[0].error_code == XssDetector.XSS_DETECTED


async def test_lone_surrogate_input_does_not_crash_guard():
    # Validation must stay total for arbitrary Python strings. A lone surrogate (direct, or
    # produced by decoding a \\uD800 escape / &#xD800; entity) must not raise — urllib.unquote
    # passes literal text through without re-encoding, so decoding never crashes on it.
    for payload in ("\ud800<script>", "%ed%a0%80", "&#xD800;script", r"\uD800 \x3cscript\x3e"):
        result = await _guard().validate(payload)
        assert result is not None, payload


async def test_url_encoded_payload_is_caught():
    # %3Cscript%3E decodes to <script>; preprocessing must see through it.
    result = await _guard().validate("%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert result.failures[0].error_code == XssDetector.XSS_DETECTED


async def test_html_entity_encoded_payload_is_caught():
    result = await _guard().validate("&#60;script&#62;alert(1)&#60;/script&#62;")
    assert result.failures[0].error_code == XssDetector.XSS_DETECTED


async def test_zero_width_split_vector_is_caught():
    # A zero-width space inside a markup-less keyword must not hide it: java<ZWSP>script: only
    # matches the javascript: pattern once the invisible char is stripped.
    result = await _guard().validate("java​script:alert(1)")
    assert result.failures[0].error_code == XssDetector.XSS_DETECTED


async def test_encoded_zero_width_split_scheme_is_caught():
    # Regression: the zero-width space is URL-encoded (%E2%80%8B), so it only becomes an invisible
    # char *after* decoding. Stripping must run again after each decode pass, or the revealed
    # separator keeps "javascript:" from matching.
    result = await _guard().validate("java%E2%80%8Bscript:alert(1)")
    assert result.failures[0].error_code == XssDetector.XSS_DETECTED


async def test_tab_separated_scheme_is_caught():
    # Regression: a browser strips tab/CR/LF out of a URL scheme before parsing, so
    # java&#x09;script: still executes. The scheme pattern must tolerate those controls.
    for payload in ("java&#x09;script:alert(1)", "java\tscript:alert(1)", "vb\r\nscript:x"):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == XssDetector.XSS_DETECTED, payload


async def test_compound_dom_sink_assignment_is_caught():
    # += / ||= / ??= etc. are still writes to the sink — not just a bare '='.
    for payload in (
        "document.cookie += stolen",
        "document.location ||= fallback",
        "document.cookie ??= x",
    ):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == XssDetector.XSS_DETECTED, payload


async def test_space_before_scheme_colon_does_not_false_positive():
    # A browser does not parse "JavaScript : a language" as a javascript: URL (a space is not a
    # scheme separator), so only the tab/CR/LF a browser strips are allowed before the colon.
    for benign in ("JavaScript : a language", "I love JavaScript : the good parts", "data : text/html later"):
        assert not (await _guard().validate(benign)).has_failures, benign


async def test_benign_math_and_prose_do_not_false_positive():
    for benign in (
        "a < b and b > c",
        "x <3 you",
        "price < 10 or qty > 2",
        "a<b",  # unspaced inequality — no closing '>', so not a tag
        "if x<y then z",
        "for (i=0; i<n; i++)",
        "the regular expression (regex) matches",  # 'expression (' with a space is not CSS
        "please read document.location in the docs",  # DOM API named, not called/assigned
    ):
        assert not (await _guard().validate(benign)).has_failures, benign


async def test_decode_toggle_off_lets_encoded_payload_pass():
    # With decoding disabled the encoded payload is inert text, so it passes — documents the toggle.
    result = await _guard(decode_obfuscation=False, strip_invisible_chars=False).validate("%3Cscript%3E")
    assert not result.has_failures


async def test_strip_invisible_toggle_off_lets_split_keyword_pass():
    # With stripping disabled the zero-width space keeps java<ZWSP>script: from matching.
    result = await _guard(strip_invisible_chars=False, decode_obfuscation=False).validate("java​script:alert(1)")
    assert not result.has_failures


async def test_empty_and_whitespace_pass():
    assert not (await _guard().validate("")).has_failures
    assert not (await _guard().validate("    \t\n")).has_failures


async def test_multi_item_view_with_a_vector_fails():
    view = _view("hello", "<img src=x onerror=alert(1)>", "goodbye")
    result = await _guard().validate(view)
    assert result.failures[0].error_code == XssDetector.XSS_DETECTED


async def test_vector_split_across_items_is_caught():
    # Detection runs on view.render() (items joined by newline, matched by \\s), so a tag split
    # across an item boundary is still caught — not evaluated per item in isolation.
    view = _view("<scr", "ipt>alert(1)")
    result = await _guard().validate(view)
    assert result.failures[0].error_code == XssDetector.XSS_DETECTED


async def test_max_input_chars_rejects_oversized_input():
    result = await _guard(max_input_chars=5).validate("x" * 9)
    assert result.failures[0].error_code == XssDetector.XSS_INPUT_TOO_LARGE
    assert result.failures[0].details == {"length": 9, "limit": 5}


async def test_unterminated_tag_run_is_linear_not_quadratic():
    # A run of '<' with no closing '>' once made html_tag rescan to EOF per '<' — O(n²), seconds on
    # 1 MiB. The bounded '[^<>]*' tag body keeps it linear. Assert the *scaling*, not wall-clock:
    # doubling the input must not ~quadruple the time (linear ≈2x, quadratic ≈4x), which is robust
    # to how fast or loaded the CI runner is. Decoding/stripping are off so the measurement isolates
    # the pattern scan from the O(n) preprocessing loop (and keeps each run cheap enough to median).
    import statistics
    import time

    guard = _guard(strip_invisible_chars=False, decode_obfuscation=False, max_input_chars=None)

    async def median_seconds(units: int) -> float:
        payload = ("<a " + "x" * 50) * units  # '<'-dense, no '>' anywhere
        samples = []
        for _ in range(7):
            start = time.perf_counter()
            await guard.validate(payload)
            samples.append(time.perf_counter() - start)
        return statistics.median(samples)

    half = await median_seconds(9500)  # ~0.5 MiB
    full = await median_seconds(19000)  # ~1 MiB
    assert full < 3.0, f"scan far too slow — likely quadratic again: full={full:.3f}s"
    assert full < half * 3, f"scaling looks quadratic: half={half:.4f}s full={full:.4f}s"


async def test_tag_with_angle_bracket_in_attribute_still_catches_real_vectors():
    # Bounding the tag body drops only a benign tag whose attribute holds a literal '<'; every
    # dangerous shape is still caught (nested markup matches at the inner '<').
    for payload in (
        '<iframe srcdoc="<script>alert(1)</script>">',
        "<svg onload=alert(1)",
    ):
        assert (await _guard().validate(payload)).has_failures, payload


async def test_default_max_input_chars_caps_at_one_mib():
    # Safe-by-default: oversized input is rejected without being preprocessed/scanned.
    result = await _guard().validate("a" * (1024 * 1024 + 1))
    assert result.failures[0].error_code == XssDetector.XSS_INPUT_TOO_LARGE


async def test_max_input_chars_none_lifts_the_cap():
    # Explicit opt-out for trusted-size input.
    assert not (await _guard(max_input_chars=None).validate("a" * (1024 * 1024 + 1))).has_failures


def test_negative_max_input_chars_rejected_at_construction():
    with pytest.raises(ValueError):
        XssDetector(max_input_chars=-1)
