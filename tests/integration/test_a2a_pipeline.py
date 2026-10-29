"""End-to-end integration tests for the A2A guardrail pipeline.

Unlike the per-component suites (``test_guarded_executor`` / ``test_guarded_event_queue``,
which mostly drive single-text input), these tests exercise the *whole* stack through
``GuardedAgentExecutor.execute()`` with a real ``EventQueue`` and **multi-part** messages:

    context.message.parts  ->  build_view  ->  guard/mutation  ->  apply_view  ->  agent/output

They prove the invariants the view/parts round-trip is supposed to hold when the pieces
run together: cardinality preserved, non-text parts passed through in place, cross-part
context available to detectors, and an unconfigured guard failing loud rather than silent.
"""

from __future__ import annotations

import asyncio
import itertools
import re

import pytest
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.types import Part, TaskArtifactUpdateEvent, TaskStatusUpdateEvent

from _a2a_compat import (
    TASK_STATE_REJECTED,
    OwnedEventQueue,
    artifact_update_event,
    data_part,
    dequeue,
    event_queue,
    get_data,
    get_text,
    is_data_part,
    request_context,
    text_part,
    user_message,
)
from aegis import ItemEdit
from aegis.a2a.executor import GuardedAgentExecutor
from aegis.a2a.types import InputGuardConfig, OutputGuardConfig
from aegis.guard import Guard
from aegis.mutation_guard import MutationGuard
from aegis.validators.base import BaseMutationValidator, BaseValidator, FailedResult, PassedResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text(t: str) -> Part:
    return text_part(t)


def _data() -> Part:
    return data_part()


_MSG_IDS = itertools.count()


def make_context(*parts: Part) -> RequestContext:
    # Unique message_id per call so nothing relies on ID collision.
    return request_context(
        user_message(list(parts), message_id=f"msg-{next(_MSG_IDS)}"),
        task_id="task-1",
        context_id="ctx-1",
    )


class CapturingExecutor(AgentExecutor):
    """Records the (guarded) context the wrapper hands to the inner agent."""

    def __init__(self) -> None:
        self.execute_called = False
        self.received_context: RequestContext | None = None

    async def execute(self, context, event_queue):
        self.execute_called = True
        self.received_context = context

    async def cancel(self, context, event_queue):
        pass


class EchoExecutor(AgentExecutor):
    """Emits the guarded user input straight back as an artifact.

    Used to prove input-side mutations flow through the agent into the output pipeline.
    """

    async def execute(self, context, event_queue):
        event = artifact_update_event(
            [_text(context.get_user_input())], artifact_id="art-1", task_id="task-1", context_id="ctx-1"
        )
        await event_queue.enqueue_event(event)

    async def cancel(self, context, event_queue):
        pass


class UpperCaseMutator(BaseMutationValidator):
    async def validate(self, view, metadata=None):
        if any(i.value != i.value.upper() for i in view.items):
            return FailedResult(error_message="not uppercase")
        return PassedResult()

    async def mutate(self, view, metadata=None, validation_failure=None):
        return [ItemEdit(i.id, i.value.upper()) for i in view.items if i.value != i.value.upper()]


class StampMutator(BaseMutationValidator):
    """Always appends '!' to every text item — a distinct output-side transform."""

    async def validate(self, view, metadata=None):
        return FailedResult(error_message="stamp")

    async def mutate(self, view, metadata=None, validation_failure=None):
        return [ItemEdit(i.id, i.value + "!") for i in view.items]


class SubstringDetector(BaseValidator):
    """Fails if ``needle`` appears anywhere in the rendered (joined) view."""

    def __init__(self, needle: str) -> None:
        self._needle = needle

    async def validate(self, view, metadata=None):
        if self._needle in view.render():
            return FailedResult(error_message=f"found {self._needle!r}")
        return PassedResult()


class SSNDetector(BaseValidator):
    """Flags a full SSN in the whitespace-stripped rendered view.

    The pattern requires all nine digits contiguously (``NNN-NN-NNNN``); it is matched
    against ``render()`` with whitespace removed. A number split across parts — e.g.
    ``"123-45-"`` in one part and ``"6789"`` in the next — only completes once
    ``render()`` joins them, so no single part can trip it. See the test that asserts
    each part alone passes.
    """

    _PATTERN = re.compile(r"\d{3}-\d{2}-\d{4}")

    async def validate(self, view, metadata=None):
        if self._PATTERN.search(re.sub(r"\s+", "", view.render())):
            return FailedResult(error_message="SSN detected")
        return PassedResult()


async def _drain(eq: OwnedEventQueue) -> list:
    events = []
    while True:
        try:
            events.append(await dequeue(eq))
        except asyncio.QueueEmpty:
            break
    return events


# ---------------------------------------------------------------------------
# 1. Multi-part input mutation reaches the agent, cardinality + non-text preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multipart_input_mutation_reaches_agent_preserving_nontext():
    inner = CapturingExecutor()
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(mutation_guard=MutationGuard("in").use(UpperCaseMutator())),
    )

    await guarded.execute(make_context(_text("cat"), _data(), _text("dog")), event_queue())

    ctx = inner.received_context
    assert ctx is not None
    parts = ctx.message.parts
    assert len(parts) == 3  # cardinality preserved
    assert get_text(parts[0]) == "CAT"
    assert is_data_part(parts[1])  # non-text passed through in place
    assert get_data(parts[1]) == {"k": "v"}
    assert get_text(parts[2]) == "DOG"
    # get_user_input() reads the same guarded message — text items only, joined
    assert ctx.get_user_input() == "CAT\nDOG"


# ---------------------------------------------------------------------------
# 2. Full pipeline: input mutation flows through the agent into output mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_input_and_output_mutation():
    guarded = GuardedAgentExecutor(
        executor=EchoExecutor(),
        input_config=InputGuardConfig(mutation_guard=MutationGuard("in").use(UpperCaseMutator())),
        output_config=OutputGuardConfig(mutation_guard=MutationGuard("out").use(StampMutator())),
    )

    eq = event_queue()
    await guarded.execute(make_context(_text("hello")), eq)

    events = await _drain(eq)
    artifacts = [e for e in events if isinstance(e, TaskArtifactUpdateEvent)]
    assert len(artifacts) == 1
    # input upper-cased "hello" -> agent echoed "HELLO" -> output stamped "HELLO!"
    assert get_text(artifacts[0].artifact.parts[0]) == "HELLO!"


# ---------------------------------------------------------------------------
# 3. Detection blocks multi-part input; the agent never runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detection_blocks_multipart_input_agent_never_runs():
    inner = CapturingExecutor()
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(
            guard=Guard("in").use(SubstringDetector("dog")),
            on_fail="Request denied.",
        ),
    )

    eq = event_queue()
    await guarded.execute(make_context(_text("cat"), _data(), _text("dog")), eq)

    assert inner.execute_called is False  # blocked before the agent
    events = await _drain(eq)
    rejections = [e for e in events if isinstance(e, TaskStatusUpdateEvent) and e.status.state == TASK_STATE_REJECTED]
    assert len(rejections) == 1
    assert get_text(rejections[0].status.message.parts[0]) == "Request denied."


# ---------------------------------------------------------------------------
# 4. Cross-part context: a secret split across parts is caught only via render()
# ---------------------------------------------------------------------------


async def _run_ssn_guard(inner: CapturingExecutor, *parts: Part) -> None:
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=Guard("in").use(SSNDetector()), on_fail="Blocked."),
    )
    await guarded.execute(make_context(*parts), event_queue())


@pytest.mark.asyncio
async def test_cross_part_context_detection_catches_split_secret():
    part_a, part_b = _text("my ssn is 123-45-"), _text("6789 thanks")

    # Each part ALONE does not complete the SSN → detector passes, the agent runs.
    # This is what proves render() is required: neither part trips the detector.
    for part in (part_a, part_b):
        inner = CapturingExecutor()
        await _run_ssn_guard(inner, part)
        assert inner.execute_called is True

    # Joined via render(), the SSN completes → blocked, the agent never runs.
    inner = CapturingExecutor()
    await _run_ssn_guard(inner, part_a, part_b)
    assert inner.execute_called is False


@pytest.mark.asyncio
async def test_cross_part_detector_passes_when_no_part_completes_the_pattern():
    """Same detector, but the digits never form a SSN — the agent runs normally."""
    inner = CapturingExecutor()
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=Guard("in").use(SSNDetector()), on_fail="Blocked."),
    )

    await guarded.execute(make_context(_text("hello there"), _text("how are you")), event_queue())

    assert inner.execute_called is True


# ---------------------------------------------------------------------------
# 5. An unconfigured (empty) input guard fails loud through execute()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_input_guard_raises_through_execute():
    guarded = GuardedAgentExecutor(
        executor=CapturingExecutor(),
        input_config=InputGuardConfig(guard=Guard("empty")),  # no validators registered
    )

    with pytest.raises(RuntimeError, match="no validators"):
        await guarded.execute(make_context(_text("hi")), event_queue())


# ---------------------------------------------------------------------------
# 6. Non-text-only input is a no-op (no spurious text part, no crash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nontext_only_input_is_a_noop():
    inner = CapturingExecutor()
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(mutation_guard=MutationGuard("in").use(UpperCaseMutator())),
    )

    await guarded.execute(make_context(_data()), event_queue())

    ctx = inner.received_context
    assert ctx is not None
    parts = ctx.message.parts
    assert len(parts) == 1  # no synthesized text part
    assert is_data_part(parts[0])
