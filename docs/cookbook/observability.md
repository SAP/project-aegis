# Observability

Patterns for monitoring guard activity without necessarily blocking requests.

!!! note "OpenTelemetry / auto-instrumentation"
    Full observability guidance — including auto-instrumentation for trace correlation and
    metrics export — is pending. More details will be added once that integration is validated.

---

## Soft Guardrails — Observe Without Blocking

During rollout or for non-critical checks, observe violations without interrupting the flow.
Useful for building a violation baseline before deciding to enforce.

```python
# Option A: LOG action — emits a warning, never raises
# PIIDetector here is a custom validator you write; Aegis also ships built-in ones
guard = Guard("soft").use(PIIDetector(), on_fail=OnFailAction.LOG)

result = await guard.validate(value=user_message)
# Always continues — failures are logged but execution proceeds
if result.has_failures:
    for f in result.failures:
        metrics.increment("pii_detected", tags={"validator": f.validator_name})
```

```python
# Option B: custom callable for structured audit logging
async def record_violation(result, validated_value, metadata):
    logger.warning(
        "Guard violation",
        extra={
            "error_code": result.error_code,
            "error_message": result.error_message,
            "details": result.details,
        },
    )
    await audit_log.record(result)
    # Do NOT raise here — observe-only
    # Add a raise to promote this to a hard block when ready to enforce

guard = Guard("audit").use(
    ContentFilter(blocked_topics=["fraud"]),
    on_fail=record_violation,
)
```

The custom callable can be promoted to a hard block at any point by adding a `raise` —
no other code changes needed.

---

## Log Output Mutations

`out.was_mutated` tells you whether any validator changed the content, and `out.output`
carries the final result — a `str` when you passed a `str`, otherwise a `ContentView`
(call `out.output.render()` for the text). Per-validator outcomes (pass and fail) are in
`out.results`; the failures that triggered a fix are `out.failures`.

```python
out = await output_guard.validate(raw_response)   # str in → out.output is a str

if out.was_mutated:
    logger.info(
        "Output sanitized",
        extra={
            "triggered_by": [f.validator_name for f in out.failures],
            "output": out.output,
        },
    )
    metrics.increment("output_mutations")
```

---

## Inspect All Results, Not Just Failures

`guard_result.results` contains every validator's outcome — passes included. Useful for
confirming that a validator ran even when it didn't fire.

```python
guard_result = await guard.validate(value=query)

for r in guard_result.results:
    logger.debug(
        "Validator result",
        extra={
            "validator": r.validator_name,
            "passed": not isinstance(r, FailedResult),
        },
    )
```
