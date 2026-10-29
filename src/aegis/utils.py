from dataclasses import replace

from aegis._normalize import NormalizationConfig
from aegis._normalize import normalize as apply_normalization
from aegis.validators.base import ContentView


def as_normalized_view(value: str | ContentView, *, normalize: bool | NormalizationConfig = False) -> ContentView:
    """Normalize the ``validate`` input into a :class:`ContentView`.

    A ``ContentView`` is used as-is; a string becomes a single text item. ``normalize`` accepts
    either a bool (a convenience alias: ``True`` applies the engine profile
    :meth:`NormalizationConfig.for_engine`, ``False`` applies nothing) or an explicit
    :class:`NormalizationConfig`. When normalization is active, each item's value is folded via
    :func:`aegis._normalize.normalize` before validators see it; the caller's input is never
    mutated.
    """
    view = value if isinstance(value, ContentView) else ContentView.from_text(value)
    config = _resolve_config(normalize)
    if config is not None:
        view = ContentView(
            items=tuple(replace(item, value=apply_normalization(item.value, config)) for item in view.items)
        )
    return view


def _resolve_config(normalize: bool | NormalizationConfig) -> NormalizationConfig | None:
    """Map the ``bool | NormalizationConfig`` alias to a config, or ``None`` for no normalization."""
    if isinstance(normalize, NormalizationConfig):
        return normalize
    return NormalizationConfig.for_engine() if normalize else None
