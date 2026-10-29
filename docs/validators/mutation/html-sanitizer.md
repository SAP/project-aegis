# HtmlSanitizer

Reduce HTML content to a safe, allowlisted subset — removing scripts, event handlers, dangerous URLs, and disallowed markup.

!!! note "Mutation is supported, with a caveat"
    Under the [Detection vs. Mutation rule](../../custom-validators.md#detection-vs-mutation-the-rule),
    XSS / HTML-stripping is a **security** concern that normally should detect-and-reject rather
    than repair. `HtmlSanitizer` is an exception: it is a thin wrapper over the well-tested
    [`nh3`](https://nh3.readthedocs.io/)/`ammonia` sanitizer, so repairing content is safe and
    the mutation path is kept. If you prefer to reject unsafe content outright instead of
    silently repairing it, run it under `Guard` (detect-only, blocking on failure).

## Description

`HtmlSanitizer` treats each content part as an HTML fragment and enforces an allowlist of tags,
attributes, URL schemes, and (optionally) inline-CSS properties. Everything outside the
allowlist — `<script>`, event-handler attributes such as `onerror`, `javascript:` URLs, HTML
comments, unknown tags — is removed, and the result is re-serialized from a spec-compliant parse
tree. It defends against cross-site scripting ([CWE-79](https://cwe.mitre.org/data/definitions/79.html))
and mutation-XSS when agent content may later be rendered as HTML by a downstream client.

It is a **detect-and-fix** validator: run it under `MutationGuard` to repair content, or under
`Guard` for detect-only (rejecting anything not already in canonical, allowlisted form). Detection
and repair share one definition of safe — a part is acceptable exactly when it already equals its
sanitized form — so `validate` reports a change and `mutate` emits the sanitized text.

The sanitizer is powered by [`nh3`](https://nh3.readthedocs.io/) (the Rust `ammonia` library over
`html5ever`). Because it parses input into a tree and re-serializes, it resists syntactic
obfuscation: mixed case (`<ScRiPt>`), HTML entities (`&#106;avascript:`), and malformed nesting are
all resolved to the canonical tree *before* the allowlist is applied.

!!! warning "HTML only — not for plain text"
    Apply this only to content meant to be rendered as HTML. A stray `<` or `>` in plain text (for
    example `5 < 3`) is HTML-escaped, which changes the text and is reported as a violation.
    Deciding whether content should be HTML at all is the caller's routing decision.

!!! warning "Widening the allowlist enlarges the attack surface"
    The defaults are safe: HTML-only, tightened URL schemes, inline CSS disabled. Two options are
    especially load-bearing — adding foreign-content tags (`svg`, `math`, `foreignObject`) reopens
    namespace-confusion vectors, and enabling inline CSS is only as safe as the property list you
    allow.

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `allowed_tags` | `set[str] \| None` | `None` (nh3's HTML-only default) | Permitted tag names. A set *replaces* the default rather than adding to it. |
| `allowed_attributes` | `dict[str, set[str]] \| None` | `None` (nh3's per-tag default) | Attributes permitted per tag (`"*"` key = any tag). *Replaces* the default entirely and is honored as given — a custom allowlist that lists an event handler (e.g. `onclick`) keeps it, so widen only for trusted content. |
| `allowed_url_schemes` | `set[str] \| frozenset[str]` | `{http, https, mailto}` | Schemes permitted in URL-bearing attributes. `javascript:` and `data:` are excluded. |
| `allowed_css_properties` | `set[str] \| frozenset[str] \| None` | `None` (inline CSS disabled) | Inline-CSS properties permitted in `style`. `None` removes `style` entirely, even if allowed in `allowed_attributes`. |
| `strip_comments` | `bool` | `True` | Remove HTML comments (they can leak metadata). |
| `link_rel` | `str \| None` | `"noopener noreferrer"` | `rel` value forced onto `<a>` (mitigates reverse-tabnabbing). `None` leaves `rel` untouched. |

## Usage

```python
from aegis import MutationGuard
from aegis.validators.mutation import HtmlSanitizer

guard = MutationGuard("output").use(HtmlSanitizer())
result = await guard.validate("<script>alert(1)</script>Hello")

if result.was_mutated:
    print(result.output)  # "Hello"
```

Enabling inline CSS is opt-in. Start from a vetted property set rather than allowing arbitrary
properties, and remember `style` must also be listed in `allowed_attributes`:

```python
sanitizer = HtmlSanitizer(
    allowed_tags={"p", "span"},
    allowed_attributes={"p": {"style"}, "span": {"style"}},
    allowed_css_properties={"color", "font-weight", "text-align"},
)
```

nh3 sanitizes CSS *values* too — a `url()` with a disallowed scheme is dropped even on an allowed
property. Two residuals are worth knowing. First, `behavior` and `-moz-binding` load script through
a `url()` that this value-level filtering does *not* catch, so do not allowlist them. Second, the
legacy IE `expression()` function is not stripped by nh3, but it is inert in every browser from IE8
(2009) onward and is treated as an accepted out-of-scope non-goal.

## Error Codes

| Error Code | Condition |
|---|---|
| `DISALLOWED_HTML` | At least one part differs from its sanitized form — it carried disallowed or non-canonical markup. |

## Failure Details

Fields populated on `FailedResult.details`. Only safe summaries are reported — never the raw
markup, which could carry an XSS payload or an embedded secret.

| Field | Type | Description |
|---|---|---|
| `changed_parts` | `int` | Number of content parts that differed from their sanitized form. |
| `original_length` | `int` | Summed character length of the offending parts, before sanitization. |
| `sanitized_length` | `int` | Summed character length of those parts after sanitization. |

## API Reference

::: aegis.validators.mutation.HtmlSanitizer
