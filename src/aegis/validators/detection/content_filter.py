"""Reject text that contains any of a configured list of blocked terms or phrases.

This module is the living specification for :class:`ContentFilter`; see
``docs/validators/detection/content-filter.md`` for the user-facing guide. The short version:

An operator maintains a list of **blocked topics** — literal words or phrases they do not want
appearing in text crossing a guard (a competitor name, a banned subject, jailbreak markers, an
internal code-name). This validator inspects a piece of text on the agent's input or output and
rejects it when the text contains any configured term, matched as a normalized, word-boundary
occurrence (substring matching is opt-in).

What it deliberately is **not**: a semantic topic classifier (it finds the exact terms you give it,
never text that is merely *about* a subject), a toxicity/profanity model, a PII detector, or a
redactor. Those are separate single-concern validators. A PASS certifies only that none of the
configured terms appeared — never that the text is on-topic, safe, or lawful; per OWASP, a denylist
is not a general security boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from aegis._normalize import NormalizationConfig
from aegis._normalize import normalize as apply_normalization
from aegis.metadata import GuardMetadata
from aegis.validators.base import (
    BaseValidator,
    ContentView,
    FailedResult,
    PassedResult,
    ValidationResult,
)


@dataclass(frozen=True)
class _Blocked:
    """One configured blocked term, prepared once at construction for repeated matching.

    Attributes:
        reference_index: The term's position within ``blocked_topics``. An opaque, non-secret
            handle reported in ``details`` so a caller can identify which term matched without the
            validator having to echo the term itself (it could be a confidential code-name).
        raw: The original configured term, kept only for the opt-in ``report_matched_topic`` echo.
        normalized: The normalized form actually matched against the (equally normalized) text.
            Equal to ``raw`` when normalization is disabled.
        pattern: The compiled word-boundary regex used in word-boundary mode, or ``None`` in
            substring mode (where a plain ``in`` / ``str.find`` is used instead).
    """

    reference_index: int
    raw: str
    normalized: str
    pattern: re.Pattern[str] | None


@dataclass(frozen=True)
class _Match:
    """A single blocked-term hit, before it is turned into a ``FailedResult``.

    Ordering across candidates is by ``match_offset`` first (the earliest hit in the text), then
    ``reference_index`` — a total order that makes the reported verdict deterministic regardless of
    the order the terms were configured in.
    """

    reference_index: int
    match_offset: int
    raw: str


class ContentFilter(BaseValidator):
    """Rejects text that contains any of a configured list of blocked terms or phrases.

    A deterministic, text-only, **detect-only** validator. It runs under ``Guard`` and scans a piece
    of text for a caller-configured list of literal terms/phrases, rejecting the text when any term
    appears as a normalized, word-boundary match (substring matching is opt-in via
    ``word_boundary=False``). Reach for it as a policy guardrail on agent input or output when you
    have a **known list of words/phrases** to keep out — a competitor list, banned subjects,
    jailbreak markers, internal code-names.

    Matching is literal, never semantic. Each term is matched as escaped literal text — so ``C++``
    and ``a.b`` match literally, never as a regex — and the validator finds only the exact terms you
    give it, not a paraphrase, synonym, or translation, and not text that is merely *about* the
    subject. Judging whether text is *about* a topic is the job of a separate, model-backed classifier.

    By default a term matches only as a discrete word or phrase, on word boundaries. This avoids the
    Scunthorpe false positive — blocking ``politics`` does not reject ``apolitical`` — at the cost of
    missing a term embedded without delimiters. The boundaries are Python ``re`` word-character
    (``\\w``) boundaries, an approximation of Unicode UAX #29, so a language such as Chinese or Thai
    that does not delimit words with spaces needs ``word_boundary=False``.

    Normalization (off by default; opt in with ``normalize=True``) folds cheap cosmetic evasions —
    case changes, added whitespace, zero-width/invisible characters, and NFKC-compatibility variants
    such as full-width letters — by
    normalizing both the text and every term before matching. It does **not** fold confusable
    look-alikes, decode Base64/hex, or reverse leetspeak; those are documented residual gaps handled
    by future, additive hardening (see the ADR).

    Out of scope by design (each is a separate single-concern validator): semantic topic
    classification, deliberate-obfuscation detection (homoglyphs, leetspeak), encoding/decoding,
    toxicity/profanity scoring, PII/secret detection, and redacting the term instead of rejecting.

    On a hit it returns a ``FailedResult`` whose ``error_message`` and ``details`` carry only safe
    metadata — an opaque term index, a match count, and an offset — and **never** the surrounding
    input. The literal term is included only when ``report_matched_topic=True`` (off by default, in
    case a term is confidential).

    Example:
        >>> from aegis import Guard
        >>> guard = Guard("input").use(ContentFilter(blocked_topics=["gambling"]))
        >>> result = await guard.validate("Come learn about gambling strategies!")
        >>> result.failures[0].error_code
        'CONTENT_BLOCKED'
    """

    # Error codes are a released contract: defined once here as the single source of truth shared by
    # validate(), the tests, and the doc page, and never renamed or repurposed. CONTENT_BLOCKED is
    # preserved from the 0.1.0 proof-of-concept for continuity with the README and docs examples.
    CONTENT_BLOCKED: Final = "CONTENT_BLOCKED"
    CONTENT_FILTER_INPUT_TOO_LARGE: Final = "CONTENT_FILTER_INPUT_TOO_LARGE"

    def __init__(
        self,
        blocked_topics: list[str],
        *,
        word_boundary: bool = True,
        case_sensitive: bool = False,
        normalize: bool = False,
        compatibility_folding: bool = True,
        report_matched_topic: bool = False,
        max_input_chars: int | None = None,
    ) -> None:
        """Configure the blocked terms and matching behavior.

        Args:
            blocked_topics: The literal terms/phrases to keep out of the text. At least one is
                required, and none may be empty or whitespace-only. Each is matched as literal text
                (regex metacharacters are escaped), never as a pattern or a semantic subject.
            word_boundary: When ``True`` (default), a term matches only as a discrete word/phrase on
                word boundaries, avoiding the Scunthorpe false positive (``politics`` does not match
                ``apolitical``). Set ``False`` for substring matching — required for languages that
                do not delimit words with spaces (Chinese, Thai), where word boundaries are
                unreliable, or for a deliberate substring policy.
            case_sensitive: When ``True``, skip case-folding during normalization. Only takes effect
                when ``normalize=True`` — case-folding is one step of normalization, so with
                ``normalize=False`` matching is fully literal regardless of this flag.
            normalize: When ``True``, normalize both the text and every term before matching —
                Unicode normalization, strip zero-width/format characters, collapse whitespace, and
                case-fold (unless ``case_sensitive``). Defeats cheap cosmetic evasions at the cost
                of some strictness. Defaults to ``False`` (matching is fully literal): opt in
                deliberately, mirroring the engine's off-by-default ``Guard(normalize=...)`` so
                normalization is never applied silently. The folds here are script-safe — they
                never map homoglyphs (that stays an engine-only concern, ``Guard(normalize=True)``),
                so genuine non-Latin letters are not rewritten to ASCII.
            compatibility_folding: Only takes effect when ``normalize=True``. When ``True`` (default),
                use NFKC, which folds full-width and other compatibility variants (``ｇａｍｂｌｉｎｇ`` →
                ``gambling``) — a real, documented evasion. When ``False``, use canonical NFC instead,
                which avoids the UAX #15 over-folding risk but no longer catches full-width forms.
            report_matched_topic: When ``True``, include the literal ``matched_topic`` in the failure
                ``details`` (restores the 0.1.0 proof-of-concept field). Off by default because a
                configured term could be a confidential code-name; the surrounding input is never
                echoed either way.
            max_input_chars: Optional resource valve. When set and the rendered text exceeds it, the
                text is rejected with ``CONTENT_FILTER_INPUT_TOO_LARGE`` rather than scanned. Off by
                default — a hard input-size limit belongs to a dedicated length/size validator; this
                is only a safety valve.

        Raises:
            ValueError: If ``blocked_topics`` is a single string rather than a list (a bare string
                would expand into one-character terms); if ``blocked_topics`` is empty; if any term
                is empty or whitespace-only (an empty needle matches every input and would block
                everything); if any term is empty after normalization; or if ``max_input_chars`` is
                negative (a negative limit exceeds even empty text, so it would reject everything).
        """
        # A bare string is itself iterable, so list("politics") would silently expand to
        # ["p", "o", "l", ...] — one-character terms that over-block almost everything. The
        # list[str] hint is not enforced at runtime (a value from config or JSON can still arrive
        # as a str), so reject it explicitly and fail fast rather than misbehave.
        if isinstance(blocked_topics, str):
            raise ValueError("blocked_topics must be a list of strings, not a single string.")

        topics = list(blocked_topics)

        # Fail closed: with nothing to match against, the validator would silently pass every input —
        # a false sense of security. Reject at construction rather than pass everything.
        if not topics:
            raise ValueError("ContentFilter requires at least one blocked topic in blocked_topics.")

        # An empty or whitespace-only needle is contained in every possible text, so it would reject
        # all input — the documented LLM Guard empty-string bug. Reject it here, reporting the index
        # never the value, in case the value is a confidential term.
        for index, topic in enumerate(topics):
            if not topic.strip():
                raise ValueError(f"blocked_topics[{index}] is empty or whitespace-only.")

        if max_input_chars is not None and max_input_chars < 0:
            # A negative limit makes len(text) > limit true for every candidate — even empty text —
            # so it would reject everything with an impossible bound. Guard it like a real control.
            # (0 is allowed: it means "reject any non-empty text".)
            raise ValueError(f"max_input_chars must be non-negative, got {max_input_chars}.")

        self._word_boundary = word_boundary
        self._report_matched_topic = report_matched_topic
        self._max_input_chars = max_input_chars

        # Build the normalization profile once. ``None`` means "no normalization" — matching is
        # fully literal. Otherwise the script-safe matching profile (NFKC/NFC + strip format chars
        # + collapse whitespace + casefold); compatibility_folding and case_sensitive only take
        # effect here, which is why they are documented as no-ops when normalize is off.
        self._norm_config = (
            NormalizationConfig.for_validators(
                compatibility_folding=compatibility_folding,
                case_sensitive=case_sensitive,
            )
            if normalize
            else None
        )

        # Normalize every term once here, not on each validate() call, since the term list is fixed
        # for the validator's lifetime. A term can survive the empty/whitespace check above yet
        # normalize to nothing (e.g. it was made entirely of zero-width format characters), which
        # would again be a match-everything needle — reject that too, at the one place terms are
        # built. Compile the word-boundary pattern per term now so validate() only runs the search.
        self._blocked: list[_Blocked] = []
        for index, topic in enumerate(topics):
            normalized = self._normalize_text(topic)
            if not normalized:
                raise ValueError(f"blocked_topics[{index}] is empty after normalization.")
            self._blocked.append(
                _Blocked(
                    reference_index=index,
                    raw=topic,
                    normalized=normalized,
                    pattern=self._compile_boundary_pattern(normalized) if word_boundary else None,
                )
            )

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Inspect the text and report whether it contains any blocked term.

        Scans ``view.render()`` — the whole text, so a Markdown/JSON/code wrapper earns no
        exemption. Since ``render()`` joins parts with a newline, a term spanning a part boundary is
        caught only when that boundary is a whitespace break; a mid-token split is a residual miss
        (see the ADR).

        Returns the single highest-precedence failure (``CONTENT_FILTER_INPUT_TOO_LARGE`` →
        ``CONTENT_BLOCKED``), or ``PassedResult`` if no term matched.

        Args:
            view: The text to inspect as a :class:`ContentView`. Never ``None``; may be empty.
            metadata: Surrounding agent context. Not read by this validator; may be ``None``.

        Returns:
            ``PassedResult`` with ``details = {"terms_checked", "word_boundary"}`` when no term
            matched, or a ``FailedResult`` carrying the ``CONTENT_BLOCKED`` code and a safe-summary
            ``details`` mapping (no surrounding input; the literal term only when
            ``report_matched_topic=True``).
        """
        raw_text = view.render()

        # The size valve runs first: it is both the highest-precedence failure and the point of the
        # cap — reject an oversized candidate before doing any scanning work.
        if self._max_input_chars is not None and len(raw_text) > self._max_input_chars:
            return FailedResult(
                error_code=self.CONTENT_FILTER_INPUT_TOO_LARGE,
                error_message=f"Text exceeds the {self._max_input_chars}-character inspection limit.",
                details={"length": len(raw_text), "limit": self._max_input_chars},
            )

        # Nothing can match in empty or whitespace-only text.
        if not raw_text.strip():
            return self._passed()

        norm_text = self._normalize_text(raw_text)

        # Scan every term and collect the earliest offset each matching term hits at. match_count is
        # the number of *distinct* configured terms that matched at least once — a count, never a
        # list of snippets — and _select picks the single reported verdict deterministically.
        matches = [match for item in self._blocked if (match := self._find(item, norm_text)) is not None]
        winner = self._select(matches)
        if winner is not None:
            return self._to_failure(winner, match_count=len(matches))

        return self._passed()

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize_text(self, text: str) -> str:
        """Return the matching form of ``text``, applied identically to the text and every term.

        Delegates to the shared :func:`aegis._normalize.normalize` with ``self._norm_config``
        (built once at construction from ``normalize`` / ``compatibility_folding`` /
        ``case_sensitive``). When normalization is disabled (``self._norm_config is None``) the
        text is returned unchanged and matching is fully literal. Both the configured terms (at
        construction) and the rendered text (at ``validate``) pass through this one method, so the
        two are always folded identically — the invariant that makes matching correct, and the
        reason the validator does not defer text normalization to the engine (which may be wired
        with a different config, or none).
        """
        if self._norm_config is None:
            return text
        return apply_normalization(text, self._norm_config)

    @staticmethod
    def _compile_boundary_pattern(normalized_term: str) -> re.Pattern[str]:
        """Compile the word-boundary matcher for one already-normalized term.

        The term is ``re.escape``d so metacharacters match literally (``C++`` matches the text
        ``C++``, never as a pattern). Boundaries use the lookarounds ``(?<!\\w)`` / ``(?!\\w)``
        rather than ``\\b``: ``\\b`` is defined relative to the pattern's own edge characters, so it
        misbehaves for a term whose edge is not a word character (the final ``+`` of ``C++``). The
        lookarounds assert "not flanked by a word character" for every term, which is the boundary
        semantics we want.
        """
        return re.compile(r"(?<!\w)" + re.escape(normalized_term) + r"(?!\w)")

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _find(self, item: _Blocked, norm_text: str) -> _Match | None:
        """Return the earliest match of one term in the normalized text, or ``None``.

        In word-boundary mode the term's precompiled pattern is searched; in substring mode a plain
        ``str.find`` is used. Both report the character offset into the normalized text — diagnostic
        only, since the text is never echoed.
        """
        if item.pattern is not None:
            found = item.pattern.search(norm_text)
            if found is None:
                return None
            offset = found.start()
        else:
            offset = norm_text.find(item.normalized)
            if offset == -1:
                return None

        return _Match(reference_index=item.reference_index, match_offset=offset, raw=item.raw)

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _select(matches: list[_Match]) -> _Match | None:
        """Return the winning match, or ``None`` if there are none.

        Ordering is by earliest offset, then reference index — a total order, so the reported verdict
        is deterministic and does not depend on the order ``blocked_topics`` was configured in.
        """
        if not matches:
            return None
        return min(matches, key=lambda m: (m.match_offset, m.reference_index))

    def _to_failure(self, match: _Match, match_count: int) -> FailedResult:
        """Build the ``CONTENT_BLOCKED`` failure, carrying only safe summary metadata.

        The message and details exclude the surrounding input in every case. The literal blocked term
        is included as ``matched_topic`` only when ``report_matched_topic`` is set, because the term
        is the operator's own policy and could be a confidential code-name.
        """
        details: dict[str, object] = {
            "matched_index": match.reference_index,
            "match_count": match_count,
            "match_offset": match.match_offset,
            "word_boundary": self._word_boundary,
        }
        if self._report_matched_topic:
            details["matched_topic"] = match.raw

        return FailedResult(
            error_code=self.CONTENT_BLOCKED,
            error_message=f"Text contains a blocked topic (reference {match.reference_index}).",
            details=details,
        )

    def _passed(self) -> PassedResult:
        """Build the ``PassedResult``, summarizing what was checked without exposing it.

        A PASS certifies only that none of the configured terms matched — never that the text is
        on-topic, safe, or lawful (OWASP: a denylist is not a security boundary). ``terms_checked``
        counts the configured terms compared against the text.
        """
        return PassedResult(
            details={
                "terms_checked": len(self._blocked),
                "word_boundary": self._word_boundary,
            }
        )
