"""Tests for ``rotobot_nuke.importer`` — uses the fake Nuke from conftest."""

from __future__ import annotations

import pytest

from rotobot_nuke import load_json
from rotobot_nuke.importer import build_roto


def _layer_names(parent):
    return [c.name for c in parent.children if type(c).__name__ == "_Layer"]


def _collect_shapes(parent):
    """Walk the layer tree recursively, yielding every _Shape found."""
    for c in parent.children:
        if type(c).__name__ == "_Shape":
            yield c
        elif type(c).__name__ == "_Layer":
            yield from _collect_shapes(c)


class TestBuildRotoBasics:
    def test_v2_fixture_creates_roto_node(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        node = build_roto(doc)
        assert fake_nuke.nodes.created == [node]
        assert node.name() == "Tokgan_Roto"

    def test_custom_roto_name(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        node = build_roto(doc, roto_name="MyRoto")
        assert node.name() == "MyRoto"

    def test_bad_curve_type_rejected(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        with pytest.raises(ValueError):
            build_roto(doc, curve_type="catmull")

    def test_set_project_fps_defaults_false(self, fake_nuke, fixtures_dir):
        """Importing a roto must not mutate project FPS unless asked."""
        doc = load_json(fixtures_dir / "v2_small.json")
        root = fake_nuke.root()
        before = root["fps"].value()
        build_roto(doc)
        assert root["fps"].value() == before

    def test_set_project_fps_true_pokes_root(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        build_roto(doc, set_project_fps=True)
        assert fake_nuke.root()["fps"].value() == doc.fps


class TestHierarchy:
    def test_v2_single_person_single_segment_hierarchy(
        self, fake_nuke, fixtures_dir
    ):
        """Hierarchy for one ``p0:leg:R:thigh`` object:
        root → p0 → p0_leg → p0_leg_R → p0_thigh → Shape."""
        doc = load_json(fixtures_dir / "v2_small.json")
        node = build_roto(doc)
        root_layer = node["curves"].rootLayer

        assert _layer_names(root_layer) == ["p0"]
        p0 = root_layer.children[0]
        assert _layer_names(p0) == ["p0_leg"]
        p0_leg = p0.children[0]
        assert _layer_names(p0_leg) == ["p0_leg_R"]
        p0_leg_R = p0_leg.children[0]
        assert _layer_names(p0_leg_R) == ["p0_thigh"]
        p0_thigh = p0_leg_R.children[0]

        shapes = [c for c in p0_thigh.children if type(c).__name__ == "_Shape"]
        assert len(shapes) == 1  # historical dup-append bug is fixed
        assert shapes[0].name == "p0:leg:R:thigh:Shape"
        assert shapes[0].curve_type == "bspline"

    def test_multi_person_multi_shape(self, fake_nuke, fixtures_dir):
        """Three objects: p0:leg:R:thigh, p0:hand:R:B_index_tip, p1:leg:L:shin.
        Expect two top-level person layers (p0, p1) and three Shape nodes."""
        doc = load_json(fixtures_dir / "v2_hd_1920_1080.json")
        node = build_roto(doc)
        root_layer = node["curves"].rootLayer

        top = _layer_names(root_layer)
        assert sorted(top) == ["p0", "p1"]

        shapes = list(_collect_shapes(root_layer))
        shape_names = sorted(s.name for s in shapes)
        assert shape_names == [
            "p0:hand:R:B_index_tip:Shape",
            "p0:leg:R:thigh:Shape",
            "p1:leg:L:shin:Shape",
        ]

    def test_hand_segment_builds_finger_subgroup(self, fake_nuke, fixtures_dir):
        """Hand parts matching ``A_thumb/B_index/C_middle/D_ring/E_pinky``
        get an extra ``p0_fingers_R → p0_B_index_R → p0_B_index_tip`` nesting."""
        doc = load_json(fixtures_dir / "v2_hd_1920_1080.json")
        node = build_roto(doc)
        root_layer = node["curves"].rootLayer

        p0 = next(c for c in root_layer.children if c.name == "p0")
        p0_hand = next(c for c in p0.children if c.name == "p0_hand")
        p0_hand_R = next(c for c in p0_hand.children if c.name == "p0_hand_R")

        # Finger subgroup
        fingers = next(c for c in p0_hand_R.children if c.name == "p0_fingers_R")
        b_index = next(c for c in fingers.children if c.name == "p0_B_index_R")
        tip = next(c for c in b_index.children if c.name == "p0_B_index_tip")
        shapes = [c for c in tip.children if type(c).__name__ == "_Shape"]
        assert len(shapes) == 1


class TestControlPointsAndKeyframes:
    def test_control_points_are_y_flipped_against_doc_resolution(
        self, fake_nuke, fixtures_dir
    ):
        """Y should flip against doc.resolution[1] (1080), not Nuke project
        format. First point in v2_small is (100, 200) → (100, 880)."""
        doc = load_json(fixtures_dir / "v2_small.json")
        build_roto(doc)
        shape = next(_collect_shapes(fake_nuke.nodes.created[0]["curves"].rootLayer))
        H = doc.resolution[1]
        assert H == 1080

        first = shape.points[0]
        assert first.initial == (100.0, H - 200.0)
        assert first.initial == (100.0, 880.0)

    def test_y_flip_uses_override_resolution_when_supplied(
        self, fake_nuke, fixtures_dir
    ):
        doc = load_json(fixtures_dir / "v2_small.json", resolution=(1920, 2160))
        build_roto(doc)
        shape = next(_collect_shapes(fake_nuke.nodes.created[0]["curves"].rootLayer))
        # First point y=200 → 2160-200=1960 (not 1080-200=880)
        assert shape.points[0].initial == (100.0, 1960.0)

    def test_bspline_mode_does_not_write_tangent_keyframes(
        self, fake_nuke, fixtures_dir
    ):
        doc = load_json(fixtures_dir / "v2_small.json")
        build_roto(doc, curve_type="bspline")
        shape = next(_collect_shapes(fake_nuke.nodes.created[0]["curves"].rootLayer))
        assert shape.points[0].leftTangent.keys == []
        assert shape.points[0].rightTangent.keys == []
        # But centers DO get animated
        assert len(shape.points[0].center.keys) == 2  # 2 frames

    def test_bezier_mode_writes_tangent_keyframes(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        build_roto(doc, curve_type="bezier")
        shape = next(_collect_shapes(fake_nuke.nodes.created[0]["curves"].rootLayer))
        assert len(shape.points[0].leftTangent.keys) == 2
        assert len(shape.points[0].rightTangent.keys) == 2
        # Tangent values are vertex-relative deltas, not absolute positions.
        frame, (dlx, dly) = shape.points[0].leftTangent.keys[0]
        assert frame == 0
        # left_x=95, x=100 → dlx = -5; left_y=200, y=200 → H-ly=H-y → dly=0.
        assert dlx == -5.0
        assert dly == 0.0


class TestVisibility:
    def test_hidden_frame_sets_shape_invisible(self, fake_nuke, fixtures_dir):
        """v2_small has visibility[3]=0 — the fixture doesn't have a frame 3
        in its ``frames`` map though, so visibility is set from the
        ``visibility`` dict only."""
        doc = load_json(fixtures_dir / "v2_small.json")
        build_roto(doc)
        shape = next(_collect_shapes(fake_nuke.nodes.created[0]["curves"].rootLayer))
        vis_at_3 = [v for (f, v) in shape.visibility_keys if f == 3]
        assert vis_at_3 == [False]
