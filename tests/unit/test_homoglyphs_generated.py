"""Regression tests for the Unicode-derived ``HOMOGLYPH_MAP``.

The map is now built by merging :mod:`aegis._normalize._generated` (mechanically
derived from the vendored Unicode UTS #39 confusables data under
``dev/data/unicode/``) with the curated layer in
:mod:`aegis._normalize._overrides`.

These tests pin down four invariants:

* **Coverage regression**: every entry from the previous hand-curated map
  still folds its source codepoint to the same ASCII letter. We never lose
  ground when bumping the generator.
* **U+04C0 fix**: the uppercase Cyrillic palochka -- the gap surfaced by the
  benchmark -- folds to ``"I"`` (override decision).
* **Sanity**: every value is a single ASCII ``[A-Za-z]`` and no key is itself
  ASCII.
* **Override layering**: ``OVERRIDES`` wins over ``GENERATED_MAP``;
  ``BLOCKLIST`` removes entries last.
"""

from __future__ import annotations

import string

import pytest

from aegis._normalize import normalize_for_guard
from aegis._normalize._generated import GENERATED_MAP
from aegis._normalize._homoglyphs import HOMOGLYPH_MAP
from aegis._normalize._overrides import BLOCKLIST, EXTRAS, OVERRIDES

# ---------------------------------------------------------------------------
# Coverage regression: the previous hand-curated map.
#
# Frozen here so we can detect a regression without keeping the old file
# around. If you intentionally drop one of these (e.g. evidence it causes
# false positives), update this list AND record the reason in the commit
# message.
# ---------------------------------------------------------------------------

_LEGACY_HAND_CURATED: dict[int, str] = {
    # Cyrillic lowercase
    0x0430: "a",
    0x0432: "b",
    0x0441: "c",
    0x0501: "d",
    0x0435: "e",
    0x0445: "x",
    0x0456: "i",
    0x0458: "j",
    0x043A: "k",
    0x04CF: "l",
    0x043E: "o",
    0x0440: "p",
    0x051B: "q",
    0x0455: "s",
    0x051D: "w",
    0x0443: "y",
    # Cyrillic uppercase
    0x0410: "A",
    0x0412: "B",
    0x0421: "C",
    0x0415: "E",
    0x041D: "H",
    0x0406: "I",
    0x0408: "J",
    0x041A: "K",
    0x041C: "M",
    0x041E: "O",
    0x0420: "P",
    0x0405: "S",
    0x0422: "T",
    0x0423: "Y",
    0x0425: "X",
    # Greek lowercase
    0x03B1: "a",
    0x03B5: "e",
    0x03B9: "i",
    0x03BA: "k",
    0x03BF: "o",
    0x03C1: "p",
    0x03BD: "v",
    0x03C7: "x",
    0x03C5: "u",
    # Greek uppercase
    0x0391: "A",
    0x0392: "B",
    0x0395: "E",
    0x0396: "Z",
    0x0397: "H",
    0x0399: "I",
    0x039A: "K",
    0x039C: "M",
    0x039D: "N",
    0x039F: "O",
    0x03A1: "P",
    0x03A4: "T",
    0x03A5: "Y",
    0x03A7: "X",
    # Latin extended / IPA
    0x0251: "a",
    0x0261: "g",
    0x0131: "i",
    0x017F: "s",
    0x01C0: "l",
    # Cherokee entries from the legacy map are deliberately omitted from this
    # regression set: their old "visual" annotations were inconsistent with
    # Unicode's confusable classification (e.g. U+13A0 was annotated A but
    # Unicode folds it to D). Trust the generator on those, see the diff
    # printed at generation time.
}


@pytest.mark.parametrize("codepoint, ascii_target", sorted(_LEGACY_HAND_CURATED.items()))
def test_legacy_entries_still_fold(codepoint: int, ascii_target: str) -> None:
    """Every codepoint from the previous hand-curated map still folds to its ASCII letter."""
    assert HOMOGLYPH_MAP.get(codepoint) == ascii_target, (
        f"U+{codepoint:04X} no longer folds to {ascii_target!r} -- coverage regression."
    )
    # And the public helper agrees end-to-end.
    assert normalize_for_guard(chr(codepoint)) == ascii_target


# ---------------------------------------------------------------------------
# The headline benchmark gap.
# ---------------------------------------------------------------------------


def test_uppercase_palochka_folds_to_capital_i() -> None:
    """U+04C0 (uppercase palochka) -- previously missing -- now folds to "I".

    The benchmark's ``cap_upper_homoglyph`` family used this codepoint to
    bypass case-aware regex validators. The override pins the target to "I"
    rather than Unicode's chosen "l" so case-sensitive triggers (e.g.
    "PII") survive the fold intact.
    """
    assert HOMOGLYPH_MAP[0x04C0] == "I"
    assert normalize_for_guard("Ӏ") == "I"
    assert normalize_for_guard("palochkaӀtest") == "palochkaItest"


# ---------------------------------------------------------------------------
# Sanity invariants over the merged map.
# ---------------------------------------------------------------------------


_ASCII_LETTERS = set(string.ascii_letters)


def test_all_values_are_single_ascii_letters() -> None:
    """Every value is a single character drawn from ``[A-Za-z]``."""
    bad = {cp: v for cp, v in HOMOGLYPH_MAP.items() if v not in _ASCII_LETTERS}
    assert not bad, f"Non-ASCII-letter values present: {bad}"


def test_no_ascii_keys() -> None:
    """ASCII codepoints must not be keys -- folding ASCII to ASCII is meaningless."""
    ascii_keys = [cp for cp in HOMOGLYPH_MAP if cp < 0x80]
    assert not ascii_keys, f"ASCII codepoints found as keys (would silently rewrite ASCII input): {ascii_keys}"


def test_map_is_non_trivial() -> None:
    """A wildly empty map almost certainly means the generator failed; guard against that."""
    assert len(HOMOGLYPH_MAP) > 100, (
        f"HOMOGLYPH_MAP has only {len(HOMOGLYPH_MAP)} entries -- is _normalize/_generated.py up to date?"
    )


# ---------------------------------------------------------------------------
# Override-layer semantics.
# ---------------------------------------------------------------------------


def test_overrides_layer_wins_over_generated() -> None:
    """For every key in OVERRIDES, the merged map carries the override value."""
    for cp, expected in OVERRIDES.items():
        assert HOMOGLYPH_MAP[cp] == expected, (
            f"OVERRIDES[U+{cp:04X}] = {expected!r} not respected (got {HOMOGLYPH_MAP.get(cp)!r})."
        )


def test_extras_appear_in_merged_map_when_not_in_generated() -> None:
    """Entries unique to EXTRAS (not also in GENERATED) must surface in the merge.

    EXTRAS is the home for confusables Unicode does not classify with an
    ASCII prototype; if one starts appearing in GENERATED on a Unicode bump
    that is fine, but it must never *disappear* from the merge.
    """
    for cp, expected in EXTRAS.items():
        assert cp in HOMOGLYPH_MAP
        # Generated always loses to OVERRIDES; if both EXTRAS and GENERATED
        # define the same key, GENERATED's update happened first, EXTRAS's
        # update second, so the EXTRAS value should win unless OVERRIDES
        # also touches it.
        if cp not in OVERRIDES:
            assert HOMOGLYPH_MAP[cp] == expected


def test_blocklist_removes_entries() -> None:
    """Codepoints listed in ``BLOCKLIST`` must not appear in the merged map.

    BLOCKLIST is empty today; the test still pins the contract so adding an
    entry tomorrow is a one-line change.
    """
    leaked = [cp for cp in BLOCKLIST if cp in HOMOGLYPH_MAP]
    assert not leaked, f"BLOCKLIST entries leaked into HOMOGLYPH_MAP: {leaked}"


# ---------------------------------------------------------------------------
# Generator output sanity.
# ---------------------------------------------------------------------------


def test_generated_map_is_disjoint_with_ascii() -> None:
    """The generator's filter must reject ASCII source codepoints."""
    ascii_keys = [cp for cp in GENERATED_MAP if cp < 0x80]
    assert not ascii_keys, f"GENERATED_MAP contains ASCII keys: {ascii_keys}"


def test_generated_map_values_are_ascii_letters() -> None:
    """The generator's filter must restrict targets to ``[A-Za-z]``."""
    bad = {cp: v for cp, v in GENERATED_MAP.items() if v not in _ASCII_LETTERS}
    assert not bad, f"GENERATED_MAP has non-letter targets: {bad}"
