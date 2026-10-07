"""Tests for the v3 reader additions (#279): camera + persons parsing."""

from __future__ import annotations

import json

import pytest

from rotobot_nuke import CameraFrame, PersonFrame, load_json


class TestV3Camera:
    def test_camera_block_parsed(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        assert doc.schema_version == 3
        assert doc.camera is not None
        assert sorted(doc.camera.keys()) == [0, 1, 2]
        for frame_cam in doc.camera.values():
            assert isinstance(frame_cam, CameraFrame)
            assert frame_cam.focal == 2800.0
            assert frame_cam.R is not None
            assert len(frame_cam.R) == 9
            assert frame_cam.H2d is not None
            assert len(frame_cam.H2d) == 9

    def test_camera_source_tag_preserved(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        sources = {f: c.source for f, c in doc.camera.items()}
        assert sources == {0: "identity", 1: "ecc", 2: "ecc"}

    def test_h2d_translation_captured(self, fixtures_dir):
        """H2d in frame 2 encodes (dx=4, dy=2) relative to frame 0 — the
        parser must preserve those offsets verbatim."""
        doc = load_json(fixtures_dir / "v3_small.json")
        h2 = doc.camera[2].H2d
        assert h2[2] == 4.0  # translation x
        assert h2[5] == 2.0  # translation y

    def test_missing_R_or_H2d_is_none(self, tmp_path):
        payload = {
            "schema": "lozenge_bezier_anim",
            "schema_version": 3,
            "resolution": [1920, 1080],
            "objects": {},
            "camera": {
                "0": {"focal": 100.0, "cx": 10.0, "cy": 10.0, "t": [1, 2, 3]}
            },
        }
        p = tmp_path / "no_R.json"
        p.write_text(json.dumps(payload))
        doc = load_json(p)
        assert doc.camera[0].R is None
        assert doc.camera[0].H2d is None
        assert doc.camera[0].t == (1.0, 2.0, 3.0)


class TestV3Persons:
    def test_persons_block_parsed(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        assert doc.persons is not None
        assert sorted(doc.persons.keys()) == [0, 1, 2]
        for frame_persons in doc.persons.values():
            assert 0 in frame_persons
            pf = frame_persons[0]
            assert isinstance(pf, PersonFrame)
            assert len(pf.cam_t) == 3
            assert len(pf.pelvis_px) == 2
            assert pf.pelvis_3d is not None

    def test_pelvis_px_values_preserved(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        assert doc.persons[0][0].pelvis_px == (500.0, 300.0)
        assert doc.persons[1][0].pelvis_px == (502.0, 300.0)
        assert doc.persons[2][0].pelvis_px == (504.0, 300.0)

    def test_missing_pelvis_3d_is_none(self, tmp_path):
        payload = {
            "schema": "lozenge_bezier_anim",
            "schema_version": 3,
            "resolution": [1920, 1080],
            "objects": {},
            "persons": {
                "0": {"0": {"cam_t": [0, 0, 1], "pelvis_px": [1, 2]}}
            },
        }
        p = tmp_path / "no_pelvis_3d.json"
        p.write_text(json.dumps(payload))
        doc = load_json(p)
        assert doc.persons[0][0].pelvis_3d is None


class TestV2BackCompat:
    def test_v2_fixture_has_null_camera_persons(self, fixtures_dir):
        """A v2 JSON must still parse and leave the new fields as None."""
        doc = load_json(fixtures_dir / "v2_small.json")
        assert doc.camera is None
        assert doc.persons is None
