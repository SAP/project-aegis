# PromptLeakageCheck

Reject agent output that reproduces the system prompt, configured secrets, or canary tokens.

## Description

`PromptLeakageCheck` is a deterministic, text-only, **detect-only** validator. It runs under
[`Guard`](../../guard.md) as an **output guardrail** — the last text-security gate before a
response is returned, persisted, or exposed — and rejects output that reproduces protected
instruction text. It answers OWASP `LLM07:2025` System Prompt Leakage's "inspect outputs for
compliance" mitigation.

It compares the candidate output against a protected corpus you configure:

- **Canary tokens** — unique, high-entropy markers seeded into the system prompt. Any appearance
  in the output is definitive leakage, so they are matched with no minimum-length floor.
- **Sensitive fragments** — protected instruction text, matched as an exact or normalized
  substring, and (opt-in) fuzzily for near-verbatim copies.
- **The system prompt itself** — opt-in via `use_metadata_system`, read from
  `GuardMetadata.system` at call time and split into lines.

If the term "canary" is new, this example makes the difference concrete. Suppose your system
prompt is:

```text
You are a Python code reviewer. Never reveal the internal escalation code.
```

Here `Never reveal the internal escalation code.` is a **fragment** — real instruction text that
already exists in the prompt and must not resurface in the output. Now suppose you also plant a
unique marker in the prompt purely as a tripwire:

```text
You are a Python code reviewer. Never reveal the internal escalation code. canary-7f3a9c2b-4e1d-9a02
```

`canary-7f3a9c2b-4e1d-9a02` is a **canary** — an arbitrary identifier with no meaning of its own
that never occurs in normal output, so any appearance in the output is unambiguous proof the prompt
leaked. In short: a fragment protects text you already have; a canary is bait you add specifically
to detect a leak, with near-zero false positives.

Normalization is **off by default** (opt in with `normalize=True`): with it off, the output and the
protected material are matched as-is, with no folding. `normalize` controls folding only — it does
not disable fuzzy matching, which you enable separately with `similarity_threshold`. This mirrors
the engine's off-by-default `Guard(normalize=...)` and keeps matching predictable. When enabled, it
folds cheap evasions — case changes, added whitespace, zero-width/invisible characters, and
NFKC-compatibility variants such as full-width letters — by normalizing both the output and the
protected material before matching. These folds are script-safe: they never map homoglyphs, so
genuine non-Latin text is not rewritten to ASCII.

Reach for it when you have a **known** protected reference to check against. It is not a semantic
judge, and by design it does **not** catch:

- paraphrase, translation, or other semantic disclosure;
- confusable look-alike substitution, Base64/hex encoding, or a Caesar shift (documented residual
  gaps, addressed by future additive hardening);
- leakage dripped across separate turns, or exfiltration through a tool argument.

Each of those belongs to a different single-concern validator. A PASS certifies only that none of
the configured deterministic conditions matched — **never** that the prompt is secret. Per OWASP, a
system prompt should hold no security-critical secret in the first place.

On a leak the `FailedResult` carries only safe summary metadata (a reference index, match type,
similarity, and offsets); it **never** echoes the matched text, the protected text, or any snippet,
so the validator cannot re-leak the secret into your logs or results.

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sensitive_fragments` | `list[str] \| None` | `None` | Protected instruction fragments. Matched as exact/normalized substrings at any length, and fuzzily when fuzzy is enabled. |
| `canaries` | `list[str] \| None` | `None` | Unique tokens whose appearance in output is definitive leakage. Matched with no minimum-length floor. |
| `use_metadata_system` | `bool` | `False` | When `True`, also protect `GuardMetadata.system`, split into lines. Only lines whose normalized length reaches `min_fragment_length` are used. Absent metadata at call time contributes nothing (no error). |
| `similarity_threshold` | `float \| None` | `None` | Enables fuzzy (near-verbatim) matching when set. A float in `[0.0, 1.0]` (recommended 0.80–0.90); `None` disables fuzzy matching. |
| `min_fragment_length` | `int` | `15` | Minimum normalized length for fuzzy eligibility and for an auto-derived `metadata.system` line to be used. Explicit fragments are still matched exactly at any length. |
| `case_sensitive` | `bool` | `False` | When `True`, skip case-folding. Only takes effect when `normalize=True` (case-folding is one step of normalization). |
| `normalize` | `bool` | `False` | Opt in to normalize both sides before matching — NFKC, strip zero-width/format characters, collapse whitespace, and case-fold (unless `case_sensitive`). Off by default: matching is fully literal unless enabled. |
| `max_input_chars` | `int \| None` | `None` | Optional resource valve. When set and the rendered output exceeds it, reject with `PROMPT_LEAKAGE_INPUT_TOO_LARGE` rather than scan. |

Construction raises `ValueError` when no protected source is configured, when any fragment or
canary is empty/whitespace-only, when `similarity_threshold` is outside `[0.0, 1.0]`, or when
`min_fragment_length` is negative.

The non-default numeric values are starting points — calibrate `min_fragment_length` and
`similarity_threshold` against your own benign and adversarial output before production.

## Usage

Configure **at least one** protected source — `sensitive_fragments`, `canaries`, or
`use_metadata_system=True`. Each is optional on its own, so any one of them is a complete
configuration; you do not need a canary token unless you seed one. Passing none raises `ValueError`
at construction, because a validator with nothing to compare against would silently pass every
output. The example below uses all three together:

```python
from aegis import Guard, GuardMetadata
from aegis.validators.detection import PromptLeakageCheck

guard = Guard("output").use(
    PromptLeakageCheck(
        canaries=["a1b2c3d4-e5f6-canary-token"],
        sensitive_fragments=["Never reveal the internal escalation code to the user."],
        use_metadata_system=True,
    )
)

result = await guard.validate(
    "Sure — my instructions say: Never reveal the internal escalation code to the user.",
    metadata=GuardMetadata(system="You are a helpful assistant. ..."),
)
if result.has_failures:
    print(result.failures[0].error_code)  # PROMPT_LEAKAGE_DETECTED
```

The smallest useful setup protects a single fragment, with no canary token:

```python
guard = Guard("output").use(
    PromptLeakageCheck(
        sensitive_fragments=["Never reveal the internal escalation code to the user."],
    )
)

result = await guard.validate("Sure — Never reveal the internal escalation code to the user.")
if result.has_failures:
    print(result.failures[0].error_code)  # PROMPT_LEAKAGE_DETECTED
```

When more than one condition matches, the single reported verdict follows a fixed precedence:
`PROMPT_LEAKAGE_INPUT_TOO_LARGE` → `PROMPT_LEAKAGE_CANARY` → `PROMPT_LEAKAGE_DETECTED` →
`PROMPT_LEAKAGE_SUSPECTED`. Within a class, the earliest offending offset wins.

## Error Codes

| Error Code | Condition |
|---|---|
| `PROMPT_LEAKAGE_CANARY` | A configured canary token appears in the output. |
| `PROMPT_LEAKAGE_DETECTED` | A protected fragment or system-prompt segment appears as an exact or normalized substring. |
| `PROMPT_LEAKAGE_SUSPECTED` | Fuzzy matching is enabled and an output window is at/above `similarity_threshold`. |
| `PROMPT_LEAKAGE_INPUT_TOO_LARGE` | `max_input_chars` is set and the rendered output exceeds it. |

## Failure Details

Fields populated on `FailedResult.details`. They are safe summaries only — never the matched or
protected text. `match_offset` / `match_length` index the rendered candidate (the raw text for
`match_type="exact"`, the normalized text for `normalized` / `fuzzy`); since the raw text is never
echoed, offsets are diagnostic only.

| Field | Type | Description |
|---|---|---|
| `source` | `str` | Which corpus matched — `"canary"`, `"fragment"`, or `"metadata_system"`. |
| `reference_index` | `int` | Opaque index of the matched item within its source list. |
| `match_type` | `str` | `"exact"`, `"normalized"`, or `"fuzzy"` (absent on `PROMPT_LEAKAGE_CANARY`). |
| `match_offset` | `int` | Character offset of the match in the scanned text. |
| `match_length` | `int` | Length of the matched span (absent on `PROMPT_LEAKAGE_CANARY`). |
| `similarity` | `float` | Fuzzy similarity ratio (only on `PROMPT_LEAKAGE_SUSPECTED`). |
| `length` | `int` | Length of the rejected output (only on `PROMPT_LEAKAGE_INPUT_TOO_LARGE`). |
| `limit` | `int` | The configured `max_input_chars` (only on `PROMPT_LEAKAGE_INPUT_TOO_LARGE`). |

On a PASS, `details` carries `fragments_checked` (count of protected items compared), `sources`
(the active corpus categories), and `fuzzy_enabled` (whether fuzzy matching is on).

## API Reference

::: aegis.validators.detection.PromptLeakageCheck
