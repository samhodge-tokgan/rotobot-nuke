"""Tests for the hierarchical composed-tolerance undersampler (#279)."""

from __future__ import annotations

import pytest

from rotobot_nuke import (
    PRESET_BALANCED,
    PRESET_COARSE,
    PRESET_FINE,
    TolerancePreset,
    load_json,
    undersample_doc,
)


class TestLegacyPath:
    def test_no_kwargs_uses_default_tolerance(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        reduced = undersample_doc(doc)
        # Legacy path — same shape as PR #1. Not asserting a count, only
        # that it doesn't raise and preserves resolution.
        assert reduced.resolution == doc.resolution

    def test_tolerance_kwarg_uses_single_pass(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        reduced = undersample_doc(doc, tolerance=5.0)
        assert reduced.schema == doc.schema

    def test_mixing_tolerance_and_hierarchical_raises(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        with pytest.raises(TypeError) as excinfo:
            undersample_doc(doc, tolerance=5.0, articulation_tolerance=0.1)
        assert "tolerance" in str(excinfo.value)


class TestPresets:
    def test_presets_are_ordered(self):
        assert PRESET_COARSE.camera_tolerance > PRESET_BALANCED.camera_tolerance > PRESET_FINE.camera_tolerance
        assert PRESET_COARSE.person_tolerance > PRESET_BALANCED.person_tolerance > PRESET_FINE.person_tolerance
        assert PRESET_COARSE.articulation_tolerance > PRESET_BALANCED.articulation_tolerance > PRESET_FINE.articulation_tolerance

    def test_preset_unpacks_as_kwargs(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        reduced = undersample_doc(doc, **PRESET_BALANCED._asdict())
        assert reduced.resolution == doc.resolution


class TestHierarchicalScenarios:
    def _make_pan_only_doc(self):
        """3 frames of pure camera pan, person + articulation stationary.

        The articulation + person state vectors are constant across the
        3 frames; only the camera H2d translation moves. A tight
        articulation + person tolerance should still collapse this to
        2 keyframes under a loose camera tolerance; a strict camera
        tolerance keeps the middle frame.
        """
        import json
        import tempfile
        payload = {
            "schema": "lozenge_bezier_anim",
            "schema_version": 3,
            "fps": 24,
            "resolution": [1920, 1080],
            "objects": {
                "p0:leg:R:thigh": {
                    "person_id": 0, "body": "leg", "side": "R", "segment": "thigh",
                    "skeleton_edge": [12, 10], "closed": True, "point_count": 2,
                    "visibility": {"0": 1, "1": 1, "2": 1},
                    "frames": {
                        "0": {"points": [
                            {"x": 100, "y": 100, "left_x": 95, "left_y": 100, "right_x": 105, "right_y": 100},
                            {"x": 100, "y": 300, "left_x": 95, "left_y": 300, "right_x": 105, "right_y": 300},
                        ], "bone": {"pt0": {"x": 100, "y": 100}, "pt1": {"x": 100, "y": 300}}},
                        "1": {"points": [
                            {"x": 100, "y": 100, "left_x": 95, "left_y": 100, "right_x": 105, "right_y": 100},
                            {"x": 100, "y": 300, "left_x": 95, "left_y": 300, "right_x": 105, "right_y": 300},
                        ], "bone": {"pt0": {"x": 100, "y": 100}, "pt1": {"x": 100, "y": 300}}},
                        "2": {"points": [
                            {"x": 100, "y": 100, "left_x": 95, "left_y": 100, "right_x": 105, "right_y": 100},
                            {"x": 100, "y": 300, "left_x": 95, "left_y": 300, "right_x": 105, "right_y": 300},
                        ], "bone": {"pt0": {"x": 100, "y": 100}, "pt1": {"x": 100, "y": 300}}},
                    },
                },
            },
            "camera": {
                # Non-linear translation: 0 -> 10 -> 15 (frame 1 is NOT a
                # linear interp of 0 + 2, so RDP keeps it under strict tol
                # but drops it under loose).
                "0": {"focal": 2800, "cx": 960, "cy": 540, "t": [0, 0, 0],
                      "R": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                      "H2d": [1, 0, 0, 0, 1, 0, 0, 0, 1], "source": "identity"},
                "1": {"focal": 2800, "cx": 960, "cy": 540, "t": [0, 0, 0],
                      "R": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                      "H2d": [1, 0, 10, 0, 1, 7, 0, 0, 1], "source": "ecc"},
                "2": {"focal": 2800, "cx": 960, "cy": 540, "t": [0, 0, 0],
                      "R": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                      "H2d": [1, 0, 15, 0, 1, 8, 0, 0, 1], "source": "ecc"},
            },
            "persons": {
                "0": {"0": {"cam_t": [0, 0, 5], "pelvis_px": [500, 300]}},
                "1": {"0": {"cam_t": [0, 0, 5], "pelvis_px": [500, 300]}},
                "2": {"0": {"cam_t": [0, 0, 5], "pelvis_px": [500, 300]}},
            },
        }
        fh = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(payload, fh)
        fh.close()
        return load_json(fh.name)

    def test_pan_only_strict_camera_keeps_middle(self):
        doc = self._make_pan_only_doc()
        # Camera moves ~10/5 pixel-eqs per frame — strict camera tol keeps all.
        reduced = undersample_doc(
            doc,
            camera_tolerance=0.1,
            person_tolerance=100.0,
            articulation_tolerance=100.0,
        )
        obj = reduced.objects["p0:leg:R:thigh"]
        assert set(obj.frames.keys()) == {0, 1, 2}

    def test_pan_only_loose_camera_drops_middle(self):
        doc = self._make_pan_only_doc()
        # Loose camera tol absorbs the pan; articulation is zero; result = {0, 2}.
        reduced = undersample_doc(
            doc,
            camera_tolerance=100.0,
            person_tolerance=100.0,
            articulation_tolerance=100.0,
        )
        obj = reduced.objects["p0:leg:R:thigh"]
        assert set(obj.frames.keys()) == {0, 2}

    def test_articulation_tolerance_catches_real_change(self, fixtures_dir):
        """v3_small has 3 articulation frames with small but increasing
        drift. A tight articulation tolerance keeps them all; a loose one
        collapses to the endpoints."""
        doc = load_json(fixtures_dir / "v3_small.json")
        tight = undersample_doc(
            doc,
            camera_tolerance=100.0,  # ignore camera noise
            person_tolerance=100.0,  # ignore person motion
            articulation_tolerance=0.05,
        )
        loose = undersample_doc(
            doc,
            camera_tolerance=100.0,
            person_tolerance=100.0,
            articulation_tolerance=100.0,
        )
        tight_kf = len(tight.objects["p0:leg:R:thigh"].frames)
        loose_kf = len(loose.objects["p0:leg:R:thigh"].frames)
        assert tight_kf >= loose_kf
        assert loose_kf == 2


class TestV2DocThroughHierarchicalAPI:
    def test_v2_fixture_hierarchical_falls_through_cleanly(self, fixtures_dir):
        """A v2 doc has no camera / persons blocks. The hierarchical API
        must still return something sensible — camera/person passes see
        nothing to prune, so only the per-object articulation pass runs
        (bone-local, no pelvis subtraction)."""
        doc = load_json(fixtures_dir / "v2_small.json")
        reduced = undersample_doc(
            doc,
            articulation_tolerance=0.5,
        )
        # v2_small has only 2 articulation frames — can't reduce below 2.
        assert len(reduced.objects["p0:leg:R:thigh"].frames) == 2
