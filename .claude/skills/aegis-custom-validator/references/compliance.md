# Content Safety & Compliance

Everything you write for a validator — code, comments, docstrings, docs, data files, commit
messages, and changelog entries — outlives the moment you write it and is often read far more
widely than you expect. If the validator (or the repo it lives in) is ever shared, published, or
open-sourced, all of it becomes **public and permanent**. Clear this bar for every file before you
stage it — it is cheaper than scrubbing history later.

This reference is deliberately generic. For your project's specific rules (license, contribution
process, security disclosure), read its `CONTRIBUTING.md` and any governance docs it points to — do
not duplicate project-specific detail here.

---

## Content-safety scan — must never appear anywhere

- **Personal data** — real names, internal user IDs, usernames, personal or non-public work email
  addresses, phone numbers.
- **Internal URLs** — internal wikis, document stores, or internal source-control hosts. Use the
  public project URL, or omit.
- **Internal ticket or work-item IDs** — issue trackers, work items, or any internal tracking
  reference. Branch names too.
- **Internal code names or unreleased product names** — use only public, released names.
- **Internal infrastructure references** — internal package registries/indexes, internal CI runner
  labels, and similar. Use public registries and public CI runners.
- **Proprietary editorial or methodology content** — brand-voice rules, curated terminology with
  internal rationale, or internal communication rules. This belongs in a private package, never
  here.
- **Confidential information or trade secrets** — anything proprietary or learned under an NDA,
  regardless of form, even if not covered by a bullet above.
- **Comments aimed at individuals** — write as if the world is reading, because it is.

Run a residual scan (grep the diff for the patterns above) before you stage. If you relocate a
file that had internal references, scrub it and confirm the scan is clean afterward.

---

## Dependencies & licensing

- New runtime dependencies must be **license-compatible with the project's license**. Permissive
  licenses (MIT, BSD, ISC, Apache-2.0) are generally safe.
- **Strong copyleft (GPL, AGPL, CC share-alike) is off-limits** as a dependency.
- No commercial third-party code; nothing known to be insecure, deprecated, or infringing.
- Make optional/heavy dependencies **lazy**: import inside the consumer and raise a clear
  `ImportError` with an install hint (`pip install '<package>[<extra>]'`) if missing. This keeps a
  heavy validator from loading its dependencies for everyone.
- When you add a dependency, update the project manifest, the install docs, and the changelog.

---

## Bundled data

If a validator ships data (word lists, terminology maps, pattern corpora):

- Scrub the file **in its entirety** against the content-safety scan above — data files are a
  common hiding place for personal data, internal URLs, and proprietary rationale.
- Confirm it is **redistributable under a license compatible with the project's**, and free of
  personal or confidential content. Data you don't have the right to publish can block a release.
- **Centralize** shared data behind a single **path-hardened accessor** — resolve to an absolute
  path and confirm it stays within the data root, so a caller-supplied path can't traverse out.
- Attribute vendored third-party data correctly via the project's bulk license annotation; do not
  edit third-party files to insert headers.
- Data that cannot be fully cleared belongs in a private package, not a public repo.

---

## Per-file licensing headers

Follow the project's license-declaration convention (many projects use SPDX headers per file, plus
a bulk annotation for generated or vendored files). Check `CONTRIBUTING.md` for the exact header
text and tooling, and confirm the license linter is clean before opening the PR.

---

## Security notes for a validator

- **No hardcoded credentials, keys, tokens, or secrets** — ever. Use environment variables or the
  SDK's credential resolution.
- A validator is itself a security surface: don't introduce injection, traversal, or similar
  weaknesses in the validator's own code.
- **Never echo raw, potentially sensitive input** in an error, message, or log — report a safe
  summary (a count, a length, a redacted marker).
- Security-relevant functionality (input sanitization, content filtering, personal-data detection,
  cryptography) may be subject to export-control review in some organizations. If your validator
  falls in this category, check the project's governance process before release.

---

## Before you stage — checklist

- [ ] Content-safety scan is clean (nothing personal, internal, or non-public).
- [ ] New dependencies are license-compatible and declared in the manifest + changelog.
- [ ] Bundled data (if any) is redistributable, scrubbed, and loaded through a hardened accessor.
- [ ] License headers / annotations follow the project convention; the license linter passes.
- [ ] No secrets anywhere; no raw sensitive input echoed in errors or logs.
- [ ] You staged only files you authored, and can name what you left unstaged and why.
