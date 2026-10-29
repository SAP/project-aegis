"""Detect an agent's output reproducing the system prompt, secrets, or canary tokens.

This module is the living specification for :class:`PromptLeakageCheck`; see
``docs/validators/detection/prompt-leakage-check.md`` for the user-facing guide. The short version:

An LLM agent is steered by a hidden **system prompt**. **System prompt leakage** is when the
agent's *output* reproduces that hidden material — verbatim, after cosmetic edits, or in
fragments — either spontaneously or because a user coaxed it out. This validator inspects the
agent's output before it crosses an external trust boundary and rejects output that reproduces
configured protected instruction text (OWASP ``LLM07:2025`` "inspect outputs for compliance").

What it deliberately is **not**: a semantic judge. It is a deterministic, string-level comparison
against a *known* protected corpus, so it cannot catch paraphrase, translation, novel encodings, or
leakage dripped across turns — those are separate validators. A PASS never means the prompt is
secret; per OWASP, a system prompt should hold no security-critical secret.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
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

# Source labels reported in ``details["source"]``. They name which protected corpus a match
# came from — never its content — so they are safe to expose to a caller.
_SOURCE_CANARY: Final = "canary"
_SOURCE_FRAGMENT: Final = "fragment"
_SOURCE_METADATA_SYSTEM: Final = "metadata_system"

# Tie-break priority when two matches sit at the same offset within one failure class. Lower
# wins. Explicit operator-configured fragments rank ahead of auto-derived system-prompt
# segments so the verdict is stable and does not depend on dict/set ordering.
_SOURCE_PRIORITY: Final = {_SOURCE_FRAGMENT: 0, _SOURCE_METADATA_SYSTEM: 1}


@dataclass(frozen=True)
class _Protected:
    """One item of protected material to compare the output against.

    Attributes:
        source: Which corpus this came from — ``_SOURCE_CANARY``, ``_SOURCE_FRAGMENT``, or
            ``_SOURCE_METADATA_SYSTEM``. Reported in ``details`` to tell a caller *where* a
            leak matched, without revealing the protected text itself.
        reference_index: The item's position within its own source list. An opaque, non-secret
            handle so a caller can identify which item matched without seeing its content.
        raw: The original text, used for an exact (character-for-character) substring check.
        normalized: The normalized form, used for normalized and fuzzy matching. Equal to
            ``raw`` when normalization is disabled.
    """

    source: str
    reference_index: int
    raw: str
    normalized: str


@dataclass(frozen=True)
class _Match:
    """A single detected leak, before it is turned into a ``FailedResult``.

    Ordering across candidates within one failure class is by ``match_offset`` first (the
    earliest leak in the output), then ``_SOURCE_PRIORITY``, then ``reference_index`` — a total
    order that makes the reported verdict deterministic.
    """

    error_code: str
    source: str
    reference_index: int
    match_offset: int
    match_length: int
    match_type: str | None = None
    similarity: float | None = None


class PromptLeakageCheck(BaseValidator):
    """Rejects agent output that reproduces the system prompt, configured secrets, or canary tokens.

    A deterministic, text-only, **detect-only** output validator. It runs under ``Guard`` and
    compares an agent's candidate output against an explicitly configured protected corpus —
    sensitive fragments and canary tokens — and, opt-in, against the actual system prompt
    (``GuardMetadata.system``). Reach for it as the last text-security gate before a response is
    returned, persisted, or exposed.

    It detects three things, in order of precedence:

    1. A configured **canary** token appearing in the output (highest precision — canaries are
       unique, so a match is unambiguous proof of leakage).
    2. A protected **fragment** (or a system-prompt segment) reproduced as an exact or
       normalized substring of the output.
    3. *(opt-in)* A near-verbatim, **fuzzy** match, for cosmetic edits that normalization alone
       does not fold.

    Normalization (off by default; opt in with ``normalize=True``) folds cheap evasions —
    case changes, added whitespace, zero-width characters, and NFKC-compatibility variants
    (e.g. full-width letters) — by
    normalizing both the output and the protected material before matching. It does **not**
    fold confusable look-alikes, decode Base64/hex, or reverse a Caesar shift; those are
    documented residual gaps handled by future, additive hardening (see the ADR).

    Out of scope by design (each is a separate single-concern validator): generic secret
    detection with no known reference, input-side prompt-injection detection, tool-egress
    inspection, paraphrase/translation (semantic) leakage, and cross-turn cumulative leakage.

    On a leak it returns a ``FailedResult`` whose ``error_message`` and ``details`` carry only
    safe metadata — a reference index, match type, similarity, and offsets — and **never** the
    matched text, protected text, or any snippet, so the validator cannot re-leak the secret
    into logs or results.

    Example:
        >>> from aegis import Guard
        >>> guard = Guard("output").use(PromptLeakageCheck(canaries=["a1b2c3d4-canary"]))
        >>> result = await guard.validate("Sure! My instructions start with a1b2c3d4-canary")
        >>> result.failures[0].error_code
        'PROMPT_LEAKAGE_CANARY'
    """

    # Error codes are a released contract: defined once here as the single source of truth
    # shared by validate(), the tests, and the doc page, and never renamed or repurposed.
    PROMPT_LEAKAGE_CANARY: Final = "PROMPT_LEAKAGE_CANARY"
    PROMPT_LEAKAGE_DETECTED: Final = "PROMPT_LEAKAGE_DETECTED"
    PROMPT_LEAKAGE_SUSPECTED: Final = "PROMPT_LEAKAGE_SUSPECTED"
    PROMPT_LEAKAGE_INPUT_TOO_LARGE: Final = "PROMPT_LEAKAGE_INPUT_TOO_LARGE"

    def __init__(
        self,
        sensitive_fragments: list[str] | None = None,
        canaries: list[str] | None = None,
        use_metadata_system: bool = False,
        similarity_threshold: float | None = None,
        min_fragment_length: int = 15,
        case_sensitive: bool = False,
        normalize: bool = False,
        max_input_chars: int | None = None,
    ) -> None:
        """Configure the protected corpus and matching behavior.

        At least one protected source must be configured — ``sensitive_fragments``,
        ``canaries``, or ``use_metadata_system=True``. A deterministic validator cannot tell a
        genuine leak from a hallucination that merely *sounds* internal without a ground-truth
        reference, so construction fails closed rather than checking against nothing.

        Args:
            sensitive_fragments: Protected instruction fragments to keep out of the output.
                Matched as exact/normalized substrings (any length) and, when fuzzy is enabled,
                fuzzily.
            canaries: Unique high-entropy tokens seeded into the system prompt. Any appearance
                in the output is definitive leakage, so they are matched with no minimum-length
                floor.
            use_metadata_system: When ``True``, also protect the agent's actual system prompt,
                read from ``GuardMetadata.system`` at call time and split into lines. Only lines
                whose normalized length is at least ``min_fragment_length`` are used, to avoid
                firing on generic boilerplate. When the metadata or its ``system`` field is
                absent at call time, this source contributes nothing (no error).
            similarity_threshold: Enables fuzzy (near-verbatim) matching when set. A float in
                ``[0.0, 1.0]`` (recommended 0.80–0.90); ``None`` disables fuzzy matching.
                Off by default because fuzzy matching is quadratic and adds false-positive risk.
            min_fragment_length: Minimum normalized length for a fragment/segment to be eligible
                for fuzzy matching, and for an auto-derived ``metadata.system`` line to be used
                at all. Suppresses noise from short generic overlaps. Explicit
                ``sensitive_fragments`` are still matched exactly at any length.
            case_sensitive: When ``True``, skip case-folding during normalization. Only takes
                effect when ``normalize=True`` — case-folding is one step of normalization, so
                with ``normalize=False`` matching is fully literal regardless of this flag.
            normalize: When ``True``, normalize both the output and the protected material before
                matching — NFKC, strip zero-width/format characters, collapse whitespace, and
                case-fold (unless ``case_sensitive``). Defeats cheap cosmetic evasions at the cost
                of some strictness. Defaults to ``False`` (matching is fully literal): opt in
                deliberately, mirroring the engine's off-by-default ``Guard(normalize=...)`` so
                normalization is never applied silently. The folds here are script-safe — they
                never map homoglyphs, so genuine non-Latin text is not rewritten to ASCII.
            max_input_chars: Optional resource valve. When set and the rendered output exceeds
                it, the output is rejected with ``PROMPT_LEAKAGE_INPUT_TOO_LARGE`` rather than
                scanned. Off by default — a hard input-size limit belongs to a dedicated length
                validator; this is only a convenience cap on fuzzy-matching work.

        Raises:
            ValueError: If no protected source is configured; if any ``sensitive_fragments`` or
                ``canaries`` entry is empty or whitespace-only (an empty string is a substring
                of every output and would force a spurious match); if ``similarity_threshold``
                is outside ``[0.0, 1.0]``; if ``min_fragment_length`` is negative; or if
                ``max_input_chars`` is negative.
        """
        fragments = list(sensitive_fragments or [])
        canary_tokens = list(canaries or [])

        # Fail closed: without a ground-truth reference there is nothing to compare against, so
        # this validator would silently pass everything — a false sense of security.
        if not fragments and not canary_tokens and not use_metadata_system:
            raise ValueError(
                "PromptLeakageCheck requires at least one protected source: pass "
                "sensitive_fragments, canaries, or set use_metadata_system=True."
            )

        # An empty or whitespace-only needle is a substring of every possible output, so it
        # would reject all input. Reject it at construction — this is a real gap, not a style
        # choice. (Report the source and index, never the value, in case the value is secret.)
        for index, fragment in enumerate(fragments):
            if not fragment.strip():
                raise ValueError(f"sensitive_fragments[{index}] is empty or whitespace-only.")
        for index, canary in enumerate(canary_tokens):
            if not canary.strip():
                raise ValueError(f"canaries[{index}] is empty or whitespace-only.")

        if similarity_threshold is not None and not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError(f"similarity_threshold must be within [0.0, 1.0], got {similarity_threshold}.")
        if min_fragment_length < 0:
            raise ValueError(f"min_fragment_length must be non-negative, got {min_fragment_length}.")
        if max_input_chars is not None and max_input_chars < 0:
            # A negative limit makes len(output) > limit true for every candidate — even empty
            # output — so it would reject everything with an impossible bound. Guard it like the
            # other numeric controls. (0 is allowed: it means "reject any non-empty output".)
            raise ValueError(f"max_input_chars must be non-negative, got {max_input_chars}.")

        self._use_metadata_system = use_metadata_system
        self._similarity_threshold = similarity_threshold
        self._min_fragment_length = min_fragment_length
        self._normalize = normalize
        self._max_input_chars = max_input_chars

        # Build the normalization profile once. ``None`` means "no normalization" — matching is
        # fully literal. Otherwise the script-safe matching profile (NFKC + strip format chars +
        # collapse whitespace + casefold); this validator always uses NFKC (no compatibility_folding
        # knob) and case_sensitive only takes effect here. ``self._normalize`` is retained because
        # the "normalized" match-type gate in _find_substring reads it.
        self._norm_config = NormalizationConfig.for_validators(case_sensitive=case_sensitive) if normalize else None

        # Canaries and fragments are fixed for the validator's lifetime, so normalize them once
        # here rather than on every validate() call. They live in separate lists because each is
        # checked by a different matcher — canaries by _find_canary, fragments (plus the per-call
        # metadata.system segments) by _find_substring / _find_fuzzy — so each matcher receives
        # exactly the items it checks, with no source filtering. Built after self._norm_config
        # above, which _normalize_text reads.
        self._canaries: list[_Protected] = [
            _Protected(_SOURCE_CANARY, index, canary, self._normalize_text(canary))
            for index, canary in enumerate(canary_tokens)
        ]
        self._fragments: list[_Protected] = [
            _Protected(_SOURCE_FRAGMENT, index, fragment, self._normalize_text(fragment))
            for index, fragment in enumerate(fragments)
        ]

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Inspect the candidate output and report whether it leaks protected material.

        Scans ``view.render()`` — the whole candidate, so a Markdown/JSON/code wrapper earns no
        exemption. Since ``render()`` joins parts with a newline, a fragment spanning a part
        boundary is caught only when that boundary is a whitespace break; a mid-token split is a
        residual miss (deferred separator/interleaving handling — see the ADR).

        Returns the single highest-precedence failure (``INPUT_TOO_LARGE`` → ``CANARY`` →
        ``DETECTED`` → ``SUSPECTED``), or ``PassedResult`` if nothing matched.

        Args:
            view: The candidate output as a :class:`ContentView`. Never ``None``; may be empty.
            metadata: Surrounding agent context. Read only when ``use_metadata_system=True``,
                for its ``system`` field. May be ``None``.

        Returns:
            ``PassedResult`` with ``details = {"fragments_checked", "sources", "fuzzy_enabled"}``
            when no configured condition matched, or a ``FailedResult`` carrying a stable
            ``error_code`` and a safe-summary ``details`` mapping (no matched or protected text).
        """
        raw_output = view.render()

        # The size valve runs first: it is both the highest-precedence failure and the point of
        # the cap — reject an oversized candidate before doing any (potentially quadratic) work.
        if self._max_input_chars is not None and len(raw_output) > self._max_input_chars:
            return FailedResult(
                error_code=self.PROMPT_LEAKAGE_INPUT_TOO_LARGE,
                error_message=f"Candidate output exceeds the {self._max_input_chars}-character inspection limit.",
                details={"length": len(raw_output), "limit": self._max_input_chars},
            )

        # Build the fragment targets — the configured fragments plus any per-call metadata.system
        # segments, both matched by _find_substring / _find_fuzzy — before the empty-output check,
        # so a PASS always summarizes the same configured sources whether or not there was content
        # to scan. Canaries need no per-call assembly; they are the static self._canaries list.
        targets = self._build_fragment_targets(metadata)

        # Nothing can have leaked from empty or whitespace-only output.
        if not raw_output.strip():
            return self._passed(self._canaries + targets)

        norm_output = self._normalize_text(raw_output)

        # Check by precedence and short-circuit: a canary match outranks any fragment match,
        # which outranks any fuzzy match, so there is no need to look for a weaker class once a
        # stronger one has fired. Each matcher gets exactly the list it checks — no source filter.
        canary_match = self._find_canary(norm_output, self._canaries)
        if canary_match is not None:
            return self._to_failure(canary_match)

        substring_match = self._find_substring(raw_output, norm_output, targets)
        if substring_match is not None:
            return self._to_failure(substring_match)

        if self._similarity_threshold is not None:
            fuzzy_match = self._find_fuzzy(norm_output, targets)
            if fuzzy_match is not None:
                return self._to_failure(fuzzy_match)

        return self._passed(self._canaries + targets)

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize_text(self, text: str) -> str:
        """Return the matching form of ``text``, applied identically to output and corpus.

        Delegates to the shared :func:`aegis._normalize.normalize` with ``self._norm_config``
        (built once at construction from ``normalize`` / ``case_sensitive``; this validator always
        uses NFKC). When normalization is disabled (``self._norm_config is None``) the text is
        returned unchanged and matching is fully literal. Both the output (at ``validate``) and the
        protected corpus (canaries/fragments at construction, ``metadata.system`` per call) pass
        through this one method, so the two sides are always folded identically — which is why the
        validator normalizes the output itself rather than relying on the engine's ``normalize``.
        """
        if self._norm_config is None:
            return text
        return apply_normalization(text, self._norm_config)

    # ------------------------------------------------------------------
    # Corpus assembly
    # ------------------------------------------------------------------

    def _build_fragment_targets(self, metadata: GuardMetadata | None) -> list[_Protected]:
        """Collect the fragment-like items matched by substring and fuzzy scanning, each normalized.

        These are the configured fragments plus — when ``use_metadata_system`` is set and
        ``metadata.system`` is present — its lines as system-prompt segments. Fragments and
        segments share the same matchers (``_find_substring`` / ``_find_fuzzy``), so they belong in
        one list; canaries are matched separately (``_find_canary``) and are not included here. A
        segment is kept only when its normalized form is non-empty and reaches
        ``min_fragment_length`` — a short boilerplate line ("You are a helpful assistant.") would
        otherwise cause noisy matches, and a blank line would match everything.
        """
        system = metadata.system if metadata is not None else None
        if not self._use_metadata_system or not system:
            # No per-call segments to add, so return the cached fragment list directly. Every
            # caller only reads it, never mutates it, so sharing the one instance is safe.
            return self._fragments

        targets = list(self._fragments)
        # Re-index from zero within this source so the reference index stays a stable, meaningful
        # handle into the system-prompt lines the operator can inspect.
        segment_index = 0
        for line in system.splitlines():
            normalized = self._normalize_text(line)
            # Require a non-empty normalized form, not just the length gate: with
            # min_fragment_length=0 the gate alone (0 >= 0) would admit a blank line, and an empty
            # needle matches every output at offset 0 (str.find("") == 0). Explicit fragments and
            # canaries are checked for emptiness at construction; auto-derived segments are guarded
            # here instead, the one place they are built.
            if normalized and len(normalized) >= self._min_fragment_length:
                targets.append(_Protected(_SOURCE_METADATA_SYSTEM, segment_index, line, normalized))
                segment_index += 1

        return targets

    # ------------------------------------------------------------------
    # Matching rules
    # ------------------------------------------------------------------

    def _find_canary(self, norm_output: str, canaries: list[_Protected]) -> _Match | None:
        """Return the best canary match, or ``None``.

        A canary is unique and high-entropy, so its mere appearance is a leak — there is no
        minimum-length floor. Matching uses the normalized forms so a canary broken up with
        zero-width characters or case changes is still caught.
        """
        matches = [
            _Match(
                error_code=self.PROMPT_LEAKAGE_CANARY,
                source=item.source,
                reference_index=item.reference_index,
                match_offset=norm_output.find(item.normalized),
                match_length=len(item.normalized),
            )
            for item in canaries
            if item.normalized and item.normalized in norm_output
        ]
        return self._select(matches)

    def _find_substring(self, raw_output: str, norm_output: str, targets: list[_Protected]) -> _Match | None:
        """Return the best exact/normalized substring match over fragments and system segments.

        For each item, an exact (character-for-character) substring is preferred and reported as
        ``match_type="exact"`` with offsets into the raw output. Failing that, when
        normalization is enabled, a normalized substring is reported as ``match_type="normalized"``
        with offsets into the normalized output. Explicit fragments are checked at any length;
        auto-derived system segments were already length-gated when the targets were built.
        """
        matches: list[_Match] = []
        for item in targets:
            raw_offset = raw_output.find(item.raw)
            if raw_offset != -1:
                matches.append(
                    _Match(
                        error_code=self.PROMPT_LEAKAGE_DETECTED,
                        source=item.source,
                        reference_index=item.reference_index,
                        match_offset=raw_offset,
                        match_length=len(item.raw),
                        match_type="exact",
                    )
                )
            elif self._normalize and item.normalized and item.normalized in norm_output:
                matches.append(
                    _Match(
                        error_code=self.PROMPT_LEAKAGE_DETECTED,
                        source=item.source,
                        reference_index=item.reference_index,
                        match_offset=norm_output.find(item.normalized),
                        match_length=len(item.normalized),
                        match_type="normalized",
                    )
                )

        return self._select(matches)

    def _find_fuzzy(self, norm_output: str, targets: list[_Protected]) -> _Match | None:
        """Return the best near-verbatim (fuzzy) match at/above the threshold, or ``None``.

        Only fragments and segments whose normalized length reaches ``min_fragment_length`` are
        eligible — short strings produce noisy similarity scores. For each, anchor on the
        longest common block via ``difflib.SequenceMatcher`` (with autojunk off so long strings
        are not silently degraded), take an output window the size of the needle around that
        anchor, and score the two. This is O(n·m) per needle, which is acceptable only because
        fuzzy matching is opt-in and off by default; the ``max_input_chars`` valve bounds it.
        """
        threshold = self._similarity_threshold
        if threshold is None:
            raise AssertionError("_find_fuzzy requires similarity_threshold to be set")
        matches: list[_Match] = []

        for item in targets:
            needle = item.normalized
            if len(needle) < self._min_fragment_length:
                continue

            matcher = SequenceMatcher(None, needle, norm_output, autojunk=False)
            block = matcher.find_longest_match()
            if block.size == 0:
                continue

            # Align an output window to where the needle's matched block sits, so a small edit
            # (an inserted or changed character) does not drag the score down the way comparing
            # the needle against the whole, much longer output would.
            start = max(0, block.b - block.a)
            window = norm_output[start : start + len(needle)]
            similarity = SequenceMatcher(None, needle, window, autojunk=False).ratio()

            if similarity >= threshold:
                matches.append(
                    _Match(
                        error_code=self.PROMPT_LEAKAGE_SUSPECTED,
                        source=item.source,
                        reference_index=item.reference_index,
                        match_offset=start,
                        match_length=len(window),
                        match_type="fuzzy",
                        similarity=round(similarity, 4),
                    )
                )

        return self._select(matches)

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _select(matches: list[_Match]) -> _Match | None:
        """Return the winning match within one failure class, or ``None`` if there are none.

        Ordering is by earliest offset, then source priority, then reference index — a total
        order, so the reported verdict is deterministic regardless of corpus iteration order.
        """
        if not matches:
            return None
        return min(
            matches,
            key=lambda m: (m.match_offset, _SOURCE_PRIORITY.get(m.source, 0), m.reference_index),
        )

    def _to_failure(self, match: _Match) -> FailedResult:
        """Build the ``FailedResult`` for a match, carrying only safe summary metadata.

        The message and details deliberately exclude the matched text, the protected text, and
        any snippet: echoing them would re-leak the secret into logs and results, which is the
        very thing this validator exists to prevent.
        """
        if match.error_code == self.PROMPT_LEAKAGE_CANARY:
            return FailedResult(
                error_code=match.error_code,
                error_message=f"Output reproduces a protected canary token (reference {match.reference_index}).",
                details={
                    "source": match.source,
                    "reference_index": match.reference_index,
                    "match_offset": match.match_offset,
                },
            )

        if match.error_code == self.PROMPT_LEAKAGE_SUSPECTED:
            return FailedResult(
                error_code=match.error_code,
                error_message=(
                    f"Output closely resembles protected instruction text "
                    f"(reference {match.reference_index}, similarity {match.similarity})."
                ),
                details={
                    "match_type": match.match_type,
                    "source": match.source,
                    "reference_index": match.reference_index,
                    "similarity": match.similarity,
                    "match_offset": match.match_offset,
                    "match_length": match.match_length,
                },
            )

        return FailedResult(
            error_code=match.error_code,
            error_message=(
                f"Output reproduces protected instruction text "
                f"(reference {match.reference_index}, match type {match.match_type})."
            ),
            details={
                "match_type": match.match_type,
                "source": match.source,
                "reference_index": match.reference_index,
                "match_offset": match.match_offset,
                "match_length": match.match_length,
            },
        )

    def _passed(self, corpus: list[_Protected]) -> PassedResult:
        """Build the ``PassedResult``, summarizing what was checked without exposing it.

        A PASS certifies only that none of the configured deterministic conditions matched —
        never that the prompt is secret (OWASP LLM07). ``sources`` lists the active corpus
        categories in a fixed order; ``fragments_checked`` counts every protected item compared
        against the output (canaries, fragments, and eligible system-prompt segments).
        """
        present = {item.source for item in corpus}
        ordered_sources = [
            source for source in (_SOURCE_CANARY, _SOURCE_FRAGMENT, _SOURCE_METADATA_SYSTEM) if source in present
        ]
        return PassedResult(
            details={
                "fragments_checked": len(corpus),
                "sources": ordered_sources,
                "fuzzy_enabled": self._similarity_threshold is not None,
            }
        )
