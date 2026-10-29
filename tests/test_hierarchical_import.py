"""Tests for the hierarchical Nuke import path (`build_roto(mode='hierarchical')`).

Verifies the three-transform cascade (camera_track → pelvis →
body-part) built on ``rotobot_nuke.hierarchy.decompose``: the exact H2d
on the camera layer's extra matrix, held data, and that every knot
recomposes through the cascade to its JSON plate position, the way Nuke
composes it (row-major extra matrix, then each child's translate +
Z-rotation, measured in Nuke 16.0v8). Design doc:
``docs/design/hierarchical-import.md``.
"""

from __future__ import annotations

import json
import math

import pytest

from rotobot_nuke import load_json
from rotobot_nuke.hierarchy import ROUND_TRIP_TOLERANCE_PX
from rotobot_nuke.importer import _conjugate_h2d_yflip, build_roto


IDENTITY_H2D = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def _apply(m, x, y):
    w = m[6] * x + m[7] * y + m[8]
    return ((m[0] * x + m[1] * y + m[2]) / w, (m[3] * x + m[4] * y + m[5]) / w)


# ---------------------------------------------------------------------------
# Pure math helpers.

class TestYFlip:
    def test_identity_stays_identity(self):
        assert _conjugate_h2d_yflip(IDENTITY_H2D, H=1080) == pytest.approx(IDENTITY_H2D)

    def test_pure_translation_flips_y(self):
        """A Y-down translation of (5, 7) is (5, -7) in Y-up."""
        yup = _conjugate_h2d_yflip((1.0, 0.0, 5.0, 0.0, 1.0, 7.0, 0.0, 0.0, 1.0), H=1080)
        assert yup == pytest.approx((1.0, 0.0, 5.0, 0.0, 1.0, -7.0, 0.0, 0.0, 1.0))

    def test_perspective_matrix_maps_flipped_points(self):
        H2d = (1.1, 0.05, 30.0, -0.04, 0.95, 20.0, 2e-4, 1e-4, 1.0)
        yup = _conjugate_h2d_yflip(H2d, H=1080)
        for x, y in ((0.0, 0.0), (500.0, 400.0), (1919.0, 1079.0)):
            dx, dy = _apply(H2d, x, y)
            ux, uy = _apply(yup, x, 1080 - y)
            assert (ux, uy) == pytest.approx((dx, 1080 - dy))


# ---------------------------------------------------------------------------
# Build-roto dispatch.

class TestDispatch:
    def test_bad_mode_rejected(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        with pytest.raises(ValueError, match="mode"):
            build_roto(doc, mode="other")

    def test_mode_legacy_is_default(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        node = build_roto(doc)
        assert node["label"].value().endswith("Mode: legacy")

    def test_mode_legacy_explicit(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        node = build_roto(doc, mode="legacy")
        assert node["label"].value().endswith("Mode: legacy")

    def test_mode_hierarchical(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        node = build_roto(doc, mode="hierarchical")
        assert node["label"].value().endswith("Mode: hierarchical")

    @pytest.mark.parametrize(
        "fixture, expected",
        [("v3_small.json", "hierarchical"), ("v3_real_cut.json", "hierarchical"),
         ("v2_small.json", "legacy"), ("v2_hd_1920_1080.json", "legacy")],
    )
    def test_mode_auto_follows_v3_data(self, fake_nuke, fixtures_dir, fixture, expected):
        node = build_roto(load_json(fixtures_dir / fixture), mode="auto")
        assert node["label"].value().endswith(f"Mode: {expected}")

    def test_hierarchy_that_does_not_recompose_falls_back_to_legacy(
        self, fake_nuke, fixtures_dir, monkeypatch
    ):
        from rotobot_nuke import importer

        monkeypatch.setattr(importer, "round_trip_error", lambda doc, hier: 1.0)
        printed = []
        monkeypatch.setattr(fake_nuke, "tprint", printed.append)
        node = build_roto(load_json(fixtures_dir / "v3_small.json"), mode="auto")
        assert node["label"].value().endswith("Mode: legacy")
        assert any("importing flat" in m for m in printed)


# ---------------------------------------------------------------------------
# Hierarchy shape.

def _child_layer(parent, name):
    for c in parent.children:
        if type(c).__name__ == "_Layer" and c.name == name:
            return c
    return None


def _child_shape(parent):
    for c in parent.children:
        if type(c).__name__ == "_Shape":
            return c
    return None


class TestHierarchyShape:
    def test_three_level_cascade_present(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        node = build_roto(doc, mode="hierarchical")
        root = node["curves"].rootLayer

        camera = _child_layer(root, "camera_track")
        assert camera is not None, "camera_track Layer missing"

        pelvis = _child_layer(camera, "p0_pelvis")
        assert pelvis is not None, "p0_pelvis Layer missing"

        part = _child_layer(pelvis, "p0:leg:R:thigh")
        assert part is not None, "body-part Layer missing"

        shape = _child_shape(part)
        assert shape is not None, "shape missing at leaf"

    def test_no_region_grouping_layers(self, fake_nuke, fixtures_dir):
        """Hierarchical mode must flatten region/side grouping. Only
        camera_track → pelvis → body-part → shape — no `p0_leg`,
        `p0_leg_R` etc intermediaries that the legacy path emits."""
        doc = load_json(fixtures_dir / "v2_hd_1920_1080.json")  # multi-object
        node = build_roto(doc, mode="hierarchical")
        root = node["curves"].rootLayer

        camera = _child_layer(root, "camera_track")
        pelvis = _child_layer(camera, "p0_pelvis")
        child_names = {c.name for c in pelvis.children if type(c).__name__ == "_Layer"}
        assert "p0_leg" not in child_names
        assert "p0_leg_R" not in child_names
        assert any(":" in n for n in child_names)

    def test_multi_person_parallel_pelvis_subtrees(
        self, fake_nuke, fixtures_dir
    ):
        """v2_hd_1920_1080 has p0 and p1 objects; expect both pelvis
        Layers under camera_track."""
        doc = load_json(fixtures_dir / "v2_hd_1920_1080.json")
        node = build_roto(doc, mode="hierarchical")
        camera = _child_layer(node["curves"].rootLayer, "camera_track")
        pelvis_names = {
            c.name for c in camera.children
            if type(c).__name__ == "_Layer" and c.name.startswith("p") and c.name.endswith("_pelvis")
        }
        assert pelvis_names == {"p0_pelvis", "p1_pelvis"}


# ---------------------------------------------------------------------------
# What the fake Nuke would draw.

def _at(curve, frame):
    return dict(curve.anim_keys)[frame]


def _camera_matrix(layer, frame):
    x = layer.getTransform()
    m = [[_at(x.getExtraMatrixAnimCurve(i, j), frame) for j in range(4)] for i in range(4)]
    # Rows/cols 0, 1, 3 carry the 2D homography; row/col 2 is z.
    assert (m[0][2], m[1][2], m[2], m[3][2]) == (0.0, 0.0, [0.0, 0.0, 1.0, 0.0], 0.0)
    return (m[0][0], m[0][1], m[0][3], m[1][0], m[1][1], m[1][3], m[3][0], m[3][1], m[3][3])


def _translate_rotate(layer, frame):
    x = layer.getTransform()
    tx = _at(x.getTranslationAnimCurve(0), frame)
    ty = _at(x.getTranslationAnimCurve(1), frame)
    rot = dict(x.getRotationAnimCurve(2).anim_keys).get(frame, 0.0)
    return tx, ty, rot


def _compose(camera, pelvis, part, frame, lx, ly):
    """Nuke's draw of a Y-up local point: M(pelvis + t + R(l))."""
    px, py, _ = _translate_rotate(pelvis, frame)
    tx, ty, rot = _translate_rotate(part, frame)
    r = math.radians(rot)
    rx, ry = math.cos(r) * lx - math.sin(r) * ly, math.sin(r) * lx + math.cos(r) * ly
    return _apply(_camera_matrix(camera, frame), px + tx + rx, py + ty + ry)


def _parts(node):
    camera = _child_layer(node["curves"].rootLayer, "camera_track")
    for pelvis in camera.children:
        for part in pelvis.children:
            yield camera, pelvis, part, _child_shape(part)


def _assert_recomposes(doc, node, curve_type):
    """Every on-curve point (and Bezier handle) Nuke would draw lands on the
    JSON's plate position, Y-flipped. Nuke evaluates the curve in the
    layer's space and maps it through the cascade."""
    H = doc.resolution[1]
    worst = 0.0
    checked = 0
    for camera, pelvis, part, shape in _parts(node):
        obj = doc.objects[part.name]
        for frame, fr in obj.frames.items():
            centers = [dict(cp.center.keys)[frame] for cp in shape.points]
            n = len(centers)
            for i, p in enumerate(fr.points):
                if curve_type == "bspline":
                    # Nuke's closed uniform cubic B-spline passes through
                    # (C[i-1] + 4 C[i] + C[i+1]) / 6.
                    (ax, ay), (bx, by), (cx, cy) = centers[i - 1], centers[i], centers[(i + 1) % n]
                    targets = [(((ax + 4 * bx + cx) / 6, (ay + 4 * by + cy) / 6), (p.x, p.y))]
                else:
                    cp = shape.points[i]
                    lt = dict(cp.leftTangent.keys)[frame]
                    rt = dict(cp.rightTangent.keys)[frame]
                    x, y = centers[i]
                    targets = [
                        ((x, y), (p.x, p.y)),
                        ((x + lt[0], y + lt[1]), (p.left_x, p.left_y)),
                        ((x + rt[0], y + rt[1]), (p.right_x, p.right_y)),
                    ]
                for (lx, ly), (jx, jy) in targets:
                    gx, gy = _compose(camera, pelvis, part, frame, lx, ly)
                    worst = max(worst, math.hypot(gx - jx, gy - (H - jy)))
                    checked += 1
    assert checked
    assert worst < ROUND_TRIP_TOLERANCE_PX, worst


def _write(tmp_path, data):
    p = tmp_path / "doc.json"
    p.write_text(json.dumps(data))
    return load_json(p)


class TestRecomposition:
    @pytest.mark.parametrize("curve_type", ["bspline", "bezier"])
    @pytest.mark.parametrize("fixture", ["v3_small.json", "v3_real_cut.json"])
    def test_knots_land_on_the_plate(self, fake_nuke, fixtures_dir, fixture, curve_type):
        doc = load_json(fixtures_dir / fixture)
        node = build_roto(doc, mode="hierarchical", curve_type=curve_type)
        _assert_recomposes(doc, node, curve_type)

    @pytest.mark.parametrize("curve_type", ["bspline", "bezier"])
    def test_perspective_camera_is_exact(self, fake_nuke, fixtures_dir, tmp_path, curve_type):
        data = json.loads((fixtures_dir / "v3_small.json").read_text())
        data["camera"]["2"]["H2d"] = [1.1, 0.05, 30.0, -0.04, 0.95, 20.0, 2e-4, 1e-4, 1.0]
        doc = _write(tmp_path, data)
        node = build_roto(doc, mode="hierarchical", curve_type=curve_type)
        camera = _child_layer(node["curves"].rootLayer, "camera_track")
        assert _camera_matrix(camera, 2)[6:8] != (0.0, 0.0)
        _assert_recomposes(doc, node, curve_type)

    def test_missing_data_is_held_not_dropped(self, fake_nuke, fixtures_dir, tmp_path):
        """A failed camera solve, a frame with both hips missing and a frame
        without a bone hold the previous good value -- the shape still lands
        on its plate position, and the transforms do not jump to identity."""
        data = json.loads((fixtures_dir / "v3_small.json").read_text())
        data["camera"]["2"]["source"] = "ecc-failed"
        data["persons"]["2"]["0"]["pelvis_px"] = [0.0, 0.0]
        del data["objects"]["p0:leg:R:thigh"]["frames"]["2"]["bone"]
        doc = _write(tmp_path, data)
        printed = []
        fake_nuke.tprint = printed.append
        node = build_roto(doc, mode="hierarchical", curve_type="bezier")
        _assert_recomposes(doc, node, "bezier")

        ((camera, pelvis, part, _),) = list(_parts(node))
        assert _camera_matrix(camera, 2) == _camera_matrix(camera, 1)
        # Pelvis held in plate pixels (502, 300) -> stabilised by the held camera.
        assert _translate_rotate(pelvis, 2) == _translate_rotate(pelvis, 1)
        assert any("held 3" in m for m in printed)


# ---------------------------------------------------------------------------
# Transform keys.

class TestTransforms:
    def test_v2_doc_t1_is_identity_on_every_frame(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        node = build_roto(doc, mode="hierarchical")
        camera = _child_layer(node["curves"].rootLayer, "camera_track")
        frames = sorted({f for o in doc.objects.values() for f in o.frames})
        for f in range(frames[0], frames[-1] + 1):
            assert _camera_matrix(camera, f) == pytest.approx(IDENTITY_H2D)

    def test_v3_doc_t1_has_per_frame_keys(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        node = build_roto(doc, mode="hierarchical")
        camera = _child_layer(node["curves"].rootLayer, "camera_track")
        keys = camera.getTransform().getExtraMatrixAnimCurve(0, 3).anim_keys
        # Y-down translate (2, 1) on frame 1 is (2, -1) in Y-up.
        assert keys == [(0, 0.0), (1, 2.0), (2, 4.0)]
        assert _at(camera.getTransform().getExtraMatrixAnimCurve(1, 3), 1) == pytest.approx(-1.0)

    def test_v3_doc_t3_rotation_lands_on_z_axis(self, fake_nuke, fixtures_dir):
        """v3_small has a bone from (150, 200) → (150, 400) (plate
        Y-down): straight down the screen, which is -90° in Y-up. Rotation
        must land on index 2 (Z-axis) — index 0 is X-axis which would
        rotate the roto out of the plate plane (the original PR #9 bug)."""
        doc = load_json(fixtures_dir / "v3_small.json")
        node = build_roto(doc, mode="hierarchical")
        ((_, _, part, _),) = list(_parts(node))
        xform = part.getTransform()
        assert _at(xform.getRotationAnimCurve(2), 0) == pytest.approx(-90.0, abs=1e-6)
        assert xform.getRotationAnimCurve(0).anim_keys == []


# ---------------------------------------------------------------------------
# Spline-knot bone-local projection.

class TestKnotProjection:
    def test_knots_stored_in_bone_local_coords(self, fake_nuke, fixtures_dir):
        """Knot (100, 200) on a vertical bone from (150, 200), pelvis at
        (500, 300): bone-local Y-up is the knot rotated into the bone's
        frame, not its plate position."""
        doc = load_json(fixtures_dir / "v3_small.json")
        node = build_roto(doc, mode="hierarchical", curve_type="bezier")
        ((_, _, _, shape),) = list(_parts(node))
        assert len(shape.points) == 4
        # The bone points down-screen (+90° in Y-down), so (-50, 0) from its
        # origin is (0, 50) in Y-down bone-local, (0, -50) in Y-up.
        assert shape.points[0].initial == pytest.approx((0.0, -50.0))
