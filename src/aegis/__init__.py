"""aegis — Standalone guardrail library for agent executors."""

from aegis._normalize import NormalizationConfig
from aegis.guard import Guard
from aegis.metadata import (
    CONVERSATION_HISTORY,
    ConversationMessage,
    GuardMetadata,
)
from aegis.mutation_guard import MutationGuard
from aegis.types import (
    OnFailAction,
    OnFailArg,
    OnFailHandler,
    ValidationError,
)
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
    "CONVERSATION_HISTORY",
    "BaseMutationValidator",
    "BaseValidator",
    "ContentItem",
    "ContentType",
    "ContentView",
    "ConversationMessage",
    "FailedResult",
    "Guard",
    "GuardMetadata",
    "GuardResult",
    "ItemEdit",
    "MutationError",
    "MutationGuard",
    "MutationGuardResult",
    "MutationValidator",
    "NormalizationConfig",
    "OnFailAction",
    "OnFailArg",
    "OnFailHandler",
    "PassedResult",
    "ValidationError",
    "ValidationResult",
    "Validator",
]
