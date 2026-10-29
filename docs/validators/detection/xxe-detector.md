# XxeDetector

Flag text carrying an XML External Entity (XXE) construct.

## Description

`XxeDetector` is a deterministic, text-only, **detect-only** validator. It runs under
[`Guard`](../../guard.md) and reports a failure when the content carries an XXE-capable XML
construct — an external entity, an external or inline DTD, an entity declaration, or XInclude; it
never changes the content. Use it as a cheap, non-LLM check at a trust boundary on input (or model
output) that could, now or downstream, reach an XML processor.

`Guard` runs its registered validators concurrently, so a failure here does **not** short-circuit
or skip other validators — "cheap" refers to this validator's own cost.

### Obfuscation is decoded before detection

Real payloads hide behind encoding layers, so the content is **preprocessed** before any
pattern runs: invisible/zero-width control characters are stripped, then URL-percent, HTML-entity,
JavaScript `\uXXXX`/`\xXX`, and CSS `\3C` escapes are decoded repeatedly until the text stops
changing (bounded). Without this, `%3C!DOCTYPE` or `&#x3C;!ENTITY` would sail past a raw-text
matcher. Each pass can be disabled via the constructor when the input's encoding is fully trusted.

### Declarations inside inert regions are not flagged

A declaration written inside a *complete* comment (`<!-- -->`), CDATA section (`<![CDATA[ ]]>`), or
processing instruction (`<? ?>`) is data, not markup — an XML processor never acts on it — so those
complete regions are blanked out before the patterns run. This removes the classic
"matcher fires on `<!DOCTYPE` inside a comment" false positive without parsing hostile XML. An
*unterminated* region is treated the opposite way: it cannot make what follows inert, so a construct
after an unclosed comment or CDATA is still flagged (fail toward detection).

### What it flags

Capability-bearing DTD/entity machinery and XInclude — not every `<` or `&`:

- `external_entity` — an entity declaration (general or `%` parameter) carrying an external ID:
  `<!ENTITY xxe SYSTEM "file:///etc/passwd">`, `<!ENTITY p PUBLIC "id" "http://host/">`. The
  file-read / SSRF vector.
- `external_dtd` — a DOCTYPE pointing at an external subset: `<!DOCTYPE foo SYSTEM "foo.dtd">`.
- `dtd_internal_subset` — a DOCTYPE opening an inline `[ ... ]` subset, where entity bombs
  (billion laughs) and internal entities live: `<!DOCTYPE foo [ <!ENTITY x "..."> ]>`.
- `parameter_entity` — a parameter-entity declaration `<!ENTITY % ...>`, the blind/out-of-band
  building block.
- `entity_declaration` — any other entity declaration, the billion-laughs building block. Presence
  is the signal; the validator never expands it to classify (CWE-776).
- `xinclude` — the XInclude namespace `http://www.w3.org/2001/XInclude` or an `<xi:include>`
  element, which pulls in an external file/URL without a DTD. Matching the namespace URI itself
  (not just an `<xi:include>` element) catches a renamed prefix, at the cost of flagging input that
  merely names the URI — a bare `xmlns:xi="…/XInclude"` declaration, or prose quoting the spec URL,
  will trip this. That false positive is intentional (bias to recall). A non-`xi` prefix bound to
  the XInclude namespace *out of view* (an `<p:include>` element whose `xmlns` binding sits on an
  ancestor that is not in the rendered text) is **not** caught — a limitation of a text scanner that
  does not resolve namespace scope across a document it may see only in part.
- `bare_doctype` — a capability-free bare `<!DOCTYPE name>`. **Only** flagged when
  `flag_bare_doctype=True` (see below); off by default so a bare HTML5 doctype passes.

Matching is case-insensitive and every pattern is linear (no nested quantifier that can backtrack),
so the validator adds no ReDoS surface of its own.

### The safety ceiling — read before you rely on this

This validator is **defense-in-depth, not a primary control**. The authoritative XXE defense is a
hardened XML parser at the processing site — DTDs disabled, external entities disabled, no external
resolution. A passed result means "no *known* XXE construct found", never "provably safe".
Byte-level transport tricks (UTF-16/UTF-32 BOM, base64- or archive-wrapped XML) are **out of scope
by design**: they belong to an upstream decode/extraction stage that hands this validator a decoded
string.

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `strip_invisible_chars` | `bool` | `True` | Strip zero-width/control characters before matching. |
| `decode_obfuscation` | `bool` | `True` | Decode URL/HTML/JS/CSS encoding layers before matching. |
| `max_input_chars` | `int \| None` | `1048576` (1 MiB) | Resource valve. When the view's items exceed it in combined character length, the input is rejected with `XXE_INPUT_TOO_LARGE` rather than preprocessed and scanned. Pass `None` to lift the cap when the input size is already trusted. Negative values raise `ValueError`. |
| `flag_bare_doctype` | `bool` | `False` | Also flag a capability-free bare `<!DOCTYPE name>` (reject-all-DOCTYPE policy). Off by default so a bare HTML5 doctype passes. |

Each item is decoded and inert-masked on its own, then the items are flattened (via
`ContentView.render()`) for matching. A construct split across an item boundary (`<!DOCTYPE foo` +
`SYSTEM "...">`) is still caught — `render()` joins items with a newline, matched by `\s` — while an
inert region (comment/CDATA/PI) in one item cannot mask a construct in another, since the parts may
be processed separately downstream.

## Usage

```python
from aegis import Guard
from aegis.validators.detection import XxeDetector

guard = Guard("input").use(XxeDetector())

result = await guard.validate('<!DOCTYPE foo SYSTEM "file:///etc/passwd">')
if result.has_failures:
    print(result.failures[0].error_code)  # XXE_DETECTED
```

## Error Codes

| Error Code | Condition |
|---|---|
| `XXE_DETECTED` | The rendered content carries an XXE-capable construct (after decoding and inert-region masking). |
| `XXE_INPUT_TOO_LARGE` | `max_input_chars` is set and the combined length of the view's items exceeds it. |

## Failure Details

`XXE_DETECTED` locates the first construct (the raw matched text is never echoed):

| Field | Type | Description |
|---|---|---|
| `match_offset` | `int` | Start position of the earliest construct, into the decoded/stripped/masked text that was scanned — **not** the raw input, since decoding shifts offsets. |
| `match_length` | `int` | Length of the matched construct. |
| `construct` | `str` | The construct class: `external_entity`, `external_dtd`, `dtd_internal_subset`, `parameter_entity`, `entity_declaration`, `xinclude`, or `bare_doctype`. |

`XXE_INPUT_TOO_LARGE` carries:

| Field | Type | Description |
|---|---|---|
| `length` | `int` | The combined character length of the view's items that exceeded the limit. |
| `limit` | `int` | The configured `max_input_chars`. |

## API Reference

::: aegis.validators.detection.XxeDetector
