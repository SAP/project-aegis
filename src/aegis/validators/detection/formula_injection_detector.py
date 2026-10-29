"""FormulaInjectionDetector — flags input that would begin a spreadsheet formula (CSV injection).

A deterministic, text-only, **detect-only** validator. It runs under ``Guard`` and reports when a
content item, written into a spreadsheet-imported cell (CSV/TSV and the like), would begin a formula
and execute when the file is opened — ``=cmd|'/C calc'!A0``, ``=HYPERLINK(...)``. It never changes
the content. "CSV injection" (CWE-1236 / OWASP) is the common alias; the check is about a *formula
initiator* at a cell's start, not about CSV structure, so the validator is named for the payload,
not the transport.

Obfuscation is decoded before detection
----------------------------------------
Real payloads hide behind encoding layers, so each item is **preprocessed** before inspection:
invisible/zero-width control characters are stripped, then URL-percent, HTML-entity, JavaScript
``\\uXXXX``/``\\xXX``, and CSS ``\\3C`` escapes are decoded repeatedly until the text stops changing
(bounded). Without this, ``%3Dcmd`` or ``&#61;cmd`` would hide the leading ``=`` from the check.
Preprocessing does **not** NFKC-fold, so the full-width initiators ``＝＋－＠`` are matched explicitly.

Detection runs per content item, not on the whole view
-------------------------------------------------------
Unlike the substring-matching detectors (``XssDetector``, ``SqlInjectionDetector``) that scan
``view.render()`` as one flattened string, this validator iterates ``view.items`` and inspects each
``ContentItem`` on its own. The reason is the nature of the check: a formula initiator is
position-sensitive — it only matters as a cell's *first* character. Scanning the single rendered
string would (1) lose each item's own start, and (2) turn every embedded newline (``render()`` joins
items with ``\\n``) into a spurious cell boundary. So each item is treated as one cell candidate: a
trigger at the start of any item is caught, but a formula-looking substring *inside* an item
(``note: =1+1``) is not.

The safety ceiling — read before you rely on this
--------------------------------------------------
This validator is **defense-in-depth, not a primary control**. The authoritative fix is
neutralization at the CSV serializer, using a consumer-specific text neutralizer such as a leading
``'`` where the opening application supports it. Ordinary CSV quoting and escaping are **not**
enough: they keep the field structurally intact, but a quoted value like ``"=1+1"`` still evaluates
as a formula when the file is opened. A guardrail cannot know whether the text will ever become a
spreadsheet cell; a passed result means "no leading formula initiator under this policy", never
"safe in every spreadsheet".

False positives are expected, and accepted by default
-----------------------------------------------------
``-``, ``+``, and ``@`` begin ordinary prose constantly — negative numbers, Markdown bullets,
``@mentions``, ``+1 650...`` phone numbers — so a default scan flags them. This is the accepted cost
of the strict policy, which has no numeric/handle exception (no universal safe-suffix grammar
exists). Two dials soften it without weakening the floor: the ``formula_shape`` detail lets a caller
raise severity on a recognized command shape (DDE / HYPERLINK), and ``ascii_triggers``
narrows class A (e.g. to ``"="``) for prose-heavy deployments.

Error codes
-----------
``FORMULA_INJECTION_DETECTED`` and ``FORMULA_INJECTION_INPUT_TOO_LARGE`` (stable; see
:class:`FormulaInjectionDetector`). Errors never echo the raw input, the matched cell, or any
formula text — only fixed enums, an offset, and counts.
"""

from __future__ import annotations

import re
from typing import Final, NamedTuple

from aegis.metadata import GuardMetadata
from aegis.validators._preprocessing import preprocess
from aegis.validators.base import (
    BaseValidator,
    ContentView,
    FailedResult,
    PassedResult,
    ValidationResult,
)

# Default resource valve. preprocess() runs several decode passes plus a per-character category scan
# over every item, and the start check then runs per item — so unbounded input lets a caller hand
# the validator seconds of work. Cap inspected text at 1 MiB of characters by default; a caller
# whose input size is already trusted passes max_input_chars=None.
_DEFAULT_MAX_INPUT_CHARS: Final = 1024 * 1024

# --- Formula initiators -----------------------------------------------------------------------
#
# The three trigger classes, each mapping a code point to a stable, non-sensitive enum reported in
# ``details``. These are the single source of truth shared by validate(), the tests, and the docs.
#   A (ASCII)      — the CWE-1236 / OWASP set; the default safety floor.
#   F (full-width) — the OWASP full-width forms; matched explicitly because preprocessing never
#                    NFKC-folds them to their ASCII equivalents.
#   C (control)    — OWASP lists a leading TAB/CR/LF as a trigger; a control prefix is itself the
#                    trigger, so it is never treated as padding (see _PADDING).
_ASCII_TRIGGERS: Final = {"=": "EQUALS", "+": "PLUS", "-": "MINUS", "@": "AT"}
_FULLWIDTH_TRIGGERS: Final = {
    "＝": "FULLWIDTH_EQUALS",  # U+FF1D fullwidth equals sign
    "＋": "FULLWIDTH_PLUS",  # U+FF0B fullwidth plus sign
    "－": "FULLWIDTH_MINUS",  # U+FF0D fullwidth hyphen-minus
    "＠": "FULLWIDTH_AT",  # U+FF20 fullwidth commercial at
}
_CONTROL_TRIGGERS: Final = {"\t": "TAB", "\r": "CR", "\n": "LF"}

# Leading padding skipped before locating the effective start (CVE-2026-9673, where leading
# spaces hid a trigger from a remove-based tool). This is the Unicode White_Space set MINUS the
# three class-C control triggers (TAB/CR/LF — a leading one of those is itself a trigger, not
# padding), PLUS zero-width space U+200B and BOM U+FEFF. When strip_invisible_chars is on,
# preprocess() already removes most of these (they are Cc/Cf); the explicit set still matters when
# that toggle is off, and for real spaces (U+0020, NBSP …) that preprocess() never strips.
_PADDING: Final = frozenset(
    "\u000b\u000c"  # line tabulation, form feed
    "\u0020\u0085\u00a0"  # space, next line, no-break space
    "\u1680"  # ogham space mark
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"  # en quad … hair space
    "\u2028\u2029"  # line separator, paragraph separator
    "\u202f\u205f\u3000"  # narrow no-break, medium mathematical, ideographic space
    "\u200b\ufeff"  # zero-width space and BOM (added beyond White_Space)
)

# --- formula_shape severity marker ------------------------------------------------------------
#
# A leading trigger already fails the candidate; this marker only names *which* high-impact shape, if
# any, the decoded text matches, so a caller can RAISE severity on a recognized one. The three shapes
# are command/exfiltration forms worth separating out: DDE protocol syntax (``cmd|...!``), the
# HYPERLINK function, and an explicit ``DDE()`` call. A non-match is reported as ``"unknown"`` — which
# means "none of these three", NOT "safe": other dangerous formulas (e.g. ``=WEBSERVICE(...)``,
# ``=IMPORTXML(...)``) also land in ``"unknown"``. Each pattern is a plain, linear search over the
# decoded candidate — negated character classes, no nested quantifier that can backtrack — so
# computing the marker adds no ReDoS surface of its own.
_SHAPE_DDE_PIPE_BANG: Final = re.compile(r"\|[^|]*!")  # DDE protocol syntax: =cmd|'/C calc'!A0
_SHAPE_HYPERLINK: Final = re.compile(r"HYPERLINK\(", re.IGNORECASE)  # =HYPERLINK("http://x","c")
_SHAPE_DDE: Final = re.compile(r"\bDDE\(", re.IGNORECASE)  # =DDE("cmd";"...")


def _formula_shape(text: str) -> str:
    """Name the recognized high-impact shape a flagged candidate matches, if any.

    Return one of ``"dde_pipe_bang"``, ``"hyperlink"``, ``"dde"``, or ``"unknown"``. This is a
    non-sensitive severity marker only — the leading-trigger check has already decided the verdict,
    which this never changes. A recognized shape lets a caller *raise* severity; ``"unknown"`` means
    none of the three matched, which is **not** a safety signal: other dangerous formulas, including
    exfiltration functions like ``=WEBSERVICE(...)`` and ``=IMPORTXML(...)``, also return ``"unknown"``,
    so a caller must never treat it as safe. ``text`` is the decoded candidate and is never echoed.

    The three shapes are matched by a **best-effort lexical scan, not a formula parser**: a look-alike
    token inside a string literal (``="not really HYPERLINK("``) still matches, so a positive result
    is a ranking hint, never proof the formula actually calls that function.

    Args:
        text: The decoded cell candidate that already matched a leading trigger.

    Returns:
        The most specific matching shape, or ``"unknown"`` when no recognized shape matches.
        ``dde_pipe_bang`` is checked first because the classic DDE payload (``=cmd|...!A0``) carries
        no ``DDE(`` literal; ``HYPERLINK(`` and ``DDE(`` are mutually exclusive function calls.
    """
    if _SHAPE_DDE_PIPE_BANG.search(text):
        return "dde_pipe_bang"
    if _SHAPE_HYPERLINK.search(text):
        return "hyperlink"
    if _SHAPE_DDE.search(text):
        return "dde"
    return "unknown"


class _ScanHit(NamedTuple):
    """One item's leading-formula-initiator match — the fields reported in ``details`` on a hit.

    Returned by :func:`_scan_item` (``None`` when the item passes). Named rather than a bare tuple so
    the five fields cannot be transposed at the call site and map one-to-one onto the
    ``FORMULA_INJECTION_DETECTED`` ``details`` keys.
    """

    match_offset: int  # effective-start position, in code points into the decoded candidate
    padding_skipped: int  # count of leading padding code points skipped to reach it
    trigger_class: str  # "ascii" / "fullwidth" / "control"
    trigger: str  # the fixed trigger enum, e.g. "EQUALS"
    formula_shape: str  # severity marker: "dde_pipe_bang" / "hyperlink" / "dde" / "unknown"


def _scan_item(
    value: str,
    *,
    ascii_triggers: frozenset[str],
    detect_fullwidth: bool,
    detect_control_prefix: bool,
    skip_leading_padding: bool,
    skip_leading_quote: bool,
    detect_formula_shape: bool,
    strip_invisible: bool,
    decode: bool,
) -> _ScanHit | None:
    """Scan one cell candidate; return the trigger at its effective start, or ``None`` to pass.

    Finding the effective start takes four steps, in order:

    1. Decode obfuscation (shared preprocessing), so an encoded ``%3D`` cannot hide the leading ``=``.
    2. Skip one optional leading structural ``"``. In a CSV file a field may be wrapped in double
       quotes as CSV *structure*; the spreadsheet strips those quotes and still sees the content, so a
       quoted ``"=1+1`` evaluates just like ``=1+1``. Skipping one leading ``"`` keeps the trigger
       after it in view (quote-aware detection, as in the CsvHelper library) — this is about CSV
       quoting, not any particular formula type.
    3. Skip the maximal run of leading padding (spaces and other White_Space, zero-width, BOM).
    4. Test the first remaining character against the enabled trigger classes (ASCII, full-width,
       control).

    Any trigger flags — there is no numeric, function-name, or handle exception, because no safe
    suffix grammar exists. The one exception is a leading ``'``: it is the spreadsheet neutralizer,
    so the cell renders as text and the candidate passes.

    Args:
        value: One ``ContentItem.value`` (one cell candidate).
        ascii_triggers: The enabled ASCII initiators (never empty — the safety floor).
        detect_fullwidth: Whether the full-width class (``＝＋－＠``) is enabled.
        detect_control_prefix: Whether the control class (TAB/CR/LF) is enabled.
        skip_leading_padding: Whether to skip leading padding to the effective start.
        skip_leading_quote: Whether to skip one leading structural ``"``.
        detect_formula_shape: Whether to compute the ``formula_shape`` marker on a hit.
        strip_invisible: Forwarded to preprocessing (strip Cc/Cf except ``\\t\\n\\r``).
        decode: Forwarded to preprocessing (decode URL/HTML/JS/CSS layers).

    Returns:
        A :class:`_ScanHit` on a hit, where ``match_offset`` is the effective-start position as a
        count of Unicode code points into the decoded candidate and ``padding_skipped`` is the number
        of padding code points skipped. ``None`` when the effective start is not an enabled trigger,
        or the candidate is empty or all-padding (no effective start). The raw value is never returned.
    """
    decoded = preprocess(value, strip_invisible=strip_invisible, decode=decode)
    if not decoded:
        return None

    offset = 0
    if skip_leading_quote and decoded[0] == '"':
        offset = 1

    padding_skipped = 0
    if skip_leading_padding:
        while offset < len(decoded) and decoded[offset] in _PADDING:
            offset += 1
            padding_skipped += 1

    if offset >= len(decoded):
        return None  # quote-only or all-padding — no effective start to inspect

    start = decoded[offset]
    if start in ascii_triggers:
        trigger_class, trigger = "ascii", _ASCII_TRIGGERS[start]
    elif detect_fullwidth and start in _FULLWIDTH_TRIGGERS:
        trigger_class, trigger = "fullwidth", _FULLWIDTH_TRIGGERS[start]
    elif detect_control_prefix and start in _CONTROL_TRIGGERS:
        trigger_class, trigger = "control", _CONTROL_TRIGGERS[start]
    else:
        return None  # not a trigger — includes a leading ``'`` (the neutralizer) and ordinary prose

    shape = _formula_shape(decoded) if detect_formula_shape else "unknown"
    return _ScanHit(
        match_offset=offset,
        padding_skipped=padding_skipped,
        trigger_class=trigger_class,
        trigger=trigger,
        formula_shape=shape,
    )


class FormulaInjectionDetector(BaseValidator):
    """Flags a content item whose effective start is a spreadsheet formula initiator.

    A detect-only validator for the ``Guard`` path. For each ``ContentItem`` (one cell candidate) it
    decodes obfuscation, skips one optional leading structural ``"`` and any leading padding, then
    reports a failure when the effective start is a formula initiator: ASCII ``= + - @``, full-width
    ``＝＋－＠``, or a control prefix (TAB/CR/LF). Any leading trigger flags — there is no numeric or
    function-name exception — except a leading ``'``, the spreadsheet neutralizer, which passes. It
    never rewrites the content.

    This is **defense-in-depth**, not a primary control — the authoritative fix is neutralization at
    the CSV serializer. A passed result means "no leading formula initiator under this policy", not
    "safe in every spreadsheet". By default ``-``/``+``/``@``-led prose (``-5``, bullets,
    ``@mentions``) is flagged; narrow ``ascii_triggers`` or read the ``formula_shape`` detail to tier
    severity.

    Attributes:
        detect_fullwidth: Flag the full-width initiators ``＝＋－＠``.
        detect_control_prefix: Flag a leading TAB/CR/LF.
        skip_leading_padding: Skip leading padding to the effective start before matching.
        skip_leading_quote: Skip one leading structural ``"`` before matching.
        detect_formula_shape: Compute the ``formula_shape`` severity marker on a hit — names a
            recognized high-impact shape to raise severity on; ``"unknown"`` is not a safety signal, and
            the marker never changes the verdict.
        strip_invisible_chars: Strip zero-width/control characters during preprocessing.
        decode_obfuscation: Decode URL/HTML/JS/CSS encoding layers during preprocessing.

    Example:
        >>> from aegis import Guard
        >>> guard = Guard("input").use(FormulaInjectionDetector())
        >>> result = await guard.validate("=cmd|'/C calc'!A0")
        >>> result.failures[0].error_code
        'FORMULA_INJECTION_DETECTED'
    """

    # Error codes are a released contract: defined once here as the single source of truth shared by
    # validate(), the tests, and the docs, and never renamed or repurposed.
    FORMULA_INJECTION_DETECTED: Final = "FORMULA_INJECTION_DETECTED"
    FORMULA_INJECTION_INPUT_TOO_LARGE: Final = "FORMULA_INJECTION_INPUT_TOO_LARGE"

    def __init__(
        self,
        *,
        ascii_triggers: str = "=+-@",
        detect_fullwidth: bool = True,
        detect_control_prefix: bool = True,
        skip_leading_padding: bool = True,
        skip_leading_quote: bool = True,
        detect_formula_shape: bool = True,
        strip_invisible_chars: bool = True,
        decode_obfuscation: bool = True,
        max_input_chars: int | None = _DEFAULT_MAX_INPUT_CHARS,
    ) -> None:
        """Configure the trigger policy and how aggressively input is decoded before matching.

        The config is narrow by design: the dials narrow or widen the trigger classes and the
        decode/skip passes, but there is no arbitrary user regex or per-value exemption (either would
        reopen a bypass). The ASCII floor cannot be emptied.

        Args:
            ascii_triggers: The class-A initiators to flag, as a string of characters drawn from
                ``=+-@``. Narrow it (e.g. ``"="``) to cut the false-positive cost on prose that leads
                with ``-``/``+``/``@``. Must be non-empty and contain only those four characters.
            detect_fullwidth: Flag the full-width initiators ``＝＋－＠``.
            detect_control_prefix: Flag a leading TAB/CR/LF.
            skip_leading_padding: Skip leading padding (spaces, NBSP, zero-width, …) to the effective
                start before matching, so a padded trigger (``"   =SUM(...)"``) is still caught.
            skip_leading_quote: Skip one leading structural ``"`` before matching, so a CSV-quoted
                field like ``"=1+1`` is still caught (the surrounding quotes are CSV structure, not
                part of the cell content).
            detect_formula_shape: Compute the ``formula_shape`` severity marker (DDE / HYPERLINK) on
                a hit, recorded in ``details`` to raise severity on a recognized shape. A best-effort
                lexical match that can over-report a look-alike token; ``"unknown"`` means no recognized
                shape matched, not that the formula is safe. Never changes the verdict.
            strip_invisible_chars: Strip zero-width/control characters before matching.
            decode_obfuscation: Decode URL/HTML/JS/CSS encoding layers before matching.
            max_input_chars: Resource valve. When the rendered view exceeds it, the input is rejected
                with ``FORMULA_INJECTION_INPUT_TOO_LARGE`` rather than preprocessed and scanned,
                bounding the per-request work (up to ``MAX_DECODE_ITERATIONS`` decode passes per item
                plus a per-character category scan). Defaults to 1 MiB of characters; pass ``None`` to
                lift the cap when the input size is already trusted.

        Raises:
            ValueError: If ``ascii_triggers`` is empty or contains a character outside ``=+-@``, or
                if ``max_input_chars`` is negative.
        """
        # Fail-closed: validate every argument before setting any attribute, so a misconfigured
        # detector raises at construction rather than silently under-enforcing at validate time.
        if not ascii_triggers:
            raise ValueError("ascii_triggers must not be empty; the ASCII safety floor cannot be disabled.")
        invalid = set(ascii_triggers) - _ASCII_TRIGGERS.keys()
        if invalid:
            raise ValueError(f"ascii_triggers may only contain =, +, -, @; got invalid {sorted(invalid)!r}.")
        if max_input_chars is not None and max_input_chars < 0:
            raise ValueError(f"max_input_chars must be non-negative, got {max_input_chars}.")
        self._ascii_triggers = frozenset(ascii_triggers)
        self.detect_fullwidth = detect_fullwidth
        self.detect_control_prefix = detect_control_prefix
        self.skip_leading_padding = skip_leading_padding
        self.skip_leading_quote = skip_leading_quote
        self.detect_formula_shape = detect_formula_shape
        self.strip_invisible_chars = strip_invisible_chars
        self.decode_obfuscation = decode_obfuscation
        self._max_input_chars = max_input_chars

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Report whether any content item begins with a spreadsheet formula initiator.

        Inspects each ``ContentItem`` in ``view.items`` as one cell candidate (see the module
        docstring for why this is per-item, not over the flattened ``view.render()``). The resource
        valve measures the rendered length first (the item lengths plus the newline joiners
        ``render()`` would add), before any decoding, so oversized input is rejected without doing the
        per-item decode/scan work — and without materializing the joined string, which the per-item
        scan never needs.

        Args:
            view: The content under inspection. Never ``None``, but may be empty.
            metadata: Surrounding agent context. Optional and unused here.

        Returns:
            ``PassedResult`` when no item's effective start is an enabled trigger. Otherwise a
            ``FailedResult`` carrying ``FORMULA_INJECTION_DETECTED`` for the earliest offending item
            (lowest ``item_index``), with ``details`` holding ``trigger_class``
            (``ascii``/``fullwidth``/``control``), ``trigger`` (the fixed enum, e.g. ``EQUALS``),
            ``match_offset`` (the effective-start position in code points into the decoded item text),
            ``padding_skipped`` (count), ``item_index``, and ``formula_shape``
            (``dde``/``hyperlink``/``dde_pipe_bang``/``unknown``). Or ``FORMULA_INJECTION_INPUT_TOO_LARGE``
            when ``max_input_chars`` is set and the rendered view exceeds it. The raw input, the
            matched cell, and any formula text are never echoed — only the fixed enums, offset, and
            counts are reported.
        """
        # Resource valve. render() joins items with "\n", so the rendered length is the sum of the
        # item lengths plus one "\n" per gap — we compute that directly instead of materializing the
        # whole joined string, which this validator never needs (it scans each item's own value,
        # below). When the cap is lifted (None) we skip the measurement entirely.
        if self._max_input_chars is not None:
            rendered_length = sum(len(item.value) for item in view.items)
            if view.items:
                rendered_length += len(view.items) - 1  # the "\n" joiners render() would insert
            if rendered_length > self._max_input_chars:
                return FailedResult(
                    error_message=f"Text exceeds the {self._max_input_chars}-character inspection limit.",
                    error_code=self.FORMULA_INJECTION_INPUT_TOO_LARGE,
                    details={"length": rendered_length, "limit": self._max_input_chars},
                )

        # The earliest offending item wins: items are scanned in order and the first hit is returned,
        # so the reported item_index is the lowest offending one. Each item is a single cell candidate
        # with one effective start, so exactly one trigger is ever reported — the output stays minimal
        # and never maps out the whole payload.
        for item_index, item in enumerate(view.items):
            hit = _scan_item(
                item.value,
                ascii_triggers=self._ascii_triggers,
                detect_fullwidth=self.detect_fullwidth,
                detect_control_prefix=self.detect_control_prefix,
                skip_leading_padding=self.skip_leading_padding,
                skip_leading_quote=self.skip_leading_quote,
                detect_formula_shape=self.detect_formula_shape,
                strip_invisible=self.strip_invisible_chars,
                decode=self.decode_obfuscation,
            )
            if hit is not None:
                return FailedResult(
                    error_message=(
                        f"A content item's effective start is a formula initiator ({hit.trigger_class}) "
                        f"at offset {hit.match_offset}."
                    ),
                    error_code=self.FORMULA_INJECTION_DETECTED,
                    details={
                        "trigger_class": hit.trigger_class,
                        "trigger": hit.trigger,
                        "match_offset": hit.match_offset,
                        "padding_skipped": hit.padding_skipped,
                        "item_index": item_index,
                        "formula_shape": hit.formula_shape,
                    },
                )
        return PassedResult()
