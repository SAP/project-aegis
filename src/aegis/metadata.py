"""Typed metadata passed to ``Guard.validate(metadata=...)``.

``GuardMetadata`` is a :func:`~dataclasses.dataclass` — discoverable, checkable,
and self-documenting. It carries the surrounding agent context a validator may
need but that is not part of the value under inspection.

Both standalone ``Guard`` users and executor-adapter users build and read the
same object; this module is the single source of truth for its shape.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

CONVERSATION_HISTORY = "conversation_history"
"""Name of the :attr:`GuardMetadata.conversation_history` field.

Retained as a stable identifier for the prior-turns context that validators
(e.g. ``ScopeCheck``) read. Populate it via the field:
``GuardMetadata(conversation_history=[...])``.
"""


class ConversationMessage(TypedDict):
    """One turn in a conversation history."""

    role: Literal["user", "assistant"]
    content: str


@dataclass
class GuardMetadata:
    """Typed metadata accompanying a validation call.

    Carries the surrounding agent context that a validator may need but that
    is not part of the value under inspection. Every field is optional — an
    empty ``GuardMetadata()`` is valid.

    Attributes:
        system: The agent's system prompt / instructions, when available.
        tools: Descriptors for the tools available to the agent. The element
            shape is provisional (``Mapping[str, Any]``) and will be replaced
            with the real tool descriptor type once it is settled.
        conversation_history: Prior turns of the current conversation,
            chronological (oldest first). Validators that use context read this
            field; validators that don't ignore it.
    """

    system: str | None = None
    tools: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    conversation_history: list[ConversationMessage] = field(default_factory=list)
