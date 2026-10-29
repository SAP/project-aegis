"""XssDetector — flags cross-site-scripting markers in text input.

A deterministic, text-only, **detect-only** validator. It runs under ``Guard`` and reports when a
message carries a known XSS vector; it never changes the content. Use it as a cheap, non-LLM
check at a trust boundary — on model output before it is rendered in a page, or on input that will
later be reflected into HTML.

Obfuscation is decoded before detection
----------------------------------------
Real payloads hide behind encoding layers, so the rendered content is **preprocessed** before any
pattern runs: invisible/zero-width control characters are stripped, then URL-percent, HTML-entity,
JavaScript ``\\uXXXX``/``\\xXX``, and CSS ``\\3C`` escapes are decoded repeatedly until the text
stops changing (bounded). Without this, ``%3Cscript%3E`` or ``&#60;script&#62;`` would sail past a
raw-text matcher.

Detection runs on the whole view, not per item
-----------------------------------------------
The validator inspects ``view.render()`` — the flattened text of the whole ``ContentView`` — rather
than each ``ContentItem`` in isolation. Because ``render()`` joins items with a newline (matched by
``\\s``), a vector split across an item boundary (``<scr`` + ``ipt>``) is still caught.

The safety ceiling — read before you rely on this
--------------------------------------------------
This validator is **defense-in-depth, not a primary control**. The authoritative XSS defense is
context-aware output encoding at render time. A passed result means "no *known* XSS marker found",
never "provably safe".

Error codes
-----------
``XSS_DETECTED`` and ``XSS_INPUT_TOO_LARGE`` (stable; see :class:`XssDetector`). Errors never echo
the raw input — only safe summaries.
"""

from __future__ import annotations

import re
from typing import Final

from aegis.metadata import GuardMetadata
from aegis.validators._preprocessing import preprocess
from aegis.validators.base import (
    BaseValidator,
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


# --- XSS detection patterns -------------------------------------------------------------------
#
# Patterns are high-signal on purpose: matching a bare ``<`` would drown callers in false
# positives on benign prose and math.

# Known HTML event-handler attribute names (``onerror``, ``onload`` …). A **curated** alternation,
# not a generic ``on\w+``, so a benign attribute such as ``online=`` or a word like ``onset`` is
# never mistaken for a handler. These are the names whose value executes script.
_EVENT_HANDLER: Final = (
    r"on(?:error|load|click|dblclick|contextmenu|auxclick"
    r"|mouse(?:over|out|move|down|up|enter|leave)|pointer(?:over|out|enter|leave|down|up|move|cancel)"
    r"|touch(?:start|end|move|cancel)|key(?:down|up|press)|focus(?:in|out)?|blur"
    r"|change|input|beforeinput|submit|reset|select|toggle|wheel|scroll|resize"
    r"|copy|cut|paste|drag(?:start|end|enter|leave|over)?|drop"
    r"|animation(?:start|end|iteration)|transition(?:start|end|run|cancel)"
    r"|play|playing|pause|ended|canplay|loadstart|loadeddata|loadedmetadata|progress"
    r"|durationchange|ratechange|volumechange|seeked|seeking|stalled|suspend|waiting|timeupdate"
    r"|abort|emptied|invalid|cuechange|formdata|hashchange|popstate|pagehide|pageshow"
    r"|beforeunload|unload|message|storage|show|search)"
)

# Tag-based XSS, two shapes:
#   1. A complete ``<tag ...>`` — requiring the closing ``>`` ignores unspaced inequalities
#      (``a<b``, ``i<n``) and spaced math (``a < b``) alike, while catching real tags, which carry
#      a ``>`` (``<script>``, ``<img ...>``, ``</div>``). The tag body is ``[^<>]*``, not ``[^>]*``:
#      stopping at the next ``<`` keeps each attempt within a single tag. With ``[^>]*`` a run of
#      ``<`` with no following ``>`` (``<a<a<a…`` to EOF) makes every ``<`` rescan to end-of-string,
#      which is O(n²) — ~3.5s on 1 MiB of such input. Bounding the body makes it linear. A tag whose
#      *attribute value* contains a literal ``<`` (``<a title="1 < 2">``) is no longer matched as one
#      span, but every high-risk vector is unaffected: ``<script>``/``<iframe>``/``<svg>`` carry no
#      ``<`` before their ``>``, nested markup (``srcdoc="<script>…"``) is caught at the inner ``<``,
#      and any event-handler tag is caught by shape 2.
#   2. An *unterminated* start tag that already carries a known event-handler attribute
#      (``<img src=x onerror=alert(1)`` with no ``>``) — browsers recover such tags at EOF so the
#      handler still fires; the curated ``_EVENT_HANDLER`` list keeps this from matching a benign
#      ``online=`` attribute. ``[^<>]*`` keeps the scan within one unclosed tag (linear, no
#      backtracking surface).
# A residual match remains for prose that straddles a later ``>`` (``x<y then z>w``) — far rarer
# than a bare inequality, the price of a regex heuristic over a real parser.
_XSS_TAG: Final = re.compile(
    rf"</?[a-zA-Z][^<>]*>|<[a-zA-Z][^<>]*\b{_EVENT_HANDLER}\b\s*=",
    re.IGNORECASE,
)


# A browser strips tab/CR/LF out of a URL scheme before parsing it, so ``java&#x09;script:`` still
# executes once the entity is decoded. (Those are the only in-scheme separators browsers ignore;
# zero-width/other invisibles are already removed in preprocessing.) Allow them between the scheme
# letters so the obfuscated form is caught too — kept linear (single-char optional runs, no
# backtracking surface).
def _scheme(word: str) -> str:
    """A scheme matcher tolerant of the tab/CR/LF a browser ignores inside a URL scheme."""
    return r"[\t\n\r]*".join(re.escape(c) for c in word)


# Each pattern is paired with a stable, non-sensitive label naming the vector class. The label is
# safe to report (it describes the *kind* of marker, never the matched text).
_XSS_PATTERNS: Final = (
    ("html_tag", _XSS_TAG),
    # script-protocol URIs. The pre-colon position allows only ``[\t\n\r]`` — the controls a
    # browser strips from a URL scheme — not ``\s``: a space is not stripped, so ``JavaScript : a
    # language`` is prose a browser never parses as a ``javascript:`` URL, and must not be flagged.
    ("script_uri", re.compile(rf"(?i)(?:{_scheme('javascript')}|{_scheme('vbscript')})[\t\n\r]*:")),
    # ``data:`` is a URL scheme too, so a browser strips tab/CR/LF from it as well — tolerate them
    # between the scheme letters so ``da&#x09;ta:text/html;base64,...`` is caught, not just the
    # contiguous form. Same ``[\t\n\r]`` discipline around the colon (not ``\s``).
    (
        "data_uri",
        re.compile(rf"(?i){_scheme('data')}[\t\n\r]*:[\t\n\r]*(?:text/html|application/xhtml\+xml|image/svg\+xml)"),
    ),
    # Legacy CSS ``expression(`` is only an injection as a *property value* (``width:expression(…)``
    # or ``style=expression(…)``), so require a preceding ``:`` or ``=``. This catches the real form
    # while leaving ordinary prose/maths like "regular expression (regex)" or "expression(2+2)"
    # alone. (A ``<style>`` tag carrying it is caught by ``html_tag`` anyway.)
    ("css_expression", re.compile(r"(?i)[:=]\s*expression\(")),
    # DOM write-sinks, as a *call or assignment*. The HTML-write properties (inner/outer HTML and
    # insertAdjacentHTML) are flagged on **any** receiver (an element write is the common sink, not
    # just ``document.``); ``document.cookie|location|write|writeln|domain`` only on ``document``. A
    # write is a ``(`` call or an ``=`` (optionally a compound/logical operator ``+= ??= ||= <<=``
    # …); the ``(?![=>])`` after ``=`` excludes a comparison (``== ===``) or arrow (``=>``), which
    # only read the sink. Prose that merely names the API ("read document.location") is not flagged.
    (
        "dom_sink",
        re.compile(
            r"(?i)(?:"
            r"document\s*\.\s*(?:cookie|location|write|writeln|domain)"
            r"|\.\s*(?:inner|outer)html"
            r"|\.\s*insertadjacenthtml"
            r")\s*(?:\(|(?:\?\?|\|\||&&|<<|>>>?|\*\*|[+\-*/%&|^])?=(?![=>]))"
        ),
    ),
)


def _find_xss(text: str) -> tuple[int, int, str] | None:
    """Return ``(start, end, label)`` of the *earliest* XSS marker in ``text``, or ``None``.

    Earliest-offset wins so the reported position points at the first offending token; ties are
    broken by pattern order (tag before the markup-less vectors). Offsets are into ``text`` — the
    already-decoded/stripped form that was scanned, not the raw input.
    """
    best: tuple[int, int, str] | None = None
    for label, pattern in _XSS_PATTERNS:
        match = pattern.search(text)
        if match is not None and (best is None or match.start() < best[0]):
            best = (match.start(), match.end(), label)
    return best


class XssDetector(BaseValidator):
    """Flags text carrying a known cross-site-scripting (XSS) vector, after decoding obfuscation.

    A detect-only validator for the ``Guard`` path. The rendered view is first decoded (see the
    module docstring) so obfuscated payloads are matched; ``validate`` then reports a failure if the
    content carries an HTML tag (``<script>``, ``<img onerror=...>``, ``</div>``) or a markup-less
    vector (``javascript:`` / ``vbscript:`` URIs, dangerous ``data:`` URIs, CSS ``expression()``, or
    a DOM-sink call/assignment like ``document.cookie=``). It never rewrites the content.

    This is **defense-in-depth**, not a primary control — the authoritative XSS defense is
    context-aware output encoding at render time. A passed result means "no known XSS marker
    found", not "provably safe".

    Attributes:
        strip_invisible_chars: Strip zero-width/control characters during preprocessing.
        decode_obfuscation: Decode URL/HTML/JS/CSS encoding layers during preprocessing.

    Example:
        >>> from aegis import Guard
        >>> guard = Guard("input").use(XssDetector())
        >>> result = await guard.validate("<script>alert(1)</script>")
        >>> result.failures[0].error_code
        'XSS_DETECTED'
    """

    # Error codes are a released contract: defined once here as the single source of truth shared by
    # validate(), the tests, and the docs, and never renamed or repurposed.
    XSS_DETECTED: Final = "XSS_DETECTED"
    XSS_INPUT_TOO_LARGE: Final = "XSS_INPUT_TOO_LARGE"

    def __init__(
        self,
        *,
        strip_invisible_chars: bool = True,
        decode_obfuscation: bool = True,
        max_input_chars: int | None = _DEFAULT_MAX_INPUT_CHARS,
    ) -> None:
        """Configure how aggressively input is decoded before matching.

        The decode/strip passes defeat encoded payloads; a caller who fully trusts the encoding of
        its input (already-decoded, no hostile source) may turn them off to save the work.

        Args:
            strip_invisible_chars: Strip zero-width/control characters before matching.
            decode_obfuscation: Decode URL/HTML/JS/CSS encoding layers before matching.
            max_input_chars: Resource valve. When the rendered text exceeds it, the text is rejected
                with ``XSS_INPUT_TOO_LARGE`` rather than preprocessed and scanned, bounding the
                per-request work (several decode passes plus a per-character category scan).
                Defaults to 1 MiB of characters; pass ``None`` to lift the cap when the input size
                is already trusted.

        Raises:
            ValueError: If ``max_input_chars`` is negative.
        """
        if max_input_chars is not None and max_input_chars < 0:
            raise ValueError(f"max_input_chars must be non-negative, got {max_input_chars}.")
        self.strip_invisible_chars = strip_invisible_chars
        self.decode_obfuscation = decode_obfuscation
        self._max_input_chars = max_input_chars

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Report whether the rendered content carries a known XSS vector (after decoding).

        Inspects ``view.render()`` — the whole flattened view — so a vector split across an item
        boundary is still caught (``render()`` joins items with a newline, which ``\\s`` matches).

        Args:
            view: The content under inspection. Never ``None``, but may be empty.
            metadata: Surrounding agent context. Optional and unused here.

        Returns:
            ``PassedResult`` when no XSS vector is found. Otherwise a ``FailedResult`` carrying
            ``XSS_DETECTED`` with ``details`` locating the first marker — ``match_offset`` and
            ``match_length`` (into the decoded/stripped text that was scanned, **not** the raw
            input, since decoding shifts offsets) and ``vector`` (the marker class, e.g.
            ``"html_tag"``, ``"script_uri"``). Or ``XSS_INPUT_TOO_LARGE`` when ``max_input_chars``
            is set and exceeded. The raw input is never echoed; only safe summaries are reported.
        """
        raw_text = view.render()

        if self._max_input_chars is not None and len(raw_text) > self._max_input_chars:
            return FailedResult(
                error_message=f"Text exceeds the {self._max_input_chars}-character inspection limit.",
                error_code=self.XSS_INPUT_TOO_LARGE,
                details={"length": len(raw_text), "limit": self._max_input_chars},
            )

        prepared = preprocess(
            raw_text,
            strip_invisible=self.strip_invisible_chars,
            decode=self.decode_obfuscation,
        )
        match = _find_xss(prepared)
        if match is not None:
            start, end, vector = match
            return FailedResult(
                error_message=f"Input contains a cross-site-scripting (XSS) marker ({vector}) at offset {start}.",
                error_code=self.XSS_DETECTED,
                details={"match_offset": start, "match_length": end - start, "vector": vector},
            )
        return PassedResult()
