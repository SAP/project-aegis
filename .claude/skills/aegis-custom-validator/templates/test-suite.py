"""Test-suite template — the matrix a validator's tests must cover.

Copy this next to your validator, wherever your project keeps its tests (e.g.
``tests/validators/test_<name>.py``). Drive the validator through a real ``Guard`` /
``MutationGuard``. aegis's own suite runs with ``asyncio_mode = "auto"`` (pytest-asyncio), so
``async def test_...`` needs no decorator — match your project's async-test setup.

Build the concrete cases from your spec's edge-case and threat sections. The stubs below name the
categories every validator suite should cover; fill in inputs and assertions, and delete the
categories that do not apply.

Assert the *property*, not an incidental byte: when output has an incidental artifact, assert the
behavioral or security property that must hold, not one exact string.
"""

from __future__ import annotations

# Import your validator from wherever it lives in your project.
from your_package.validators.length_validator import LengthValidator

from aegis import Guard

# For a mutation validator, also import:
#   from aegis import ContentItem, ContentType, ContentView, MutationGuard


# --- Detection: happy path ---------------------------------------------------


async def test_clean_value_passes():
    guard = Guard("test").use(LengthValidator(max_length=5))
    result = await guard.validate("ok")
    assert not result.has_failures


# --- Detection: the violation fails with the EXPECTED error_code -------------


async def test_violation_fails_with_expected_code():
    guard = Guard("test").use(LengthValidator(max_length=5))
    result = await guard.validate("way too long")
    assert result.has_failures
    assert result.failures[0].error_code == LengthValidator.TOO_LONG


# --- Detection: boundaries ---------------------------------------------------


async def test_boundaries():
    guard = Guard("test").use(LengthValidator(max_length=5))
    # Exactly at the limit passes; one over fails. Cover empty, whitespace-only, and very large
    # input for your own validator here.
    assert not (await guard.validate("12345")).has_failures
    assert (await guard.validate("123456")).has_failures


# --- Detection: malformed / empty input --------------------------------------


async def test_empty_and_malformed_input():
    guard = Guard("test").use(LengthValidator(max_length=5))
    # A validator treats hostile input as the normal case — empty and malformed input must not
    # raise, only pass or fail deterministically.
    assert not (await guard.validate("")).has_failures


# --- Detection: adversarial cases from the threat model ----------------------


async def test_adversarial_inputs():
    # Encode the bypass attempts your spec's threat model lists (encoding tricks, homoglyphs,
    # casing, whitespace, chunking). Each must resolve to the correct verdict.
    #
    # Fail loudly until this is filled in: a bare `...` body would pass silently and falsely
    # signal that the threat-model cases are covered. Replace this line with real cases, or
    # delete the test if the threat model has none.
    raise NotImplementedError("Add the adversarial cases from your spec's threat model, or delete this test.")


# --- Mutation: repair correctness (only for detect-and-fix validators) -------
#
# Uncomment and adapt for a MutationValidator. Two properties every safe repair must hold:
#
# async def test_repair_is_noop_on_clean_input():
#     guard = MutationGuard("test").use(MyMutator())
#     result = await guard.validate("already clean")
#     assert not result.was_mutated               # idempotent on acceptable input
#
# async def test_repaired_output_passes_revalidation():
#     guard = MutationGuard("test").use(MyMutator())
#     result = await guard.validate("NEEDS FIXING")
#     assert result.was_mutated
#     # Feeding the repaired output back in must not trigger another mutation — fixed point.
#     again = await guard.validate(result.output)
#     assert not again.was_mutated
#
# async def test_preserves_item_count_and_order():
#     view = ContentView(items=(
#         ContentItem(id="p0", type=ContentType.TEXT, value="NEEDS FIXING"),
#         ContentItem(id="p1", type=ContentType.TEXT, value="already fine"),
#     ))
#     guard = MutationGuard("test").use(MyMutator())
#     result = await guard.validate(view)
#     assert [i.id for i in result.output.items] == ["p0", "p1"]   # count + order preserved
#     assert result.output.items[1].value == "already fine"        # untouched, in place
