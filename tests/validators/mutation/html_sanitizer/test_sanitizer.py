"""Tests for :class:`HtmlSanitizer`.

The suite runs with ``asyncio_mode = "auto"`` (see ``pyproject.toml``), so tests
are plain ``async def`` and drive the validator through a ``Guard`` /
``MutationGuard`` exactly as a caller would.
"""

from __future__ import annotations

import pytest

from aegis import ContentItem, ContentType, ContentView, Guard, MutationGuard
from aegis.validators.mutation import HtmlSanitizer
from aegis.validators.mutation.html_sanitizer._defaults import SAFE_CSS_PROPERTIES


def _view(*values: str) -> ContentView:
    """Build a multi-part view with stable ids ``p0``, ``p1``, … ."""
    return ContentView(
        items=tuple(ContentItem(id=f"p{i}", type=ContentType.TEXT, value=v) for i, v in enumerate(values))
    )


# ---------------------------------------------------------------------------
# Happy path — clean content passes untouched
# ---------------------------------------------------------------------------


async def test_plain_text_passes_unchanged():
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("Just some plain text.")
    assert result.was_mutated is False
    assert result.output == "Just some plain text."


async def test_already_canonical_html_passes():
    # nh3's canonical serialization of this fragment is itself, so there is
    # nothing to repair.
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("<p>hello <b>world</b></p>")
    assert result.was_mutated is False
    assert result.output == "<p>hello <b>world</b></p>"


async def test_empty_string_passes():
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("")
    assert result.was_mutated is False
    assert result.output == ""


async def test_whitespace_only_passes():
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("   \n  ")
    assert result.was_mutated is False
    assert result.output == "   \n  "


async def test_empty_view_passes():
    result = await MutationGuard("t").use(HtmlSanitizer()).validate(ContentView(items=()))
    assert result.was_mutated is False


# ---------------------------------------------------------------------------
# Validation rules R1-R8 — each violation fails and is repaired
# ---------------------------------------------------------------------------


async def test_script_removed_with_its_content():
    # R2: <script> is removed together with its text, not merely unwrapped.
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("<script>alert(1)</script>safe")
    assert result.was_mutated is True
    assert result.output == "safe"


async def test_style_element_removed_with_its_content():
    # R2: the <style> element (a stylesheet block) is removed with its content,
    # distinct from the inline `style` attribute governed by allowed_css_properties.
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("<style>body{color:red}</style>text")
    assert result.was_mutated is True
    assert result.output == "text"


async def test_iframe_and_object_are_stripped():
    # R1: non-allowlisted tags do not survive.
    result = await MutationGuard("t").use(HtmlSanitizer()).validate('<iframe src="http://evil"></iframe>hi')
    assert result.was_mutated is True
    assert "iframe" not in result.output


async def test_event_handler_attribute_dropped():
    # R3: event-handler attributes are never allowlisted.
    guard = Guard("t").use(HtmlSanitizer())
    result = await guard.validate('<img src="http://x/y.png" onerror="alert(1)">')
    assert result.has_failures
    mutated = await MutationGuard("t").use(HtmlSanitizer()).validate('<img src="http://x/y.png" onerror="alert(1)">')
    assert "onerror" not in mutated.output


async def test_javascript_url_scheme_stripped():
    # R4: javascript: is not in the default scheme allowlist.
    result = await MutationGuard("t").use(HtmlSanitizer()).validate('<a href="javascript:alert(1)">x</a>')
    assert result.was_mutated is True
    assert "javascript:" not in result.output


async def test_data_url_scheme_stripped():
    result = await MutationGuard("t").use(HtmlSanitizer()).validate('<a href="data:text/html,<script>">x</a>')
    assert result.was_mutated is True
    assert "data:" not in result.output


async def test_comments_are_stripped():
    # R6.
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("<!-- secret -->visible")
    assert result.was_mutated is True
    assert result.output == "visible"


async def test_link_rel_added_to_anchor():
    # R7: reverse-tabnabbing mitigation.
    result = await MutationGuard("t").use(HtmlSanitizer()).validate('<a href="http://example.com">x</a>')
    assert result.was_mutated is True
    assert 'rel="noopener noreferrer"' in result.output


async def test_malformed_markup_is_normalized_not_rejected():
    # R8: an unclosed tag is repaired to canonical form; it is never an error.
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("<b>hi")
    assert result.was_mutated is True
    assert result.output == "<b>hi</b>"


# ---------------------------------------------------------------------------
# Error contract — one code, safe details only
# ---------------------------------------------------------------------------


async def test_failure_uses_the_single_error_code():
    result = await Guard("t").use(HtmlSanitizer()).validate("<script>alert(1)</script>")
    assert result.has_failures
    assert result.failures[0].error_code == "DISALLOWED_HTML"


async def test_details_carry_only_safe_summaries_never_raw_html():
    # The raw markup may hold an XSS payload or an embedded secret, so details must
    # report counts and lengths only — never the offending content.
    payload = "<script>steal('SECRET_TOKEN')</script>"
    result = await Guard("t").use(HtmlSanitizer()).validate(payload)
    details = result.failures[0].details
    assert set(details) == {"changed_parts", "original_length", "sanitized_length"}
    assert "SECRET_TOKEN" not in str(details)
    assert "script" not in str(details)


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


async def test_plain_text_with_angle_brackets_is_escaped():
    # Documents the HTML-only precondition: applied to plain text, a stray "<" is
    # HTML-escaped, which changes the string and is therefore flagged.
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("5 < 3 and 3 > 1")
    assert result.was_mutated is True
    assert result.output == "5 &lt; 3 and 3 &gt; 1"


async def test_large_input_is_sanitized():
    big = "<script>x</script>" + ("a" * 100_000)
    result = await MutationGuard("t").use(HtmlSanitizer()).validate(big)
    assert result.was_mutated is True
    assert result.output == "a" * 100_000


# ---------------------------------------------------------------------------
# Adversarial — obfuscation resolved before allowlisting
# ---------------------------------------------------------------------------


async def test_mixed_case_script_tag_neutralized():
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("<ScRiPt>alert(1)</ScRiPt>done")
    assert result.was_mutated is True
    assert "alert" not in result.output


async def test_entity_encoded_javascript_scheme_neutralized():
    # &#106; is "j"; html5ever resolves the entity before the scheme is checked.
    result = await MutationGuard("t").use(HtmlSanitizer()).validate('<a href="&#106;avascript:alert(1)">x</a>')
    assert result.was_mutated is True
    assert "javascript:" not in result.output.lower()


async def test_svg_foreign_content_stripped_by_default():
    result = await MutationGuard("t").use(HtmlSanitizer()).validate("<svg><script>alert(1)</script></svg>text")
    assert result.was_mutated is True
    assert "svg" not in result.output
    assert "alert" not in result.output


async def test_tag_split_across_parts_does_not_reassemble():
    # Each part is sanitized independently, so a tag split across two parts
    # ("<scr" | "ipt>") cannot recombine into a live tag: the incomplete "<scr" is
    # dropped and the stray ">" in the second part is HTML-escaped.
    guard = MutationGuard("t").use(HtmlSanitizer())
    result = await guard.validate(_view("<scr", "ipt>alert(1)</script>"))
    assert isinstance(result.output, ContentView)
    joined = result.output.render()
    assert "<script" not in joined
    assert "<scr" not in result.output.items[0].value  # incomplete tag neutralized


# ---------------------------------------------------------------------------
# Repair correctness — idempotency and the re-validation fixed point
# ---------------------------------------------------------------------------


# The adversarial corpus that the fixed-point and idempotency guarantees are
# checked against. If any input here breaks idempotency, the mutation path can no
# longer be trusted, so these are asserted explicitly rather than assumed.
_CORPUS = [
    "<script>alert(1)</script>hi",
    '<img src="x" onerror="alert(1)">',
    '<a href="javascript:alert(1)">x</a>',
    "<ScRiPt>alert(1)</ScRiPt>",
    "<b>hi",
    "5 < 3",
    "<!-- c -->text",
    "<svg><script>x</script></svg>",
    '<a href="&#106;avascript:alert(1)">x</a>',
    "<style>body{color:red}</style>text",
]


@pytest.mark.parametrize("raw", _CORPUS)
async def test_clean_is_idempotent(raw: str):
    # clean(clean(x)) == clean(x): sanitizing already-sanitized content is a no-op.
    sanitizer = HtmlSanitizer()
    once = sanitizer._clean(raw)
    twice = sanitizer._clean(once)
    assert once == twice


@pytest.mark.parametrize("raw", _CORPUS)
async def test_mutate_output_passes_revalidation(raw: str):
    # Feeding mutate's output back through validate must pass — the guarantee that
    # makes the repair trustworthy.
    guard = MutationGuard("t").use(HtmlSanitizer())
    first = await guard.validate(raw)
    assert first.was_mutated is True
    second = await guard.validate(first.output)
    assert second.was_mutated is False
    assert second.has_failures is False


async def test_clean_input_is_a_noop_on_mutate():
    guard = MutationGuard("t").use(HtmlSanitizer())
    result = await guard.validate("<p>already clean</p>")
    assert result.was_mutated is False


async def test_determinism_same_input_same_output():
    sanitizer = HtmlSanitizer()
    payload = '<div onclick="x"><script>y</script><a href="javascript:z">t</a></div>'
    outputs = {sanitizer._clean(payload) for _ in range(5)}
    assert len(outputs) == 1


# ---------------------------------------------------------------------------
# Multi-part — only changed parts are addressed, order is preserved
# ---------------------------------------------------------------------------


async def test_only_changed_parts_are_mutated_and_order_preserved():
    guard = MutationGuard("t").use(HtmlSanitizer())
    result = await guard.validate(_view("<script>x</script>bad", "already clean", "<b>fix"))
    assert isinstance(result.output, ContentView)
    assert [i.id for i in result.output.items] == ["p0", "p1", "p2"]
    assert result.output.items[0].value == "bad"
    assert result.output.items[1].value == "already clean"  # untouched, in place
    assert result.output.items[2].value == "<b>fix</b>"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


async def test_tightened_default_schemes_strip_ftp_and_tel():
    # ftp: and tel: are outside the default {http, https, mailto}.
    guard = MutationGuard("t").use(HtmlSanitizer())
    result = await guard.validate('<a href="ftp://host/f">a</a><a href="tel:123">b</a>')
    assert "ftp:" not in result.output
    assert "tel:" not in result.output


async def test_custom_allowed_tags_replaces_default():
    # Allowing only {b} means <i> is stripped (its text kept) while <b> survives.
    sanitizer = HtmlSanitizer(allowed_tags={"b"})
    result = await MutationGuard("t").use(sanitizer).validate("<b>keep</b><i>plain</i>")
    assert result.output == "<b>keep</b>plain"


async def test_inline_css_opt_in_keeps_allowed_property():
    sanitizer = HtmlSanitizer(
        allowed_tags={"p"},
        allowed_attributes={"p": {"style"}},
        allowed_css_properties={"color"},
    )
    result = await MutationGuard("t").use(sanitizer).validate('<p style="color:red">hi</p>')
    assert result.output == '<p style="color:red">hi</p>'
    assert result.was_mutated is False


async def test_inline_css_strips_javascript_url_value():
    # nh3 sanitizes CSS values: a url() with a disallowed scheme is dropped even on
    # an allowlisted property.
    sanitizer = HtmlSanitizer(
        allowed_tags={"p"},
        allowed_attributes={"p": {"style"}},
        allowed_css_properties={"background-image"},
    )
    result = (
        await MutationGuard("t").use(sanitizer).validate('<p style="background-image:url(javascript:alert(1))">x</p>')
    )
    assert "javascript:" not in result.output


async def test_curated_safe_css_excludes_script_bearing_properties():
    # The curated set must never grant the legacy script-binding properties.
    assert "behavior" not in SAFE_CSS_PROPERTIES
    assert "-moz-binding" not in SAFE_CSS_PROPERTIES


async def test_caller_allowlisted_css_property_is_honored():
    # `allowed_css_properties` is honored as given, the same contract as
    # allowed_tags / allowed_url_schemes. A caller who allowlists a property gets
    # it — including `behavior`, which loads script through a url() that nh3's
    # value-level filtering does not catch. Allowlisting it is the caller's
    # explicit, documented-as-unsafe choice; safety comes from the safe defaults,
    # not from silently overriding the request.
    sanitizer = HtmlSanitizer(
        allowed_tags={"p"},
        allowed_attributes={"p": {"style"}},
        allowed_css_properties={"color", "behavior"},
    )
    result = await MutationGuard("t").use(sanitizer).validate('<p style="color:red;behavior:url(x.htc)">hi</p>')
    assert "color:red" in result.output
    assert "behavior" in result.output  # honored, not silently stripped


async def test_css_off_by_default_drops_style_even_when_attribute_allowed():
    # allowed_css_properties=None means no inline CSS, so `style` is removed even
    # if the caller lists it in allowed_attributes.
    sanitizer = HtmlSanitizer(allowed_tags={"p"}, allowed_attributes={"p": {"style"}})
    result = await MutationGuard("t").use(sanitizer).validate('<p style="color:red">hi</p>')
    assert result.output == "<p>hi</p>"


async def test_caller_allowlisted_event_handler_is_honored():
    # A caller-supplied allowed_attributes replaces the default entirely and is
    # honored as given — the same contract as allowed_tags / allowed_url_schemes.
    # Allowlisting `onclick` is an explicit, unmistakable opt-in, no different from
    # allowlisting the `javascript` scheme; the caller owns that choice. The default
    # (no custom attributes) still carries no event handlers — see
    # test_event_handler_attribute_dropped.
    sanitizer = HtmlSanitizer(allowed_tags={"a"}, allowed_attributes={"a": {"href", "onclick"}})
    result = await MutationGuard("t").use(sanitizer).validate('<a href="http://x" onclick="alert(1)">y</a>')
    assert "onclick" in result.output  # honored, not silently stripped


async def test_style_off_is_case_insensitive():
    # The CSS-off rule drops `style` regardless of case when inline CSS is disabled.
    sanitizer = HtmlSanitizer(allowed_tags={"p"}, allowed_attributes={"p": {"STYLE"}})
    result = await MutationGuard("t").use(sanitizer).validate('<p STYLE="color:red">hi</p>')
    assert result.output == "<p>hi</p>"
