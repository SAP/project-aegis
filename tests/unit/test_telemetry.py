from __future__ import annotations

import pytest

from aegis import Guard
from aegis.validators import BaseValidator, PassedResult


class AlwaysPass(BaseValidator):
    """Minimal inline validator — the engine tests only need something that
    satisfies the protocol and emits a span."""

    async def validate(self, view, metadata=None):
        view.render()
        return PassedResult()


class TestTelemetrySpans:
    """Verify OTEL spans are created when a tracer is available."""

    @pytest.mark.asyncio
    async def test_spans_created(self):
        """Set up an SDK provider with a collecting exporter, run validate, check spans."""
        try:
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                SimpleSpanProcessor,
                SpanExporter,
                SpanExportResult,
            )
        except ImportError:
            pytest.skip("opentelemetry-sdk not installed")

        from opentelemetry import trace

        # Minimal in-memory exporter
        class _Collector(SpanExporter):
            def __init__(self):
                self.spans = []

            def export(self, spans):
                self.spans.extend(spans)
                return SpanExportResult.SUCCESS

            def shutdown(self):
                pass

        collector = _Collector()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(collector))
        old_provider = trace.get_tracer_provider()
        trace.set_tracer_provider(provider)

        guard = Guard("test-otel").use(AlwaysPass())
        await guard.validate(value="hello")

        provider.force_flush()
        span_names = [s.name for s in collector.spans]

        assert any("guard.test-otel.validate" in n for n in span_names)
        assert any("validator.AlwaysPass" in n for n in span_names)

        # Check attributes on validator span
        val_span = next(s for s in collector.spans if "validator." in s.name)
        attrs = dict(val_span.attributes)
        assert attrs["validator.name"] == "AlwaysPass"
        assert attrs["validator.result_type"] == "pass"

        # Restore
        trace.set_tracer_provider(old_provider)

    @pytest.mark.asyncio
    async def test_noop_when_no_sdk(self):
        """Guard works even if telemetry returns a no-op span."""
        guard = Guard("no-sdk").use(AlwaysPass())
        result = await guard.validate(value="hello")
        assert not result.has_failures
