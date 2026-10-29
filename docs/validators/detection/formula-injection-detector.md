# FormulaInjectionDetector

Flag input that would begin a spreadsheet formula if written into a CSV/TSV cell (CSV injection).

## Description

`FormulaInjectionDetector` is a deterministic, text-only, **detect-only** validator. It runs under
[`Guard`](../../guard.md) and reports a failure when a content item, written into a
spreadsheet-imported cell (CSV, TSV, and the like), would begin a formula and execute when the file
is opened — `=cmd|'/C calc'!A0`, `=HYPERLINK(...)`. It never changes the content. Reach for it on
input or model output that could later be exported to a spreadsheet.

"CSV injection" (CWE-1236 / OWASP) is the common alias. The check is about a *formula initiator* at
a cell's start, not about CSV structure, so the validator is named for the payload rather than the
transport; CSV **structural** injection (forging columns/rows) is a separate concern.

`Guard` runs its registered validators concurrently, so a failure here does **not** short-circuit or
skip other validators.

### Obfuscation is decoded before detection

Real payloads hide behind encoding layers, so each item is **preprocessed** before inspection:
invisible/zero-width control characters are stripped, then URL-percent, HTML-entity, JavaScript
`\uXXXX`/`\xXX`, and CSS `\3C` escapes are decoded repeatedly until the text stops changing
(bounded). Without this, `%3Dcmd` or `&#61;cmd` would hide the leading `=`. Preprocessing does
**not** NFKC-fold, so the full-width initiators `＝＋－＠` are matched explicitly. Both passes can be
disabled via the constructor when the input's encoding is fully trusted.

### What it flags

For each cell candidate the validator decodes obfuscation, skips one optional leading structural `"`
and any leading padding (spaces, NBSP, zero-width, …), then inspects the **effective start**:

- `ascii` — a leading `=`, `+`, `-`, or `@` (CWE-1236 / OWASP). The default safety floor; narrowable
  via `ascii_triggers`.
- `fullwidth` — a leading full-width initiator `＝＋－＠` (OWASP), matched explicitly.
- `control` — a leading TAB, CR, or LF (OWASP). A control prefix is itself a trigger, so it is never
  treated as padding.

Any leading trigger flags — there is **no** numeric, function-name, or handle exception (no universal
safe-suffix grammar exists), so `-5`, `+1 650 555 0100`, and `@mention` fail by default. A leading
`'` (the spreadsheet neutralizer) passes. On a hit, the `formula_shape` detail names a recognized
high-impact shape (`dde_pipe_bang`, `hyperlink`, `dde`) so a caller can *raise* severity on it.

`formula_shape` is a **best-effort lexical hint, not a formula parser**. A recognized shape can
over-match a look-alike token inside a string literal (`="not really HYPERLINK("`). A value of
`unknown` means none of the three recognized shapes matched — it is **not** a safety signal: other
dangerous formulas, including exfiltration functions like `=WEBSERVICE(...)` and `=IMPORTXML(...)`,
report `unknown`. Use a recognized shape to raise severity; never treat `unknown` as safe. The marker
never changes the verdict, which is already a failure.

All patterns are linear (no nested quantifier that can backtrack), so the validator adds no ReDoS
surface of its own.

### The safety ceiling — read before you rely on this

This validator is **defense-in-depth, not a primary control**. The authoritative fix is
neutralization at the CSV serializer, using a consumer-specific text neutralizer such as a leading
`'` where the opening application supports it. Ordinary CSV quoting and escaping are **not** enough:
they keep the field structurally intact, but a quoted value like `"=1+1"` still evaluates as a
formula when the file is opened. A guardrail cannot know whether the text will ever become a
spreadsheet cell; a passed result means "no leading formula initiator under this policy", never
"safe in every spreadsheet".

On general prose the strict policy's false-positive rate is high — `-`, `+`, and `@` begin ordinary
text constantly. This is the accepted default cost; narrow `ascii_triggers` (e.g. to `"="`) for
prose-heavy deployments, or use `formula_shape` to raise severity on a recognized command shape.

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `ascii_triggers` | `str` | `"=+-@"` | Class-A initiators to flag, drawn from `=+-@`. Narrow it (e.g. `"="`) to cut the false-positive cost on prose. Must be non-empty and contain only those four characters. |
| `detect_fullwidth` | `bool` | `True` | Flag the full-width initiators `＝＋－＠` (class F). |
| `detect_control_prefix` | `bool` | `True` | Flag a leading TAB/CR/LF (class C). |
| `skip_leading_padding` | `bool` | `True` | Skip leading padding to the effective start before matching, so a padded trigger (`"   =SUM(...)"`) is still caught. |
| `skip_leading_quote` | `bool` | `True` | Skip one leading structural `"` before matching, so a quoted `"=cmd...` is still caught. |
| `detect_formula_shape` | `bool` | `True` | Compute the `formula_shape` severity marker (DDE / HYPERLINK) on a hit. |
| `strip_invisible_chars` | `bool` | `True` | Strip zero-width/control characters before matching. |
| `decode_obfuscation` | `bool` | `True` | Decode URL/HTML/JS/CSS encoding layers before matching. |
| `max_input_chars` | `int \| None` | `1048576` | Resource valve (1 MiB of characters by default). When the rendered view exceeds it, the input is rejected with `FORMULA_INJECTION_INPUT_TOO_LARGE` rather than preprocessed and scanned. Pass `None` to lift the cap when the input size is already trusted. |

Detection runs **per `ContentItem`**, not on the whole `view.render()`: a formula initiator is
position-sensitive (it must be a cell's *first* character), so each item is treated as one cell
candidate. A trigger at the start of any item is caught; a formula-looking substring *inside* an item
(`note: =1+1`) is not, and an embedded newline (`text\n=1+1`) is interior, not a new cell.

## Usage

```python
from aegis import Guard
from aegis.validators.detection import FormulaInjectionDetector

guard = Guard("input").use(FormulaInjectionDetector())

result = await guard.validate("=cmd|'/C calc'!A0")
if result.has_failures:
    print(result.failures[0].error_code)  # FORMULA_INJECTION_DETECTED
```

## Error Codes

| Error Code | Condition |
|---|---|
| `FORMULA_INJECTION_DETECTED` | A content item's effective start is a formula initiator (after decoding, quote/padding skip). |
| `FORMULA_INJECTION_INPUT_TOO_LARGE` | `max_input_chars` is set and the rendered view exceeds it. |

## Failure Details

`FORMULA_INJECTION_DETECTED` reports the earliest offending item (the raw input and matched cell are
never echoed):

| Field | Type | Description |
|---|---|---|
| `trigger_class` | `str` | The trigger class: `ascii`, `fullwidth`, or `control`. |
| `trigger` | `str` | The fixed trigger enum: `EQUALS`, `PLUS`, `MINUS`, `AT`, `FULLWIDTH_EQUALS`/`FULLWIDTH_PLUS`/`FULLWIDTH_MINUS`/`FULLWIDTH_AT`, or `TAB`/`CR`/`LF`. |
| `match_offset` | `int` | Effective-start position, as a count of Unicode code points into the decoded item text (a trigger is a single code point, so no length is reported). |
| `padding_skipped` | `int` | Number of leading padding code points skipped to reach the effective start. |
| `item_index` | `int` | Index of the offending item in `view.items`. |
| `formula_shape` | `str` | Names a recognized high-impact shape — `dde_pipe_bang`, `hyperlink`, or `dde` — to raise severity on; best-effort lexical match (can over-report a look-alike token). `unknown` means no recognized shape matched, **not** that the formula is safe (e.g. `=WEBSERVICE(...)` reports `unknown`). Never changes the verdict. |

`FORMULA_INJECTION_INPUT_TOO_LARGE` carries:

| Field | Type | Description |
|---|---|---|
| `length` | `int` | The rendered view length that exceeded the limit. |
| `limit` | `int` | The configured `max_input_chars`. |

## API Reference

::: aegis.validators.detection.FormulaInjectionDetector
