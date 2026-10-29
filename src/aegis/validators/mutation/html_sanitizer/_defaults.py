"""Default allowlists for :class:`HtmlSanitizer`.

These constants are the safe out-of-the-box policy. They live in one place so the
sanitizer, its tests, and its documentation all read the same values, and so a
maintainer can see the whole default policy at a glance.

Two allowlists are intentionally left to the underlying library rather than
restated here: the set of permitted **tags** and the per-tag **attributes**. When
the sanitizer is constructed with ``allowed_tags=None`` / ``allowed_attributes=None``,
those are passed straight through to ``nh3`` so its vetted HTML-only defaults apply.
Restating them would create a second list that silently drifts from the library's
as it is updated for new bypasses.
"""

from __future__ import annotations

# URL schemes permitted in URL-bearing attributes (such as ``href`` / ``src``).
#
# This is tightened from nh3's own 20-plus-scheme default down to the three that
# untrusted agent content realistically needs. The narrower the set, the smaller
# the surface: `javascript:`, `data:`, `vbscript:` and other script-capable or
# niche schemes are simply not on the list, so they are dropped rather than
# reasoned about case by case.
DEFAULT_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})

# A curated set of inline-CSS properties that are safe to permit when a caller
# opts into inline `style` (by passing ``allowed_css_properties=SAFE_CSS_PROPERTIES``
# and allowing the ``style`` attribute). It is *not* a default — inline CSS is off
# unless the caller enables it.
#
# The set is limited to visual, presentational properties. It deliberately omits
# the properties that historically execute script — `behavior` and `-moz-binding`
# (legacy IE / Gecko script-binding vectors) — so widening the property list is a
# conscious step the caller takes, not something they inherit. `nh3` separately
# sanitizes the *values* of these properties (it strips `url()` values with a
# disallowed scheme, such as `url(javascript:...)`), so this name-level allowlist
# and nh3's value-level parsing reinforce each other.
SAFE_CSS_PROPERTIES: frozenset[str] = frozenset(
    {
        "azimuth",
        "background",
        "background-color",
        "background-image",
        "background-position",
        "background-repeat",
        "border",
        "border-bottom",
        "border-bottom-color",
        "border-bottom-style",
        "border-bottom-width",
        "border-collapse",
        "border-color",
        "border-left",
        "border-left-color",
        "border-left-style",
        "border-left-width",
        "border-right",
        "border-right-color",
        "border-right-style",
        "border-right-width",
        "border-spacing",
        "border-style",
        "border-top",
        "border-top-color",
        "border-top-style",
        "border-top-width",
        "border-width",
        "caption-side",
        "clear",
        "color",
        "cursor",
        "direction",
        "display",
        "empty-cells",
        "float",
        "font",
        "font-family",
        "font-size",
        "font-style",
        "font-variant",
        "font-weight",
        "height",
        "letter-spacing",
        "line-height",
        "list-style",
        "list-style-image",
        "list-style-position",
        "list-style-type",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-height",
        "max-width",
        "min-height",
        "min-width",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "table-layout",
        "text-align",
        "text-decoration",
        "text-indent",
        "text-transform",
        "unicode-bidi",
        "vertical-align",
        "white-space",
        "width",
        "word-spacing",
    }
)
