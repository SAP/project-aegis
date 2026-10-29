"""Tests for InputGuardConfig.history_provider wiring.

Verifies:
- Provider not configured → validators see metadata=None.
- Provider set, returns list → GuardMetadata.conversation_history populated.
- Provider raises → warning logged, validation still runs, no history.
- Provider returns [] → metadata is None.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from a2a.server.agent_execution import AgentExecutor, RequestContext

from _a2a_compat import event_queue, request_context, text_part, user_message
from aegis import Guard, PassedResult
from aegis.a2a.executor import GuardedAgentExecutor
from aegis.a2a.types import InputGuardConfig
from aegis.validators.base import BaseValidator


def _make_context() -> RequestContext:
    return request_context(
        user_message([text_part("hi")], message_id="msg-1"),
        task_id="task-1",
        context_id="ctx-1",
    )


class _RecordingValidator(BaseValidator):
    """Captures the metadata it was invoked with."""

    def __init__(self):
        self.seen_metadata: Any = "SENTINEL"

    async def validate(self, view, metadata=None):
        view.render()
        self.seen_metadata = metadata
        return PassedResult()


class _NoopExecutor(AgentExecutor):
    async def execute(self, context, event_queue):
        pass

    async def cancel(self, context, event_queue):
        pass


@pytest.mark.asyncio
async def test_no_provider_configured_metadata_is_none():
    """Without a history_provider, validators receive metadata=None."""
    v = _RecordingValidator()
    guard = Guard("input").use(v)
    executor = GuardedAgentExecutor(
        executor=_NoopExecutor(),
        input_config=InputGuardConfig(guard=guard),
    )
    await executor.execute(_make_context(), event_queue())
    assert v.seen_metadata is None


@pytest.mark.asyncio
async def test_provider_returns_history_populates_metadata():
    """Provider returning messages puts them on GuardMetadata.conversation_history."""
    v = _RecordingValidator()
    guard = Guard("input").use(v)

    async def provider(ctx):
        return [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

    executor = GuardedAgentExecutor(
        executor=_NoopExecutor(),
        input_config=InputGuardConfig(guard=guard, history_provider=provider),
    )
    await executor.execute(_make_context(), event_queue())

    assert v.seen_metadata is not None
    assert v.seen_metadata.conversation_history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


@pytest.mark.asyncio
async def test_provider_raises_falls_back_to_no_history(caplog):
    """Exception in provider is caught; validation runs with metadata=None."""
    v = _RecordingValidator()
    guard = Guard("input").use(v)

    async def provider(ctx):
        raise RuntimeError("upstream is down")

    executor = GuardedAgentExecutor(
        executor=_NoopExecutor(),
        input_config=InputGuardConfig(guard=guard, history_provider=provider),
    )
    with caplog.at_level(logging.WARNING):
        await executor.execute(_make_context(), event_queue())
        await executor.execute(_make_context(), event_queue())

    assert v.seen_metadata is None
    assert any("history_provider failed" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_provider_returns_empty_omits_key():
    """Provider returning [] → no history added, metadata is None."""
    v = _RecordingValidator()
    guard = Guard("input").use(v)

    async def provider(ctx):
        return []

    executor = GuardedAgentExecutor(
        executor=_NoopExecutor(),
        input_config=InputGuardConfig(guard=guard, history_provider=provider),
    )
    await executor.execute(_make_context(), event_queue())

    # Empty history → metadata dict is empty → executor passes None to validators
    assert v.seen_metadata is None
