# Vendored Unicode confusables data

This directory holds pinned copies of the Unicode® security mechanism data
used by `scripts/generate_homoglyph_map.py` to produce
`src/aegis/_normalize/_generated.py`.

## Files

| File | Source | Version | Date | Bytes | SHA-256 |
| :--- | :--- | :--- | :--- | ---: | :--- |
| `confusables-17.0.0.txt` | https://www.unicode.org/Public/security/latest/confusables.txt | 17.0.0 | 2025-07-22 | 740 924 | `8fe4f36a094414e17b543d6c6c6962da523ee4be5a3f3e77cfa3c8a25a2d1742` |

Format: `confusables.txt` is line-oriented `SOURCE ; TARGET ; MA # ( glyph
→ glyph ) NAME → NAME` (UTS #39 confusables data). See
https://www.unicode.org/reports/tr39/ for the spec.

## Why vendor?

The generator must be deterministic and offline — runs at code-review time, not
at install or build. Pinning the file at a specific Unicode version means
re-running the generator against the same input always produces the same
`_normalize/_generated.py` byte-for-byte. Bumping the Unicode version is an
explicit two-step: drop in a newer file, rerun the generator, review the diff.

## Refresh procedure

1. Pick a target Unicode version. Confusables data is published at
   `https://www.unicode.org/Public/security/<version>/confusables.txt` and at
   `https://www.unicode.org/Public/security/latest/confusables.txt`.
2. Download:
   ```
   curl -fsSL -o dev/data/unicode/confusables-<version>.txt \
     https://www.unicode.org/Public/security/<version>/confusables.txt
   ```
3. Record version, byte size, and SHA-256 in the table above.
4. Update the default `--version` in `scripts/generate_homoglyph_map.py`.
5. Rerun the generator and review the printed diff summary against the
   previous `_normalize/_generated.py`.

## Licence

Unicode® data files are distributed under the **Unicode® License v3**
(https://www.unicode.org/license.txt) — permissive, requires the copyright and
permission notice to appear with copies of the data or in associated
documentation. The notice from the file header reads:

> © 2025 Unicode®, Inc.
> Unicode and the Unicode Logo are registered trademarks of Unicode, Inc. in
> the U.S. and other countries.
> For terms of use and license, see https://www.unicode.org/terms_of_use.html

The generated `src/aegis/_normalize/_generated.py` is a derivative work under
the same licence; its file header reproduces the attribution.
