"""Mutation validators — run under ``MutationGuard`` (sequential, detect-and-fix).

A mutation validator implements the ``MutationValidator`` protocol. ``validate(view)``
detects (returns ``PassedResult``/``FailedResult``), and ``mutate(view)`` fixes by
returning a list of ``ItemEdit`` — id-addressed, whole-part replacements. Both methods
receive a ``ContentView``: the message's text parts, in order, each a ``ContentItem``
with an ``id`` that is stable across the run's ``validate`` and ``mutate`` calls, so
``mutate`` can address the parts ``validate`` saw. Use ``view.render()`` when a fix needs
cross-part context. The engine applies these edits over the original parts, so part count
and order are preserved. Subclass ``BaseMutationValidator`` for convenience.

Both protocols are view-based, so a ``MutationValidator`` is a superset of ``Validator``
and can also run detect-only under ``Guard`` (its ``validate`` runs; ``mutate`` is not
called).

Aegis ships the built-in ``HtmlSanitizer`` mutation validator (listed in the imports and
``__all__`` below), importable as ``from aegis.validators.mutation import HtmlSanitizer``. To
add another, drop a module in this package and list its class in the imports and ``__all__``
below. Keep additions simple and broadly useful; see ``docs/custom-validators.md``.

Example
-------
    # aegis/validators/mutation/email_redactor.py
    import re
    from aegis.validators import BaseMutationValidator, FailedResult, ItemEdit, PassedResult

    EMAIL = re.compile(r"[\\w.+-]+@[\\w-]+\\.[\\w.-]+")

    class EmailRedactor(BaseMutationValidator):
        async def validate(self, view, metadata=None):
            if any(EMAIL.search(p.value) for p in view.items):
                return FailedResult(error_message="email present", error_code="EMAIL")
            return PassedResult()

        async def mutate(self, view, metadata=None, validation_failure=None):
            return [
                ItemEdit(p.id, EMAIL.sub("<EMAIL>", p.value))
                for p in view.items if EMAIL.search(p.value)
            ]
"""

from aegis.validators.mutation.html_sanitizer import HtmlSanitizer

__all__ = ["HtmlSanitizer"]
