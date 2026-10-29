from __future__ import annotations

from a2a.types import Message

from _a2a_compat import (
    ROLE_USER,
    data_part,
    get_text,
    is_data_part,
    request_context,
    text_part,
)
from aegis.a2a.context import GuardedContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_message(text: str, **kwargs) -> Message:
    return Message(
        message_id="msg-1",
        parts=[text_part(text)],
        role=ROLE_USER,
        **kwargs,
    )


def make_context(message: Message):
    return request_context(message, task_id="task-1", context_id="ctx-1")


def make_mutated_parts(text: str) -> list:
    return [text_part(text)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mutated_input():
    ctx = make_context(make_message("original"))
    guarded = GuardedContext(ctx, mutated_parts=make_mutated_parts("mutated"))
    assert guarded.get_user_input() == "mutated"


def test_mutated_parts():
    ctx = make_context(make_message("original"))
    guarded = GuardedContext(ctx, mutated_parts=make_mutated_parts("mutated"))
    parts = guarded.message.parts
    assert len(parts) == 1
    assert get_text(parts[0]) == "mutated"


def test_mutated_parts_consistency():
    ctx = make_context(make_message("original"))
    guarded = GuardedContext(ctx, mutated_parts=make_mutated_parts("mutated"))
    text = guarded.get_user_input()
    parts_text = get_text(guarded.message.parts[0])
    assert text == parts_text


def test_no_mutation_delegates():
    ctx = make_context(make_message("original"))
    guarded = GuardedContext(ctx)
    assert guarded.get_user_input() == "original"
    assert guarded.message is ctx.message


def test_original_input_always_available():
    ctx = make_context(make_message("original"))
    guarded = GuardedContext(ctx, mutated_parts=make_mutated_parts("mutated"))
    assert guarded.original_input == "original"
    assert guarded.get_user_input() == "mutated"


def test_original_message_always_available():
    original_msg = make_message("original")
    ctx = make_context(original_msg)
    guarded = GuardedContext(ctx, mutated_parts=make_mutated_parts("mutated"))
    # Identity, not a copy: GuardedContext exposes the context's own message object.
    # (Compared to ctx.message, not the external original_msg — on protobuf SDKs the
    # request copies the message in, so the external ref is never object-identical.)
    assert guarded.original_message is ctx.message
    assert get_text(guarded.original_message.parts[0]) == "original"


def test_message_clone_preserves_other_fields():
    msg = make_message("original", metadata={"k": "v"})
    ctx = make_context(msg)
    guarded = GuardedContext(ctx, mutated_parts=make_mutated_parts("mutated"))
    cloned = guarded.message
    assert cloned.metadata == {"k": "v"}
    assert cloned.role == ROLE_USER
    assert cloned.message_id == "msg-1"
    # Parts are replaced, but everything else preserved
    assert get_text(cloned.parts[0]) == "mutated"


def test_non_text_parts_in_mutation():
    # v1 has no FilePart/file oneof; both fixtures are non-text parts and only
    # their untouched pass-through is asserted here.
    fpart = data_part({"file": "AAAA"})
    dpart = data_part({"key": "value"})
    mutated = [text_part("new"), fpart, dpart]

    ctx = make_context(make_message("original"))
    guarded = GuardedContext(ctx, mutated_parts=mutated)
    parts = guarded.message.parts
    assert len(parts) == 3
    assert get_text(parts[0]) == "new"
    assert is_data_part(parts[1])
    assert is_data_part(parts[2])


def test_attribute_delegation():
    ctx = make_context(make_message("original"))
    guarded = GuardedContext(ctx)
    assert guarded.task_id == "task-1"
    assert guarded.context_id == "ctx-1"
    assert guarded.current_task is None
