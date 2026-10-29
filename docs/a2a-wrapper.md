# A2A Wrapper

The A2A wrapper provides two integration paths that eliminate guardrail boilerplate from your A2A
agent executors. Both produce identical runtime behaviour — choose the pattern that fits your
architecture.

1. **`GuardedAgentExecutor`** — wraps any existing `AgentExecutor` instance (composition)
2. **`@guarded` decorator** — decorates any `AgentExecutor` class

Your `execute()` method stays unchanged. The wrapper sits around it, applying guards
transparently via wrapped context and event queue.

```
A2A Server
  → GuardedAgentExecutor.execute(context, event_queue)
       ├─ Input pipeline  (Guard → MutationGuard, or reversed with mutation_first=True)
       ├─ Wrap context  → GuardedContext  (mutated input transparently accessible)
       ├─ Wrap event_queue → GuardedEventQueue  (output guards on artifacts)
       └─ inner_executor.execute(guarded_context, guarded_event_queue)
            └─ Your normal execute() code — unchanged
```

---

## Config Objects

Both `GuardedAgentExecutor` and `@guarded` accept `InputGuardConfig` and `OutputGuardConfig`
objects that group the guard, mutation guard, pipeline order, and failure hook for each phase.

```python
from aegis.a2a import InputGuardConfig, OutputGuardConfig
```

### InputGuardConfig

| Field | Type | Default | Purpose |
|---|---|---|---|
| `guard` | `Guard | None` | `None` | Detection guard — validators run concurrently, no mutation |
| `mutation_guard` | `MutationGuard | None` | `None` | Mutation guard — validators run sequentially, transforms the value |
| `mutation_first` | `bool` | `False` | When `True`, run `MutationGuard` before `Guard` |
| `on_fail` | `InputFailHook | None` | `None` | Hook fired when the guard has failures |
| `history_provider` | `HistoryProvider | None` | `None` | Async callable returning prior conversation turns. See [Conversation history](#conversation-history) below. |
| `speculative` | `bool` | `False` | Run the detection guard in parallel with the agent, buffering output until it passes. See [Speculative holdback](#speculative-holdback) below. |
| `stream_lifecycle_events` | `bool` | `False` | When speculating, forward non-content lifecycle events immediately instead of buffering them. See [Speculative holdback](#speculative-holdback). |

### OutputGuardConfig

| Field | Type | Default | Purpose |
|---|---|---|---|
| `guard` | `Guard | None` | `None` | Detection guard on outbound artifact parts |
| `mutation_guard` | `MutationGuard | None` | `None` | Mutation guard on outbound artifact parts |
| `mutation_first` | `bool` | `False` | When `True`, run `MutationGuard` before `Guard` |
| `on_fail` | `OutputFailHook | None` | `None` | Hook fired when the output guard has failures |
| `artifact_filter` | `ArtifactFilter` | `None` | Predicate to select which artifacts are guarded |

### Pipeline order

| `mutation_first` | Pipeline |
|---|---|
| `False` (default) | `Guard → MutationGuard` — check first, then sanitize |
| `True` | `MutationGuard → Guard` — sanitize first, then check the clean content |

---

## GuardedAgentExecutor

Wrap any `AgentExecutor` instance:

`ContentFilter` below is a built-in validator; `MaxLength` and `PIIRedactor` are custom ones
you write against the contract. Aegis ships built-in validators (see
[Validators](validators/index.md)) and lets you add your own (see
[Custom Validators](custom-validators.md)).

```python
from aegis import Guard, MutationGuard, OnFailAction
from aegis.a2a import GuardedAgentExecutor, InputGuardConfig, OutputGuardConfig
from aegis.validators.detection import ContentFilter
from my_validators import MaxLength, PIIRedactor  # your own validators
from agent_config import BLOCKED_TOPICS

guarded_executor = GuardedAgentExecutor(
    executor=MyAgentExecutor(),
    input_config=InputGuardConfig(
        guard=(
            Guard("input")
            .use(MaxLength(5000), on_fail=OnFailAction.NOOP)
            .use(ContentFilter(blocked_topics=BLOCKED_TOPICS), on_fail=OnFailAction.NOOP)
        ),
        on_fail="I'm unable to help with that request.",
    ),
    output_config=OutputGuardConfig(
        mutation_guard=MutationGuard("output").use(PIIRedactor()),
    ),
)

# Register guarded_executor with the A2A server instead of the raw executor
```

| Parameter | Type | Purpose |
|---|---|---|
| `executor` | `AgentExecutor` | The inner executor to wrap |
| `input_config` | `InputGuardConfig | None` | Input phase configuration |
| `output_config` | `OutputGuardConfig | None` | Output phase configuration |

---

## @guarded Decorator

Same config objects, class-level syntax:

```python
from aegis.a2a import guarded, InputGuardConfig, OutputGuardConfig

@guarded(
    input_config=InputGuardConfig(
        guard=(
            Guard("input")
            .use(MaxLength(5000), on_fail=OnFailAction.NOOP)
            .use(ContentFilter(blocked_topics=BLOCKED_TOPICS), on_fail=OnFailAction.NOOP)
        ),
        on_fail="I'm unable to help with that request.",
    ),
    output_config=OutputGuardConfig(
        mutation_guard=MutationGuard("output").use(PIIRedactor()),
    ),
)
class MyAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        query = context.get_user_input()  # mutated input if mutation_guard was set
        # ... your logic unchanged ...
```

!!! note
    Pass `on_fail` inside the config object. Class-level `on_input_fail` / `on_output_fail`
    methods are not supported — put the hook in `InputGuardConfig(on_fail=...)` instead.

---

## on_fail Hooks

`on_fail` is set inside the config object for each phase. It fires after `guard.validate()`
returns with failures (not on mutation — `MutationGuard` always runs silently).

### Input on_fail

Callable signature: `(failures: list[FailedResult], context: RequestContext, event_queue: EventQueue)`

| Value / return | Behaviour |
|---|---|
| `True` | Reject the task with all failure messages joined by `"; "` |
| `str` | Reject the task with this custom message |
| `False` | Continue execution — explicit opt-in to allow through |
| `None` (implicit return) | Same as `True` — fail-close default |

The wrapper handles task rejection automatically (a rejected terminal status + message). The
hook only decides *whether* to reject and optionally *what message* to use.

```python
# Static: reject with custom message
InputGuardConfig(guard=guard, on_fail="Your request cannot be processed.")

# Static: reject with aggregated validator errors
InputGuardConfig(guard=guard, on_fail=True)

# Callable: conditionally allow through
async def reject_on_multiple(failures, context, event_queue):
    if len(failures) >= 2:
        return True   # reject
    return False      # allow through — must be explicit

InputGuardConfig(guard=guard, on_fail=reject_on_multiple)
```

### Output on_fail

Callable signature: `(failures: list[FailedResult])`

| Value / return | Behaviour |
|---|---|
| `True` | Suppress the artifact — do not emit it |
| `str` | Replace artifact content with this message before emitting |
| `False` | Emit the artifact as-is — explicit opt-in to allow through |
| `None` (implicit return) | Same as `True` — fail-close default |

```python
# Static: suppress the artifact
OutputGuardConfig(guard=guard, on_fail=True)

# Static: replace with a safe message
OutputGuardConfig(guard=guard, on_fail="This content has been redacted.")

# Callable: log but allow through
def log_and_continue(failures):
    logger.warning("Output issues: %s", failures)
    return False  # must be explicit

OutputGuardConfig(guard=guard, on_fail=log_and_continue)
```

!!! note "Multiple artifacts"
    Each `add_artifact()` call is guarded independently. If two artifacts fail, the hook is
    called once per artifact. If you need different behaviour per artifact, manage state externally
    via a closure.

---

## Artifact Filtering

By default, output guards run on **every** `TaskArtifactUpdateEvent`. Use `artifact_filter` to
selectively guard only certain artifacts — skipping metadata, sources, or debug artifacts that
would cause false positives or waste compute.

The filter is a callable that receives an `Artifact` and returns `True` (guard it) or `False`
(pass through unmodified). Both sync and async callables are supported.

```python
# Guard only the main response, skip metadata/sources
OutputGuardConfig(
    mutation_guard=output_guard,
    artifact_filter=lambda artifact: artifact.name == "main_response",
)

# Guard everything except specific artifacts
OutputGuardConfig(
    mutation_guard=output_guard,
    artifact_filter=lambda artifact: artifact.name not in {"sources", "debug_metadata"},
)

# Async predicate (e.g., feature flag lookup)
async def should_guard(artifact):
    if artifact.name.startswith("_internal"):
        return False
    return True

OutputGuardConfig(
    mutation_guard=output_guard,
    artifact_filter=should_guard,
)
```

When `artifact_filter` is `None` (the default), all artifacts are guarded — preserving
backwards-compatible behaviour.

---

## mutation_first

Use `mutation_first=True` when you want to sanitize content *before* checking it — useful when
guards should see the clean version rather than raw user input.

```python
InputGuardConfig(
    guard=(
        Guard("input")
        .use(ContentFilter(blocked_topics=BLOCKED_TOPICS), on_fail=OnFailAction.NOOP)
    ),
    mutation_guard=MutationGuard("sanitize").use(PIIRedactor()),
    mutation_first=True,   # redact PII first, then filter the clean input
    on_fail="I can't help with that.",
)
```

The same flag works on `OutputGuardConfig` — set `mutation_first=True` to sanitize output
before running the output guard.

---

## Input Mutation and GuardedContext

When `InputGuardConfig.mutation_guard` produces a mutated value, the context passed to
`execute()` is a `GuardedContext` — a `RequestContext` subclass that transparently provides
the mutated text:

```python
async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
    query = context.get_user_input()          # mutated text (or original if nothing changed)
    original = context.original_input         # pre-mutation text
    original_msg = context.original_message   # pre-mutation Message object
```

`get_user_input()` and `message.parts` both read from the same guarded `Message` — it is not
possible to get inconsistent results.

---

## Output Interception

Output guards run inside `GuardedEventQueue`, which intercepts `TaskArtifactUpdateEvent`
events. This works because `TaskUpdater.add_artifact()` ultimately calls
`event_queue.enqueue_event()`.

| Event type | Guarded? |
|---|---|
| `TaskArtifactUpdateEvent` | Yes — output guard + mutation applied |
| `TaskStatusUpdateEvent` (working, completed, etc.) | No — passes through |
| `Task` (task creation) | No — passes through |

`ValidationError` from `on_fail=OnFailAction.EXCEPTION` propagates up through
`add_artifact()`, halting execution — use this for hard stops on output violations.

---

## Telemetry

The wrapper adds parent spans around existing Guard/MutationGuard spans:

```
guarded_executor.execute
├─ guarded_executor.input_guard
│  └─ guard.{name}.validate          (existing Guard spans)
│     └─ validator.{name}
├─ guarded_executor.input_mutation
│  └─ mutation_guard.{name}.validate (existing MutationGuard spans)
│     └─ mutation_validator.{name}
├─ [user's execute() runs]
├─ guarded.output_guard              (per artifact event)
│  └─ guard.{name}.validate
└─ guarded.output_mutation           (per artifact event)
   └─ mutation_guard.{name}.validate
```

No span duplication — wrapper spans provide pipeline-level timing, existing spans provide
per-validator detail. In speculative mode the guard runs under a `guarded_executor.speculative`
span while the agent runs concurrently. See [Observability](cookbook/observability.md) for exporter setup.

---

## Speculative Holdback

Input detection guards can add noticeable latency when a validator is slow (for example, one
that makes a network call). Normally they run *before* the agent, so **every** request pays that
cost even though ~99% are benign.

**Speculative holdback runs the guard and the agent at the same time.** The agent starts
immediately; its output is held in a buffer and only revealed once the guard passes. Think of
it as CPU branch prediction: assume the likely branch (input is valid), do the work early,
and discard it in the rare miss.

```
SEQUENTIAL (default):                  SPECULATIVE (speculative=True):

│ guard ═══════════╗ (~800ms)          ├─ guard ═══════════╗ (~800ms)
│                  ║                    └─ agent ───────────╫────────▶ response
│      agent ──────╨───────▶ response        └─ output ▶[BUFFER]│
                                                              released on pass
total = guard + agent                  total ≈ agent alone
```

- **Guard passes (~99%):** the buffer is flushed → output flows to the user. The guard's
  latency was hidden behind the agent's own work.
- **Guard fails (~1%):** the agent is cancelled and the buffer discarded. The user sees only
  the rejection — exactly as in sequential mode.

**Key invariant:** no agent output ever reaches the user before the guard passes.

```python
InputGuardConfig(
    guard=Guard("input").use(ContentFilter(blocked_topics=BLOCKED_TOPICS)),
    on_fail="I'm unable to help with that request.",
    speculative=True,
)
```

### When speculation applies

Speculation is only safe when the agent gets **clean input before it starts** — so it
requires either no input `mutation_guard`, or `mutation_first=True` (mutation runs first,
then the agent and guard run concurrently on the mutated input). If you set `speculative=True`
alongside a non-`mutation_first` mutation guard, Aegis **silently falls back to sequential
execution** — detection must run before mutation in that configuration, so it can't run
concurrently.

### Read-only agents only

!!! warning "Read-only agents only"
    Enable speculative holdback **only** for agents with no external side effects at any point
    during execution — reads, computation, and LLM calls are fine, but no database writes, no
    state-mutating API calls, no file writes.

Buffering hides *output*; it does not undo *actions*. The agent gets no signal for when (or
whether) the guard passed, and on a failing verdict cancellation is not instant: it only
lands at the agent's next `await`. So a write issued at *any* point while speculating can
commit even though the request is rejected. There is no safe window for writes.

This first release therefore supports read-only agents only. Do **not** set
`speculative=True` on a write-capable agent; run it sequentially instead.

### Lifecycle events vs. content

By default (`stream_lifecycle_events=False`), Aegis holds **everything** until the guard
passes — strict non-leakage: the user sees nothing (not even a "working" status) before the
verdict. Set `stream_lifecycle_events=True` to forward non-content lifecycle events (`Task`
creation, `TaskStatusUpdateEvent`) immediately for better perceived latency, while still
buffering every `TaskArtifactUpdateEvent` (the actual answer). Enable this **only** when your
pre-verdict lifecycle events carry no user or model content (e.g. a static status string).

### Preconditions and caveats

- **Read-only agents only.** See the warning above — the agent must have no external side
  effects at any point during execution.
- **Cancellation safety is required.** The failure path cancels the agent mid-execution. Your
  agent, its LLM/graph invocation, and any clients must unwind cleanly on `CancelledError`.
- **Wasted compute on rejects.** Speculative runs burn LLM/tool work that is discarded when
  the guard fails. Acceptable at ~99% benign, but real.
- **ROI is workload-dependent.** The win is largest when the guard is a large fraction of a
  short total (light / lookup queries). On long flows where the guard is a small slice of the
  total, the benefit is near-zero. Enable per-agent based on profiling.

---

## Conversation History

Context-aware validators can benefit from conversation history to interpret simple replies
like "yes" or "no" in context. The wrapper populates
`GuardMetadata.conversation_history` from a user-supplied **history provider** and
passes it to every validator; validators that use context read the field,
validators that don't ignore it.

### The history_provider contract

`InputGuardConfig.history_provider` is an async callable:

```python
HistoryProvider = Callable[[RequestContext], Awaitable[list[ConversationMessage]]]
```

- Return chronological messages (oldest first), each a
  `{"role": "user" | "assistant", "content": str}` dict.
- Return `[]` when no history is available — do not raise.
- Any exception is caught, logged at `WARNING`, and validation continues
  without history.
- The provider owns truncation. Aegis does not cap the returned length.

Aegis ships **no** built-in providers — the backend (SAP Agent Memory,
`context.related_tasks`, Redis, Postgres, …) is user code. Two reference
implementations live in `examples/`:

- `examples/history_provider_related_tasks.py` — reads from
  `RequestContext.related_tasks`.
- `examples/history_provider_sap_agent_memory.py` — reads from SAP Agent
  Memory Service (requires `sap-cloud-sdk`).

### Example: SAP Agent Memory

```python
from examples.history_provider_sap_agent_memory import make_sap_agent_memory_provider

InputGuardConfig(
    guard=guard,
    history_provider=make_sap_agent_memory_provider(
        agent_id="my-agent-id",
        invoker_id_key="user_id",
        max_messages=10,
    ),
)
```

### Example: related_tasks fallback

Requires the A2A server to populate related tasks:

```python
# A2A server configuration
context_builder = SimpleRequestContextBuilder(
    should_populate_referred_tasks=True,
    task_store=your_task_store,
)
```

```python
from examples.history_provider_related_tasks import make_related_tasks_provider

InputGuardConfig(
    guard=guard,
    history_provider=make_related_tasks_provider(max_messages=5),
)
```

!!! warning "Multi-pod deployments"
    `related_tasks` reads from the A2A server's `TaskStore`. The default
    in-memory store is **per-pod** — in a Kubernetes / horizontally-scaled
    deployment, a follow-up request routed to a different pod will find no
    prior tasks and the provider returns `[]`. Either pin sessions with sticky
    routing, back the `TaskStore` with a shared store (Redis, Postgres), or
    use a cross-pod backend like SAP Agent Memory as the primary provider.

### Chaining providers

Providers are plain callables — wrap them however you want. A common pattern
is SAP Agent Memory with `related_tasks` as fallback:

```python
async def hybrid_provider(context):
    history = await sap_provider(context)
    return history or await related_tasks_provider(context)

InputGuardConfig(guard=guard, history_provider=hybrid_provider)
```

### Standalone Guard users

Callers of `Guard.validate()` outside the A2A wrapper populate metadata
themselves — the `history_provider` field is only used by
`GuardedAgentExecutor`:

```python
from aegis import GuardMetadata

history = fetch_from_wherever(user_id)  # your code
await guard.validate(
    value=user_text,
    metadata=GuardMetadata(conversation_history=history),
)
```
