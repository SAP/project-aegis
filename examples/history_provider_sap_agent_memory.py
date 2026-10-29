"""Reference history provider that reads from SAP Agent Memory Service.

Copy-paste into your project as-is. Aegis does NOT ship this — the SAP SDK is
not an aegis dependency. Install ``sap-cloud-sdk`` yourself and adapt.

Usage::

    from aegis.a2a import GuardedAgentExecutor, InputGuardConfig
    from my_project.history_provider_sap_agent_memory import make_sap_agent_memory_provider

    executor = GuardedAgentExecutor(
        executor=my_executor,
        input_config=InputGuardConfig(
            guard=my_guard,
            history_provider=make_sap_agent_memory_provider(
                agent_id="data-assistant",
                invoker_id_key="user_id",
                max_messages=5,
            ),
        ),
    )
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from a2a.server.agent_execution import RequestContext

from aegis.metadata import ConversationMessage

logger = logging.getLogger(__name__)


def make_sap_agent_memory_provider(
    agent_id: str,
    invoker_id_key: str = "user_id",
    max_messages: int = 5,
) -> Callable[[RequestContext], Awaitable[list[ConversationMessage]]]:
    """Build a history provider backed by SAP Agent Memory Service.

    The client is created once at provider-construction time and shared across
    every invocation (same connection pool, same auth). ``list_messages`` is a
    synchronous SDK call, so we hand it off to a worker thread via
    ``asyncio.to_thread`` to avoid stalling the event loop.

    Returns an async callable suitable for ``InputGuardConfig.history_provider``.
    Returns an empty list (no exception) when history cannot be retrieved for
    the current request (missing invoker_id, missing context_id, SDK not
    installed, service unreachable, etc.) — aegis will proceed with no history.
    """
    try:
        from sap_cloud_sdk.agent_memory import create_client
    except ImportError:
        logger.warning(
            "sap_cloud_sdk not installed — SAP Agent Memory provider will always "
            "return empty history. Install with: pip install sap-cloud-sdk"
        )
        client = None
    else:
        try:
            client = create_client()
        except Exception as e:
            logger.warning(f"Failed to create Agent Memory client: {e}")
            client = None

    def _map_role(raw: object) -> str | None:
        role_str = str(raw).upper() if raw else "UNKNOWN"
        if role_str == "USER":
            return "user"
        if role_str in ("ASSISTANT", "AGENT"):
            return "assistant"
        return None

    async def provider(context: RequestContext) -> list[ConversationMessage]:
        if client is None:
            return []
        if not context.message or not context.message.metadata:
            return []
        invoker_id = context.message.metadata.get(invoker_id_key)
        if not invoker_id:
            return []
        message_group = context.context_id
        if not message_group:
            return []

        try:
            messages = await asyncio.to_thread(
                client.list_messages,
                agent_id=agent_id,
                invoker_id=invoker_id,
                message_group=message_group,
                limit=max_messages,
            )
        except Exception as e:
            logger.warning(f"Agent Memory list_messages failed: {e}")
            return []

        history: list[ConversationMessage] = []
        for msg in messages:
            role = _map_role(getattr(msg, "role", None))
            content = getattr(msg, "content", None)
            if role and content:
                history.append({"role": role, "content": content})
        return history

    return provider
