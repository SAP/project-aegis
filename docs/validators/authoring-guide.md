# Authoring Guide

This guide is the **end-to-end walkthrough** for adding a validator to Aegis: where the
code, tests, and docs live, how a validator is registered, and the workflow that takes it
from an idea to a shippable component.

It builds on [Custom Validators](../custom-validators.md), which is the **contract
reference** — the protocols, result types, and the detect-vs-mutate rule. Read that first
to learn *what* a validator must implement; read this to learn *where it goes* and *how to
ship it*.

!!! note "Prerequisites"
    Set up your environment first — see the project's `CONTRIBUTING.md` for
    `pip install -e ".[dev]"`, the pre-commit hooks, and the commit-message convention.

---

## Workflow at a glance

Every new validator goes through the same four steps. They lean on a few durable engineering
practices — design before you code, defend against bad input, hide internals:

1. **Research, then spec it** — research is mandatory and comes *first*. Study the domain and
   any standard that governs it *before* you define the contract: a PII redactor, for example,
   takes its definition of "personal data" and its redaction rules from the applicable
   regulation (GDPR and its equivalents), not from intuition. Requirements you assume instead of
   research are the ones that leak in production.

   Turn that research into an explicit, written spec *before* any code — what the validator
   validates, its input/output contract, its edge cases, and what it deliberately leaves out of
   scope. Defining the intent before the implementation (construct-by-intent) is the cheapest
   place to be wrong. Keep the spec a living description — the module/class docstring is a good
   home — and update it as the implementation evolves.
2. **Implement to the spec** — write the validator against the contract (see
   [Custom Validators](../custom-validators.md)), then harden it. Treat every input as hostile
   and barricade it: malformed, empty, and adversarial input are the normal case for a
   validator, not the exception (defensive programming). Keep it to one concern — one validator
   checks one thing — and its public surface minimal: one class, helpers `_`-private
   (information hiding). Conform the signatures and return types exactly, and match the
   folder/file layout below.
3. **Test it thoroughly** — cover the happy path, boundaries, error paths, and malformed input.
   Boundary and error cases are where defects concentrate, and a validator is a security
   surface — miss nothing.
4. **Peer review** — a second pair of eyes before it merges. Include the spec, code, tests,
   and docs in the same PR.

The rest of this page details the layout and conventions each step relies on.

### The contract, in brief

Your validator is a class that implements the methods below — either by subclassing
`BaseValidator` / `BaseMutationValidator`, or by matching the protocol directly. The
signatures are **fixed**: implement them exactly as shown; don't rename parameters or change
the return types. This section is a quick recap only — [Custom Validators](../custom-validators.md)
is the authoritative contract reference, with the full explanation and worked examples.

```python
# Every validator implements validate() — it receives a ContentView (under Guard or MutationGuard):
async def validate(self, view: ContentView, metadata: GuardMetadata | None = None) -> ValidationResult

# Mutation validators additionally implement mutate() — MutationGuard calls it after a
# FailedResult to fix, returning id-addressed edits:
async def mutate(
    self,
    view: ContentView,
    metadata: GuardMetadata | None = None,
    validation_failure: FailedResult | None = None,
) -> list[ItemEdit]
```

- `validate()` (and `mutate()`) receive a `ContentView` — call `view.render()` for the
  flattened text, or iterate `view.items` for per-item / cross-item context. `metadata` is a
  `GuardMetadata` dataclass or `None` (check before accessing).
- `validate()` returns `PassedResult` or `FailedResult`; put extra context in the `details`
  mapping. Every `FailedResult` gets a **stable, specific `error_code`**.
- `mutate()` returns `list[ItemEdit]` — one id-addressed whole-part replacement per part it
  changes; the engine rebuilds over the original parts, preserving count and order.

---

## Where code lives

Validators live under `src/aegis/validators/`, split by what they do:

| Package | For | Runs under | Returns from `validate()` |
|---|---|---|---|
| `src/aegis/validators/detection/` | Detect-only | `Guard` | `PassedResult` / `FailedResult` |
| `src/aegis/validators/mutation/` | Detect **and** fix | `MutationGuard` | same, plus a `mutate()` |

Pick `detection/` if your validator only *reports*; pick `mutation/` if it also *repairs* the
message — but that choice is governed by the
[Detection vs. Mutation rule](../custom-validators.md#detection-vs-mutation-the-rule):
**security concerns (XSS, HTML/tag stripping, injection) are detection-only; mutation is for
non-adversarial cleanup** such as PII redaction or content-quality fixes. (Both are view-based;
a `MutationValidator` is a superset of `Validator`, so it can also run detect-only under
`Guard` — see
[Custom Validators](../custom-validators.md#the-mutationvalidator-protocol).)

### Simple validator — one module

A self-contained validator is a single snake_case module:

```
src/aegis/validators/detection/length_validator.py   ->  class LengthValidator
```

### Complex validator — one package folder

When a validator needs helpers, pattern tables, or prompt templates, make it a **package
folder**. The public class lives in a `_`-prefixed private module and is re-exported from the
folder's `__init__.py`; internal helpers stay `_`-prefixed so the import surface is just the
public class:

```
src/aegis/validators/mutation/input_sanitizer/
├── __init__.py          # re-exports InputSanitizer (the ONLY public symbol)
├── _sanitizer.py        # class InputSanitizer(BaseMutationValidator)
├── _preprocessor.py     # internal helper
└── patterns/            # nested sub-package when helpers group up
    ├── __init__.py
    ├── _xss.py
    └── _sqli.py
```

```python
# input_sanitizer/__init__.py
from aegis.validators.mutation.input_sanitizer._sanitizer import InputSanitizer

__all__ = ["InputSanitizer"]
```

**Rule of thumb:** start with one file; promote to a folder only when helpers appear. Keep the
public surface to the validator class — everything else is `_`-private.

---

## Registering the validator

Aegis does **not** auto-detect validators — you register each one explicitly. For a simple,
broadly useful validator that's a single re-export from its category package:

1. Add your class to the category package's `__init__.py` and its `__all__`:

    ```python
    # src/aegis/validators/detection/__init__.py
    from aegis.validators.detection.length_validator import LengthValidator

    __all__ = ["LengthValidator"]
    ```

2. It's now importable from its **category** package — `detection` or `mutation`:

    ```python
    from aegis.validators.detection import LengthValidator
    ```

    The category segment is deliberate: the import path itself tells the reader whether a
    validator is detect-only (`detection`) or detect-and-fix (`mutation`). Concrete
    validators are **not** aggregated up to the package root — the `aegis` and
    `aegis.validators` roots export only the *contract* (protocols, base classes, result
    types), so importing the contract never pulls in every validator's dependencies.

3. Users register it at runtime with the fluent `.use()` API:

    ```python
    from aegis import Guard
    from aegis.validators.detection import LengthValidator

    guard = Guard("input").use(LengthValidator(max_length=500))
    ```

!!! note "Heavy or niche validators: import from the leaf module"
    Keep the category `__init__.py` re-exports to simple, broadly useful, **non-LLM**
    validators — importing `aegis.validators.detection` eagerly loads everything it
    re-exports. A validator with heavy or optional dependencies (an LLM client, large pattern
    corpora) should **not** be re-exported from the category `__all__`; leave it importable
    only from its own leaf module so the import cost is paid on demand:

    ```python
    from aegis.validators.detection.scope_check import ScopeCheck
    ```

    The registration mechanics are otherwise identical, and it still runs under `Guard` /
    `MutationGuard` the same way.

---

## Error codes and messages

A `FailedResult` carries an `error_code` (callers branch on it), an `error_message` (what the
guard logs on `on_fail=LOG`), and a `details` mapping (the specifics) — the validator's
**public output contract**. Consistency across *all* validators isn't enforced, and that's
fine; but *within one validator* the same failure must always produce the same code, message
shape, and `details` keys.

**Codes** — stable and machine-readable:

- One code per distinct failure, specific (`TOO_LONG`) not generic (`VALIDATION_FAILED`).
  `SCREAMING_SNAKE_CASE`; optionally domain-prefixed (`LENGTH_TOO_LONG`) to stay unique across
  validators.
- **Define each once** as a class constant or `Enum` — the single source of truth shared by
  `validate()`, the tests, and the doc page.
- A released code is a contract: add a new one, never rename or repurpose it.

**Messages** — one fixed template per code; keep the variable specifics in `details`, not baked
into prose. The log already records the validator's name, so don't repeat it. **Never echo a
raw value that could be secret or personal** — report a safe summary (length, count, redacted
marker) in `details`.

```python
from typing import Final

from aegis import BaseValidator, FailedResult, PassedResult


class LengthValidator(BaseValidator):
    TOO_LONG: Final = "TOO_LONG"  # define codes once, in one place

    def __init__(self, max_length: int) -> None:
        self.max_length = max_length

    async def validate(self, view, metadata=None):
        value = view.render()
        length = len(value)  # a safe summary — never the raw value
        if length > self.max_length:
            return FailedResult(
                error_code=self.TOO_LONG,
                error_message=f"Value exceeds the {self.max_length}-character limit.",
                details={"length": length, "limit": self.max_length},
            )
        return PassedResult()
```

Every code gets a row in the doc page's **Error Codes** table and every `details` key a row in
**Failure Details** (see [Documentation](#documentation)), so the contract stays discoverable
without reading the source.

---

## Data files

If a validator needs bundled data (word lists, terminology maps, pattern corpora), **centralize
it** rather than scattering per-validator data directories:

- Put data under `src/aegis/data/` and load it through a single **path-hardened accessor** that
  validates against path traversal and symlink escape (resolve to an absolute path, confirm it
  stays within the data root).
- Never read from a caller-supplied path without validating it.

!!! warning "Bundled data must be clearable"
    Before adding any data file, make sure it is redistributable under a license compatible
    with the project's, and free of confidential or personal (PII) data — data you don't have
    the right to publish can block a release. The library ships no data accessor yet; the
    first data-backed validator introduces it, following this convention.

---

## Tests

Tests mirror the source tree under `tests/validators/`:

```
src/aegis/validators/detection/length_validator.py
tests/validators/detection/test_length_validator.py
```

Folder-based validators mirror the folder:

```
tests/validators/mutation/input_sanitizer/test_sanitizer.py
```

- Tests are async — the suite runs with `asyncio_mode = "auto"` (pytest-asyncio), so just write
  `async def test_...` and drive the validator through a `Guard` / `MutationGuard`.
- Cover, at minimum: a clean value passes, a violating value fails with the **expected
  `error_code`**, boundary conditions, and malformed / empty-string input.

```python
from aegis import Guard
from aegis.validators.detection import LengthValidator

async def test_length_validator_blocks_long_input():
    guard = Guard("test").use(LengthValidator(max_length=5))
    result = await guard.validate("way too long")
    assert result.has_failures
    assert result.failures[0].error_code == "TOO_LONG"

async def test_length_validator_passes_short_input():
    guard = Guard("test").use(LengthValidator(max_length=5))
    result = await guard.validate("ok")
    assert not result.has_failures
```

See [Custom Validators — Testing](../custom-validators.md#testing-validators) for the base
patterns.

---

## Documentation

Every validator ships a doc page in the **same PR** as the code. Two edits:

1. **Add a page** at `docs/validators/detection/<name>.md` or
   `docs/validators/mutation/<name>.md` (hyphenated filename, e.g. `length-validator.md`).
   Use this template:

    ````markdown
    # ValidatorName

    One-line tagline — the single thing this validator guards against.

    ## Description

    A short paragraph orienting the reader: what the validator checks and why, when to reach
    for it, and whether it is detect-only (runs under `Guard`) or detect-and-fix (runs under
    `MutationGuard`). Call out any notable behavior or limits. Keep it general — enough for a
    reader to tell whether this is the validator they need, not a full specification.

    ## Constructor Parameters

    | Parameter | Type | Default | Description |
    |---|---|---|---|
    | `max_length` | `int` | — | Maximum allowed length. |

    ## Usage

    ```python
    from aegis import Guard
    from aegis.validators.detection import ValidatorName

    guard = Guard("input").use(ValidatorName(...))
    result = await guard.validate("...")
    ```

    ## Error Codes

    | Error Code | Condition |
    |---|---|
    | `TOO_LONG` | Value exceeds `max_length`. |

    ## Failure Details

    Fields populated on `FailedResult.details`:

    | Field | Type | Description |
    |---|---|---|
    | `length` | `int` | Actual length of the offending value. |

    ## API Reference

    ::: aegis.validators.detection.ValidatorName
    ````

    The `::: aegis.validators.detection.ValidatorName` line is an
    [mkdocstrings](https://mkdocstrings.github.io/) directive — it auto-renders the class's
    API from its docstrings, so write **Google-style docstrings** on the class and its
    `__init__`. Use the dotted path the validator is actually importable from: the category
    package for re-exported validators (above), or the leaf module
    (`aegis.validators.detection.scope_check.ScopeCheck`) for heavy ones.

2. **Wire it into the nav** in `mkdocs.yml` under the `Validators:` section, and add a row to
   the overview table in [`docs/validators/index.md`](index.md).

---

## Contributor checklist

Before opening the PR:

- [ ] **Researched and spec'd** — domain and any governing standards researched up front; the
      spec captured in the class/module docstring (kept current with the code).
- [ ] Implements the [contract](../custom-validators.md) — correct `validate()` / `mutate()`
      signature and return types.
- [ ] In the right package (`detection/` vs `mutation/`); simple = file, complex = folder with
      `_`-private helpers.
- [ ] Registered in the category `__init__.py` + `__all__`.
- [ ] Every `FailedResult` has a **stable, specific `error_code`** (e.g. `TOO_LONG`, not
      `VALIDATION_FAILED`), defined once as a constant/`Enum`; each code has a consistent
      message and `details` keys (see [Error codes and messages](#error-codes-and-messages)).
- [ ] Tests mirror the source tree and cover boundaries, error paths, and bad input.
- [ ] Doc page added (using the template) + `mkdocs.yml` nav entry + `docs/validators/index.md`
      row.
- [ ] `CHANGELOG.md` updated.
- [ ] `ruff check .`, `ruff format .`, `pyright`, and `pytest` all clean.
- [ ] Peer reviewed.
