from __future__ import annotations

import asyncio
import logging
from typing import cast

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater

from aegis.a2a._compat import new_agent_text_message, new_task
from aegis.a2a._utils import apply_view, build_view, resolve_hook
from aegis.a2a.context import GuardedContext
from aegis.a2a.event_queue import GuardedEventQueue
from aegis.a2a.types import InputGuardConfig, OutputGuardConfig
from aegis.metadata import GuardMetadata
from aegis.telemetry import start_span
from aegis.types import ValidationError

logger = logging.getLogger("aegis.a2a")


class GuardedAgentExecutor(AgentExecutor):
    """Wraps any AgentExecutor with automatic input/output guardrails.

    The inner executor's ``execute()`` and ``cancel()`` methods are
    unchanged — guardrails are applied transparently via wrapped
    context and event queue.

    Pass :class:`InputGuardConfig` and/or :class:`OutputGuardConfig` to
    configure each phase independently::

        GuardedAgentExecutor(
            executor=my_executor,
            input_config=InputGuardConfig(
                guard=my_input_guard,
                on_fail="I'm unable to help with that request.",
            ),
            output_config=OutputGuardConfig(
                mutation_guard=my_output_mutation_guard,
            ),
        )

    ``on_fail`` in each config accepts a **static value** or a **callable**.
    Return ``False`` explicitly to allow through; ``None`` (implicit return)
    is treated as rejection/suppression (fail-close default).

    Conversation history for context-aware validators (e.g. ``ScopeCheck``) is
    supplied through :attr:`InputGuardConfig.history_provider` — an async
    callable that returns prior turns from whatever backend the user chooses
    (SAP Agent Memory, ``context.related_tasks``, Redis, etc.). Aegis calls
    the provider once per request and stores the result on
    ``GuardMetadata.conversation_history``. See ``examples/history_provider_*.py``
    for reference implementations.
    """

    def __init__(
        self,
        executor: AgentExecutor,
        input_config: InputGuardConfig | None = None,
        output_config: OutputGuardConfig | None = None,
    ) -> None:
        self._executor = executor
        self._input_config = input_config
        self._output_config = output_config

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        with start_span(
            "guarded_executor.execute",
            attributes={"executor.class": type(self._executor).__name__},
        ):
            if self._should_speculate(self._input_config):
                # _should_speculate returns True only when the config is set.
                assert self._input_config is not None
                await self._execute_speculative(self._input_config, context, event_queue)
            else:
                await self._execute_sequential(context, event_queue)

    async def _execute_sequential(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Current behaviour — the input guard blocks before the agent starts."""
        # --- Input pipeline ---
        guarded_context = await self._run_input_pipeline(context, event_queue)
        if guarded_context is None:
            return  # input was rejected

        # --- Wrap event_queue for output pipeline ---
        guarded_eq = GuardedEventQueue(
            event_queue,
            output_config=self._output_config,
        )

        # --- Delegate to inner executor ---
        await self._executor.execute(guarded_context, cast(EventQueue, guarded_eq))

    def _should_speculate(self, cfg: InputGuardConfig | None) -> bool:
        """Whether speculative holdback is enabled and safe for this request.

        Speculation is only valid when the agent gets clean input before it
        starts — i.e. no input mutation guard, or the mutation runs first.
        If a consumer sets ``speculative=True`` but also configures a
        non-``mutation_first`` mutation guard, we silently fall back to
        sequential (the guard rail protects other consumers of this path).
        """
        if cfg is None or not cfg.speculative:
            return False
        if cfg.guard is None:
            return False  # nothing to parallelize
        if cfg.mutation_guard is not None and not cfg.mutation_first:
            # Detection must run before mutation, so it can't run concurrently.
            return False
        return True

    async def _execute_speculative(
        self,
        cfg: InputGuardConfig,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Run the detection guard in parallel with the agent (branch prediction).

        The agent starts immediately and its output is held in a buffer; the
        guard validates concurrently. On pass we flush the buffer and let the
        agent finish; on fail/raise we cancel the agent and discard the buffer
        so nothing ever reaches the user.
        """
        # --- Input mutation (always sequential, runs before the guard) ---
        parts = self._extract_parts(context)
        mutated_parts = None
        if cfg.mutation_guard is not None and cfg.mutation_first:
            mutated_parts = await self._run_mutation_stage(cfg.mutation_guard, parts)
            if mutated_parts is not None:
                parts = mutated_parts

        # --- Set up the speculation seam ---
        holdback = asyncio.Event()
        guarded_context = GuardedContext(context, mutated_parts=mutated_parts)
        guarded_eq = GuardedEventQueue(
            event_queue,
            output_config=self._output_config,
            holdback=holdback,
            stream_lifecycle_events=cfg.stream_lifecycle_events,
        )

        # --- Launch the agent and the guard concurrently ---
        executor_task = asyncio.create_task(self._executor.execute(guarded_context, cast(EventQueue, guarded_eq)))
        guard_task = asyncio.create_task(
            self._run_guard_stage(cfg.guard, cfg.on_fail, parts, guarded_context, event_queue)
        )

        with start_span("guarded_executor.speculative"):
            try:
                try:
                    rejection_message = await guard_task
                except BaseException:
                    # The guard stage raised instead of returning a verdict — an
                    # unexpected validator crash, or this coroutine being cancelled.
                    # (A normal rejection, including on_fail=EXCEPTION, comes back as a
                    # returned message; see _run_guard_stage.) Tear down the speculating
                    # agent and its buffer, then re-raise as sequential mode would.
                    await self._cancel_agent(executor_task)
                    guarded_eq.discard()
                    raise

                if rejection_message is not None:
                    # Guard failed — cancel the agent first (it stops emitting),
                    # then discard the buffer. We reject only *after* the agent
                    # has fully unwound, so it cannot forward a Task event
                    # concurrently with our rejection. If lifecycle streaming
                    # already forwarded the agent's Task, reject against that one
                    # instead of minting a second (see
                    # GuardedEventQueue.forwarded_task).
                    await self._cancel_agent(executor_task)
                    guarded_eq.discard()
                    await self._reject_task(
                        context,
                        event_queue,
                        rejection_message,
                        existing_task=guarded_eq.forwarded_task,
                    )
                    return

                # Guard passed — release the buffered output. flush() drains in
                # order before opening the gate, so the still-running agent's new
                # events cannot overtake the buffered ones; see GuardedEventQueue.flush.
                await guarded_eq.flush()

                # Let the agent finish; remaining events forward directly. Any
                # exception the agent raised propagates here, as in sequential mode.
                await executor_task
            finally:
                # If we exit early (e.g. the outer coroutine is cancelled during
                # flush/await), make sure the agent task is never left dangling.
                if not executor_task.done():
                    await self._cancel_agent(executor_task)

    @staticmethod
    async def _cancel_agent(task: asyncio.Task) -> None:
        """Cancel the agent task and await its unwind.

        Awaiting lets the agent's ``finally`` / context-manager cleanup run
        before we return, so we never leave a dangling task. This runs only on
        the reject/raise path where the agent's work is discarded, so any
        exception it raises (including one from an already-finished fast agent)
        is swallowed — it must not mask the guard's rejection. Cancellation is
        re-raised if *this* coroutine is itself being cancelled.
        """
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # We don't care who cancelled the agent (our cancel() or an inner
            # timeout) — we're throwing its work away either way, so the result
            # is the same. Accepted trade-off.
            if task.cancelled():
                return  # expected: the agent unwound on our cancel()
            raise  # we were cancelled from outside — propagate
        except Exception:
            logger.debug("Discarded agent raised during speculative cancel", exc_info=True)

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        await self._executor.cancel(context, event_queue)

    # ------------------------------------------------------------------
    # Input pipeline
    # ------------------------------------------------------------------

    async def _run_input_pipeline(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> GuardedContext | None:
        """Run the input guard pipeline.

        Returns :class:`GuardedContext` on success, ``None`` if rejected.

        Default order (``mutation_first=False``): ``Guard → MutationGuard``.
        When ``mutation_first=True``: ``MutationGuard → Guard``
        (sanitize first, then check the clean input).
        """
        if self._input_config is None:
            return GuardedContext(context)

        cfg = self._input_config
        parts = self._extract_parts(context)
        mutated_parts = None

        if cfg.mutation_first:
            if cfg.mutation_guard:
                mutated_parts = await self._run_mutation_stage(cfg.mutation_guard, parts)
                if mutated_parts is not None:
                    parts = mutated_parts
            if cfg.guard:
                message = await self._run_guard_stage(cfg.guard, cfg.on_fail, parts, context, event_queue)
                if message is not None:
                    await self._reject_task(context, event_queue, message)
                    return None
        else:
            if cfg.guard:
                message = await self._run_guard_stage(cfg.guard, cfg.on_fail, parts, context, event_queue)
                if message is not None:
                    await self._reject_task(context, event_queue, message)
                    return None
            if cfg.mutation_guard:
                mutated_parts = await self._run_mutation_stage(cfg.mutation_guard, parts)

        return GuardedContext(context, mutated_parts=mutated_parts)

    async def _run_guard_stage(self, guard, on_fail, parts, context, event_queue) -> str | None:
        """Evaluate the detection guard stage.

        Returns the rejection **message** (``str``) if the task should be
        rejected, or ``None`` if it passed / was explicitly allowed through.

        Emission of the rejection is left to the caller. The speculative path
        relies on this: it cancels the agent *before* the rejection is enqueued,
        so a still-running agent cannot forward its own ``Task`` event in between
        (which would leave the client seeing two tasks).
        """
        with start_span("guarded_executor.input_guard"):
            metadata = await self._build_metadata(context)
            try:
                result = await guard.validate(
                    build_view(parts),
                    metadata=metadata,
                )
            except ValidationError as e:
                return str(e)

            if result.has_failures and on_fail is not None:
                resolved = await resolve_hook(on_fail, result.failures, context, event_queue)
                if resolved is not False:
                    return (
                        resolved if isinstance(resolved, str) else "; ".join(f.error_message for f in result.failures)
                    )
        return None

    async def _run_mutation_stage(self, mutation_guard, parts):
        """Run the input mutation stage. Returns mutated parts, or ``None`` when
        there is nothing to mutate (no parts) or nothing changed — so the caller
        keeps the original. Part cardinality and order are preserved; non-text
        parts pass through untouched.

        A message with no parts short-circuits to ``None`` rather than letting
        ``MutationGuard.validate(parts=None)`` synthesise an empty text part,
        which could otherwise inject a spurious part into a partless message.
        """
        if not parts:
            return None
        with start_span("guarded_executor.input_mutation"):
            result = await mutation_guard.validate(build_view(parts))
            # A2A only ever feeds a ContentView (build_view), so output is never a str.
            # Fail loud rather than silently drop a security mutation.
            if isinstance(result.output, str):
                raise TypeError(f"MutationGuard returned {type(result.output).__name__}, expected ContentView")
            if result.was_mutated and result.output is not None:
                return apply_view(parts, result.output)
            return None

    def _extract_parts(self, context: RequestContext):
        if context.message and context.message.parts:
            return context.message.parts
        return None

    async def _build_metadata(self, context: RequestContext) -> GuardMetadata | None:
        """Assemble metadata for the guard stage.

        Currently only populates ``conversation_history`` when a
        ``history_provider`` is configured. Returns ``None`` when there is no
        provider or no history to add, so validators see ``metadata=None``
        rather than an empty object. Provider errors are logged and swallowed —
        validation still runs, just without history.
        """
        provider = self._input_config.history_provider if self._input_config else None
        if provider is None:
            return None

        try:
            history = await provider(context)
        except Exception as e:
            logger.warning(f"history_provider failed: {e}")
            return None

        if history:
            return GuardMetadata(conversation_history=history)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _reject_task(context, event_queue, error_message: str, existing_task=None):
        """Reject the current task with an error message.

        ``existing_task`` lets the speculative fail path reuse a ``Task`` the
        agent already streamed (under ``stream_lifecycle_events=True``) instead
        of creating a second one — otherwise the client would see two Task
        events. Preference order: the context's current task, then a task
        already forwarded to the queue, then a freshly created one.
        """
        task = context.current_task or existing_task
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        # ``reject`` marks the task terminal with a rejected status + message on both
        # a2a-sdk majors. 1.x dropped the ``update_status(..., final=True)`` form the
        # original code used; on 0.3 ``reject`` is exactly that call wrapped.
        await updater.reject(new_agent_text_message(error_message, task.context_id, task.id))
