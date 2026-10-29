"""Version-agnostic a2a object constructors for the test suite.

Mirrors ``src/aegis/a2a/_compat.py`` on the *test* side: lets the whole suite run
against both a2a-sdk 0.3 (Pydantic) and 1.x (Protobuf) without per-test version
branches. Tests import builders/readers from here instead of ``a2a.types`` directly.

Primitives that already live in the shipped shim (part text read/write, part
discrimination, enum constants, helpers) are re-exported rather than duplicated.

Note: tests run an executor outside the framework, so they must construct a real queue to
feed ``execute()`` and read events back. On a2a-sdk 1.x, constructing ``EventQueue()``
directly is deprecated, so the suite builds :class:`OwnedEventQueue` instead (via
``event_queue()``, drained via ``dequeue()``). See that class and
docs/a2a-v1-migration.md §9 for the reasoning.
"""

# Same dual-major situation as src/aegis/a2a/_compat.py: whichever SDK is installed,
# the other branch references unresolvable symbols. Only one branch runs.
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportRedeclaration=false

from __future__ import annotations

import asyncio
from typing import Any

from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import Artifact, Message, TaskArtifactUpdateEvent, TaskStatus, TaskStatusUpdateEvent

from aegis.a2a._compat import (
    _V03,
    ROLE_AGENT,
    ROLE_USER,
    TASK_STATE_COMPLETED,
    TASK_STATE_REJECTED,
    get_message_text,
    is_text_part,
    new_task,
)
from aegis.a2a._compat import make_text_part as text_part
from aegis.a2a._compat import part_text as get_text

__all__ = [
    "OwnedEventQueue",
    "ROLE_AGENT",
    "ROLE_USER",
    "TASK_STATE_COMPLETED",
    "TASK_STATE_REJECTED",
    "artifact_update_event",
    "data_part",
    "dequeue",
    "event_queue",
    "get_data",
    "get_message_text",
    "get_text",
    "is_data_part",
    "is_text_part",
    "new_task",
    "request_context",
    "status_update_event",
    "text_part",
    "user_message",
]

_DEFAULT_DATA = {"k": "v"}


if _V03:
    from a2a.types import DataPart, MessageSendParams, Part

    def data_part(payload: dict | None = None) -> Part:
        return Part(root=DataPart(data=payload if payload is not None else dict(_DEFAULT_DATA)))

    def is_data_part(part: Part) -> bool:
        return isinstance(part.root, DataPart)

    def get_data(part: Part) -> dict:
        return part.root.data

    def request_context(
        message: Message, task_id: str | None = None, context_id: str | None = None, **kw: Any
    ) -> RequestContext:
        return RequestContext(request=MessageSendParams(message=message), task_id=task_id, context_id=context_id, **kw)

    def status_update_event(
        state: Any,
        task_id: str = "task-1",
        context_id: str = "ctx-1",
        final: bool = True,
        message: Message | None = None,
    ) -> TaskStatusUpdateEvent:
        return TaskStatusUpdateEvent(
            task_id=task_id, context_id=context_id, final=final, status=TaskStatus(state=state, message=message)
        )

else:
    from a2a.server.context import ServerCallContext
    from a2a.types import Part, SendMessageRequest
    from google.protobuf.struct_pb2 import Value

    def data_part(payload: dict | None = None) -> Part:
        value = Value()
        value.struct_value.update(payload if payload is not None else dict(_DEFAULT_DATA))
        return Part(data=value)

    def is_data_part(part: Part) -> bool:
        return part.HasField("data")

    def get_data(part: Part) -> dict:
        return dict(part.data.struct_value)

    def request_context(
        message: Message, task_id: str | None = None, context_id: str | None = None, call_context: Any = None, **kw: Any
    ) -> RequestContext:
        return RequestContext(
            call_context=call_context or ServerCallContext(),
            request=SendMessageRequest(message=message),
            task_id=task_id,
            context_id=context_id,
            **kw,
        )

    def status_update_event(
        state: Any,
        task_id: str = "task-1",
        context_id: str = "ctx-1",
        final: bool = True,
        message: Message | None = None,
    ) -> TaskStatusUpdateEvent:
        # v1 dropped the `final` field; terminal-ness is implied by the state. Accept
        # and ignore `final` so call sites stay identical across majors.
        return TaskStatusUpdateEvent(
            task_id=task_id, context_id=context_id, status=TaskStatus(state=state, message=message)
        )


class OwnedEventQueue(EventQueue):
    """Concrete, test-owned producer queue to feed ``execute()``/``cancel()``.

    Tests run executors outside the framework, so they must build the queue the framework
    would otherwise supply. ``EventQueue`` is an abstract producer interface on 1.x;
    constructing it directly is deprecated (redirects to ``EventQueueLegacy``, which upstream
    says will also be removed), so this implements the sanctioned "write a custom subclass"
    path from the ``EventQueue`` docstring. It owns its buffer rather than borrowing a
    third-party queue's internals, and provides only what the suite actually exercises: the
    producer ``enqueue_event`` plus the ``close``/``is_closed`` that ``GuardedEventQueue``
    delegates to the wrapped queue, and ``dequeue_nowait`` to read events back. Works
    identically on both a2a-sdk majors (``EventQueue`` exists in each); see
    docs/a2a-v1-migration.md §9.
    """

    def __init__(self) -> None:
        self._events: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False

    async def enqueue_event(self, event: Any) -> None:
        self._events.put_nowait(event)

    async def close(self) -> None:
        self._closed = True

    def is_closed(self) -> bool:
        return self._closed

    def dequeue_nowait(self) -> Any:
        """Read the next buffered event; raises ``asyncio.QueueEmpty`` when empty."""
        return self._events.get_nowait()


def event_queue() -> OwnedEventQueue:
    """Construct a real, concrete producer queue for tests. See :class:`OwnedEventQueue`."""
    return OwnedEventQueue()


async def dequeue(eq: OwnedEventQueue) -> Any:
    """Dequeue one event without blocking from a queue built by :func:`event_queue`;
    raises ``asyncio.QueueEmpty`` when empty.

    The parameter is typed :class:`OwnedEventQueue`, not the abstract ``EventQueue``, because
    reading events back needs ``dequeue_nowait`` — a method the producer-only ``EventQueue``
    interface does not expose. Annotating the concrete type lets the type checker reject any
    other queue at the call site, instead of failing at runtime with an ``AttributeError``.
    """
    return eq.dequeue_nowait()


def user_message(parts: list, message_id: str = "msg-1") -> Message:
    return Message(message_id=message_id, parts=list(parts), role=ROLE_USER)


def artifact_update_event(
    parts: list,
    artifact_id: str = "art-1",
    task_id: str = "task-1",
    context_id: str = "ctx-1",
    name: str | None = None,
) -> TaskArtifactUpdateEvent:
    artifact = (
        Artifact(artifact_id=artifact_id, parts=list(parts))
        if name is None
        else Artifact(artifact_id=artifact_id, name=name, parts=list(parts))
    )
    return TaskArtifactUpdateEvent(artifact=artifact, task_id=task_id, context_id=context_id)
