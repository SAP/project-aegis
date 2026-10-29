from __future__ import annotations

from _a2a_compat import data_part, get_text, is_data_part, text_part
from aegis import (
    BaseMutationValidator,
    ContentView,
    FailedResult,
    ItemEdit,
    MutationGuard,
    PassedResult,
)
from aegis.a2a._utils import apply_view, build_view


def _text(t: str):
    return text_part(t)


def _data():
    return data_part()


class UpperCaseMutator(BaseMutationValidator):
    """Fails if any text part has lowercase; upper-cases each offending part."""

    async def validate(self, view, metadata=None):
        if any(p.value != p.value.upper() for p in view.items):
            return FailedResult(error_message="has lowercase", error_code="LOWERCASE")
        return PassedResult()

    async def mutate(self, view, metadata=None, validation_failure=None):
        return [ItemEdit(p.id, p.value.upper()) for p in view.items if p.value != p.value.upper()]


class ContextTagger(BaseMutationValidator):
    """Appends the full rendered context length to the FIRST text part only."""

    async def validate(self, view, metadata=None):
        return FailedResult(error_message="tag", error_code="TAG")

    async def mutate(self, view, metadata=None, validation_failure=None):
        n = len(view.render())
        first = view.items[0]
        return [ItemEdit(first.id, f"{first.value}[ctx={n}]")]


class SyncPassMutator(BaseMutationValidator):
    """Synchronous validate/mutate — exercises the sync/async dispatch."""

    def validate(self, view, metadata=None):  # note: not async
        return FailedResult(error_message="sync", error_code="SYNC")

    def mutate(self, view, metadata=None, validation_failure=None):
        return [ItemEdit(p.id, p.value + "!") for p in view.items]


async def test_single_text_item_mutates():
    guard = MutationGuard("g").use(UpperCaseMutator())
    result = await guard.validate("abc")
    assert result.was_mutated is True
    assert result.output == "ABC"  # string in → string out


async def test_multipart_preserves_cardinality_and_passes_through_images():
    parts = [_text("cat"), _data(), _text("dog")]
    guard = MutationGuard("g").use(UpperCaseMutator())
    result = await guard.validate(build_view(parts))
    # engine view: both text items upper-cased, order preserved
    assert isinstance(result.output, ContentView)  # view in → view out
    assert [i.value for i in result.output.items] == ["CAT", "DOG"]
    # a2a write-back preserves cardinality and passes non-text through in place
    out = apply_view(parts, result.output)
    assert len(out) == 3
    assert get_text(out[0]) == "CAT"
    assert is_data_part(out[1])
    assert get_text(out[2]) == "DOG"


async def test_passing_validator_is_a_noop():
    class Pass(BaseMutationValidator):
        async def validate(self, view, metadata=None):
            return PassedResult()

        async def mutate(self, view, metadata=None, validation_failure=None):
            raise AssertionError("mutate must not be called on pass")

    guard = MutationGuard("g").use(Pass())
    result = await guard.validate("abc")
    assert result.was_mutated is False
    assert result.output == "abc"


async def test_failing_validator_with_noop_mutate_reads_as_not_mutated():
    """was_mutated tracks actual content change, not whether edits were emitted."""

    class NoOpMutator(BaseMutationValidator):
        async def validate(self, view, metadata=None):
            return FailedResult(error_message="x", error_code="X")

        async def mutate(self, view, metadata=None, validation_failure=None):
            return [ItemEdit(p.id, p.value) for p in view.items]  # same value = no change

    result = await MutationGuard("g").use(NoOpMutator()).validate("abc")
    assert result.was_mutated is False
    assert result.output == "abc"


async def test_sequential_pipeline_second_sees_first_output():
    # ContextTagger sees UpperCaseMutator's output in its render()
    guard = MutationGuard("g").use(UpperCaseMutator()).use(ContextTagger())
    result = await guard.validate("ab")
    assert result.output == "AB[ctx=2]"  # tagged after upper-casing


async def test_sync_validator_supported():
    guard = MutationGuard("g").use(SyncPassMutator())
    result = await guard.validate("x")
    assert result.output == "x!"


async def test_use_rejects_non_mutation_validator():
    guard = MutationGuard("g")
    try:
        guard.use(object())  # type: ignore[arg-type]
    except TypeError:
        return
    raise AssertionError("use() should reject a non-MutationValidator")


async def test_empty_mutation_guard_raises():
    try:
        await MutationGuard("empty").validate("hi")
    except RuntimeError:
        return
    raise AssertionError("an empty MutationGuard should refuse to validate")


class CapturingMutator(BaseMutationValidator):
    """Always fails; records the validation_failure passed into mutate()."""

    def __init__(self) -> None:
        self.seen_failure: FailedResult | None = None

    async def validate(self, view, metadata=None):
        return FailedResult(error_message="boom", error_code="BOOM")

    async def mutate(self, view, metadata=None, validation_failure=None):
        self.seen_failure = validation_failure
        return [ItemEdit(p.id, p.value) for p in view.items]


def test_use_returns_self_for_chaining():
    guard = MutationGuard("g")
    assert guard.use(UpperCaseMutator()) is guard


def test_name_property():
    assert MutationGuard("out-sanitizer").name == "out-sanitizer"


async def test_validator_name_set_on_results():
    guard = MutationGuard("g").use(UpperCaseMutator())
    result = await guard.validate("abc")
    assert result.results[0].validator_name == "UpperCaseMutator"


async def test_validation_failure_forwarded_to_mutate():
    capt = CapturingMutator()
    await MutationGuard("g").use(capt).validate("x")
    assert isinstance(capt.seen_failure, FailedResult)
    assert capt.seen_failure.error_code == "BOOM"
