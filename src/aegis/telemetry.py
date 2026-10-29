from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import trace

    _tracer = trace.get_tracer("aegis", "0.2.0")
except ImportError:  # pragma: no cover — defensive fallback
    _tracer = None  # type: ignore[assignment]


class _NoOpSpan:
    """Minimal stand-in when no OTEL tracer is available."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ARG002
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        pass

    def record_exception(self, exception: BaseException) -> None:  # noqa: ARG002
        pass

    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


_NOOP_SPAN = _NoOpSpan()


@contextmanager
def start_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """Start an OTEL span if a tracer is available, otherwise yield a no-op."""
    if _tracer is not None:
        with _tracer.start_as_current_span(name) as span:
            if attributes:
                for k, v in attributes.items():
                    span.set_attribute(k, v)
            yield span
    else:
        yield _NOOP_SPAN
