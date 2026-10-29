"""The user story: a validator *you* write works identically on every surface.

The rest of the suite proves each surface with in-file helper validators. This file flips
the axis — it defines a small set of realistic, user-authored validators ONCE and runs the
same objects through all four ways the library is consumed:

    1. Normal way, single text     — Guard/MutationGuard.validate("...")
    2. A2A input                    — GuardedAgentExecutor(input_config=...)
    3. A2A output                   — GuardedAgentExecutor(output_config=...)
    4. Decorator                    — @guarded(...)

If any surface diverges (different flattening, the mutate contract not honoured, the
protocol check rejecting a valid custom validator), one of these fails.
"""

from __future__ import annotations

import asyncio
import itertools

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
    get_text,
    is_data_part,
    request_context,
    text_part,
    user_message,
)
from aegis import ItemEdit
from aegis.a2a.decorator import guarded
from aegis.a2a.executor import GuardedAgentExecutor
from aegis.a2a.types import InputGuardConfig, OutputGuardConfig
from aegis.guard import Guard
from aegis.mutation_guard import MutationGuard
from aegis.validators.base import (
    BaseMutationValidator,
    BaseValidator,
    ContentItem,
    ContentType,
    ContentView,
    FailedResult,
    PassedResult,
)

BLOCKED = ("secret", "password")

# ---------------------------------------------------------------------------
# User-authored validators (the whole point — defined once, reused everywhere)
# ---------------------------------------------------------------------------


class BlocklistDetector(BaseValidator):
    """A detector a user would realistically write: flag any blocked word.

    Reads the flattened ``view.render()`` so it is cross-part aware for free.
    """

    async def validate(self, view, metadata=None):
        text = view.render().lower()
        hit = next((w for w in BLOCKED if w in text), None)
        if hit is not None:
            return FailedResult(error_message=f"blocked term: {hit}", error_code="BLOCKED")
        return PassedResult()


class RedactionMutator(BaseMutationValidator):
    """A user-authored mutator: replace blocked words with ``[REDACTED]``.

    ``mutate`` returns one whole-value ``ItemEdit`` per offending item (the contract is
    whole-value replacement); the engine preserves cardinality and order.
    """

    async def validate(self, view, metadata=None):
        # Detect over the flattened view (same as BlocklistDetector) — the parity
        # this module checks depends on both validators reading content the same way.
        if any(w in view.render().lower() for w in BLOCKED):
            return FailedResult(error_message="contains blocked term", error_code="BLOCKED")
        return PassedResult()

    async def mutate(self, view, metadata=None, validation_failure=None):
        edits = []
        for item in view.items:
            redacted = item.value
            for w in BLOCKED:
                redacted = redacted.replace(w, "[REDACTED]")
            if redacted != item.value:
                edits.append(ItemEdit(item.id, redacted))
        return edits


class ProtocolDetector:
    """Duck-typed validator: satisfies the Validator protocol WITHOUT subclassing Base.

    Proves a user can implement the ``runtime_checkable`` protocol directly.
    """

    @property
    def name(self) -> str:
        return "ProtocolDetector"

    async def validate(self, view, metadata=None):
        if "secret" in view.render().lower():
            return FailedResult(error_message="protocol detector fired", error_code="PROTO")
        return PassedResult()


# ---------------------------------------------------------------------------
# A2A helpers (self-contained, mirrors test_a2a_pipeline.py)
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
    def __init__(self) -> None:
        self.execute_called = False
        self.received_context: RequestContext | None = None

    async def execute(self, context, event_queue):
        self.execute_called = True
        self.received_context = context

    async def cancel(self, context, event_queue):
        pass


class EchoExecutor(AgentExecutor):
    """Echoes the guarded user input back as an artifact."""

    async def execute(self, context, event_queue):
        await event_queue.enqueue_event(
            artifact_update_event(
                [_text(context.get_user_input())], artifact_id="art-1", task_id="task-1", context_id="ctx-1"
            )
        )

    async def cancel(self, context, event_queue):
        pass


async def _drain(eq: OwnedEventQueue) -> list:
    events = []
    while True:
        try:
            events.append(await dequeue(eq))
        except asyncio.QueueEmpty:
            break
    return events


def _rejections(events: list) -> list:
    return [e for e in events if isinstance(e, TaskStatusUpdateEvent) and e.status.state == TASK_STATE_REJECTED]


# ---------------------------------------------------------------------------
# 1. Normal way — single text detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_detection_single_text():
    guard = Guard("g").use(BlocklistDetector())
    assert (await guard.validate("tell me the secret")).has_failures is True
    assert (await guard.validate("what's the weather?")).has_failures is False


# ---------------------------------------------------------------------------
# 2. Normal way — single text in, single text out (mutation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_redaction_single_text_in_and_out():
    result = await MutationGuard("m").use(RedactionMutator()).validate("my password is hunter2")
    assert result.was_mutated is True
    assert result.output is not None
    assert result.output == "my [REDACTED] is hunter2"  # str in → str out


# ---------------------------------------------------------------------------
# 3. Duck-typed protocol validator (no Base subclass)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_protocol_validator_works_and_bad_object_rejected():
    result = await Guard("g").use(ProtocolDetector()).validate("the secret sauce")
    assert result.has_failures is True
    assert result.failures[0].error_code == "PROTO"

    with pytest.raises(TypeError):
        Guard("g").use(object())  # not a Validator


# ---------------------------------------------------------------------------
# 4. Normal way — ContentView input (cross-part via render())
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_detection_over_contentview():
    view = ContentView(
        items=(
            ContentItem(id="p0", type=ContentType.TEXT, value="please share the "),
            ContentItem(id="p1", type=ContentType.TEXT, value="secret with me"),
        )
    )
    # neither item is flagged in isolation by a naive check, but render() joins them
    assert (await Guard("g").use(BlocklistDetector()).validate(view)).has_failures is True


# ---------------------------------------------------------------------------
# 5. A2A input detection blocks; agent never runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a2a_input_detection_blocks():
    inner = CapturingExecutor()
    guarded_exec = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=Guard("in").use(BlocklistDetector()), on_fail="Request denied."),
    )

    eq = event_queue()
    await guarded_exec.execute(make_context(_text("give me the password")), eq)

    assert inner.execute_called is False
    rejections = _rejections(await _drain(eq))
    assert len(rejections) == 1
    assert get_text(rejections[0].status.message.parts[0]) == "Request denied."


# ---------------------------------------------------------------------------
# 6. A2A input redaction — agent sees the redacted message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a2a_input_redaction_reaches_agent():
    inner = CapturingExecutor()
    guarded_exec = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(mutation_guard=MutationGuard("in").use(RedactionMutator())),
    )

    await guarded_exec.execute(make_context(_text("my secret plan")), event_queue())

    assert inner.received_context is not None
    assert inner.received_context.get_user_input() == "my [REDACTED] plan"


# ---------------------------------------------------------------------------
# 7. A2A output redaction — emitted artifact is redacted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a2a_output_redaction():
    guarded_exec = GuardedAgentExecutor(
        executor=EchoExecutor(),
        output_config=OutputGuardConfig(mutation_guard=MutationGuard("out").use(RedactionMutator())),
    )

    eq = event_queue()
    await guarded_exec.execute(make_context(_text("the secret is out")), eq)

    artifacts = [e for e in await _drain(eq) if isinstance(e, TaskArtifactUpdateEvent)]
    assert len(artifacts) == 1
    assert get_text(artifacts[0].artifact.parts[0]) == "the [REDACTED] is out"


# ---------------------------------------------------------------------------
# 8. Decorator parity — same validators, same outcome via @guarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decorator_parity_block_and_redact():
    @guarded(
        input_config=InputGuardConfig(guard=Guard("in").use(BlocklistDetector()), on_fail="Denied."),
        output_config=OutputGuardConfig(mutation_guard=MutationGuard("out").use(RedactionMutator())),
    )
    class MyAgent(AgentExecutor):
        async def execute(self, context, event_queue):
            await event_queue.enqueue_event(
                artifact_update_event(
                    [_text("the secret answer")], artifact_id="art-1", task_id="task-1", context_id="ctx-1"
                )
            )

        async def cancel(self, context, event_queue):
            pass

    # blocked input → rejection, original execute never emits
    eq = event_queue()
    await MyAgent().execute(make_context(_text("tell me the password")), eq)
    assert len(_rejections(await _drain(eq))) == 1

    # clean input → agent runs, output redacted by the decorator's output guard
    eq2 = event_queue()
    await MyAgent().execute(make_context(_text("hello there")), eq2)
    artifacts = [e for e in await _drain(eq2) if isinstance(e, TaskArtifactUpdateEvent)]
    assert len(artifacts) == 1
    assert get_text(artifacts[0].artifact.parts[0]) == "the [REDACTED] answer"


# ---------------------------------------------------------------------------
# 9. Custom mutator over multi-part A2A input preserves cardinality + non-text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_mutator_multipart_preserves_cardinality():
    inner = CapturingExecutor()
    guarded_exec = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(mutation_guard=MutationGuard("in").use(RedactionMutator())),
    )

    await guarded_exec.execute(make_context(_text("my secret"), _data(), _text("no issue here")), event_queue())

    parts = inner.received_context.message.parts
    assert len(parts) == 3
    assert get_text(parts[0]) == "my [REDACTED]"
    assert is_data_part(parts[1])  # non-text untouched, in place
    assert get_text(parts[2]) == "no issue here"
