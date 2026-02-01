"""Pydantic v1 patch for Python 3.14+ compatibility.

This module patches pydantic v1's ModelMetaclass to handle PEP 649 deferred
annotations. Must be imported before any pydantic v1 models are loaded
(e.g., before importing langfuse).

See: https://github.com/langfuse/langfuse/issues/9618
"""

from __future__ import annotations

import annotationlib
import contextlib

import pydantic.v1.main

_original_new = pydantic.v1.main.ModelMetaclass.__new__


def _patched(__cls: type, name: str, bases: tuple[type, ...], namespace: dict, **kwargs) -> type:
    if "__annotations__" not in namespace and "__annotate_func__" in namespace:
        with contextlib.suppress(Exception):
            namespace["__annotations__"] = namespace["__annotate_func__"](annotationlib.Format.VALUE)
    return _original_new(__cls, name, bases, namespace, **kwargs)


pydantic.v1.main.ModelMetaclass.__new__ = staticmethod(_patched)  # type: ignore[assignment]
