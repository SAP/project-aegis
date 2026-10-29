from __future__ import annotations

import pytest
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.types import Message, TaskArtifactUpdateEvent, TaskStatusUpdateEvent

from _a2a_compat import (
    TASK_STATE_COMPLETED,
    TASK_STATE_REJECTED,
    artifact_update_event,
    dequeue,
    event_queue,
    get_text,
    request_context,
    status_update_event,
    text_part,
    user_message,
)
from aegis import ItemEdit
from aegis.a2a.decorator import guarded
from aegis.a2a.types import InputGuardConfig, OutputGuardConfig
from aegis.guard import Guard
from aegis.mutation_guard import MutationGuard
from aegis.types import OnFailAction
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


def make_artifact_executor(text: str = "hello"):
    """Return an AgentExecutor subclass that emits one artifact."""

    class _Executor(AgentExecutor):
        async def execute(self, context, event_queue):
            event = artifact_update_event([text_part(text)])
            await event_queue.enqueue_event(event)

        async def cancel(self, context, event_queue):
            pass

    return _Executor


# ---------------------------------------------------------------------------
# Tests: No guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decorator_no_guards():
    execute_called = False

    @guarded()
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            nonlocal execute_called
            execute_called = True

        async def cancel(self, context, event_queue):
            pass

    await MyExecutor().execute(make_context(), event_queue())
    assert execute_called


# ---------------------------------------------------------------------------
# Tests: Input guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decorator_input_guard_pass():
    execute_called = False
    guard = Guard("in").use(AlwaysPassValidator())

    @guarded(input_config=InputGuardConfig(guard=guard))
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            nonlocal execute_called
            execute_called = True

        async def cancel(self, context, event_queue):
            pass

    await MyExecutor().execute(make_context(), event_queue())
    assert execute_called


@pytest.mark.asyncio
async def test_decorator_input_guard_fail_noop_default():
    """Guard fails (LOG), no on_fail -> continues."""
    execute_called = False
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.LOG)

    @guarded(input_config=InputGuardConfig(guard=guard))
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            nonlocal execute_called
            execute_called = True

        async def cancel(self, context, event_queue):
            pass

    await MyExecutor().execute(make_context(), event_queue())
    assert execute_called


@pytest.mark.asyncio
async def test_decorator_input_guard_exception_rejects():
    """Validator with on_fail=EXCEPTION -> task rejected, execute() not called."""
    execute_called = False
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.EXCEPTION)

    @guarded(input_config=InputGuardConfig(guard=guard))
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            nonlocal execute_called
            execute_called = True

        async def cancel(self, context, event_queue):
            pass

    eq = event_queue()
    await MyExecutor().execute(make_context(), eq)
    assert not execute_called

    # Verify rejection events emitted
    event = await dequeue(eq)
    assert event is not None


@pytest.mark.asyncio
async def test_decorator_input_guard_fail_with_hook():
    """on_fail hook passed via config rejects task."""
    execute_called = False
    hook_called = False
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)

    async def hook(failures, context, event_queue):
        nonlocal hook_called
        hook_called = True
        return True  # reject

    @guarded(input_config=InputGuardConfig(guard=guard, on_fail=hook))
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            nonlocal execute_called
            execute_called = True

        async def cancel(self, context, event_queue):
            pass

    await MyExecutor().execute(make_context(), event_queue())
    assert hook_called
    assert not execute_called


@pytest.mark.asyncio
async def test_decorator_input_guard_fail_static_true_rejects():
    """on_fail=True via config -> task rejected."""
    execute_called = False
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)

    @guarded(input_config=InputGuardConfig(guard=guard, on_fail=True))
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            nonlocal execute_called
            execute_called = True

        async def cancel(self, context, event_queue):
            pass

    eq = event_queue()
    await MyExecutor().execute(make_context(), eq)
    assert not execute_called

    _task_event = await dequeue(eq)
    rejection = await dequeue(eq)
    assert rejection.status.state == TASK_STATE_REJECTED


@pytest.mark.asyncio
async def test_decorator_input_guard_fail_static_string_rejects():
    """on_fail='msg' via config -> rejected with custom message."""
    execute_called = False
    guard = Guard("in").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)

    @guarded(input_config=InputGuardConfig(guard=guard, on_fail="Nope."))
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            nonlocal execute_called
            execute_called = True

        async def cancel(self, context, event_queue):
            pass

    eq = event_queue()
    await MyExecutor().execute(make_context(), eq)
    assert not execute_called

    _task_event = await dequeue(eq)
    rejection = await dequeue(eq)
    assert get_text(rejection.status.message.parts[0]) == "Nope."


# ---------------------------------------------------------------------------
# Tests: Input mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decorator_input_mutation():
    """Input mutated -> context.get_user_input() returns mutated text."""
    received_text = None
    mutation = MutationGuard("in-mutate").use(UpperCaseMutator())

    @guarded(input_config=InputGuardConfig(mutation_guard=mutation))
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            nonlocal received_text
            received_text = context.get_user_input()

        async def cancel(self, context, event_queue):
            pass

    await MyExecutor().execute(make_context("hello"), event_queue())
    assert received_text == "HELLO"


@pytest.mark.asyncio
async def test_decorator_input_mutation_first_reverses_order():
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

    guard = Guard("in").use(TrackingValidator())
    mutation = MutationGuard("in-mutate").use(TrackingMutator())

    @guarded(input_config=InputGuardConfig(guard=guard, mutation_guard=mutation, mutation_first=True))
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            pass

        async def cancel(self, context, event_queue):
            pass

    await MyExecutor().execute(make_context(), event_queue())
    assert call_order == ["mutation_validate", "mutation_mutate", "guard"]


# ---------------------------------------------------------------------------
# Tests: Output mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decorator_output_mutation():
    """Output artifact text is mutated."""
    mutation = MutationGuard("out-mutate").use(UpperCaseMutator())

    @guarded(output_config=OutputGuardConfig(mutation_guard=mutation))
    class MyExecutor(make_artifact_executor("hello")):
        pass

    eq = event_queue()
    await MyExecutor().execute(make_context(), eq)

    event = await dequeue(eq)
    assert isinstance(event, TaskArtifactUpdateEvent)
    assert get_text(event.artifact.parts[0]) == "HELLO"


@pytest.mark.asyncio
async def test_decorator_output_guard_then_mutation_default_order():
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

    @guarded(output_config=OutputGuardConfig(guard=guard, mutation_guard=mutation))
    class MyExecutor(make_artifact_executor("hi")):
        pass

    eq = event_queue()
    await MyExecutor().execute(make_context(), eq)
    assert call_order == ["guard", "mutation_validate", "mutation_mutate"]


@pytest.mark.asyncio
async def test_decorator_output_mutation_first_reverses_order():
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

    @guarded(output_config=OutputGuardConfig(guard=guard, mutation_guard=mutation, mutation_first=True))
    class MyExecutor(make_artifact_executor("hi")):
        pass

    eq = event_queue()
    await MyExecutor().execute(make_context(), eq)
    assert call_order == ["mutation_validate", "mutation_mutate", "guard"]


# ---------------------------------------------------------------------------
# Tests: Output guard — static hook values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decorator_output_fail_static_true_suppresses():
    """on_fail=True via config -> artifact suppressed."""
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)

    @guarded(output_config=OutputGuardConfig(guard=guard, on_fail=True))
    class MyExecutor(make_artifact_executor("hello")):
        pass

    import asyncio

    eq = event_queue()
    await MyExecutor().execute(make_context(), eq)

    with pytest.raises(asyncio.QueueEmpty):
        await dequeue(eq)


@pytest.mark.asyncio
async def test_decorator_output_fail_static_string_replaces():
    """on_fail='msg' via config -> artifact text replaced."""
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)

    @guarded(output_config=OutputGuardConfig(guard=guard, on_fail="Redacted."))
    class MyExecutor(make_artifact_executor("hello")):
        pass

    eq = event_queue()
    await MyExecutor().execute(make_context(), eq)

    event = await dequeue(eq)
    assert isinstance(event, TaskArtifactUpdateEvent)
    assert get_text(event.artifact.parts[0]) == "Redacted."


# ---------------------------------------------------------------------------
# Tests: Passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decorator_non_artifact_passthrough():
    """Non-artifact events pass through unmodified."""
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.EXCEPTION)

    @guarded(output_config=OutputGuardConfig(guard=guard))
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            event = status_update_event(TASK_STATE_COMPLETED, task_id="task-1", context_id="ctx-1")
            await event_queue.enqueue_event(event)

        async def cancel(self, context, event_queue):
            pass

    eq = event_queue()
    await MyExecutor().execute(make_context(), eq)

    event = await dequeue(eq)
    assert isinstance(event, TaskStatusUpdateEvent)


# ---------------------------------------------------------------------------
# Tests: Class structure preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decorator_preserves_class():
    """Decorated class retains original attributes and name."""
    guard = Guard("in").use(AlwaysPassValidator())

    @guarded(input_config=InputGuardConfig(guard=guard))
    class MyExecutor(AgentExecutor):
        custom_attr = "preserved"

        async def execute(self, context, event_queue):
            pass

        async def cancel(self, context, event_queue):
            pass

        def custom_method(self):
            return 42

    assert MyExecutor.__name__ == "MyExecutor"
    assert MyExecutor.custom_attr == "preserved"

    inst = MyExecutor()
    assert inst.custom_method() == 42


@pytest.mark.asyncio
async def test_decorator_multiple_instances_independent():
    """Each instance gets its own _guarded_executor — no cross-instance state."""
    execute_counts = []
    guard = Guard("in").use(AlwaysPassValidator())

    @guarded(input_config=InputGuardConfig(guard=guard))
    class MyExecutor(AgentExecutor):
        async def execute(self, context, event_queue):
            execute_counts.append(id(self))

        async def cancel(self, context, event_queue):
            pass

    a = MyExecutor()
    b = MyExecutor()
    assert a._guarded_executor is not b._guarded_executor

    await a.execute(make_context(), event_queue())
    await b.execute(make_context(), event_queue())
    assert len(execute_counts) == 2
