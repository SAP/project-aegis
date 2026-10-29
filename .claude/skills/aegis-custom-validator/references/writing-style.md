# Writing Style — Comments & Documentation

The tone and style for **every code comment, docstring, and documentation page** you write for a
validator (Python code and Markdown docs).

The goal is simple: a developer reading the code for the **first time** should understand it
without anyone to translate or summarize it for them.

> **Public by default.** Every comment and doc here is public once the repository is open-sourced.
> Write for a worldwide audience. Never include personal data, internal user IDs, internal URLs,
> ticket IDs, or code names — see `compliance.md`.

> The snippets below illustrate the *style*, not the current API. Do not copy them as canonical;
> the contract lives in the installed `aegis` package (read the docstrings on the types you import).

---

## The core principle

**Write for a new developer reading the code for the first time.** Do not assume prior context. If
a reader needs to know something to understand the code, say it — plainly, once, and in order.

**State conclusions, don't ask questions.** A committed comment is not a discussion: state the
decision as fact and give the reason. Save open questions for the pull request.

---

## The five rules

### 1. Explain HOW and WHY — the WHY matters most

- **HOW**: what the code does, when it is not obvious from reading it.
- **WHY**: the design decision behind it. This is what gets lost over time and is the most valuable
  thing a comment can preserve.

A future developer can always read *what* the code does; they cannot read *why* unless you tell
them. Write the WHY as natural prose — not tagged with a `# WHY:` label.

Use inline comments sparingly (as PEP 8 advises): reach for one only when the WHY is not obvious
from the code itself. Anything API-level — what a function does, its parameters, its return — belongs
in the docstring (see below), not in an inline comment.

```python
# We check `was_mutated` instead of comparing the full item list because only
# the fact that a mutation happened matters here — comparing every item would
# be slower and would expose validator internals we want to keep hidden.
if result.was_mutated and result.output is not None:
    ...
```

### 2. Keep it simple and concise — match the comment to the code

Two failure modes to avoid:

1. **Cryptic** — a complicated comment on simple code. If the code is obvious, add little or none.
2. **Verbose** — a comment so long the reader must summarize it themselves. If you can say it in
   one clear sentence, do.

```python
# BAD (cryptic): forces the reader to decode it.
# Idempotent projection of the edit delta onto the canonical view surface.

# GOOD: says exactly what matters, once.
# Rebuild the edits into a ContentView so downstream code receives the same
# shape it gets everywhere else.
```

### 3. Structure logic step by step

When a comment covers more than one point, or gives more than one reason, use a **numbered list**.
Lead with the observation, then the reasoning, then the conclusion.

```python
# We keep both result classes frozen for two reasons:
# 1. A result should never be mutated after construction — especially its
#    error code and error message.
# 2. The related content types are already frozen, so this keeps the codebase
#    consistent and stricter.
```

### 4. Ground it in specifics

Refer to the exact function, class, or file you mean — never vaguely. Include a short snippet when
it makes the point clearer.

### 5. Explain error handling explicitly

When code handles an error, make the mechanism clear: what can fail, what is caught, and what
happens as a result. Do not leave the reader to guess the failure path.

```python
try:
    module = import_module(optional_dependency)
except ImportError as err:
    # This validator is optional. If its dependency is missing we raise with a
    # clear install hint instead of a raw ImportError, so the user knows exactly
    # how to fix it.
    raise ImportError(
        "The extra for this validator is required. Install it with: "
        "pip install '<package>[<extra>]'"
    ) from err
```

---

## Function & data-structure docstrings

Write a docstring a first-time reader can act on:

- State what it does in one clear sentence.
- **Use the imperative mood for functions** — "Build a ContentView", not "Builds a ContentView"
  (PEP 257). Class and dataclass docstrings *describe* instead: "The outcome of a validator run…".
- Describe the parameters and return value, including error codes or raised exceptions where
  relevant.
- **Add a short usage example when it helps** — not mandatory; include it when usage is not obvious
  from the signature.
- **Keep the docstring in sync with the code.** A docstring that no longer matches the signature is
  worse than none — the reader trusts it and is misled. Update it in the same change.

```python
def from_text(text: str) -> ContentView:
    """Build a ContentView from a plain string.

    Use this when a validator works on raw text and needs it in the standard
    ContentView shape the rest of the pipeline expects.

    Args:
        text: The raw input text to wrap.

    Returns:
        A ContentView containing a single TEXT item holding `text`.
    """
```

For a dataclass, state what it represents, document each field, and — when a design decision shaped
the type — record the WHY, exactly as in a comment.

```python
@dataclass(frozen=True)
class FailedResult:
    """The outcome of a validator run that rejected its input.

    Frozen on purpose: the error code and message a caller acts on must stay
    exactly as the validator produced them, and it keeps this type consistent
    with the other frozen result types.

    Attributes:
        error_code: The machine-readable reason the input was rejected.
        error_message: A human-readable explanation, safe to show to the caller.
    """
```

> These conventions follow established practice (PEP 257 and widely used Python style guidance).
> Their shared core lesson — comment at the level of **intent**, not to restate the code — is Rule 1
> above.

---

## Documentation pages (Markdown)

The five rules carry over to prose docs almost unchanged; a doc page has one extra job: orient the
reader before the detail.

- **Open with what the page is for, in one sentence.** A first-time reader should know within the
  first line whether this page answers their question.
- **Lead with the observation, then the reasoning, then the conclusion** — same order as a comment.
  Numbered lists for multi-step reasoning, bullets for options.
- **Ground every claim in a real symbol, command, or file**, and show a runnable snippet where it
  helps. A doc example must use the **public import path and current signature** — never an internal
  one.
- **Match length to the point.** Prefer a short, complete page over an exhaustive one.
- Everything in the content-safety note above applies: doc pages are public.

---

## Quick checklist

- [ ] A first-time reader would understand it without extra help.
- [ ] It explains the **WHY** (the design decision), not just the what.
- [ ] It is as short as it can be while complete — neither cryptic nor verbose.
- [ ] Multi-point reasoning is a numbered list.
- [ ] It refers to specific code, with a snippet where that helps.
- [ ] Error handling, if any, is explained clearly.
- [ ] Docstrings use the imperative mood for functions and match the current signature.
- [ ] It is grammatically correct.
- [ ] It contains nothing internal, personal, or non-public (see `compliance.md`).
