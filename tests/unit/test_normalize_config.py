"""Tests for the composable normalization utility — ``NormalizationConfig`` and ``normalize``.

These pin the consolidation contract (the engine and the validators now share one implementation):

* ``NormalizationConfig.for_engine`` reproduces the historical ``normalize_for_guard`` pipeline.
* ``NormalizationConfig.for_validators`` reproduces the historical validator ``_normalize_text``.
* Each strategy toggle acts independently and in the one fixed order.
* The homoglyph step is the only one that corrupts non-Latin scripts; the ``for_validators`` profile
  omits it, so Cyrillic/Greek/CJK/Arabic text round-trips (case-folding aside).
"""

from __future__ import annotations

import re
import unicodedata

import pytest

from aegis._normalize import _VARIATION_SELECTORS, NormalizationConfig, normalize, normalize_for_guard
from aegis._normalize._homoglyphs import HOMOGLYPH_MAP


# Historical engine pipeline, inlined WITHOUT the ``isascii()`` fast path — the independent
# reference the optimized ``normalize`` must still match on every input, ASCII or not. This is
# what guards the fast path: if the skip ever changed output, this parity check fails.
def _old_engine_normalize(text: str) -> str:
    if not text:
        return text or ""
    stripped = "".join(c for c in text if ord(c) not in _VARIATION_SELECTORS)
    if stripped != text:
        text = stripped
    if not unicodedata.is_normalized("NFKC", text):
        text = unicodedata.normalize("NFKC", text)
    return text.translate(HOMOGLYPH_MAP)


# Historical validator pipeline, inlined WITHOUT the ``isascii()`` fast path on the Cf strip —
# the same role as the engine oracle above, for the matching profile.
def _old_validator_normalize(text: str, *, compatibility_folding: bool = True, case_sensitive: bool = False) -> str:
    text = unicodedata.normalize("NFKC" if compatibility_folding else "NFC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = re.sub(r"\s+", " ", text).strip()
    if not case_sensitive:
        text = text.casefold()
    return text


_SAMPLES = [
    "ѕocials",  # Cyrillic dze homoglyph
    "ＡＢＣ",  # full-width
    "ﬁle",  # ligature
    "𝐀𝐁𝐂",  # math bold
    "credit​card",  # zero-width space
    "credit\tcard  here",  # whitespace runs
    "POLİTİCS",  # dotted capital I
    "Fuß",  # sharp s
    "x️y",  # variation selector
    "普通话",  # CJK
    "مرحبا",  # Arabic
    "Ελληνικά",  # Greek
    "plain ascii only 123",  # pure ASCII — exercises the isascii() fast path
    "",
]


# ---------------------------------------------------------------------------
# Parity with the historical pipelines (also locks the isascii() fast path)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", _SAMPLES)
def test_for_engine_matches_unskipped_reference(text: str) -> None:
    # Compares against a no-fast-path reference, so a broken ASCII skip would surface here. The
    # public wrapper is checked to agree too.
    assert normalize(text, NormalizationConfig.for_engine()) == _old_engine_normalize(text)
    assert normalize_for_guard(text) == _old_engine_normalize(text)


@pytest.mark.parametrize("text", _SAMPLES)
def test_for_validators_matches_old_validator_pipeline(text: str) -> None:
    assert normalize(text, NormalizationConfig.for_validators()) == _old_validator_normalize(text)


@pytest.mark.parametrize("text", _SAMPLES)
def test_for_validators_canonical_folding(text: str) -> None:
    config = NormalizationConfig.for_validators(compatibility_folding=False)
    assert normalize(text, config) == _old_validator_normalize(text, compatibility_folding=False)


@pytest.mark.parametrize("text", _SAMPLES)
def test_for_validators_case_sensitive(text: str) -> None:
    config = NormalizationConfig.for_validators(case_sensitive=True)
    assert normalize(text, config) == _old_validator_normalize(text, case_sensitive=True)


# ---------------------------------------------------------------------------
# Individual toggles
# ---------------------------------------------------------------------------


def test_empty_and_none_round_trip() -> None:
    config = NormalizationConfig.for_engine()
    assert normalize("", config) == ""
    assert normalize(None, config) == ""


def test_no_strategies_is_identity() -> None:
    config = NormalizationConfig(unicode_form=None)
    text = "ＡＢＣ ѕocials  x​"
    assert normalize(text, config) == text


def test_only_strip_format_chars() -> None:
    config = NormalizationConfig(unicode_form=None, strip_format_chars=True)
    assert normalize("a​b­c", config) == "abc"


def test_only_strip_variation_selectors() -> None:
    config = NormalizationConfig(unicode_form=None, strip_variation_selectors=True)
    assert normalize("x️y", config) == "xy"


def test_only_collapse_whitespace() -> None:
    config = NormalizationConfig(unicode_form=None, collapse_whitespace=True)
    assert normalize("  a\t\n b  ", config) == "a b"


def test_only_casefold() -> None:
    config = NormalizationConfig(unicode_form=None, casefold=True)
    assert normalize("Fuß", config) == "fuss"


def test_only_homoglyphs() -> None:
    config = NormalizationConfig(unicode_form=None, map_homoglyphs=True)
    assert normalize("ѕocials", config) == "socials"


def test_unicode_form_nfkc_vs_nfc() -> None:
    assert normalize("ﬁ", NormalizationConfig(unicode_form="NFKC")) == "fi"
    # NFC leaves the ligature intact (it is not a canonical decomposition).
    assert normalize("ﬁ", NormalizationConfig(unicode_form="NFC")) == "ﬁ"


# ---------------------------------------------------------------------------
# Non-Latin safety — for_validators must not corrupt real scripts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["привет мир", "ελληνικά", "مرحبا بالعالم", "普通话测试", "日本語のテスト"],
)
def test_for_validators_preserves_non_latin(text: str) -> None:
    # The script-safe profile only case-folds (a no-op for scripts without case) — it never maps a
    # genuine non-Latin letter to ASCII, so the text survives apart from casing.
    assert normalize(text, NormalizationConfig.for_validators()) == text.casefold()


def test_for_engine_folds_cyrillic_but_for_validators_does_not() -> None:
    # The homoglyph step is what corrupts non-Latin input, and it lives only in the engine profile.
    cyrillic = "ѕocials"
    assert normalize(cyrillic, NormalizationConfig.for_engine()) == "socials"
    assert normalize(cyrillic, NormalizationConfig.for_validators()) == cyrillic.casefold()
