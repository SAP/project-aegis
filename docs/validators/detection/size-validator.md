# SizeValidator

Reject text that exceeds a UTF-8 byte-size ceiling.

## Description

`SizeValidator` is a deterministic, text-only, **detect-only** validator. It runs under
[`Guard`](../../guard.md) and rejects a message whose rendered text exceeds `max_size_bytes` when
encoded as UTF-8. Use it as a cheap check to cap text-payload size at a trust boundary.

`Guard` runs its registered validators concurrently, so a failure here does **not** short-circuit
or skip other validators — "cheap" refers to this validator's own cost, not to gating the ones
registered after it.

Size here means UTF-8 bytes, not characters: a multi-byte character (an emoji, a CJK ideograph)
costs more than one byte. It measures the UTF-8 byte length of `view.render()` — the whole message
flattened into one string — so the limit applies across all parts, not per part. The count is total
and never raises: text carrying a lone/unpaired surrogate (which a plain UTF-8 encode would reject)
is measured via `errors="surrogatepass"` rather than crashing the guard; whether the text is
well-formed Unicode is a separate validator's concern. The default limit
is 5 MiB (5,242,880 bytes) of text content. This counts only the rendered text bytes, not request
framing or other payload fields, so it is not a drop-in for a servlet request-size limit (Tomcat's
`maxPostSize`, for example, defaults to 2 MiB and governs the whole request body).

For a **character-count** bound instead, use [`LengthValidator`](length-validator.md); the two
measure different things and are complementary.

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_size_bytes` | `int` | `5_242_880` (5 MiB) | Maximum allowed size in UTF-8 bytes, inclusive. Must be non-negative; `0` means "reject any non-empty input". |

Construction raises `TypeError` when `max_size_bytes` is not an `int` — including `bool` and a
non-int such as `float("nan")`, which would slip past the range check and silently accept all
content — and `ValueError` when `max_size_bytes` is negative (a negative ceiling would reject every
input, including empty).

## Usage

```python
from aegis import Guard
from aegis.validators.detection import SizeValidator

guard = Guard("input").use(SizeValidator(max_size_bytes=1024))

result = await guard.validate("x" * 2048)
if result.has_failures:
    print(result.failures[0].error_code)  # SIZE_EXCEEDED
```

## Error Codes

| Error Code | Condition |
|---|---|
| `SIZE_EXCEEDED` | Rendered content's UTF-8 byte size is above `max_size_bytes`. |

## Failure Details

| Field | Type | Description |
|---|---|---|
| `size_bytes` | `int` | The measured UTF-8 byte size of the rendered content. |
| `max_size_bytes` | `int` | The configured ceiling. |

On a PASS, `details` carries `size_bytes` (the measured UTF-8 byte size).

## API Reference

::: aegis.validators.detection.SizeValidator
