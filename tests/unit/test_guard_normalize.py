"""Tests for the ``normalize`` flag on :class:`aegis.Guard`.

When ``Guard(name, normalize=True)`` is used, validators receive the
NFKC-folded / variation-selector-stripped / homoglyph-mapped form of the
input. The caller's original ``value`` and ``parts`` arguments must NOT
be mutated, and the normalized form must NOT escape via ``GuardResult`` —
the caller forwards the original to the agent on pass.
"""

from __future__ import annotations

import pytest

from _a2a_compat import data_part, get_text, text_part
from aegis import BaseValidator, FailedResult, Guard, NormalizationConfig, PassedResult
from aegis.a2a._utils import build_view

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class CaptureValue(BaseValidator):
    """Spy validator that records the ``value`` it received."""

    def __init__(self) -> None:
        self.seen_value: str | None = None

    async def validate(self, view, metadata=None):
        value = view.render()
        self.seen_value = value
        return PassedResult()


class SubstringBlocker(BaseValidator):
    """Minimal inline validator that fails when ``needle`` is in the text.

    Stands in for a real content-filter validator so this engine test can
    exercise ``Guard(normalize=True)`` without bundling any validator.
    """

    def __init__(self, needle: str) -> None:
        self.needle = needle

    async def validate(self, view, metadata=None):
        value = view.render()
        if value and self.needle in value:
            return FailedResult(error_message="blocked", error_code="BLOCKED")
        return PassedResult()


# ---------------------------------------------------------------------------
# Opt-in semantic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_config_matches_bool_true() -> None:
    """``normalize=NormalizationConfig.for_engine()`` behaves like the ``normalize=True`` alias."""
    guard = Guard("g", normalize=NormalizationConfig.for_engine()).use(SubstringBlocker("socials"))
    result = await guard.validate(value="check ѕocials please")
    assert result.has_failures, "explicit engine config should fold the homoglyph like normalize=True"


@pytest.mark.asyncio
async def test_normalize_custom_config_applies_only_declared_strategies() -> None:
    """A custom config applies exactly what it declares — casefold here, no homoglyph fold."""
    config = NormalizationConfig(unicode_form="NFKC", casefold=True)
    guard = Guard("g", normalize=config).use(SubstringBlocker("socials"))

    # Casefold is on, so an upper-case ASCII variant is folded and caught.
    folded = await guard.validate(value="check SOCIALS please")
    assert folded.has_failures

    # Homoglyph mapping is off, so the Cyrillic variant is NOT folded and slips through.
    homoglyph = await guard.validate(value="check ѕocials please")
    assert not homoglyph.has_failures


@pytest.mark.asyncio
async def test_normalize_true_blocks_homoglyph_input() -> None:
    """Guard(normalize=True) catches ``ѕocials`` (Cyrillic ѕ) via a substring validator."""
    guard = Guard("g", normalize=True).use(SubstringBlocker("socials"))
    result = await guard.validate(value="check ѕocials please")
    assert result.has_failures, "homoglyph variant should be blocked when normalize=True"


@pytest.mark.asyncio
async def test_normalize_false_lets_homoglyph_through() -> None:
    """Default Guard(normalize=False) does NOT catch the homoglyph variant."""
    guard = Guard("g").use(SubstringBlocker("socials"))
    result = await guard.validate(value="check ѕocials please")
    assert not result.has_failures, "raw Cyrillic ѕ should not match the ASCII regex when normalize=False"


@pytest.mark.asyncio
async def test_normalize_false_still_blocks_ascii() -> None:
    """The off-switch must not weaken matching of legitimate ASCII content."""
    guard = Guard("g").use(SubstringBlocker("socials"))
    result = await guard.validate(value="check socials please")
    assert result.has_failures


# ---------------------------------------------------------------------------
# Validators see the canonical form when the flag is on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validator_sees_canonical_value_when_normalize_true() -> None:
    spy = CaptureValue()
    guard = Guard("g", normalize=True).use(spy)
    await guard.validate(value="check ѕocials please")  # Cyrillic ѕ
    assert spy.seen_value == "check socials please"


@pytest.mark.asyncio
async def test_validator_sees_raw_value_when_normalize_false() -> None:
    spy = CaptureValue()
    guard = Guard("g").use(spy)
    await guard.validate(value="check ѕocials please")
    assert spy.seen_value == "check ѕocials please"


# ---------------------------------------------------------------------------
# Caller's originals are never mutated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_original_value_unchanged_after_validate() -> None:
    """The caller can forward the original to the agent on pass."""
    spy = CaptureValue()
    orig = "check ѕocials please"
    guard = Guard("g", normalize=True).use(spy)
    await guard.validate(value=orig)
    assert orig == "check ѕocials please"
    assert "ѕ" in orig  # Cyrillic ѕ still present in caller's variable


@pytest.mark.asyncio
async def test_original_parts_unchanged_after_validate() -> None:
    """The caller's Part objects are not mutated; parts are flattened + normalized.

    Multi-part input is flattened to the single ``value`` the validator sees;
    text parts contribute their (normalized) text and non-text parts are
    dropped. The caller's original Part objects must be left byte-identical.
    """
    tpart = text_part("check ѕocials please")
    fpart = data_part()  # non-text part (v1 has no FilePart oneof; only pass-through matters here)
    parts = [tpart, fpart]

    spy = CaptureValue()
    guard = Guard("g", normalize=True).use(spy)
    await guard.validate(build_view(parts))

    # Caller's originals are byte-identical (no in-place mutation).
    assert get_text(tpart) == "check ѕocials please"
    assert "ѕ" in get_text(tpart)
    # The validator saw the flattened, normalized text (non-text part dropped).
    assert spy.seen_value == "check socials please"


@pytest.mark.asyncio
async def test_normalize_true_with_no_value_or_parts() -> None:
    """An empty validate() call still runs cleanly when normalize=True."""
    spy = CaptureValue()
    guard = Guard("g", normalize=True).use(spy)
    await guard.validate("")
    assert spy.seen_value == ""
