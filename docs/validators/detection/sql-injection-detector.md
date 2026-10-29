# SqlInjectionDetector

Flag text carrying a known SQL-injection construct.

## Description

`SqlInjectionDetector` is a deterministic, text-only, **detect-only** validator. It runs under
[`Guard`](../../guard.md) and reports a failure when the content carries a known SQL-injection
construct; it never changes the content. Use it as a cheap, non-LLM check at a trust boundary on
text that may reach a query layer.

`Guard` runs its registered validators concurrently, so a failure here does **not** short-circuit
or skip other validators — "cheap" refers to this validator's own cost.

### Obfuscation is decoded before detection

Real payloads hide behind encoding layers, so the rendered content is **preprocessed** before any
pattern runs: invisible/zero-width control characters are stripped, then URL-percent, HTML-entity,
JavaScript `\uXXXX`/`\xXX`, and CSS `\3C` escapes are decoded repeatedly until the text stops
changing (bounded). Without this, a URL-encoded `UNION%20SELECT` would sail past a raw-text
matcher. Each pass can be disabled via the constructor when the input's encoding is fully trusted.

### What it flags

The classic query-breaking constructs — not individual metacharacters (a bare `;` or `'` would
drown callers in false positives on ordinary prose):

- `union_select` — `UNION SELECT` / `UNION ALL SELECT` extraction.
- `stacked_query` — a terminator (`;`) followed by a *complete* statement: `DROP TABLE`,
  `DELETE FROM`, `TRUNCATE TABLE`, `ALTER TABLE/DATABASE`, `CREATE TABLE/…`, `INSERT INTO <t>` with
  a `(` / `VALUES` / `SELECT` / `SET` continuation, `UPDATE <t> SET <col>=`, or `EXEC(`/`EXEC sp_`/
  `EXEC xp_`/`EXEC schema.proc`. Each keyword is anchored to a word boundary and (for the ambiguous
  forms) a SQL continuation, so English prose after a semicolon (`; create your profile`,
  `; delete fromagerie`, `; insert into the box`) is not flagged.
- `tautology` — an always-true equal-operands comparison with `OR` *or* `AND` (boolean-blind):
  `' OR '1'='1`, `0 OR '1'='1'`, `OR 1=1`, `AND 1=1`. A false comparison (`'admin'='guest'`,
  `1=2`, `'1'='10'`) and benign arithmetic (`2 or 3 = 5`) are not flagged.
- `comment_tail` / `block_comment` — SQL comments: a `/*` opener, or a `--`/`#` tail after a quote
  or terminator (`'--`, `; --`).
- `time_based_probe` — blind probes with a numeric argument: `SLEEP(5)`, `pg_sleep(10)`,
  `BENCHMARK(n,…)`, or `WAITFOR DELAY`. (A numeric arg keeps `sleep (now)` prose from matching.)

Every pattern is linear (no nested quantifier that can backtrack), so the validator adds no ReDoS
surface of its own.

### The safety ceiling — read before you rely on this

This validator is **defense-in-depth, not a primary control**. The authoritative SQL-injection
defense is parameterized queries (prepared statements) — never string-concatenated SQL. A passed
result means "no *known* injection marker found", never "provably safe".

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `strip_invisible_chars` | `bool` | `True` | Strip zero-width/control characters before matching. |
| `decode_obfuscation` | `bool` | `True` | Decode URL/HTML/JS/CSS encoding layers before matching. |
| `max_input_chars` | `int \| None` | `None` | Optional resource valve. When set and the rendered text exceeds it, the text is rejected with `SQLI_INPUT_TOO_LARGE` rather than preprocessed and scanned. Off by default. |

Detection runs on `view.render()` — the whole flattened `ContentView` — so a construct split
across an item boundary (`1 UNION` + `SELECT …`) is still caught, since `render()` joins items with
a newline (matched by `\s`).

## Usage

```python
from aegis import Guard
from aegis.validators.detection import SqlInjectionDetector

guard = Guard("input").use(SqlInjectionDetector())

result = await guard.validate("1 UNION SELECT password FROM users")
if result.has_failures:
    print(result.failures[0].error_code)  # SQLI_DETECTED
```

## Error Codes

| Error Code | Condition |
|---|---|
| `SQLI_DETECTED` | The rendered content carries a known SQL-injection construct (after decoding). |
| `SQLI_INPUT_TOO_LARGE` | `max_input_chars` is set and the rendered text exceeds it. |

## Failure Details

`SQLI_DETECTED` locates the first construct (the raw matched text is never echoed):

| Field | Type | Description |
|---|---|---|
| `match_offset` | `int` | Start position of the earliest construct, into the decoded/stripped text that was scanned — **not** the raw input, since decoding shifts offsets. |
| `match_length` | `int` | Length of the matched construct. |
| `construct` | `str` | The construct class: `union_select`, `stacked_query`, `tautology`, `block_comment`, `comment_tail`, or `time_based_probe`. |

`SQLI_INPUT_TOO_LARGE` carries:

| Field | Type | Description |
|---|---|---|
| `length` | `int` | The rendered text length that exceeded the limit. |
| `limit` | `int` | The configured `max_input_chars`. |

## API Reference

::: aegis.validators.detection.SqlInjectionDetector
