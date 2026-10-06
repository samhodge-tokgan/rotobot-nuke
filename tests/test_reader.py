"""Tests for ``rotobot_nuke.reader`` — pure Python, no Nuke."""

from __future__ import annotations

import io
import json

import pytest

from rotobot_nuke import LozengeDoc, MissingResolutionError, load_json


class TestResolutionPolicy:
    def test_v2_json_uses_in_band_resolution(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        assert doc.resolution == (1920, 1080)
        assert doc.schema == "lozenge_bezier_anim"
        assert doc.schema_version == 2

    def test_v1_json_without_kwarg_raises(self, fixtures_dir):
        with pytest.raises(MissingResolutionError) as excinfo:
            load_json(fixtures_dir / "v1_small.json")
        # Message must name the kwarg so the artist knows how to fix it.
        assert "resolution=" in str(excinfo.value)

    def test_v1_json_with_explicit_kwarg_loads(self, fixtures_dir):
        doc = load_json(
            fixtures_dir / "v1_small.json", resolution=(1920, 1080)
        )
        assert doc.resolution == (1920, 1080)
        assert doc.schema_version == 1

    def test_explicit_kwarg_overrides_in_band_resolution(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json", resolution=(3840, 2160))
        assert doc.resolution == (3840, 2160)

    def test_width_height_fields_without_resolution_array(self, tmp_path):
        p = tmp_path / "wh_only.json"
        p.write_text(
            json.dumps(
                {
                    "schema": "lozenge_bezier_anim",
                    "schema_version": 2,
                    "fps": 24,
                    "width": 1280,
                    "height": 720,
                    "objects": {},
                }
            )
        )
        doc = load_json(p)
        assert doc.resolution == (1280, 720)

    def test_bad_kwarg_shape_raises(self, fixtures_dir):
        with pytest.raises(MissingResolutionError):
            load_json(fixtures_dir / "v2_small.json", resolution=(0, 1080))

    def test_bad_kwarg_type_raises(self, fixtures_dir):
        with pytest.raises(MissingResolutionError):
            load_json(fixtures_dir / "v2_small.json", resolution=("huge", "wide"))  # type: ignore[arg-type]


class TestSchemaValidation:
    def test_wrong_schema_name_rejected(self, tmp_path):
        p = tmp_path / "wrong.json"
        p.write_text(json.dumps({"schema": "something_else", "resolution": [1, 1]}))
        with pytest.raises(ValueError) as excinfo:
            load_json(p)
        assert "something_else" in str(excinfo.value)

    def test_malformed_json_raises_valueerror_with_path(self, tmp_path):
        p = tmp_path / "garbage.json"
        p.write_text("{not valid json")
        with pytest.raises(ValueError) as excinfo:
            load_json(p)
        assert str(p) in str(excinfo.value)

    def test_top_level_array_rejected(self, tmp_path):
        p = tmp_path / "array.json"
        p.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(ValueError):
            load_json(p)

    def test_accepts_file_handle(self, fixtures_dir):
        with (fixtures_dir / "v2_small.json").open("r") as fh:
            doc = load_json(fh)
        assert doc.resolution == (1920, 1080)

    def test_schema_missing_defaults_to_v1_but_still_tagged(self, tmp_path):
        """Older captures sometimes omit the schema field entirely."""
        p = tmp_path / "no_schema.json"
        p.write_text(json.dumps({"resolution": [100, 100], "objects": {}}))
        doc = load_json(p)
        assert doc.schema == "lozenge_bezier_anim"
        assert doc.schema_version == 1


class TestObjectParsing:
    def test_v2_single_object_fields(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        obj = doc.objects["p0:leg:R:thigh"]
        assert obj.person_id == 0
        assert obj.body == "leg"
        assert obj.side == "R"
        assert obj.segment == "thigh"
        assert obj.skeleton_edge == (12, 10)
        assert obj.closed is True
        assert obj.point_count == 4
        assert obj.visibility == {0: True, 1: True, 2: True, 3: False}
        assert set(obj.frames.keys()) == {0, 1}

    def test_v2_frame_points_fully_populated(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        f0 = doc.objects["p0:leg:R:thigh"].frames[0]
        assert len(f0.points) == 4
        p0 = f0.points[0]
        assert (p0.x, p0.y) == (100.0, 200.0)
        assert (p0.left_x, p0.left_y) == (95.0, 200.0)
        assert (p0.right_x, p0.right_y) == (105.0, 200.0)
        assert f0.bone == ((150.0, 200.0), (150.0, 400.0))

    def test_v1_frame_as_bare_point_list(self, fixtures_dir):
        """v1 stores ``frames[k]`` as a list of points, not {"points": ...}."""
        doc = load_json(fixtures_dir / "v1_small.json", resolution=(1920, 1080))
        obj = doc.objects["p0:arm:L:upper"]
        f0 = obj.frames[0]
        assert len(f0.points) == 4
        assert f0.bone is None

    def test_object_key_fallback_populates_anatomical_fields(self, tmp_path):
        """A JSON that omits body/side/segment inside each object must still
        parse — we recover them from the key format ``p0:region:side:part``."""
        p = tmp_path / "keys_only.json"
        p.write_text(
            json.dumps(
                {
                    "schema": "lozenge_bezier_anim",
                    "schema_version": 2,
                    "resolution": [1, 1],
                    "objects": {
                        "p3:torso:C:abdomen": {
                            "closed": True,
                            "frames": {},
                        }
                    },
                }
            )
        )
        obj = load_json(p).objects["p3:torso:C:abdomen"]
        assert obj.person_id == 3
        assert obj.body == "torso"
        assert obj.side == "C"
        assert obj.segment == "abdomen"


class TestPersonDepth:
    def test_person_depth_parsed_when_present(self, tmp_path):
        p = tmp_path / "with_depth.json"
        p.write_text(
            json.dumps(
                {
                    "schema": "lozenge_bezier_anim",
                    "schema_version": 2,
                    "resolution": [1, 1],
                    "objects": {},
                    "person_depth": {
                        "0": {"0": 12.34, "1": 15.6},
                        "1": {"0": 12.5},
                    },
                }
            )
        )
        doc = load_json(p)
        assert doc.person_depth == {0: {0: 12.34, 1: 15.6}, 1: {0: 12.5}}

    def test_person_depth_absent_is_none(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        assert doc.person_depth is None


class TestDoctypeMisuse:
    def test_bytes_io_rejected_cleanly(self):
        buf = io.BytesIO(b"{}")
        with pytest.raises((TypeError, ValueError)):
            load_json(buf)  # type: ignore[arg-type]
