# Custom Validators

This page is the **contract reference**: *what* a validator must implement to run under
`Guard` or `MutationGuard`. It applies equally whether you write a validator for your own
application or contribute one to aegis.

Validators in Aegis are **protocol-based**: any class with the right methods qualifies.
No inheritance required. `BaseValidator` and `BaseMutationValidator` are optional convenience
base classes — they provide a default `name` property and enforce the interface via ABC,
but you can satisfy the protocol directly without subclassing anything.

!!! tip "Contributing a validator to the repo?"
    This page covers *what* to implement. For *where* the code, tests, and docs live, how a
    validator is registered, and the release workflow, see the
    [Authoring Guide](validators/authoring-guide.md).

---

## Detection vs. Mutation: the rule

**A validator's category is a security decision, not a convenience choice.** Decide it before
you write any code:

- **Security concerns are detection-only.** A validator that exists to stop an attack —
  prompt/instruction injection, SQL/command injection, XXE, SSRF payloads — must only *detect*
  and reject the request. It runs under `Guard`, and the caller blocks on failure (see
  [Guards](guard.md)); it must **never** try to repair the content and let it continue. For most
  of these the real control lives elsewhere anyway — parameterized queries / an ORM for SQL, a
  parser configured to disable external entities for XXE — so a validator that *sanitizes* the
  payload is security theater; detection is the only honest job it has.
- **Exception — sanitize-at-sink when the dangerous shape is also legitimate input.** A few
  concerns can't be rejected because the thing that looks like an attack is also valid,
  user-authored content you must preserve — so detect-and-reject isn't an available move and
  output-encoding would destroy the meaning. For these, neutralizing in place *is* the correct
  control. It qualifies only when **all three** hold: (a) the content is legitimate input the
  user is entitled to send, not an attack wearing a disguise; (b) you use a **vetted allowlist**
  transform scoped to one specific sink — never a hand-rolled denylist; and (c) you apply it *at
  that sink*, not as a general "now it's clean" stamp. Safe for one sink is not safe for a
  different sink (SQL, a shell, an LLM prompt); **never sanitize-then-forward across sinks**.

    The one concern that qualifies today is **HTML for display** (WYSIWYG, markdown→HTML, rich
    comments): our [`HtmlSanitizer`](validators/mutation/html-sanitizer.md) wraps
    [`nh3`](https://nh3.readthedocs.io/) (Rust `ammonia`), the server-side equivalent of OWASP's
    DOMPurify — an allowlist sanitizer over a real HTML5 parse tree, applied at the HTML render
    sink. A future validator earns this exception only by meeting the same three tests, not by
    analogy to HTML.
- **Mutation is for non-adversarial cleanup only.** Reach for `MutationGuard` / `mutate()` when
  the issue is a quality or compliance concern on otherwise-trusted content — PII redaction,
  tone/casing/whitespace normalization, formatting fixes, stripping a banned-but-benign token.
  The content is not an attack; you are tidying it.

Why security must fail closed instead of mutating:

- **Sanitizing an attack and forwarding it gives a false sense of safety.** One bypass you
  didn't model now flows downstream stamped "cleaned." Blocking fails closed; mutating fails
  open.
- **Mutating attacker-controlled input hands the attacker a second move** — they craft input
  designed to survive your transform into a still-dangerous shape.
- The safe response to an attack is to **refuse** it, not to negotiate with it.

| Concern | Example | Category |
|---|---|---|
| XSS / script injection (not for display) | `<script>…`, `onerror=` in non-HTML content | **Detection only** |
| HTML for display | user-authored rich text rendered as HTML | **Mutation OK** (allowlist-sanitize at the render sink, vetted lib) |
| Prompt / instruction injection | "ignore previous instructions" | **Detection only** |
| SQL / command injection, XXE, SSRF | `' OR 1=1`, external-entity DTD | **Detection only** (control lives at the sink) |
| PII | emails, phone numbers, national IDs | Mutation OK (redact) |
| Content quality | tone, casing, whitespace, formatting | Mutation OK (normalize) |
| Banned benign tokens | a disallowed brand name | Mutation OK (strip) |

> **Litmus test:** if you can't honestly describe the job as "tidying trusted content," it is a
> security concern — build it detection-only. The sole exception is allowlist-sanitizing HTML
> *at the render sink* with a vetted library; if you're tempted to sanitize an attack payload
> for any other sink, you're negotiating with it — reject instead.

---

## The Validator Protocol

For use with `Guard` (detection only):

```python
from typing import Protocol, runtime_checkable
from aegis import GuardMetadata
from aegis.validators import ContentView, ValidationResult

@runtime_checkable
class Validator(Protocol):
    @property
    def name(self) -> str: ...

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult: ...
```

Any class satisfying this protocol can be passed to `Guard.use()`.

---

## Writing a Detection Validator

The recommended approach is to subclass `BaseValidator` — it provides a default `name`
(the class name) and makes the protocol requirement explicit via ABC. But a plain class
works just as well.

### Using BaseValidator (recommended)

```python
from aegis import BaseValidator, PassedResult, FailedResult

class ProfanityFilter(BaseValidator):
    """Blocks requests containing known profanity."""

    PROFANITY = {"badword1", "badword2"}

    async def validate(self, view, metadata=None):
        value = view.render()
        found = [w for w in self.PROFANITY if w in value.lower()]
        if found:
            return FailedResult(
                error_message=f"Profanity detected: {found}",
                error_code="PROFANITY_DETECTED",
                details={"matches": found},
            )
        return PassedResult()
```

### Protocol-only (no subclassing)

```python
from aegis import PassedResult, FailedResult

class ProfanityFilter:
    """Satisfies the Validator protocol without subclassing."""

    @property
    def name(self) -> str:
        return "ProfanityFilter"

    async def validate(self, view, metadata=None):
        value = view.render()
        if "badword" in value.lower():
            return FailedResult(
                error_message="Profanity detected",
                error_code="PROFANITY_DETECTED",
            )
        return PassedResult()
```

Both work identically in `Guard.use()`.

---

## The MutationValidator Protocol

For use with `MutationGuard` (detect + fix). Like the detection `Validator`, it's
**view-based** (`validate(view)`); it additionally implements `mutate(view)`, which returns
id-addressed edits the engine applies while guaranteeing part count and order are preserved.

```python
from typing import Protocol, runtime_checkable
from aegis import GuardMetadata
from aegis.validators import ContentView, FailedResult, ItemEdit, ValidationResult

@runtime_checkable
class MutationValidator(Protocol):
    @property
    def name(self) -> str: ...

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult: ...

    async def mutate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
        validation_failure: FailedResult | None = None,
    ) -> list[ItemEdit]: ...
```

`mutate()` is only called when `validate()` returns a `FailedResult`; it receives that
failure as `validation_failure`.

### What a mutator sees and returns

- **`ContentView`** — the message's content items, in order. Iterate `view.items` (each is a
  `ContentItem` with a stable `id`, a `type` — a `ContentType` enum, `ContentType.TEXT` today —
  and its `value`), or call `view.render()` to get all the text joined with newlines for
  cross-part context.
- **`ItemEdit(id, value)`** — a whole-item replacement addressed by `id`. Return one per item
  you want to change; items you don't address are left untouched. Returning edits for every
  item is equally valid — an edit that repeats an item's existing value is a no-op — so a
  partial list (only the ids you changed) and a full list produce identical output. Edits are
  keyed by `id` over the original items, so order doesn't matter and a repeated `id` takes its
  last value.

The engine rebuilds the view from your edits over the *original* items, so item count and
order are preserved by construction — an item you don't address keeps its value. An edit whose
`id` doesn't match an item raises `MutationError`. (Under an A2A agent, the view only ever
exposes text items, and the `guarded` wrapper carries non-text parts such as images through
untouched — see [the A2A wrapper](a2a-wrapper.md) for how a validator plugs in.)

!!! tip "A mutation validator also works detect-only in `Guard`"
    Both protocols are view-based, so a `MutationValidator` is a superset of `Validator`:
    the same class can be registered with `Guard` for detection only (its `validate` runs;
    `mutate` is never called) or with `MutationGuard` to also fix. One class covers both.

---

## Writing a Mutation Validator

Detect on the view, then return one `ItemEdit` per part you want to rewrite:

```python
from aegis import BaseMutationValidator, PassedResult, FailedResult, ItemEdit

class UppercaseNormalizer(BaseMutationValidator):
    """Detects ALL CAPS text parts and normalizes each to sentence case."""

    def _is_caps(self, text: str) -> bool:
        return text.isupper() and len(text) > 3

    async def validate(self, view, metadata=None):
        if any(self._is_caps(p.value) for p in view.items):
            return FailedResult(error_message="Text is all uppercase", error_code="ALL_CAPS")
        return PassedResult()

    async def mutate(self, view, metadata=None, validation_failure=None):
        return [
            ItemEdit(p.id, p.value.capitalize())
            for p in view.items
            if self._is_caps(p.value)
        ]
```

Usage:

```python
from aegis import ContentItem, ContentType, ContentView, MutationGuard

guard = MutationGuard("normalizer").use(UppercaseNormalizer())

# Single string in, single string out (convenience for the common one-item case):
result = await guard.validate("HELLO WORLD")
if result.was_mutated:
    print(result.output)   # "Hello world" — str in → output is a str

# Multiple parts: pass a ContentView, read the mutated view back.
view = ContentView(items=(
    ContentItem(id="p0", type=ContentType.TEXT, value="HELLO"),
    ContentItem(id="p1", type=ContentType.TEXT, value="world"),
))
result = await guard.validate(view)
if result.was_mutated:
    print(result.output.render())      # flattened text, parts joined by newline
    for item in result.output.items:   # same ids, count, and order as the input
        print(item.id, item.value)
```

`output` mirrors the input type: a `str` when you passed a `str`, otherwise a `ContentView`
with the same items — same ids, count, and order — as the input. Read it with
`result.output.render()` for the flattened text or `result.output.items` for per-part access.
When a validator runs under an A2A agent, the `guarded` wrapper maps the transport's parts to
and from this view for you, so a validator never touches transport types.

### Using cross-part context

Because `mutate()` receives the whole message, a mutator can make decisions that need
surrounding parts — the reason the contract is view-based. Read `view.render()` for the
joined text and still return whole-part edits:

```python
class ToneRewriter(BaseMutationValidator):
    def __init__(self, llm):
        self.llm = llm

    async def validate(self, view, metadata=None):
        return FailedResult(error_message="tone check", error_code="TONE")

    async def mutate(self, view, metadata=None, validation_failure=None):
        context = view.render()            # all parts, in order — cross-part context
        edits = []
        for p in view.items:
            rewritten = await self.llm.rewrite(p.value, context=context)
            if rewritten != p.value:
                edits.append(ItemEdit(p.id, rewritten))
        return edits
```

You own the model call, batching, and latency budget; the engine only guarantees the
message shape. Whole-part rewrite means rewrite *each* part in place — you cannot merge two
parts into one or split one into two (cardinality is fixed).

---

## Testing Validators

### Basic pattern

```python
from aegis import Guard

async def test_profanity_filter_blocks_badwords():
    guard = Guard("test").use(ProfanityFilter())
    result = await guard.validate("This contains badword1")
    assert result.has_failures
    assert result.failures[0].error_code == "PROFANITY_DETECTED"

async def test_profanity_filter_passes_clean_text():
    guard = Guard("test").use(ProfanityFilter())
    result = await guard.validate("This is perfectly fine")
    assert not result.has_failures
```

### Mutation validators

Drive them through `MutationGuard`; assert on `result.output` — for a `str` input it is the
mutated `str` directly, and for a `ContentView` input use `result.output.render()` for the
flattened text or `result.output.items` for per-part access. Item count and order are
guaranteed, so they are worth asserting too:

```python
from aegis import ContentItem, ContentType, ContentView, MutationGuard

async def test_uppercase_normalizer_single_part():
    guard = MutationGuard("test").use(UppercaseNormalizer())
    result = await guard.validate("HELLO WORLD")
    assert result.was_mutated
    assert result.output == "Hello world"   # str in → output is a str

async def test_uppercase_normalizer_preserves_item_count_and_order():
    view = ContentView(items=(
        ContentItem(id="p0", type=ContentType.TEXT, value="HELLO"),
        ContentItem(id="p1", type=ContentType.TEXT, value="already fine"),
    ))
    guard = MutationGuard("test").use(UppercaseNormalizer())
    result = await guard.validate(view)
    assert result.was_mutated
    assert [i.id for i in result.output.items] == ["p0", "p1"]  # count + order preserved
    assert result.output.items[0].value == "Hello"             # caps item normalized
    assert result.output.items[1].value == "already fine"      # untouched, in place
```

---

## Best Practices

- **Use specific error codes** — prefer `PROFANITY_DETECTED` over `VALIDATION_FAILED`. Callers use codes programmatically.
- **Populate details** — include enough context for callers to understand what failed and why.
- **Handle `None` metadata gracefully** — `metadata` can be `None`. Both `Validator` and `MutationValidator` receive a `ContentView` (`view.items` may be empty but is never `None`); call `view.render()` for the flattened text. Check `metadata` before accessing.
- **Return edits only for parts you changed** — in `mutate()`, emit a `ItemEdit` per part you actually rewrite; skipping unchanged parts keeps the diff minimal and the intent clear. Every `ItemEdit.id` must match a text part in the view, or the engine raises `MutationError`.
- **Keep `validate()` and `mutate()` independent** — `mutate()` receives the `FailedResult` from `validate()` via `validation_failure=`; use that to know what to fix rather than re-running detection logic.
