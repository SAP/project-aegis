# Getting Started

## Installation

See [Installation](installation.md) for setup instructions.

---

## Recommended Path — A2A Wrapper

If you're building an A2A agent with a standard `AgentExecutor`, the wrapper is the fastest path. Configure your guards once in `InputGuardConfig` / `OutputGuardConfig` objects, and the wrapper applies them transparently to every request — rejecting bad input, sanitizing output — without touching your `execute()` logic.

`ContentFilter` below is a built-in validator; `PIIRedactor` is a custom one you write against
the contract. Aegis ships built-in validators (see [Validators](validators/index.md)) and lets
you add your own (see [Custom Validators](custom-validators.md)).

```python
from aegis import Guard, MutationGuard, OnFailAction
from aegis.a2a import GuardedAgentExecutor, InputGuardConfig, OutputGuardConfig
from aegis.validators.detection import ContentFilter
from my_validators import PIIRedactor  # your own validators
from agent_config import BLOCKED_TOPICS

guarded = GuardedAgentExecutor(
    executor=MyExecutor(),
    input_config=InputGuardConfig(
        guard=(
            Guard("input")
            .use(ContentFilter(blocked_topics=BLOCKED_TOPICS), on_fail=OnFailAction.NOOP)
        ),
        on_fail="I'm unable to help with that request.",
    ),
    output_config=OutputGuardConfig(
        mutation_guard=MutationGuard("output").use(PIIRedactor()),
    ),
)
```

Or use the `@guarded` decorator — same config objects, class-level syntax:

```python
from aegis import Guard, MutationGuard, OnFailAction
from aegis.a2a import guarded, InputGuardConfig, OutputGuardConfig
from aegis.validators.detection import ContentFilter
from my_validators import PIIRedactor  # your own validators

@guarded(
    input_config=InputGuardConfig(
        guard=Guard("input").use(ContentFilter(...), on_fail=OnFailAction.NOOP),
        on_fail="I'm unable to help with that request.",
    ),
    output_config=OutputGuardConfig(
        mutation_guard=MutationGuard("output").use(PIIRedactor()),
    ),
)
class MyExecutor(AgentExecutor):
    async def execute(self, context, event_queue):
        query = context.get_user_input()  # mutated input if mutation_guard was set
        ...
```

See [A2A Wrapper](a2a-wrapper.md) for the full reference — config objects, failure handling, pipeline ordering, and telemetry.

---

## Manual Integration — Guard and MutationGuard

When you need full control — custom rejection messages per validator, streaming state machines, structured logging — use the primitives directly.

### Guard

Runs all validators **concurrently** and returns a `GuardResult`. Values are never modified.

```python
from aegis import Guard, OnFailAction
from aegis.validators.detection import ContentFilter
from my_validators import PIIDetector  # your own validator
from agent_config import BLOCKED_TOPICS

input_guard = (
    Guard("input")
    .use(ContentFilter(blocked_topics=BLOCKED_TOPICS), on_fail=OnFailAction.NOOP)
    .use(PIIDetector(), on_fail=OnFailAction.NOOP)
)

result = await input_guard.validate(value=user_message)
if result.has_failures:
    for failure in result.failures:
        print(f"{failure.validator_name}: {failure.error_message}")
```

See [Guard](guard.md) for the full API reference.

### MutationGuard

Runs validators **sequentially**. When a validator fails, its `mutate()` is called and the corrected value is passed to the next validator.

```python
from aegis import MutationGuard
from my_validators import PIIRedactor, ToneNormalizer  # your own validators

output_guard = (
    MutationGuard("output")
    .use(PIIRedactor())
    .use(ToneNormalizer())
)

result = await output_guard.validate(agent_response)
final = result.output if result.was_mutated else agent_response   # str in → output is a str
```

See [MutationGuard](mutation-guard.md) for the full API reference.

---

## Failure Handling

The `on_fail` parameter on each `.use()` call controls what happens when that validator fails:

| Action | Behaviour |
|--------|-----------|
| `OnFailAction.LOG` | Logs a warning (default) |
| `OnFailAction.EXCEPTION` | Raises `ValidationError` |
| `OnFailAction.NOOP` | Does nothing |
| Custom callable | Called with `(result, validated_value, metadata)` |

```python
def notify_slack(result, validated_value, metadata):
    print(f"ALERT: {result.error_message}")

guard = Guard("alerts").use(PIIDetector(), on_fail=notify_slack)
```

---

## Writing a Custom Validator

Validators satisfy a structural protocol — any class with the right methods qualifies.
`BaseValidator` is an optional convenience base class.

```python
from aegis import BaseValidator, PassedResult, FailedResult

class ProfanityFilter(BaseValidator):
    async def validate(self, view, metadata=None):
        value = view.render()
        if "badword" in value.lower():
            return FailedResult(
                error_message="Profanity detected",
                error_code="PROFANITY",
            )
        return PassedResult()

guard = Guard("content").use(ProfanityFilter())
```

For a complete guide including mutation validators, see
[Custom Validators](custom-validators.md).
