from __future__ import annotations

import asyncio

import pytest
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.types import Message, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent

from _a2a_compat import (
    TASK_STATE_COMPLETED,
    TASK_STATE_REJECTED,
    artifact_update_event,
    dequeue,
    event_queue,
    get_text,
    new_task,
    request_context,
    status_update_event,
    text_part,
    user_message,
)
from aegis import ItemEdit
from aegis.a2a.executor import GuardedAgentExecutor
from aegis.a2a.types import InputGuardConfig, OutputGuardConfig
from aegis.guard import Guard
from aegis.mutation_guard import MutationGuard
from aegis.types import OnFailAction, ValidationError
from aegis.validators.base import (
    BaseMutationValidator,
    BaseValidator,
    FailedResult,
    PassedResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_message(text: str) -> Message:
    return user_message([text_part(text)])


def make_context(text: str = "hello") -> RequestContext:
    return request_context(make_message(text), task_id="task-1", context_id="ctx-1")


class DummyExecutor(AgentExecutor):
    """Tracks calls to execute() and cancel()."""

    def __init__(self):
        self.execute_called = False
        self.cancel_called = False
        self.received_context = None
        self.received_eq = None

    async def execute(self, context, event_queue):
        self.execute_called = True
        self.received_context = context
        self.received_eq = event_queue

    async def cancel(self, context, event_queue):
        self.cancel_called = True


class ArtifactEmittingExecutor(AgentExecutor):
    """Emits a TaskArtifactUpdateEvent during execute()."""

    def __init__(self, text: str = "output"):
        self._text = text

    async def execute(self, context, event_queue):
        event = artifact_update_event([text_part(self._text)])
        await event_queue.enqueue_event(event)

    async def cancel(self, context, event_queue):
        pass


class AlwaysPassValidator(BaseValidator):
    async def validate(self, view, metadata=None):
        view.render()
        return PassedResult()


class AlwaysFailValidator(BaseValidator):
    async def validate(self, view, metadata=None):
        view.render()
        return FailedResult(error_message="blocked")


class UpperCaseMutator(BaseMutationValidator):
    async def validate(self, view, metadata=None):
        if any(p.value != p.value.upper() for p in view.items):
            return FailedResult(error_message="not uppercase")
        return PassedResult()

    async def mutate(self, view, metadata=None, validation_failure=None):
        return [ItemEdit(p.id, p.value.upper()) for p in view.items if p.value != p.value.upper()]


class AlwaysStampMutator(BaseMutationValidator):
    """Always fails and appends '!' to every text part — fires even on empty text."""

    async def validate(self, view, metadata=None):
        return FailedResult(error_message="stamp")

    async def mutate(self, view, metadata=None, validation_failure=None):
        return [ItemEdit(p.id, p.value + "!") for p in view.items]


@pytest.mark.asyncio
async def test_run_mutation_stage_noop_when_no_parts():
    """A partless message must not gain a synthesized part, even if a mutator would
    fire on an empty view. Regression: validate(parts=None) synthesizes [TextPart('')]."""
    guarded = GuardedAgentExecutor(executor=DummyExecutor())
    mutation = MutationGuard("m").use(AlwaysStampMutator())
    assert await guarded._run_mutation_stage(mutation, None) is None
    assert await guarded._run_mutation_stage(mutation, []) is None


# ---------------------------------------------------------------------------
# Tests: No guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_guards():
    inner = DummyExecutor()
    guarded = GuardedAgentExecutor(executor=inner)
    ctx = make_context()
    eq = event_queue()

    await guarded.execute(ctx, eq)
    assert inner.execute_called


# ---------------------------------------------------------------------------
# Tests: Input guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_input_guard_pass():
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysPassValidator())
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard),
    )

    await guarded.execute(make_context(), event_queue())
    assert inner.execute_called


@pytest.mark.asyncio
async def test_input_guard_fail_noop_default():
    """Guard fails with LOG, no on_fail set -> wrapper continues."""
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.LOG)
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard),
    )

    await guarded.execute(make_context(), event_queue())
    assert inner.execute_called


@pytest.mark.asyncio
async def test_input_guard_exception_rejects():
    """Validator with on_fail=EXCEPTION -> task rejected, execute() not called."""
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.EXCEPTION)
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)
    assert not inner.execute_called

    # Verify rejection event was emitted
    event = await dequeue(eq)
    assert event is not None


@pytest.mark.asyncio
async def test_input_guard_fail_async_hook_rejects():
    """Async on_fail returns True -> task rejected."""
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)

    async def hook(failures, context, event_queue):
        return True  # reject

    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, on_fail=hook),
    )

    await guarded.execute(make_context(), event_queue())
    assert not inner.execute_called


@pytest.mark.asyncio
async def test_input_guard_fail_hook_custom_message():
    """Hook returns a string -> task rejected with that custom message."""
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)

    async def hook(failures, context, event_queue):
        return "Custom rejection message"

    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, on_fail=hook),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)
    assert not inner.execute_called

    # Drain events: task creation then rejection status
    _task_event = await dequeue(eq)
    rejection = await dequeue(eq)
    assert rejection.status.state == TASK_STATE_REJECTED
    assert get_text(rejection.status.message.parts[0]) == "Custom rejection message"


@pytest.mark.asyncio
async def test_input_guard_fail_async_hook_continues():
    """Async on_fail returns False -> execution continues."""
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)

    async def hook(failures, context, event_queue):
        return False  # continue

    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, on_fail=hook),
    )

    await guarded.execute(make_context(), event_queue())
    assert inner.execute_called


@pytest.mark.asyncio
async def test_input_guard_fail_sync_hook():
    """Sync on_fail works via _maybe_await."""
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    hook_calls = []

    def hook(failures, context, event_queue):
        hook_calls.append(failures)
        return True  # reject

    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, on_fail=hook),
    )

    await guarded.execute(make_context(), event_queue())
    assert not inner.execute_called
    assert len(hook_calls) == 1


# ---------------------------------------------------------------------------
# Tests: Input guard — static hook values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_input_guard_fail_static_true_rejects():
    """on_fail=True -> task rejected with aggregated errors."""
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, on_fail=True),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)
    assert not inner.execute_called

    _task_event = await dequeue(eq)
    rejection = await dequeue(eq)
    assert rejection.status.state == TASK_STATE_REJECTED
    assert get_text(rejection.status.message.parts[0]) == "blocked"


@pytest.mark.asyncio
async def test_input_guard_fail_static_string_rejects():
    """on_fail='msg' -> task rejected with that message."""
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, on_fail="Request denied."),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)
    assert not inner.execute_called

    _task_event = await dequeue(eq)
    rejection = await dequeue(eq)
    assert rejection.status.state == TASK_STATE_REJECTED
    assert get_text(rejection.status.message.parts[0]) == "Request denied."


@pytest.mark.asyncio
async def test_input_guard_fail_static_false_continues():
    """on_fail=False -> execution continues."""
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, on_fail=False),
    )

    await guarded.execute(make_context(), event_queue())
    assert inner.execute_called


# ---------------------------------------------------------------------------
# Tests: Input mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_input_mutation_guard():
    """Inner executor receives mutated input."""
    inner = DummyExecutor()
    mutation = MutationGuard("in-mutate").use(UpperCaseMutator())
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(mutation_guard=mutation),
    )

    await guarded.execute(make_context("hello"), event_queue())
    assert inner.execute_called
    assert inner.received_context.get_user_input() == "HELLO"


@pytest.mark.asyncio
async def test_input_mutation_guard_original_input():
    """Original input is accessible via original_input."""
    inner = DummyExecutor()
    mutation = MutationGuard("in-mutate").use(UpperCaseMutator())
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(mutation_guard=mutation),
    )

    await guarded.execute(make_context("hello"), event_queue())
    assert inner.received_context.original_input == "hello"
    assert inner.received_context.get_user_input() == "HELLO"


@pytest.mark.asyncio
async def test_input_guard_then_mutation_default_order():
    """Default order: Guard runs before MutationGuard."""
    call_order = []

    class TrackingValidator(BaseValidator):
        async def validate(self, view, metadata=None):
            view.render()
            call_order.append("guard")
            return PassedResult()

    class TrackingMutator(BaseMutationValidator):
        async def validate(self, view, metadata=None):
            call_order.append("mutation_validate")
            return FailedResult(error_message="needs fix")

        async def mutate(self, view, metadata=None, validation_failure=None):
            call_order.append("mutation_mutate")
            return [ItemEdit(p.id, p.value) for p in view.items]

    inner = DummyExecutor()
    guard = Guard("in").use(TrackingValidator())
    mutation = MutationGuard("in-mutate").use(TrackingMutator())
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, mutation_guard=mutation),
    )

    await guarded.execute(make_context(), event_queue())
    assert call_order == ["guard", "mutation_validate", "mutation_mutate"]


@pytest.mark.asyncio
async def test_input_mutation_first_reverses_order():
    """mutation_first=True: MutationGuard runs before Guard."""
    call_order = []

    class TrackingValidator(BaseValidator):
        async def validate(self, view, metadata=None):
            view.render()
            call_order.append("guard")
            return PassedResult()

    class TrackingMutator(BaseMutationValidator):
        async def validate(self, view, metadata=None):
            call_order.append("mutation_validate")
            return FailedResult(error_message="needs fix")

        async def mutate(self, view, metadata=None, validation_failure=None):
            call_order.append("mutation_mutate")
            return [ItemEdit(p.id, p.value) for p in view.items]

    inner = DummyExecutor()
    guard = Guard("in").use(TrackingValidator())
    mutation = MutationGuard("in-mutate").use(TrackingMutator())
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, mutation_guard=mutation, mutation_first=True),
    )

    await guarded.execute(make_context(), event_queue())
    assert call_order == ["mutation_validate", "mutation_mutate", "guard"]


@pytest.mark.asyncio
async def test_input_mutation_first_guard_sees_mutated_input():
    """mutation_first=True: guard receives already-mutated input."""
    seen_values = []

    class CapturingValidator(BaseValidator):
        async def validate(self, view, metadata=None):
            value = view.render()
            seen_values.append(value)
            return PassedResult()

    inner = DummyExecutor()
    guard = Guard("in").use(CapturingValidator())
    mutation = MutationGuard("in-mutate").use(UpperCaseMutator())
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, mutation_guard=mutation, mutation_first=True),
    )

    await guarded.execute(make_context("hello"), event_queue())
    # Guard saw the uppercased input (flattened to a single value)
    assert seen_values[0] == "HELLO"


# ---------------------------------------------------------------------------
# Tests: Output guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_guard_fail_noop_default():
    """Output guard fails (LOG) with no on_fail -> artifact still emitted."""
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.LOG)
    inner = ArtifactEmittingExecutor("hello")
    guarded = GuardedAgentExecutor(
        executor=inner,
        output_config=OutputGuardConfig(guard=guard),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    event = await dequeue(eq)
    assert isinstance(event, TaskArtifactUpdateEvent)
    assert get_text(event.artifact.parts[0]) == "hello"


@pytest.mark.asyncio
async def test_output_guard_exception_propagates():
    """Validator with on_fail=EXCEPTION -> ValidationError propagates up."""
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.EXCEPTION)
    inner = ArtifactEmittingExecutor("hello")
    guarded = GuardedAgentExecutor(
        executor=inner,
        output_config=OutputGuardConfig(guard=guard),
    )

    with pytest.raises(ValidationError, match="blocked"):
        await guarded.execute(make_context(), event_queue())


@pytest.mark.asyncio
async def test_output_guard_async_callback():
    """Async on_fail called on guard failure."""
    callback_failures = []

    async def hook(failures):
        callback_failures.extend(failures)
        return False  # explicit continue

    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    inner = ArtifactEmittingExecutor("hello")
    guarded = GuardedAgentExecutor(
        executor=inner,
        output_config=OutputGuardConfig(guard=guard, on_fail=hook),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)
    assert len(callback_failures) == 1
    assert callback_failures[0].error_message == "blocked"


@pytest.mark.asyncio
async def test_output_guard_sync_callback():
    """Sync on_fail works via _maybe_await."""
    callback_failures = []

    def hook(failures):
        callback_failures.extend(failures)
        return False  # explicit continue

    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    inner = ArtifactEmittingExecutor("hello")
    guarded = GuardedAgentExecutor(
        executor=inner,
        output_config=OutputGuardConfig(guard=guard, on_fail=hook),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)
    assert len(callback_failures) == 1


# ---------------------------------------------------------------------------
# Tests: Output guard — static hook values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_guard_static_true_suppresses():
    """on_fail=True -> artifact suppressed."""
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    inner = ArtifactEmittingExecutor("hello")
    guarded = GuardedAgentExecutor(
        executor=inner,
        output_config=OutputGuardConfig(guard=guard, on_fail=True),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    import asyncio

    with pytest.raises(asyncio.QueueEmpty):
        await dequeue(eq)


@pytest.mark.asyncio
async def test_output_guard_static_string_replaces():
    """on_fail='msg' -> artifact text replaced."""
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    inner = ArtifactEmittingExecutor("hello")
    guarded = GuardedAgentExecutor(
        executor=inner,
        output_config=OutputGuardConfig(guard=guard, on_fail="Redacted."),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    event = await dequeue(eq)
    assert isinstance(event, TaskArtifactUpdateEvent)
    assert get_text(event.artifact.parts[0]) == "Redacted."


@pytest.mark.asyncio
async def test_output_guard_static_false_continues():
    """on_fail=False -> artifact emitted as-is."""
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    inner = ArtifactEmittingExecutor("hello")
    guarded = GuardedAgentExecutor(
        executor=inner,
        output_config=OutputGuardConfig(guard=guard, on_fail=False),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    event = await dequeue(eq)
    assert isinstance(event, TaskArtifactUpdateEvent)
    assert get_text(event.artifact.parts[0]) == "hello"


# ---------------------------------------------------------------------------
# Tests: Output mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_mutation_guard():
    """Output artifact text is mutated."""
    mutation = MutationGuard("out-mutate").use(UpperCaseMutator())
    inner = ArtifactEmittingExecutor("hello")
    guarded = GuardedAgentExecutor(
        executor=inner,
        output_config=OutputGuardConfig(mutation_guard=mutation),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    event = await dequeue(eq)
    assert isinstance(event, TaskArtifactUpdateEvent)
    assert get_text(event.artifact.parts[0]) == "HELLO"


@pytest.mark.asyncio
async def test_output_guard_then_mutation_default_order():
    """Default order: output guard runs before output mutation."""
    call_order = []

    class TrackingDetector(BaseValidator):
        async def validate(self, view, metadata=None):
            view.render()
            call_order.append("guard")
            return PassedResult()

    class TrackingMutator(BaseMutationValidator):
        async def validate(self, view, metadata=None):
            call_order.append("mutation_validate")
            return FailedResult(error_message="needs fix")

        async def mutate(self, view, metadata=None, validation_failure=None):
            call_order.append("mutation_mutate")
            return [ItemEdit(p.id, p.value) for p in view.items]

    guard = Guard("out").use(TrackingDetector())
    mutation = MutationGuard("out-mutate").use(TrackingMutator())
    inner = ArtifactEmittingExecutor("hello")
    guarded = GuardedAgentExecutor(
        executor=inner,
        output_config=OutputGuardConfig(guard=guard, mutation_guard=mutation),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)
    assert call_order == ["guard", "mutation_validate", "mutation_mutate"]


@pytest.mark.asyncio
async def test_output_mutation_first_reverses_order():
    """mutation_first=True: MutationGuard runs before Guard in output phase."""
    call_order = []

    class TrackingDetector(BaseValidator):
        async def validate(self, view, metadata=None):
            view.render()
            call_order.append("guard")
            return PassedResult()

    class TrackingMutator(BaseMutationValidator):
        async def validate(self, view, metadata=None):
            call_order.append("mutation_validate")
            return FailedResult(error_message="needs fix")

        async def mutate(self, view, metadata=None, validation_failure=None):
            call_order.append("mutation_mutate")
            return [ItemEdit(p.id, p.value) for p in view.items]

    guard = Guard("out").use(TrackingDetector())
    mutation = MutationGuard("out-mutate").use(TrackingMutator())
    inner = ArtifactEmittingExecutor("hello")
    guarded = GuardedAgentExecutor(
        executor=inner,
        output_config=OutputGuardConfig(guard=guard, mutation_guard=mutation, mutation_first=True),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)
    assert call_order == ["mutation_validate", "mutation_mutate", "guard"]


# ---------------------------------------------------------------------------
# Tests: Passthrough and delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_artifact_events_passthrough():
    """Status events pass through the guarded event queue unmodified."""

    class StatusEmitter(AgentExecutor):
        async def execute(self, context, event_queue):
            event = status_update_event(TASK_STATE_COMPLETED, task_id="task-1", context_id="ctx-1")
            await event_queue.enqueue_event(event)

        async def cancel(self, context, event_queue):
            pass

    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.EXCEPTION)
    guarded = GuardedAgentExecutor(
        executor=StatusEmitter(),
        output_config=OutputGuardConfig(guard=guard),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    event = await dequeue(eq)
    assert isinstance(event, TaskStatusUpdateEvent)


@pytest.mark.asyncio
async def test_cancel_delegates():
    inner = DummyExecutor()
    guarded = GuardedAgentExecutor(executor=inner)

    await guarded.cancel(make_context(), event_queue())
    assert inner.cancel_called


# ---------------------------------------------------------------------------
# Tests: Speculative holdback
# ---------------------------------------------------------------------------


class SlowGuardValidator(BaseValidator):
    """Validator that sleeps, then passes or fails, tracking timing."""

    def __init__(self, delay: float, fail: bool = False):
        self._delay = delay
        self._fail = fail

    async def validate(self, view, metadata=None):
        view.render()
        await asyncio.sleep(self._delay)
        if self._fail:
            return FailedResult(error_message="blocked")
        return PassedResult()


class ReadOnlyExecutor(AgentExecutor):
    """Read-only agent: emits an artifact, with no external side effects.

    Models a read-only agent under speculative holdback — the only configuration
    the feature supports. ``emit`` is safe; there is nothing to undo on a failing
    verdict. With ``idle=True`` the agent keeps running after emitting (modelling
    ongoing work / streaming) so a failing verdict can cancel it in flight; with
    ``idle=False`` it returns immediately after emitting.
    """

    def __init__(self, text: str = "answer", *, idle: bool = False):
        self._text = text
        self._idle = idle
        self.emitted = False
        self.cancelled = False

    async def execute(self, context, event_queue):
        # Emit output (buffered while held).
        await event_queue.enqueue_event(artifact_update_event([text_part(self._text)]))
        self.emitted = True
        if not self._idle:
            return
        try:
            # Keep running until a failing verdict cancels us.
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def cancel(self, context, event_queue):
        pass


@pytest.mark.asyncio
async def test_speculative_pass_flushes_output():
    """Guard passes → buffered output is delivered to the user."""
    inner = ReadOnlyExecutor(text="hello")
    guard = Guard("in").use(SlowGuardValidator(0.01))
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, speculative=True),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    assert inner.emitted
    event = await dequeue(eq)
    assert isinstance(event, TaskArtifactUpdateEvent)
    assert get_text(event.artifact.parts[0]) == "hello"


@pytest.mark.asyncio
async def test_speculative_fail_cancels_agent_no_output():
    """Guard fails → agent cancelled, no buffered output leaks."""
    inner = ReadOnlyExecutor(text="hello", idle=True)
    guard = Guard("in").use(SlowGuardValidator(0.01, fail=True), on_fail=OnFailAction.NOOP)
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, speculative=True, on_fail=True),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    # Invariant 1: no agent output leaked — only the task + rejection.
    _task_event = await dequeue(eq)
    rejection = await dequeue(eq)
    assert rejection.status.state == TASK_STATE_REJECTED
    with pytest.raises(asyncio.QueueEmpty):
        await dequeue(eq)

    # Invariant 2: the still-running agent was cancelled.
    assert inner.cancelled


@pytest.mark.asyncio
async def test_speculative_guard_exception_rejects_and_cancels():
    """on_fail=EXCEPTION → guard stage rejects the task, agent cancelled.

    Mirrors sequential ``test_input_guard_exception_rejects``: the guard stage
    catches ``ValidationError`` and rejects rather than propagating.
    """
    inner = ReadOnlyExecutor(idle=True)
    guard = Guard("in").use(SlowGuardValidator(0.01, fail=True), on_fail=OnFailAction.EXCEPTION)
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, speculative=True),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    # Rejection emitted, no leaked output, agent cancelled.
    _task_event = await dequeue(eq)
    rejection = await dequeue(eq)
    assert rejection.status.state == TASK_STATE_REJECTED
    with pytest.raises(asyncio.QueueEmpty):
        await dequeue(eq)
    assert inner.cancelled


@pytest.mark.asyncio
async def test_speculative_guard_stage_raises_propagates_and_cancels():
    """An unexpected error from the guard stage cancels the agent and propagates.

    Exercises the ``except BaseException`` branch of the controller (distinct
    from a normal rejection). A guard whose validator raises a non-Validation
    error surfaces to the caller after the agent is cancelled and the buffer
    discarded.
    """
    inner = ReadOnlyExecutor(idle=True)

    class ExplodingValidator(BaseValidator):
        async def validate(self, value=None, **kwargs):
            await asyncio.sleep(0.01)
            raise RuntimeError("guard exploded")

    guard = Guard("in").use(ExplodingValidator())
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, speculative=True),
    )

    with pytest.raises(RuntimeError, match="guard exploded"):
        await guarded.execute(make_context(), event_queue())

    assert inner.cancelled


@pytest.mark.asyncio
async def test_speculative_agent_finishes_before_guard():
    """Fast agent finishes (buffered) before slow guard → flushed on pass."""
    inner = ReadOnlyExecutor(text="fast")
    guard = Guard("in").use(SlowGuardValidator(0.05))
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(guard=guard, speculative=True),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    event = await dequeue(eq)
    assert get_text(event.artifact.parts[0]) == "fast"
    assert inner.emitted


@pytest.mark.asyncio
async def test_speculative_agent_exception_propagates_after_pass():
    """Agent raises after guard passes → exception propagates to caller."""

    class RaisingExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            raise RuntimeError("agent boom")

        async def cancel(self, context, event_queue):
            pass

    guard = Guard("in").use(SlowGuardValidator(0.01))
    guarded = GuardedAgentExecutor(
        executor=RaisingExecutor(),
        input_config=InputGuardConfig(guard=guard, speculative=True),
    )

    with pytest.raises(RuntimeError, match="agent boom"):
        await guarded.execute(make_context(), event_queue())


@pytest.mark.asyncio
async def test_speculative_falls_back_when_mutation_not_first():
    """speculative=True + non-mutation_first mutation guard → sequential path."""
    inner = DummyExecutor()
    guard = Guard("in").use(AlwaysPassValidator())
    mutation = MutationGuard("in-mutate").use(UpperCaseMutator())
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(
            guard=guard,
            mutation_guard=mutation,
            mutation_first=False,
            speculative=True,
        ),
    )

    assert guarded._should_speculate(guarded._input_config) is False
    await guarded.execute(make_context("hi"), event_queue())
    # Sequential path ran mutation, so inner saw mutated input
    assert inner.received_context.get_user_input() == "HI"


@pytest.mark.asyncio
async def test_speculative_with_mutation_first_qualifies():
    """speculative=True + mutation_first=True → speculates on mutated input."""
    inner = ReadOnlyExecutor(text="out")
    guard = Guard("in").use(SlowGuardValidator(0.01))
    mutation = MutationGuard("in-mutate").use(UpperCaseMutator())
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(
            guard=guard,
            mutation_guard=mutation,
            mutation_first=True,
            speculative=True,
        ),
    )

    assert guarded._should_speculate(guarded._input_config) is True
    eq = event_queue()
    await guarded.execute(make_context("hi"), eq)
    event = await dequeue(eq)
    assert get_text(event.artifact.parts[0]) == "out"


class TaskEmittingExecutor(AgentExecutor):
    """Emits its own Task creation event (lifecycle) before the artifact.

    Models a read-only agent whose first emission is a ``Task`` — the shape an
    agent produces when it opts into ``stream_lifecycle_events=True`` so the
    framework's lifecycle events reach the client immediately. After emitting it
    keeps running (no side effects) so a failing verdict can cancel it in flight.
    """

    def __init__(self, text: str = "answer"):
        self._text = text

    async def execute(self, context, event_queue):
        task = new_task(context.message)
        await event_queue.enqueue_event(task)  # lifecycle event
        await event_queue.enqueue_event(artifact_update_event([text_part(self._text)], task_id=task.id))
        await asyncio.Event().wait()  # run until cancelled by the failing verdict

    async def cancel(self, context, event_queue):
        pass


@pytest.mark.asyncio
async def test_speculative_fail_with_lifecycle_streaming_no_duplicate_task():
    """Guard fails while streaming lifecycle events → exactly one Task reaches
    the client, no leaked artifact, and the rejection is emitted.

    Regression guard for the case where the agent streams its own ``Task``
    creation event (``stream_lifecycle_events=True``) and, on a failing verdict
    with ``context.current_task is None``, ``_reject_task`` calls ``new_task()``
    and enqueues a *second* Task — the client would then see two tasks on the
    stream. The fail path must not double-emit the task.
    """
    inner = TaskEmittingExecutor(text="secret")
    guard = Guard("in").use(SlowGuardValidator(0.01, fail=True), on_fail=OnFailAction.NOOP)
    guarded = GuardedAgentExecutor(
        executor=inner,
        input_config=InputGuardConfig(
            guard=guard,
            speculative=True,
            stream_lifecycle_events=True,
            on_fail=True,
        ),
    )

    eq = event_queue()
    await guarded.execute(make_context(), eq)

    events = []
    while True:
        try:
            events.append(await dequeue(eq))
        except asyncio.QueueEmpty:
            break

    # The rejection reached the client.
    rejections = [e for e in events if isinstance(e, TaskStatusUpdateEvent) and e.status.state == TASK_STATE_REJECTED]
    assert len(rejections) == 1

    # No agent output ever leaked (the buffered artifact was discarded).
    assert not any(isinstance(e, TaskArtifactUpdateEvent) for e in events)

    # Exactly one Task on the stream — the fail path must not double-emit.
    tasks = [e for e in events if isinstance(e, Task)]
    assert len(tasks) == 1, f"expected 1 Task, client saw {len(tasks)}: {[t.id for t in tasks]}"
