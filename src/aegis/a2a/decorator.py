from __future__ import annotations

import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from aegis.a2a.executor import GuardedAgentExecutor
from aegis.a2a.types import InputGuardConfig, OutputGuardConfig

logger = logging.getLogger("aegis.a2a")


class _OriginalAdapter(AgentExecutor):
    """Binds the pre-decoration execute method to the original instance.

    Captures ``original_execute`` (the unbound method as it existed before
    the decorator replaced ``cls.execute``) and binds it to the instance at
    call time. This avoids infinite recursion: by the time ``new_init`` runs,
    ``cls.execute`` is already ``wrapped_execute``, so passing ``self`` directly
    as the executor would recurse indefinitely.
    """

    def __init__(self, instance, original_execute):
        self._instance = instance
        self._original_execute = original_execute

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self._original_execute(self._instance, context, event_queue)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self._instance.cancel(context, event_queue)


def guarded(
    input_config: InputGuardConfig | None = None,
    output_config: OutputGuardConfig | None = None,
):
    """Decorator that wraps an AgentExecutor's ``execute()`` with guardrails.

    A thin wrapper over :class:`GuardedAgentExecutor` — the decorated class's
    original ``execute()`` is preserved and called through the same guard
    pipeline as the wrapper, so there is no duplicated logic.

    Usage::

        @guarded(
            input_config=InputGuardConfig(
                guard=my_input_guard,
                on_fail="I'm unable to help with that request.",
            ),
            output_config=OutputGuardConfig(
                mutation_guard=my_output_mutation_guard,
            ),
        )
        class MyAgentExecutor(AgentExecutor):
            async def execute(self, context, event_queue):
                # original logic here — called after guards pass
                ...

    ``on_fail`` in each config accepts a **static value** or a **callable**.
    Return ``False`` explicitly to allow through; ``None`` (implicit return)
    is treated as rejection/suppression (fail-close default).
    """

    def decorator(cls):
        original_init = cls.__init__
        original_execute = cls.execute  # captured before cls.execute is replaced

        def new_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self._guarded_executor = GuardedAgentExecutor(
                executor=_OriginalAdapter(self, original_execute),
                input_config=input_config,
                output_config=output_config,
            )

        async def wrapped_execute(self, context, event_queue):
            await self._guarded_executor.execute(context, event_queue)

        cls.__init__ = new_init
        cls.execute = wrapped_execute
        return cls

    return decorator
