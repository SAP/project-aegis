from __future__ import annotations

import pytest

from aegis.a2a._utils import _maybe_await


async def _async_return(value):
    return value


def _sync_return(value):
    return value


@pytest.mark.asyncio
async def test_maybe_await_with_coroutine():
    result = await _maybe_await(_async_return(42))
    assert result == 42


@pytest.mark.asyncio
async def test_maybe_await_with_sync():
    result = await _maybe_await(_sync_return("hello"))
    assert result == "hello"


@pytest.mark.asyncio
async def test_maybe_await_with_none():
    result = await _maybe_await(None)
    assert result is None
