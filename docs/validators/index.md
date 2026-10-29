# Validators

Aegis ships a small, growing set of **built-in validators** alongside the engine
(`Guard`, `MutationGuard`) and the validator *contract* — the `Validator` /
`MutationValidator` protocols and the `BaseValidator` / `BaseMutationValidator`
convenience base classes. Register a built-in with `.use()`, or write your own by
following [Custom Validators](../custom-validators.md).

## Bundled validators

| Validator | Category | Runs under | Guards against |
|---|---|---|---|
| [`HtmlSanitizer`](mutation/html-sanitizer.md) | Mutation | `MutationGuard` (or `Guard` detect-only) | XSS / unsafe HTML markup — reduces content to a safe allowlisted subset |
| [`LengthValidator`](detection/length-validator.md) | Detection | `Guard` | Text whose character length is outside a configured `[min, max]` range |
| [`PromptLeakageCheck`](detection/prompt-leakage-check.md) | Detection | `Guard` | Output reproducing the system prompt, configured secrets, or canary tokens |
| [`ContentFilter`](detection/content-filter.md) | Detection | `Guard` | Text containing any of a configured list of blocked terms/phrases (deterministic, literal match) |
| [`FormulaInjectionDetector`](detection/formula-injection-detector.md) | Detection | `Guard` | Input that would begin a spreadsheet formula if written into a CSV/TSV cell — leading `=`/`+`/`-`/`@`, full-width, or control-char initiators (a.k.a. CSV injection; decodes obfuscation first) |
| [`SizeValidator`](detection/size-validator.md) | Detection | `Guard` | Text exceeding a configured UTF-8 byte-size ceiling |
| [`SqlInjectionDetector`](detection/sql-injection-detector.md) | Detection | `Guard` | Text carrying a known SQL-injection construct — UNION SELECT, stacked queries, tautologies, comments, time-based probes (decodes obfuscation first) |
| [`XssDetector`](detection/xss-detector.md) | Detection | `Guard` | Text carrying a known cross-site-scripting (XSS) vector — HTML tags, script URIs, DOM sinks (decodes obfuscation first) |
| [`XxeDetector`](detection/xxe-detector.md) | Detection | `Guard` | Text carrying an XXE construct — external entities, external/inline DTDs, entity declarations, XInclude (decodes obfuscation first; masks inert comment/CDATA/PI regions) |

More built-ins are planned. To write one, see the
[Authoring Guide](authoring-guide.md).

!!! info "Opt-in Unicode normalization on `Guard`"
    To defeat bypasses where an attacker disguises a banned token with
    visually identical but bytewise different characters (e.g. Cyrillic
    `ѕ` U+0455 instead of ASCII `s`), construct your detection guard with
    `Guard("input", normalize=True)`. The guard applies NFKC +
    variation-selector strip + homoglyph→ASCII fold once before fanning
    out to validators; the caller's original text is forwarded to the
    agent on pass.

    `MutationGuard` does **not** normalize text. Pair a normalizing
    detection `Guard` upstream when you need homoglyph protection on the
    mutation path. See
    [Architecture](../architecture.md).

---

## The Contract

Everything you need to build a validator is importable from `aegis` or
`aegis.validators`:

| Symbol | Purpose |
|---|---|
| `Validator` | Protocol for detection validators (used with `Guard`) |
| `MutationValidator` | Protocol for detect-and-fix validators (used with `MutationGuard`) |
| `BaseValidator` | Optional base class for detection validators |
| `BaseMutationValidator` | Optional base class for mutation validators |
| `PassedResult` / `FailedResult` | Return values from `validate()` |
| `ContentView` / `ContentItem` | The content a validator sees (`validate(view)`) |
| `ItemEdit` | Id-addressed whole-part edit returned from `mutate()` |

!!! tip "One class can do both"
    Both protocols are view-based (`validate(view)`), so a `MutationValidator` is a superset
    of `Validator`: the same class runs under `Guard` for detection only (`validate` runs;
    `mutate` is not called) or under `MutationGuard` to also fix.

    See [Custom Validators — MutationValidator Protocol](../custom-validators.md#the-mutationvalidator-protocol)
    for a full explanation.

---

## Writing Your Own

Any class satisfying the `Validator` or `MutationValidator` protocol works as a validator.
Start with the [Custom Validators](../custom-validators.md) contract, then follow the
[Authoring Guide](authoring-guide.md) for where code/tests/docs live and the end-to-end
workflow.
