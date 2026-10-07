"""Tests for the host-agnostic camera / person / part decomposition (#279).

``v3_real_cut.json`` is cut from a real v0.10.0 run (shot_9184994, 24 4K
frames): two objects with every sixth knot, the real ECC homographies (which
are projective, not affine) and the real pelvis track.
"""

from __future__ import annotations

import json
import math

import pytest

from rotobot_nuke import load_json
from rotobot_nuke.hierarchy import (
    IDENTITY,
    ROUND_TRIP_TOLERANCE_PX,
    apply_h,
    corner_pin,
    decompose,
    mat3_inv,
    mat3_mul,
    normalise,
    round_trip_error,
    unwrap_degrees,
)


def _load_raw(fixtures_dir, name):
    return json.loads((fixtures_dir / name).read_text())


def _doc_from(tmp_path, raw):
    p = tmp_path / "doc.json"
    p.write_text(json.dumps(raw))
    return load_json(p)


def _homography_from_corners(src, dst):
    """Solve the 3x3 H with H(src[i]) == dst[i] (DLT, h33 = 1)."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); rhs.append(v)
    n = 8
    a = [r + [b] for r, b in zip(rows, rhs)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        a[c], a[p] = a[p], a[c]
        for r in range(n):
            if r != c:
                k = a[r][c] / a[c][c]
                a[r] = [x - k * y for x, y in zip(a[r], a[c])]
    return tuple(a[i][n] / a[i][i] for i in range(n)) + (1.0,)


class TestMatrix:
    def test_inverse(self):
        m = normalise((0.964, 0.084, 719.9, 0.004, 0.995, -121.3, -1.03e-5, 1.05e-5, 0.9956))
        prod = mat3_mul(m, mat3_inv(m))
        assert all(abs(a - b) < 1e-9 for a, b in zip(prod, IDENTITY))

    def test_normalise_sets_scale_term(self):
        assert normalise((2, 0, 4, 0, 2, 6, 0, 0, 2)) == (1, 0, 2, 0, 1, 3, 0, 0, 1)

    def test_unwrap_never_jumps_more_than_180(self):
        assert unwrap_degrees([170, 179, -179, -170, 175]) == [170, 179, 181, 190, 175]
        assert unwrap_degrees([-10, 350]) == [-10, -10]

    def test_corner_pin_reproduces_the_homography_exactly(self, fixtures_dir):
        """A four-corner pin is the whole camera: rebuild H from the corners."""
        doc = load_json(fixtures_dir / "v3_real_cut.json")
        h = decompose(doc)
        m = h.camera[h.frames[-1]]
        assert abs(m[6]) > 1e-6, "fixture should be projective"
        w, ht = h.width, h.height
        rebuilt = _homography_from_corners(
            [(0, 0), (w, 0), (w, ht), (0, ht)], corner_pin(m, w, ht))
        for x, y in [(0, 0), (w / 2, ht / 2), (w, ht), (123, 1987)]:
            a, b = apply_h(m, x, y), apply_h(rebuilt, x, y)
            assert math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-6


class TestRoundTrip:
    @pytest.mark.parametrize("name", ["v3_small.json", "v3_real_cut.json"])
    def test_plate_positions_are_reproduced(self, fixtures_dir, name):
        doc = load_json(fixtures_dir / name)
        assert round_trip_error(doc, decompose(doc)) < 1e-6

    def test_v2_has_identity_camera_and_still_round_trips(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v2_small.json")
        h = decompose(doc)
        assert all(m == IDENTITY for m in h.camera.values())
        assert round_trip_error(doc, h) < 1e-6

    def test_tolerance_is_sub_pixel(self):
        assert ROUND_TRIP_TOLERANCE_PX <= 0.05


class TestStructure:
    def test_camera_and_pelvis_keyed_on_every_frame(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_real_cut.json")
        h = decompose(doc)
        assert h.frames == list(range(1, 25))
        assert sorted(h.camera) == h.frames
        assert sorted(h.pelvis[0]) == h.frames
        assert not h.held

    def test_pelvis_is_stabilised(self, fixtures_dir):
        """T2 lives under T1: mapping it back through the camera gives the
        measured plate pelvis."""
        raw = _load_raw(fixtures_dir, "v3_real_cut.json")
        doc = load_json(fixtures_dir / "v3_real_cut.json")
        h = decompose(doc)
        for f in (1, 12, 24):
            plate = apply_h(h.camera[f], *h.pelvis[0][f])
            want = raw["persons"][str(f)]["0"]["pelvis_px"]
            assert math.hypot(plate[0] - want[0], plate[1] - want[1]) < 1e-6

    def test_part_angle_follows_the_bone(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")
        h = decompose(doc)
        # thigh bone points straight down in Y-down pixels: +90 degrees.
        assert h.parts["p0:leg:R:thigh"][0].angle == pytest.approx(90.0)


class TestHeldData:
    def _cut(self, fixtures_dir):
        return _load_raw(fixtures_dir, "v3_real_cut.json")

    def test_ecc_failed_holds_previous_camera(self, fixtures_dir, tmp_path):
        raw = self._cut(fixtures_dir)
        raw["camera"]["6"]["source"] = "ecc-failed"
        raw["camera"]["6"]["H2d"] = [1, 0, 0, 0, 1, 0, 0, 0, 1]
        doc = _doc_from(tmp_path, raw)
        h = decompose(doc)
        assert h.camera[6] == h.camera[5]
        assert "camera: frame 6 held" in h.held
        assert round_trip_error(doc, h) < 1e-6

    def test_identity_after_track_start_holds(self, fixtures_dir, tmp_path):
        raw = self._cut(fixtures_dir)
        raw["camera"]["9"] = {"H2d": [1, 0, 0, 0, 1, 0, 0, 0, 1], "source": "identity"}
        doc = _doc_from(tmp_path, raw)
        h = decompose(doc)
        assert h.camera[9] == h.camera[8] != IDENTITY

    def test_identity_before_track_start_is_the_camera(self, fixtures_dir):
        doc = load_json(fixtures_dir / "v3_small.json")  # frame 0 is "identity"
        assert decompose(doc).camera[0] == IDENTITY

    def test_missing_camera_frame_holds(self, fixtures_dir, tmp_path):
        raw = self._cut(fixtures_dir)
        del raw["camera"]["10"]
        h = decompose(_doc_from(tmp_path, raw))
        assert h.camera[10] == h.camera[9]

    def test_zero_pelvis_holds(self, fixtures_dir, tmp_path):
        raw = self._cut(fixtures_dir)
        raw["persons"]["12"]["0"]["pelvis_px"] = [0.0, 0.0]
        doc = _doc_from(tmp_path, raw)
        h = decompose(doc)
        # Held in PLATE pixels, then stabilised with frame 12's own camera.
        held_plate = apply_h(h.camera[12], *h.pelvis[0][12])
        prev_plate = raw["persons"]["11"]["0"]["pelvis_px"]
        assert math.hypot(held_plate[0] - prev_plate[0], held_plate[1] - prev_plate[1]) < 1e-6
        assert "person 0: frame 12 held" in h.held
        assert round_trip_error(doc, h) < 1e-6

    def test_leading_gap_takes_first_good_value(self, fixtures_dir, tmp_path):
        raw = self._cut(fixtures_dir)
        for f in ("1", "2"):
            del raw["persons"][f]
        h = decompose(_doc_from(tmp_path, raw))
        assert h.pelvis[0][1] != (0.0, 0.0)

    def test_missing_bone_holds_previous_bone(self, fixtures_dir, tmp_path):
        raw = self._cut(fixtures_dir)
        del raw["objects"]["p0:leg:R:thigh"]["frames"]["7"]["bone"]
        doc = _doc_from(tmp_path, raw)
        h = decompose(doc)
        assert any(s.startswith("p0:leg:R:thigh bone: frame 7") for s in h.held)
        assert round_trip_error(doc, h) < 1e-6

    def test_person_without_any_pelvis_roots_at_origin(self, fixtures_dir, tmp_path):
        raw = self._cut(fixtures_dir)
        del raw["persons"]
        doc = _doc_from(tmp_path, raw)
        h = decompose(doc)
        assert set(h.pelvis[0].values()) == {(0.0, 0.0)}
        assert round_trip_error(doc, h) < 1e-6
