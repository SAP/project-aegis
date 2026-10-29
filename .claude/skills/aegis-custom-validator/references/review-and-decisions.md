# Decisions & the Review Loop

The two phases where the real engineering happens are **research/decisions** and **review**.
Coding is nearly mechanical once the spec and contract are pinned. This reference captures how to
handle both: when to stop and ask a human, and how to respond to review feedback.

---

## Keep a human in the loop

Detect genuine decision points and **ask** — do not guess. Ask with a structured, multiple-choice
question that includes a recommendation and the reason for it. Everything the spec already settled,
just do.

Good candidates to ask about:

- **Detect-only vs. detect-and-fix.** Repair is appropriate only when a *safe*, unambiguous fix
  exists **and** silently altering the input is less harmful than rejecting it. In many domains the
  correct answer is detect-only. This choice determines whether you implement `mutate()` at all.
- **Default strictness / posture.** How tight should the out-of-the-box configuration be? Prefer
  safe-tight defaults, but confirm — the safe default sometimes breaks a legitimate existing use.
- **Any place two sources conflict.** If two references propose different designs, present both,
  recommend one, and record the rejected alternative and why.
- **Any security or compatibility trade-off.** A choice that trades safety for convenience (or the
  reverse) is the user's to make, not yours to assume. For example, a sanitizer could strip a risky
  HTML attribute by default (safer, but it drops a feature some callers rely on) or keep it (more
  compatible, but a wider attack surface) — present both and let the user pick the default.
- **Whether a document may ship publicly.** The content-safety boundary is a human decision when
  it is not obvious. For example, a design or research note that still names an internal team,
  ticket, or system needs a human call — scrub it and publish, or keep it in a private location, but
  never publish it on a guess.

The examples above are illustrations, not a checklist — the real decision points are case by case.
Treat them as a cue to stop and ask whenever a choice genuinely changes the design.

When a user pushes for deeper analysis instead of a quick pick, do the research and return a
recommendation with evidence — not a coin flip.

---

## The safety floor vs. the configurable dial

For a **security** validator, separate two things and treat them differently:

1. **The safety floor** — the guarantees enforced in code *regardless of configuration*. A caller
   must never be able to configure these away. Strip the dangerous thing unconditionally.
2. **The configurable dial** — the richness a caller may tune (which optional, safe features to
   allow). This is what configuration controls.

The failure mode to watch for: a purely allowlist-driven dependency has **no special-casing** —
anything a caller allowlists survives, including the dangerous constructs you meant to block. If
you build on one, enforce the floor yourself, above the library, so no configuration can lower it.
An empty filter set means "allow none," not "allow all" — verify which your building blocks mean.

---

## The review-response loop

Each review item is its own mini-cycle. The loop holds whether the item is a reviewer comment, a
change someone already made for you to vet, or an open question:

1. **Reproduce the claim.** Before you agree with a review comment, reproduce it — run the case,
   observe the actual behavior. Many comments are right; some are not, and you can only tell by
   reproducing.
2. **Decide valid or not.**
   - **Valid:** fix it *narrowly*. Add or update a test that would have caught it. Re-check the
     writing style and the content-safety scan on what you changed. Make the docstring's promise
     true if the fix changed behavior.
   - **Not valid, or a question:** reply with a reasoned, cited answer. Push back with evidence.
3. **Keep tests, style, and compliance in lockstep** with every fix — never let them drift apart.

---

## Hard parts to expect (they need judgment, not automation)

- **Reconciling conflicting research** and recording the rejected alternative with its reason.
- **Judging what "safe defaults" mean** for a new domain.
- **Document placement and structure** — the living spec belongs in the class docstring, usage in
  a doc page (if your project publishes docs), and any decision record wherever your project keeps
  them. One owner per layer; don't duplicate.
- **Compliance scrubs** that need domain judgment about what counts as "internal."

---

## Cross-cutting principles

- **Research first, and verify empirically** — convert undocumented behavior into a verified fact
  or a test-enforced assumption.
- **Spec before code; keep the spec living in the docstring.**
- **Never echo raw, potentially sensitive input** in errors or logs — report safe summaries.
- **Reproduce before you agree**; fix narrowly.
- **Staging discipline** — stage only what you authored; name what you left and why.
- **Match the house style and conventions** rather than inventing your own.
- **Don't assume an existing file is a good example.** Follow the repo's conventions — where files
  go, how they are named, how a doc is structured — but not the style of one specific file just
  because it is already there. If an existing validator or doc is poorly written, copy the
  convention, not its mistakes.
- **Ask on real trade-offs; act on settled ones.**
