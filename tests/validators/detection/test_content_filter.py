"""Unit tests for ``ContentFilter``.

Structured to match the validator's spec (the ``ContentFilter`` module docstring): the
happy path, each failure identifier, construction/input errors, precision (Scunthorpe), normalization
strength, boundaries, adversarial cases v1 defeats, the residual misses v1 deliberately lets pass
(asserted so the scope boundary is explicit, not silently broken), diagnostic safety, and
determinism.

Blocked terms used in these tests are invented, generic placeholder words — no real, confidential,
or internal term appears anywhere.
"""

from __future__ import annotations

import pytest

from aegis import Guard
from aegis.validators.detection import ContentFilter

# Generic placeholder terms. "politics" stands in for a banned single word, "credit card" for a
# multi-word phrase, and, inline in the Scunthorpe tests, "cat" for the substring case ("cat" is a
# substring of "category").
BLOCKED_WORD = "politics"
BLOCKED_PHRASE = "credit card"


def _guard(**kwargs) -> Guard:
    return Guard("test").use(ContentFilter(**kwargs))


# ---------------------------------------------------------------------------
# Happy path — clean text passes
# ---------------------------------------------------------------------------


async def test_benign_text_passes():
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate("Here is a friendly, unrelated sentence about gardening.")
    assert not result.has_failures


async def test_empty_text_passes():
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate("")
    assert not result.has_failures


async def test_whitespace_only_text_passes():
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate("   \n\t  ")
    assert not result.has_failures


async def test_discussing_subject_without_the_term_passes():
    # The text is about the subject but never uses the configured word. Rejecting it would require
    # a semantic judge, which this validator deliberately is not.
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate("The election debate raised many questions about public policy.")
    assert not result.has_failures


async def test_multilingual_benign_text_passes():
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate("Bonjour! Comment puis-je vous aider aujourd'hui? 你好！")
    assert not result.has_failures


async def test_pass_details_summarize_configuration():
    guard = _guard(blocked_topics=[BLOCKED_WORD, BLOCKED_PHRASE])
    result = await guard.validate("An ordinary answer.")
    passed = result.results[0]
    assert passed.details["terms_checked"] == 2
    assert passed.details["word_boundary"] is True


# ---------------------------------------------------------------------------
# Failure identifiers
# ---------------------------------------------------------------------------


async def test_blocked_word_fails_with_content_blocked():
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate(f"Let's talk about {BLOCKED_WORD} today.")
    failure = result.failures[0]
    assert failure.error_code == ContentFilter.CONTENT_BLOCKED
    assert failure.details["matched_index"] == 0
    assert failure.details["match_count"] == 1
    assert failure.details["match_offset"] == len("Let's talk about ")


async def test_matched_topic_absent_by_default():
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate(f"About {BLOCKED_WORD}.")
    assert "matched_topic" not in result.failures[0].details


async def test_matched_topic_present_when_opted_in():
    guard = _guard(blocked_topics=[BLOCKED_WORD], report_matched_topic=True)
    result = await guard.validate(f"About {BLOCKED_WORD}.")
    assert result.failures[0].details["matched_topic"] == BLOCKED_WORD


async def test_multi_word_phrase_with_variable_spacing_fails():
    # The phrase appears with irregular internal spacing; whitespace collapse folds it back so the
    # phrase still matches.
    guard = _guard(blocked_topics=[BLOCKED_PHRASE], normalize=True)
    result = await guard.validate("Please enter your credit    card number.")
    failure = result.failures[0]
    assert failure.error_code == ContentFilter.CONTENT_BLOCKED
    assert failure.details["matched_index"] == 0


async def test_over_max_input_chars_fails():
    guard = _guard(blocked_topics=[BLOCKED_WORD], max_input_chars=10)
    result = await guard.validate("this text is definitely longer than ten characters")
    failure = result.failures[0]
    assert failure.error_code == ContentFilter.CONTENT_FILTER_INPUT_TOO_LARGE
    assert failure.details["limit"] == 10
    assert failure.details["length"] > 10


async def test_input_too_large_outranks_content_blocked():
    # Both would fail, but the size valve is the highest-precedence verdict and short-circuits before
    # any scanning.
    guard = _guard(blocked_topics=[BLOCKED_WORD], max_input_chars=5)
    result = await guard.validate(f"talk about {BLOCKED_WORD}")
    assert result.failures[0].error_code == ContentFilter.CONTENT_FILTER_INPUT_TOO_LARGE


# ---------------------------------------------------------------------------
# Construction and input errors
# ---------------------------------------------------------------------------


def test_empty_blocked_topics_raises():
    with pytest.raises(ValueError, match="at least one blocked topic"):
        ContentFilter(blocked_topics=[])


def test_bare_string_blocked_topics_raises():
    # A bare string is iterable, so without this guard it would silently become a list of
    # one-character terms that over-block almost everything. Reject it at construction.
    with pytest.raises(ValueError, match="not a single string"):
        ContentFilter(blocked_topics="politics")


def test_whitespace_only_term_raises():
    with pytest.raises(ValueError, match="blocked_topics"):
        ContentFilter(blocked_topics=["ok term", "   "])


def test_term_empty_after_normalization_raises():
    # A term made entirely of zero-width format characters survives the .strip() check (a zero-width
    # space is not stripped by str.strip) but normalizes to nothing — still a match-everything needle.
    # Only reachable with normalize=True, since that is when the term is normalized at construction.
    zwsp = "​"
    with pytest.raises(ValueError, match="after normalization"):
        ContentFilter(blocked_topics=[zwsp * 3], normalize=True)


def test_negative_max_input_chars_raises():
    with pytest.raises(ValueError, match="max_input_chars"):
        ContentFilter(blocked_topics=[BLOCKED_WORD], max_input_chars=-1)


# ---------------------------------------------------------------------------
# Precision — the Scunthorpe problem
# ---------------------------------------------------------------------------


async def test_term_inside_longer_word_passes_in_word_boundary_mode():
    # "cat" is a substring of "category", but word-boundary mode matches only the discrete word, so
    # the benign longer word is not rejected — the Scunthorpe false positive is avoided.
    guard = _guard(blocked_topics=["cat"])
    result = await guard.validate("Pick a category from the menu.")
    assert not result.has_failures


async def test_term_inside_longer_word_fails_in_substring_mode():
    # The operator opted into substring matching, so the same longer word now matches.
    guard = _guard(blocked_topics=["cat"], word_boundary=False)
    result = await guard.validate("Pick a category from the menu.")
    assert result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


async def test_regex_metacharacter_term_matched_literally():
    # "C++" contains regex metacharacters. It must match the literal text "C++", and the term must
    # not behave as a pattern (e.g. "C" followed by any repetition).
    guard = _guard(blocked_topics=["C++"])
    hit = await guard.validate("I write C++ every day.")
    assert hit.failures[0].error_code == ContentFilter.CONTENT_BLOCKED

    miss = await guard.validate("I gave it a grade of C on the test.")
    assert not miss.has_failures


# ---------------------------------------------------------------------------
# Normalization strength
# ---------------------------------------------------------------------------


async def test_full_width_variant_fails_with_compatibility_folding():
    # Full-width letters look like ASCII but are distinct code points; NFKC (the normalize default)
    # folds them.
    full_width = "ｐｏｌｉｔｉｃｓ"
    guard = _guard(blocked_topics=[BLOCKED_WORD], normalize=True)
    result = await guard.validate(f"Learn {full_width} here.")
    assert result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


async def test_full_width_variant_passes_with_canonical_folding():
    # With compatibility_folding=False, canonical NFC no longer folds full-width forms, so the
    # full-width spelling is a documented miss. Asserted so the trade-off stays explicit.
    full_width = "ｐｏｌｉｔｉｃｓ"
    guard = _guard(blocked_topics=[BLOCKED_WORD], normalize=True, compatibility_folding=False)
    result = await guard.validate(f"Learn {full_width} here.")
    assert not result.has_failures


async def test_casefold_matches_sharp_s():
    # casefold (not lower) folds the German ß to "ss", so a term "fuss" matches the text "Fuß".
    guard = _guard(blocked_topics=["fuss"], normalize=True)
    result = await guard.validate("So viel Fuß um nichts.")
    assert result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


async def test_case_sensitive_skips_casefolding():
    guard = _guard(blocked_topics=[BLOCKED_WORD], normalize=True, case_sensitive=True)
    lower = await guard.validate(f"about {BLOCKED_WORD}")
    assert lower.failures[0].error_code == ContentFilter.CONTENT_BLOCKED

    upper = await guard.validate(f"about {BLOCKED_WORD.upper()}")
    assert not upper.has_failures


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


async def test_term_at_start_and_end_of_text_matches():
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    at_start = await guard.validate(f"{BLOCKED_WORD} is the subject.")
    assert at_start.failures[0].details["match_offset"] == 0

    at_end = await guard.validate(f"The subject is {BLOCKED_WORD}")
    assert at_end.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


async def test_term_adjacent_to_punctuation_matches():
    # A word boundary sits between the term and adjacent punctuation, so the term still matches.
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate(f"Really?! {BLOCKED_WORD}, again.")
    assert result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


async def test_term_in_non_first_part_of_view_matches():
    # render() joins parts with a newline, so a term in any part is scanned.
    from aegis.validators.base import ContentItem, ContentType, ContentView

    view = ContentView(
        items=(
            ContentItem(id="p0", type=ContentType.TEXT, value="A harmless opening line."),
            ContentItem(id="p1", type=ContentType.TEXT, value=f"Then a line about {BLOCKED_WORD}."),
        )
    )
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate(view)
    assert result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


async def test_multiple_matches_report_earliest_and_count():
    # Two distinct terms both appear; the verdict reports the earliest offset and counts both.
    guard = _guard(blocked_topics=[BLOCKED_PHRASE, BLOCKED_WORD])
    # BLOCKED_WORD ("politics") appears before BLOCKED_PHRASE ("credit card") in the text.
    result = await guard.validate(f"First {BLOCKED_WORD}, then {BLOCKED_PHRASE}.")
    failure = result.failures[0]
    assert failure.details["match_count"] == 2
    # The earliest-offset match wins, so the reported index is BLOCKED_WORD's (index 1), not the
    # smaller list index of BLOCKED_PHRASE (index 0).
    assert failure.details["matched_index"] == 1
    assert failure.details["match_offset"] == len("First ")


async def test_repeated_term_counts_once():
    # The same term appearing several times is one distinct match, so match_count is 1.
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate(f"{BLOCKED_WORD} and more {BLOCKED_WORD} and yet more {BLOCKED_WORD}.")
    assert result.failures[0].details["match_count"] == 1


# ---------------------------------------------------------------------------
# Adversarial — evasions v1 defeats via normalization
# ---------------------------------------------------------------------------


async def test_mixed_case_evasion_detected():
    guard = _guard(blocked_topics=[BLOCKED_WORD], normalize=True)
    scrambled = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(BLOCKED_WORD))
    result = await guard.validate(f"Read about {scrambled} now.")
    assert result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


async def test_zero_width_character_evasion_detected():
    # A zero-width space wedged into the term is invisible to a reader but breaks a literal match;
    # stripping format characters during normalization defeats it.
    zwsp = "​"
    smuggled = zwsp.join(BLOCKED_WORD)
    guard = _guard(blocked_topics=[BLOCKED_WORD], normalize=True)
    result = await guard.validate(f"About {smuggled} today.")
    assert result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


async def test_soft_hyphen_evasion_detected():
    # A soft hyphen (U+00AD) is a category-Cf format character, stripped during normalization.
    shy = "­"
    smuggled = shy.join(BLOCKED_WORD)
    guard = _guard(blocked_topics=[BLOCKED_WORD], normalize=True)
    result = await guard.validate(f"About {smuggled} today.")
    assert result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


async def test_extra_whitespace_evasion_detected():
    # Irregular whitespace inside a phrase collapses to a single space before matching.
    guard = _guard(blocked_topics=[BLOCKED_PHRASE], normalize=True)
    result = await guard.validate("a credit\n\tcard here")
    assert result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


# ---------------------------------------------------------------------------
# Residual misses — asserted PASS so the scope boundary stays explicit
# ---------------------------------------------------------------------------


async def test_homoglyph_evasion_passes_as_residual_miss():
    # Cyrillic look-alikes are distinct code points that NFKC does not fold; confusable-skeleton
    # matching is a deferred feature. Asserted with normalize=True so the miss is shown to survive
    # the validator's full normalization, not merely because normalization is off.
    confusable = BLOCKED_WORD.replace("o", "о").replace("i", "і")  # Cyrillic о, і
    guard = _guard(blocked_topics=[BLOCKED_WORD], normalize=True)
    result = await guard.validate(f"Learn {confusable} here.")
    assert not result.has_failures


async def test_leetspeak_evasion_passes_as_residual_miss():
    guard = _guard(blocked_topics=[BLOCKED_WORD], normalize=True)
    result = await guard.validate("Learn p0l1t1cs here.")
    assert not result.has_failures


async def test_base64_encoded_term_passes_as_residual_miss():
    import base64

    encoded = base64.b64encode(BLOCKED_WORD.encode()).decode()
    guard = _guard(blocked_topics=[BLOCKED_WORD], normalize=True)
    result = await guard.validate(f"Here is a blob: {encoded}")
    assert not result.has_failures


async def test_paraphrase_passes_as_residual_miss():
    guard = _guard(blocked_topics=[BLOCKED_WORD], normalize=True)
    result = await guard.validate("The affairs of government and the state are on my mind.")
    assert not result.has_failures


async def test_cjk_term_without_delimiters_passes_in_word_boundary_mode():
    # A CJK term embedded without spaces is a residual miss in word-boundary mode: UAX #29 word
    # boundaries are unreliable for scriptio-continua. The operator should use word_boundary=False.
    cjk_term = "赌博"  # a placeholder CJK term
    guard = _guard(blocked_topics=[cjk_term])
    result = await guard.validate("这里有很多赌博相关的内容。")
    assert not result.has_failures

    # With substring matching it is caught — the documented escape hatch for these languages.
    substring_guard = _guard(blocked_topics=[cjk_term], word_boundary=False)
    caught = await substring_guard.validate("这里有很多赌博相关的内容。")
    assert caught.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


# ---------------------------------------------------------------------------
# Diagnostic safety — never echo the surrounding input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("report_matched_topic", [False, True])
async def test_failure_never_echoes_surrounding_input(report_matched_topic):
    surrounding = "my private diary entry mentioning"
    guard = _guard(blocked_topics=[BLOCKED_WORD], report_matched_topic=report_matched_topic)
    result = await guard.validate(f"{surrounding} {BLOCKED_WORD} in passing")
    failure = result.failures[0]

    # The surrounding user text must never appear in anything a caller can read.
    haystack = failure.error_message + " " + repr(failure.details)
    assert surrounding not in haystack


async def test_failure_hides_the_term_unless_opted_in():
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate(f"about {BLOCKED_WORD}")
    failure = result.failures[0]
    haystack = failure.error_message + " " + repr(failure.details)
    assert BLOCKED_WORD not in haystack


# ---------------------------------------------------------------------------
# Default is literal — normalization is opt-in
# ---------------------------------------------------------------------------


async def test_normalize_defaults_to_off():
    # The clean term still matches literally, but a cosmetic (full-width) variant slips through:
    # normalization is off unless the operator opts in. Mirrors the engine's off-by-default stance.
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    clean = await guard.validate(f"about {BLOCKED_WORD}")
    assert clean.failures[0].error_code == ContentFilter.CONTENT_BLOCKED

    full_width = "ｐｏｌｉｔｉｃｓ"
    cosmetic = await guard.validate(f"Learn {full_width} here.")
    assert not cosmetic.has_failures


async def test_case_variant_passes_by_default():
    # With the literal default, a mixed-case spelling is not folded, so it does not match.
    guard = _guard(blocked_topics=[BLOCKED_WORD])
    result = await guard.validate(f"Read about {BLOCKED_WORD.upper()} now.")
    assert not result.has_failures


# ---------------------------------------------------------------------------
# Self-contained — the validator does not depend on engine normalization
# ---------------------------------------------------------------------------


async def test_blocks_under_plain_guard_without_engine_normalize():
    # Fail-safe: the validator normalizes both its term and the view text itself, so a cosmetic
    # evasion is blocked even under a Guard with engine normalization off. The validator never
    # outsources its text-side normalization to the engine, which could be wired with a different
    # config (or none) — closing the §6.2 fail-open risk.
    full_width = "ｐｏｌｉｔｉｃｓ"
    zwsp = "​"
    smuggled = zwsp.join(BLOCKED_WORD)

    # Engine normalization explicitly off; only the validator normalizes.
    guard = Guard("plain", normalize=False).use(ContentFilter(blocked_topics=[BLOCKED_WORD], normalize=True))

    full_width_result = await guard.validate(f"Learn {full_width} here.")
    assert full_width_result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED

    zero_width_result = await guard.validate(f"About {smuggled} today.")
    assert zero_width_result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


# ---------------------------------------------------------------------------
# Non-Latin text is not corrupted by the safe normalization profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "text"),
    [
        ("привет", "скажи привет пожалуйста"),  # Cyrillic
        ("ελληνικά", "μιλάω ελληνικά καλά"),  # Greek
        ("مرحبا", "قل مرحبا من فضلك"),  # Arabic
        ("赌博", "这里有赌博内容"),  # CJK
    ],
)
async def test_non_latin_term_matches_without_corruption(term, text):
    # The validator's normalization profile (NFKC + casefold, no homoglyph fold) is script-safe:
    # a genuine non-Latin term still matches its own script and is not rewritten toward ASCII.
    guard = _guard(blocked_topics=[term], normalize=True, word_boundary=False)
    result = await guard.validate(text)
    assert result.failures[0].error_code == ContentFilter.CONTENT_BLOCKED


async def test_non_latin_not_folded_to_ascii():
    # The homoglyph step is engine-only and off here, so a Cyrillic term is NOT folded to its ASCII
    # look-alike: an ASCII spelling must not match the Cyrillic term (no over-folding of real scripts).
    cyrillic_pope = "роре"  # р о р е — Cyrillic look-alike of "pope"
    guard = _guard(blocked_topics=[cyrillic_pope], normalize=True, word_boundary=False)
    result = await guard.validate("the word pope in latin")  # ASCII look-alike
    assert not result.has_failures


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


async def test_identical_inputs_give_identical_verdicts():
    guard = _guard(blocked_topics=[BLOCKED_WORD, BLOCKED_PHRASE])
    text = f"talk of {BLOCKED_WORD} and a {BLOCKED_PHRASE}"
    first = await guard.validate(text)
    second = await guard.validate(text)
    assert first.failures[0].error_code == second.failures[0].error_code
    assert first.failures[0].details == second.failures[0].details


async def test_reordering_terms_does_not_change_reported_match():
    # The earliest-offset match wins regardless of the order the terms are configured in, so the
    # reported offset is stable even though the matched_index tracks the term's position.
    text = f"First {BLOCKED_WORD}, then {BLOCKED_PHRASE}."
    one = await _guard(blocked_topics=[BLOCKED_WORD, BLOCKED_PHRASE]).validate(text)
    two = await _guard(blocked_topics=[BLOCKED_PHRASE, BLOCKED_WORD]).validate(text)
    assert one.failures[0].details["match_offset"] == two.failures[0].details["match_offset"]
