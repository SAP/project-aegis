# AgentExecutor Integration

How to wire `aegis` into a standard A2A `AgentExecutor`. Two approaches are available:

| Approach | When to use |
|---|---|
| [GuardedAgentExecutor](#guardedagentexecutor-wrapper) | Standard input/output guardrails with minimal boilerplate |
| [Manual integration](#manual-integration) | Full control over rejection logic, state machine, logging |

---

## GuardedAgentExecutor Wrapper

The simplest path — wrap your executor with `GuardedAgentExecutor` and pass config objects
for input and output. The wrapper handles guard execution, rejection, and output mutation
transparently.

`ContentFilter` below is a built-in validator; `MaxLength` and `PIIRedactor` are custom ones
you write against the contract. Aegis ships built-in validators (see
[Validators](../validators/index.md)) and lets you add your own (see
[Custom Validators](../custom-validators.md)).

```python
from aegis import Guard, MutationGuard, OnFailAction
from aegis.a2a import GuardedAgentExecutor, InputGuardConfig, OutputGuardConfig
from aegis.validators.detection import ContentFilter
from my_validators import MaxLength, PIIRedactor
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
```

Or use the `@guarded` decorator:

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
        ...
```

### Reversing pipeline order

By default the input pipeline runs `Guard → MutationGuard`. Set `mutation_first=True` to
sanitize input before checking it — useful when you want guards to check the cleaned version:

```python
InputGuardConfig(
    guard=Guard("input").use(MaxLength(5000)).use(ContentFilter(...)),
    mutation_guard=MutationGuard("sanitize").use(PIIRedactor()),
    mutation_first=True,  # redact PII first, then check the clean input
    on_fail="I'm unable to help with that request.",
)
```

See [Guardrail Lifecycle](../architecture.md#guardrail-lifecycle) for how the default pipeline maps to the full A2A request cycle. See [A2A Wrapper](../a2a-wrapper.md) for full reference on config objects, failure handling, pipeline ordering, and telemetry.

---

## Manual Integration

Use this when you need full control — custom rejection messages per validator, structured
logging, or non-standard task state handling.

> This example targets the **a2a-sdk 1.x** API (the default install). On 0.3.x the a2a
> APIs differ — `from a2a.utils import ...`, `Part(root=TextPart(text=...))`,
> `TaskState.rejected`, `update_status(..., final=True)`. See
> [the migration notes](../a2a-v1-migration.md) for the mapping.

```python
import logging
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState, Part
from a2a.helpers import new_task_from_user_message, new_text_message
from a2a.server.errors import ServerError, InternalError

from aegis import Guard, MutationGuard, OnFailAction
from aegis.validators.detection import ContentFilter
from my_validators import MaxLength, PIIRedactor  # your own validators
from agent_config import BLOCKED_TOPICS

logger = logging.getLogger(__name__)


class MyAgentExecutor(AgentExecutor):
    def __init__(self):
        self.agent = MyAgent()
        self.guard = (
            Guard("input")
            .use(MaxLength(5000), on_fail=OnFailAction.NOOP)
            .use(ContentFilter(blocked_topics=BLOCKED_TOPICS), on_fail=OnFailAction.NOOP)
        )
        self.mutate_guard = MutationGuard("output").use(PIIRedactor())

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        query = context.get_user_input()
        task = context.current_task

        if not task:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        # ── Input guard ────────────────────────────────────────────────────────
        results = await self.guard.validate(query)

        for failed_result in results.failures:
            logger.warning(
                "Input failed sensitivity check",
                extra={
                    "task_id": task.id,
                    "context_id": task.context_id,
                    "error_code": failed_result.error_code,
                    "error_message": failed_result.error_message,
                }
            )
            await updater.reject(
                new_text_message(failed_result.error_message, context_id=task.context_id, task_id=task.id)
            )
            return
        # ──────────────────────────────────────────────────────────────────────

        try:
            async for item in self.agent.stream(
                query=query,
                context_id=task.context_id,
            ):
                is_task_complete = item["is_task_complete"]
                require_user_input = item["require_user_input"]
                content = item.get("content", "")
                metadata = item.get("metadata", {})

                if not is_task_complete and not require_user_input:
                    await updater.update_status(
                        TaskState.TASK_STATE_WORKING,
                        new_text_message(content, context_id=task.context_id, task_id=task.id),
                    )

                elif require_user_input:
                    await updater.requires_input(
                        new_text_message(content, context_id=task.context_id, task_id=task.id)
                    )
                    break

                else:
                    # ── Output mutation guard ──────────────────────────────────
                    mutation_result = await self.mutate_guard.validate(content)
                    output_value = mutation_result.output if mutation_result.was_mutated else content

                    logger.info(
                        "Output after mutation guard",
                        extra={
                            "task_id": task.id,
                            "context_id": task.context_id,
                            "was_mutated": mutation_result.was_mutated,
                            "output_length": len(output_value) if output_value else 0,
                        },
                    )
                    # ──────────────────────────────────────────────────────────

                    parts = [Part(text=output_value)]

                    structured_data = item.get("structured_data")
                    if structured_data:
                        metadata["structured_data"] = structured_data

                    await updater.add_artifact(parts, name="result", metadata=metadata)
                    await updater.complete()
                    break

        except Exception as e:
            logger.error(
                "Error while executing agent",
                extra={"task_id": task.id, "error": str(e)},
                exc_info=True,
            )
            raise ServerError(error=InternalError()) from e
```

### How It Works

#### Input Guard — Before Streaming

- `OnFailAction.NOOP` lets all validators run and collects all failures before deciding
- `updater.reject(...)` closes the task cleanly with a terminal rejected status
- `return` after the first failure — no reason to send multiple rejection messages

#### Stream State Machine

| `is_task_complete` | `require_user_input` | A2A State | Action |
|---|---|---|---|
| `False` | `False` | `working` | Status update, keep streaming |
| `False` | `True` | `input_required` | Status update, `final=True`, break |
| `True` | `False` | completed | Run output guard, add artifact, complete |

#### Output Mutation Guard — On Completion

- Only runs on the completed branch — not on working updates or input_required
- guarded by `mutation_result.was_mutated` — we use `mutation_result.output` (a `str`, since `content` is a `str`) when it changed, else fall back to `content`

#### Guard Placement

```
RequestContext
    │
    ▼
Task setup (new_task / existing task)
    │
    ▼
Input guard ──── fails ──▶ reject() + return
    │
    passes
    ▼
agent.stream()
    ├── working        ──▶ TaskState.TASK_STATE_WORKING
    ├── input_required ──▶ requires_input() + break
    └── completed      ──▶ Output mutation guard ──▶ add_artifact + complete
```
