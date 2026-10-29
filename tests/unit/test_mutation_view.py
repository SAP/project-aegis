from __future__ import annotations

import pytest

from _a2a_compat import data_part, get_text, is_data_part, is_text_part, text_part
from aegis.a2a._utils import apply_view, build_view
from aegis.validators.base import (
    ContentItem,
    ContentType,
    ContentView,
    ItemEdit,
    MutationError,
)


def _view(*values: str) -> ContentView:
    return ContentView(
        items=tuple(ContentItem(id=f"p{i}", type=ContentType.TEXT, value=v) for i, v in enumerate(values))
    )


def _text(t: str):
    return text_part(t)


def _data():
    return data_part()


def test_content_view_render_joins_values_with_newlines():
    view = ContentView(
        items=(
            ContentItem(id="p0", type=ContentType.TEXT, value="Cats are ugly. "),
            ContentItem(id="p1", type=ContentType.TEXT, value="I hate them."),
        )
    )
    assert view.render() == "Cats are ugly. \nI hate them."


def test_content_item_and_edit_are_frozen():
    item = ContentItem(id="p0", type=ContentType.TEXT, value="x")
    edit = ItemEdit(id="p0", value="y")
    with pytest.raises(AttributeError):
        item.value = "z"  # frozen dataclass
    with pytest.raises(AttributeError):
        edit.value = "z"


def test_mutation_error_is_exception():
    assert issubclass(MutationError, Exception)


# --- engine: rebuild operates on a ContentView -----------------------------


def test_rebuild_preserves_cardinality_and_order():
    out = _view("a", "b").rebuild([ItemEdit("p0", "A"), ItemEdit("p1", "B")])
    assert [i.value for i in out.items] == ["A", "B"]


def test_rebuild_passes_through_unaddressed_items():
    out = _view("a", "b").rebuild([ItemEdit("p1", "B")])
    assert [i.value for i in out.items] == ["a", "B"]


def test_rebuild_rejects_unknown_id():
    with pytest.raises(MutationError):
        _view("a").rebuild([ItemEdit("p9", "x")])


def test_rebuild_allows_empty_value():
    out = _view("a").rebuild([ItemEdit("p0", "")])
    assert out.items[0].value == ""


# --- a2a adapter: build_view / apply_view round-trip -----------------------


def test_build_view_exposes_only_text_parts_with_index_ids():
    view = build_view([_text("a"), _data(), _text("b")])
    assert [(i.id, i.value) for i in view.items] == [("p0", "a"), ("p2", "b")]
    assert all(i.type == "text" for i in view.items)


def test_apply_view_writes_back_preserving_cardinality_and_order():
    parts = [_text("a"), _data(), _text("b")]
    mutated = build_view(parts).rebuild([ItemEdit("p0", "A"), ItemEdit("p2", "B")])
    out = apply_view(parts, mutated)
    assert len(out) == 3
    assert is_text_part(out[0]) and get_text(out[0]) == "A"
    assert is_data_part(out[1])  # non-text untouched, in place
    assert is_text_part(out[2]) and get_text(out[2]) == "B"


def test_public_exports():
    import aegis

    for name in ("ItemEdit", "ContentItem", "ContentType", "ContentView", "MutationError"):
        assert hasattr(aegis, name), f"aegis.{name} not exported"
    assert not hasattr(aegis, "MutationResult"), "MutationResult should be removed"
