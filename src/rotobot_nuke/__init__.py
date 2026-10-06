"""rotobot-nuke — Import Rotobot-Next lozenge_bezier_anim JSON into Nuke.

Public API:
    load_json, build_roto, MissingResolutionError
    LozengeDoc, LozengeObject, LozengeFrame, LozengePoint
"""

from ._version import __version__
from .reader import (
    LozengeDoc,
    LozengeFrame,
    LozengeObject,
    LozengePoint,
    MissingResolutionError,
    load_json,
)

__all__ = [
    "__version__",
    "LozengeDoc",
    "LozengeFrame",
    "LozengeObject",
    "LozengePoint",
    "MissingResolutionError",
    "load_json",
    "build_roto",
]


def __getattr__(name: str):
    if name == "build_roto":
        from .importer import build_roto as _build_roto

        return _build_roto
    raise AttributeError(f"module 'rotobot_nuke' has no attribute {name!r}")
