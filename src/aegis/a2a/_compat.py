"""Dual-SDK compatibility shim for a2a-sdk 0.3 (Pydantic) and 1.x (Protobuf).

Aegis touches only a handful of version-sensitive a2a primitives — part
discrimination, text read/write, a couple of object clones, and four helper
functions/enums. This module is the *single* place that imports version-specific
symbols; every other file in ``aegis.a2a`` imports from here.

Version detection is EAFP (try the 0.3-only import): more robust than parsing a
version string, and it copes with pre-releases and forks. The critical invariant
is that aegis src must never do a top-level ``from a2a.types import TextPart`` —
that name is gone in 1.x and the import would fail at load time.

# TODO: dual-SDK shim — delete the _V03 branch once no consumer pins a2a-sdk <1.0
"""

# This module deliberately targets two incompatible a2a-sdk majors, so whichever
# one is installed, the *other* branch references symbols the type checker can't
# resolve (missing imports, renamed enum members, wrapper types that only exist on
# 0.3). Only the branch matching the installed SDK ever runs. Suppress the
# cross-version noise here — this is the one file where it is unavoidable.
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportPossiblyUnboundVariable=false, reportCallIssue=false

from __future__ import annotations

from typing import Any

from a2a.types import Part  # ``Part`` exists in both majors; only its shape differs.

__all__ = [
    "Part",
    "ROLE_AGENT",
    "ROLE_USER",
    "TASK_STATE_COMPLETED",
    "TASK_STATE_REJECTED",
    "copy_artifact_event",
    "copy_message_with_parts",
    "copy_text_part",
    "get_message_text",
    "is_text_part",
    "make_text_part",
    "new_agent_text_message",
    "new_task",
    "part_text",
]

try:
    # 0.3 wrapper types exist; in 1.x they are gone (Part is a flat proto).
    from a2a.types import TextPart

    _V03 = True
except ImportError:  # pragma: no cover - exercised by the 1.x CI leg
    _V03 = False


if _V03:
    from a2a.types import Role, TaskState
    from a2a.utils import get_message_text, new_agent_text_message
    from a2a.utils import new_task as _new_task

    TASK_STATE_REJECTED = TaskState.rejected
    TASK_STATE_COMPLETED = TaskState.completed

    ROLE_USER = Role.user
    ROLE_AGENT = Role.agent

    # def-wrapped so both branches expose new_task with the same (message) param
    # name — 0.3's helper names the param ``request``, which else the checker flags.
    def new_task(message: Any) -> Any:
        return _new_task(message)

    def is_text_part(part: Part) -> bool:
        return isinstance(part.root, TextPart)

    def part_text(part: Part) -> str:
        return part.root.text

    def make_text_part(text: str) -> Part:
        return Part(root=TextPart(text=text))

    def copy_text_part(part: Part, text: str) -> Part:
        # Precondition: ``part`` is a text part (callers guard with is_text_part; see
        # apply_view). Copy the wrapped part with only ``text`` replaced; preserves sibling
        # fields (metadata, and future non-text payloads on mixed parts). See §2 of the guide.
        return Part(root=part.root.model_copy(update={"text": text}))

    def copy_message_with_parts(message: Any, parts: list[Part]) -> Any:
        return message.model_copy(update={"parts": parts})

    def copy_artifact_event(event: Any, parts: list[Part]) -> Any:
        return event.model_copy(update={"artifact": event.artifact.model_copy(update={"parts": parts})})

else:  # a2a-sdk 1.x — flat Protobuf union
    from a2a.helpers import get_message_text as _get_message_text
    from a2a.helpers import new_task_from_user_message as _new_task
    from a2a.helpers import new_text_message
    from a2a.types import Role, TaskState

    TASK_STATE_REJECTED = TaskState.TASK_STATE_REJECTED
    TASK_STATE_COMPLETED = TaskState.TASK_STATE_COMPLETED

    ROLE_USER = Role.ROLE_USER
    ROLE_AGENT = Role.ROLE_AGENT

    # def-wrapped (not re-exported) so the type checker sees a concrete module-level
    # binding even though a2a.helpers is unresolvable when 0.3 is the installed SDK.
    def get_message_text(message: Any, delimiter: str = "\n") -> str:
        return _get_message_text(message, delimiter)

    def new_task(message: Any) -> Any:  # 0.3 new_task(message) equivalent
        return _new_task(message)

    def new_agent_text_message(text: str, context_id: str | None = None, task_id: str | None = None) -> Any:
        # new_text_message defaults role to ROLE_AGENT — matches the removed helper.
        return new_text_message(text, context_id=context_id, task_id=task_id)

    def is_text_part(part: Part) -> bool:
        return part.HasField("text")

    def part_text(part: Part) -> str:
        return part.text

    def make_text_part(text: str) -> Part:
        return Part(text=text)

    def copy_text_part(part: Part, text: str) -> Part:
        # Precondition: ``part`` is a text part (callers guard with is_text_part; see apply_view).
        # Protobuf has no model_copy: clone, then mutate the single field in place.
        # CopyFrom (not Part(text=...)) so sibling fields survive once mixed parts land.
        new = type(part)()
        new.CopyFrom(part)
        new.text = text
        return new

    def copy_message_with_parts(message: Any, parts: list[Part]) -> Any:
        new = type(message)()
        new.CopyFrom(message)
        del new.parts[:]
        new.parts.extend(parts)
        return new

    def copy_artifact_event(event: Any, parts: list[Part]) -> Any:
        new = type(event)()
        new.CopyFrom(event)
        del new.artifact.parts[:]
        new.artifact.parts.extend(parts)
        return new
