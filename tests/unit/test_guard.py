from __future__ import annotations

import asyncio
import logging
import time

import pytest

from _a2a_compat import text_part
from aegis import (
    BaseValidator,
    FailedResult,
    Guard,
    GuardMetadata,
    GuardResult,
    OnFailAction,
    PassedResult,
    ValidationError,
)
from aegis.a2a._utils import build_view

# ---------------------------------------------------------------------------
# Helper validators
# ---------------------------------------------------------------------------


class AlwaysPass(BaseValidator):
    async def validate(self, view, metadata=None):
        view.render()
        return PassedResult()


class AlwaysFail(BaseValidator):
    async def validate(self, view, metadata=None):
        view.render()
        return FailedResult(error_message="always fails", error_code="ALWAYS_FAIL")


class SlowValidator(BaseValidator):
    """Takes a fixed delay — used to prove concurrency."""

    def __init__(self, delay: float, *, name: str = "SlowValidator"):
        self._delay = delay
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def validate(self, view, metadata=None):
        view.render()
        await asyncio.sleep(self._delay)
        return PassedResult()


class SyncValidator:
    """Plain sync validator — no async."""

    @property
    def name(self) -> str:
        return "SyncValidator"

    def validate(self, view, metadata=None):
        value = view.render()
        if value and "bad" in value:
            return FailedResult(error_message="bad word", error_code="BAD")
        return PassedResult()


class InputCapture(BaseValidator):
    """Captures the value and metadata to verify they are forwarded."""

    def __init__(self):
        self.captured: dict = {}

    async def validate(self, view, metadata=None):
        value = view.render()
        self.captured = {"value": value, "metadata": metadata}
        return PassedResult()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGuardConstruction:
    def test_use_returns_self(self):
        g = Guard("test")
        assert g.use(AlwaysPass()) is g

    def test_chaining(self):
        g = Guard("test").use(AlwaysPass()).use(AlwaysFail())
        assert len(g._validators) == 2


class TestValidateReturnsGuardResult:
    @pytest.mark.asyncio
    async def test_returns_guard_result(self):
        result = await Guard("t").use(AlwaysPass()).validate(value="hi")
        assert isinstance(result, GuardResult)

    @pytest.mark.asyncio
    async def test_empty_guard_raises(self):
        # A guard with no validators would pass everything — refuse loudly instead.
        with pytest.raises(RuntimeError, match="no validators"):
            await Guard("empty").validate("hi")


class TestOnFailLog:
    @pytest.mark.asyncio
    async def test_default_on_fail_logs(self, caplog):
        guard = Guard("t").use(AlwaysFail())
        with caplog.at_level(logging.WARNING, logger="aegis"):
            result = await guard.validate(value="test")
        assert result.has_failures
        assert "always fails" in caplog.text

    @pytest.mark.asyncio
    async def test_log_does_not_raise(self):
        guard = Guard("t").use(AlwaysFail(), on_fail=OnFailAction.LOG)
        result = await guard.validate(value="test")
        assert len(result.failures) == 1


class TestOnFailException:
    @pytest.mark.asyncio
    async def test_raises_validation_error(self):
        guard = Guard("t").use(AlwaysFail(), on_fail=OnFailAction.EXCEPTION)
        with pytest.raises(ValidationError, match="always fails") as exc_info:
            await guard.validate(value="test")
        assert exc_info.value.result is not None
        assert exc_info.value.result.error_code == "ALWAYS_FAIL"


class TestOnFailNoop:
    @pytest.mark.asyncio
    async def test_noop_records_failure_silently(self, caplog):
        guard = Guard("t").use(AlwaysFail(), on_fail=OnFailAction.NOOP)
        with caplog.at_level(logging.WARNING, logger="aegis"):
            result = await guard.validate(value="test")
        assert result.has_failures
        assert caplog.text == ""  # nothing logged


class TestCustomOnFail:
    @pytest.mark.asyncio
    async def test_sync_callable(self):
        captured = {}

        def handler(result, **kwargs):
            captured["result"] = result
            captured.update(kwargs)

        guard = Guard("t").use(AlwaysFail(), on_fail=handler)
        metadata = GuardMetadata(system="sys-prompt")
        result = await guard.validate(value="hello", metadata=metadata)
        assert result.has_failures
        assert captured["result"].error_code == "ALWAYS_FAIL"
        assert captured["validated_value"] == "hello"
        assert captured["metadata"] is metadata

    @pytest.mark.asyncio
    async def test_async_callable(self):
        captured = {}

        async def handler(result, **kwargs):
            captured["result"] = result

        guard = Guard("t").use(AlwaysFail(), on_fail=handler)
        result = await guard.validate(value="x")
        assert result.has_failures
        assert captured["result"].error_code == "ALWAYS_FAIL"

    @pytest.mark.asyncio
    async def test_callable_that_raises(self):
        def handler(result, **kwargs):
            raise ValidationError("custom boom", result=result)

        guard = Guard("t").use(AlwaysFail(), on_fail=handler)
        with pytest.raises(ValidationError, match="custom boom"):
            await guard.validate(value="x")


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_validators_run_concurrently(self):
        """Three validators each taking 0.1s should finish in ~0.1s, not 0.3s."""
        guard = (
            Guard("t")
            .use(SlowValidator(0.1, name="A"))
            .use(SlowValidator(0.1, name="B"))
            .use(SlowValidator(0.1, name="C"))
        )
        start = time.monotonic()
        result = await guard.validate(value="x")
        elapsed = time.monotonic() - start
        assert elapsed < 0.25  # generous margin, but well under 0.3
        assert len(result.results) == 3

    @pytest.mark.asyncio
    async def test_results_order_matches_registration(self):
        guard = (
            Guard("t")
            .use(SlowValidator(0.05, name="First"))
            .use(SlowValidator(0.01, name="Second"))
            .use(SlowValidator(0.03, name="Third"))
        )
        result = await guard.validate(value="x")
        names = [r.validator_name for r in result.results]
        assert names == ["First", "Second", "Third"]


class TestValidatorName:
    @pytest.mark.asyncio
    async def test_validator_name_set_by_guard(self):
        guard = Guard("t").use(AlwaysPass()).use(AlwaysFail())
        result = await guard.validate(value="x")
        assert result.results[0].validator_name == "AlwaysPass"
        assert result.results[1].validator_name == "AlwaysFail"


class TestValueAndMetadata:
    @pytest.mark.asyncio
    async def test_value_and_metadata_forwarded_to_validator(self):
        v = InputCapture()
        guard = Guard("t").use(v)
        metadata = GuardMetadata(system="sys")
        await guard.validate(value="hi", metadata=metadata)
        assert v.captured["value"] == "hi"
        assert v.captured["metadata"] is metadata

    @pytest.mark.asyncio
    async def test_parts_flattened_to_value(self):
        """Multi-part input is flattened to a single text value for validators."""
        v = InputCapture()
        guard = Guard("t").use(v)
        parts = [text_part("hello"), text_part("world")]
        await guard.validate(build_view(parts))
        assert v.captured["value"] == "hello\nworld"

    @pytest.mark.asyncio
    async def test_empty_input_yields_empty_string(self):
        """With no value or parts, validators still receive a str (never None)."""
        v = InputCapture()
        guard = Guard("t").use(v)
        await guard.validate("")
        assert v.captured["value"] == ""


class TestMixedSyncAsync:
    @pytest.mark.asyncio
    async def test_sync_and_async_together(self):
        guard = Guard("t").use(SyncValidator()).use(AlwaysPass())
        result = await guard.validate(value="good text")
        assert len(result.results) == 2
        assert not result.has_failures

    @pytest.mark.asyncio
    async def test_sync_validator_fails(self):
        guard = Guard("t").use(SyncValidator(), on_fail=OnFailAction.NOOP)
        result = await guard.validate(value="bad input")
        assert result.has_failures
        assert result.failures[0].error_code == "BAD"
