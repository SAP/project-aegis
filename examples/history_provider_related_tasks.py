"""Reference history provider that extracts prior turns from ``context.related_tasks``.

Copy-paste into your project as-is. Aegis does NOT ship this as importable API —
users own their history providers so aegis stays backend-agnostic.

Usage::

    from aegis.a2a import GuardedAgentExecutor, InputGuardConfig
    from my_project.history_provider_related_tasks import make_related_tasks_provider

    executor = GuardedAgentExecutor(
        executor=my_executor,
        input_config=InputGuardConfig(
            guard=my_guard,
            history_provider=make_related_tasks_provider(max_messages=5),
        ),
    )
"""

from __future__ import annotations

# Dual-SDK example: whichever a2a-sdk major is installed, the other branch's
# imports/role names are unresolvable to the type checker. Only the matching
# branch runs. Same rationale as aegis.a2a._compat.
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

import logging
from collections.abc import Awaitable, Callable

from a2a.server.agent_execution import RequestContext
from a2a.types import Role

try:
    from a2a.helpers import get_message_text  # a2a-sdk 1.x
    ROLE_USER, ROLE_AGENT = Role.ROLE_USER, Role.ROLE_AGENT
except ImportError:  # a2a-sdk 0.3.x
    from a2a.utils import get_message_text
    ROLE_USER, ROLE_AGENT = Role.user, Role.agent

from aegis.metadata import ConversationMessage

logger = logging.getLogger(__name__)


def make_related_tasks_provider(
    max_messages: int = 5,
) -> Callable[[RequestContext], Awaitable[list[ConversationMessage]]]:
    """Build a history provider that reads from ``context.related_tasks``.

    Returns an async callable suitable for ``InputGuardConfig.history_provider``.
    Chronological order (oldest first), truncated to the last ``max_messages``.
    """

    async def provider(context: RequestContext) -> list[ConversationMessage]:
        history: list[ConversationMessage] = []
        if not context.related_tasks:
            return history

        for task in context.related_tasks:
            task_history = getattr(task, "history", None)
            if not task_history:
                continue
            for msg in task_history:
                text = get_message_text(msg)
                if not text:
                    continue
                # Compare against the SDK's own role constants: 0.3 roles are string
                # enums, 1.x roles are protobuf ints — equality works for both.
                if msg.role == ROLE_USER:
                    history.append({"role": "user", "content": text})
                elif msg.role == ROLE_AGENT:
                    history.append({"role": "assistant", "content": text})
                else:
                    logger.warning(f"Unknown role '{msg.role}', skipping message")

        return history[-max_messages:] if len(history) > max_messages else history

    return provider
