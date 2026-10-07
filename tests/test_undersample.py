"""Tests for ``rotobot_nuke.undersample`` — RDP + file-level helpers."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from rotobot_nuke import (
    DEFAULT_TOLERANCE,
    TOLERANCE_AGGRESSIVE,
    TOLERANCE_BALANCED,
    TOLERANCE_CONSERVATIVE,
    MissingResolutionError,
    load_json,
    rdp_reduction,
    undersample_doc,
    undersample_json,
    undersample_object,
)


# ---------------------------------------------------------------------------
# RDP (pure algorithm)


class TestRDPReduction:
    def test_empty_input(self):
        assert rdp_reduction([], 1.0) == []

    def test_single_point(self):
        assert rdp_reduction([[0.0, 0.0]], 1.0) == [0]

    def test_two_points_always_both_kept(self):
        assert rdp_reduction([[0.0, 0.0], [1.0, 1.0]], 1.0) == [0, 1]

    def test_colinear_points_collapse_to_endpoints(self):
        pts = [[float(i), 2.0 * i] for i in range(20)]
        assert rdp_reduction(pts, 0.5) == [0, 19]

    def test_outlier_in_the_middle_is_kept(self):
        pts = [[0.0, 0.0], [1.0, 0.0], [2.0, 10.0], [3.0, 0.0], [4.0, 0.0]]
        kept = rdp_reduction(pts, 1.0)
        assert 2 in kept
        assert 0 in kept and 4 in kept

    def test_very_tight_tolerance_keeps_everything(self):
        pts = [[0.0, 0.0], [1.0, 0.1], [2.0, -0.1], [3.0, 0.0]]
        assert rdp_reduction(pts, 0.0001) == [0, 1, 2, 3]

    def test_zero_tolerance_keeps_everything(self):
        pts = [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]
        # tolerance <= 0 is treated as a no-op (upstream convention).
        assert rdp_reduction(pts, 0.0) == [0, 1, 2]

    def test_higher_dimensional(self):
        pts = [[float(i), float(i), float(i), float(i)] for i in range(10)]
        assert rdp_reduction(pts, 0.5) == [0, 9]


# ---------------------------------------------------------------------------
# undersample_object


class TestUndersampleObject:
    def test_bone_less_v1_object_untouched(self, fixtures_dir):
        """v1 frames have no ``bone``; undersample must leave them alone."""
        doc = load_json(
            fixtures_dir / "v1_small.json", resolution=(1920, 1080)
        )
        obj = doc.objects["p0:arm:L:upper"]
        new_obj, before, after = undersample_object(obj, tolerance=1.0)
        assert before == after == len(obj.frames)
        assert new_obj.frames.keys() == obj.frames.keys()

    def test_two_frames_cannot_be_reduced(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        obj = doc.objects["p0:leg:R:thigh"]
        assert len(obj.frames) == 2
        _, before, after = undersample_object(obj, tolerance=100.0)
        assert before == after == 2

    def test_zero_tolerance_is_no_op(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        obj = doc.objects["p0:leg:R:thigh"]
        new_obj, _, _ = undersample_object(obj, tolerance=0.0)
        assert new_obj is obj  # exact object identity, no copy


class TestUndersampleDoc:
    def test_bone_less_v1_doc_untouched(self, fixtures_dir):
        doc = load_json(
            fixtures_dir / "v1_small.json", resolution=(1920, 1080)
        )
        new = undersample_doc(doc, tolerance=1.0)
        assert new.objects.keys() == doc.objects.keys()
        for key in doc.objects:
            assert new.objects[key].frames.keys() == doc.objects[key].frames.keys()

    def test_resolution_and_fps_preserved(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        new = undersample_doc(doc, tolerance=1.0)
        assert new.resolution == doc.resolution
        assert new.fps == doc.fps
        assert new.schema_version == doc.schema_version

    def test_point_geometry_untouched_on_kept_frames(self, fixtures_dir):
        """Undersampling must never alter geometry — only drop frames."""
        doc = load_json(fixtures_dir / "v2_small.json")
        new = undersample_doc(doc, tolerance=1.0)
        for key, obj in new.objects.items():
            for f, frame in obj.frames.items():
                orig = doc.objects[key].frames[f]
                assert frame.points == orig.points
                assert frame.bone == orig.bone


# ---------------------------------------------------------------------------
# undersample_json (file-to-file wrapper)


class TestUndersampleJsonFile:
    def test_file_roundtrip_v2(self, fixtures_dir, tmp_path):
        inp = fixtures_dir / "v2_small.json"
        out = tmp_path / "out.json"
        report = undersample_json(inp, out, tolerance=1.0)
        assert set(report.keys()) == {"p0:leg:R:thigh"}
        rebuilt = json.loads(out.read_text())
        # Only ``frames`` content may change — everything else stays.
        orig = json.loads(inp.read_text())
        for k in ("schema", "schema_version", "fps", "resolution", "width", "height"):
            assert rebuilt[k] == orig[k]
        obj_key = "p0:leg:R:thigh"
        assert rebuilt["objects"][obj_key]["visibility"] == orig["objects"][obj_key]["visibility"]

    def test_file_v2_hd_multi_object(self, fixtures_dir, tmp_path):
        inp = fixtures_dir / "v2_hd_1920_1080.json"
        out = tmp_path / "out.json"
        report = undersample_json(inp, out, tolerance=1.0)
        assert set(report.keys()) == {
            "p0:leg:R:thigh",
            "p0:hand:R:B_index_tip",
            "p1:leg:L:shin",
        }

    def test_v1_file_untouched_by_cli_wrapper(self, fixtures_dir, tmp_path):
        """v1 files have no bone → file-level wrapper keeps every frame."""
        inp = fixtures_dir / "v1_small.json"
        out = tmp_path / "out.json"
        report = undersample_json(inp, out, tolerance=1.0)
        for before, after in report.values():
            assert before == after


# ---------------------------------------------------------------------------
# Build-roto plumbing (undersample_tolerance kwarg on build_roto)


class TestBuildRotoWithUndersample:
    def test_build_roto_passes_tolerance_through(
        self, fake_nuke, fixtures_dir
    ):
        """A positive tolerance must not raise even when the doc has
        only 2 frames (can't be reduced below 2)."""
        doc = load_json(fixtures_dir / "v2_small.json")
        from rotobot_nuke.importer import build_roto

        build_roto(doc, undersample_tolerance=TOLERANCE_BALANCED)
        # No crash + at least one shape got through
        root_layer = fake_nuke.nodes.created[0]["curves"].rootLayer
        shapes = [
            c
            for layer in root_layer.children
            for c in _flatten(layer)
            if type(c).__name__ == "_Shape"
        ]
        assert len(shapes) == 1

    def test_build_roto_none_tolerance_no_op(self, fake_nuke, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        from rotobot_nuke.importer import build_roto

        build_roto(doc, undersample_tolerance=None)
        shape = _only_shape(fake_nuke)
        # Every original frame still has a center keyframe
        assert len(shape.points[0].center.keys) == len(doc.objects["p0:leg:R:thigh"].frames)


# ---------------------------------------------------------------------------
# Tolerance presets


class TestTolerancePresets:
    def test_preset_values_ordered(self):
        assert (
            TOLERANCE_CONSERVATIVE
            < TOLERANCE_BALANCED
            < TOLERANCE_AGGRESSIVE
        )

    def test_default_is_balanced(self):
        assert DEFAULT_TOLERANCE == TOLERANCE_BALANCED


# ---------------------------------------------------------------------------
# Helpers


def _flatten(layer):
    yield layer
    if hasattr(layer, "children"):
        for c in layer.children:
            yield from _flatten(c)


def _only_shape(fake_nuke):
    root_layer = fake_nuke.nodes.created[0]["curves"].rootLayer
    for item in _flatten(root_layer):
        if type(item).__name__ == "_Shape":
            return item
    raise AssertionError("no shape created")
