"""The validator contract every validator is built against.

This module is the source of truth for the validator interface. It defines:

- The two protocols a validator satisfies: :class:`Validator` (detect-only) and
  :class:`MutationValidator` (detect-and-fix).
- Optional base classes that supply the ``name`` boilerplate:
  :class:`BaseValidator` and :class:`BaseMutationValidator`.
- The verdict types a validator returns — :class:`PassedResult` and
  :class:`FailedResult` — and the per-run results the engines collect,
  :class:`GuardResult` and :class:`MutationGuardResult`.
- The content types a validator inspects and edits: :class:`ContentView`,
  :class:`ContentItem`, :class:`ContentType`, and :class:`ItemEdit`, plus the
  :class:`MutationError` raised when an edit cannot be applied.
"""

from __future__ import annotations

import abc
import enum
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from aegis.metadata import GuardMetadata


@dataclass(frozen=True)
class PassedResult:
    """The outcome of a validator run that accepted its input.

    Frozen on purpose: a result must never change after construction, which also
    keeps it consistent with the other frozen result types (FailedResult,
    ContentItem, ContentView).

    Attributes:
        validator_name: Name of the validator that produced this result. A
            validator leaves this empty; the engine fills it in with the
            validator's ``name`` so a caller can tell results apart.
        details: Optional structured data the validator chose to attach (for
            example, a count of what it checked). Free-form and safe to ignore —
            the engine does not read it.

    Example:
        >>> PassedResult().validator_name  # engine fills this in later
        ''
    """

    validator_name: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FailedResult:
    """The outcome of a validator run that rejected its input.

    Frozen for the same reasons as PassedResult: the ``error_code`` and
    ``error_message`` a caller acts on must stay exactly as the validator
    produced them, and it keeps the result types consistent.

    Attributes:
        error_message: Human-readable explanation of what failed, safe to show
            to the caller.
        error_code: Machine-readable reason for the rejection, for callers that
            branch on the failure kind rather than parse the message.
        validator_name: Name of the validator that produced this result. A
            validator leaves this empty; the engine fills it in with the
            validator's ``name``.
        details: Optional structured data the validator chose to attach. Free-form
            and safe to ignore — the engine does not read it.

    Example:
        >>> result = FailedResult(error_message="Email found in input.", error_code="PII_DETECTED")
        >>> result.error_code
        'PII_DETECTED'
    """

    error_message: str = ""
    error_code: str = ""
    validator_name: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


# A validator's verdict: PassedResult when it accepts the input, FailedResult when
# it rejects it. Both `validate` methods return this union.
ValidationResult = PassedResult | FailedResult


# ---------------------------------------------------------------------------
# Validation types
# ---------------------------------------------------------------------------


@runtime_checkable
class Validator(Protocol):
    """The detect-only validator contract: a ``name`` and an ``async validate``.

    This is a structural ``Protocol``, so any object with the right shape counts
    as a ``Validator`` — you do not have to inherit from anything. Subclass
    :class:`BaseValidator` if you would rather have the ``name`` boilerplate
    supplied for you.
    """

    @property
    def name(self) -> str: ...

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Inspect the content and report a verdict.

        Args:
            view: The content under inspection as an ordered :class:`ContentView`.
                Call ``view.render()`` for the flattened text, or iterate
                ``view.items`` (each a :class:`ContentItem` with ``id``/``type``/
                ``value``) for per-item / cross-item inspection. Never ``None`` —
                it may be empty.
            metadata: Surrounding agent context. Optional.

        Returns:
            ``PassedResult`` or ``FailedResult``.
        """
        ...


class BaseValidator(abc.ABC):
    """Optional base class for detect-only validators.

    Implements the :class:`Validator` protocol's ``name`` (defaulting to the
    class name), so a subclass only has to implement ``validate``.
    """

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abc.abstractmethod
    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Inspect the content and report a verdict.

        Args:
            view: The content under inspection as an ordered :class:`ContentView`.
                Call ``view.render()`` for the flattened text, or iterate
                ``view.items`` for per-item / cross-item inspection. Never ``None`` —
                it may be empty.
            metadata: Surrounding agent context. Optional.

        Returns:
            ``PassedResult`` or ``FailedResult``.
        """
        ...


@dataclass
class GuardResult:
    """The combined outcome of a :meth:`Guard.validate` run.

    ``Guard`` runs its validators concurrently and collects every verdict here.
    Use ``has_failures`` to gate on the run as a whole, and ``failures`` to
    report the specific rejections.

    Attributes:
        results: One :class:`PassedResult` or :class:`FailedResult` per registered
            validator, in registration order.
    """

    results: list[ValidationResult] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        """Return True if any validator rejected the input."""
        return any(isinstance(r, FailedResult) for r in self.results)

    @property
    def failures(self) -> list[FailedResult]:
        """Return just the failing results, preserving registration order."""
        return [r for r in self.results if isinstance(r, FailedResult)]


# ---------------------------------------------------------------------------
# Mutation types
# ---------------------------------------------------------------------------


@runtime_checkable
class MutationValidator(Protocol):
    """The detect-and-fix validator contract: a ``name``, an ``async validate``, and an ``async mutate``.

    Both ``Validator`` and ``MutationValidator`` are view-based, so a
    ``MutationValidator`` is a superset of ``Validator`` — the same class can run
    detect-only under ``Guard`` (its ``validate`` runs; ``mutate`` is not called).
    """

    @property
    def name(self) -> str: ...

    async def validate(self, view: ContentView, metadata: GuardMetadata | None = None) -> ValidationResult:
        """Inspect the message and report whether it needs mutation.

        Args:
            view: The message under inspection as an ordered :class:`ContentView`
                of text parts. Iterate ``view.items`` (each a :class:`ContentItem`
                with ``id``/``type``/``value``) or use ``view.render()`` for
                cross-part context. Never ``None`` — it may be empty.
            metadata: Surrounding agent context. Optional.

        Returns:
            ``PassedResult`` when nothing needs fixing, or ``FailedResult`` to
            trigger :meth:`mutate`.
        """
        ...

    async def mutate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
        validation_failure: FailedResult | None = None,
    ) -> list[ItemEdit]:
        """Return whole-part edits that remediate the detected issue.

        Called only when :meth:`validate` returned a ``FailedResult``.

        Args:
            view: The message as an ordered :class:`ContentView`. Address edits by
                ``ContentItem.id``; ``view.render()`` gives cross-part context.
            metadata: Surrounding agent context. Optional.
            validation_failure: The :class:`FailedResult` that triggered this
                mutation, so the mutator can act on the specific failure rather
                than re-running detection.

        Returns:
            One :class:`ItemEdit` (``id`` → replacement ``value``) per part to
            change; unaddressed parts are left untouched. The engine rebuilds over
            the original parts, so part count and order are preserved. An ``id`` that
            matches no text part raises :class:`MutationError`.
        """
        ...


class BaseMutationValidator(abc.ABC):
    """Optional base class for detect-and-fix (mutation) validators.

    Implements the :class:`MutationValidator` protocol's ``name`` (defaulting to
    the class name), so a subclass only has to implement ``validate`` and
    ``mutate``.
    """

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abc.abstractmethod
    async def validate(self, view: ContentView, metadata: GuardMetadata | None = None) -> ValidationResult:
        """Inspect the message and report whether it needs mutation.

        Args:
            view: The message under inspection as an ordered :class:`ContentView`
                of text parts. Iterate ``view.items`` or use ``view.render()`` for
                cross-part context. Never ``None`` — it may be empty.
            metadata: Surrounding agent context. Optional.

        Returns:
            ``PassedResult`` when nothing needs fixing, or ``FailedResult`` to
            trigger :meth:`mutate`.
        """
        ...

    @abc.abstractmethod
    async def mutate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
        validation_failure: FailedResult | None = None,
    ) -> list[ItemEdit]:
        """Return whole-part edits that remediate the detected issue.

        Called only when :meth:`validate` returned a ``FailedResult``.

        Args:
            view: The message as an ordered :class:`ContentView`. Address edits by
                ``ContentItem.id``; ``view.render()`` gives cross-part context.
            metadata: Surrounding agent context. Optional.
            validation_failure: The :class:`FailedResult` that triggered this
                mutation, so the mutator can act on the specific failure.

        Returns:
            One :class:`ItemEdit` (``id`` → replacement ``value``) per part to
            change; unaddressed parts are left untouched. The engine rebuilds over
            the original parts, so part count and order are preserved. An ``id`` that
            matches no text part raises :class:`MutationError`.
        """
        ...


@dataclass
class MutationGuardResult:
    """The combined outcome of a :meth:`MutationGuard.validate` run.

    Use ``has_failures`` to gate on the run as a whole, and ``failures`` to
    report the specific rejections.

    Attributes:
        results: Validation results from each registered validator, in registration order.
        was_mutated: True when the mutated ``output`` differs from the input content.
            This tracks the content actually changing, not merely that a validator
            emitted edits — a no-op edit (a replacement whose value equals the
            original) leaves ``was_mutated`` False. Inspect ``output`` for the
            mutated content.
        output: The final content after all mutations, mirroring the input type — a
            ``str`` when ``validate`` was called with a ``str``, otherwise a
            :class:`ContentView` (same item count and order as the input). For the view
            case, call ``output.render()`` for flattened text, or map it back onto your
            transport's parts in your adapter.
    """

    results: list[ValidationResult] = field(default_factory=list)
    was_mutated: bool = False
    output: str | ContentView | None = None

    @property
    def has_failures(self) -> bool:
        """Return True if any validator rejected the input."""
        return any(isinstance(r, FailedResult) for r in self.results)

    @property
    def failures(self) -> list[FailedResult]:
        """Return just the failing results, preserving registration order."""
        return [r for r in self.results if isinstance(r, FailedResult)]


class MutationError(Exception):
    """Raised when edits cannot be applied to the view.

    The engine guarantees the mutated output has the same parts, in the same
    order, as the input. An :class:`ItemEdit` whose ``id`` matches no part would
    break that guarantee, so :meth:`ContentView.rebuild` raises this instead of
    silently dropping or misplacing the edit.
    """


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class ContentType(enum.StrEnum):
    """Modality of a :class:`ContentItem`. Text-only today; more kinds later."""

    TEXT = "text"


@dataclass(frozen=True)
class ContentItem:
    """One typed piece of content as seen by a validator.

    Attributes:
        id: Engine-assigned handle for this part, used to address edits back at
            it in :class:`ItemEdit`. Stable for one guard run — the same id
            identifies this part across ``validate``, ``mutate``, and any later
            validators in the pipeline. It is not a durable identifier: a new run
            reassigns ids, so do not store an id and reuse it across runs.
        type: The content modality — :attr:`ContentType.TEXT` today, with other
            kinds (image, audio, ...) planned.
        value: The content payload (text today).
    """

    id: str
    type: ContentType
    value: str


@dataclass(frozen=True)
class ContentView:
    """The whole message a validator inspects: its parts, in order.

    A validator reads the parts through ``items`` (each a :class:`ContentItem`)
    or as one flattened string through :meth:`render`. A mutation validator then
    returns :class:`ItemEdit` values addressed by ``ContentItem.id``, which
    :meth:`rebuild` applies to produce the mutated view.
    """

    items: tuple[ContentItem, ...]

    def render(self) -> str:
        """Return every part's value joined by newlines, in order.

        Use this when a validator reasons over the whole message at once rather
        than part by part — for example, matching a pattern that may span parts.

        Note: parts are joined with ``"\\n"`` regardless of how the transport
        actually separated them, so ``render()`` is for inspection, not for
        reconstructing the exact original wire format.
        """
        # TODO: revisit if a validator ever needs byte-exact reconstruction of
        # the original part separators.
        return "\n".join(p.value for p in self.items)

    @classmethod
    def from_text(cls, text: str) -> ContentView:
        """Build a view holding a single TEXT item from a plain string.

        Use this when a validator works on raw text and needs it in the standard
        ``ContentView`` shape the rest of the pipeline expects.
        """
        return cls(items=(ContentItem(id="p0", type=ContentType.TEXT, value=text),))

    def rebuild(self, edits: list[ItemEdit]) -> ContentView:
        """Apply ``edits`` to this view, returning a new view with identical item count and order.

        Each :class:`ItemEdit` replaces one item's ``value`` by ``id``. Items that are not
        addressed pass through unchanged, so cardinality and order hold by construction. An
        edit whose ``id`` matches no item raises :class:`MutationError`.
        """
        ids = {item.id for item in self.items}
        new_values: dict[str, str] = {}
        for edit in edits:
            if edit.id not in ids:
                raise MutationError(f"edit references unknown item: {edit.id}")
            new_values[edit.id] = edit.value  # last edit wins on a repeated id

        return ContentView(
            items=tuple(
                replace(item, value=new_values[item.id]) if item.id in new_values else item for item in self.items
            )
        )


@dataclass(frozen=True)
class ItemEdit:
    """A whole-item replacement: swap the item with this ``id`` for a new ``value``.

    This is the unit a mutation validator's ``mutate`` returns. The engine applies
    it through :meth:`ContentView.rebuild`, which addresses the target item by
    ``id`` and leaves every other item untouched.

    Attributes:
        id: The :attr:`ContentItem.id` of the item to replace.
        value: The full replacement text for that item.
    """

    id: str
    value: str
