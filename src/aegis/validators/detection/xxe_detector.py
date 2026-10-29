"""XxeDetector — flags XML External Entity (XXE) constructs in text input.

A deterministic, text-only, **detect-only** validator. It runs under ``Guard`` and reports when a
message carries an XXE-capable XML construct — an external entity, an external or inline DTD, an
entity declaration, or XInclude; it never changes the content. Use it as a cheap, non-LLM check at
a trust boundary on input (or model output) that could, now or downstream, reach an XML processor.

Obfuscation is decoded before detection
----------------------------------------
Real payloads hide behind encoding layers, so the content is **preprocessed** before any
pattern runs: invisible/zero-width control characters are stripped, then URL-percent, HTML-entity,
JavaScript ``\\uXXXX``/``\\xXX``, and CSS ``\\3C`` escapes are decoded repeatedly until the text
stops changing (bounded). Without this, ``%3C!DOCTYPE`` or ``&#x3C;!ENTITY`` would sail past a
raw-text matcher.

These are **deobfuscation** steps — they reveal a hidden payload (``%3C`` -> ``<``) — not
**normalization** (NFKC/homoglyph/case folding), which the engine handles separately via
``Guard(normalize=...)``. So ``strip_invisible_chars`` is this validator's own strip, not that
pipeline reused.

Declarations inside inert regions are not flagged
-------------------------------------------------
A ``<!ENTITY>`` written inside a complete comment, ``<![CDATA[ ]]>`` section, or processing
instruction is data, not markup — an XML processor never acts on it — so those complete regions are
blanked out before the construct patterns run. This removes the classic "a matcher fires on
``<!DOCTYPE`` inside a comment" false positive without parsing hostile XML. An *unterminated* region
is the opposite case: it cannot make what follows inert, so a construct after an unclosed comment or
CDATA is still flagged (fail toward detection). Masking is applied to **each item on its own**: an
inert-region opener in one item cannot neutralize a construct in another, since the items may be
processed independently downstream (one part is never trusted to comment out the next).

Matching spans the view; masking does not
------------------------------------------
Each item is masked on its own, then the masked items are flattened through
``ContentView.render()`` and the construct patterns run over that whole flattened text — so a
construct split across an item boundary (``<!DOCTYPE foo`` + ``SYSTEM "...">``) is still caught
(``render()`` joins items with whitespace, matched by ``\\s``), while an inert region in one item
cannot reach into another (see above). Flattening is delegated to ``render()`` rather than re-joined
here, so the scan follows whatever ``render()`` does as the view grows new content types.

The safety ceiling — read before you rely on this
--------------------------------------------------
This validator is **defense-in-depth, not a primary control**. The authoritative XXE defense is a
hardened XML parser at the processing site — DTDs disabled, external entities disabled, no external
resolution. A passed result means "no *known* XXE construct found", never "provably safe". Byte-level
transport tricks (UTF-16/UTF-32 BOM, base64- or archive-wrapped XML) are out of scope by design: they
belong to an upstream decode/extraction stage that hands this validator a decoded ``str``.

Error codes
-----------
``XXE_DETECTED`` and ``XXE_INPUT_TOO_LARGE`` (stable; see :class:`XxeDetector`). Errors never echo
the raw input — only safe summaries.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Final

from aegis.metadata import GuardMetadata
from aegis.validators._preprocessing import preprocess
from aegis.validators.base import (
    BaseValidator,
    ContentItem,
    ContentView,
    FailedResult,
    PassedResult,
    ValidationResult,
)

# Default resource valve. preprocess() runs several decode passes plus a per-character category
# scan over the whole string, and every pattern then scans it — so unbounded input lets a caller
# hand the validator seconds of work. Cap inspected text at 1 MiB of characters by default; a
# caller whose input size is already trusted passes max_input_chars=None.
_DEFAULT_MAX_INPUT_CHARS: Final = 1024 * 1024


# --- Inert-region masking ---------------------------------------------------------------------
#
# A construct inside a *complete* comment / CDATA / PI is inert, so those regions are blanked out
# (replaced with equal-length filler, preserving offsets) before the construct patterns run. We do
# this with a left-to-right opener/closer scan rather than a non-greedy regex: a document full of
# unclosed ``<!--`` openers would make a regex rescan to end-of-string from each one (quadratic),
# and a validator must not add a DoS vector of its own. The scan is linear — each closer search
# jumps past the region it consumes.
_INERT_OPENER: Final = re.compile(r"<!--|<!\[CDATA\[|<\?")
_INERT_CLOSERS: Final = {"<!--": "-->", "<![CDATA[": "]]>", "<?": "?>"}


def _mask_inert_regions(text: str) -> str:
    """Blank out complete comment / CDATA / PI regions, leaving every offset in place.

    Each complete ``<!-- -->`` / ``<![CDATA[ ]]>`` / ``<? ?>`` span is replaced with spaces of the
    same length, so a construct the scan later reports keeps the same offset it had in ``text``. An
    *unterminated* region (an opener with no closer) is deliberately left untouched: it cannot make
    the text after it inert, so masking stops there and the remainder stays visible to the patterns
    — the "fail toward detection" rule.
    """
    pieces: list[str] = []
    cursor = 0
    while True:
        opener = _INERT_OPENER.search(text, cursor)
        if opener is None:
            pieces.append(text[cursor:])
            break
        closer = _INERT_CLOSERS[opener.group(0)]
        close_at = text.find(closer, opener.end())
        if close_at == -1:
            # Unterminated region: keep the rest as-is so a construct after it is still flagged.
            pieces.append(text[cursor:])
            break
        region_end = close_at + len(closer)
        pieces.append(text[cursor : opener.start()])  # text before the region, unchanged
        pieces.append(" " * (region_end - opener.start()))  # region blanked, same length
        cursor = region_end
    return "".join(pieces)


# --- XXE detection patterns -------------------------------------------------------------------
#
# Each pattern recognizes one XXE-capable construct and is paired with a stable, non-sensitive label
# naming the construct class. The label is safe to report — it names the *kind* of construct, never
# the matched text (which could carry a file path or URL). Matching is case-insensitive: the XML
# grammar's keywords are uppercase, so this is strictly conservative (it also catches ``<!doctype``),
# and every pattern is anchored on ``<!`` (or the XInclude namespace) — high-signal enough that
# ordinary prose containing the words "system"/"entity"/"doctype" is not flagged. Patterns are
# linear: disjoint character classes, no nested quantifier (no ReDoS backtracking), and every
# quantified body stops at the next token — ``[^\s>]+`` at whitespace, ``[^<>\[]*`` at ``<`` — so a
# run of repeated openers (``<!DOCTYPE <!DOCTYPE …``) cannot make ``re.search`` rescan from each one
# to end-of-string (no quadratic across match offsets either).
#
# "Capability-bearing" is the dividing line: a construct is flagged only when it can actually cause
# external resolution or entity expansion. A bare ``<!DOCTYPE html>`` (no external ID, no internal
# subset) carries no capability and is inert by default — flagging it would fire on nearly every
# HTML page. The opt-in ``bare_doctype`` rule exists for deployments that want reject-all-DOCTYPE.
#
# Order matters: the earliest-offset match wins and ties break by position in this tuple, so at a
# shared start offset the more specific/severe label is reported first.
_XXE_PATTERNS: Final = (
    # External entity (general or parameter) carrying an external ID — the file-read / SSRF vector.
    # The optional ``% `` lets an *external* parameter-entity declaration (``<!ENTITY % x SYSTEM>``)
    # match here, ranked ahead of the plain parameter_entity rule so it reports as external_entity.
    ("external_entity", re.compile(r"<!ENTITY\s+(?:%\s+)?[^\s>]+\s+(?:SYSTEM|PUBLIC)\b", re.IGNORECASE)),
    # External DTD: a DOCTYPE pointing at an external subset (``<!DOCTYPE foo SYSTEM "...">``).
    ("external_dtd", re.compile(r"<!DOCTYPE\s+[^\s>]+\s+(?:SYSTEM|PUBLIC)\b", re.IGNORECASE)),
    # Internal DTD subset: a DOCTYPE opening an inline ``[ ... ]`` subset, where entity bombs and
    # internal entities live. The ``[^<>\[]*`` body stops at the first ``<``, ``>``, or ``[``, so a
    # bare ``<!DOCTYPE html>`` (no ``[``) does not match. Excluding ``<`` is what keeps the scan
    # linear: with a mere ``[^>\[]*``, input that repeats ``<!DOCTYPE `` with no ``[``/``>`` hands
    # ``re.search`` a fresh start at every ``<!DOCTYPE`` and each one rescans to end-of-string —
    # O(n²), tens of seconds of CPU on sub-MiB input (a DoS the validator must not add). Bounding the
    # body at ``<`` makes each attempt stop at the next token (same reasoning as the ``XssDetector``
    # ``[^<>]*`` tag body).
    ("dtd_internal_subset", re.compile(r"<!DOCTYPE\s+[^<>\[]*\[", re.IGNORECASE)),
    # Parameter-entity declaration (``<!ENTITY % ...>``) — the blind/out-of-band building block.
    # The external case was already taken above, so this is the internal one.
    ("parameter_entity", re.compile(r"<!ENTITY\s+%\s", re.IGNORECASE)),
    # Any other entity declaration — a general entity, the billion-laughs building block. Presence is
    # the signal; the validator never expands it to classify (CWE-776).
    ("entity_declaration", re.compile(r"<!ENTITY\b", re.IGNORECASE)),
    # XInclude: pulls in an external file/URL without a DTD, so it needs its own rule. Match the
    # namespace URI (covers any prefix binding that is in view) or the conventional ``<xi:include``
    # element. ``(?![-\w.])`` ends the element name on a real XML name terminator, so a different
    # element whose name merely starts with "include" (``<xi:include-extra>``) is not mistaken for
    # it, while an unterminated ``<xi:include`` at end-of-input is still matched (fail toward
    # detection) — which a plain ``(?=[\s/>])`` lookahead would miss. A non-``xi`` prefix bound to
    # the XInclude namespace *out of view* (``<p:include>`` with the ``xmlns`` on an ancestor not in
    # the rendered text) is a documented limitation, not caught here.
    ("xinclude", re.compile(r"<xi:include(?![-\w.])|https?://www\.w3\.org/2001/XInclude\b", re.IGNORECASE)),
    # Bare DOCTYPE (opt-in, scanned only when flag_bare_doctype=True). Last so a DOCTYPE that is
    # *also* external or has an internal subset reports under the more specific label above.
    ("bare_doctype", re.compile(r"<!DOCTYPE\b", re.IGNORECASE)),
)


def _find_xxe(text: str, *, flag_bare_doctype: bool) -> tuple[int, int, str] | None:
    """Return ``(start, end, label)`` of the *earliest* XXE construct in ``text``, or ``None``.

    Earliest-offset wins so the reported position points at the first offending construct; ties are
    broken by pattern order, so the more specific label wins at a shared offset. Offsets are into
    ``text`` — the already-decoded/stripped/masked form that was scanned, not the raw input. The
    opt-in ``bare_doctype`` rule is skipped unless ``flag_bare_doctype`` is set.
    """
    best: tuple[int, int, str] | None = None
    for label, pattern in _XXE_PATTERNS:
        if label == "bare_doctype" and not flag_bare_doctype:
            continue
        match = pattern.search(text)
        if match is not None and (best is None or match.start() < best[0]):
            best = (match.start(), match.end(), label)
    return best


class XxeDetector(BaseValidator):
    """Flags text carrying an XXE-capable XML construct, after decoding obfuscation.

    A detect-only validator for the ``Guard`` path. Each item is first decoded (see the module
    docstring) and its complete comment/CDATA/PI regions blanked out, then the items are flattened
    for matching; ``validate`` reports a failure if the content carries an external entity, an
    external or inline DTD, an entity or parameter-entity declaration, or XInclude. It never rewrites
    the content — an XXE payload is refused, not laundered, since stripping a DTD can silently change
    the document's meaning.

    DTD handling is **capability-based**: a bare ``<!DOCTYPE html>`` (no external ID, no internal
    subset) carries no XXE capability and passes by default. Set ``flag_bare_doctype=True`` to reject
    every DOCTYPE for a strict deployment.

    This is **defense-in-depth**, not a primary control — the authoritative XXE defense is a hardened
    XML parser at the processing site. A passed result means "no known XXE construct found", not
    "provably safe".

    Attributes:
        strip_invisible_chars: Strip zero-width/control characters during preprocessing.
        decode_obfuscation: Decode URL/HTML/JS/CSS encoding layers during preprocessing.
        flag_bare_doctype: Also flag a capability-free bare DOCTYPE (reject-all-DOCTYPE policy).

    Example:
        >>> from aegis import Guard
        >>> guard = Guard("input").use(XxeDetector())
        >>> result = await guard.validate('<!DOCTYPE foo SYSTEM "file:///etc/passwd">')
        >>> result.failures[0].error_code
        'XXE_DETECTED'
    """

    # Error codes are a released contract: defined once here as the single source of truth shared by
    # validate(), the tests, and the docs, and never renamed or repurposed.
    XXE_DETECTED: Final = "XXE_DETECTED"
    XXE_INPUT_TOO_LARGE: Final = "XXE_INPUT_TOO_LARGE"

    def __init__(
        self,
        *,
        strip_invisible_chars: bool = True,
        decode_obfuscation: bool = True,
        max_input_chars: int | None = _DEFAULT_MAX_INPUT_CHARS,
        flag_bare_doctype: bool = False,
    ) -> None:
        """Configure decoding and the DTD policy.

        The decode/strip passes defeat encoded payloads; a caller who fully trusts the encoding of
        its input (already-decoded, no hostile source) may turn them off to save the work.

        Args:
            strip_invisible_chars: Strip zero-width/control characters wedged into a keyword to dodge
                a match — deobfuscation, not Unicode normalization.
            decode_obfuscation: Decode URL/HTML/JS/CSS encoding layers before matching.
            max_input_chars: Resource valve. When the view's items exceed it in combined character
                length, the input is rejected with ``XXE_INPUT_TOO_LARGE`` rather than preprocessed
                and scanned, bounding the per-request work (several decode passes plus a per-character
                category scan). Defaults to 1 MiB of characters — ample for real XML, small enough to
                cap abuse; pass ``None`` to lift the cap when the input size is already trusted.
            flag_bare_doctype: When ``True``, a capability-free bare ``<!DOCTYPE name>`` is also
                flagged (as ``bare_doctype``). Off by default, so a bare HTML5 doctype passes.

        Raises:
            ValueError: If ``max_input_chars`` is negative.
        """
        if max_input_chars is not None and max_input_chars < 0:
            raise ValueError(f"max_input_chars must be non-negative, got {max_input_chars}.")
        self.strip_invisible_chars = strip_invisible_chars
        self.decode_obfuscation = decode_obfuscation
        self.flag_bare_doctype = flag_bare_doctype
        self._max_input_chars = max_input_chars

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Report whether the rendered content carries an XXE-capable construct (after decoding).

        Each item is decoded and inert-masked on its own, then the masked items are flattened through
        ``ContentView.render()`` for matching — so a construct split across an item boundary is still
        caught (``render()`` joins with whitespace, matched by ``\\s``), while an inert region in one
        item cannot mask a construct in another.

        Args:
            view: The content under inspection. Never ``None``, but may be empty.
            metadata: Surrounding agent context. Optional and unused here.

        Returns:
            ``PassedResult`` when no XXE construct is found. Otherwise a ``FailedResult`` carrying
            ``XXE_DETECTED`` with ``details`` locating the first construct — ``match_offset`` and
            ``match_length`` (into the decoded/stripped/masked text that was scanned, **not** the raw
            input, since decoding shifts offsets) and ``construct`` (the construct class, e.g.
            ``"external_entity"``, ``"dtd_internal_subset"``). Or ``XXE_INPUT_TOO_LARGE`` when
            ``max_input_chars`` is set and exceeded. The raw input — and any path, URL, or name
            inside it — is never echoed; only safe summaries are reported.
        """
        # Resource valve first: reject oversized input before any decode/scan. The cap bounds the
        # total text the scan will process — the sum of the item values — so many just-under-limit
        # items cannot blow up the aggregate work. Summing item lengths (rather than measuring
        # ``view.render()``) keeps the valve off render()'s internal format: the separators it places
        # between items are its concern, not work this validator does.
        total_length = sum(len(item.value) for item in view.items)
        if self._max_input_chars is not None and total_length > self._max_input_chars:
            return FailedResult(
                error_message=f"Text exceeds the {self._max_input_chars}-character inspection limit.",
                error_code=self.XXE_INPUT_TOO_LARGE,
                details={"length": total_length, "limit": self._max_input_chars},
            )

        # Decode and inert-mask each item on its own (see _decode_and_mask: one item's inert region
        # must never make another item's construct inert), then let render() flatten the masked
        # items. Delegating the join to the view keeps the scan aligned with however render()
        # flattens it, rather than hardcoding a separator here.
        masked_view = ContentView(items=tuple(self._decode_and_mask(item) for item in view.items))
        scanned = masked_view.render()
        match = _find_xxe(scanned, flag_bare_doctype=self.flag_bare_doctype)
        if match is not None:
            start, end, construct = match
            return FailedResult(
                error_message=f"Input contains an XXE construct ({construct}) at offset {start}.",
                error_code=self.XXE_DETECTED,
                details={"match_offset": start, "match_length": end - start, "construct": construct},
            )
        return PassedResult()

    def _decode_and_mask(self, item: ContentItem) -> ContentItem:
        """Return a copy of ``item`` whose text is decoded and inert-masked, ready to scan.

        Both steps run on this item's value alone: ``preprocess`` reverses obfuscation (see the
        module docstring) and ``_mask_inert_regions`` blanks complete comment/CDATA/PI regions — so
        an inert region in one item can never reach into another. ``replace`` copies the item,
        keeping its id and type and changing only its value; masking preserves length, so offsets
        into the scanned text still locate the construct.
        """
        decoded = preprocess(
            item.value,
            strip_invisible=self.strip_invisible_chars,
            decode=self.decode_obfuscation,
        )
        return replace(item, value=_mask_inert_regions(decoded))
