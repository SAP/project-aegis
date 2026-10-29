from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import replace

from aegis._normalize import NormalizationConfig
from aegis.metadata import GuardMetadata
from aegis.telemetry import start_span
from aegis.types import OnFailAction, OnFailArg, ValidationError, ValidatorConfig
from aegis.utils import as_normalized_view
from aegis.validators.base import (
    ContentView,
    FailedResult,
    GuardResult,
    ValidationResult,
    Validator,
)

logger = logging.getLogger("aegis")


class Guard:
    """Pure validation engine with a fluent ``.use()`` API.

    All registered validators run concurrently on each ``validate()`` call. Input is a plain
    string or a :class:`ContentView`; validators receive the view (call ``view.render()`` for
    the flattened text). The engine never touches transport parts — adapters map parts to a view.

    Args:
        name: Identifier for this guard (used in spans / logs).
        normalize: Controls guard-side normalization applied to each content item's value *once*
            before fanning out to validators. Accepts a bool (convenience alias) or an explicit
            :class:`NormalizationConfig`. ``True`` applies the engine profile
            (:meth:`NormalizationConfig.for_engine` — NFKC-folded, variation-selector-stripped,
            homoglyph-mapped); ``False`` applies none; a ``NormalizationConfig`` applies exactly
            the strategies it declares. Validators see the canonical form; the caller's original
            input is never mutated and the normalized form does not escape via ``GuardResult``.
            Defaults to ``False`` so multilingual inputs round-trip untouched. Enable for
            security-sensitive detection over untrusted input (homoglyph / Unicode-bypass
            protection).
    """

    def __init__(self, name: str, normalize: bool | NormalizationConfig = False) -> None:
        self.name = name
        self._normalize = normalize
        self._validators: list[ValidatorConfig] = []

    def use(
        self,
        validator: Validator,
        on_fail: OnFailArg = OnFailAction.LOG,
    ) -> Guard:
        if not isinstance(validator, Validator):
            raise TypeError(
                f"Guard requires a Validator (with name and validate()). "
                f"{type(validator).__name__} does not satisfy Validator protocol."
            )
        self._validators.append(ValidatorConfig(validator=validator, on_fail=on_fail))
        return self

    async def validate(
        self,
        value: str | ContentView,
        metadata: GuardMetadata | None = None,
    ) -> GuardResult:
        if not self._validators:
            raise RuntimeError(
                f"Guard '{self.name}' has no validators registered; refusing to validate. "
                f"An empty guard would pass everything (a false sense of security) — register "
                f"at least one validator via .use()."
            )
        with start_span(
            f"guard.{self.name}.validate",
            attributes={"guard.name": self.name},
        ):
            view = as_normalized_view(value, normalize=self._normalize)
            results = await self._run_all(view, metadata)
            return GuardResult(results=results)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_all(
        self,
        view: ContentView,
        metadata: GuardMetadata | None,
    ) -> list[ValidationResult]:
        tasks = [self._run_one(config, view, metadata) for config in self._validators]
        results = await asyncio.gather(*tasks)
        return list(results)

    async def _run_one(
        self,
        config: ValidatorConfig,
        view: ContentView,
        metadata: GuardMetadata | None,
    ) -> ValidationResult:
        validator_name = config.validator.name

        with start_span(
            f"validator.{validator_name}",
            attributes={"validator.name": validator_name},
        ) as span:
            result = await self._invoke_validator(config.validator, view, metadata)
            result = replace(result, validator_name=validator_name)

            result_type = "fail" if isinstance(result, FailedResult) else "pass"
            span.set_attribute("validator.result_type", result_type)

            if isinstance(result, FailedResult):
                span.set_attribute(
                    "guard.on_fail_action",
                    config.on_fail.value if isinstance(config.on_fail, OnFailAction) else "custom",
                )
                await self._handle_fail(config, result, view, metadata)

            return result

    async def _invoke_validator(
        self,
        validator: Validator,
        view: ContentView,
        metadata: GuardMetadata | None,
    ) -> ValidationResult:
        if inspect.iscoroutinefunction(validator.validate):
            return await validator.validate(view, metadata=metadata)
        result: ValidationResult = await asyncio.to_thread(
            validator.validate,
            view,
            metadata=metadata,  # type: ignore[arg-type]
        )
        return result

    async def _handle_fail(
        self,
        config: ValidatorConfig,
        result: FailedResult,
        view: ContentView,
        metadata: GuardMetadata | None,
    ) -> None:
        on_fail = config.on_fail

        if isinstance(on_fail, OnFailAction):
            if on_fail is OnFailAction.EXCEPTION:
                raise ValidationError(result.error_message, result=result)
            if on_fail is OnFailAction.LOG:
                logger.warning(
                    "Validation failed: %s (validator=%s)",
                    result.error_message,
                    config.validator.name,
                )
            # NOOP: do nothing
        else:
            # Custom hooks stay string-based: pass the flattened text as validated_value.
            validated_value = view.render()
            if inspect.iscoroutinefunction(on_fail):
                await on_fail(
                    result=result,
                    validated_value=validated_value,
                    metadata=metadata,
                )
            else:
                on_fail(
                    result=result,
                    validated_value=validated_value,
                    metadata=metadata,
                )
