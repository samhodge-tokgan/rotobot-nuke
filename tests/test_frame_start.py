"""Source frame numbers: ``frame_start`` puts every key on the plate's own frames.

JSON frames are 1..N. A JSON that records ``frame_start`` says which plate
frame JSON frame 1 is (1001 for ``shot.1001.exr``), so JSON frame ``f`` is
keyed on Nuke frame ``f + frame_start - 1``. Without it, or with a value that
is not a usable frame number, keys stay on 1..N and the project range is left
alone. Uses the fake Nuke from conftest.
"""

from __future__ import annotations

import io
import json

import pytest

from rotobot_nuke import cache, load_json, undersample_doc, undersample_json
from rotobot_nuke.importer import build_roto
from rotobot_nuke.reader import json_frame_start, source_frame_offset


def _clip(n=3, **top):
    """An ``n``-frame, one-object v2 JSON on frames 1..n, with bones so the
    undersampler can run on it."""
    def pts(f):
        return [{"x": x + f, "y": y, "left_x": x + f - 1, "left_y": y,
                 "right_x": x + f + 1, "right_y": y}
                for x, y in ((10, 10), (50, 10), (50, 50), (10, 50))]

    data = {
        "schema": "lozenge_bezier_anim", "schema_version": 2, "fps": 24,
        "resolution": [100, 100],
        "objects": {"p0:leg:R:thigh": {
            "visibility": {str(f): 1 for f in range(1, n + 1)},
            "frames": {str(f): {"points": pts(f),
                                "bone": {"pt0": {"x": 10 + f, "y": 10},
                                         "pt1": {"x": 50 + f, "y": 50}}}
                       for f in range(1, n + 1)},
        }},
    }
    data.update(top)
    return data


def _load(data):
    return load_json(io.StringIO(json.dumps(data)))


def _shapes(parent):
    for c in parent.children:
        if type(c).__name__ == "_Shape":
            yield c
        elif type(c).__name__ == "_Layer":
            yield from _shapes(c)


def _import(data, **kw):
    node = build_roto(_load(data), **kw)
    (shape,) = list(_shapes(node["curves"].rootLayer))
    return node, shape


def _keys(shape, knob="center"):
    return sorted({f for cp in shape for f, _ in getattr(cp, knob).keys})


def _visible(shape):
    """frame -> visibility, last write wins (as in Nuke)."""
    return dict(shape.visibility_keys)


def _range(fake_nuke):
    root = fake_nuke.root()
    return root["first_frame"].value(), root["last_frame"].value()


def _range_untouched(fake_nuke):
    knobs = fake_nuke.root()._knobs
    return "first_frame" not in knobs and "last_frame" not in knobs


# ---------------------------------------------------------------------------
# Reader


class TestReader:
    def test_frame_start_is_parsed(self):
        doc = _load(_clip(frame_start=1001))
        assert doc.frame_start == 1001
        assert source_frame_offset(doc) == 1000

    def test_a_whole_float_is_a_frame_number(self):
        assert json_frame_start(1001.0) == 1001

    def test_missing_frame_start_is_none(self):
        doc = _load(_clip())
        assert doc.frame_start is None
        assert source_frame_offset(doc) == 0

    @pytest.mark.parametrize(
        "bad", [-5, "977", 9.5, True, float("inf"), float("nan"), None, [1001]]
    )
    def test_a_malformed_frame_start_is_absent(self, bad):
        assert json_frame_start(bad) is None


# ---------------------------------------------------------------------------
# Legacy build path


class TestLegacy:
    def test_a_977_start_clip_keys_on_977(self, fake_nuke):
        _, shape = _import(_clip(72, frame_start=977, frame_padding=8))
        assert _keys(shape)[0] == 977
        assert _keys(shape)[-1] == 1048
        assert _range(fake_nuke) == (977, 1048)
        vis = _visible(shape)
        assert vis[976] is False, "hidden before the clip, on source numbers"
        assert vis[977] is True
        assert vis[1049] is False
        assert 0 not in vis

    def test_a_1001_start_clip_keys_on_1001(self, fake_nuke):
        _, shape = _import(_clip(3, frame_start=1001, frame_padding=4))
        assert _keys(shape) == [1001, 1002, 1003]
        assert _range(fake_nuke) == (1001, 1003)
        assert _visible(shape)[1000] is False

    def test_a_0_start_clip_keys_from_0(self, fake_nuke):
        _, shape = _import(_clip(3, frame_start=0, frame_padding=4),
                           curve_type="bezier")
        assert _keys(shape) == [0, 1, 2]
        assert _keys(shape, "leftTangent") == [0, 1, 2]
        assert _keys(shape, "rightTangent") == [0, 1, 2]
        assert _range(fake_nuke) == (0, 2)

    def test_without_frame_start_nothing_changes(self, fake_nuke):
        _, shape = _import(_clip(3))
        assert _keys(shape) == [1, 2, 3]
        assert _visible(shape)[0] is False
        assert _range_untouched(fake_nuke), "an old JSON never set the project range"

    @pytest.mark.parametrize("bad", [-5, "977", 9.5, True, float("inf"), float("nan")])
    def test_a_malformed_frame_start_is_ignored(self, fake_nuke, bad):
        _, shape = _import(_clip(2, frame_start=bad))
        assert _keys(shape) == [1, 2]
        assert _range_untouched(fake_nuke)

    def test_last_frame_is_set_before_first_frame(self, fake_nuke, monkeypatch):
        """Moving first_frame to 1001 while last_frame is still a 1-based
        default would ask Nuke for a backwards range on the way there."""
        order = []
        root = fake_nuke.root()
        for name in ("first_frame", "last_frame"):
            knob = root[name]
            monkeypatch.setattr(
                knob, "setValue",
                lambda v, name=name: order.append((name, v)),
            )
        _import(_clip(3, frame_start=1001))
        assert order == [("last_frame", 1003), ("first_frame", 1001)]

    def test_set_frame_range_false_leaves_the_project_alone(self, fake_nuke):
        _, shape = _import(_clip(3, frame_start=1001), set_frame_range=False)
        assert _keys(shape) == [1001, 1002, 1003]
        assert _range_untouched(fake_nuke)


# ---------------------------------------------------------------------------
# Hierarchical build path


def _child(parent, name):
    return next(c for c in parent.children if getattr(c, "name", None) == name)


class TestHierarchical:
    def test_every_key_lands_on_source_numbers(self, fake_nuke, fixtures_dir):
        data = json.loads((fixtures_dir / "v3_small.json").read_text())
        # The fixture keys JSON frames 0..2; frame_start says JSON frame 1
        # is plate 1001, so they land on 1000..1002.
        data["frame_start"] = 1001
        node = build_roto(_load(data), mode="hierarchical")
        root_layer = node["curves"].rootLayer
        (shape,) = list(_shapes(root_layer))
        assert _keys(shape) == [1000, 1001, 1002]
        vis = _visible(shape)
        assert vis[999] is False and vis[1003] is False
        assert vis[1000] is True

        camera = _child(root_layer, "camera_track")
        pelvis = _child(camera, "p0_pelvis")
        part = _child(pelvis, "p0:leg:R:thigh")
        # The camera is the homography on the extra matrix; translate (0, 3)
        # is its x translation.
        cam_keys = camera.getTransform().getExtraMatrixAnimCurve(0, 3).anim_keys
        assert {f for f, _ in cam_keys} == {1000, 1001, 1002}
        for layer in (pelvis, part):
            xform = layer.getTransform()
            assert {f for f, _ in xform.getTranslationAnimCurve(0).anim_keys} == {
                1000, 1001, 1002
            }, layer.name
        assert _range(fake_nuke) == (1001, 1002)

    def test_v2_identity_keys_follow_the_offset(self, fake_nuke):
        node = build_roto(_load(_clip(3, frame_start=1001)), mode="hierarchical")
        camera = _child(node["curves"].rootLayer, "camera_track")
        tx_keys = camera.getTransform().getExtraMatrixAnimCurve(0, 3).anim_keys
        # JSON frames 1..3 on plate 1001..1003, identity on each.
        assert tx_keys == [(1001, 0.0), (1002, 0.0), (1003, 0.0)]

    def test_without_frame_start_nothing_changes(self, fake_nuke, fixtures_dir):
        node = build_roto(load_json(fixtures_dir / "v3_small.json"), mode="hierarchical")
        (shape,) = list(_shapes(node["curves"].rootLayer))
        assert _keys(shape) == [0, 1, 2]
        assert _range_untouched(fake_nuke)


# ---------------------------------------------------------------------------
# Undersampling


class TestUndersample:
    def test_undersample_doc_keeps_frame_start(self):
        doc = undersample_doc(_load(_clip(24, frame_start=1001)), tolerance=5.0)
        assert doc.frame_start == 1001

    def test_undersampled_import_keys_on_source_numbers(self, fake_nuke):
        _, shape = _import(_clip(24, frame_start=1001), undersample_tolerance=5.0)
        keys = _keys(shape)
        assert keys[0] == 1001 and keys[-1] == 1024
        assert len(keys) < 24, "linear motion should undersample"
        assert _range(fake_nuke) == (1001, 1024)

    @pytest.mark.parametrize("kw", [{"tolerance": 5.0}, {"articulation_tolerance": 5.0}])
    def test_undersample_json_keeps_frame_start(self, tmp_path, kw):
        src = tmp_path / "in.json"
        out = tmp_path / "out.json"
        src.write_text(json.dumps(_clip(24, frame_start=1001)))
        undersample_json(src, out, **kw)
        assert json.loads(out.read_text())["frame_start"] == 1001
        assert load_json(out).frame_start == 1001


# ---------------------------------------------------------------------------
# Import cache


@pytest.fixture
def xdg_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    yield tmp_path / "xdg" / "rotobot_nuke"


def _marker(node):
    return any(getattr(c, "name", None) == "__from_script_marker__"
               for c in node["curves"].rootLayer.children)


class TestCache:
    def test_frame_start_changes_digest(self, tmp_path):
        p = tmp_path / "doc.json"
        p.write_text("{}")
        a = cache.make_key(str(p))
        b = cache.make_key(str(p), frame_start=1001)
        c = cache.make_key(str(p), frame_start=977)
        assert len({a.digest(), b.digest(), c.digest()}) == 3

    def test_another_frame_start_misses_the_cache(self, xdg_cache, fake_nuke, tmp_path):
        p = tmp_path / "doc.json"
        p.write_text(json.dumps(_clip(3, frame_start=1001)))
        doc = load_json(p)
        build_roto(doc, cache_path=str(p))
        assert _marker(build_roto(doc, cache_path=str(p))), "same doc hits"
        # Same path and mtime, another frame_start (e.g. a caller-edited doc).
        doc.frame_start = 977
        assert not _marker(build_roto(doc, cache_path=str(p)))

    def test_a_cache_hit_still_sets_the_range(self, xdg_cache, fake_nuke, tmp_path):
        p = tmp_path / "doc.json"
        p.write_text(json.dumps(_clip(3, frame_start=1001)))
        doc = load_json(p)
        build_roto(doc, cache_path=str(p))
        fake_nuke.root()._knobs.clear()
        node = build_roto(doc, cache_path=str(p))
        assert _marker(node)
        assert _range(fake_nuke) == (1001, 1003)
