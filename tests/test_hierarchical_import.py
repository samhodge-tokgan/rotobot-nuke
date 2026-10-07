"""Tests for the hierarchical Nuke import path (`build_roto(mode='hierarchical')`).

Verifies the three-transform cascade (camera_track → pelvis →
body-part), the H2d → affine decomposition, and the bone-local
projection of spline knots. Design doc:
``docs/design/hierarchical-import.md``.
"""

from __future__ import annotations

import math

import pytest

from rotobot_nuke import load_json
from rotobot_nuke.importer import (
    _affine_decompose,
    _conjugate_h2d_yflip,
    _invert_affine,
    _perspective_magnitude,
    build_roto,
)


IDENTITY_H2D = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Pure math helpers.

class TestAffineMath:
    def test_identity_decomposes_to_identity_params(self):
        tx, ty, rot_deg, sx, sy = _affine_decompose(IDENTITY_H2D)
        assert tx == pytest.approx(0.0)
        assert ty == pytest.approx(0.0)
        assert rot_deg == pytest.approx(0.0)
        assert sx == pytest.approx(1.0)
        assert sy == pytest.approx(1.0)

    def test_translation_only_decomposes_cleanly(self):
        # Y-up translation of (5, 7): already Y-up input.
        H2d = (1.0, 0.0, 5.0, 0.0, 1.0, 7.0, 0.0, 0.0, 1.0)
        tx, ty, rot_deg, sx, sy = _affine_decompose(H2d)
        assert tx == pytest.approx(5.0)
        assert ty == pytest.approx(7.0)
        assert rot_deg == pytest.approx(0.0)
        assert sx == pytest.approx(1.0)
        assert sy == pytest.approx(1.0)

    def test_scale_decomposes(self):
        H2d = (2.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 1.0)
        tx, ty, rot_deg, sx, sy = _affine_decompose(H2d)
        assert sx == pytest.approx(2.0)
        assert sy == pytest.approx(3.0)

    def test_rotation_decomposes(self):
        theta = math.radians(30)
        H2d = (
            math.cos(theta), -math.sin(theta), 0.0,
            math.sin(theta), math.cos(theta), 0.0,
            0.0, 0.0, 1.0,
        )
        tx, ty, rot_deg, sx, sy = _affine_decompose(H2d)
        assert rot_deg == pytest.approx(30.0, abs=1e-9)
        assert sx == pytest.approx(1.0, abs=1e-9)
        assert sy == pytest.approx(1.0, abs=1e-9)

    def test_yflip_conjugation_identity_stays_identity(self):
        yup = _conjugate_h2d_yflip(IDENTITY_H2D, H=1080)
        tx, ty, rot_deg, sx, sy = _affine_decompose(yup)
        assert tx == pytest.approx(0.0)
        assert ty == pytest.approx(0.0)
        assert rot_deg == pytest.approx(0.0)
        assert sx == pytest.approx(1.0)
        assert sy == pytest.approx(1.0)

    def test_yflip_conjugation_pure_translation(self):
        """A pure Y-down translation of (5, 7) becomes (5, -7) in Y-up
        (plus any image-height offset absorbed into the decomposition)."""
        H2d = (1.0, 0.0, 5.0, 0.0, 1.0, 7.0, 0.0, 0.0, 1.0)
        yup = _conjugate_h2d_yflip(H2d, H=1080)
        tx, ty, _rot, _sx, _sy = _affine_decompose(yup)
        assert tx == pytest.approx(5.0)
        assert ty == pytest.approx(-7.0)

    def test_perspective_magnitude_detects_nonzero_pq(self):
        H2d = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.001, 0.0, 1.0)
        assert _perspective_magnitude(H2d) == pytest.approx(0.001)
        assert _perspective_magnitude(IDENTITY_H2D) == 0.0

    def test_invert_affine_translation_only_round_trip(self):
        # Apply (tx=10, ty=20), then invert.
        inverted = _invert_affine(15.0, 25.0, 10.0, 20.0, 0.0, 1.0, 1.0)
        # Inverse of a translation sends (15, 25) to (5, 5).
        assert inverted == pytest.approx((5.0, 5.0))

    def test_invert_affine_round_trip(self):
        # (tx, ty, rot=30deg, sx=2, sy=2) applied to (0, 0) gives (tx, ty).
        # Inverting (tx, ty) with the same params gives back (0, 0).
        tx, ty, rot_deg = 10.0, 20.0, 30.0
        sx, sy = 2.0, 2.0
        back = _invert_affine(tx, ty, tx, ty, rot_deg, sx, sy)
        assert back == pytest.approx((0.0, 0.0), abs=1e-9)


# ---------------------------------------------------------------------------
# Build-roto dispatch.

class TestDispatch:
    def test_bad_mode_rejected(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        with pytest.raises(ValueError, match="mode"):
            build_roto(doc, mode="other")

    def test_mode_legacy_is_default(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
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
        # Direct child names under pelvis — flat, no grouping layers.
        child_names = {c.name for c in pelvis.children if type(c).__name__ == "_Layer"}
        assert "p0_leg" not in child_names
        assert "p0_leg_R" not in child_names
        # Full-colon-separated keys instead.
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
# Transform cascade math.

class TestTransforms:
    def test_v2_doc_t1_at_identity(self, fake_nuke, fixtures_dir):
        """v2 docs have no camera block; T1 should be set to identity
        at frame 0 (no per-frame data to differentiate)."""
        doc = load_json(fixtures_dir / "v2_small.json")
        node = build_roto(doc, mode="hierarchical")
        camera = _child_layer(node["curves"].rootLayer, "camera_track")
        xform = camera.getTransform()
        tx_keys = xform.getTranslationAnimCurve(0).anim_keys
        assert len(tx_keys) == 1
        frame, val = tx_keys[0]
        assert frame == 0 and val == 0.0

    def test_v3_doc_t1_has_per_frame_keys(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        node = build_roto(doc, mode="hierarchical")
        camera = _child_layer(node["curves"].rootLayer, "camera_track")
        xform = camera.getTransform()
        tx_keys = xform.getTranslationAnimCurve(0).anim_keys
        # v3_small has 3 camera frames; expect at least that many keys.
        assert len(tx_keys) == 3
        frames = {f for f, _ in tx_keys}
        assert frames == {0, 1, 2}

    def test_v3_doc_t3_rotation_matches_bone(self, fake_nuke, fixtures_dir):
        """v3_small has a bone from (150, 200) → (150, 400) (plate
        Y-down). In Y-up: (150, 880) → (150, 680). Direction: (0, -200)
        = angle atan2(-200, 0) = -90°. T3 rotation key at frame 0
        should match."""
        doc = load_json(fixtures_dir / "v3_small.json")
        node = build_roto(doc, mode="hierarchical")
        root = node["curves"].rootLayer
        camera = _child_layer(root, "camera_track")
        pelvis = _child_layer(camera, "p0_pelvis")
        part = _child_layer(pelvis, "p0:leg:R:thigh")
        xform = part.getTransform()
        rot_keys = xform.getRotationAnimCurve(0).anim_keys
        # One key per frame; frame-0 rotation should be ~-90 degrees.
        first_rot = next(v for f, v in rot_keys if f == 0)
        assert first_rot == pytest.approx(-90.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Spline-knot bone-local projection.

class TestKnotProjection:
    def test_knots_stored_in_bone_local_coords(
        self, fake_nuke, fixtures_dir
    ):
        """For a knot at plate (100, 200) with bone origin at plate
        (150, 200) and bone axis vertical, bone-local coords should be:
        y-flipped knot = (100, 880), bone origin (y-flipped) at (150, 880),
        pelvis_cf subtracted etc. We just verify that the knot's
        bone-local x,y differs from its raw plate position — proving
        the projection has been applied."""
        doc = load_json(fixtures_dir / "v3_small.json")
        node = build_roto(doc, mode="hierarchical")
        root = node["curves"].rootLayer
        camera = _child_layer(root, "camera_track")
        pelvis = _child_layer(camera, "p0_pelvis")
        part = _child_layer(pelvis, "p0:leg:R:thigh")
        shape = _child_shape(part)
        assert shape is not None
        assert len(shape.points) == 4
        # The initial AnimControlPoint position is in bone-local coords.
        # The raw plate position was (100, 200); legacy would have placed
        # it at (100, 880) in Y-up. Hierarchical should be different.
        bl_x, bl_y = shape.points[0].initial
        assert (bl_x, bl_y) != (100.0, 880.0)
