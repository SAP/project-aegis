"""Smoke test for aegis.metadata — guards against accidental rename/refactor."""

from __future__ import annotations

import dataclasses
from typing import get_type_hints

from aegis.metadata import (
    CONVERSATION_HISTORY,
    ConversationMessage,
    GuardMetadata,
)


def test_conversation_history_key_is_stable():
    """The stable identifier must not change silently — validators and users rely on it."""
    assert CONVERSATION_HISTORY == "conversation_history"


def test_guard_metadata_is_a_dataclass():
    """GuardMetadata is a dataclass (typed, not a bare dict)."""
    assert dataclasses.is_dataclass(GuardMetadata)


def test_guard_metadata_declares_expected_fields():
    """system, tools, and conversation_history are the agreed fields."""
    field_names = {f.name for f in dataclasses.fields(GuardMetadata)}
    assert field_names == {"system", "tools", "conversation_history"}


def test_guard_metadata_all_fields_optional():
    """Every field has a default — an empty GuardMetadata() is valid."""
    md = GuardMetadata()
    assert md.system is None
    assert tuple(md.tools) == ()
    assert md.conversation_history == []


def test_conversation_message_has_role_and_content():
    """Message schema is {role, content} — both keys must exist."""
    hints = get_type_hints(ConversationMessage)
    assert "role" in hints
    assert "content" in hints


def test_valid_metadata_constructs():
    """Sanity check: a realistic GuardMetadata is accepted at runtime."""
    md = GuardMetadata(
        system="You are a helpful assistant.",
        tools=({"name": "search"},),
        conversation_history=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    )
    assert md.system == "You are a helpful assistant."
    assert len(md.conversation_history) == 2
    assert md.tools[0]["name"] == "search"
