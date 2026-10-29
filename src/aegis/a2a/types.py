from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import Artifact

from aegis.guard import Guard
from aegis.metadata import ConversationMessage
from aegis.mutation_guard import MutationGuard
from aegis.validators.base import FailedResult

HistoryProvider = Callable[[RequestContext], Awaitable[list[ConversationMessage]]]
"""Async callable that returns prior conversation turns for the current request.

The provider owns the *how* — SAP Agent Memory, ``context.related_tasks``,
Redis, an in-memory dict, anything. Aegis calls it once per request when
``InputGuardConfig.history_provider`` is set and injects the returned list
into ``GuardMetadata.conversation_history`` before running validators.

Contract:
- Return a chronological list (oldest first) of
  ``{"role": "user"|"assistant", "content": str}``.
- Return ``[]`` when no history is available — do not raise for this case.
- Any exception is caught, logged as a warning, and validation continues
  with no history. Providers should not raise for expected "no data" paths.
- The provider is responsible for truncation (``[-N:]`` slice). Aegis does
  not cap the length.

See ``examples/history_provider_*.py`` for reference implementations.
"""

InputFailHookCallable = (
    Callable[[list[FailedResult], RequestContext, EventQueue], Awaitable[bool | str]]
    | Callable[[list[FailedResult], RequestContext, EventQueue], bool | str]
)
"""A callable that decides whether to reject the task after input guard failures."""

InputFailHook = bool | str | InputFailHookCallable
"""Controls behaviour after input guard failures.

Accepts a static value **or** a callable:

Static values (decided at configuration time):
- ``True``: reject the task with all failure messages joined by ``"; "``.
- ``str``: reject the task with this custom message.
- ``False``: continue execution (explicit opt-in to allow through).

Callable (decided at runtime):
- Called with ``(failures, context, event_queue)``. Both sync and async are supported.
- Return value uses the same semantics as static values above.
- ``None`` (implicit return) is treated as ``True`` (reject).

Defaults to rejection — the safe default is to block.
Return ``False`` explicitly to allow through.
"""

OutputFailHookCallable = (
    Callable[[list[FailedResult]], Awaitable[bool | str | None]] | Callable[[list[FailedResult]], bool | str | None]
)
"""A callable that decides how to handle an artifact after output guard failures."""

OutputFailHook = bool | str | OutputFailHookCallable
"""Controls behaviour after output guard failures.

Accepts a static value **or** a callable:

Static values (decided at configuration time):
- ``True``: suppress the artifact (do not emit).
- ``str``: replace artifact text with this message before emitting.
- ``False``: emit the artifact as-is (explicit opt-in to allow through).

Callable (decided at runtime):
- Called with ``(failures,)``. Both sync and async are supported.
- Return value uses the same semantics as static values above.
- ``None`` (implicit return) is treated as ``True`` (suppress).

Defaults to suppression — the safe default is to block.
Return ``False`` explicitly to allow through.
"""

ArtifactFilter = Callable[[Artifact], bool] | Callable[[Artifact], Awaitable[bool]] | None
"""Predicate controlling which artifacts are guarded.

- ``None`` (default): guard all artifacts (current behaviour).
- ``Callable[[Artifact], bool]``: return ``True`` to guard, ``False`` to skip.
- Async callables are also supported.

Artifacts that do not pass the filter are emitted unmodified — no guards run,
no compute wasted, no risk of false positives on non-target artifacts.
"""


@dataclass
class InputGuardConfig:
    """Configuration for the input guard phase of ``GuardedAgentExecutor``.

    Groups the guard, mutation guard, pipeline order, and fail hook for the
    input phase into a single object.
    """

    guard: Guard | None = None
    """Detection guard — runs validators concurrently and collects failures."""

    mutation_guard: MutationGuard | None = None
    """Mutation guard — runs validators sequentially and transforms input parts."""

    mutation_first: bool = False
    """When ``True``, run ``MutationGuard`` before ``Guard`` (sanitize then check).
    Default ``False`` preserves the original ``Guard → MutationGuard`` order."""

    on_fail: InputFailHook | None = None
    """Hook invoked when the guard has failures. See :class:`InputFailHook`."""

    history_provider: HistoryProvider | None = None
    """Optional provider that supplies prior conversation turns for validators.

    When set, ``GuardedAgentExecutor`` calls the provider once per request and
    stores the returned list on ``GuardMetadata.conversation_history`` before
    running validators. Validators that use context (e.g. :class:`ScopeCheck`)
    read from that field; validators that don't ignore it.

    Standalone callers of ``Guard.validate()`` (no A2A executor) should populate
    ``GuardMetadata.conversation_history`` directly instead.

    See :data:`HistoryProvider` for the callable contract and
    ``examples/history_provider_*.py`` for reference implementations.
    """

    speculative: bool = False
    """When ``True``, run the detection ``guard`` in parallel with the agent.

    Normally the guard runs *before* the agent, so every request pays the
    guard's latency (LLM-based validators add ~0.8–2.6s). Speculative holdback
    starts the agent immediately while the guard validates concurrently, and
    holds the agent's output in a buffer until the guard passes:

    - **Guard passes (~99%):** the buffer is flushed to the user. The guard's
      latency was hidden behind the agent's own work.
    - **Guard fails (~1%):** the agent is cancelled and the buffer discarded —
      the user sees only the rejection, exactly as in sequential mode. No
      output ever reaches the user before the guard passes.

    Only applies when the agent can start on clean input — i.e. no input
    ``mutation_guard``, or ``mutation_first=True``. If a non-``mutation_first``
    mutation guard is configured, aegis silently falls back to sequential
    execution (:meth:`GuardedAgentExecutor._should_speculate`).

    Warning:
        **Read-only agents only.** Enable speculative holdback *only* for agents
        with no external side effects at any point during execution — reads,
        computation, and LLM calls are fine, but no database writes, no
        state-mutating API calls, no file writes. On a failing verdict the agent
        is cancelled mid-execution; buffering hides its *output* but cannot undo
        an *action* already taken, so a write issued while speculating may be
        committed even though the request was rejected. Do **not** set
        ``speculative=True`` on write-capable agents.

    Cancellation safety is also required: on the failure path the agent is
    cancelled mid-execution, so the agent, its LLM/graph invocation, and any
    clients must unwind cleanly on ``CancelledError``.

    Defaults to ``False`` — existing behaviour is unchanged.
    """

    stream_lifecycle_events: bool = False
    """When ``True`` (and speculating), let non-content lifecycle events through
    the holdback buffer instead of buffering them.

    The agent's first emissions are usually a ``Task`` creation event and a
    ``TaskStatusUpdateEvent`` (e.g. an "Analyzing your request…" status). By
    default (``False``) aegis holds **everything** until the guard passes —
    strict non-leakage: the user sees nothing, not even a "working" status,
    before the verdict.

    Setting this ``True`` forwards those lifecycle events immediately for better
    perceived latency, while still buffering every ``TaskArtifactUpdateEvent``
    (the actual answer). This is a deliberate, documented relaxation of strict
    non-leakage — enable it **only** when your pre-verdict lifecycle events
    carry no user or model content (e.g. a static status string).
    """


@dataclass
class OutputGuardConfig:
    """Configuration for the output guard phase of ``GuardedAgentExecutor``.

    Groups the guard, mutation guard, pipeline order, and fail hook for the
    output phase into a single object.
    """

    guard: Guard | None = None
    """Detection guard — runs validators concurrently and collects failures."""

    mutation_guard: MutationGuard | None = None
    """Mutation guard — runs validators sequentially and transforms output parts."""

    mutation_first: bool = False
    """When ``True``, run ``MutationGuard`` before ``Guard`` (sanitize then check).
    Default ``False`` preserves the original ``Guard → MutationGuard`` order."""

    on_fail: OutputFailHook | None = None
    """Hook invoked when the guard has failures. See :class:`OutputFailHook`."""

    artifact_filter: ArtifactFilter = None
    """Optional predicate to select which artifacts are guarded.

    When None (default), all artifacts are guarded (backwards-compatible).
    When provided, called with each Artifact before guards run.
    Return True to guard it, False to emit unmodified.
    """
