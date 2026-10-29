"""Guard-side text normalization for regex-based validators.

Pattern-matching validators operate on raw input text. An attacker can use
visually identical but bytewise-different characters to bypass them:

* **Cyrillic / Greek / Cherokee homoglyphs** -- e.g. U+0455 ``ѕ`` looks like
  ASCII ``s`` but is a different code point, so ``re.search(r"\\bsocials\\b",
  "ѕocials")`` does not match while a human reader and an LLM still parse it
  as "socials".
* **Unicode variation selectors** (U+FE00..U+FE0F, U+E0100..U+E01EF, plus the
  Mongolian FVS1..FVS3 at U+180B..U+180D) -- zero-width modifiers that can be
  inserted between or after letters with no visual effect, defeating literal
  regex matches.
* **NFKC compatibility characters** -- fullwidth ``ＡＢＣ``, mathematical
  ``𝐀𝐁𝐂``, ligatures (``ﬁ``), etc.

``normalize()`` applies a configurable set of these strategies, declared via a
:class:`NormalizationConfig`, in one fixed order. Both the engine
(``Guard(normalize=...)``) and the detection validators call it, so each strategy
has a single implementation and the two layers cannot drift apart.
``normalize_for_guard()`` is the historical engine entry point, now a thin wrapper
over ``normalize()`` with the engine profile.

The output is suitable for *matching only*. Callers must keep the original string
and forward that to the agent: the Guard validates the normalized text while the
agent receives the original.
"""

from __future__ import annotations

import re
import unicodedata

from aegis._normalize._config import NormalizationConfig
from aegis._normalize._homoglyphs import HOMOGLYPH_MAP

__all__ = ["NormalizationConfig", "normalize", "normalize_for_guard"]

# Unicode variation selectors. These render as zero width but break regex
# byte matches. Categories: VS1..VS16 are ``Mn`` (Mark, Nonspacing) so they
# are NOT covered by the existing ``strip_invisible()`` (which targets ``C*``
# control categories).
_VARIATION_SELECTORS: frozenset[int] = frozenset(
    set(range(0xFE00, 0xFE10))  # VS1..VS16
    | set(range(0xE0100, 0xE01F0))  # VS17..VS256 (supplementary)
    | {0x180B, 0x180C, 0x180D}  # Mongolian Free Variation Selectors
)

# Runs of any whitespace collapse to a single space. Applied after format-char
# stripping so a zero-width character removed from between two words does not
# survive as a spurious separator.
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize(text: str | None, config: NormalizationConfig) -> str:
    """Normalize ``text`` by applying the strategies enabled in ``config``.

    The enabled strategies always run in this fixed order, chosen so that each layer's historical
    pipeline is reproduced exactly when it enables its own strategies:

    1. Strip Unicode variation selectors (invisible modifiers).
    2. Unicode normalization (NFKC or canonical NFC) -- folds ``ﬁ`` -> ``fi``,
       ``ＡＢＣ`` -> ``ABC``, ``𝐀𝐁𝐂`` -> ``ABC``.
    3. Strip format characters (category ``Cf``: zero-width/invisible).
    4. Collapse whitespace runs to a single space and strip the ends.
    5. Map Cyrillic / Greek / Cherokee homoglyphs to ASCII look-alikes.
    6. Case-fold (``str.casefold``).

    Pure-ASCII text skips the two character-removal passes (steps 1 and 3): variation selectors and
    format characters are never ASCII, so those passes are guaranteed no-ops and a single
    ``str.isascii()`` check stands in for a per-character scan. This keeps the common case cheap.

    Because the same ``config`` yields the same transform, a term and the text it is matched
    against stay in lockstep as long as both go through this one function with the same config.

    The output is intended for matching only; the caller must retain the untouched original
    string to forward to the agent.

    Args:
        text: The input text, or ``None``.
        config: Which strategies to apply.

    Returns:
        The normalized text. ``None`` and empty strings round-trip unchanged (an empty string is
        returned as-is; ``None`` returns ``""``).
    """
    if not text:
        return text or ""

    # Step 1: drop variation selectors. Skip the per-character scan for pure-ASCII text: every
    # variation selector sits at U+180B or higher, so ASCII can never contain one and the strip is
    # a guaranteed no-op. ``str.isascii()`` is a single C-level scan, far cheaper than the Python
    # loop over every code point. The skip is keyed on an immutable property of the characters being
    # removed — not on which other strategies are enabled — so adding a new strategy can never make
    # it skip work that was actually needed.
    if config.strip_variation_selectors and not text.isascii():
        stripped = "".join(c for c in text if ord(c) not in _VARIATION_SELECTORS)
        if stripped != text:
            text = stripped

    # Step 2: Unicode normalization (idempotent; cheap when text is already normalized).
    if config.unicode_form is not None and not unicodedata.is_normalized(config.unicode_form, text):
        text = unicodedata.normalize(config.unicode_form, text)

    # Step 3: strip format characters (category ``Cf``). Same ASCII skip as step 1, for the same
    # reason: the lowest Cf code point is U+00AD, so pure-ASCII text has none and the strip is a
    # no-op. The guard is a property of category ``Cf`` itself, so it stays correct however the
    # strategy set grows.
    if config.strip_format_chars and not text.isascii():
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")

    # Step 4: collapse whitespace runs, after step 3 so a zero-width char removed from between two
    # words does not leave a stray separator.
    if config.collapse_whitespace:
        text = _WHITESPACE_RUN.sub(" ", text).strip()

    # Step 5: homoglyph -> ASCII map. ``str.translate`` is implemented in C and is O(n).
    if config.map_homoglyphs:
        text = text.translate(HOMOGLYPH_MAP)

    # Step 6: case-fold last, so it operates on the already-folded ASCII form.
    if config.casefold:
        text = text.casefold()

    return text


def normalize_for_guard(text: str | None) -> str:
    """Normalize ``text`` for guard-side pattern matching (engine profile).

    Thin wrapper over :func:`normalize` with :meth:`NormalizationConfig.for_engine`: strip
    variation selectors, NFKC-fold, then map Cyrillic / Greek / Cherokee homoglyphs to ASCII
    (``ѕ`` -> ``s``, ``а`` -> ``a``, ``ο`` -> ``o``, ...).

    The output is intended for matching only; the caller must retain the untouched original
    string to forward to the agent.

    Args:
        text: The input text, or ``None``.

    Returns:
        The normalized text. ``None`` and empty strings round-trip unchanged.
    """
    return normalize(text, NormalizationConfig.for_engine())
