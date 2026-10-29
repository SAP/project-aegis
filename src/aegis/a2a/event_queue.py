from __future__ import annotations

import asyncio
import logging
from typing import Any

from a2a.server.events import Event, EventQueue
from a2a.types import Artifact, Part, Task, TaskArtifactUpdateEvent

from aegis.a2a._compat import copy_artifact_event, make_text_part
from aegis.a2a._utils import _maybe_await, apply_view, build_view, resolve_hook
from aegis.a2a.types import OutputGuardConfig
from aegis.guard import Guard
from aegis.mutation_guard import MutationGuard
from aegis.telemetry import start_span

logger = logging.getLogger("aegis.a2a")


class GuardedEventQueue:
    """Proxy around EventQueue that applies output guards on artifact events.

    Works exclusively with parts — passes the artifact's parts list
    to guards, gets mutated parts back, replaces in the event. No
    per-TextPart iteration at this level; that's the validators' job.
    """

    def __init__(
        self,
        event_queue: EventQueue,
        output_config: OutputGuardConfig | None = None,
        holdback: asyncio.Event | None = None,
        stream_lifecycle_events: bool = False,
    ) -> None:
        self._event_queue = event_queue
        self._output_config = output_config
        self._holdback = holdback
        self._stream_lifecycle_events = stream_lifecycle_events
        self._buffer: list[Event] = []
        self._forwarded_task: Task | None = None

    async def enqueue_event(self, event: Event) -> None:
        """Intercept events to apply output guards on artifacts.

        When a ``holdback`` event is configured and not yet set (speculative
        mode, guard still pending), events are buffered instead of forwarded —
        except non-content lifecycle events when ``stream_lifecycle_events`` is
        enabled, which forward immediately.
        """
        if self._holdback is not None and not self._holdback.is_set():
            if self._stream_lifecycle_events and not self._is_content_event(event):
                await self._forward(event)
                return
            self._buffer.append(event)
            return

        await self._forward(event)

    async def _forward(self, event: Event) -> None:
        """Apply output guards on artifacts, then enqueue to the real queue."""
        # Remember the first Task we actually forward so the reject path can
        # reuse it instead of minting a second one (see forwarded_task).
        if isinstance(event, Task) and self._forwarded_task is None:
            self._forwarded_task = event
        if isinstance(event, TaskArtifactUpdateEvent) and event.artifact.parts:
            guarded = await self._guard_artifact_event(event)
            if guarded is None:
                return  # suppressed by on_fail hook
            await self._event_queue.enqueue_event(guarded)
            return
        await self._event_queue.enqueue_event(event)

    @staticmethod
    def _is_content_event(event: Event) -> bool:
        """Whether an event carries user/model content (vs. lifecycle).

        Only ``TaskArtifactUpdateEvent`` carries the agent's answer. Task
        creation and status updates are lifecycle events that may be streamed
        early under ``stream_lifecycle_events`` without leaking content.
        """
        return isinstance(event, TaskArtifactUpdateEvent)

    async def flush(self) -> None:
        """Release buffered events in order, then switch to direct forwarding.

        Called after the guard passes. Correctness hinges on draining *before*
        disabling buffering (§9a of the architecture plan): if we flipped to
        direct-forwarding first, a still-running agent's new events could
        overtake the older buffered ones and be delivered out of order.

        This method drains in a loop. While it drains, ``_holdback`` is still
        unset, so any event the agent emits during a ``_forward`` await lands in
        the buffer and is picked up by the next iteration — never forwarded
        ahead of an earlier event. Only once the buffer is fully empty (and,
        being single-threaded, with no await between the emptiness check and the
        release) is ``_holdback`` set so subsequent events forward directly.

        Idempotent-safe to call once per request. The holdback is monotonic:
        once released it never re-holds. The controller calls exactly one of
        ``flush()`` / ``discard()`` per request (guard-pass vs. guard-fail),
        never both and never concurrently.
        """
        while self._buffer:
            batch = self._buffer
            self._buffer = []
            for event in batch:
                await self._forward(event)
        # Buffer empty with no intervening await → safe to release the gate.
        if self._holdback is not None:
            self._holdback.set()

    def discard(self) -> None:
        """Discard all buffered events without processing.

        Called when the input guard fails and the agent is cancelled — nothing
        buffered is ever forwarded to the user.
        """
        self._buffer.clear()

    @property
    def forwarded_task(self) -> Task | None:
        """The first ``Task`` event already forwarded to the real queue, if any.

        Under ``stream_lifecycle_events=True`` the agent's own ``Task`` creation
        event is forwarded before the guard's verdict is known. On a failing
        verdict the controller must reject against *that* task rather than mint a
        fresh one via ``new_task()``, or the client would see two Task events on
        the stream. Returns ``None`` when nothing was forwarded (the strict
        default buffers the Task, so it is discarded, not forwarded).
        """
        return self._forwarded_task

    async def _guard_artifact_event(self, event: TaskArtifactUpdateEvent) -> TaskArtifactUpdateEvent | None:
        """Apply the output pipeline to artifact parts.

        Default order (``mutation_first=False``): ``Guard → MutationGuard``.
        When ``mutation_first=True``: ``MutationGuard → Guard``.

        Returns the (possibly modified) event, or ``None`` if suppressed.
        """
        if self._output_config is None:
            return event

        if not await self._should_guard_artifact(event.artifact):
            return event

        cfg = self._output_config
        parts = list(event.artifact.parts)

        if cfg.mutation_first:
            if cfg.mutation_guard:
                parts = await self._run_output_mutation_stage(cfg.mutation_guard, parts)
            if cfg.guard:
                parts, suppressed = await self._run_output_guard_stage(cfg.guard, cfg.on_fail, parts)
                if suppressed:
                    return None
        else:
            if cfg.guard:
                parts, suppressed = await self._run_output_guard_stage(cfg.guard, cfg.on_fail, parts)
                if suppressed:
                    return None
            if cfg.mutation_guard:
                parts = await self._run_output_mutation_stage(cfg.mutation_guard, parts)

        return copy_artifact_event(event, parts)

    async def _should_guard_artifact(self, artifact: Artifact) -> bool:
        """Evaluate the artifact filter predicate.

        Returns True if the artifact should be guarded, False to skip.
        Handles both sync and async callables.
        """
        cfg = self._output_config
        if cfg is None or cfg.artifact_filter is None:
            return True  # no filter = guard everything (default)

        return await _maybe_await(cfg.artifact_filter(artifact))

    async def _run_output_guard_stage(self, guard: Guard, on_fail: Any, parts: list[Part]) -> tuple[list[Part], bool]:
        """Run the output detection guard stage.

        Returns ``(parts, suppressed)`` where ``suppressed=True`` means the
        caller should return ``None`` (suppress the event).

        - ``on_fail=False`` → continue (explicit opt-in to allow through)
        - ``on_fail=str`` → replace artifact parts with the message string
        - ``on_fail=None/True`` → suppress the event
        """
        with start_span("guarded.output_guard"):
            # ValidationError from on_fail=EXCEPTION propagates up
            result = await guard.validate(build_view(parts))

            if result.has_failures and on_fail is not None:
                resolved = await resolve_hook(on_fail, result.failures)
                if resolved is False:
                    pass  # explicit opt-in to continue
                elif isinstance(resolved, str):
                    parts = [make_text_part(resolved)]
                else:
                    return parts, True  # None or True → suppress
        return parts, False

    async def _run_output_mutation_stage(self, mutation_guard: MutationGuard, parts: list[Part]) -> list[Part]:
        """Run the output mutation stage. Returns updated parts (identical count
        and order); non-text parts pass through untouched."""
        with start_span("guarded.output_mutation"):
            result = await mutation_guard.validate(build_view(parts))
            # A2A only ever feeds a ContentView (build_view), so output is never a str.
            # Fail loud rather than silently drop a security mutation.
            if isinstance(result.output, str):
                raise TypeError(f"MutationGuard returned {type(result.output).__name__}, expected ContentView")
            if result.was_mutated and result.output is not None:
                return apply_view(parts, result.output)
            return parts

    def __getattr__(self, name: str) -> Any:
        try:
            eq = object.__getattribute__(self, "_event_queue")
        except AttributeError:
            raise AttributeError(name) from None
        return getattr(eq, name)
