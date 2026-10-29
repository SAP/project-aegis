"""Public homoglyph -> ASCII mapping for guard-side text normalization.

When ``Guard(normalize=True)`` is set, input text is folded through this map
before validators run, so attacker-supplied confusables (Cyrillic ``ѕ``, Greek
``ο``, ...) collapse to their ASCII look-alikes and the validators see the
canonical form.

The dict is built from two sources:

* :mod:`aegis._homoglyphs_generated` -- mechanically derived from the vendored
  Unicode UTS #39 confusables data under ``dev/data/unicode/``. Refresh via
  ``scripts/generate_homoglyph_map.py``. Filtered to the subset of entries
  whose source is a single non-ASCII codepoint and whose target is a single
  ASCII letter that NFKC does not already fold to. ~600 entries.
* :mod:`aegis._homoglyphs_overrides` -- a small curated layer with three roles:
  ``OVERRIDES`` redirects entries to a different ASCII target than Unicode's
  prototype dictates; ``EXTRAS`` adds visually confusable codepoints whose
  Unicode prototype is itself non-ASCII; ``BLOCKLIST`` drops entries that
  cause false positives.

Merge precedence: ``GENERATED -> EXTRAS -> OVERRIDES`` (dict-update order, so
later wins on conflict), then ``BLOCKLIST`` strips. The result is exposed as
``HOMOGLYPH_MAP`` and is suitable for ``str.translate()`` -- keys are ``int``
code points, values are single-character ASCII strings.

To audit the merge result, prefer reading ``_homoglyphs_generated.py`` and
``_homoglyphs_overrides.py`` side by side; this module is just a thin combiner.
"""

from __future__ import annotations

from aegis._normalize._generated import GENERATED_MAP
from aegis._normalize._overrides import BLOCKLIST, EXTRAS, OVERRIDES

# Build by chained dict update so override precedence is explicit:
# GENERATED is the base, EXTRAS adds Unicode-not-yet-classified entries,
# OVERRIDES wins on key conflict, BLOCKLIST removes last.
_merged: dict[int, str] = {**GENERATED_MAP, **EXTRAS, **OVERRIDES}
HOMOGLYPH_MAP: dict[int, str] = {cp: ch for cp, ch in _merged.items() if cp not in BLOCKLIST}
del _merged
