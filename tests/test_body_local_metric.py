"""Tests for the body-local articulation metric (#279)."""

from __future__ import annotations

import math

import pytest

from rotobot_nuke import load_json
from rotobot_nuke.reader import LozengeDoc, LozengeFrame, LozengeObject, LozengePoint
from rotobot_nuke.undersample import (
    _person_max_radius,
    _state_vector_for_frame,
)


def _frame(points_rel, bone_len=200.0):
    """Build a LozengeFrame whose bone is a vertical segment of length
    ``bone_len`` at the origin, with knots at ``points_rel`` (bone-local
    coords; each entry is (axial, perpendicular))."""
    p0 = (0.0, 0.0)
    p1 = (0.0, bone_len)
    # bone axis is +y, so axial direction ux=(0,1); perpendicular uy=(-1,0)
    pts = []
    for axial, perp in points_rel:
        # knot = p0 + axial*ux + perp*uy; our ux=(0,1), uy=(-1,0)
        x = p0[0] + axial * 0.0 + perp * -1.0
        y = p0[1] + axial * 1.0 + perp * 0.0
        pts.append(LozengePoint(x, y, x, y, x, y))
    return LozengeFrame(points=pts, bone=(p0, p1))


class TestPersonMaxRadius:
    def test_empty_doc_returns_empty_map(self):
        doc = LozengeDoc(
            schema="lozenge_bezier_anim", schema_version=3, fps=24,
            resolution=(1920, 1080), objects={},
        )
        assert _person_max_radius(doc) == {}

    def test_single_segment_returns_max_perpendicular(self):
        """A single object with knots at perpendicular distances [5, 10, 7]
        from the bone axis should produce max_radius = 10."""
        obj = LozengeObject(
            key="p0:leg:R:thigh", person_id=0, body="leg", side="R",
            segment="thigh", skeleton_edge=(12, 10), closed=True,
            point_count=3, visibility={0: True},
            frames={0: _frame([(10, 5), (50, 10), (90, 7)])},
        )
        doc = LozengeDoc(
            schema="lozenge_bezier_anim", schema_version=3, fps=24,
            resolution=(1920, 1080), objects={obj.key: obj},
        )
        radii = _person_max_radius(doc)
        assert set(radii.keys()) == {0}
        assert radii[0] == pytest.approx(10.0, abs=1e-6)

    def test_max_across_objects_per_person(self):
        """Multiple objects for one person should return the max of all of
        them, not a per-object value."""
        o1 = LozengeObject(
            key="p0:leg:R:thigh", person_id=0, body="leg", side="R",
            segment="thigh", skeleton_edge=(12, 10), closed=True,
            point_count=1, visibility={}, frames={0: _frame([(50, 20)])},
        )
        o2 = LozengeObject(
            key="p0:body:C:body", person_id=0, body="body", side="C",
            segment="body", skeleton_edge=(1, 2), closed=True,
            point_count=1, visibility={}, frames={0: _frame([(50, 100)])},
        )
        doc = LozengeDoc(
            schema="lozenge_bezier_anim", schema_version=3, fps=24,
            resolution=(1920, 1080),
            objects={o1.key: o1, o2.key: o2},
        )
        radii = _person_max_radius(doc)
        assert radii == pytest.approx({0: 100.0})

    def test_separate_people_separate_radii(self):
        o0 = LozengeObject(
            key="p0:leg:R:thigh", person_id=0, body="leg", side="R",
            segment="thigh", skeleton_edge=(12, 10), closed=True,
            point_count=1, visibility={}, frames={0: _frame([(50, 25)])},
        )
        o1 = LozengeObject(
            key="p1:leg:R:thigh", person_id=1, body="leg", side="R",
            segment="thigh", skeleton_edge=(12, 10), closed=True,
            point_count=1, visibility={}, frames={0: _frame([(50, 75)])},
        )
        doc = LozengeDoc(
            schema="lozenge_bezier_anim", schema_version=3, fps=24,
            resolution=(1920, 1080),
            objects={o0.key: o0, o1.key: o1},
        )
        radii = _person_max_radius(doc)
        assert radii == pytest.approx({0: 25.0, 1: 75.0})

    def test_degenerate_zero_length_bone_skipped(self):
        """A bone whose endpoints coincide produces no useful perpendicular
        signal — skipped from the max."""
        f = LozengeFrame(
            points=[LozengePoint(0, 0, 0, 0, 0, 0)],
            bone=((100.0, 100.0), (100.0, 100.0)),
        )
        obj = LozengeObject(
            key="p0:leg:R:thigh", person_id=0, body="leg", side="R",
            segment="thigh", skeleton_edge=(12, 10), closed=True,
            point_count=1, visibility={}, frames={0: f},
        )
        doc = LozengeDoc(
            schema="lozenge_bezier_anim", schema_version=3, fps=24,
            resolution=(1920, 1080), objects={obj.key: obj},
        )
        assert _person_max_radius(doc) == {}


class TestBodyScaleNormalisation:
    def test_default_scale_is_pixel_identity(self):
        """body_scale defaults to 1.0 so legacy call sites get unchanged
        pixel-space output."""
        f = _frame([(50, 20), (100, 30)])
        v = _state_vector_for_frame(f)
        # First knot at axial=50 perp=20; local coords should be (50, 20).
        # State vector shape: [p0x, p0y, angle, lx_0, ly_0, lx_1, ly_1]
        assert v is not None
        assert v[0] == 0.0  # p0x
        assert v[1] == 0.0  # p0y
        # Last two pairs are the knots in bone-local coords
        assert v[3] == pytest.approx(50.0)   # lx_0 axial
        assert v[4] == pytest.approx(20.0)   # ly_0 perp
        assert v[5] == pytest.approx(100.0)
        assert v[6] == pytest.approx(30.0)

    def test_body_scale_divides_spatial_components(self):
        """With body_scale=10, every spatial component is / 10. Rotation
        stays in degrees."""
        f = _frame([(50, 20), (100, 30)])
        v = _state_vector_for_frame(f, body_scale=10.0)
        assert v is not None
        # p0 unchanged (0,0) regardless
        assert v[0] == 0.0 and v[1] == 0.0
        # Angle component stays untouched (atan2 of bone direction)
        angle = v[2]
        v_unit = _state_vector_for_frame(f, body_scale=1.0)
        assert v[2] == v_unit[2]
        # Knot coords are divided by scale
        assert v[3] == pytest.approx(5.0)
        assert v[4] == pytest.approx(2.0)
        assert v[5] == pytest.approx(10.0)
        assert v[6] == pytest.approx(3.0)

    def test_pelvis_subtraction_still_cancels_out_of_locals(self):
        """Local knot coords are invariant to pelvis_px — pelvis cancels
        when projecting into bone-local."""
        f = _frame([(50, 20)])
        v1 = _state_vector_for_frame(f, pelvis_px=None, body_scale=1.0)
        v2 = _state_vector_for_frame(f, pelvis_px=(1000.0, 500.0), body_scale=1.0)
        # Only p0x, p0y change (shifted by pelvis). Angle + knot coords stay.
        assert v2[0] == pytest.approx(-1000.0)
        assert v2[1] == pytest.approx(-500.0)
        assert v2[2] == pytest.approx(v1[2])
        assert v2[3:] == pytest.approx(v1[3:])
