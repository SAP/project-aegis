# Validator Research Prompt

A reusable prompt for the **research-then-spec** phase of building a validator — *before* any
code is written. It exists because requirements you assume instead of research are the ones that
leak in production: what a validator accepts or rejects should come from the standard,
regulation, or specification that governs the domain, not from intuition.

The prompt is **deliberately domain-only and tool-agnostic**. It produces a written specification
of *what* the validator must do and *where its boundary lies*. Translating that spec into the
aegis contract (types, base classes, registration) is the separate, later implementation step.

---

## How to use it

1. Fill in the three placeholders below.
2. Run everything under **The prompt** through a capable model (ideally one with web access, so it
   can ground claims in real sources).
3. Review the output as a spec, not a draft — challenge every `[Assumption]` and confirm every
   `[Established]` claim against its cited source.

| Placeholder | What to put there |
|---|---|
| `{{VALIDATOR_SUBJECT}}` | The one thing this validator guards against, stated plainly. E.g. "detects and redacts personally identifiable information", "rejects prompt-injection attempts", "enforces a maximum length". |
| `{{KNOWN_CONTEXT}}` | *(Optional)* Anything already decided or constrained: target jurisdiction/locale, must run offline, no heavy dependencies, specific inputs it will see. Write "None" if unsure — the research should surface these. |
| `{{TARGET_ENVIRONMENT}}` | *(Optional)* Where it will run (language, runtime, latency budget). Used only to keep trade-offs realistic; the spec stays implementation-agnostic. Write "Unspecified" if it doesn't matter yet. |

---

## The prompt

You are a **domain researcher and specification author**. Your job is to research a validator thoroughly and then write its specification. **Do not write any implementation code** — this phase ends at a written spec.

A **validator** is a self-contained component that inspects a piece of input and decides whether it is acceptable: it either passes the input or reports a **specific** reason it was rejected. Some validators additionally **repair** the input so it becomes acceptable. A good validator does exactly **one thing** and treats every input as potentially hostile.

**The validator to research:**

- Guards against: `{{VALIDATOR_SUBJECT}}`
- Known context / constraints: `{{KNOWN_CONTEXT}}`
- Eventual target environment: `{{TARGET_ENVIRONMENT}}`

### Your operating principles

1. **Research first, and ground it.** Every rule the validator enforces must trace to an authoritative source — a standard, regulation, RFC, official specification, or reputable reference — not to intuition or a plausible guess.
2. **Map the whole boundary.** Be as clear about what the validator does *not* cover as what it does. Undrawn boundaries become production surprises.
3. **Surface every trade-off.** Where two reasonable choices exist (stricter vs. looser, faster vs. more thorough), name both, state the consequence of each, and recommend one with a reason.
4. **Label your confidence.** Tag each requirement `[Established]` (backed by a cited source) or `[Assumption]` (reasonable, but needs confirmation). Never disguise an assumption as a fact. If sources conflict, say so and explain the conflict.

### Phase 1 — Research

Investigate and report on each of the following. Cite sources inline (name + link/edition/version). If you cannot find an authoritative source for something, say so explicitly rather than inventing one.

1. **Domain & terminology** — Define the problem precisely and the key terms as this domain uses them. Where does the "acceptable vs. unacceptable" line actually fall?
2. **Scope shape** — Is `{{VALIDATOR_SUBJECT}}` genuinely *one* concern, or several bundled together? A good validator does exactly one thing. If the subject decomposes into independent checks, say so and name the separate validators it should split into. Draw this line now, before the spec locks it in.
3. **Governing standards** — Which standards, regulations, or specifications define this domain (with edition/version)? What exactly do they require? Note jurisdiction- or locale-specific differences. If the domain has **no** authoritative standard — many heuristic domains (e.g. prompt-injection, toxicity) do not — say so plainly and anchor the rules in reputable prior art and threat intelligence instead. Do not invent a standard to fill the gap.
4. **Prior art** — How do existing tools/libraries define and solve this problem? What are their documented pitfalls, false-positive/false-negative traps, and known bypasses?
5. **Detection rules** — Enumerate the concrete, testable criteria that separate valid from invalid input. Prefer precise rules ("matches this pattern / this format") over vague ones ("looks suspicious"). Attach a source to each.
6. **Repair viability** — Should this validator repair the input, or only report it? Decide it from evidence, not convenience: repair is appropriate only when a *safe*, unambiguous fix exists **and** silently altering the input is less harmful than rejecting it. In many domains the correct answer is detect-only. State which, and why — this decision feeds Phase 2.
7. **Threat & adversarial model** — How would someone deliberately evade this validator (encoding tricks, homoglyphs, whitespace/casing, chunking, locale games)? What must the validator resist to be trustworthy?
8. **Edge cases** — Empty input, whitespace-only, extremely large input, malformed/partial input, multilingual/Unicode, mixed content, and genuinely ambiguous cases. State what the *correct* behavior is for each.
9. **Trade-offs** — At minimum: precision vs. recall (the cost of a false accept vs. a false reject in this domain), performance/latency, configurability vs. simplicity, locale/coverage breadth, dependency/data weight, and **determinism** (a deterministic, reproducible check is testable and trustworthy; if the approach cannot be — e.g. a model-backed check — name that as a cost and prefer a deterministic alternative where one exists). For each, give a recommendation.
10. **Open questions** — List everything the research could not settle and that a human must decide before implementation. Do not paper over these.

### Phase 2 — Specification

Using the research, write the spec with these sections. Be concrete; avoid vague prose.

1. **Purpose & scope** — One-line tagline, then a short paragraph: what it checks and why, and when someone should reach for it.
2. **Detect-only or detect-and-fix** — State which, and why (carry the decision from research). If it repairs input, define the *safe* repair precisely — what it replaces, with what, and what it must never silently change or lose — and state the two properties every repair must hold: it is a **no-op on already-acceptable input** (idempotent), and its output **passes re-validation** by this same validator.
3. **Definitions** — The key terms exactly as this validator uses them.
4. **Validation rules** — The enumerated, testable rules from research, each with its source and its `[Established]`/`[Assumption]` tag.
5. **Input contract** — What input the validator accepts: shape, type, encoding assumptions, and any preconditions.
6. **Output contract** — The pass signal, and each **distinct** failure condition. Give every failure a **stable, specific identifier** (e.g. `TOO_LONG`, not `INVALID`) and list the context it should report alongside it. **Never echo a raw value that could be secret or personal** — specify a safe summary instead (a count, a length, a redacted marker). State whether the verdict is **deterministic** — the same input must yield the same result and identifier; if it cannot be (e.g. a model-backed check), say so explicitly.
7. **Configuration & parameters** — Tunable options, their defaults, and *which trade-off each one controls*.
8. **Edge-case behavior** — A table: input condition → expected behavior, covering the Phase 1 edge cases.
9. **Out of scope** — Explicit non-goals: what this validator deliberately does not do, and why (often: it belongs to a different, single-concern validator).
10. **Trade-offs & decisions** — The trade-offs from research, each with the choice made and its rationale.
11. **Dependencies & data** — Any external libraries or bundled data needed; their size; and for data, whether it is **redistributable under a permissive license** and **free of personal/confidential content** (flag anything that could block an open-source release).
12. **Open questions & risks** — Carried from research; the decisions a human still owes.
13. **Test outline** — The cases a test suite must cover: happy path, each boundary, each failure identifier, malformed/empty input, and the adversarial cases from the threat model. For a detect-and-fix validator, add repair correctness: a no-op on already-clean input, and repaired output that passes re-validation.

### Output format

Return Markdown with the Phase 1 and Phase 2 sections above, in that order. Keep each point as short as it can be while staying complete. If a section genuinely does not apply, keep the heading and write one line explaining why.

---

## The bar this prompt clears

The spec is ready to hand to implementation when:

- [ ] Every validation rule cites a source, and assumptions are labeled as such.
- [ ] The boundary is explicit — the **Out of scope** section is real, not empty.
- [ ] Each failure has a stable, specific identifier and reports only safe, non-sensitive detail.
- [ ] For a detect-and-fix validator, the repair is defined as safe — idempotent on clean input, and its output passes re-validation.
- [ ] Edge cases and adversarial inputs each have a defined expected behavior.
- [ ] Trade-offs are named with a decision and a reason, not left implicit.
- [ ] Bundled data (if any) is confirmed redistributable and free of personal/confidential content.
- [ ] Open questions are listed, not hidden.
