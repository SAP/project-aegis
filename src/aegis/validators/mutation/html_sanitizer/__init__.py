"""The ``HtmlSanitizer`` mutation validator.

The public surface is the single class :class:`HtmlSanitizer`; everything else in
this package (default allowlists, the cleaning helper) is an implementation detail.
"""

from aegis.validators.mutation.html_sanitizer._sanitizer import HtmlSanitizer

__all__ = ["HtmlSanitizer"]
