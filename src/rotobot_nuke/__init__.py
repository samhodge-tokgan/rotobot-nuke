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

from .undersample import (
    DEFAULT_TOLERANCE,
    TOLERANCE_AGGRESSIVE,
    TOLERANCE_BALANCED,
    TOLERANCE_CONSERVATIVE,
    TOLERANCE_VERY_AGGRESSIVE,
    rdp_reduction,
    undersample_doc,
    undersample_json,
    undersample_object,
)

__all__ = [
    "__version__",
    "DEFAULT_TOLERANCE",
    "TOLERANCE_CONSERVATIVE",
    "TOLERANCE_BALANCED",
    "TOLERANCE_AGGRESSIVE",
    "TOLERANCE_VERY_AGGRESSIVE",
    "LozengeDoc",
    "LozengeFrame",
    "LozengeObject",
    "LozengePoint",
    "MissingResolutionError",
    "load_json",
    "build_roto",
    "rdp_reduction",
    "undersample_doc",
    "undersample_json",
    "undersample_object",
]


def __getattr__(name: str):
    if name == "build_roto":
        from .importer import build_roto as _build_roto

        return _build_roto
    raise AttributeError(f"module 'rotobot_nuke' has no attribute {name!r}")
