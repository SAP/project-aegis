"""The validator contract — the protocols and base classes every validator implements.

This package defines the interface, not the validators themselves:

- ``Validator`` and ``MutationValidator`` are the structural protocols a validator
  must satisfy: detect-only and detect-and-fix, respectively.
- ``BaseValidator`` and ``BaseMutationValidator`` are optional base classes that
  supply the boilerplate (such as ``name``), so a subclass implements only its
  own ``validate`` (and, for mutation, ``mutate``) logic.
- The types a validator works with are re-exported here too, so
  ``from aegis.validators import ...`` is the import path a validator uses:
  ``PassedResult`` / ``FailedResult`` (verdicts), ``ContentView`` / ``ContentItem`` /
  ``ContentType`` and ``ItemEdit`` (what a validator inspects and edits),
  ``MutationError`` (a failed edit), and ``GuardResult`` / ``MutationGuardResult``
  (per-run results).

Aegis ships its built-in validators in the ``aegis.validators.detection`` and
``aegis.validators.mutation`` subpackages; this package provides the contract they — and
your own validators — implement. The ``Guard`` / ``MutationGuard`` engines run whatever
validators you register, built-in or your own. Implement your own by following the
protocol; see ``docs/custom-validators.md`` to get started.
"""

from aegis.validators.base import (
    BaseMutationValidator,
    BaseValidator,
    ContentItem,
    ContentType,
    ContentView,
    FailedResult,
    GuardResult,
    ItemEdit,
    MutationError,
    MutationGuardResult,
    MutationValidator,
    PassedResult,
    ValidationResult,
    Validator,
)

__all__ = [
    "BaseMutationValidator",
    "BaseValidator",
    "ContentItem",
    "ContentType",
    "ContentView",
    "FailedResult",
    "GuardResult",
    "ItemEdit",
    "MutationError",
    "MutationGuardResult",
    "MutationValidator",
    "PassedResult",
    "ValidationResult",
    "Validator",
]
