"""Composable configuration for text normalization, shared by the engine and the validators.

A :class:`NormalizationConfig` declares *which* independent normalization strategies to apply.
The strategies themselves live in :func:`aegis._normalize.normalize`, which runs them in a single
fixed order. Both layers — the engine (``Guard(normalize=...)``) and the detection validators —
build a config and hand it to that one function, so there is exactly one implementation of each
strategy and no copy-pasted pipelines to drift out of sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NormalizationConfig:
    """The set of normalization strategies to apply, each field an independent on/off toggle.

    The strategies always run in the same fixed order (see :func:`aegis._normalize.normalize`), so
    two callers that enable the same strategies produce identical output — the invariant that makes
    matching a normalized term against normalized text correct.

    Two recommended presets cover the common cases — :meth:`for_engine` for the guard engine and
    :meth:`for_validators` for the detection validators. They differ in one decisive step: only
    ``for_engine`` maps homoglyphs. Construct ``NormalizationConfig`` directly for any other
    combination.

    Attributes:
        unicode_form: Unicode normalization form, or ``None`` to skip it. ``"NFKC"`` folds
            compatibility variants (full-width ``Ａ`` -> ``A``, ligatures, math letters) and catches
            full-width evasions; ``"NFC"`` is canonical-only, avoiding NFKC over-folding
            (``㎏`` -> ``kg``) at the cost of missing those evasions.
        strip_variation_selectors: Drop Unicode variation selectors (VS1–VS256 and the Mongolian
            FVS, category ``Mn``) — zero-width modifiers inserted to defeat literal matches.
        strip_format_chars: Drop format characters (category ``Cf``): zero-width/invisible
            characters (ZWSP/ZWNJ/ZWJ, BOM, soft hyphen, bidi controls).
        collapse_whitespace: Collapse every run of whitespace to a single space and strip the ends.
        map_homoglyphs: Map confusable look-alikes (Cyrillic/Greek/Cherokee) to their ASCII
            prototype. The aggressive step: it rewrites genuine non-Latin letters toward ASCII and
            can corrupt legitimately non-Latin input, so it is off in every default profile and
            opt-in only.
        casefold: Apply :meth:`str.casefold` (not ``lower``) for locale-independent, multilingual
            case-insensitive matching (``ß`` matches ``SS``).
    """

    unicode_form: Literal["NFC", "NFKC"] | None = "NFKC"
    strip_variation_selectors: bool = False
    strip_format_chars: bool = False
    collapse_whitespace: bool = False
    map_homoglyphs: bool = False
    casefold: bool = False

    @classmethod
    def for_engine(cls) -> NormalizationConfig:
        """Return the recommended profile for the engine (``Guard(normalize=...)``).

        Enables the security-focused steps: strip variation selectors, NFKC-fold, then map
        confusable homoglyphs to ASCII. This is the historical ``normalize_for_guard`` pipeline,
        meant for matching over untrusted input where an attacker may use look-alike or invisible
        characters to slip past a check.

        The homoglyph step is the decisive difference from :meth:`for_validators`: it rewrites
        genuine Cyrillic/Greek/Cherokee letters toward ASCII, which can corrupt legitimately
        non-Latin input — which is why ``Guard`` keeps normalization off by default.

        Returns:
            A config with ``strip_variation_selectors``, ``unicode_form="NFKC"``, and
            ``map_homoglyphs`` on; everything else off.
        """
        return cls(
            unicode_form="NFKC",
            strip_variation_selectors=True,
            strip_format_chars=False,
            collapse_whitespace=False,
            map_homoglyphs=True,
            casefold=False,
        )

    @classmethod
    def for_validators(
        cls,
        *,
        compatibility_folding: bool = True,
        case_sensitive: bool = False,
    ) -> NormalizationConfig:
        """Return the recommended profile for the detection validators (e.g. ``ContentFilter``).

        Enables the script-safe matching steps: Unicode-fold (NFKC or canonical NFC), strip format
        characters, collapse whitespace, and case-fold. A validator applies this to both its
        configured terms and the input text, so cosmetic evasions (case, full-width, zero-width,
        extra spacing) fold away before matching.

        Unlike :meth:`for_engine`, it does **not** map homoglyphs, so it never rewrites genuine
        non-Latin letters to ASCII — it is safe to run on any script.

        Args:
            compatibility_folding: ``True`` uses NFKC (folds full-width and other compatibility
                variants); ``False`` uses canonical NFC only.
            case_sensitive: ``True`` skips case-folding, so matching stays case-sensitive.

        Returns:
            A config with ``strip_format_chars``, ``collapse_whitespace``, the chosen
            ``unicode_form``, and (unless ``case_sensitive``) ``casefold`` on; variation-selector
            stripping and homoglyph mapping off.
        """
        return cls(
            unicode_form="NFKC" if compatibility_folding else "NFC",
            strip_variation_selectors=False,
            strip_format_chars=True,
            collapse_whitespace=True,
            map_homoglyphs=False,
            casefold=not case_sensitive,
        )
