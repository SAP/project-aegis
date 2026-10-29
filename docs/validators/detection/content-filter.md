# ContentFilter

Reject text that contains any of a configured list of blocked terms or phrases.

## Description

`ContentFilter` is a deterministic, text-only, **detect-only** validator. It runs under
[`Guard`](../../guard.md) as a policy guardrail on agent **input** or **output**, scanning the text
for a caller-configured list of literal terms and rejecting it when any term appears. Reach for it
when you have a **known list of words or phrases** to keep out — a competitor list, banned subjects,
jailbreak markers, or internal code-names.

Matching is **literal, never semantic**. Each term is matched as escaped literal text, so the
validator finds the exact terms you configure — not a paraphrase, a synonym, a translation, or text
that is merely *about* the subject. Deciding whether text is *about* a topic without using the words
needs a model-backed classifier, which is a separate validator.

By default a term matches only as a discrete word or phrase, on word boundaries. This avoids the
**Scunthorpe problem** — the classic false positive where a clean word is blocked because a banned
string appears inside it. Blocking `politics` does not reject `apolitical`; blocking `cat` does not
reject `category`. The trade-off is that a term embedded inside a longer run of characters is not
matched. The boundaries are Python `re` word-character (`\w`) boundaries, an approximation of
Unicode UAX #29 — reliable for space-delimited scripts. A language that does not delimit words with
spaces (Chinese, Thai) needs `word_boundary=False`, which matches anywhere in the text.

Normalization is **off by default** (opt in with `normalize=True`): matching is fully literal
unless you enable it, which mirrors the engine's off-by-default `Guard(normalize=...)` and keeps
matching predictable. When enabled, it folds cheap cosmetic evasions — case changes, added
whitespace, zero-width/invisible characters, and NFKC-compatibility variants such as full-width
letters (`ｇａｍｂｌｉｎｇ` → `gambling`) — by normalizing both the text and every term before matching.
These folds are script-safe: they never map homoglyphs (an engine-only concern), so genuine
non-Latin letters are not rewritten to ASCII.

It is not a general safety net, and by design it does **not** catch:

- text that is *about* a blocked subject but never uses a configured term (semantic disclosure);
- confusable look-alike substitution (Cyrillic `о` for ASCII `o`), leetspeak (`p0litics`), or
  Base64/hex-encoded terms (documented residual gaps, addressed by future additive hardening);
- a term split across separately validated messages or chunks.

Each of those belongs to a different single-concern validator. A PASS certifies only that none of
the configured terms appeared — **never** that the text is on-topic, safe, or lawful. Per OWASP, a
denylist is not a general security boundary.

On a hit the `FailedResult` carries only safe summary metadata (an opaque term index, a match count,
and an offset); it **never** echoes the surrounding input. The literal blocked term is included only
when `report_matched_topic=True`, which is off by default in case a term is a confidential
code-name. If you enable it, or log the failure `details`, keep confidential values out of
`blocked_topics`.

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `blocked_topics` | `list[str]` | *(required)* | The literal terms/phrases to keep out of the text. At least one is required; each is matched as literal text, never as a regex or a semantic subject. |
| `word_boundary` | `bool` | `True` | When `True`, match only discrete words/phrases on word boundaries (avoids the Scunthorpe false positive). Set `False` for substring matching — required for Chinese/Thai and other scripts without word delimiters. |
| `case_sensitive` | `bool` | `False` | When `True`, skip case-folding. Only takes effect when `normalize=True` (case-folding is one step of normalization). |
| `normalize` | `bool` | `False` | Opt in to normalize both the text and every term before matching — Unicode normalization, strip zero-width/format characters, collapse whitespace, and case-fold (unless `case_sensitive`). Off by default: matching is fully literal unless enabled. |
| `compatibility_folding` | `bool` | `True` | Only when `normalize=True`. `True` uses NFKC, folding full-width and other compatibility variants (catches a real evasion). `False` uses canonical NFC, avoiding NFKC over-folding but no longer catching full-width forms. |
| `report_matched_topic` | `bool` | `False` | When `True`, include the literal `matched_topic` in the failure `details`. Off by default because a configured term could be confidential; the surrounding input is never echoed either way. |
| `max_input_chars` | `int \| None` | `None` | Optional resource valve. When set and the rendered text exceeds it, reject with `CONTENT_FILTER_INPUT_TOO_LARGE` rather than scan. A hard input-size limit belongs to a dedicated length/size validator; this is only a safety valve. |

Construction raises `ValueError` when `blocked_topics` is a single string instead of a list (a bare
string would expand into one-character terms), when `blocked_topics` is empty, when any term is
empty/whitespace-only or empty after normalization (an empty needle matches everything), or when
`max_input_chars` is negative.

The defaults suit space-delimited scripts and literal matching. Enable `normalize=True` for
cosmetic-evasion resistance, and calibrate `word_boundary` and `compatibility_folding` against your
target languages and your own benign and adversarial text before production.

## Usage

The minimal setup blocks a list of words with the safe defaults — word-boundary matching,
normalization off (fully literal), nothing echoed:

```python
from aegis import Guard
from aegis.validators.detection import ContentFilter

guard = Guard("input").use(ContentFilter(blocked_topics=["gambling", "credit card"]))

result = await guard.validate("Come learn about gambling strategies!")
if result.has_failures:
    print(result.failures[0].error_code)  # CONTENT_BLOCKED
```

For a Chinese/Thai term, or a deliberate substring policy, opt into substring matching:

```python
guard = Guard("input").use(
    ContentFilter(blocked_topics=["赌博"], word_boundary=False)
)

result = await guard.validate("这里有很多赌博相关的内容。")
if result.has_failures:
    print(result.failures[0].error_code)  # CONTENT_BLOCKED
```

When more than one term matches, the single reported verdict follows a fixed precedence:
`CONTENT_FILTER_INPUT_TOO_LARGE` → `CONTENT_BLOCKED`. Within `CONTENT_BLOCKED`, the earliest matching
offset wins (ties broken by the term's index in `blocked_topics`), and `match_count` reports how
many distinct terms matched.

## Error Codes

| Error Code | Condition |
|---|---|
| `CONTENT_BLOCKED` | A configured term appears in the text (as a word-boundary match by default, or anywhere in substring mode). |
| `CONTENT_FILTER_INPUT_TOO_LARGE` | `max_input_chars` is set and the rendered text exceeds it. |

## Failure Details

Fields populated on `FailedResult.details`. They are safe summaries only — never the surrounding
input. `match_offset` indexes the normalized text and is diagnostic only, since the text is never
echoed.

| Field | Type | Description |
|---|---|---|
| `matched_index` | `int` | Opaque index of the matched term within `blocked_topics`. Reveals nothing about the content. |
| `match_count` | `int` | How many distinct configured terms matched at least once. |
| `match_offset` | `int` | Character offset of the reported match in the normalized text. |
| `word_boundary` | `bool` | Whether word-boundary matching was in effect. |
| `matched_topic` | `str` | The literal blocked term — present **only** when `report_matched_topic=True`. |
| `length` | `int` | Length of the rejected text (only on `CONTENT_FILTER_INPUT_TOO_LARGE`). |
| `limit` | `int` | The configured `max_input_chars` (only on `CONTENT_FILTER_INPUT_TOO_LARGE`). |

On a PASS, `details` carries `terms_checked` (count of configured terms compared) and
`word_boundary`.

## API Reference

::: aegis.validators.detection.ContentFilter
