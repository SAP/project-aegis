"""SqlInjectionDetector — flags SQL-injection markers in text input.

A deterministic, text-only, **detect-only** validator. It runs under ``Guard`` and reports when a
message carries a known SQL-injection construct; it never changes the content. Use it as a cheap,
non-LLM check at a trust boundary on text that may reach a query layer.

Obfuscation is decoded before detection
----------------------------------------
Real payloads hide behind encoding layers, so the rendered content is **preprocessed** before any
pattern runs: invisible/zero-width control characters are stripped, then URL-percent, HTML-entity,
JavaScript ``\\uXXXX``/``\\xXX``, and CSS ``\\3C`` escapes are decoded repeatedly until the text
stops changing (bounded). Without this, a URL-encoded ``UNION%20SELECT`` would sail past a raw-text
matcher.

Detection runs on the whole view, not per item
-----------------------------------------------
The validator inspects ``view.render()`` — the flattened text of the whole ``ContentView`` — rather
than each ``ContentItem`` in isolation. Because ``render()`` joins items with a newline (matched by
``\\s``), a construct split across an item boundary (``1 UNION`` + ``SELECT ...``) is still caught.

The safety ceiling — read before you rely on this
--------------------------------------------------
This validator is **defense-in-depth, not a primary control**. The authoritative SQL-injection
defense is parameterized queries (prepared statements) — never string-concatenated SQL. A passed
result means "no *known* injection marker found", never "provably safe"; a determined attacker can
phrase an injection this pattern set does not cover.

Error codes
-----------
``SQLI_DETECTED`` and ``SQLI_INPUT_TOO_LARGE`` (stable; see :class:`SqlInjectionDetector`). Errors
never echo the raw input — only safe summaries.
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

# --- SQL-injection detection patterns ---------------------------------------------------------
#
# The classic query-breaking constructs, not individual metacharacters: matching a bare ``;`` or
# ``'`` would drown callers in false positives on ordinary prose. Each pattern is linear (no
# nested quantifier that can backtrack) — a validator must not add a ReDoS surface of its own.
# Each pattern is paired with a stable, non-sensitive label naming the construct class. The label
# is safe to report (it describes the *kind* of construct, never the matched text).
_SQLI_PATTERNS: Final = (
    ("union_select", re.compile(r"\bunion\s+(?:all\s+)?select\b", re.IGNORECASE)),  # UNION extraction
    # Stacked query: a terminator followed by a *complete* DDL/DML statement. Pairing each verb with
    # its SQL object (``drop table``, ``delete from`` …) keeps this from firing on ordinary prose
    # that happens to put a common verb after a semicolon ("Log in; create your profile").
    # Stacked query: a terminator followed by a *complete* DDL/DML statement. Each verb is paired
    # with its SQL object AND a trailing ``\b`` (so ``; delete fromagerie`` / ``; create indexer``
    # — a keyword as a *prefix* of a longer word — do not match), and the ambiguous ``INSERT INTO``
    # / ``UPDATE … SET`` forms additionally require SQL continuation (``(`` / ``VALUES`` / ``SET`` /
    # ``col=``) so an English phrase like ``; insert into the box`` is not flagged.
    (
        "stacked_query",
        re.compile(
            r";\s*(?:"
            r"drop\s+(?:table|database|schema|index|view|procedure|function|trigger)\b"
            r"|delete\s+from\b"
            r"|truncate\s+table\b"
            r"|insert\s+into\s+\w+\s*(?:\(|values\b|select\b|set\b|default\b)"
            r"|update\s+\w+\s+set\s+\S+?\s*="
            r"|alter\s+(?:table|database)\b"
            r"|create\s+(?:table|database|schema|view|index|procedure|function)\b"
            # EXEC/EXECUTE of a stored proc: a call ``(``, a system-proc prefix (``sp_``/``xp_``),
            # or a *qualified* (dotted) proc name (``dbo.proc``). Requiring qualification — not any
            # bare word — is what keeps ``; execute the task`` (prose) from matching while still
            # catching ``; EXEC dbo.dangerous_proc``. ``\b`` after the verb avoids matching
            # ``executed``.
            r"|(?:execute|exec)\b\s*(?:\(|sp_|xp_|(?:\w+\.)+\w+)"
            r")",
            re.IGNORECASE,
        ),
    ),
    # Tautologies require *equal* operands (always-true): the backreference ``\1`` ties the right
    # operand to the left, so ``' OR '1'='1`` / ``OR 1=1`` match but a false comparison like
    # ``' OR 'admin'='guest'`` / ``OR 1=2`` — and benign arithmetic like ``2 or 3 = 5`` — do not.
    # ``(?!\w)`` / ``\b`` after the backreference stops a *prefix* match (``' OR '1'='10``, 1 ≠ 10).
    # Three forms cover: a leading quote that closes a string (``admin' OR '1'='1``); quoted operands
    # with no leading quote, for a numeric injection point (``0 OR '1'='1'``); and bare numerics
    # (``OR 1=1``). All accept ``AND`` as well as ``OR`` (boolean-blind ``AND 1=1``).
    ("tautology", re.compile(r"'\s*(?:or|and)\s+'?(\w+)'?\s*=\s*'?\1(?!\w)'?", re.IGNORECASE)),
    ("tautology", re.compile(r"\b(?:or|and)\s+'(\w+)'\s*=\s*'\1'(?!\w)", re.IGNORECASE)),
    ("tautology", re.compile(r"\b(?:or|and)\s+(\d+)\s*=\s*\1\b", re.IGNORECASE)),
    # MySQL *executable* comment opener (``/*!``). Code inside ``/*!... */`` — optionally gated on a
    # version, ``/*!50000...*/`` — runs in MySQL but is inert to every other parser, so it is a
    # classic SQLi evasion wrapper (``/*!50000UNION*/``). We match only this opener, not a bare
    # ``/*``: plain block comments are common in benign code/prose (false positives), while ``/*!``
    # is high-signal and, as the opener alone, also catches one left unterminated to swallow the
    # rest of a query. Matching the whole ``/*! ... */`` pair would rescan the suffix from every
    # opener — quadratic, breaking the no-ReDoS guarantee.
    ("mysql_exec_comment", re.compile(r"/\*!")),
    # Comment tail: a quote then a SQL comment (``'--`` / ``'#`` — the classic auth-bypass tail), or
    # a terminator then ``--``. Bare ``;#`` is excluded: ``#`` after a semicolon is common in prose
    # and lists ("see item; #3"), while ``'--`` / ``; --`` are high-signal.
    ("comment_tail", re.compile(r"(?:'\s*(?:--|#)|;\s*--)")),
    # Time-based blind probe. Requiring a numeric first argument keeps ``go to sleep (now)`` and
    # ``benchmark (results)`` prose from matching, while catching ``SLEEP(5)`` / ``pg_sleep(10)`` /
    # ``BENCHMARK(1000000,MD5(1))``.
    ("time_based_probe", re.compile(r"\b(?:sleep|pg_sleep|benchmark)\s*\(\s*\d", re.IGNORECASE)),
    ("time_based_probe", re.compile(r"\bwaitfor\s+(?:delay|time)\b", re.IGNORECASE)),  # T-SQL delay
)


def _find_sqli(text: str) -> tuple[int, int, str] | None:
    """Return ``(start, end, label)`` of the *earliest* SQL-injection construct in ``text``.

    Earliest-offset wins so the reported position points at the first offending construct; ties are
    broken by pattern order. Offsets are into ``text`` — the already-decoded/stripped form that was
    scanned, not the raw input. Returns ``None`` when nothing matches.
    """
    best: tuple[int, int, str] | None = None
    for label, pattern in _SQLI_PATTERNS:
        match = pattern.search(text)
        if match is not None and (best is None or match.start() < best[0]):
            best = (match.start(), match.end(), label)
    return best


class SqlInjectionDetector(BaseValidator):
    """Flags text carrying a known SQL-injection construct, after decoding obfuscation.

    A detect-only validator for the ``Guard`` path. The rendered view is first decoded (see the
    module docstring) so obfuscated payloads are matched; ``validate`` then reports a failure if the
    content carries a known SQL-injection marker — ``UNION SELECT``, a stacked ``; DROP TABLE``
    query, a tautology (``' OR '1'='1``, ``OR 1=1``), a SQL comment (``/*!``, ``'--``), or a
    time-based blind probe (``sleep()`` / ``benchmark()`` / ``WAITFOR DELAY``). It never rewrites the
    content.

    This is **defense-in-depth**, not a primary control — the authoritative SQL-injection defense
    is parameterized queries. A passed result means "no known injection marker found", not
    "provably safe".

    Attributes:
        strip_invisible_chars: Strip zero-width/control characters during preprocessing.
        decode_obfuscation: Decode URL/HTML/JS/CSS encoding layers during preprocessing.

    Example:
        >>> from aegis import Guard
        >>> guard = Guard("input").use(SqlInjectionDetector())
        >>> result = await guard.validate("1 UNION SELECT password FROM users")
        >>> result.failures[0].error_code
        'SQLI_DETECTED'
    """

    # Error codes are a released contract: defined once here as the single source of truth shared by
    # validate(), the tests, and the docs, and never renamed or repurposed.
    SQLI_DETECTED: Final = "SQLI_DETECTED"
    SQLI_INPUT_TOO_LARGE: Final = "SQLI_INPUT_TOO_LARGE"

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
                with ``SQLI_INPUT_TOO_LARGE`` rather than preprocessed and scanned, bounding the
                per-request work (up to ``MAX_DECODE_ITERATIONS`` decode passes plus a per-character
                category scan). Defaults to 1 MiB of characters; pass ``None`` to lift the cap when
                the input size is already trusted.

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
        """Report whether the rendered content carries a known SQL-injection construct.

        Inspects ``view.render()`` — the whole flattened view — so a construct split across an item
        boundary is still caught (``render()`` joins items with a newline, which ``\\s`` matches).

        Args:
            view: The content under inspection. Never ``None``, but may be empty.
            metadata: Surrounding agent context. Optional and unused here.

        Returns:
            ``PassedResult`` when no injection marker is found. Otherwise a ``FailedResult``
            carrying ``SQLI_DETECTED`` with ``details`` locating the first construct —
            ``match_offset`` and ``match_length`` (into the decoded/stripped text that was scanned,
            **not** the raw input, since decoding shifts offsets) and ``construct`` (the construct
            class, e.g. ``"union_select"``, ``"stacked_query"``). Or ``SQLI_INPUT_TOO_LARGE`` when
            ``max_input_chars`` is set and exceeded. The raw input is never echoed; only safe
            summaries are reported.
        """
        raw_text = view.render()

        if self._max_input_chars is not None and len(raw_text) > self._max_input_chars:
            return FailedResult(
                error_message=f"Text exceeds the {self._max_input_chars}-character inspection limit.",
                error_code=self.SQLI_INPUT_TOO_LARGE,
                details={"length": len(raw_text), "limit": self._max_input_chars},
            )

        prepared = preprocess(
            raw_text,
            strip_invisible=self.strip_invisible_chars,
            decode=self.decode_obfuscation,
        )
        match = _find_sqli(prepared)
        if match is not None:
            start, end, construct = match
            return FailedResult(
                error_message=f"Input contains a SQL-injection construct ({construct}) at offset {start}.",
                error_code=self.SQLI_DETECTED,
                details={"match_offset": start, "match_length": end - start, "construct": construct},
            )
        return PassedResult()
