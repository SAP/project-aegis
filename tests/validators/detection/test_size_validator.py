"""Unit tests for ``SizeValidator`` — driven through a real ``Guard``."""

from __future__ import annotations

import pytest

from aegis import Guard
from aegis.validators.detection import SizeValidator


def _guard(**kwargs) -> Guard:
    return Guard("test").use(SizeValidator(**kwargs))


async def test_within_limit_passes():
    result = await _guard(max_size_bytes=10).validate("hello")
    assert not result.has_failures
    assert result.results[0].details["size_bytes"] == 5


async def test_over_limit_fails():
    result = await _guard(max_size_bytes=4).validate("hello")
    assert result.failures[0].error_code == SizeValidator.SIZE_EXCEEDED
    assert result.failures[0].details == {"size_bytes": 5, "max_size_bytes": 4}


async def test_limit_is_inclusive():
    assert not (await _guard(max_size_bytes=5).validate("12345")).has_failures
    assert (await _guard(max_size_bytes=5).validate("123456")).has_failures


async def test_empty_passes():
    result = await _guard(max_size_bytes=0).validate("")
    assert not result.has_failures


async def test_size_counts_utf8_bytes_not_code_points():
    # "😀" is one code point but four UTF-8 bytes: a byte limit of 2 rejects it, where a
    # character limit of 2 would not. This is the SizeValidator / LengthValidator distinction.
    result = await _guard(max_size_bytes=2).validate("😀")
    assert result.failures[0].error_code == SizeValidator.SIZE_EXCEEDED
    assert result.failures[0].details["size_bytes"] == 4


async def test_lone_surrogate_is_measured_not_crashed():
    # A str with an unpaired surrogate (e.g. from errors="surrogateescape" decoding upstream, or a
    # surrogate pair split mid-buffer) would make a plain encode("utf-8") raise UnicodeEncodeError.
    # The size check must stay total: measure it via surrogatepass (3 bytes), never throw.
    bad = "hi" + chr(0xD83D)  # "hi" (2 bytes) + lone high surrogate (3 bytes via surrogatepass)
    assert not (await _guard(max_size_bytes=10).validate(bad)).has_failures
    over = await _guard(max_size_bytes=4).validate(bad)
    assert over.failures[0].error_code == SizeValidator.SIZE_EXCEEDED
    assert over.failures[0].details["size_bytes"] == 5


def test_negative_limit_rejected_at_construction():
    with pytest.raises(ValueError):
        SizeValidator(max_size_bytes=-1)


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), 1024.0, True, False])
def test_non_int_limit_rejected_at_construction(limit):
    # nan/inf slip past the "< 0" check and would silently accept all content; a plain float is
    # still not an int; a bool compares as int but leaks into the error message.
    with pytest.raises(TypeError):
        SizeValidator(max_size_bytes=limit)
