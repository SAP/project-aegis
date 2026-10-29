"""Mutation validator template — a detect-and-fix validator that runs under ``MutationGuard``.

Copy this into your own project as a single module (e.g. ``your_package/validators/<name>.py``)
or, when the validator needs helpers or pattern tables, into a package folder with the public
class in a ``_``-private module re-exported from the folder's ``__init__.py``. Register it where
your app builds its guard, at runtime: ``MutationGuard("input").use(YourValidator(...))``.

This template is written against the current contract in the installed ``aegis`` package:

- ``validate(view, metadata=None)`` returns ``PassedResult`` or ``FailedResult``.
- ``mutate(view, metadata=None, validation_failure=None)`` runs only after a ``FailedResult`` and
  returns ``list[ItemEdit]`` — one whole-part replacement per part it changes, addressed by
  ``ContentItem.id``. The engine rebuilds over the original parts, so part count and order are
  preserved; an id that matches no part raises ``MutationError``.

A mutation validator must repair *safely*: the repair is a **no-op on already-clean input**
(idempotent) and its output **passes re-validation** by this same validator. Assert both in tests.

Replace ``UppercaseNormalizer`` with your validator and delete what you do not need.
"""

from __future__ import annotations

from typing import Final

from aegis import BaseMutationValidator, FailedResult, ItemEdit, PassedResult
from aegis.metadata import GuardMetadata
from aegis.validators import ContentView, ValidationResult


class UppercaseNormalizer(BaseMutationValidator):
    """Detects all-uppercase text parts and normalizes each to sentence case.

    A detect-and-fix validator: ``validate`` flags shouting text, and ``mutate`` rewrites it in
    place. It runs under ``MutationGuard``. Because both methods are view-based, the same class
    also runs detect-only under ``Guard`` (``mutate`` is simply never called).

    The repair is safe by construction: it only lowercases text that is entirely uppercase, so
    running it on already-normal text changes nothing (idempotent), and the rewritten text no
    longer trips ``validate`` (passes re-validation).

    Attributes:
        min_length: All-caps runs longer than this are treated as shouting. At or below it, short
            tokens like acronyms are left alone — the dial that trades recall for fewer false
            positives.
    """

    # Define each failure code once — the single source of truth for validate(), mutate(), the
    # tests, and any docs.
    ALL_CAPS: Final = "ALL_CAPS"

    def __init__(self, min_length: int = 4) -> None:
        """Store the shouting threshold.

        Args:
            min_length: All-caps runs longer than this are treated as shouting.
        """
        self.min_length = min_length

    def _is_shouting(self, text: str) -> bool:
        """Return True when ``text`` is all uppercase and long enough to count as shouting."""
        return text.isupper() and len(text) > self.min_length

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Report whether any part is shouting.

        Args:
            view: The message under inspection; iterate ``view.items`` for per-part checks. Never
                ``None``, but may be empty.
            metadata: Surrounding agent context. Optional and unused here.

        Returns:
            ``FailedResult`` with ``error_code == ALL_CAPS`` when at least one part shouts,
            otherwise ``PassedResult``.
        """
        shouting = [p for p in view.items if self._is_shouting(p.value)]
        if shouting:
            return FailedResult(
                error_message="One or more parts are all uppercase.",
                error_code=self.ALL_CAPS,
                # a safe summary — the count, never the raw text
                details={"shouting_parts": len(shouting)},
            )
        return PassedResult()

    async def mutate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
        validation_failure: FailedResult | None = None,
    ) -> list[ItemEdit]:
        """Rewrite each shouting part to sentence case, leaving other parts untouched.

        Called only after ``validate`` returned a ``FailedResult``. Returns one ``ItemEdit`` per
        part actually changed; unaddressed parts pass through unchanged, so part count and order
        hold by construction.

        Args:
            view: The message to repair; address edits by ``ContentItem.id``.
            metadata: Surrounding agent context. Optional and unused here.
            validation_failure: The ``FailedResult`` from ``validate`` that triggered this call.

        Returns:
            A list of ``ItemEdit`` (id → replacement value), one per rewritten part.
        """
        # Emit an edit only for parts we actually change — a minimal diff makes the intent clear.
        return [ItemEdit(p.id, p.value.capitalize()) for p in view.items if self._is_shouting(p.value)]
