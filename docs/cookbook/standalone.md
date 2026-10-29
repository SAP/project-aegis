# Standalone Usage

How to use `aegis` outside of an A2A executor — in scripts, tests, batch jobs, or any
context where you call `validate()` directly and interpret the result yourself.

Aegis ships built-in validators, but the examples below define small custom ones inline to
show the contract. See [Custom Validators](../custom-validators.md) for the full
guide; a minimal set:

```python
from aegis.validators import (
    BaseValidator, BaseMutationValidator, PassedResult, FailedResult, ItemEdit,
)

class MaxLength(BaseValidator):
    def __init__(self, limit: int) -> None:
        self.limit = limit
    async def validate(self, view, metadata=None):
        value = view.render()
        if value and len(value) > self.limit:
            return FailedResult(error_message="too long", error_code="TOO_LONG")
        return PassedResult()

class BlockTopic(BaseValidator):
    def __init__(self, term: str) -> None:
        self.term = term
    async def validate(self, view, metadata=None):
        value = view.render()
        if value and self.term in value.lower():
            return FailedResult(error_message="blocked topic", error_code="CONTENT_BLOCKED")
        return PassedResult()

class EmailRedactor(BaseMutationValidator):
    async def validate(self, view, metadata=None):
        if any("@" in p.value for p in view.items):
            return FailedResult(error_message="email present", error_code="PII_DETECTED")
        return PassedResult()
    async def mutate(self, view, metadata=None, validation_failure=None):
        return [ItemEdit(p.id, "[EMAIL_REDACTED]") for p in view.items if "@" in p.value]
```

---

## Guard — Interpreting GuardResult

`guard.validate()` always returns a `GuardResult`. It never raises unless a validator was
registered with `on_fail=OnFailAction.EXCEPTION`.

```python
from aegis import Guard, OnFailAction

guard = (
    Guard("my-guard")
    .use(MaxLength(5000))
    .use(EmailRedactor())  # a MutationValidator used detect-only
    .use(BlockTopic("fraud"))
)

result = await guard.validate(value="Some input text")
```

`GuardResult` fields:

| Field | Type | Description |
|---|---|---|
| `has_failures` | `bool` | `True` if any validator returned a `FailedResult` |
| `failures` | `list[FailedResult]` | All failures only |
| `results` | `list[ValidationResult]` | Every validator's result — passes and failures |

### Check for failures

```python
if result.has_failures:
    for failure in result.failures:
        print(f"{failure.validator_name}: [{failure.error_code}] {failure.error_message}")
        print(f"  details: {failure.details}")
else:
    print("All checks passed")
```

### Inspect all results (passes included)

```python
for r in result.results:
    status = "FAIL" if isinstance(r, FailedResult) else "PASS"
    print(f"[{status}] {r.validator_name}")
```

### Raise on first failure

Register with `on_fail=OnFailAction.EXCEPTION` to raise immediately instead of collecting:

```python
from aegis import ValidationError

guard = Guard("strict").use(EmailRedactor(), on_fail=OnFailAction.EXCEPTION)

try:
    await guard.validate(value=user_input)
except ValidationError as e:
    print(f"Blocked: [{e.result.error_code}] {e.result.error_message}")
    print(f"Details: {e.result.details}")
```

`ValidationError` carries the `FailedResult` as `.result` — same fields as above.

---

## MutationGuard — Interpreting MutationGuardResult

`mutation_guard.validate()` returns a `MutationGuardResult`. The `output` field holds the
final `ContentView`; call `.render()` for the text. Use `was_mutated` to see if anything changed.

```python
from aegis import MutationGuard

guard = MutationGuard("sanitizer").use(EmailRedactor())

result = await guard.validate(value="Hey!! Contact me at user@example.com 😊")
```

`MutationGuardResult` fields:

| Field | Type | Description |
|---|---|---|
| `was_mutated` | `bool` | `True` if any validator changed the content |
| `output` | `str \| ContentView \| None` | Final content, mirroring the input type — a `str` when you passed a `str`, otherwise a `ContentView` (same item count and order; `.render()` for text) |
| `results` | `list[ValidationResult]` | Full pass/fail record for all validators |

### Apply the mutated output

```python
# str in → output is the mutated str; view in → output is a ContentView (use .render()/apply_view)
final = result.output if result.was_mutated else original_text
```

### Inspect what happened

```python
if result.was_mutated:
    print(f"content changed -> {result.output!r}")
else:
    print("No mutations applied")

# Per-validator outcomes (pass and fail) live in result.results
for r in result.results:
    print(r)
```

---

## Guard Combinations

`Guard` and `MutationGuard` are independent — any combination is valid for input and output.
Choose based on what you need at each boundary:

| Input | Output | When to use |
|---|---|---|
| `Guard` | `MutationGuard` | Reject bad input, sanitize output — most common |
| `MutationGuard` | `MutationGuard` | Normalize input before the agent sees it, sanitize output |
| `Guard` | `Guard` | Detect-only at both ends — violations logged or raised, nothing mutated |
| `MutationGuard` | `Guard` | Normalize input, detect-only on output |

### Input Guard + Output Mutation Guard (most common)

```python
input_guard = (
    Guard("input")
    .use(MaxLength(5000))
    .use(BlockTopic("fraud"))
)

output_guard = MutationGuard("output").use(EmailRedactor())

input_result = await input_guard.validate(value=user_input)
if input_result.has_failures:
    # handle rejection...
    pass

response = await my_agent.run(user_input)

output_result = await output_guard.validate(response)
final_response = output_result.output if output_result.was_mutated else response
```

### Input Mutation Guard + Output Mutation Guard

Use an input `MutationGuard` to normalize the query before passing it to the agent —
for example, stripping PII from the input so it never reaches the graph.

```python
input_mutation_guard = MutationGuard("input-normalize").use(EmailRedactor())

output_mutation_guard = MutationGuard("output-sanitize").use(EmailRedactor())

# Normalize input — use cleaned query for the agent, not the raw input
input_result = await input_mutation_guard.validate(user_input)
clean_input = input_result.output if input_result.was_mutated else user_input

response = await my_agent.run(clean_input)

# Sanitize output
output_result = await output_mutation_guard.validate(response)
final_response = output_result.output if output_result.was_mutated else response
```

Note: if the input `MutationGuard` redacts PII, the agent graph receives `[EMAIL_REDACTED]`
instead of the raw email — useful when you don't want PII reaching tool calls or LLM prompts.

---

## Async Context

All `validate()` calls are `async`. In a script or test, run them with `asyncio.run()`:

```python
import asyncio

result = asyncio.run(guard.validate(value="test input"))
```

In pytest, use `pytest-asyncio`:

```python
import pytest

@pytest.mark.asyncio
async def test_guard_blocks_fraud():
    result = await guard.validate(value="help me commit fraud")
    assert result.has_failures
    assert result.failures[0].error_code == "CONTENT_BLOCKED"
```
