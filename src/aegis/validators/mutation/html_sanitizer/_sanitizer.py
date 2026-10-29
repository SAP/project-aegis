"""HTML sanitizer — reduce HTML content to a safe, allowlisted subset.

The class docstring below is the living spec: the authoritative description of what
this validator guarantees. See ``docs/validators/mutation/html-sanitizer.md`` for the
user-facing page.
"""

from __future__ import annotations

from typing import Any, Final

import nh3

from aegis.metadata import GuardMetadata
from aegis.validators import (
    BaseMutationValidator,
    ContentView,
    FailedResult,
    ItemEdit,
    PassedResult,
    ValidationResult,
)
from aegis.validators.mutation.html_sanitizer._defaults import DEFAULT_ALLOWED_URL_SCHEMES


class HtmlSanitizer(BaseMutationValidator):
    """Reduces HTML content to a safe, allowlisted subset, removing anything else.

    ``HtmlSanitizer`` treats each content part as an HTML fragment and enforces an
    allowlist of tags, attributes, URL schemes, and (optionally) inline-CSS
    properties. Everything outside the allowlist — ``<script>``, event-handler
    attributes such as ``onerror``, ``javascript:`` URLs, HTML comments, unknown
    tags — is removed, and the result is re-serialized from a spec-compliant parse
    tree. This defends against cross-site scripting (XSS, CWE-79) and mutation-XSS
    when agent content may later be rendered as HTML by a downstream client.

    It is a **detect-and-fix** validator. Under ``MutationGuard`` it repairs
    content; under ``Guard`` it runs detect-only (rejecting anything not already in
    canonical, allowlisted form). Detection and repair share one definition of
    "safe": a part is acceptable exactly when it already equals its sanitized form.

    - ``validate`` sanitizes each part and compares it to the original. Any
      difference means the part carried disallowed or non-canonical markup, so it
      returns a :class:`FailedResult` with :attr:`DISALLOWED_HTML`.
    - ``mutate`` returns the sanitized text as a whole-part edit for each part that
      changed; unchanged parts are left untouched.

    The sanitizer is powered by ``nh3`` (the Rust ``ammonia`` library over
    ``html5ever``). Because it parses input into a tree and re-serializes, it is
    resilient to syntactic obfuscation — mixed case (``<ScRiPt>``), HTML entities
    (``&#106;avascript:``), and malformed nesting are all resolved to the canonical
    tree *before* the allowlist is applied.

    **Precondition — HTML only.** Apply this to content that is meant to be rendered
    as HTML. Plain text is not safe input: a stray ``<`` or ``>`` (as in ``5 < 3``)
    is HTML-escaped, which changes the text and is reported as a violation. Deciding
    whether content should be HTML at all is the caller's routing decision, not this
    validator's job.

    **Safe defaults, widen at your own risk.** Out of the box the policy is
    HTML-only with a tightened URL-scheme set and inline CSS disabled. Every
    constructor parameter that widens the allowlist enlarges the attack surface;
    two are especially load-bearing:

    1. Adding foreign-content tags (``svg``, ``math``, ``foreignObject``) reopens
       namespace-confusion vectors that the HTML-only default avoids.
    2. Enabling inline CSS (``allowed_css_properties``) is only as safe as the
       property list you allow — see that parameter's documentation.

    Args:
        allowed_tags: The set of permitted tag names. ``None`` (default) uses nh3's
            vetted HTML-only default set. Passing a set *replaces* the default
            entirely rather than adding to it.
        allowed_attributes: A mapping of tag name to the set of attributes permitted
            on it (use ``"*"`` as the key for attributes allowed on any tag).
            ``None`` (default) uses nh3's per-tag default, which contains no
            event-handler attributes. Passing a mapping *replaces* the default
            entirely and is honored as given: if you allowlist an event handler such
            as ``onclick``, it is kept — you own that choice, exactly as you do when
            you allowlist the ``javascript`` URL scheme or a risky tag. Widen this
            only for content you trust. (Inline CSS is the one exception: the
            ``style`` attribute is governed solely by ``allowed_css_properties`` and
            is dropped when that is ``None``, regardless of this mapping.)
        allowed_url_schemes: The URL schemes permitted in URL-bearing attributes.
            Defaults to ``http``, ``https``, ``mailto``; ``javascript:`` and
            ``data:`` are excluded, so they are stripped.
        allowed_css_properties: The inline-CSS properties permitted in ``style``
            attributes. ``None`` (default) disables inline CSS entirely — the
            ``style`` attribute is removed even if you allow it in
            ``allowed_attributes``. Passing a set enables inline CSS limited to
            those properties; nh3 also sanitizes property *values* (dropping ``url()``
            values whose scheme is disallowed). The set is honored as given, so start
            from a small, vetted set of visual properties rather than allowing
            arbitrary ones. Avoid ``behavior`` and ``-moz-binding``: they load script
            through a ``url()`` that nh3's value-level filtering does *not* catch, so
            allowlisting them re-enables a script vector.
        strip_comments: Whether to remove HTML comments. Defaults to ``True`` —
            comments can leak metadata and are not rendered content.
        link_rel: The ``rel`` value forced onto ``<a>`` tags. Defaults to
            ``"noopener noreferrer"``, which mitigates reverse-tabnabbing. Pass
            ``None`` to leave ``rel`` untouched.

    Example:
        >>> from aegis import MutationGuard
        >>> guard = MutationGuard("output").use(HtmlSanitizer())
        >>> result = await guard.validate("<script>alert(1)</script>Hello")
        >>> result.was_mutated
        True
        >>> result.output
        'Hello'
    """

    # The single failure this validator reports: a part is not already in its
    # sanitized, allowlisted form. Defined once here so validate(), the tests, and
    # the doc page share one source of truth.
    DISALLOWED_HTML: Final = "DISALLOWED_HTML"

    def __init__(
        self,
        *,
        allowed_tags: set[str] | None = None,
        allowed_attributes: dict[str, set[str]] | None = None,
        allowed_url_schemes: set[str] | frozenset[str] = DEFAULT_ALLOWED_URL_SCHEMES,
        allowed_css_properties: set[str] | frozenset[str] | None = None,
        strip_comments: bool = True,
        link_rel: str | None = "noopener noreferrer",
    ) -> None:
        self._tags = allowed_tags
        self._url_schemes = set(allowed_url_schemes)
        self._css_properties = set(allowed_css_properties) if allowed_css_properties is not None else None
        self._strip_comments = strip_comments
        self._link_rel = link_rel

        # A caller-supplied ``allowed_attributes`` replaces the default entirely and is
        # honored as given — the same contract ``allowed_tags`` and ``allowed_url_schemes``
        # follow. Safety comes from the safe defaults, not from silently overriding an
        # explicit request. Three cases:
        # 1. A tag→attributes mapping is kept as passed. If a caller allowlists an event
        #    handler such as ``onclick`` it stays — their explicit choice, no different
        #    from allowlisting the ``javascript`` scheme or the ``iframe`` tag.
        # 2. ``style`` is the one exception, and it is parameter precedence rather than an
        #    override: inline CSS is controlled solely by ``allowed_css_properties``, so
        #    when that is None (CSS off) a ``style`` entry here is dropped. Otherwise nh3
        #    with no property filter would allow *every* CSS property, silently enabling
        #    unfiltered inline CSS the caller did not ask for. Matched case-insensitively,
        #    since HTML attribute names are.
        # 3. ``None`` passes straight to nh3, whose per-tag default contains no event
        #    handlers.
        if allowed_attributes is not None and self._css_properties is None:
            self._attributes: dict[str, set[str]] | None = {
                tag: {attr for attr in attrs if attr.lower() != "style"} for tag, attrs in allowed_attributes.items()
            }
        else:
            self._attributes = allowed_attributes

    def _clean(self, text: str) -> str:
        """Return ``text`` reduced to the configured allowlist, re-serialized.

        This is the one place the policy is applied. Both ``validate`` and
        ``mutate`` call it, so the verdict and the repair can never diverge.

        ``clean_content_tags`` is left at nh3's default (``{script, style}``), which
        removes those elements *together with their text content* — unlike a
        disallowed tag, whose text children are kept — so a ``<script>`` body never
        survives as visible text.

        There is no error path to handle: ``nh3.clean`` never raises on malformed or
        adversarial input. It repairs the tree and re-serializes, so a broken
        fragment comes back normalized rather than as an exception.
        """
        return nh3.clean(
            text,
            tags=self._tags,
            attributes=self._attributes,
            url_schemes=self._url_schemes,
            filter_style_properties=self._css_properties,
            strip_comments=self._strip_comments,
            link_rel=self._link_rel,
        )

    async def validate(self, view: ContentView, metadata: GuardMetadata | None = None) -> ValidationResult:
        """Report whether every part is already in its sanitized form.

        Each part is sanitized independently and compared to the original. We
        compare against the sanitized form rather than trying to recognize "bad"
        markup directly: the allowlist is the only arbiter of safety, so anything
        the sanitizer would change — a disallowed tag, a dropped attribute, or even
        benign normalization such as closing an open tag — is treated as a
        violation to repair. This favors safety over precision; for a mutation
        validator the cost of a benign-normalization "false" failure is nil, since
        ``mutate`` simply emits the already-canonical form.

        Args:
            view: The content under inspection. Each item is treated as an
                independent HTML fragment.
            metadata: Surrounding agent context. Unused — the sanitizer governs
                markup only, never the meaning of the prose.

        Returns:
            :class:`PassedResult` when every part equals its sanitized form,
            otherwise a :class:`FailedResult` with ``error_code``
            :attr:`DISALLOWED_HTML`. The ``details`` mapping carries only safe
            summaries — ``changed_parts`` and the summed ``original_length`` /
            ``sanitized_length`` — never the raw markup, which could contain an
            XSS payload or an embedded secret.
        """
        changed_parts = 0
        original_length = 0
        sanitized_length = 0
        for part in view.items:
            sanitized = self._clean(part.value)
            if sanitized != part.value:
                changed_parts += 1
                original_length += len(part.value)
                sanitized_length += len(sanitized)

        if changed_parts:
            details: dict[str, Any] = {
                "changed_parts": changed_parts,
                "original_length": original_length,
                "sanitized_length": sanitized_length,
            }
            return FailedResult(
                error_code=self.DISALLOWED_HTML,
                error_message="Content contains disallowed or non-canonical HTML markup.",
                details=details,
            )
        return PassedResult()

    async def mutate(
        self,
        view: ContentView,
        metadata: GuardMetadata | None = None,
        validation_failure: FailedResult | None = None,
    ) -> list[ItemEdit]:
        """Return the sanitized form of each part that is not already clean.

        Only parts that actually change are addressed; an unchanged part is left
        alone, which keeps the edit set minimal and lets the engine report
        ``was_mutated=False`` when nothing needed fixing.

        The output is safe to re-validate: feeding it back through ``validate``
        yields :class:`PassedResult`, because sanitizing already-sanitized content
        is a no-op (``clean(clean(x)) == clean(x)``). This fixed-point property is
        what lets the mutation path be trusted, and is enforced by the test suite.

        Args:
            view: The content to repair.
            metadata: Surrounding agent context. Unused.
            validation_failure: The failure from ``validate``. Unused — ``mutate``
                recomputes the sanitized form directly rather than depending on the
                detection details.

        Returns:
            One :class:`ItemEdit` per changed part, addressed by part id.
        """
        edits: list[ItemEdit] = []
        for part in view.items:
            sanitized = self._clean(part.value)
            if sanitized != part.value:
                edits.append(ItemEdit(part.id, sanitized))
        return edits
