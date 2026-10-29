<!--
Optional doc-page template. Use it if your project publishes docs; adapt the structure and any
tooling directives (see the API Reference note below) to your own docs system. If you do not
publish docs, the class docstring is still the validator's living spec — keep that current
regardless.
-->

# ValidatorName

One-line tagline — the single thing this validator guards against.

## Description

A short paragraph orienting the reader: what the validator checks and why, when to reach for it,
and whether it is detect-only (runs under `Guard`) or detect-and-fix (runs under `MutationGuard`).
Call out any notable behavior or limits. Keep it general — enough for a reader to tell whether this
is the validator they need, not a full specification.

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_length` | `int` | — | Maximum allowed length. |

## Usage

```python
from aegis import Guard

from your_package.validators import ValidatorName

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
| `limit` | `int` | Configured maximum allowed length. |

## API Reference

::: your_package.validators.ValidatorName

<!--
Notes for the author (delete before committing):

- The `:::` line is an mkdocstrings directive (for MkDocs projects) — it auto-renders the class
  API from its Google-style docstrings. Use the dotted path the validator is importable from in
  your project. If you use a different docs toolchain, replace this with its equivalent, or just
  hand-write the API section from the docstrings.
- For a mutation validator, validate through `MutationGuard` in Usage and describe the mutation
  behavior in Description.
- Record the new validator wherever your project tracks changes (e.g. a changelog).
-->
