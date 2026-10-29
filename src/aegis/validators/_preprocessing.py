"""Decode obfuscation before matching — shared normalization for text-scanning validators.

Real injection payloads (XSS, SQLi, XXE, formula) hide behind encoding layers, so content is
normalized before any pattern runs: invisible/zero-width control characters are stripped, then
URL-percent, HTML-entity, JavaScript ``\\uXXXX``/``\\xXX``, and CSS ``\\3C`` escapes are decoded
repeatedly until the text stops changing (bounded). Without this, ``%3Cscript%3E`` or
``&#60;script&#62;`` would sail past a raw-text matcher.

:func:`preprocess` is the entry point; the individual decoders and the fixed-point loop are
implementation detail.
"""

from __future__ import annotations

import html
import re
import unicodedata
import urllib.parse
from typing import Final

# Fast path: text of only word chars and whitespace can carry no injection marker, so skip
# decoding entirely.
_SAFE_CHARS: Final = re.compile(r"^[\w\s]+$", re.UNICODE)

_JS_UNICODE: Final = re.compile(r"\\u([0-9a-fA-F]{4})")
_JS_HEX: Final = re.compile(r"\\x([0-9a-fA-F]{2})")
_CSS_HEX: Final = re.compile(r"\\([0-9a-fA-F]{1,6})\s?")
MAX_DECODE_ITERATIONS: Final = 5


def _from_hex(match: re.Match[str]) -> str:
    """Turn a captured hex escape into its character, or leave it verbatim if out of range.

    A 6-digit CSS escape can name a value above the Unicode maximum (``0x10FFFF``); ``chr`` would
    raise on it, so such an escape is returned unchanged rather than crashing the caller.
    """
    codepoint = int(match.group(1), 16)
    return chr(codepoint) if codepoint <= 0x10FFFF else match.group(0)


def _strip_invisible(text: str) -> str:
    """Remove Unicode control/format characters (categories ``Cc``/``Cf``), keeping ``\\t \\n \\r``.

    This defeats zero-width spaces, zero-width joiners, soft hyphens, and bidirectional markers
    used to break a keyword up (``<scr\u200bipt>``). Ordinary whitespace is preserved.
    """
    return "".join(c for c in text if c in "\t\n\r" or unicodedata.category(c) not in {"Cc", "Cf"})


def _decode_pass(text: str) -> str:
    """Apply one round of every decoder, in the order attackers stack them."""
    text = urllib.parse.unquote(text)  # URL percent-encoding
    text = html.unescape(text)  # HTML entities (&#60; &lt; …)
    text = _JS_UNICODE.sub(_from_hex, text)  # JavaScript \uXXXX
    text = _JS_HEX.sub(_from_hex, text)  # JavaScript \xXX
    text = _CSS_HEX.sub(_from_hex, text)  # CSS \3C
    return text


def preprocess(text: str, *, strip_invisible: bool, decode: bool) -> str:
    """Normalize obfuscation so a detector sees the real payload.

    Decoding is looped until the text stabilizes (bounded by ``MAX_DECODE_ITERATIONS``) because
    attackers double-encode; the fixed point is what the patterns match against.
    """
    if _SAFE_CHARS.fullmatch(text):
        # Even an all-word/whitespace string can hide a strippable separator: a vertical tab (\x0b)
        # or form feed (\x0c) is category Cc yet matches ``\s``, so such a string reaches the fast
        # path. Nothing here needs *decoding* (no %/&/\\/< markers can match ``\w``), but it must
        # still be stripped so the two preprocessing paths agree.
        return _strip_invisible(text) if strip_invisible else text
    if strip_invisible:
        text = _strip_invisible(text)
    if decode:
        prev = ""
        for _ in range(MAX_DECODE_ITERATIONS):
            if text == prev:
                break
            prev = text
            text = _decode_pass(text)
            # Strip again after each decode: a separator can be *encoded* (e.g. %E2%80%8B or a
            # ``&#x200b;`` entity) and only surface as an invisible char once this pass decodes it,
            # so stripping only before the loop would let the revealed separator split a keyword
            # (``java%E2%80%8Bscript:``).
            if strip_invisible:
                text = _strip_invisible(text)
    return text
