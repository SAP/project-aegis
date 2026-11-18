"""PII sanitizer — detect and redact personally identifiable information in content."""

from __future__ import annotations

from collections.abc import Iterable, Set
from typing import Any, Final

from aegis.metadata import GuardMetadata
from aegis.validators import (
    BaseMutationValidator,
    ContentView,
    FailedResult,
    ItemEdit,
    PassedResult,
    ValidationResult,
)
from aegis.validators.mutation.pii_sanitizer._defaults import (
    CREDIT_CARD_CANDIDATE_REGEX,
    DEFAULT_PII_ENTITIES,
    EMAIL_REGEX,
    IBAN_REGEX,
    IPV4_REGEX,
    PHONE_REGEX,
    SSN_REGEX,
    PiiEntity,
    is_luhn_valid,
)


class PiiSanitizer(BaseMutationValidator):
    """Detects and redacts Personally Identifiable Information (PII) from content.

    ``PiiSanitizer`` identifies sensitive data entities — such as email addresses,
    phone numbers, credit card numbers, IBANs, Social Security numbers (SSNs),
    and IP addresses — and redacts them with structured placeholder masks (for
    example, ``<EMAIL>`` or ``<CREDIT_CARD>``).

    Under ``MutationGuard``, it sanitizes content in-place to protect user privacy
    and ensure compliance with data protection regulations (such as GDPR, CCPA, and
    enterprise privacy mandates). Under ``Guard``, it acts as a detector, rejecting
    any input that contains unmasked sensitive data.

    Args:
        entities: The set of PII entity types to detect and redact. Defaults to all
            supported entities (:data:`DEFAULT_PII_ENTITIES`).
        mask_format: A formatting string for replacing detected entities. The token
            ``{entity}`` is replaced with the matching entity type name.
            Defaults to ``"<{entity}>"``.

    Example:
        >>> from aegis import MutationGuard
        >>> from aegis.validators.mutation import PiiSanitizer
        >>> guard = MutationGuard("output").use(PiiSanitizer())
        >>> result = await guard.validate("Contact me at alice@example.com.")
        >>> result.was_mutated
        True
        >>> result.output
        'Contact me at <EMAIL>.'
    """

    PII_DETECTED: Final = "PII_DETECTED"

    def __init__(
        self,
        *,
        entities: Set[PiiEntity] | Iterable[PiiEntity] | None = None,
        mask_format: str = "<{entity}>",
    ) -> None:
        if entities is None:
            self._entities: frozenset[PiiEntity] = DEFAULT_PII_ENTITIES
        else:
            self._entities = frozenset(entities)
        self._mask_format = mask_format

    def _find_spans(self, text: str) -> list[tuple[int, int, PiiEntity]]:
        """Identify non-overlapping start/end spans of configured PII entities in ``text``."""
        candidates: list[tuple[int, int, PiiEntity]] = []

        if PiiEntity.EMAIL in self._entities:
            for match in EMAIL_REGEX.finditer(text):
                candidates.append((match.start(), match.end(), PiiEntity.EMAIL))

        if PiiEntity.IPV4 in self._entities:
            for match in IPV4_REGEX.finditer(text):
                candidates.append((match.start(), match.end(), PiiEntity.IPV4))

        if PiiEntity.SSN in self._entities:
            for match in SSN_REGEX.finditer(text):
                candidates.append((match.start(), match.end(), PiiEntity.SSN))

        if PiiEntity.IBAN in self._entities:
            for match in IBAN_REGEX.finditer(text):
                candidates.append((match.start(), match.end(), PiiEntity.IBAN))

        if PiiEntity.CREDIT_CARD in self._entities:
            for match in CREDIT_CARD_CANDIDATE_REGEX.finditer(text):
                matched_str = match.group(0)
                if is_luhn_valid(matched_str):
                    candidates.append((match.start(), match.end(), PiiEntity.CREDIT_CARD))

        if PiiEntity.PHONE in self._entities:
            for match in PHONE_REGEX.finditer(text):
                matched_str = match.group(0).strip()
                # Ensure candidate has at least 7 digits to prevent matching short numeric sequences
                digits_count = sum(1 for c in matched_str if c.isdigit())
                if 7 <= digits_count <= 15:
                    candidates.append((match.start(), match.end(), PiiEntity.PHONE))

        if not candidates:
            return []

        # Sort by start offset ascending, and longer spans first on tie
        candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))

        # Discard overlapping intervals (greedy selection)
        non_overlapping: list[tuple[int, int, PiiEntity]] = []
        last_end = -1
        for start, end, entity in candidates:
            if start >= last_end:
                non_overlapping.append((start, end, entity))
                last_end = end

        return non_overlapping

    def _clean(self, text: str) -> tuple[str, list[PiiEntity]]:
        """Replace all detected PII entity spans with mask tokens."""
        spans = self._find_spans(text)
        if not spans:
            return text, []

        detected: list[PiiEntity] = []
        result_chars = list(text)

        # Replace from end to start to preserve earlier indices
        for start, end, entity in reversed(spans):
            mask = self._mask_format.format(entity=entity.value)
            result_chars[start:end] = list(mask)
            detected.append(entity)

        detected.reverse()
        return "".join(result_chars), detected

    async def validate(self, view: ContentView, metadata: GuardMetadata | None = None) -> ValidationResult:
        """Report whether any part contains unmasked PII.

        Args:
            view: Content view under inspection.
            metadata: Unused agent context.

        Returns:
            :class:`PassedResult` if no configured PII entity is present,
            otherwise :class:`FailedResult` with ``error_code``
            :attr:`PII_DETECTED`. Details include the detected entity types and
            counts, but never raw sensitive text.
        """
        all_detected: list[str] = []
        changed_parts = 0

        for part in view.items:
            _, detected = self._clean(part.value)
            if detected:
                changed_parts += 1
                all_detected.extend(entity.value for entity in detected)

        if all_detected:
            details: dict[str, Any] = {
                "detected_entities": sorted(set(all_detected)),
                "total_matches": len(all_detected),
                "affected_parts": changed_parts,
            }
            return FailedResult(
                error_code=self.PII_DETECTED,
                error_message=f"Content contains unredacted PII entities: {', '.join(sorted(set(all_detected)))}.",
                details=details,
            )

        return PassedResult()

    async def mutate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
        validation_failure: FailedResult | None = None,
    ) -> list[ItemEdit]:
        """Return whole-part replacements for parts containing PII.

        Args:
            view: Content view to sanitize.
            metadata: Unused.
            validation_failure: Unused.

        Returns:
            List of :class:`ItemEdit` replacing parts that carried PII.
        """
        edits: list[ItemEdit] = []
        for part in view.items:
            sanitized, detected = self._clean(part.value)
            if detected:
                edits.append(ItemEdit(part.id, sanitized))
        return edits
