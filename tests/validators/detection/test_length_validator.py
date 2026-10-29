"""Unit tests for ``LengthValidator`` — driven through a real ``Guard``."""

from __future__ import annotations

import pytest

from aegis import Guard
from aegis.validators.detection import LengthValidator


def _guard(**kwargs) -> Guard:
    return Guard("test").use(LengthValidator(**kwargs))


async def test_within_bounds_passes():
    result = await _guard(max_length=10, min_length=2).validate("hello")
    assert not result.has_failures
    assert result.results[0].details["length"] == 5


async def test_too_long_fails():
    result = await _guard(max_length=4).validate("hello")
    assert result.failures[0].error_code == LengthValidator.LENGTH_EXCEEDED
    assert result.failures[0].details == {"length": 5, "max_length": 4}


async def test_too_short_fails():
    result = await _guard(max_length=10, min_length=5).validate("hi")
    assert result.failures[0].error_code == LengthValidator.LENGTH_TOO_SHORT
    assert result.failures[0].details == {"length": 2, "min_length": 5}


async def test_empty_passes_by_default():
    # Default min_length=0, so empty input (length 0) is within bounds.
    result = await _guard(max_length=10).validate("")
    assert not result.has_failures


async def test_boundaries_are_inclusive():
    # Exactly at each bound passes; one past the ceiling fails.
    assert not (await _guard(max_length=5, min_length=5).validate("12345")).has_failures
    assert (await _guard(max_length=5).validate("123456")).has_failures


async def test_length_counts_code_points_not_bytes():
    # A 2-code-point string of multi-byte characters is length 2, not its byte size.
    result = await _guard(max_length=2).validate("é😀")
    assert not result.has_failures


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_length": -1},
        {"max_length": 5, "min_length": -1},
        {"max_length": 2, "min_length": 5},  # empty range
    ],
)
def test_invalid_bounds_rejected_at_construction(kwargs):
    with pytest.raises(ValueError):
        LengthValidator(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        # nan/inf slip past every "< 0" and "<" check, so they must be rejected by type — else
        # the validator silently accepts all content.
        {"max_length": float("nan")},
        {"max_length": float("inf")},
        {"max_length": 10, "min_length": float("nan")},
        {"max_length": 10.0},  # a plain float is still not an int
        {"max_length": True},  # bool compares as int but leaks into error messages
        {"max_length": 10, "min_length": False},
    ],
)
def test_non_int_bounds_rejected_at_construction(kwargs):
    with pytest.raises(TypeError):
        LengthValidator(**kwargs)


async def test_no_ceiling_by_default_passes_any_length():
    # The default (max_length=None) means no upper bound: even very long input passes.
    guard = Guard("input").use(LengthValidator())
    result = await guard.validate("x" * 10_000)
    assert not result.has_failures


async def test_explicit_none_max_length_still_enforces_floor():
    # No ceiling, but min_length still applies.
    guard = Guard("input").use(LengthValidator(max_length=None, min_length=3))
    assert (await guard.validate("ab")).failures[0].error_code == LengthValidator.LENGTH_TOO_SHORT
    assert not (await guard.validate("abc")).has_failures
