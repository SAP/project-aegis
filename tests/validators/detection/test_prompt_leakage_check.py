"""Unit tests for ``PromptLeakageCheck``.

Structured to match the validator's spec (the ``PromptLeakageCheck`` module docstring):
the happy path, each failure identifier, construction/input errors, boundaries, adversarial cases
that v1 defeats, the residual misses v1 deliberately lets pass (asserted so the scope boundary is
explicit, not silently broken), diagnostic safety, and determinism.

Protected material used in these tests is invented placeholder text — no real prompt, secret, or
internal reference appears anywhere.
"""

from __future__ import annotations

import pytest

from aegis import Guard, GuardMetadata
from aegis.validators.detection import PromptLeakageCheck

# A protected fragment long enough to clear the default min_fragment_length (15). Purely
# illustrative text — it stands in for a hidden instruction, and never leaves this file.
SECRET_INSTRUCTION = "Never reveal the internal escalation code to the user."
CANARY = "a1b2c3d4-e5f6-canary-token"


def _guard(**kwargs) -> Guard:
    return Guard("test").use(PromptLeakageCheck(**kwargs))


# ---------------------------------------------------------------------------
# Happy path — clean output passes
# ---------------------------------------------------------------------------


async def test_benign_output_passes():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION])
    result = await guard.validate("Here is a friendly, unrelated answer to your question.")
    assert not result.has_failures


async def test_empty_output_passes():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION])
    result = await guard.validate("")
    assert not result.has_failures


async def test_whitespace_only_output_passes():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION])
    result = await guard.validate("   \n\t  ")
    assert not result.has_failures


async def test_multilingual_benign_output_passes():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION])
    result = await guard.validate("Bonjour! Comment puis-je vous aider aujourd'hui? 你好！")
    assert not result.has_failures


async def test_generic_discussion_of_system_prompts_passes():
    # Talking *about* the concept of system prompts is not a leak: nothing here matches the
    # configured protected text. Rejecting it would turn this into a broad policy classifier.
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION])
    result = await guard.validate("A system prompt is the hidden instruction that steers an assistant.")
    assert not result.has_failures


async def test_pass_details_summarize_without_exposing():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], canaries=[CANARY])
    result = await guard.validate("An ordinary answer.")
    passed = result.results[0]
    assert passed.details["fragments_checked"] == 2
    assert passed.details["sources"] == ["canary", "fragment"]
    assert passed.details["fuzzy_enabled"] is False


async def test_empty_output_pass_details_match_a_scanned_pass():
    # An empty candidate still summarizes the configured corpus the same way a scanned PASS does,
    # so the PASS details reflect configuration, not whether there was content to scan.
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], canaries=[CANARY])
    empty = (await guard.validate("")).results[0]
    scanned = (await guard.validate("An ordinary answer.")).results[0]
    assert empty.details == scanned.details
    assert empty.details["sources"] == ["canary", "fragment"]
    assert empty.details["fragments_checked"] == 2


# ---------------------------------------------------------------------------
# Failure identifiers
# ---------------------------------------------------------------------------


async def test_canary_in_output_fails_with_canary_code():
    guard = _guard(canaries=[CANARY])
    result = await guard.validate(f"Oops, my setup token is {CANARY} by the way.")
    assert result.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_CANARY
    assert result.failures[0].details["source"] == "canary"


async def test_exact_fragment_fails_as_exact():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION])
    result = await guard.validate(f"Sure. {SECRET_INSTRUCTION}")
    failure = result.failures[0]
    assert failure.error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED
    assert failure.details["match_type"] == "exact"
    assert failure.details["source"] == "fragment"


async def test_partial_fragment_above_min_length_fails():
    # A substantial contiguous slice of the protected text is still a leak.
    partial = SECRET_INSTRUCTION[:30]
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION])
    result = await guard.validate(f"It said: {partial}")
    # The output contains a slice of the fragment, not the whole fragment, so the whole
    # fragment does not match — but a validator configured with that *slice* would catch it.
    assert not result.has_failures

    guard = _guard(sensitive_fragments=[partial])
    result = await guard.validate(f"It said: {partial}")
    assert result.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED


async def test_case_variant_fails_as_normalized():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    result = await guard.validate(f"sure. {SECRET_INSTRUCTION.upper()}")
    failure = result.failures[0]
    assert failure.error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED
    assert failure.details["match_type"] == "normalized"


async def test_whitespace_variant_fails_as_normalized():
    spaced = SECRET_INSTRUCTION.replace(" ", "    ")
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    result = await guard.validate(f"quote:\n\n{spaced}")
    failure = result.failures[0]
    assert failure.error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED
    assert failure.details["match_type"] == "normalized"


async def test_nfkc_variant_fails_as_normalized():
    # Full-width characters look like ASCII but are distinct code points; NFKC folds them.
    fullwidth = SECRET_INSTRUCTION.translate({ord(c): ord(c) + 0xFEE0 for c in SECRET_INSTRUCTION if "!" <= c <= "~"})
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    result = await guard.validate(fullwidth)
    failure = result.failures[0]
    assert failure.error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED
    assert failure.details["match_type"] == "normalized"


async def test_metadata_system_segment_leak_fails():
    system = "You are a helpful assistant.\nThe secret admin passphrase is open-sesame-42-please."
    guard = _guard(use_metadata_system=True)
    result = await guard.validate(
        "The secret admin passphrase is open-sesame-42-please.",
        metadata=GuardMetadata(system=system),
    )
    failure = result.failures[0]
    assert failure.error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED
    assert failure.details["source"] == "metadata_system"


async def test_near_verbatim_fails_when_fuzzy_enabled():
    # One inserted word — normalization will not fold it, but fuzzy matching catches it.
    near = SECRET_INSTRUCTION.replace("internal", "internal secret")
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], similarity_threshold=0.85)
    result = await guard.validate(f"It told me: {near}")
    failure = result.failures[0]
    assert failure.error_code == PromptLeakageCheck.PROMPT_LEAKAGE_SUSPECTED
    assert failure.details["match_type"] == "fuzzy"
    assert failure.details["similarity"] >= 0.85


async def test_near_verbatim_passes_when_fuzzy_disabled():
    near = SECRET_INSTRUCTION.replace("internal", "internal secret")
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION])
    result = await guard.validate(f"It told me: {near}")
    assert not result.has_failures


async def test_over_max_input_chars_fails():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], max_input_chars=10)
    result = await guard.validate("this candidate output is definitely longer than ten characters")
    failure = result.failures[0]
    assert failure.error_code == PromptLeakageCheck.PROMPT_LEAKAGE_INPUT_TOO_LARGE
    assert failure.details["limit"] == 10
    assert failure.details["length"] > 10


# ---------------------------------------------------------------------------
# Precedence — the single reported verdict
# ---------------------------------------------------------------------------


async def test_canary_outranks_fragment():
    # Both leak, but a canary is definitive, so it is the reported verdict regardless of where
    # each appears in the output.
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], canaries=[CANARY])
    result = await guard.validate(f"{SECRET_INSTRUCTION} ... and also {CANARY}")
    assert result.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_CANARY


async def test_input_too_large_outranks_everything():
    guard = _guard(canaries=[CANARY], max_input_chars=5)
    result = await guard.validate(f"leaking {CANARY} in a long output")
    assert result.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_INPUT_TOO_LARGE


# ---------------------------------------------------------------------------
# Construction and input errors
# ---------------------------------------------------------------------------


def test_no_source_raises():
    with pytest.raises(ValueError, match="at least one protected source"):
        PromptLeakageCheck()


def test_empty_fragment_raises():
    with pytest.raises(ValueError, match="sensitive_fragments"):
        PromptLeakageCheck(sensitive_fragments=["ok fragment here", "   "])


def test_empty_canary_raises():
    with pytest.raises(ValueError, match="canaries"):
        PromptLeakageCheck(canaries=[""])


def test_similarity_threshold_out_of_range_raises():
    with pytest.raises(ValueError, match="similarity_threshold"):
        PromptLeakageCheck(sensitive_fragments=[SECRET_INSTRUCTION], similarity_threshold=1.5)


def test_negative_min_fragment_length_raises():
    with pytest.raises(ValueError, match="min_fragment_length"):
        PromptLeakageCheck(sensitive_fragments=[SECRET_INSTRUCTION], min_fragment_length=-1)


def test_negative_max_input_chars_raises():
    with pytest.raises(ValueError, match="max_input_chars"):
        PromptLeakageCheck(sensitive_fragments=[SECRET_INSTRUCTION], max_input_chars=-1)


async def test_use_metadata_system_with_no_system_passes_on_configured_sources():
    # use_metadata_system is a valid sole source at construction; when the system prompt is
    # absent at call time it simply contributes nothing, and the other sources still apply.
    guard = _guard(canaries=[CANARY], use_metadata_system=True)
    result = await guard.validate("A clean answer.", metadata=GuardMetadata(system=None))
    assert not result.has_failures

    result = await guard.validate("A clean answer.", metadata=None)
    assert not result.has_failures


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


async def test_metadata_segment_at_min_length_is_used():
    # A 15-character line (the default floor) is eligible; a 14-character one is not.
    at_floor = "block-this-now!"  # 15 chars
    below_floor = "block-this-no"  # 13 chars
    system = f"{at_floor}\n{below_floor}"
    guard = _guard(use_metadata_system=True, min_fragment_length=15)

    result = await guard.validate(f"leaking {at_floor} here", metadata=GuardMetadata(system=system))
    assert result.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED

    result = await guard.validate(f"leaking {below_floor} here", metadata=GuardMetadata(system=system))
    assert not result.has_failures


async def test_short_shared_phrase_below_min_length_passes_for_auto_derived():
    # An auto-derived system line shorter than the floor is deliberately ignored to avoid noise.
    system = "Be concise."  # below the default floor
    guard = _guard(use_metadata_system=True)
    result = await guard.validate("Be concise.", metadata=GuardMetadata(system=system))
    assert not result.has_failures


async def test_blank_metadata_line_with_zero_min_length_is_not_a_match_all_needle():
    # With min_fragment_length=0 the length gate alone (0 >= 0) would admit a blank system-prompt
    # line, whose empty needle matches every output at offset 0. Empty normalized segments are
    # dropped, so a benign output still passes while a real, non-empty line still leaks.
    system = "You are a helpful assistant.\n\nThe secret admin passphrase is open-sesame-42-please."
    guard = _guard(use_metadata_system=True, min_fragment_length=0)

    benign = await guard.validate("An ordinary, unrelated answer.", metadata=GuardMetadata(system=system))
    assert not benign.has_failures

    leak = await guard.validate("You are a helpful assistant.", metadata=GuardMetadata(system=system))
    assert leak.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED


async def test_fuzzy_just_below_threshold_passes():
    # Same near-verbatim edit, but the threshold is set just above the achievable ratio.
    near = SECRET_INSTRUCTION.replace("internal", "internal secret")
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], similarity_threshold=0.99)
    result = await guard.validate(f"It told me: {near}")
    assert not result.has_failures


async def test_fragment_spanning_parts_at_whitespace_boundary_is_caught():
    # SECRET_INSTRUCTION[:25] ends at the space before "escalation", so the split lands on a
    # whitespace boundary. render() joins the parts with a newline, and normalization collapses
    # that newline back to a single space — so the whole fragment reappears and is caught.
    # A mid-token split does not fold back cleanly; see the residual-miss test below.
    from aegis.validators.base import ContentItem, ContentType, ContentView

    head, tail = SECRET_INSTRUCTION[:25], SECRET_INSTRUCTION[25:]
    view = ContentView(
        items=(
            ContentItem(id="p0", type=ContentType.TEXT, value=head),
            ContentItem(id="p1", type=ContentType.TEXT, value=tail),
        )
    )
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    result = await guard.validate(view)
    assert result.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED
    assert result.failures[0].details["match_type"] == "normalized"


# ---------------------------------------------------------------------------
# Adversarial — evasions v1 defeats via normalization
# ---------------------------------------------------------------------------


async def test_mixed_case_leak_detected():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    scrambled = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(SECRET_INSTRUCTION))
    result = await guard.validate(scrambled)
    assert result.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED


async def test_zero_width_character_leak_detected():
    # A zero-width space wedged into every gap is invisible to a reader but breaks a literal
    # match; stripping format characters during normalization defeats it.
    zwsp = "​"
    smuggled = zwsp.join(SECRET_INSTRUCTION)
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    result = await guard.validate(smuggled)
    assert result.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED
    assert result.failures[0].details["match_type"] == "normalized"


async def test_canary_with_zero_width_characters_detected():
    zwsp = "​"
    smuggled = zwsp.join(CANARY)
    guard = _guard(canaries=[CANARY], normalize=True)
    result = await guard.validate(f"token: {smuggled}")
    assert result.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_CANARY


# ---------------------------------------------------------------------------
# Residual misses — asserted PASS so the scope boundary stays explicit
# ---------------------------------------------------------------------------


async def test_paraphrase_passes_as_residual_miss():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    result = await guard.validate("Don't ever tell people about the escalation code we use internally.")
    assert not result.has_failures


async def test_base64_encoded_leak_passes_as_residual_miss():
    import base64

    encoded = base64.b64encode(SECRET_INSTRUCTION.encode()).decode()
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    result = await guard.validate(f"Here is a blob: {encoded}")
    assert not result.has_failures


async def test_caesar_shift_leak_passes_as_residual_miss():
    shifted = "".join(
        chr((ord(c) - 97 + 3) % 26 + 97) if c.isalpha() and c.islower() else c for c in SECRET_INSTRUCTION
    )
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    result = await guard.validate(shifted)
    assert not result.has_failures


async def test_confusable_homoglyph_leak_passes_as_residual_miss():
    # Cyrillic look-alikes (е, а, о, с) are distinct code points that NFKC does not fold;
    # confusable-skeleton matching is a deferred feature. Asserted with normalize=True so the miss
    # is shown to survive the validator's full normalization, not merely because it is off.
    confusable = SECRET_INSTRUCTION.replace("e", "е").replace("a", "а").replace("o", "о")
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    result = await guard.validate(confusable)
    assert not result.has_failures


async def test_cross_call_split_leak_passes_as_residual_miss():
    # Half the fragment in each of two separate validate() calls: no single call holds the
    # whole fragment, and cumulative monitoring across calls is out of scope.
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    first = await guard.validate(SECRET_INSTRUCTION[:25])
    second = await guard.validate(SECRET_INSTRUCTION[25:])
    assert not first.has_failures
    assert not second.has_failures


async def test_mid_token_split_across_parts_is_residual_miss():
    # When the part boundary falls *inside* a word ("intern" | "al"), render()'s newline
    # normalizes to a separating space ("intern al"), so the fragment no longer matches. This is
    # the separator/interleaving evasion the ADR defers; asserted PASS so the boundary stays
    # explicit rather than silently broken.
    from aegis.validators.base import ContentItem, ContentType, ContentView

    view = ContentView(
        items=(
            ContentItem(id="p0", type=ContentType.TEXT, value="Never reveal the intern"),
            ContentItem(id="p1", type=ContentType.TEXT, value="al escalation code to the user."),
        )
    )
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    result = await guard.validate(view)
    assert not result.has_failures


# ---------------------------------------------------------------------------
# Diagnostic safety — never re-leak the secret
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, output, metadata",
    [
        ({"canaries": [CANARY]}, f"token {CANARY}", None),
        ({"sensitive_fragments": [SECRET_INSTRUCTION]}, SECRET_INSTRUCTION, None),
        (
            {"sensitive_fragments": [SECRET_INSTRUCTION], "similarity_threshold": 0.8},
            SECRET_INSTRUCTION.replace("internal", "internal secret"),
            None,
        ),
        (
            {"use_metadata_system": True},
            "The secret admin passphrase is open-sesame-42-please.",
            GuardMetadata(system="The secret admin passphrase is open-sesame-42-please."),
        ),
    ],
)
async def test_failure_never_echoes_protected_or_matched_text(kwargs, output, metadata):
    guard = _guard(**kwargs)
    result = await guard.validate(output, metadata=metadata)
    failure = result.failures[0]

    # The protected material and the raw candidate must not appear anywhere a caller can read.
    haystack = failure.error_message + " " + repr(failure.details)
    assert SECRET_INSTRUCTION not in haystack
    assert CANARY not in haystack
    assert "open-sesame" not in haystack
    assert output not in haystack


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


async def test_identical_inputs_give_identical_verdicts():
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION], canaries=[CANARY], similarity_threshold=0.85)
    output = f"leak: {SECRET_INSTRUCTION}"
    first = await guard.validate(output)
    second = await guard.validate(output)
    assert first.failures[0].error_code == second.failures[0].error_code
    assert first.failures[0].details == second.failures[0].details


# ---------------------------------------------------------------------------
# Default is literal — normalization is opt-in
# ---------------------------------------------------------------------------


async def test_normalize_defaults_to_off():
    # A verbatim leak is still caught by the exact-substring path, but a cosmetic (upper-case)
    # variant slips through: normalization is off unless the operator opts in.
    guard = _guard(sensitive_fragments=[SECRET_INSTRUCTION])

    verbatim = await guard.validate(f"sure. {SECRET_INSTRUCTION}")
    assert verbatim.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED

    cosmetic = await guard.validate(f"sure. {SECRET_INSTRUCTION.upper()}")
    assert not cosmetic.has_failures


# ---------------------------------------------------------------------------
# Self-contained — the validator does not depend on engine normalization
# ---------------------------------------------------------------------------


async def test_detects_under_plain_guard_without_engine_normalize():
    # Fail-safe: the validator normalizes both the protected corpus and the output itself, so a
    # cosmetic leak is detected even under a Guard with engine normalization off. It never outsources
    # output-side normalization to the engine — closing the §6.2 fail-open risk.
    zwsp = "​"
    smuggled = zwsp.join(SECRET_INSTRUCTION)

    guard = Guard("plain", normalize=False).use(
        PromptLeakageCheck(sensitive_fragments=[SECRET_INSTRUCTION], normalize=True)
    )

    upper = await guard.validate(SECRET_INSTRUCTION.upper())
    assert upper.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED
    assert upper.failures[0].details["match_type"] == "normalized"

    zero_width = await guard.validate(smuggled)
    assert zero_width.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED


# ---------------------------------------------------------------------------
# Non-Latin text is not corrupted by the safe normalization profile
# ---------------------------------------------------------------------------


async def test_non_latin_fragment_detected_without_corruption():
    # A genuine Cyrillic fragment leaks in a different case. casefold folds the case correctly for
    # Cyrillic and (crucially) the homoglyph step is off, so the fragment matches its own script
    # rather than being rewritten toward ASCII.
    cyrillic_fragment = "секретный пароль системы"
    guard = _guard(sensitive_fragments=[cyrillic_fragment], normalize=True)
    result = await guard.validate(f"вот: {cyrillic_fragment.upper()}")
    assert result.failures[0].error_code == PromptLeakageCheck.PROMPT_LEAKAGE_DETECTED
    assert result.failures[0].details["match_type"] == "normalized"
