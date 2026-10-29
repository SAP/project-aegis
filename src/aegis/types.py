from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aegis.validators.base import FailedResult, Validator


class OnFailAction(enum.Enum):
    """Built-in failure-handling actions."""

    LOG = "log"
    EXCEPTION = "exception"
    NOOP = "noop"


OnFailHandler = Callable[..., None]
"""Custom on_fail callback signature: ``(result: FailedResult, **kwargs) -> None``."""

OnFailArg = OnFailAction | OnFailHandler
"""Accepted type for the ``on_fail`` parameter on :meth:`Guard.use`."""


class ValidationError(Exception):
    """Raised when a validator fails and ``on_fail=OnFailAction.EXCEPTION``."""

    def __init__(self, message: str, *, result: FailedResult | None = None) -> None:
        super().__init__(message)
        self.result = result


@dataclass
class ValidatorConfig:
    """Internal pairing of a validator instance with its on_fail setting."""

    validator: Validator
    on_fail: OnFailArg = OnFailAction.LOG
