from __future__ import annotations

import inspect
from collections.abc import Awaitable
from typing import Any

from a2a.types import Part

from aegis.a2a._compat import copy_text_part, is_text_part, part_text
from aegis.validators.base import ContentItem, ContentType, ContentView


def _part_id(index: int) -> str:
    """Opaque view-item id for the part at ``index`` in the original parts list.

    ``build_view`` and ``apply_view`` must agree on this format. The id is matched by
    equality, never parsed back into an int, so the scheme stays encapsulated here.
    """
    return f"p{index}"


def build_view(parts: list[Part]) -> ContentView:
    """Project A2A parts into a :class:`ContentView` (text parts only; ``id`` = original index)."""
    items = [
        ContentItem(id=_part_id(index), type=ContentType.TEXT, value=part_text(part))
        for index, part in enumerate(parts)
        if is_text_part(part)
    ]
    return ContentView(items=tuple(items))


def apply_view(parts: list[Part], view: ContentView) -> list[Part]:
    """Write a mutated :class:`ContentView` back onto the original A2A parts.

    Item ids are :func:`_part_id` handles into the original parts list; each text item's
    value is written into its part via ``model_copy``. Non-text and unaddressed parts pass
    through untouched, so part count and order are preserved (the view already guarantees
    item cardinality).
    """
    new_values = {item.id: item.value for item in view.items}
    return [
        copy_text_part(part, new_values[_part_id(index)])
        if is_text_part(part) and _part_id(index) in new_values
        else part
        for index, part in enumerate(parts)
    ]


async def _maybe_await[T](result: T | Awaitable[T]) -> T:
    """Await the result if it's awaitable, otherwise return it as-is."""
    if inspect.isawaitable(result):
        return await result
    return result


async def resolve_hook(on_fail: Any, failures: list, *args: Any) -> Any:
    """Resolve an ``on_fail`` hook value.

    If ``on_fail`` is callable, call it with ``(failures, *args)`` and return
    the result (awaiting if necessary). Otherwise return ``on_fail`` as-is.
    """
    if callable(on_fail):
        return await _maybe_await(on_fail(failures, *args))
    return on_fail
