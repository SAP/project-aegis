# Project Aegis

**Standalone guardrail library for A2A agent executors.**

Aegis provides two composable guardrail primitives — `Guard` and `MutationGuard` — with a
fluent `.use()` API. Drop them into any A2A executor to validate inputs and sanitize outputs
without coupling your agent logic to guardrail implementation details.

Aegis ships the **engine, a set of built-in validators, and the validator contract**. Use
the built-ins (see [Validators](validators/index.md)), or write your own by satisfying a
small protocol (or subclassing `BaseValidator` / `BaseMutationValidator`) — see
[Custom Validators](custom-validators.md).

---

## Why Aegis?

Every A2A executor that handles user input or produces output needs guardrails. The alternative
is implementing checks manually in each executor. Here's why a library is better:

The core reason to run guardrails as separate validators — rather than embedding instructions
in your agent's system prompt — is **reliability**. Prompt instructions are suggestions; under
adversarial pressure (prompt injection, jailbreak attempts, multi-turn escalation), a model can
ignore them. Separate validators enforce independently, outside the agent's context window, and
cannot be talked out of doing their job.

Beyond that:

| DIY Approach | Aegis |
|---|---|
| Checks written inline, duplicated per executor | Validators written once, reused across any executor or agent architecture |
| No standard for what "fail" means | `PassedResult` / `FailedResult` with consistent error codes and details |
| Concurrency wiring left to the developer | Concurrent execution handled automatically via `asyncio.gather()` |
| No spans on validation calls | OpenTelemetry span written for every validation, automatically |
| Ad-hoc error handling per check | Centralized `on_fail` — `LOG`, `EXCEPTION`, `NOOP`, or custom callable |
| Detection and mutation logic tangled together | `Guard` for detection, `MutationGuard` for mutation — clean separation |
| Guardrail policy mixed into agent business logic | Guards are defined and composed independently — policy changes don't touch agent code |

For a deeper treatment of why standalone validators outperform prompt-embedded instructions on reliability, reusability, testability, and cost, see [Why Separate Validators?](architecture.md#why-separate-validators)

---

## Two Execution Modes

### Guard — Concurrent Detection

All validators run in parallel. No values are modified. Returns a `GuardResult` you inspect
to decide what to do.

`BlockTopic` and `MaxLength` below are validators you write — subclass
`BaseValidator` (see [Custom Validators](custom-validators.md)):

```python
from aegis import Guard, OnFailAction

input_guard = (
    Guard("input-guard")
    .use(BlockTopic("fraud"), on_fail=OnFailAction.EXCEPTION)
    .use(MaxLength(5000))
)

result = await input_guard.validate(value=user_message)
if result.has_failures:
    for failure in result.failures:
        print(f"{failure.validator_name}: {failure.error_message}")
```

### MutationGuard — Sequential Mutation Pipeline

Validators run in order. When one fails, its `mutate()` is called and the corrected value
is passed to the next validator. Returns a `MutationGuardResult` with the final output.

```python
from aegis import MutationGuard

output_guard = (
    MutationGuard("output-guard")
    .use(EmailRedactor())
    .use(ToneNormalizer())
)

result = await output_guard.validate(agent_response)
final_response = result.output if result.was_mutated else agent_response   # str in → output is a str
```

---

## Install

See [Installation](installation.md) for the different ways to install the package — from
source today, and from PyPI once the first release is published.

---

## Next Steps

- [Getting Started](getting-started.md) — full walkthrough, failure handling, custom validators
- [Architecture](architecture.md) — lifecycle diagram, design principles, Guard vs MutationGuard
- [Cookbook](cookbook.md) — integration recipes for A2A executors
- [Custom Validators](custom-validators.md) — write your own validators against the contract
- [Validators](validators/index.md) — the built-in validators and the contract they implement
