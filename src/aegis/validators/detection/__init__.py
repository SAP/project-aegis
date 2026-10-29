"""Detection validators — run under ``Guard`` (concurrent, detect-only).

A detection validator implements the ``Validator`` protocol: a ``name`` and an
``async validate(view)`` that returns ``PassedResult`` or ``FailedResult``. It receives a
``ContentView`` (call ``view.render()`` for the flattened text, or iterate ``view.items``)
and never changes the content — it only reports a verdict. Subclass ``BaseValidator``
for convenience.

Aegis ships a set of built-in detection validators (listed in the imports and ``__all__``
below), each importable as ``from aegis.validators.detection import ContentFilter`` (and so
on). To add another, drop a module in this package and list its class in the imports and
``__all__`` below. Keep additions simple and broadly useful; see ``docs/custom-validators.md``.

Example
-------
    # aegis/validators/detection/max_length.py
    from aegis.validators import BaseValidator, FailedResult, PassedResult

    class MaxLength(BaseValidator):
        def __init__(self, limit: int) -> None:
            self.limit = limit

        async def validate(self, view, metadata=None):
            if len(view.render()) > self.limit:
                return FailedResult(error_message="too long", error_code="TOO_LONG")
            return PassedResult()
"""

from aegis.validators.detection.content_filter import ContentFilter
from aegis.validators.detection.formula_injection_detector import FormulaInjectionDetector
from aegis.validators.detection.length_validator import LengthValidator
from aegis.validators.detection.prompt_leakage_check import PromptLeakageCheck
from aegis.validators.detection.size_validator import SizeValidator
from aegis.validators.detection.sql_injection_detector import SqlInjectionDetector
from aegis.validators.detection.xss_detector import XssDetector
from aegis.validators.detection.xxe_detector import XxeDetector

__all__ = [
    "LengthValidator",
    "PromptLeakageCheck",
    "SizeValidator",
    "ContentFilter",
    "SqlInjectionDetector",
    "XssDetector",
    "FormulaInjectionDetector",
    "XxeDetector",
]
