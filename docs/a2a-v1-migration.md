# Aegis: a2a-sdk v0.3 → v1.0 migration

> Scope note: Aegis is a **server-side wrapper library**. It wraps `AgentExecutor`,
> `EventQueue`, and `RequestContext`, and transforms `Part`/`Message`/`Artifact`
> objects. It builds **no** servers, clients, or `AgentCard`s. So most of the
> upstream migration guide does not touch Aegis code — it touches *your consumers'*
> apps. This doc separates the two.

## TL;DR — what actually breaks Aegis

Two changes account for ~all the work:

1. **Pydantic → Protobuf types.** `Part(root=TextPart(...))`, `part.root`,
   `isinstance(part.root, TextPart)`, and every `.model_copy(update=...)` call
   stop working. This is the core of `_utils.py`, plus clones in `context.py`
   and `event_queue.py`.
2. **`a2a.utils` → `a2a.helpers`**, and `new_agent_text_message` no longer exists.

Everything else (enum renames, one import move) is mechanical. AgentCard, server
setup, client, and push-notification changes **do not apply to Aegis** — see the
"Not Aegis's problem" section so you can tell consumers what *they* must change.

---

## 1. Blast radius by file

| File | What breaks | Severity |
|---|---|---|
| `src/aegis/a2a/_utils.py` | `part.root`, `isinstance(part.root, TextPart)`, `Part(root=...)`, `part.root.model_copy(...)`, `import TextPart` | **High** — core projection layer |
| `src/aegis/a2a/event_queue.py` | `Part(root=TextPart(...))`, `event.model_copy`, `event.artifact.model_copy`, `import TextPart` | **High** |
| `src/aegis/a2a/context.py` | `context.message.model_copy(...)`, `from a2a.utils import get_message_text` | **High** |
| `src/aegis/a2a/executor.py` | `from a2a.utils import new_agent_text_message, new_task`, `TaskState.rejected`, `TaskUpdater` args | **Medium** |
| `pyproject.toml` | version pin `a2a-sdk>=0.3.20,<0.4` | trivial |
| `tests/**` | `Part(root=TextPart(...))`, `Role.user`, `TaskState.*`, `FilePart/DataPart/FileWithBytes` | mechanical, high volume |

---

## 2. The Part/Message model change (the hard part)

### v0.3 (Pydantic `RootModel` union)
```python
from a2a.types import Part, TextPart
Part(root=TextPart(text="hi"))           # wrap, then access via .root
isinstance(part.root, TextPart)          # discriminate by wrapper type
part.root.text                           # read text
part.root.model_copy(update={"text": x}) # copy via Pydantic
```

### v1.0 (Protobuf flat union)
```python
from a2a.types import Part
Part(text="hi")                          # flat — no wrapper, no .root
part.HasField("text")                    # discriminate via HasField()
part.text                                # read text
# copy: protobuf has no model_copy — see below
```

### Concrete rewrites

**`_utils.py :: build_view`** — the type discriminator changes from `isinstance` to `HasField`:
```python
# v0.3
items = [
    ContentItem(id=_part_id(i), type=ContentType.TEXT, value=part.root.text)
    for i, part in enumerate(parts) if isinstance(part.root, TextPart)
]
# v1.0
items = [
    ContentItem(id=_part_id(i), type=ContentType.TEXT, value=part.text)
    for i, part in enumerate(parts) if part.HasField("text")
]
```

**`_utils.py :: apply_view`** — the copy-with-update. Protobuf has **no `model_copy`**.
Options (pick one, verify against the shipped SDK):
```python
# Option A — copy the proto, mutate the field in place (protobuf idiom)
def _copy_with_text(part, text):
    new = type(part)()
    new.CopyFrom(part)
    new.text = text
    return new
# Option B — if a2a.helpers exposes a Part text constructor, rebuild:
#   Part(text=text)   # only safe when the part carries nothing but text
```
`build_view`/`apply_view` only ever address **text** parts today and preserve
non-text parts untouched. Option B (`Part(text=...)`) *looks* viable because of
that, but **prefer Option A (`CopyFrom`)**: Aegis is slated to support file/data
parts in future, and Option B silently drops any sibling field on the part
(metadata, and eventually file/data payloads on mixed parts). `CopyFrom`-then-mutate
is future-proof; `Part(text=...)` bakes in the text-only assumption you're planning
to remove. **Verify against the real v1.0 `Part` proto before committing** — this
is the one place the upstream guide is thin on server-side detail.

**`event_queue.py`**
```python
# v0.3
parts = [Part(root=TextPart(text=resolved))]                              # line ~197
return event.model_copy(update={"artifact":
        event.artifact.model_copy(update={"parts": parts})})              # line ~164
# v1.0
parts = [Part(text=resolved)]
new = type(event)(); new.CopyFrom(event)
del new.artifact.parts[:]; new.artifact.parts.extend(parts)               # proto repeated-field write
return new
```

**`context.py`**
```python
# v0.3
self._guarded_message = context.message.model_copy(update={"parts": mutated_parts})
# v1.0
msg = type(context.message)(); msg.CopyFrom(context.message)
del msg.parts[:]; msg.parts.extend(mutated_parts)
self._guarded_message = msg
```

> These `CopyFrom` + repeated-field patterns are protobuf standard. The exact
> field names (`parts`, `artifact`, `text`) should match the v1.0 protos; confirm
> once against the installed package.

---

## 3. Helper / util import moves

| v0.3 | v1.0 | Used in |
|---|---|---|
| `from a2a.utils import get_message_text` | `from a2a.helpers import get_message_text` | `context.py:5` |
| `from a2a.utils import new_task` | `from a2a.helpers import new_task` | `executor.py:11`, tests |
| `from a2a.utils import new_agent_text_message` | **removed** — use `new_message` / `new_text_message` | `executor.py:11,381` |

**`new_agent_text_message` has no drop-in replacement.** v0.3 signature was
`new_agent_text_message(text, context_id, task_id)`. In v1.0:
```python
from a2a.helpers import new_message   # role defaults to ROLE_AGENT
# was: new_agent_text_message(error_message, task.context_id, task.id)
# now (verify param names against helpers signature):
new_message([Part(text=error_message)], task_id=task.id, context_id=task.context_id)
# or new_text_message(text=error_message, ...) if it accepts task/context ids
```

---

## 4. Enum renames (mechanical)

Only two enums are used in Aegis: `TaskState` and `Role`.

| v0.3 | v1.0 |
|---|---|
| `TaskState.rejected` | `TaskState.TASK_STATE_REJECTED` |
| `TaskState.completed` | `TaskState.TASK_STATE_COMPLETED` |
| `Role.user` | `Role.ROLE_USER` |
| `Role.agent` | `Role.ROLE_AGENT` |

Locations: `executor.py:381` (`TaskState.rejected`); tests use `TaskState.completed/rejected`
and `Role.user` throughout `tests/integration/*` and `tests/unit/test_guarded_context.py`.

---

## 5. Server-internal types — VERIFY, not covered by upstream guide

The upstream guide documents the **client** and **app-setup** surface. Aegis lives
on server internals the guide barely mentions. Confirm these still exist / same shape
in v1.0 before trusting the migration:

- `a2a.server.agent_execution.RequestContext` — Aegis subclasses it and reads
  `._params`, `.task_id`, `.context_id`, `.current_task`, `.related_tasks`,
  `.call_context`, `.message`. **`GuardedContext` is the biggest risk** if the
  base constructor signature changed.
- `a2a.server.events.{Event, EventQueue}` — `GuardedEventQueue` proxies these.
- `a2a.server.tasks.TaskUpdater(event_queue, task_id, context_id)` and
  `.update_status(state, message, final=True)` — used in `_reject_task`.
- `TaskArtifactUpdateEvent`, `Artifact`, `Task`, `TaskStatusUpdateEvent` field
  access (`event.artifact.parts`, `task.context_id`, `task.id`).

The client guide shows `StreamResponse` wrapping `artifact_update`/`status_update`
on the **client** side; server-side event types may keep their own shape. Don't
assume — check the installed `a2a.server.events`.

---

## 6. Not Aegis's problem (tell your consumers)

These upstream v1.0 changes are **app-level** — Aegis never touches them, but your
docs/examples/README should point consumers at the upstream guide for:

- `DefaultRequestHandler(..., agent_card=...)` now required
- `A2AStarletteApplication` etc. removed → `create_jsonrpc_routes()` / `create_rest_routes()` / `create_agent_card_routes()`
- `AgentCard` restructure (`supported_interfaces`, `AgentInterface`, modes moved)
- `create_client()` replaces `A2AClient`/`ClientFactory`
- `send_message()` returns `AsyncIterator[StreamResponse]`
- `ClientConfig.push_notification_config` singular
- v0.3 compat flag (`enable_v0_3_compat=True`) if consumers run mixed fleets

`docs/getting-started.md`, `docs/installation.md`, and `docs/a2a-wrapper.md` likely
show consumer app setup — audit those for the removed wrapper classes.

---

## 7. Backward compatibility — supporting both SDKs from one Aegis

> **Two different "v0.3 compat" ideas — don't conflate them:**
> - **Upstream `enable_v0_3_compat=True`** (§6) = a v1.0 *server* accepting v0.3
>   *wire clients*. App-level. Not aegis. Consumers set it on their route factories.
> - **This section** = aegis-the-library working whether the installed `a2a-sdk`
>   is 0.3 **or** 1.0, so existing 0.3 agents don't break when Aegis adds 1.0
>   support. This is the one that matters for a library shipped to many agents.

### Why a single codebase can support both

Aegis's entire version-sensitive surface is ~6 primitive operations, and they're
already funneled through one seam (`build_view`/`apply_view`
+ two clones). Everything else — the speculative executor, the holdback buffer,
config dataclasses — is version-agnostic and doesn't change.
Isolate those 6 primitives behind a compat module and the rest of Aegis never
imports a version-specific symbol.

**Critical constraint:** Aegis src must **not** do top-level
`from a2a.types import TextPart` anywhere — that import *fails to load* on v1.0
(the type is gone). All version-specific imports move into `_compat.py`.

### The shim: `src/aegis/a2a/_compat.py`

Detect the version once with **EAFP import guards** (more robust than parsing the
version string — handles pre-releases/forks):

```python
# src/aegis/a2a/_compat.py
from __future__ import annotations
from a2a.types import Part

try:
    # v0.3: wrapper types exist
    from a2a.types import TextPart
    _V03 = True
except ImportError:
    TextPart = None            # type: ignore
    _V03 = False

if _V03:
    def is_text_part(part) -> bool:            return isinstance(part.root, TextPart)
    def part_text(part) -> str:                return part.root.text
    def make_text_part(text: str) -> Part:     return Part(root=TextPart(text=text))
    def copy_text_part(part, text: str) -> Part:
        return Part(root=part.root.model_copy(update={"text": text}))
    def copy_with_parts(obj, parts):           return obj.model_copy(update={"parts": parts})
    def copy_artifact_event(event, parts):
        return event.model_copy(update={"artifact": event.artifact.model_copy(update={"parts": parts})})
    from a2a.types import TaskState
    TASK_STATE_REJECTED = TaskState.rejected
    from a2a.utils import get_message_text, new_task, new_agent_text_message
else:
    def is_text_part(part) -> bool:            return part.HasField("text")
    def part_text(part) -> str:                return part.text
    def make_text_part(text: str) -> Part:     return Part(text=text)
    def copy_text_part(part, text: str) -> Part:
        new = type(part)(); new.CopyFrom(part); new.text = text; return new
    def copy_with_parts(obj, parts):
        new = type(obj)(); new.CopyFrom(obj)
        del new.parts[:]; new.parts.extend(parts); return new
    def copy_artifact_event(event, parts):
        new = type(event)(); new.CopyFrom(event)
        del new.artifact.parts[:]; new.artifact.parts.extend(parts); return new
    from a2a.types import TaskState
    TASK_STATE_REJECTED = TaskState.TASK_STATE_REJECTED
    from a2a.helpers import get_message_text, new_task
    from a2a.helpers import new_message as _new_message
    def new_agent_text_message(text, context_id=None, task_id=None):
        return _new_message([Part(text=text)], task_id=task_id, context_id=context_id)
```

Then the seam files import from the shim instead of `a2a` directly:

- **`_utils.py`**: `build_view` uses `is_text_part`/`part_text`; `apply_view` uses
  `copy_text_part`.
- **`event_queue.py`**: `Part(root=TextPart(...))` → `make_text_part(...)`;
  the `event.model_copy(...)` line → `copy_artifact_event(event, parts)`.
- **`context.py`**: `context.message.model_copy(...)` → `copy_with_parts(msg, parts)`;
  `get_message_text` from shim.
- **`executor.py`**: `new_task`/`new_agent_text_message`/`TASK_STATE_REJECTED` from shim.

**Future data-type support:** Aegis handles only text parts today, but file/data
parts are on the roadmap. When they land, the shim is the natural extension point —
add `is_file_part`/`part_file`/`make_data_part` etc. alongside the text primitives,
version-branched the same way. Keep the text-only `Part(text=...)` shortcut *out*
of `copy_text_part` (use `CopyFrom`, per §2) so mixed parts carrying both text and
a payload don't lose data once non-text support arrives. `build_view`/`apply_view`
will grow to project non-text `ContentItem` types, but the seam stays the seam.

`GuardedContext`'s base-class construction (§5) is the one thing a shim can't fully
paper over if the `RequestContext.__init__` signature diverged between versions —
verify that constructor on both SDKs; if it differs, the shim needs a
`build_request_context(...)` primitive too.

### Declaring the dependency

Keep the pin **wide**, not split:
```toml
# pyproject.toml — one package, both majors
dependencies = ["a2a-sdk>=0.3.20,<2"]
```
Add a CI matrix testing against `a2a-sdk==0.3.*` and `a2a-sdk==1.*` so the shim
can't silently rot on one side. The full test suite already exercises the seam —
run it twice, once per pinned major.

### When to drop it

The shim is deliberate, tracked debt. Remove the `_V03` branch (and widen the pin
to `>=1.0.0,<2`) once your fleet's 0.3 agents have migrated — the code collapses
back to the v1.0-only forms in §2–4. Mark it:

```python
# TODO: dual-SDK shim — delete the _V03 branch once no consumer pins a2a-sdk <1.0
```

### Alternatives (if the shim's runtime branching bothers you)

- **Optional extras** — `aegis[a2a03]` vs `aegis[a2a1]` with version-gated pins.
  Simpler code, but consumers must pick the right extra; the import-time branch
  problem (top-level `TextPart` import) still forces a `_compat.py` anyway.
- **Major-version split** — Aegis `1.x` → a2a 0.3, Aegis `2.x` → a2a 1.0. Cleanest
  code (no branching), but two maintenance lines and consumers must bump Aegis in
  lockstep. Only worth it if the shim surface grows well past these 6 primitives.

**Recommendation:** the shim. The surface is tiny and already isolated; one
package that "just works" on both is the least friction for a fleet mid-migration.

---

## 8. Suggested migration order

1. Bump pin: `a2a-sdk>=1.0.0,<2` in `pyproject.toml`; `uv sync`. Let imports fail loudly.
2. Fix `_utils.py` first (`build_view`/`apply_view`) — everything routes through it.
   Nail the Part copy semantics (§2) and keep the Part cardinality guarantee.
3. Fix `context.py` and `event_queue.py` clones (`CopyFrom` + repeated fields).
4. Fix `executor.py` imports/enums/`new_agent_text_message`.
5. Update helper imports repo-wide (`a2a.utils` → `a2a.helpers`).
6. Migrate tests mechanically (`Part(root=TextPart)` → `Part(text=)`, enum renames,
   `FilePart/DataPart` → flat `Part(file=)/Part(data=)`).
7. Run the suite. The integration tests in `tests/integration/test_a2a_pipeline.py`
   and `test_guarded_*` are the real acceptance gate.

**Unverified assumptions flagged above** (§2 Part copy, §5 server internals) are the
only places this could go sideways — resolve those against the installed SDK first.

---

## 9. As-built — verified against a2a-sdk 1.1.2

Everything above §9 was written speculatively (hence the "verify against the installed
SDK" caveats). This section records what the migration actually took once run against
the real SDK. **Where §9 conflicts with §1–§8, §9 wins.**

### Shape shipped: dual-SDK shim (§7), lock pinned to 1.x

- `src/aegis/a2a/_compat.py` — the shim. `pyproject.toml` stays `a2a-sdk>=0.3.20,<2`;
  **`uv.lock` pins 1.1.2**, so the default dev/CI environment is v1.
- `tests/_a2a_compat.py` — a test-side twin (version-agnostic object builders/readers)
  so the whole suite runs on both majors. `pythonpath = ["tests"]` in the pytest config
  lets `tests/unit/` and `tests/integration/` do `from _a2a_compat import ...`. It is a
  helper module, not a test case — hence it lives at the `tests/` root, not in a subfolder.
- CI: the suite runs in a dedicated test workflow with two legs — the primary job runs on the
  locked 1.x environment with coverage; a second job pins `a2a-sdk==0.3.*` as a **pass/fail-only**
  0.3 leg so the shim can't silently rot on the 0.3 side.

### Corrections — the guide got these wrong

| Guide said | Reality on 1.1.2 |
|---|---|
| §3: `from a2a.helpers import new_task` (drop-in) | **Wrong.** v1 `new_task(task_id, context_id, state, ...)` is a *different* function. The 0.3 `new_task(message)` equivalent is **`new_task_from_user_message(message)`**. |
| §2/§5: `TaskUpdater.update_status(state, msg, final=True)` | **`final` was removed** (v1 sig: `update_status(state, message, timestamp, metadata)`), and `TaskStatusUpdateEvent` has no `final` field. Use **`TaskUpdater.reject(message)`** for the reject path — it exists in both majors with the same signature, so no shim branch is needed. |
| §5: `GuardedContext` base ctor is "the biggest risk" | **Non-issue.** `RequestContext.__init__` did change (v1 requires `call_context`; `request` is now `SendMessageRequest`), but `GuardedContext` already passes everything by keyword incl. `call_context`, and the `_params` instance attr exists in both majors. No change needed. |
| §2: discriminate via `part.HasField("text")` | Correct for text. But `HasField("file")` **raises** — v1 `Part` has no `file` oneof member (its fields: `text, raw, url, data, filename, media_type`). File parts do not map 1:1 from 0.3. |

### Additions — SDK changes the guide didn't cover

- **`MessageSendParams` → `SendMessageRequest`.** v1 removed `MessageSendParams`;
  `RequestContext(request=...)` now takes a `SendMessageRequest` (fields:
  `tenant, message, configuration, metadata`). Any test/harness that builds a context
  must branch on this.
- **Data-part construction.** `Part(data={...})` fails on v1 — the `data` field is a
  `google.protobuf.Value`, not a dict/Struct. Build via `Part(data=Value(struct_value=...))`
  and read via `dict(part.data.struct_value)`.
- **Protobuf copy semantics.** Assigning a sub-message (`SendMessageRequest(message=m)`)
  **copies** it — there is no shared reference as on Pydantic 0.3. Identity assertions
  (`x is original_msg`) that passed on 0.3 fail on protobuf; assert against the object the
  context exposes (`ctx.message`) or on content, not on the external input object.
- **`EventQueue()` construction deprecated on 1.x** — see below.
- **v1 is real and stable** — 1.0.0 … 1.1.2 are published; this migration is verified
  against 1.1.2, not a pre-release.

### `EventQueue()` direct instantiation — deprecated on 1.x (owned test subclass)

v1 makes `a2a.server.events.EventQueue` an abstract producer interface. Constructing
`EventQueue()` directly emits a `DeprecationWarning` and redirects to `EventQueueLegacy`;
its docstring states *"executors should accept it and not construct one"* and warns that
both the redirect and `EventQueueLegacy` "will be removed in a future release." It offers
two sanctioned ways to run an executor outside the framework: *"write a custom subclass or
use `EventQueueLegacy` (deprecated)."*

- **Shipped Aegis is unaffected — permanently.** Aegis is an executor *wrapper*: it accepts
  the framework-provided `event_queue` and wraps it in `GuardedEventQueue`; it never
  constructs an `EventQueue`. (`grep 'EventQueue(' src/` → none — only type annotations.)
  No consumer hits this warning through aegis.
- **Only the test suite triggered it**, because tests run an executor outside the framework
  and need a real queue to feed `execute()` and read events back — exactly the deprecated
  path.
- **Decision: own a concrete subclass in the shim.** `tests/_a2a_compat.py::OwnedEventQueue`
  is a ~15-line `EventQueue` subclass (the docstring's first sanctioned path). It implements
  only what the suite exercises: the producer `enqueue_event`, the `close`/`is_closed` that
  `GuardedEventQueue` delegates to the wrapped queue, and `dequeue_nowait` to read events back.
  `event_queue()` returns it and `dequeue()` takes it, so the type checker enforces that only
  this queue is drained, with no unchecked cast. This is warning-free **and** depends on
  neither `EventQueueLegacy` (whose eventual removal would otherwise break test collection at
  import time, even under the `a2a-sdk<2` pin) nor any third-party queue internals. It works
  unchanged on both majors (`EventQueue` exists in each), and replaces the earlier
  `dequeue_event(no_wait=True)` branch the 0.3 drain path used. The ~100 `DeprecationWarning`s
  are gone, not silenced with a `filterwarnings` ignore.
