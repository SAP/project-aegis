# MutationGuard

`MutationGuard` is a sequential mutation pipeline. Validators run in registration order — when
one returns `FailedResult`, its `mutate()` is called; the resulting edits are applied and the
rebuilt message is passed to the next validator. Use it to clean up content before passing it
on: PII redaction, tone correction, normalization.

Aegis ships the `HtmlSanitizer` mutation validator (see [Validators](validators/index.md));
you can also write your own by subclassing `BaseMutationValidator` with a `validate()`
(detect) and `mutate()` (fix). Mutators are **view-based**: they see the message as a
`ContentView` and return id-addressed `ItemEdit`s. A tiny custom one that redacts emails:

```python
import re
from aegis import MutationGuard
from aegis.validators import BaseMutationValidator, PassedResult, FailedResult, ItemEdit

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

class EmailRedactor(BaseMutationValidator):
    async def validate(self, view, metadata=None):
        if any(EMAIL.search(p.value) for p in view.items):
            return FailedResult(error_message="email present", error_code="PII_DETECTED")
        return PassedResult()

    async def mutate(self, view, metadata=None, validation_failure=None):
        return [
            ItemEdit(p.id, EMAIL.sub("<EMAIL>", p.value))
            for p in view.items if EMAIL.search(p.value)
        ]

output_guard = MutationGuard("output").use(EmailRedactor())
```

See [Custom Validators](custom-validators.md) for the full guide.

---

## Guard vs MutationGuard

| | Guard | MutationGuard |
|---|---|---|
| Execution | Concurrent | Sequential |
| Mutation | Never | When validator returns `FailedResult` |
| Return type | `GuardResult` | `MutationGuardResult` |
| `on_fail` per validator | Yes | No — always runs `mutate()` on failure |
| Typical use | Input validation, compliance, scope checking | Output sanitization, PII redaction, tone correction |

---

## Creating a MutationGuard

```python
guard = MutationGuard("my-guard")  # name used in telemetry spans and logs
```

> **Text normalization is intentionally absent.** `MutationGuard` operates
> on raw text — there is no `normalize` flag. Mutation validators match
> against whatever text the caller hands them and emit byte-equivalent
> output for the matched spans only.
>
> If you need homoglyph / variation-selector / NFKC bypass protection on
> the mutation path, pair a normalizing detection `Guard` upstream that
> blocks the request before it reaches the mutation pipeline:
>
> ```python
> # Detect-and-block first; on pass, mutate the (untouched) original.
> input_guard = Guard("input", normalize=True).use(
>     BlockSocials(),
>     on_fail=OnFailAction.EXCEPTION,
> )
> sanitizer = MutationGuard("sanitize").use(EmailRedactor())
>
> await input_guard.validate(value=user_text)
> result = await sanitizer.validate(value=user_text)
> ```

---

## Registering Validators

Use the fluent `.use()` API. Validators run in registration order.

```python
guard = (
    MutationGuard("output-sanitizer")
    .use(EmailRedactor())    # runs first
    .use(ToneNormalizer())   # sees the output of EmailRedactor
)
```

`.use()` validates the protocol at registration time — it will raise `TypeError` immediately if
the object does not satisfy the `MutationValidator` protocol (requires both `validate()` and
`mutate()` methods).

---

## validate()

```python
result = await guard.validate(value="Some agent response text")
```

For A2A parts, build a `ContentView` with the adapter helper (the A2A wrapper does this for you):

```python
from aegis.a2a._utils import build_view

result = await guard.validate(build_view(artifact.parts))
```

---

## MutationGuardResult

`validate()` returns a `MutationGuardResult`:

| Field | Type | Description |
|---|---|---|
| `was_mutated` | `bool` | `True` if any validator changed the content |
| `output` | `str \| ContentView \| None` | Final content, **mirroring the input type**: a `str` when you called `validate` with a `str`, otherwise a `ContentView` (same item count and order as the input). For the view case, call `.render()` for the flattened text |
| `results` | `list[ValidationResult]` | All validation results (pass and fail) |

```python
result = await guard.validate(agent_response)   # str in → str out

if result.was_mutated:
    final = result.output                  # already the mutated string (str in → str out)
else:
    final = agent_response                 # original unchanged
```

For A2A parts, the adapter helpers in `aegis.a2a._utils` convert to and from the view:
`build_view` projects text parts into a `ContentView` (so a mutator sees the whole message via
`view.render()`), and `apply_view` writes the mutated `output` back over the **original**
parts — so `[text, image, text]` stays `[text', image, text']`, with non-text parts
(`DataPart`, `FilePart`) passed through untouched in place. Item count and order are preserved.

```python
from aegis.a2a._utils import apply_view, build_view

result = await guard.validate(build_view(artifact.parts))   # view in → output is a ContentView
if result.was_mutated:
    artifact = artifact.model_copy(update={"parts": apply_view(artifact.parts, result.output)})
```

The A2A executor / event-queue wrappers do this for you on the input and output stages. A
mutator sees all parts together, so context-sensitive mutation (e.g. tone rewriting that
depends on neighbouring parts) works.

---

## Sequential Execution

Validators run in the order they were registered. Each validator sees a fresh `ContentView` of
the current parts — so a later validator sees the earlier one's edits:

```
build_view(parts)
  │
  ├── ValidatorA.validate(view)
  │       ├── PassedResult → no edits, parts unchanged
  │       └── FailedResult → ValidatorA.mutate(view) → edits → rebuild → parts_a
  │
  └── ValidatorB.validate(view of parts_a)   ← sees A's output
          ├── PassedResult → no edits
          └── FailedResult → ValidatorB.mutate(view) → edits → rebuild → parts_b (final)
```

- A validator's `mutate()` is only called when its own `validate()` returns `FailedResult`.
- Validators that return `PassedResult` apply no edits; the message is unchanged for that step.
- `output` (a `ContentView`; call `.render()` for the single-text case) reflects the message after all mutations.

---

## Detect-only vs detect-and-fix

Both `Validator` and `MutationValidator` are **view-based** (`validate(view)`), so a
`MutationValidator` is a superset of `Validator`: the same class can be registered with
`Guard` for detect-only use (its `validate` runs; `mutate` is never called) or with
`MutationGuard` to also fix. See [Custom Validators](custom-validators.md).
