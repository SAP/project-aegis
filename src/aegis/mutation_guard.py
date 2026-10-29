from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import replace

from aegis.metadata import GuardMetadata
from aegis.telemetry import start_span
from aegis.utils import as_normalized_view
from aegis.validators.base import (
    ContentView,
    FailedResult,
    ItemEdit,
    MutationGuardResult,
    MutationValidator,
    ValidationResult,
)

logger = logging.getLogger("aegis")


class MutationGuard:
    """Sequential mutation pipeline over a :class:`ContentView`.

    Validators run in registration order. Each sees the current view (so a later validator
    sees earlier mutations), detects via ``validate``, and returns whole-item ``ItemEdit``s
    via ``mutate``. The engine rebuilds the view after each, so item count and order are
    preserved. Input is a plain string or a ``ContentView`` — the engine never touches
    transport parts (adapters map parts ↔ view).
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._validators: list[MutationValidator] = []

    @property
    def name(self) -> str:
        return self._name

    def use(self, validator: MutationValidator) -> MutationGuard:
        """Register a mutation validator. Validates protocol at registration time."""
        if not isinstance(validator, MutationValidator):
            raise TypeError(
                f"MutationGuard requires a MutationValidator (with validate() and mutate()). "
                f"{type(validator).__name__} does not satisfy the protocol."
            )
        self._validators.append(validator)
        return self

    async def validate(
        self,
        value: str | ContentView,
        metadata: GuardMetadata | None = None,
    ) -> MutationGuardResult:
        if not self._validators:
            raise RuntimeError(
                f"MutationGuard '{self._name}' has no validators registered; refusing to validate. "
                f"An empty guard is a no-op (a false sense of security) — register at least one "
                f"validator via .use()."
            )
        with start_span(
            f"mutation_guard.{self._name}.validate",
            attributes={"mutation_guard.name": self._name},
        ) as span:
            view = as_normalized_view(value)
            results, edits, current = await self._run_all(view, metadata)

            # "Was mutated" = the content actually changed, not "edits were emitted".
            # A bug that drops edits, or a no-op edit (same value), must read as unchanged.
            was_mutated = current != view
            span.set_attribute("mutation_guard.was_mutated", was_mutated)
            span.set_attribute("mutation_guard.mutation_count", len(edits))
            # Output mirrors input: string in → string out, view in → view out.
            output = current.render() if isinstance(value, str) else current
            return MutationGuardResult(results=results, was_mutated=was_mutated, output=output)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_all(
        self,
        view: ContentView,
        metadata: GuardMetadata | None,
    ) -> tuple[list[ValidationResult], list[ItemEdit], ContentView]:
        results: list[ValidationResult] = []
        edits: list[ItemEdit] = []
        current = view

        for validator in self._validators:
            with start_span(
                f"mutation_validator.{validator.name}",
                attributes={"validator.name": validator.name},
            ) as span:
                result = await self._invoke_validate(validator, current, metadata)
                result = replace(result, validator_name=validator.name)
                results.append(result)

                if isinstance(result, FailedResult):
                    span.set_attribute("validator.result_type", "fail")
                    logger.info(
                        "Issue detected: %s (validator=%s, guard=%s)",
                        result.error_message,
                        validator.name,
                        self._name,
                    )
                    new_edits = await self._invoke_mutate(validator, current, metadata, result)
                    # Here we do a quick check to determine if mutation has happened.
                    # Non-empty new_edits implies that mutation has taken place
                    span.set_attribute("mutated", bool(new_edits))
                    current = current.rebuild(new_edits)
                    edits.extend(new_edits)
                    logger.info(
                        "Mutation applied (validator=%s, guard=%s, edits=%d)",
                        validator.name,
                        self._name,
                        len(new_edits),
                    )
                else:
                    span.set_attribute("validator.result_type", "pass")
                    logger.debug(
                        "Validation passed (validator=%s, guard=%s)",
                        validator.name,
                        self._name,
                    )
                    span.set_attribute("mutated", False)

        return results, edits, current

    async def _invoke_validate(
        self,
        validator: MutationValidator,
        view: ContentView,
        metadata: GuardMetadata | None,
    ) -> ValidationResult:
        if inspect.iscoroutinefunction(validator.validate):
            return await validator.validate(view, metadata=metadata)
        return await asyncio.to_thread(validator.validate, view, metadata=metadata)  # type: ignore[return-value]

    async def _invoke_mutate(
        self,
        validator: MutationValidator,
        view: ContentView,
        metadata: GuardMetadata | None,
        failure: FailedResult,
    ) -> list[ItemEdit]:
        if inspect.iscoroutinefunction(validator.mutate):
            return await validator.mutate(view, metadata=metadata, validation_failure=failure)
        return await asyncio.to_thread(  # type: ignore[return-value]
            validator.mutate, view, metadata=metadata, validation_failure=failure
        )
