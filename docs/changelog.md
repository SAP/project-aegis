# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-27

First public release. `aegis` is a deterministic guardrail engine for AI systems: a
`Guard` / `MutationGuard` core with a fluent `.use()` API, an A2A wrapper layer, optional
Unicode normalization, OpenTelemetry tracing, eight stdlib-only detection validators,
and one `nh3`-powered mutation validator.

### Core

- **`Guard` / `MutationGuard`** — view-based validation engine. `Guard.validate` /
  `MutationGuard.validate` take a `value: str | ContentView` (a string becomes a single
  text item). Validators implement `validate(view: ContentView, metadata=None)` — call
  `view.render()` for the flattened text or iterate `view.items`. A `MutationValidator` is a
  superset of `Validator` and can run detect-only under `Guard`.
- **Result types** — `PassedResult` and `FailedResult`, each carrying a `details` mapping.
  Failure `details` carry only safe summaries, never the raw input.
- **Mutation path** — `MutationValidator` adds
  `mutate(view, metadata=None, validation_failure=None) -> list[ItemEdit]`. `MutationGuard`
  applies the edits and rebuilds the view, so item cardinality and order are guaranteed by
  the engine. `MutationGuardResult` carries a single `output` that mirrors the input type
  (`str` or `ContentView`). Public
  types: `ContentView`, `ContentItem`, `ContentType`, `ItemEdit`, `MutationError`.
- **`GuardMetadata` dataclass** with fields `system`, `tools`, and `conversation_history`.
- **Empty guard raises** — `Guard.validate` / `MutationGuard.validate` raise `RuntimeError`
  when no validators are registered; register at least one via `.use()`.
- **Flexible `on_fail` handling** — `LOG`, `EXCEPTION`, `NOOP`, or a custom callable.
- **Shared text normalization (`aegis.NormalizationConfig`)** — one `normalize()` function
  plus a `NormalizationConfig` dataclass of per-strategy toggles (Unicode form, strip
  variation selectors, strip format characters, collapse whitespace, map homoglyphs,
  case-fold), used by both the engine and the detection validators. `Guard(normalize=...)`
  accepts a `bool` or an explicit `NormalizationConfig`. Pure-ASCII input skips the
  non-ASCII passes for speed.
- **OpenTelemetry tracing** — integrated, and a no-op when no exporter is configured.

### A2A wrapper

- **`GuardedAgentExecutor` / `GuardedEventQueue`** — stage `build_view` → `validate` →
  `apply_view` on input and output. A2A parts ↔ view conversion lives in
  `aegis.a2a._utils`. Non-text parts (`DataPart`, `FilePart`) are preserved in place; a
  partless message is a no-op. The engine itself is A2A-agnostic.

### Detection validators (stdlib-only, detect-only)

- **`PromptLeakageCheck`** — rejects output reproducing the system prompt, configured
  sensitive fragments, or canary tokens (OWASP `LLM07:2025`). Exact and opt-in normalized
  substring matching, optional `difflib` fuzzy matching, optional `GuardMetadata.system`
  protection, and a `max_input_chars` valve.
- **`ContentFilter`** — rejects text containing any configured blocked term or phrase.
  Word-boundary matching by default (avoids the Scunthorpe problem) with an opt-in substring
  mode; optional normalization; opaque term-index reporting by default.
- **`XssDetector`** — flags known XSS vectors (CWE-79): HTML tags, dangerous URI schemes,
  legacy CSS `expression()`, and DOM write-sinks. Scans `view.render()`; decodes obfuscation
  to a fixed point. Toggleable `strip_invisible_chars` / `decode_obfuscation` and a
  `max_input_chars` valve.
- **`SqlInjectionDetector`** — flags known SQL-injection constructs (CWE-89): `UNION SELECT`,
  stacked queries, equal-operand tautologies, SQL comments, and time-based blind probes.
  High-signal, linear (no ReDoS surface). Scans `view.render()`; decodes obfuscation.
- **`FormulaInjectionDetector`** — flags input that would begin a spreadsheet formula on CSV
  import (CWE-1236). Scans each `ContentItem` as one cell candidate; skips leading padding and
  one structural quote; names high-impact DDE/HYPERLINK shapes via `formula_shape`.
- **`XxeDetector`** — flags XML External Entity constructs (CWE-611): external entities,
  external/inline DTDs, parameter/general-entity declarations, and XInclude. DTD handling is
  capability-based (`flag_bare_doctype` for reject-all). Scans `view.render()`; masks inert
  comment/CDATA/PI regions; decodes obfuscation.
- **`LengthValidator`** — min/max text length validation.
- **`SizeValidator`** — byte-size validation against a configurable limit.

### Mutation validators

- **`HtmlSanitizer`** — detect-and-fix sanitizer reducing HTML to a safe, allowlisted subset
  (XSS / mutation-XSS defense), powered by `nh3` (Rust `ammonia`/`html5ever`), so it resists
  syntactic obfuscation. `validate` flags parts differing from their sanitized form with
  `DISALLOWED_HTML`; `mutate` returns the sanitized form per changed part. Safe defaults
  (HTML-only, URL schemes tightened to `{http, https, mailto}`, inline CSS disabled) with
  widening knobs. Runs under `MutationGuard`, or `Guard` for detect-only.
