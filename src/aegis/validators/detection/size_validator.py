"""SizeValidator — UTF-8 byte-size ceiling for text input.

A deterministic, text-only, **detect-only** validator. It runs under ``Guard`` and rejects a
message whose rendered text exceeds a configured byte limit when encoded as UTF-8. Use it as a
cheap check to cap text-payload size at a trust boundary.

Size here means UTF-8 bytes, not characters: a multi-byte character (an emoji, a CJK ideograph)
costs more than one byte. For a character-count bound instead, use :class:`LengthValidator`; the
two measure different things and are complementary.
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


class SizeValidator(BaseValidator):
    """Rejects text that exceeds ``max_size_bytes`` when encoded as UTF-8.

    Measures the UTF-8 byte length of ``view.render()`` — the whole message as one flattened string
    — so the limit applies across all parts, not per part. The default limit is 5 MiB
    (5,242,880 bytes) of text content. This counts only the rendered text bytes, not any request
    framing or other payload fields, so it is not a drop-in for a servlet request-size limit.

    The byte count is total and never raises: text carrying a lone/unpaired surrogate (which a plain
    UTF-8 encode would reject) is measured via ``errors="surrogatepass"`` rather than crashing the
    guard. Whether the text is well-formed Unicode is a separate validator's concern.

    Example:
        >>> from aegis import Guard
        >>> guard = Guard("input").use(SizeValidator(max_size_bytes=1024))
        >>> result = await guard.validate("x" * 2048)
        >>> result.failures[0].error_code
        'SIZE_EXCEEDED'
    """

    # Error code is a released contract: defined once here as the single source of truth shared by
    # validate(), the tests, and any docs, and never renamed or repurposed.
    SIZE_EXCEEDED: Final = "SIZE_EXCEEDED"

    DEFAULT_MAX_SIZE_BYTES: Final = 5_242_880  # 5 MiB

    def __init__(self, max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES) -> None:
        """Configure the UTF-8 byte-size ceiling.

        Args:
            max_size_bytes: Maximum allowed size in UTF-8 bytes, inclusive. Must be non-negative;
                0 means "reject any non-empty input".

        Raises:
            TypeError: If ``max_size_bytes`` is not an ``int``. The annotation is not enforced at
                runtime, and a non-int such as ``float("nan")`` would slip past the range check
                (every comparison with ``nan`` is ``False``) and then silently accept all content.
            ValueError: If ``max_size_bytes`` is negative — a negative ceiling would reject every
                input, including empty, so it is rejected at construction.
        """
        # Reject a non-int ceiling before comparing: a float("nan") passes "< 0" (all nan
        # comparisons are False), which would make `size_bytes > max_size_bytes` always False too,
        # silently accepting all content. bool is excluded too: although True/False == 1/0 compare
        # correctly, they leak into the error message ("... > False bytes"), so reject them.
        if not isinstance(max_size_bytes, int) or isinstance(max_size_bytes, bool):
            raise TypeError(f"max_size_bytes must be an int, got {type(max_size_bytes).__name__}.")
        if max_size_bytes < 0:
            raise ValueError(f"max_size_bytes must be non-negative, got {max_size_bytes}.")
        self.max_size_bytes = max_size_bytes

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Report whether the rendered content's UTF-8 byte size is within the limit.

        Args:
            view: The content under inspection. Never ``None``; may be empty.
            metadata: Surrounding agent context. Unused — size is self-contained.

        Returns:
            ``PassedResult`` when ``size_bytes <= max_size_bytes``, otherwise a ``FailedResult``
            carrying ``SIZE_EXCEEDED`` and the measured and allowed sizes in ``details``.
        """
        # errors="surrogatepass" keeps the size check total: a str carrying a lone/unpaired
        # surrogate (e.g. produced upstream by errors="surrogateescape" decoding, or by splitting a
        # surrogate pair mid-buffer) would make a plain encode("utf-8") raise UnicodeEncodeError and
        # crash the guard. surrogatepass encodes such a code point as its 3 UTF-8 bytes instead, so
        # malformed text is measured, not thrown on. Whether the text is well-formed is a separate
        # validator's concern — the size gate only counts bytes.
        size_bytes = len(view.render().encode("utf-8", "surrogatepass"))

        if size_bytes > self.max_size_bytes:
            return FailedResult(
                error_message=f"Text size too large: {size_bytes} bytes > {self.max_size_bytes} bytes",
                error_code=self.SIZE_EXCEEDED,
                details={"size_bytes": size_bytes, "max_size_bytes": self.max_size_bytes},
            )
        return PassedResult(details={"size_bytes": size_bytes})
