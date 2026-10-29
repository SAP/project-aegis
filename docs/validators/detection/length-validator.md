# LengthValidator

Reject text whose character length falls outside a configured range.

## Description

`LengthValidator` is a deterministic, text-only, **detect-only** validator. It runs under
[`Guard`](../../guard.md) and rejects a message whose rendered character length is below
`min_length` or above `max_length`. Use it as a cheap check that enforces a floor (reject
near-empty input) and/or a ceiling (reject oversized input).

`Guard` runs its registered validators concurrently, so a failure here does **not** short-circuit
or skip other validators — "cheap" refers to this validator's own cost, not to gating the ones
registered after it.

Length here means Unicode code points (`len(str)`), not bytes or grapheme clusters. It measures
`view.render()` — the whole message flattened into one string — so the bound applies across all
parts, not per part. Empty input has length 0 and passes only when `min_length` is 0 (the default).

For a **byte** ceiling (counting UTF-8 bytes rather than characters), use
[`SizeValidator`](size-validator.md) instead; the two measure different things and are
complementary.

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_length` | `int \| None` | `None` | Maximum allowed length, inclusive. `None` means no ceiling. When set, must be a non-negative `int` not less than `min_length`. |
| `min_length` | `int` | `0` | Minimum required length, inclusive. Must be non-negative. |

Construction raises `TypeError` when `min_length` is not an `int`, or when `max_length` is neither
an `int` nor `None` — including `bool` and a non-int such as `float("nan")`, which would slip past
the range checks and silently accept all content — and `ValueError` when either bound is negative or
when `max_length < min_length` (an empty range would reject every input).

## Usage

```python
from aegis import Guard
from aegis.validators.detection import LengthValidator

guard = Guard("input").use(LengthValidator(max_length=100, min_length=1))

result = await guard.validate("x" * 200)
if result.has_failures:
    print(result.failures[0].error_code)  # LENGTH_EXCEEDED
```

## Error Codes

| Error Code | Condition |
|---|---|
| `LENGTH_TOO_SHORT` | Rendered length is below `min_length`. |
| `LENGTH_EXCEEDED` | Rendered length is above `max_length`. |

## Failure Details

| Field | Type | Description |
|---|---|---|
| `length` | `int` | The measured character length of the rendered content. |
| `min_length` | `int` | The configured floor (only on `LENGTH_TOO_SHORT`). |
| `max_length` | `int` | The configured ceiling (only on `LENGTH_EXCEEDED`). |

On a PASS, `details` carries `length` (the measured character length).

## API Reference

::: aegis.validators.detection.LengthValidator
