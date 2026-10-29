"""Tests for ``aegis._normalize.normalize_for_guard``.

The helper defeats homoglyph / variation-selector bypasses of regex-based
validators. These tests pin down the contract:

* ASCII inputs round-trip unchanged.
* Variation selectors (VS1..VS256, Mongolian FVS) are stripped.
* NFKC compatibility folding is applied (ligatures, fullwidth, math).
* Cyrillic / Greek / Cherokee homoglyphs fold to ASCII look-alikes.
* The function is idempotent.
* Empty / ``None`` inputs return ``""``.
"""

from __future__ import annotations

import time

import pytest

from aegis._normalize import _VARIATION_SELECTORS, normalize_for_guard

# ---------------------------------------------------------------------------
# Core contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "Hello, world! 123",
        "underscores_and-dashes",
        "newlines\nand\ttabs",
    ],
)
def test_ascii_passthrough(text: str) -> None:
    """Plain ASCII text is returned unchanged."""
    assert normalize_for_guard(text) == text


def test_none_returns_empty_string() -> None:
    """``None`` is coerced to an empty string (callers can always ``.lower()``)."""
    assert normalize_for_guard(None) == ""


def test_idempotent() -> None:
    """Applying the helper twice yields the same result as once."""
    text = "ѕосіаls ＡＢＣ ﬁle 𝐀𝐁𝐂"
    once = normalize_for_guard(text)
    twice = normalize_for_guard(once)
    assert once == twice


# ---------------------------------------------------------------------------
# Variation selectors
# ---------------------------------------------------------------------------


def test_strips_vs16() -> None:
    """U+FE0F (VS16, the emoji-presentation selector) is removed."""
    # "te" + VS16 + "st"
    text = "te️st"
    assert normalize_for_guard(text) == "test"


def test_strips_vs1() -> None:
    """U+FE00 (VS1) is removed."""
    text = "ab︀cd"
    assert normalize_for_guard(text) == "abcd"


def test_strips_supplementary_variation_selector() -> None:
    """Supplementary plane VS17..VS256 (U+E0100..U+E01EF) are removed."""
    text = "x\U000e0100y"
    assert normalize_for_guard(text) == "xy"


def test_strips_mongolian_fvs() -> None:
    """Mongolian FVS1..FVS3 (U+180B..U+180D) are removed."""
    text = "a᠋b᠌c᠍d"
    assert normalize_for_guard(text) == "abcd"


def test_variation_selector_set_size() -> None:
    """Sanity-check the constant (16 + 240 + 3 = 259 selectors)."""
    assert len(_VARIATION_SELECTORS) == 16 + 240 + 3


# ---------------------------------------------------------------------------
# NFKC folding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ﬁle", "file"),  # LATIN SMALL LIGATURE FI
        ("ﬂag", "flag"),  # LATIN SMALL LIGATURE FL
        ("ＡＢＣ", "ABC"),  # FULLWIDTH LATIN
        ("ｈｅｌｌｏ", "hello"),  # FULLWIDTH lowercase
        ("\U0001d400\U0001d401\U0001d402", "ABC"),  # MATH BOLD A B C
        ("ⅠⅡⅢ", "IIIIII"),  # ROMAN NUMERALS I, II, III -> "I" "II" "III"
    ],
)
def test_nfkc_folds_compatibility_chars(raw: str, expected: str) -> None:
    assert normalize_for_guard(raw) == expected


# ---------------------------------------------------------------------------
# Homoglyph map
# ---------------------------------------------------------------------------


def test_cyrillic_dze_to_s() -> None:
    """The canonical homoglyph example: U+0455 ѕ -> ASCII s."""
    assert normalize_for_guard("ѕocials") == "socials"
    assert normalize_for_guard("check ѕocials please") == "check socials please"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("аррӏе", "apple"),  # Cyrillic а р р ӏ е -> apple
        ("оpen", "open"),  # Cyrillic о
        ("раypal", "paypal"),  # Cyrillic р а
        ("xerοx", "xerox"),  # Greek omicron
        ("Сyrillic", "Cyrillic"),  # Cyrillic capital ES -> C
    ],
)
def test_homoglyph_folding(raw: str, expected: str) -> None:
    assert normalize_for_guard(raw) == expected


def test_greek_omicron_folds() -> None:
    assert normalize_for_guard("οpenai") == "openai"


# ---------------------------------------------------------------------------
# Combination
# ---------------------------------------------------------------------------


def test_all_three_stages_compose() -> None:
    """Variation selector + NFKC + homoglyph in the same string all collapse."""
    # Cyrillic ѕ + VS16 + fullwidth О + math bold C
    text = "ѕ️Ｏ\U0001d402"
    assert normalize_for_guard(text) == "sOC"


# ---------------------------------------------------------------------------
# Performance smoke
# ---------------------------------------------------------------------------


def test_perf_under_100ms_for_100kb() -> None:
    """Sanity-check: 120 KB of pure-ASCII text normalizes well under 100 ms.

    Pure-ASCII input takes the fast path in ``normalize_for_guard``: the variation-selector and
    format-character passes are skipped via ``str.isascii()`` (both categories are non-ASCII only),
    NFKC is a no-op on ASCII, and the homoglyph ``str.translate`` has no ASCII keys. This guards
    against a later change reintroducing a per-character Python loop on the common ASCII path.
    """
    text = "hello world " * 10_000  # ~120 KB, pure ASCII
    start = time.perf_counter()
    result = normalize_for_guard(text)
    elapsed = time.perf_counter() - start
    assert result == text  # unchanged: every ASCII pass is a no-op
    assert elapsed < 0.1, f"normalize_for_guard too slow: {elapsed:.3f}s"
