from __future__ import annotations

import asyncio

import pytest
from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent

from _a2a_compat import (
    TASK_STATE_COMPLETED,
    OwnedEventQueue,
    artifact_update_event,
    data_part,
    dequeue,
    event_queue,
    get_data,
    get_text,
    is_data_part,
    status_update_event,
    text_part,
)
from aegis import ItemEdit
from aegis.a2a.event_queue import GuardedEventQueue
from aegis.a2a.types import OutputGuardConfig
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


def make_artifact_event(text: str) -> TaskArtifactUpdateEvent:
    return artifact_update_event([text_part(text)])


def make_status_event() -> TaskStatusUpdateEvent:
    return status_update_event(TASK_STATE_COMPLETED, task_id="task-1", context_id="ctx-1")


class AlwaysPassValidator(BaseValidator):
    async def validate(self, view, metadata=None):
        view.render()
        return PassedResult()


class AlwaysFailValidator(BaseValidator):
    async def validate(self, view, metadata=None):
        view.render()
        return FailedResult(error_message="output blocked")


class UpperCaseMutator(BaseMutationValidator):
    async def validate(self, view, metadata=None):
        if any(p.value != p.value.upper() for p in view.items):
            return FailedResult(error_message="not uppercase")
        return PassedResult()

    async def mutate(self, view, metadata=None, validation_failure=None):
        return [ItemEdit(p.id, p.value.upper()) for p in view.items if p.value != p.value.upper()]


async def _drain_event(eq: OwnedEventQueue):
    """Get the next event from the queue."""
    return await dequeue(eq)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_event_guarded():
    real_eq = event_queue()
    mutation_guard = MutationGuard("out-mutate").use(UpperCaseMutator())
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(mutation_guard=mutation_guard),
    )

    event = make_artifact_event("hello")
    await guarded_eq.enqueue_event(event)

    result = await _drain_event(real_eq)
    assert isinstance(result, TaskArtifactUpdateEvent)
    assert get_text(result.artifact.parts[0]) == "HELLO"


@pytest.mark.asyncio
async def test_non_artifact_event_passthrough():
    real_eq = event_queue()
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.LOG)
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard),
    )

    event = make_status_event()
    await guarded_eq.enqueue_event(event)

    result = await _drain_event(real_eq)
    assert isinstance(result, TaskStatusUpdateEvent)
    assert result.status.state == TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_mixed_parts_in_artifact_preserves_non_text():
    """Value-based mutation preserves non-text parts.

    The text is flattened to a single value for the validators and the mutated
    value is folded back into the first text slot; non-text parts (e.g.
    ``DataPart``) are preserved in place.
    """
    real_eq = event_queue()
    mutation_guard = MutationGuard("out-mutate").use(UpperCaseMutator())
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(mutation_guard=mutation_guard),
    )

    event = artifact_update_event([text_part("hello"), data_part({"key": "value"})])
    await guarded_eq.enqueue_event(event)

    result = await _drain_event(real_eq)
    assert len(result.artifact.parts) == 2
    assert get_text(result.artifact.parts[0]) == "HELLO"
    assert is_data_part(result.artifact.parts[1])
    assert get_data(result.artifact.parts[1]) == {"key": "value"}


@pytest.mark.asyncio
async def test_no_guards_passthrough():
    real_eq = event_queue()
    guarded_eq = GuardedEventQueue(real_eq)

    event = make_artifact_event("hello")
    await guarded_eq.enqueue_event(event)

    result = await _drain_event(real_eq)
    assert get_text(result.artifact.parts[0]) == "hello"


@pytest.mark.asyncio
async def test_guard_then_mutation_default_order():
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

    real_eq = event_queue()
    guard = Guard("out").use(TrackingDetector())
    mutation_guard = MutationGuard("out-mutate").use(TrackingMutator())
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, mutation_guard=mutation_guard),
    )

    await guarded_eq.enqueue_event(make_artifact_event("hi"))
    assert call_order == ["guard", "mutation_validate", "mutation_mutate"]


@pytest.mark.asyncio
async def test_mutation_first_reverses_order():
    """mutation_first=True: MutationGuard runs before Guard."""
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

    real_eq = event_queue()
    guard = Guard("out").use(TrackingDetector())
    mutation_guard = MutationGuard("out-mutate").use(TrackingMutator())
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, mutation_guard=mutation_guard, mutation_first=True),
    )

    await guarded_eq.enqueue_event(make_artifact_event("hi"))
    assert call_order == ["mutation_validate", "mutation_mutate", "guard"]


@pytest.mark.asyncio
async def test_mutation_first_guard_sees_mutated_value():
    """mutation_first=True: guard receives the already-mutated value."""
    seen_values = []

    class CapturingDetector(BaseValidator):
        async def validate(self, view, metadata=None):
            value = view.render()
            seen_values.append(value)
            return PassedResult()

    real_eq = event_queue()
    guard = Guard("out").use(CapturingDetector())
    mutation_guard = MutationGuard("out-mutate").use(UpperCaseMutator())
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, mutation_guard=mutation_guard, mutation_first=True),
    )

    await guarded_eq.enqueue_event(make_artifact_event("hello"))
    assert seen_values[0] == "HELLO"


@pytest.mark.asyncio
async def test_on_output_fail_called():
    callback_failures = []

    def sync_callback(failures):
        callback_failures.extend(failures)
        return False  # explicit continue

    real_eq = event_queue()
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.LOG)
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, on_fail=sync_callback),
    )

    await guarded_eq.enqueue_event(make_artifact_event("hi"))
    assert len(callback_failures) == 1
    assert callback_failures[0].error_message == "output blocked"

    # Event still emitted (hook returned False)
    result = await _drain_event(real_eq)
    assert get_text(result.artifact.parts[0]) == "hi"


@pytest.mark.asyncio
async def test_on_output_fail_suppress_artifact():
    """Hook returns True -> artifact is suppressed (not enqueued)."""

    def suppress_hook(failures):
        return True

    real_eq = event_queue()
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, on_fail=suppress_hook),
    )

    await guarded_eq.enqueue_event(make_artifact_event("bad content"))

    # Queue should be empty — artifact was suppressed
    with pytest.raises(asyncio.QueueEmpty):
        await dequeue(real_eq)


@pytest.mark.asyncio
async def test_on_output_fail_replace_with_message():
    """Hook returns a string -> artifact text replaced with that message."""

    def replace_hook(failures):
        return "Content has been redacted."

    real_eq = event_queue()
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, on_fail=replace_hook),
    )

    await guarded_eq.enqueue_event(make_artifact_event("bad content"))

    result = await _drain_event(real_eq)
    assert isinstance(result, TaskArtifactUpdateEvent)
    assert len(result.artifact.parts) == 1
    assert get_text(result.artifact.parts[0]) == "Content has been redacted."


@pytest.mark.asyncio
async def test_on_output_fail_none_suppresses():
    """Hook returns None (implicit) -> artifact suppressed (fail-closed default)."""

    def noop_hook(failures):
        pass  # implicit None return

    real_eq = event_queue()
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, on_fail=noop_hook),
    )

    await guarded_eq.enqueue_event(make_artifact_event("hello"))

    # Artifact suppressed — queue is empty
    with pytest.raises(asyncio.QueueEmpty):
        await dequeue(real_eq)


@pytest.mark.asyncio
async def test_on_output_fail_false_continues():
    """Hook returns False -> artifact emitted as-is (explicit opt-in)."""

    def allow_hook(failures):
        return False

    real_eq = event_queue()
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, on_fail=allow_hook),
    )

    await guarded_eq.enqueue_event(make_artifact_event("hello"))

    result = await _drain_event(real_eq)
    assert get_text(result.artifact.parts[0]) == "hello"


@pytest.mark.asyncio
async def test_on_output_fail_static_true_suppresses():
    """on_fail=True (static) -> artifact suppressed."""
    real_eq = event_queue()
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, on_fail=True),
    )

    await guarded_eq.enqueue_event(make_artifact_event("hello"))

    with pytest.raises(asyncio.QueueEmpty):
        await dequeue(real_eq)


@pytest.mark.asyncio
async def test_on_output_fail_static_string_replaces():
    """on_fail='msg' (static) -> artifact text replaced."""
    real_eq = event_queue()
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, on_fail="Redacted."),
    )

    await guarded_eq.enqueue_event(make_artifact_event("hello"))

    result = await _drain_event(real_eq)
    assert isinstance(result, TaskArtifactUpdateEvent)
    assert get_text(result.artifact.parts[0]) == "Redacted."


@pytest.mark.asyncio
async def test_on_output_fail_static_false_continues():
    """on_fail=False (static) -> artifact emitted as-is."""
    real_eq = event_queue()
    guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
    guarded_eq = GuardedEventQueue(
        real_eq,
        output_config=OutputGuardConfig(guard=guard, on_fail=False),
    )

    await guarded_eq.enqueue_event(make_artifact_event("hello"))

    result = await _drain_event(real_eq)
    assert get_text(result.artifact.parts[0]) == "hello"


@pytest.mark.asyncio
async def test_delegates_other_methods():
    real_eq = event_queue()
    guarded_eq = GuardedEventQueue(real_eq)

    assert guarded_eq.is_closed() is False
    await guarded_eq.close()
    assert guarded_eq.is_closed() is True


# ---------------------------------------------------------------------------
# Artifact filter tests
# ---------------------------------------------------------------------------


class TestArtifactFilter:
    """Test artifact_filter predicate on OutputGuardConfig."""

    @pytest.mark.asyncio
    async def test_no_filter_guards_all(self):
        """Default (None) guards every artifact — mutation pipeline runs."""
        real_eq = event_queue()
        mutation_guard = MutationGuard("out-mutate").use(UpperCaseMutator())
        guarded_eq = GuardedEventQueue(
            real_eq,
            output_config=OutputGuardConfig(mutation_guard=mutation_guard),
        )

        await guarded_eq.enqueue_event(make_artifact_event("hello"))

        result = await _drain_event(real_eq)
        assert get_text(result.artifact.parts[0]) == "HELLO"

    @pytest.mark.asyncio
    async def test_filter_true_guards_artifact(self):
        """Predicate returning True → guards run."""
        real_eq = event_queue()
        mutation_guard = MutationGuard("out-mutate").use(UpperCaseMutator())
        guarded_eq = GuardedEventQueue(
            real_eq,
            output_config=OutputGuardConfig(
                mutation_guard=mutation_guard,
                artifact_filter=lambda a: True,
            ),
        )

        await guarded_eq.enqueue_event(make_artifact_event("hello"))

        result = await _drain_event(real_eq)
        assert get_text(result.artifact.parts[0]) == "HELLO"

    @pytest.mark.asyncio
    async def test_filter_false_skips_guards(self):
        """Predicate returning False → artifact emitted unmodified."""
        real_eq = event_queue()
        guard = Guard("out").use(AlwaysFailValidator(), on_fail=OnFailAction.NOOP)
        mutation_guard = MutationGuard("out-mutate").use(UpperCaseMutator())
        guarded_eq = GuardedEventQueue(
            real_eq,
            output_config=OutputGuardConfig(
                guard=guard,
                mutation_guard=mutation_guard,
                on_fail=True,  # would suppress if guard ran
                artifact_filter=lambda a: False,
            ),
        )

        await guarded_eq.enqueue_event(make_artifact_event("hello"))

        # Artifact passes through unmodified — neither guard nor mutation ran
        result = await _drain_event(real_eq)
        assert get_text(result.artifact.parts[0]) == "hello"

    @pytest.mark.asyncio
    async def test_filter_by_name(self):
        """Filter by artifact name — guarded artifact is mutated, skipped one is not."""
        real_eq = event_queue()
        mutation_guard = MutationGuard("out-mutate").use(UpperCaseMutator())
        guarded_eq = GuardedEventQueue(
            real_eq,
            output_config=OutputGuardConfig(
                mutation_guard=mutation_guard,
                artifact_filter=lambda a: a.name == "main_response",
            ),
        )

        # Artifact with matching name → guarded
        guarded_event = artifact_update_event([text_part("hello")], artifact_id="art-1", name="main_response")
        await guarded_eq.enqueue_event(guarded_event)

        result = await _drain_event(real_eq)
        assert result.artifact.artifact_id == "art-1"
        assert get_text(result.artifact.parts[0]) == "HELLO"

        # Artifact with non-matching name → skipped
        skipped_event = artifact_update_event([text_part("some source")], artifact_id="art-2", name="sources")
        await guarded_eq.enqueue_event(skipped_event)

        result = await _drain_event(real_eq)
        assert result.artifact.artifact_id == "art-2"
        assert get_text(result.artifact.parts[0]) == "some source"

    @pytest.mark.asyncio
    async def test_async_filter(self):
        """Async predicate is awaited correctly."""

        async def my_filter(artifact):
            return artifact.name != "skip_me"

        real_eq = event_queue()
        mutation_guard = MutationGuard("out-mutate").use(UpperCaseMutator())
        guarded_eq = GuardedEventQueue(
            real_eq,
            output_config=OutputGuardConfig(
                mutation_guard=mutation_guard,
                artifact_filter=my_filter,
            ),
        )

        # Artifact that passes the async filter → guarded
        pass_event = artifact_update_event([text_part("hello")], artifact_id="art-1", name="response")
        await guarded_eq.enqueue_event(pass_event)

        result = await _drain_event(real_eq)
        assert get_text(result.artifact.parts[0]) == "HELLO"

        # Artifact that fails the async filter → skipped
        skip_event = artifact_update_event([text_part("should stay lowercase")], artifact_id="art-2", name="skip_me")
        await guarded_eq.enqueue_event(skip_event)

        result = await _drain_event(real_eq)
        assert get_text(result.artifact.parts[0]) == "should stay lowercase"


# ---------------------------------------------------------------------------
# Holdback buffering tests (speculative holdback)
# ---------------------------------------------------------------------------


class TestHoldback:
    """Holdback buffer: buffer while held, flush in order, discard on fail."""

    @pytest.mark.asyncio
    async def test_events_buffered_while_held(self):
        """With holdback unset, events are buffered, not forwarded."""
        real_eq = event_queue()
        holdback = asyncio.Event()
        guarded_eq = GuardedEventQueue(real_eq, holdback=holdback)

        await guarded_eq.enqueue_event(make_artifact_event("one"))
        await guarded_eq.enqueue_event(make_status_event())

        # Nothing forwarded yet
        with pytest.raises(asyncio.QueueEmpty):
            await dequeue(real_eq)

    @pytest.mark.asyncio
    async def test_flush_releases_in_order(self):
        """flush() forwards buffered events in original order."""
        real_eq = event_queue()
        holdback = asyncio.Event()
        guarded_eq = GuardedEventQueue(real_eq, holdback=holdback)

        await guarded_eq.enqueue_event(make_artifact_event("first"))
        await guarded_eq.enqueue_event(make_artifact_event("second"))
        await guarded_eq.enqueue_event(make_artifact_event("third"))

        await guarded_eq.flush()

        r1 = await _drain_event(real_eq)
        r2 = await _drain_event(real_eq)
        r3 = await _drain_event(real_eq)
        assert get_text(r1.artifact.parts[0]) == "first"
        assert get_text(r2.artifact.parts[0]) == "second"
        assert get_text(r3.artifact.parts[0]) == "third"

    @pytest.mark.asyncio
    async def test_flush_applies_output_guards(self):
        """Buffered artifacts pass through output guards on flush."""
        real_eq = event_queue()
        holdback = asyncio.Event()
        mutation_guard = MutationGuard("out-mutate").use(UpperCaseMutator())
        guarded_eq = GuardedEventQueue(
            real_eq,
            output_config=OutputGuardConfig(mutation_guard=mutation_guard),
            holdback=holdback,
        )

        await guarded_eq.enqueue_event(make_artifact_event("hello"))
        await guarded_eq.flush()

        result = await _drain_event(real_eq)
        assert get_text(result.artifact.parts[0]) == "HELLO"

    @pytest.mark.asyncio
    async def test_flush_sets_holdback_then_forwards_directly(self):
        """After flush, holdback is set and later events forward immediately."""
        real_eq = event_queue()
        holdback = asyncio.Event()
        guarded_eq = GuardedEventQueue(real_eq, holdback=holdback)

        await guarded_eq.enqueue_event(make_artifact_event("buffered"))
        await guarded_eq.flush()
        assert holdback.is_set()

        # Direct forward, no re-buffering
        await guarded_eq.enqueue_event(make_artifact_event("direct"))

        r1 = await _drain_event(real_eq)
        r2 = await _drain_event(real_eq)
        assert get_text(r1.artifact.parts[0]) == "buffered"
        assert get_text(r2.artifact.parts[0]) == "direct"

    @pytest.mark.asyncio
    async def test_flush_preserves_order_with_late_arrivals(self):
        """Events emitted mid-flush queue behind buffered ones (§9a).

        A guard whose output guard awaits gives a concurrently-running agent a
        chance to emit during the drain. Those late events must not overtake
        the already-buffered ones.
        """
        real_eq = event_queue()
        holdback = asyncio.Event()

        class SlowMutator(BaseMutationValidator):
            async def validate(self, view, metadata=None):
                return FailedResult(error_message="fix")

            async def mutate(self, view, metadata=None, validation_failure=None):
                # Yield control so a "late" event can be enqueued mid-flush.
                await asyncio.sleep(0)
                return [ItemEdit(p.id, p.value) for p in view.items]

        guarded_eq = GuardedEventQueue(
            real_eq,
            output_config=OutputGuardConfig(mutation_guard=MutationGuard("m").use(SlowMutator())),
            holdback=holdback,
        )

        await guarded_eq.enqueue_event(make_artifact_event("early-1"))
        await guarded_eq.enqueue_event(make_artifact_event("early-2"))

        async def late_emitter():
            # Enqueue while flush is mid-drain (holdback not yet set).
            await asyncio.sleep(0)
            await guarded_eq.enqueue_event(make_artifact_event("late"))

        await asyncio.gather(guarded_eq.flush(), late_emitter())

        order = []
        while True:
            try:
                ev = await dequeue(real_eq)
            except asyncio.QueueEmpty:
                break
            order.append(get_text(ev.artifact.parts[0]))

        assert order == ["early-1", "early-2", "late"]

    @pytest.mark.asyncio
    async def test_discard_drops_buffer(self):
        """discard() clears the buffer; nothing is ever forwarded."""
        real_eq = event_queue()
        holdback = asyncio.Event()
        guarded_eq = GuardedEventQueue(real_eq, holdback=holdback)

        await guarded_eq.enqueue_event(make_artifact_event("secret"))
        guarded_eq.discard()
        await guarded_eq.flush()  # nothing left to flush

        with pytest.raises(asyncio.QueueEmpty):
            await dequeue(real_eq)

    @pytest.mark.asyncio
    async def test_lifecycle_events_stream_through_when_enabled(self):
        """stream_lifecycle_events=True forwards status events but buffers artifacts."""
        real_eq = event_queue()
        holdback = asyncio.Event()
        guarded_eq = GuardedEventQueue(real_eq, holdback=holdback, stream_lifecycle_events=True)

        await guarded_eq.enqueue_event(make_status_event())  # forwarded now
        await guarded_eq.enqueue_event(make_artifact_event("answer"))  # buffered

        # Status event came through immediately
        status = await _drain_event(real_eq)
        assert isinstance(status, TaskStatusUpdateEvent)

        # Artifact is still held
        with pytest.raises(asyncio.QueueEmpty):
            await dequeue(real_eq)

        # Flush releases the artifact
        await guarded_eq.flush()
        art = await _drain_event(real_eq)
        assert get_text(art.artifact.parts[0]) == "answer"

    @pytest.mark.asyncio
    async def test_lifecycle_events_buffered_by_default(self):
        """Strict default: even lifecycle events are held until flush."""
        real_eq = event_queue()
        holdback = asyncio.Event()
        guarded_eq = GuardedEventQueue(real_eq, holdback=holdback)

        await guarded_eq.enqueue_event(make_status_event())

        with pytest.raises(asyncio.QueueEmpty):
            await dequeue(real_eq)
