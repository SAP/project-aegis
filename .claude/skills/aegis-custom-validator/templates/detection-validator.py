"""Detection validator template — a detect-only validator that runs under ``Guard``.

Copy this into your own project as a single module (e.g. ``your_package/validators/<name>.py``)
or, when the validator needs helpers or pattern tables, into a package folder with the public
class in a ``_``-private module re-exported from the folder's ``__init__.py``. Register it where
your app builds its guard, at runtime: ``Guard("input").use(YourValidator(...))``.

This template is written against the current contract in the installed ``aegis`` package:
``validate(view, metadata=None)`` returns ``PassedResult`` or ``FailedResult``, and reads the
content through a ``ContentView``. Replace ``LengthValidator`` with your validator and delete the
parts you do not need.

The docstrings follow the writing style in ``references/writing-style.md``: state what and *why*,
imperative mood for functions, describe for the class.
"""

from __future__ import annotations

from typing import Final

from aegis import BaseValidator, FailedResult, PassedResult
from aegis.metadata import GuardMetadata
from aegis.validators import ContentView, ValidationResult


class LengthValidator(BaseValidator):
    """Rejects input longer than a configured maximum number of characters.

    A detect-only validator: it reports over-length input but never changes it, so it runs under
    ``Guard``. It does exactly one thing — length — and leaves every other concern to other
    validators.

    Attributes:
        max_length: The largest input length, in characters, that passes.

    Example:
        >>> from aegis import Guard
        >>> guard = Guard("input").use(LengthValidator(max_length=500))
        >>> result = await guard.validate("...")
        >>> result.has_failures
        False
    """

    # Define each failure code once, as the single source of truth shared by validate(), the
    # tests, and the doc page. A released code is a contract: add new ones, never rename.
    TOO_LONG: Final = "TOO_LONG"

    def __init__(self, max_length: int) -> None:
        """Store the maximum allowed length.

        Args:
            max_length: The largest input length, in characters, that passes.
        """
        self.max_length = max_length

    async def validate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
    ) -> ValidationResult:
        """Report whether the flattened input stays within ``max_length``.

        Args:
            view: The content under inspection. ``view.render()`` gives the flattened text; it is
                never ``None`` but may be empty.
            metadata: Surrounding agent context. Optional and unused here.

        Returns:
            ``PassedResult`` when the input fits, otherwise ``FailedResult`` with
            ``error_code == TOO_LONG``.
        """
        # render() flattens all parts to one string, so a limit that spans parts is measured as a
        # whole rather than per part.
        value = view.render()
        length = len(value)  # a safe summary — we never put the raw value in the result

        if length > self.max_length:
            return FailedResult(
                error_message=f"Value exceeds the {self.max_length}-character limit.",
                error_code=self.TOO_LONG,
                details={"length": length, "limit": self.max_length},
            )
        return PassedResult()
