"""Visibility keys: a part that leaves the plate mid-shot (fingers out of
frame) has no JSON entry for those frames, and must be keyed hidden on each
of them -- Nuke holds a visibility key until the next one."""

from __future__ import annotations

import json

import pytest

from rotobot_nuke import load_json
from rotobot_nuke.importer import build_roto


def _clip(frames, **top):
    """A one-object v2 JSON keyed on ``frames``, with bones."""
    def pts(f):
        return [{"x": x + f, "y": y, "left_x": x + f - 1, "left_y": y,
                 "right_x": x + f + 1, "right_y": y}
                for x, y in ((10, 10), (50, 10), (50, 50), (10, 50))]

    data = {
        "schema": "lozenge_bezier_anim", "schema_version": 2, "fps": 24,
        "resolution": [100, 100],
        "objects": {"p0:hand:L:B_index2": {
            "visibility": {str(f): 1 for f in frames},
            "frames": {str(f): {"points": pts(f),
                                "bone": {"pt0": {"x": 10 + f, "y": 10},
                                         "pt1": {"x": 50 + f, "y": 50}}}
                       for f in frames},
        }},
    }
    data.update(top)
    return data


def _load(tmp_path, data):
    p = tmp_path / "doc.json"
    p.write_text(json.dumps(data))
    return load_json(p)


def _shape(node):
    def walk(layer):
        for c in layer.children:
            if type(c).__name__ == "_Shape":
                yield c
            elif type(c).__name__ == "_Layer":
                yield from walk(c)

    (shape,) = list(walk(node["curves"].rootLayer))
    return shape


def _visible(shape):
    """frame -> visibility, last write wins (as in Nuke)."""
    return dict(shape.visibility_keys)


@pytest.mark.parametrize("mode", ["legacy", "hierarchical"])
def test_out_of_frame_gap_is_hidden_until_the_part_returns(fake_nuke, tmp_path, mode):
    # On the plate 1-3, out of frame 4-6, back 7-9.
    frames = [1, 2, 3, 7, 8, 9]
    vis = _visible(_shape(build_roto(_load(tmp_path, _clip(frames)), mode=mode)))
    assert [vis[f] for f in range(0, 11)] == [
        False, True, True, True, False, False, False, True, True, True, False
    ]


@pytest.mark.parametrize("mode", ["legacy", "hierarchical"])
def test_gap_keys_follow_frame_start(fake_nuke, tmp_path, mode):
    data = _clip([1, 2, 5, 6], frame_start=1001)
    vis = _visible(_shape(build_roto(_load(tmp_path, data), mode=mode)))
    assert vis[1002] is True and vis[1003] is False and vis[1004] is False
    assert vis[1005] is True


def test_explicit_hidden_frames_stay_hidden(fake_nuke, tmp_path):
    data = _clip([1, 2, 3, 4])
    data["objects"]["p0:hand:L:B_index2"]["visibility"]["3"] = 0
    vis = _visible(_shape(build_roto(_load(tmp_path, data))))
    assert (vis[2], vis[3], vis[4]) == (True, False, True)


def test_undersampled_frames_stay_visible(fake_nuke, tmp_path):
    """Undersampling drops keyframes, not visibility: a thinned-out frame is
    still on the plate."""
    frames = list(range(1, 21))
    node = build_roto(_load(tmp_path, _clip(frames)), undersample_tolerance=1000.0)
    shape = _shape(node)
    keyed = {f for cp in shape for f, _ in cp.center.keys}
    assert len(keyed) < len(frames)  # frames really were thinned
    vis = _visible(shape)
    assert all(vis[f] for f in frames)
    assert vis[0] is False and vis[21] is False
