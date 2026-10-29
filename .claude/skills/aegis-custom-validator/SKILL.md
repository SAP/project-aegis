---
name: aegis-custom-validator
description: 'Author a custom aegis validator in your own project, end to end — research, spec, implement, test, document, and review. Use when a user wants to add, write, or author a validator that runs on aegis (detection or mutation), build a guardrail that inspects or repairs agent input/output, or asks how to implement a new check against the aegis validator contract. Works in any repo that has the aegis package installed: it reads the contract from the installed package, conforms to the fixed validate()/mutate() contract, and keeps a human in the loop at every real design decision.'
argument-hint: 'Describe the validator to build (what it guards against) and any known constraints'
user-invocable: true
---

# Author a custom aegis validator

Use this skill to take a validator from an idea to a working, tested component **in your own
project** — a repo that uses `aegis` as a dependency. The validator you produce lives in your
codebase and runs on the `aegis` engine; you do not need the aegis source tree.

A **validator** is a self-contained class that inspects input and decides whether it is
acceptable: it either passes the input or reports a *specific* reason it was rejected. A
**mutation validator** additionally *repairs* the input. A good validator does exactly **one
thing** and treats every input as potentially hostile.

The two hardest, highest-value phases are **research/spec** (phase 1) and **review** (phase 8).
Coding is nearly mechanical once the spec and the contract are pinned. Over-invest there, not
in code generation.

## Read these first — do not write code before you have

The contract is defined by the `aegis` package you install, not by this skill. Read it from the
installed package so you implement against the version you actually have, not a remembered one:

1. **The contract, from the installed `aegis` package.** Read the docstrings on the types you will
   implement and return — all importable from the top-level `aegis` package (most are also under
   `aegis.validators`; `GuardMetadata` also lives in `aegis.metadata`):
   - `BaseValidator`, `BaseMutationValidator` — the base classes you subclass.
   - `ContentView`, `ContentItem` — the content a validator inspects (`view.render()` for the
     flattened text, `view.items` for per-item access).
   - `PassedResult`, `FailedResult` — the verdicts `validate()` returns.
   - `ItemEdit` — the id-addressed edit `mutate()` returns.
   - `GuardMetadata` — the optional surrounding context.

   Read them however your environment makes easiest — your IDE's go-to-definition, `help(aegis...)`,
   or the source that ships inside the installed package. When a docstring and this skill disagree,
   the installed package wins; it is the source of truth.
2. `references/writing-style.md` — the tone for every comment, docstring, and doc you write.
3. `references/compliance.md` — the content-safety bar every file must clear before you share it.

## Workflow

### Phase 0 — Preflight: confirm aegis is installed

A validator is aegis code — it imports from `aegis` — so the package must be present before you
start:

1. **Check it is importable, and that it is the right `aegis`:** run
   `python -c "from aegis.validators import BaseValidator, ContentView, ItemEdit"` (use the
   project's interpreter — e.g. `uv run python -c "..."` for a `uv` project). Importing a real
   contract symbol — not just `import aegis` — is deliberate: the bare import name `aegis` also
   belongs to an unrelated package, so checking a symbol confirms the guardrail library is the one
   installed, not just that *something* named `aegis` is.
2. **If it is missing, stop and install it first.** Tell the user to add the aegis dependency the
   way their project manages packages — its PyPI/distribution name may differ from the import name
   `aegis`, so confirm the name from the project rather than guessing (do not assume
   `pip install aegis`). Nothing else in this skill can proceed until the import above succeeds.
3. **Read the contract from that installed package** (the section above), so everything you write
   matches the version the project actually has.
4. **Sanity-check the rest of the surface** you will use beyond the import check above (for example
   `PassedResult`, `FailedResult`, `GuardMetadata`). If anything is missing, the installed aegis may
   be too old or too new for this skill — surface that to the user and reconcile before continuing,
   rather than guessing.

Then read the two bundled `references/` documents in full. Requirements you assume instead of read
are the ones that leak in production.

### Phase 1 — Research → spec (mandatory, comes first)

1. Carry out the research prompt in `references/research-prompt.md` against the validator subject.
   Fill in its placeholders (subject, known constraints, target environment) from the user's
   request; ask if a placeholder is genuinely unknown.
2. **Ground every rule in an authoritative source** — a standard, regulation, RFC, or reputable
   reference — not intuition. Tag each requirement `[Established]` (cited) or `[Assumption]`
   (needs confirmation). Never disguise an assumption as a fact.
3. **Verify empirically, don't trust docs.** If the validator will lean on a library, run the
   library over adversarial input to confirm what it actually does (for example, test
   `clean(clean(x)) == clean(x)` before you claim idempotency). Convert every undocumented
   guarantee into either a verified fact or a test that fails loudly if it ever breaks.
4. Produce a written spec (Phase 2 of the research prompt). Its **living home is the class/module
   docstring** — keep it current with the code, not in a throwaway file.
5. **Reconcile conflicting sources explicitly.** If two references point at different designs,
   surface both, recommend one with a reason, and record the rejected alternative and why.

### Phase 2 — Plan

1. Fan out up to **three read-only exploration agents in parallel**, each with a distinct focus:
   (a) the exact contract signatures and result types in the installed `aegis` package, (b) any
   prior art for the domain (existing libraries or a reference implementation to reconcile), (c) how
   *this* project is laid out — where its code and tests live, how it packages and lints. This reads
   widely without polluting the main context.
2. Write a short plan naming the exact files, the folder layout **in this project**, where the
   validator is registered, and the test matrix. **Get sign-off before writing code.**

### Phase 3 — Implement to the contract

1. **Decide detect-only vs. detect-and-fix** from what the validator does: detect-only *reports*
   (runs under `Guard`); detect-and-fix also *repairs* (runs under `MutationGuard`, and adds a
   `mutate()`). Both are view-based, so a mutation validator also runs detect-only under `Guard`.
2. **Pick the layout by complexity, in your own package:** a self-contained validator is a single
   snake_case module (e.g. `your_package/validators/xml_validator.py`); promote to a **package
   folder** only when it needs helpers, pattern tables, or templates — then the public class lives
   in a `_`-private module re-exported from the folder's `__init__.py`, and helpers stay `_`-private.
   Start with one file; promote only when helpers appear.
3. **Conform the signatures exactly.** Copy the shapes verbatim — do not rename parameters or
   change return types:

    ```python
    async def validate(self, view: ContentView, metadata: GuardMetadata | None = None) -> ValidationResult
    async def mutate(self, view: ContentView, metadata: GuardMetadata | None = None, validation_failure: FailedResult | None = None) -> list[ItemEdit]
    ```

   Adapt `templates/detection-validator.py` or `templates/mutation-validator.py` — they import from
   the public `aegis` package and match the current contract.
4. **Every `FailedResult` gets a stable, specific `error_code`** (`TOO_LONG`, not
   `VALIDATION_FAILED`), defined once as a `Final` class constant or `Enum` — the single source
   shared by `validate()`, the tests, and any docs. A released code is a contract: add new ones,
   never rename.
5. **Never echo a raw value that could be secret or personal** in an error, message, or log.
   Report a safe summary in `details` — a length, a count, a redacted marker.
6. **Register it where your app builds its guard**, at runtime with the fluent `.use()` API — e.g.
   `Guard("input").use(YourValidator(...))` or `MutationGuard("input").use(YourValidator(...))`. If
   you package the validator for reuse, export the class from your module's public surface.
7. **Smoke-test immediately** through the real engine (`Guard`/`MutationGuard`) before writing the
   full suite.

### Phase 4 — Test

1. **Put the tests where your project keeps them**, mirroring wherever the validator lives. Drive
   the validator through a real `Guard` / `MutationGuard`; write `async def test_...` (aegis's tests
   use `pytest` with `asyncio_mode = "auto"` — match your project's async-test setup).
2. Build the matrix from the spec's **edge-case and threat** sections. Cover, at minimum: a clean
   value passes; a violating value fails with the **expected `error_code`**; boundaries (empty,
   whitespace-only, very large, malformed); and the adversarial cases from the threat model.
3. For a mutation validator, also assert **repair correctness**: a no-op on already-clean input
   (idempotency) and that repaired output **passes re-validation**.
4. **Assert the property, not an incidental byte.** When output has an incidental artifact, assert
   the security/behavioral property that must hold, not one exact string. Adapt
   `templates/test-suite.py`.

### Phase 5 — Document it

1. Write **Google-style docstrings** on the class and `__init__` — the class docstring is the
   validator's **living spec** (what it checks, its rules, its error codes, what it leaves out of
   scope). Keep it in sync with the code.
2. If your project publishes docs, add a page for the validator — `templates/doc-page.md` is a
   ready starting point (adapt it to your own docs system). At minimum, record the constructor
   parameters, the error codes, and the `details` fields somewhere a caller can find them.
3. Note the new validator wherever your project tracks changes (e.g. a changelog), following your
   own conventions.

### Phase 6 — Quality gates

1. Run your project's linters, type checker, and tests (aegis itself uses `ruff format`,
   `ruff check`, `pyright`, and `pytest` — run whatever your project has configured).
2. **Scope fixes to files you own.** If a gate flags pre-existing files you did not author, report
   it honestly — do not reformat other people's files or claim an unverified build passed.

### Phase 7 — Staging discipline

Stage **only** the files you authored. **Never `git add -A`.** List what you left unstaged and
why, so the boundary between your work and pre-existing changes is explicit.

### Phase 8 — Commits and the review loop

1. Draft commits in your project's commit convention (aegis uses Conventional Commits — `feat` /
   `fix` / `docs` with a scope), one per logical change, and a short, developer-first description of
   what changed and why.
2. On review feedback, run the loop: **reproduce the claim → decide valid or not → if valid, fix
   narrowly + add a test + re-check style and compliance; if not (or if it's a question), reply
   with reasoned, cited evidence.** Reproduce before you agree; push back with evidence when a
   comment is wrong.

See `references/review-and-decisions.md` for the decision gates and the review loop in detail.

## Always gate on the human — ask, don't assume

Use a structured question (with a recommendation) at any point where the answer changes the
design. Do **not** guess on these:

- **Detect-only vs. detect-and-fix** — repair is appropriate only when a *safe*, unambiguous fix
  exists and silently altering the input is less harmful than rejecting it. In many domains the
  correct answer is detect-only.
- **Default strictness / posture** — how tight the out-of-the-box configuration should be.
- **Any place two sources conflict**, or any security/compatibility trade-off.
- **Whether something is safe to share or publish** — the content-safety boundary, when it is not
  obvious (for example, a validator built from internal rules or data).

Everything the spec already settled, just do — reserve questions for genuine trade-offs.

## Guardrails (build these in, every time)

- **Research first, verify empirically.** Don't assume how a library behaves — confirm it by
  running it, or write a test that fails loudly if it ever changes.
- **Safety floor vs. configurable dial** (security validators): the safety floor is enforced in
  code *regardless of configuration*; the dial is only what callers may loosen. A caller must not
  be able to configure away a security guarantee. Confirm this holds — allowlist-driven libraries
  often let configuration re-enable exactly what you meant to block.
- **Never echo raw, potentially sensitive input** in errors or logs — report safe summaries
  (counts, lengths, redacted markers).
- **Reproduce before you agree** with a review comment; keep tests, style, and compliance in
  lockstep with every fix.
- **Run the content-safety scan** in `references/compliance.md` before staging — nothing internal,
  personal, or non-public ships.
- **Don't over-trust existing examples.** Follow the folder and format *conventions*, but a repo
  artifact is not automatically an exemplar; if one is poorly written, don't copy its shape.
