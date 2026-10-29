# Guard

`Guard` is a pure validation engine. All registered validators run **concurrently** on each
`validate()` call — no value is ever modified. Use it to decide whether to proceed with a
request: accept or reject content as-is.

Aegis ships built-in detection validators (see [Validators](validators/index.md)); you can
also write your own by subclassing `BaseValidator` (or satisfying the `Validator` protocol).
A tiny custom one:

```python
from aegis import Guard, OnFailAction
from aegis.validators import BaseValidator, PassedResult, FailedResult

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

input_guard = (
    Guard("input")
    .use(MaxLength(5000), on_fail=OnFailAction.NOOP)
    .use(BlockTopic("fraud"), on_fail=OnFailAction.NOOP)
)
```

See [Custom Validators](custom-validators.md) for the full guide.

---

## Creating a Guard

```python
guard = Guard("my-guard")  # name is used in telemetry spans and logs
```

### Opt-in text normalization

For homoglyph / variation-selector / NFKC bypass protection, construct the
guard with `normalize=True`:

```python
guard = Guard("input", normalize=True)
```

When `normalize=True`, the guard applies (NFKC fold → variation-selector
strip → homoglyph→ASCII map) **once** to `value` and to the text of any
text part in `parts`, then fans out the canonical form to every validator.
Validators see the normalized text for matching; non-text parts pass
through untouched.

The original `value` / `parts` arguments are **never mutated** and the
normalized form does **not** escape via `GuardResult` — on pass, the caller
forwards the untouched original to the agent.

```python
text = "check ѕocials please"  # Cyrillic ѕ (U+0455)
guard = Guard("input", normalize=True).use(BlockTopic("socials"))
result = await guard.validate(value=text)
# result.has_failures == True   — homoglyph variant blocked
# text still == "check ѕocials please"  — caller's original is intact
```

Default is `normalize=False`. Leave it off for multilingual content where
NFKC + homoglyph fold would corrupt legitimate non-ASCII text, and for
guards built around raw-text validators (e.g. length or byte-size checks)
where you want the unaltered byte counts.

`normalize=True` is a convenience alias for the engine profile. For finer
control, pass a `NormalizationConfig` with per-strategy toggles instead of a
bool — e.g. to NFKC-fold and case-fold without the non-Latin-corrupting
homoglyph step:

```python
from aegis import Guard, NormalizationConfig

guard = Guard("input", normalize=NormalizationConfig(unicode_form="NFKC", casefold=True))
```

> **Note:** `MutationGuard` does not have a `normalize` flag — its
> mutation validators always operate on raw text. Pair a normalizing
> detection `Guard` upstream of a `MutationGuard` when you need both
> homoglyph defence *and* sanitization.

---

## Registering Validators

Use the fluent `.use()` API. Each call registers a validator and returns the guard for chaining.

```python
guard = (
    Guard("input-guard")
    .use(ValidatorA(), on_fail=OnFailAction.EXCEPTION)
    .use(ValidatorB(), on_fail=OnFailAction.LOG)
    .use(ValidatorC())  # defaults to LOG
)
```

`.use()` validates the protocol at registration time — it will raise `TypeError` immediately if
the object does not satisfy the `Validator` protocol.

---

## validate()

```python
result = await guard.validate("Some user input text")  # a plain string
```

For A2A messages, build a `ContentView` with the adapter helper (the A2A wrapper does this for you):

```python
from aegis.a2a._utils import build_view

result = await guard.validate(build_view(context.message.parts))
```

`validate` accepts a `str` or a `ContentView`; validators receive the view (call
`view.render()` for the flattened text, or iterate `view.items`).

---

## GuardResult

`validate()` returns a `GuardResult`:

| Field | Type | Description |
|---|---|---|
| `results` | `list[ValidationResult]` | All results — one per registered validator |
| `has_failures` | `bool` | `True` if any validator returned `FailedResult` |
| `failures` | `list[FailedResult]` | Filtered list of only the failing results |

```python
result = await guard.validate(value=user_message)

if result.has_failures:
    for failure in result.failures:
        print(f"[{failure.validator_name}] {failure.error_message}")
        print(f"  error_code: {failure.error_code}")
        print(f"  details:    {failure.details}")
```

---

## on_fail per Validator

Each `.use()` call accepts an `on_fail` parameter that controls what happens when that
validator returns `FailedResult`. This fires inside `validate()` before the result is returned
to the caller.

| Action | Behaviour |
|---|---|
| `OnFailAction.LOG` | Logs a warning with validator name and error message (default) |
| `OnFailAction.EXCEPTION` | Raises `ValidationError` immediately — `validate()` does not return |
| `OnFailAction.NOOP` | Does nothing — result is collected silently |
| Custom callable | Called with `(result, validated_value, metadata)` — side effects only |

```python
# EXCEPTION — hard stop, raises ValidationError
guard.use(BlockTopic("secrets"), on_fail=OnFailAction.EXCEPTION)

# NOOP — collect failures, decide later from GuardResult
guard.use(BlockTopic("fraud"), on_fail=OnFailAction.NOOP)

# Custom callable — log to your system, then continue
def send_alert(result, validated_value, metadata):
    logger.warning("Validator %s failed: %s", result.validator_name, result.error_message)

guard.use(MaxLength(5000), on_fail=send_alert)
```

!!! tip
    Use `OnFailAction.NOOP` when you want all validators to run and collect *all* failures before
    deciding what to do. Use `OnFailAction.EXCEPTION` for an immediate hard stop.

---

## Concurrent Execution

All validators registered on a `Guard` run simultaneously via `asyncio.gather()`. This means:

- **No value chaining** — every validator receives the same original input. A later validator
  cannot see an earlier validator's side effects.
- **All validators always run** — there is no short-circuit on the first failure (unless
  `on_fail=EXCEPTION` raises).
- **Order of results** matches registration order, regardless of which validator finishes first.

```
validate(value)
  │
  ├── ValidatorA.validate()  ──┐
  ├── ValidatorB.validate()  ──┤── asyncio.gather() ──► GuardResult
  └── ValidatorC.validate()  ──┘
```

If you need mutation or sequential execution, use [MutationGuard](mutation-guard.md) instead.
