from __future__ import annotations

from a2a.server.agent_execution import RequestContext
from a2a.types import Message, Part

from aegis.a2a._compat import copy_message_with_parts, get_message_text


class GuardedContext(RequestContext):
    """RequestContext subclass that transparently provides mutated parts.

    Parts are the single source of truth. When input_mutation_guard
    produces output_parts, we clone the Message with replaced parts.
    Both get_user_input() and message.parts read from the same
    guarded Message — impossible to get inconsistent results.
    """

    def __init__(
        self,
        context: RequestContext,
        mutated_parts: list[Part] | None = None,
    ) -> None:
        super().__init__(
            request=context._params,  # noqa: SLF001
            task_id=context.task_id,
            context_id=context.context_id,
            task=context.current_task,
            related_tasks=context.related_tasks,
            call_context=context.call_context,
        )

        self._original_context = context
        self._guarded_message: Message | None = None

        if mutated_parts and context.message:
            self._guarded_message = copy_message_with_parts(context.message, mutated_parts)

    @property
    def message(self) -> Message | None:
        if self._guarded_message is not None:
            return self._guarded_message
        return self._original_context.message

    def get_user_input(self, delimiter: str = "\n") -> str:
        """Read text from guarded message.parts.

        Overridden because the parent reads from ``self._params.message``
        which would bypass the guarded message.
        """
        msg = self.message
        if msg is None:
            return ""
        return get_message_text(msg, delimiter)

    @property
    def original_input(self) -> str | None:
        """Pre-mutation text."""
        return self._original_context.get_user_input()

    @property
    def original_message(self) -> Message | None:
        """Pre-mutation Message."""
        return self._original_context.message
