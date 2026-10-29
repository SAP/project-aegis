#!/usr/bin/env python3
"""Generate `src/aegis/_normalize/_generated.py` from the vendored Unicode confusables file.

This is a *developer* tool — it runs by hand when bumping the Unicode security
data version, **not** at install or import time. The `aegis` library imports
the generated dict directly; users never run this script.

Pipeline:

1. Read the vendored ``dev/data/unicode/confusables-<version>.txt`` (line
   oriented, ``SOURCE ; TARGET ; MA # ( glyph -> glyph ) NAME -> NAME``).
2. Filter to entries usable as a single ``str.translate()`` table:
   * SOURCE is a single (non-ASCII) codepoint.
   * TARGET is a single ASCII letter ``[A-Za-z]``.
   * SOURCE does not already NFKC-fold to its target (NFKC runs upstream of
     the homoglyph translate step, so listing those would be dead weight).
3. Write the result to ``src/aegis/_normalize/_generated.py`` as a literal
   ``GENERATED_MAP: dict[int, str]``, sorted by source codepoint, each entry
   commented with the Unicode source name.
4. Print a diff summary against the previous generated file (added / removed
   / changed entries) so reviewers can sanity-check Unicode version bumps.

Determinism: re-running with the same vendored input file produces a
byte-for-byte identical output. No network access; no system clock embedded
in the output (a fixed banner refers to the input file's recorded date and
SHA-256).

Usage::

    python scripts/generate_homoglyph_map.py
    python scripts/generate_homoglyph_map.py --version 17.0.0
    python scripts/generate_homoglyph_map.py --check    # exit 1 if regen would change the file

Licence: the vendored confusables data is published under the Unicode License
v3. The generated file's header reproduces the attribution; do not strip it.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Default Unicode security data version. Bump in lockstep with the vendored
# file under ``dev/data/unicode/confusables-<version>.txt``.
DEFAULT_VERSION = "17.0.0"

# Repo-relative paths. Resolved from this script's location so the generator
# works regardless of the caller's CWD.
REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED_DIR = REPO_ROOT / "dev" / "data" / "unicode"
OUTPUT_PATH = REPO_ROOT / "src" / "aegis" / "_normalize" / "_generated.py"

# Match a single confusables.txt mapping line. The TARGET column may list
# multiple codepoints separated by spaces (e.g. multi-char prototype like
# ``rn -> m``); we capture the whole field and reject multi-codepoint targets
# in the filter step below.
_LINE_RE = re.compile(
    r"""^
    \s* (?P<source> [0-9A-Fa-f]+ (?:\s+ [0-9A-Fa-f]+)* ) \s* ;
    \s* (?P<target> [0-9A-Fa-f]+ (?:\s+ [0-9A-Fa-f]+)* ) \s* ;
    \s* MA \s* \#
    .* $
    """,
    re.VERBOSE,
)

# Match the comment field's "SOURCE_NAME -> TARGET_NAME" tail. Used purely to
# annotate generated entries; if it does not match we fall back to the
# ``unicodedata.name`` of the source codepoint.
_COMMENT_NAMES_RE = re.compile(r"\#\s*\([^)]*\)\s*(?P<src_name>[^→\->]+?)\s*(?:→|->)\s*(?P<dst_name>.+?)\s*(?:\#.*)?$")


@dataclass(frozen=True)
class _Entry:
    """One row of the generated map.

    ``codepoint`` is the SOURCE codepoint (the confusable). ``ascii_target`` is
    the single ASCII letter we fold it to. ``source_name`` is the Unicode name
    used as a code comment.
    """

    codepoint: int
    ascii_target: str
    source_name: str


def _parse_confusables(path: Path) -> list[_Entry]:
    """Parse the Unicode confusables file into single-source/single-ASCII-target entries."""

    entries: list[_Entry] = []
    skipped_multi_source = 0
    skipped_multi_target = 0
    skipped_non_ascii_target = 0
    skipped_ascii_source = 0
    skipped_nfkc_redundant = 0

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        # Strip comments-only and blank lines fast.
        if not raw_line or raw_line.lstrip().startswith("#"):
            continue
        m = _LINE_RE.match(raw_line)
        if not m:
            # The file has a few non-MA legacy artifacts; safe to ignore.
            continue

        source_field = m.group("source").split()
        target_field = m.group("target").split()

        # We only support single-codepoint sources (``str.translate`` keys).
        if len(source_field) != 1:
            skipped_multi_source += 1
            continue
        # We only fold to a single ASCII letter for v1 (digits / multi-char
        # ASCII targets are deliberately out of scope per the plan).
        if len(target_field) != 1:
            skipped_multi_target += 1
            continue

        source_cp = int(source_field[0], 16)
        target_cp = int(target_field[0], 16)

        # ASCII -> anything has no place in a homoglyph fold; skip.
        if source_cp < 0x80:
            skipped_ascii_source += 1
            continue
        # Target must be an ASCII letter A-Za-z. Drop punctuation, digits,
        # whitespace, control chars.
        if not (0x41 <= target_cp <= 0x5A or 0x61 <= target_cp <= 0x7A):
            skipped_non_ascii_target += 1
            continue

        # Skip entries already handled by NFKC upstream of the translate step
        # (math alphanumerics, fullwidth, ligatures, ...). A fold counts as
        # NFKC-redundant when NFKC of the source string equals the target
        # ASCII char (or starts with it; NFKC may produce combining marks).
        source_char = chr(source_cp)
        target_char = chr(target_cp)
        nfkc = unicodedata.normalize("NFKC", source_char)
        if nfkc == target_char:
            skipped_nfkc_redundant += 1
            continue

        # Annotate with the Unicode source name. Prefer the comment field
        # (it is what the file authors wrote); fall back to unicodedata.
        comment_match = _COMMENT_NAMES_RE.search(raw_line)
        if comment_match:
            source_name = comment_match.group("src_name").strip()
        else:
            try:
                source_name = unicodedata.name(source_char)
            except ValueError:
                source_name = f"U+{source_cp:04X}"

        entries.append(_Entry(codepoint=source_cp, ascii_target=target_char, source_name=source_name))

    # The Unicode data sometimes lists the same source multiple times across
    # different equivalence rounds — keep the first occurrence (file order is
    # stable across a given Unicode version).
    seen: dict[int, _Entry] = {}
    for entry in entries:
        seen.setdefault(entry.codepoint, entry)

    print(
        f"Parsed {len(entries)} candidate rows -> {len(seen)} unique sources. "
        f"Skipped: multi-cp source={skipped_multi_source}, "
        f"multi-cp target={skipped_multi_target}, "
        f"non-ASCII target={skipped_non_ascii_target}, "
        f"ASCII source={skipped_ascii_source}, "
        f"NFKC-redundant={skipped_nfkc_redundant}.",
        file=sys.stderr,
    )
    return sorted(seen.values(), key=lambda e: e.codepoint)


def _render(entries: list[_Entry], *, version: str, vendored_path: Path, sha256: str) -> str:
    """Render the entries as a Python source file."""

    rel_vendored = vendored_path.relative_to(REPO_ROOT)

    header = f'''"""Mechanically generated homoglyph -> ASCII map (DO NOT EDIT BY HAND).

Generated from the vendored Unicode confusables data:

    Source file: {rel_vendored.as_posix()}
    Unicode UTS #39 version: {version}
    SHA-256: {sha256}

Re-generate via::

    python scripts/generate_homoglyph_map.py --version {version}

The generator script lives at ``scripts/generate_homoglyph_map.py``. Its
filtering rules and the rationale for the chosen subset are documented in
that file's module docstring.

This file is consumed by ``src/aegis/_normalize/_homoglyphs.py``, which merges
``GENERATED_MAP`` with curated overrides before exposing the public
``HOMOGLYPH_MAP``.

Unicode License v3 attribution (required for redistribution of derivative
data): "© 2025 Unicode®, Inc. Unicode and the Unicode Logo are registered
trademarks of Unicode, Inc. in the U.S. and other countries. For terms of
use and license, see https://www.unicode.org/terms_of_use.html".
"""

from __future__ import annotations

GENERATED_MAP: dict[int, str] = {{
'''

    body_lines: list[str] = []
    for entry in entries:
        # Each entry: ``    0x0430: "a",  # CYRILLIC SMALL LETTER A``
        # Quote escape: target is always [A-Za-z], no escape needed.
        body_lines.append(f'    0x{entry.codepoint:04X}: "{entry.ascii_target}",  # {entry.source_name}')
    footer = "}\n"
    return header + "\n".join(body_lines) + ("\n" if body_lines else "") + footer


def _diff_against_existing(new_entries: list[_Entry], output_path: Path) -> None:
    """Print a brief added/removed/changed summary against the previous file."""

    if not output_path.exists():
        print("No previous generated file -- this is a fresh write.", file=sys.stderr)
        return

    # Lazy import: only touch the existing module when it exists, and via
    # source parsing rather than import to keep the generator independent of
    # whatever state the package is in.
    pat = re.compile(r"^\s*0x([0-9A-Fa-f]+):\s*\"([^\"]+)\"")
    existing: dict[int, str] = {}
    for line in output_path.read_text(encoding="utf-8").splitlines():
        m = pat.match(line)
        if m:
            existing[int(m.group(1), 16)] = m.group(2)

    new_dict = {e.codepoint: e.ascii_target for e in new_entries}

    added = sorted(set(new_dict) - set(existing))
    removed = sorted(set(existing) - set(new_dict))
    changed = sorted(cp for cp in (set(new_dict) & set(existing)) if new_dict[cp] != existing[cp])
    print(
        f"Diff vs previous _generated.py: +{len(added)} -{len(removed)} ~{len(changed)} (total {len(new_dict)}).",
        file=sys.stderr,
    )
    # Show up to 10 of each kind for spot-checking. Longer lists go to a
    # follow-up review, not the terminal.
    for label, cps in (("added", added), ("removed", removed), ("changed", changed)):
        for cp in cps[:10]:
            old = existing.get(cp, "-")
            new = new_dict.get(cp, "-")
            print(f"  {label:7s} U+{cp:04X}  {old} -> {new}", file=sys.stderr)
        if len(cps) > 10:
            print(f"  ... ({len(cps) - 10} more {label})", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help=f"Unicode security data version (default: {DEFAULT_VERSION})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the output file would differ from the regeneration.",
    )
    args = parser.parse_args(argv)

    vendored = VENDORED_DIR / f"confusables-{args.version}.txt"
    if not vendored.exists():
        print(f"error: vendored file not found: {vendored}", file=sys.stderr)
        return 2

    raw_bytes = vendored.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    entries = _parse_confusables(vendored)
    rendered = _render(entries, version=args.version, vendored_path=vendored, sha256=sha256)

    if args.check:
        existing = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
        if existing != rendered:
            print(
                f"error: {OUTPUT_PATH.relative_to(REPO_ROOT)} is out of date (rerun without --check).",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT_PATH.relative_to(REPO_ROOT)} is up to date.", file=sys.stderr)
        return 0

    _diff_against_existing(entries, OUTPUT_PATH)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(
        f"Wrote {len(entries)} entries to {OUTPUT_PATH.relative_to(REPO_ROOT)}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
