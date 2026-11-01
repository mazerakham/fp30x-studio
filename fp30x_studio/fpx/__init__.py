"""FPX container: the on-disk format defined in ``docs/SPEC-fpx-format.md``.

This package holds tooling for the *container*, not for the music. Today that is
one module, :mod:`fp30x_studio.fpx.validate`, which reads a bundle and reports
which numbered requirements of the specification it violates::

    python -m fp30x_studio.fpx.validate

The names below are re-exported lazily. Importing ``validate`` eagerly here
would make ``python -m fp30x_studio.fpx.validate`` load the module twice, which
CPython warns about and which would run the CLI against a stale copy.
"""

from __future__ import annotations

__all__ = ["CHECKS", "BundleReport", "LibraryReport", "Result",
           "check_tombstones", "validate_bundle", "validate_tree"]


def __getattr__(name: str):
    if name in __all__:
        from . import validate as _v
        return getattr(_v, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
