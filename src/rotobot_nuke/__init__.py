"""rotobot-nuke — Import Rotobot-Next lozenge_bezier_anim JSON into Nuke.

Public API:
    load_json, build_roto, MissingResolutionError
    LozengeDoc, LozengeObject, LozengeFrame, LozengePoint
"""

from ._version import __version__
from .reader import (
    CameraFrame,
    LozengeDoc,
    LozengeFrame,
    LozengeObject,
    LozengePoint,
    MissingResolutionError,
    PersonFrame,
    load_json,
)

from .undersample import (
    DEFAULT_ARTICULATION_TOLERANCE,
    DEFAULT_CAMERA_TOLERANCE,
    DEFAULT_PERSON_TOLERANCE,
    DEFAULT_TOLERANCE,
    PRESET_BALANCED,
    PRESET_COARSE,
    PRESET_FINE,
    PRESETS,
    TOLERANCE_AGGRESSIVE,
    TOLERANCE_BALANCED,
    TOLERANCE_CONSERVATIVE,
    TOLERANCE_VERY_AGGRESSIVE,
    TolerancePreset,
    rdp_reduction,
    undersample_doc,
    undersample_json,
    undersample_object,
)

__all__ = [
    "__version__",
    "CameraFrame",
    "DEFAULT_ARTICULATION_TOLERANCE",
    "DEFAULT_CAMERA_TOLERANCE",
    "DEFAULT_PERSON_TOLERANCE",
    "DEFAULT_TOLERANCE",
    "LozengeDoc",
    "LozengeFrame",
    "LozengeObject",
    "LozengePoint",
    "MissingResolutionError",
    "PRESET_BALANCED",
    "PRESET_COARSE",
    "PRESET_FINE",
    "PRESETS",
    "PersonFrame",
    "TOLERANCE_AGGRESSIVE",
    "TOLERANCE_BALANCED",
    "TOLERANCE_CONSERVATIVE",
    "TOLERANCE_VERY_AGGRESSIVE",
    "TolerancePreset",
    "build_roto",
    "load_json",
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
