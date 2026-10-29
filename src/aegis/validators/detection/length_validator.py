"""LengthValidator — character-count bounds for text input.

A deterministic, text-only, **detect-only** validator. It runs under ``Guard`` and rejects a
message whose rendered character length falls outside a configured ``[min_length, max_length]``
range. Use it as a cheap check that enforces a floor (reject near-empty input) and/or a ceiling
(reject oversized input).

Length here means Unicode code points (``len(str)``), not bytes or grapheme clusters. For a byte
ceiling (counting UTF-8 bytes rather than characters), use :class:`SizeValidator` instead; the two
measure different things and are complementary.
"""

from __future__ import annotations

from typing import Final

from aegis.metadata import GuardMetadata
from aegis.validators.base import (
    BaseValidator,
    ContentView,
    FailedResult,
    PassedResult,
    ValidationResult,
)


class LengthValidator(BaseValidator):
    """Rejects text whose character length is below ``min_length`` or above ``max_length``.

    Measures ``view.render()`` — the whole message as one flattened string — so the bound applies
    across all parts, not per part. Empty input has length 0: it passes only when ``min_length`` is
    0 (the default).

    Example:
        >>> from aegis import Guard
        >>> guard = Guard("input").use(LengthValidator(max_length=100))
        >>> result = await guard.validate("x" * 200)
        >>> result.failures[0].error_code
        'LENGTH_EXCEEDED'
    """

    # Error codes are a released contract: defined once here as the single source of truth shared
    # by validate(), the tests, and any docs, and never renamed or repurposed.
    LENGTH_TOO_SHORT: Final = "LENGTH_TOO_SHORT"
    LENGTH_EXCEEDED: Final = "LENGTH_EXCEEDED"

    def __init__(self, max_length: int | None = None, min_length: int = 0) -> None:
        """Configure the character-length bounds.

        Args:
            max_length: Maximum allowed length, inclusive. ``None`` (the default) means no ceiling.
                When set, must be a non-negative ``int`` not less than ``min_length``.
            min_length: Minimum required length, inclusive. Defaults to 0 (no floor). Must be a
                non-negative ``int``.

        Raises:
            TypeError: If ``min_length`` is not an ``int``, or if ``max_length`` is neither ``int``
                nor ``None``. The annotations are not enforced at runtime, and a non-int such as
                ``float("nan")`` would slip past the range checks (every comparison with ``nan`` is
                ``False``) and then silently accept all content.
            ValueError: If either bound is negative, or if ``max_length < min_length`` — an empty
                range would reject every input, so it is rejected at construction rather than
                silently failing closed on every call.
        """
        # Reject non-int bounds before comparing: the type annotations are not enforced at
        # runtime, and a float("nan") passes every "< 0" / "<" check (all comparisons with nan
        # are False), which would then make `length > max_length` always False too — silently
        # accepting all content. bool is excluded too: although True/False == 1/0 compare
        # correctly, they leak into the error message ("... > True"), so reject them as a type.
        # max_length also accepts None, meaning "no ceiling" — the length > max check is skipped.
        if not isinstance(min_length, int) or isinstance(min_length, bool):
            raise TypeError(f"min_length must be an int, got {type(min_length).__name__}.")
        if max_length is not None and (not isinstance(max_length, int) or isinstance(max_length, bool)):
            raise TypeError(f"max_length must be an int or None, got {type(max_length).__name__}.")
        if min_length < 0:
            raise ValueError(f"min_length must be non-negative, got {min_length}.")
        if max_length is not None and max_length < 0:
            raise ValueError(f"max_length must be non-negative, got {max_length}.")
        if max_length is not None and max_length < min_length:
            raise ValueError(f"max_length ({max_length}) must be >= min_length ({min_length}).")

        self.min_length = min_length
        self.max_length = max_length

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Report whether the rendered content length is within bounds.

        Args:
            view: The content under inspection. Never ``None``; may be empty.
            metadata: Surrounding agent context. Unused — length is self-contained.

        Returns:
            ``PassedResult`` when ``min_length <= length <= max_length``, otherwise a
            ``FailedResult`` carrying ``LENGTH_TOO_SHORT`` or ``LENGTH_EXCEEDED`` and the measured
            length plus the breached bound in ``details``.
        """
        length = len(view.render())

        if length < self.min_length:
            return FailedResult(
                error_message=f"Text too short: {length} < {self.min_length}",
                error_code=self.LENGTH_TOO_SHORT,
                details={"length": length, "min_length": self.min_length},
            )
        if self.max_length is not None and length > self.max_length:
            return FailedResult(
                error_message=f"Text too long: {length} > {self.max_length}",
                error_code=self.LENGTH_EXCEEDED,
                details={"length": length, "max_length": self.max_length},
            )
        return PassedResult(details={"length": length})
