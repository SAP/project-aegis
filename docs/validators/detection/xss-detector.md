# XssDetector

Flag text carrying a known cross-site-scripting (XSS) vector.

## Description

`XssDetector` is a deterministic, text-only, **detect-only** validator. It runs under
[`Guard`](../../guard.md) and reports a failure when the content carries a known XSS vector; it
never changes the content. Use it as a cheap, non-LLM check at a trust boundary — on model output
before it is rendered in a page, or on input that will later be reflected into HTML.

`Guard` runs its registered validators concurrently, so a failure here does **not** short-circuit
or skip other validators — "cheap" refers to this validator's own cost.

### Obfuscation is decoded before detection

Real payloads hide behind encoding layers, so the rendered content is **preprocessed** before any
pattern runs: invisible/zero-width control characters are stripped, then URL-percent, HTML-entity,
JavaScript `\uXXXX`/`\xXX`, and CSS `\3C` escapes are decoded repeatedly until the text stops
changing (bounded). Without this, `%3Cscript%3E` or `&#60;script&#62;` would sail past a raw-text
matcher. Each pass can be disabled via the constructor when the input's encoding is fully trusted.

### What it flags

- **Tag-based** markup (`html_tag`): a complete `<tag …>` (requiring the closing `>` ignores
  benign math/prose such as `a < b`, `a<b`, `i<n`), **or** an *unterminated* start tag that already
  carries a known event-handler attribute (`<img src=x onerror=alert(1)` with no `>`) — browsers
  recover these at EOF so the handler still fires. The handler list is curated, so a benign
  attribute like `online=` is not mistaken for a handler.
- **Markup-less** vectors that carry no tag:
    - `script_uri` — `javascript:` / `vbscript:` URIs (tolerating the tab/CR/LF a browser strips
      inside a scheme; a plain space before the colon is *not* a URL and is not flagged).
    - `data_uri` — dangerous `data:` URIs (`text/html`, `application/xhtml+xml`, `image/svg+xml`).
    - `css_expression` — legacy CSS `expression(` **as a property value** (`width:expression(…)` /
      `style=expression(…)`); bare prose like `expression(2+2)` is not flagged.
    - `dom_sink` — a write to a DOM sink: `.innerHTML` / `.outerHTML` / `insertAdjacentHTML` on any
      receiver, or `document.cookie|location|write|writeln|domain`, as a call `(` or an assignment
      (`=`, including compound/logical `+= ??= ||=` …). A comparison (`==`) or arrow (`=>`) only
      reads the sink and is not flagged.

### The safety ceiling — read before you rely on this

This validator is **defense-in-depth, not a primary control**. The authoritative XSS defense is
context-aware output encoding at render time. A passed result means "no *known* XSS marker found",
never "provably safe".

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `strip_invisible_chars` | `bool` | `True` | Strip zero-width/control characters before matching. |
| `decode_obfuscation` | `bool` | `True` | Decode URL/HTML/JS/CSS encoding layers before matching. |
| `max_input_chars` | `int \| None` | `None` | Optional resource valve. When set and the rendered text exceeds it, the text is rejected with `XSS_INPUT_TOO_LARGE` rather than preprocessed and scanned. Off by default. |

Detection runs on `view.render()` — the whole flattened `ContentView` — so a vector split across an
item boundary (`<scr` + `ipt>`) is still caught, since `render()` joins items with a newline
(matched by `\s`).

## Usage

```python
from aegis import Guard
from aegis.validators.detection import XssDetector

guard = Guard("input").use(XssDetector())

result = await guard.validate("<script>alert(1)</script>")
if result.has_failures:
    print(result.failures[0].error_code)  # XSS_DETECTED
```

## Error Codes

| Error Code | Condition |
|---|---|
| `XSS_DETECTED` | The rendered content carries a known XSS vector (after decoding). |
| `XSS_INPUT_TOO_LARGE` | `max_input_chars` is set and the rendered text exceeds it. |

## Failure Details

`XSS_DETECTED` locates the first marker (the raw matched text is never echoed):

| Field | Type | Description |
|---|---|---|
| `match_offset` | `int` | Start position of the earliest marker, into the decoded/stripped text that was scanned — **not** the raw input, since decoding shifts offsets. |
| `match_length` | `int` | Length of the matched marker. |
| `vector` | `str` | The marker class: `html_tag`, `script_uri`, `data_uri`, `css_expression`, or `dom_sink`. |

`XSS_INPUT_TOO_LARGE` carries:

| Field | Type | Description |
|---|---|---|
| `length` | `int` | The rendered text length that exceeded the limit. |
| `limit` | `int` | The configured `max_input_chars`. |

## API Reference

::: aegis.validators.detection.XssDetector
