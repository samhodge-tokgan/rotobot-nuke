"""Every import turns on Roto shape motion blur (global mode, 16 samples, 0.5-frame centred shutter).

A new Roto node draws no motion blur: its mode is "shape" and each shape's own
motion blur is off. Uses the fake Nuke from conftest.
"""

from __future__ import annotations

import io
import json

import pytest

from rotobot_nuke import load_json
from rotobot_nuke.importer import MOTION_BLUR_KNOBS, build_roto

EXPECTED = {
    "motionblur_mode": "global",
    "global_motionblur": 16,
    "global_motionblur_shutter": 0.5,
    "global_motionblur_shutter_offset_type": "centred",
}


def _doc():
    pts = [{"x": x, "y": y, "left_x": x - 1, "left_y": y, "right_x": x + 1, "right_y": y}
           for x, y in ((10, 10), (50, 10), (50, 50), (10, 50))]
    data = {"schema": "lozenge_bezier_anim", "schema_version": 2, "fps": 24, "resolution": [100, 100],
            "objects": {"p0:body:C:body": {"visibility": {"1": 1, "2": 1},
                                           "frames": {"1": {"points": pts}, "2": {"points": pts}}}}}
    return load_json(io.StringIO(json.dumps(data)))


def test_knob_table():
    assert dict(MOTION_BLUR_KNOBS) == EXPECTED


@pytest.mark.parametrize("curve_type", ["bspline", "bezier"])
@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_import_turns_on_shape_motion_blur(fake_nuke, curve_type, mode):
    node = build_roto(_doc(), curve_type=curve_type, mode=mode)
    for name, value in EXPECTED.items():
        assert node[name].value() == value, name
    assert "Shape motion blur: on" in node["label"].value()


def test_cached_import_turns_it_on_too(fake_nuke, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    src = tmp_path / "shot.json"
    src.write_text("{}")
    build_roto(_doc(), cache_path=str(src))
    node = build_roto(_doc(), cache_path=str(src))  # second import: curves from the cache
    for name, value in EXPECTED.items():
        assert node[name].value() == value, name
