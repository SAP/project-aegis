"""Curated overrides applied on top of the Unicode-derived ``GENERATED_MAP``.

The Unicode confusables data classifies characters into equivalence sets and
publishes a single "prototype" per set. That prototype is not always the ASCII
letter we want -- e.g. uppercase "I", lowercase "l", and digit "1" all share
one set, and Unicode picks lowercase "l" as the prototype, so codepoints that
should fold to "I" for our case-aware regex matchers come out as "l" in the
generated map.

This file is the small, hand-edited correction layer:

* ``OVERRIDES``  -- entries whose ASCII target we *redirect* (key already exists
  in ``GENERATED_MAP`` or ``EXTRAS``; we replace the value).
* ``EXTRAS``     -- entries Unicode does not (yet) fold to an ASCII letter but
  that we know are visually confusable with one. These typically come from the
  old hand-curated map: Unicode's prototype is itself non-ASCII (e.g. Greek
  epsilon's prototype is LATIN SMALL LETTER C WITH BAR, but visually it
  matches ASCII "e" closely enough that attackers will use it).
* ``BLOCKLIST``  -- codepoints from ``GENERATED_MAP`` we want to *drop*. Empty
  for now; intended for future false-positive triage.

Precedence in :mod:`aegis._homoglyphs` is ``GENERATED < EXTRAS < OVERRIDES``,
with ``BLOCKLIST`` removing entries last.

Audit each entry by hand. Keep the comment showing the Unicode name and the
literal glyph -- this file is read line-by-line during code review, that is
the whole point of carving it out from the generated dict.
"""

from __future__ import annotations

# Codepoints we want to fold to a *different* ASCII letter than the Unicode
# prototype dictates. Each entry should justify itself in the comment.
OVERRIDES: dict[int, str] = {
    # ----- Disambiguating the "l/I/1" equivalence class -----
    # The Unicode prototype for this entire class is lowercase "l". For our
    # regex matchers we want capital-letter targets to land on "I" so a
    # case-sensitive trigger word survives the fold.
    0x0399: "I",  # GREEK CAPITAL LETTER IOTA          Ι
    0x0406: "I",  # CYRILLIC CAPITAL LETTER BYELORUSSIAN-UKRAINIAN I  І
    0x04C0: "I",  # CYRILLIC LETTER PALOCHKA           Ӏ
    # Note: U+04CF (lowercase palochka) intentionally stays "l" -- that one is
    # paired with the lowercase form, not the capital.
    # ----- LATIN SMALL LETTER LONG S -----
    # Unicode's prototype is "f" (the long-s glyph really does look like an
    # f without the crossbar). For attacker-bypass detection we want the
    # historical meaning preserved -- an attacker writing "ſocial" is hiding
    # "social", not "focial" -- so we override to "s".
    0x017F: "s",  # LATIN SMALL LETTER LONG S          ſ
}

# Codepoints that are visually confusable with an ASCII letter but whose
# Unicode prototype is itself non-ASCII (so they would never appear in
# ``GENERATED_MAP``). Sourced from the previous hand-curated map; reviewing
# each on a Unicode bump is part of the refresh procedure.
EXTRAS: dict[int, str] = {
    # ----- Greek lowercase whose Unicode prototype is non-ASCII -----
    0x03B5: "e",  # GREEK SMALL LETTER EPSILON         ε   (Unicode -> ꞓ)
    0x03BA: "k",  # GREEK SMALL LETTER KAPPA           κ   (Unicode -> ĸ)
    0x03C7: "x",  # GREEK SMALL LETTER CHI             χ   (Unicode lists χ as the prototype itself)
    # ----- Cyrillic lowercase whose Unicode prototype is non-ASCII -----
    0x0432: "b",  # CYRILLIC SMALL LETTER VE           в   (Unicode -> ʙ small-cap B)
    0x043A: "k",  # CYRILLIC SMALL LETTER KA           к   (Unicode -> ĸ)
}

# Generated-map entries to *exclude* from the merged map. Add codepoints here
# when the Unicode-derived fold causes false positives in legitimate input.
# Empty for now; revisit once the eval surfaces a concrete case.
BLOCKLIST: frozenset[int] = frozenset()
