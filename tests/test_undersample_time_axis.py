"""The tolerance must actually govern the reduction.

These are regression tests for two defects that the rest of the suite was
blind to -- a complete rewrite of the articulation metric passed all 147
existing tests unchanged, before and after. They are written to FAIL on the
pre-fix implementation, which is the only thing that makes them a gate.

1. ``rdp_reduction`` measured perpendicular distance to the start->end chord
   in state space, with no time axis. Motion driven by a single time-varying
   scalar traces a 1-D segment back and forth, every interior sample projects
   onto the chord with zero distance, and the tolerance had NO EFFECT.
2. The articulation state vector divided its spatial terms by the body radius
   but left rotation in degrees, so the rotation term saturated the metric
   whenever a bone rotated and the preset triples could not differentiate.
"""
from __future__ import annotations

import math

import pytest

from rotobot_nuke import (
    PRESET_BALANCED,
    PRESET_COARSE,
    PRESET_FINE,
    load_json,
    rdp_reduction,
    undersample_doc,
)
from rotobot_nuke.undersample import _state_vector_for_frame


def _recon_error(samples, kept):
    """Worst deviation of any sample from the linear-in-time interpolation
    between its retained neighbours -- what a host actually reconstructs."""
    worst = 0.0
    for i, s in enumerate(samples):
        if i <= kept[0] or i >= kept[-1]:
            continue
        lo = max(k for k in kept if k <= i)
        hi = min(k for k in kept if k >= i)
        t = 0.0 if hi == lo else (i - lo) / (hi - lo)
        for j in range(len(s)):
            got = samples[lo][j] + t * (samples[hi][j] - samples[lo][j])
            worst = max(worst, abs(s[j] - got))
    return worst


class TestTheToleranceGovernsRetracingMotion:
    """A signal that returns along its own path is still moving."""

    #: A sine: every sample lies ON the chord between the first and last, so
    #: the old perpendicular metric saw a straight line and kept 4 points at
    #: every tolerance from 0.5 to 25.
    SINE = [[40.0 * math.sin(f / 6.0)] for f in range(1, 49)]

    def test_a_tighter_tolerance_keeps_more_keyframes(self):
        counts = [len(rdp_reduction(self.SINE, t)) for t in (0.5, 2.0, 5.0, 10.0)]
        assert counts == sorted(counts, reverse=True), counts
        assert counts[0] > counts[-1], f"tolerance is inert: {counts}"

    @pytest.mark.parametrize("tol", [0.5, 1.0, 2.0, 5.0])
    def test_the_result_is_within_the_tolerance_it_was_given(self, tol):
        kept = rdp_reduction(self.SINE, tol)
        err = _recon_error(self.SINE, kept)
        # The documented contract: "the remaining keyframes still describe
        # the full motion to within tolerance".
        assert err <= tol * 1.05, f"{err:.3f} > {tol} with {len(kept)} keys"

    def test_a_straight_line_still_collapses_to_its_endpoints(self):
        # The behaviour that was always correct, and must stay correct: a
        # uniformly sampled ramp IS linear in time, so two keys reproduce it.
        pts = [[float(i), 2.0 * i] for i in range(20)]
        assert rdp_reduction(pts, 0.5) == [0, 19]

    def test_pure_translation_is_not_mistaken_for_rest(self):
        xs = [[200.0 + 40.0 * math.sin(f / 6.0), 300.0] for f in range(1, 49)]
        kept = rdp_reduction(xs, 1.0)
        assert _recon_error(xs, kept) <= 1.05


class TestARigidLimbSwing:
    """The most common motion in roto, and the worst case for both defects:
    a rigid segment varies ONLY in its bone-angle component."""

    N = 48
    ELBOW = (900.0, 520.0)
    LEN = 160.0
    LOCAL = [(0.1 * LEN, 22), (0.35 * LEN, 26), (0.65 * LEN, 24), (0.95 * LEN, 16),
             (0.95 * LEN, -16), (0.65 * LEN, -24), (0.35 * LEN, -26), (0.1 * LEN, -22)]

    def _doc(self, tmp_path):
        import json

        frames = {}
        for f in range(1, self.N + 1):
            th = math.radians(30.0 * math.sin(2 * math.pi * f / 24.0))
            c, s = math.cos(th), math.sin(th)
            frames[str(f)] = {
                "points": [{"x": self.ELBOW[0] + lx * c - ly * s,
                            "y": self.ELBOW[1] + lx * s + ly * c,
                            "left_x": 0.0, "left_y": 0.0,
                            "right_x": 0.0, "right_y": 0.0}
                           for lx, ly in self.LOCAL],
                "bone": {"pt0": {"x": self.ELBOW[0], "y": self.ELBOW[1]},
                         "pt1": {"x": self.ELBOW[0] + self.LEN * c,
                                 "y": self.ELBOW[1] + self.LEN * s}},
            }
        cam = {str(f): {"t": [0.0, 0.0, 0.0], "R": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                        "focal": 2100.0, "cx": 1920.0, "cy": 1080.0,
                        "H2d": [1, 0, 0, 0, 1, 0, 0, 0, 1], "source": "test"}
               for f in range(1, self.N + 1)}
        persons = {str(f): {"0": {"cam_t": [0.0, 0.0, 4.2],
                                  "pelvis_px": [960.0, 900.0],
                                  "pelvis_3d": [0.0, 0.0, 4.2],
                                  "joint_xforms": [], "pose": []}}
                   for f in range(1, self.N + 1)}
        doc = {"schema": "lozenge_bezier_anim", "schema_version": 3, "fps": 24,
               "resolution": [3840, 2160], "camera": cam, "persons": persons,
               "objects": {"p0:arm:L:forearm": {
                   "person_id": 0, "body": "arm", "side": "L",
                   "segment": "forearm", "skeleton_edge": [5, 7],
                   "closed": True, "point_count": 8,
                   "visibility": {str(f): 1 for f in range(1, self.N + 1)},
                   "frames": frames}}}
        p = tmp_path / "swing.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return load_json(p)

    def _kept(self, tmp_path, preset):
        out = undersample_doc(
            self._doc(tmp_path),
            camera_tolerance=preset.camera_tolerance,
            person_tolerance=preset.person_tolerance,
            articulation_tolerance=preset.articulation_tolerance,
        )
        return len(out.objects["p0:arm:L:forearm"].frames)

    def test_the_presets_differentiate(self, tmp_path):
        fine = self._kept(tmp_path, PRESET_FINE)
        bal = self._kept(tmp_path, PRESET_BALANCED)
        coarse = self._kept(tmp_path, PRESET_COARSE)
        assert fine > bal > coarse, (fine, bal, coarse)

    def test_it_does_not_collapse_to_the_extrema(self, tmp_path):
        # Pre-fix this kept exactly 6 -- the start, the end, and the four
        # turning points of the two swing cycles -- at every preset.
        assert self._kept(tmp_path, PRESET_FINE) > 10


class TestTheRotationUnit:
    """Rotation must be commensurate with the spatial terms beside it."""

    def _frame(self, deg):
        from rotobot_nuke.reader import LozengeFrame, LozengePoint

        th = math.radians(deg)
        c, s = math.cos(th), math.sin(th)
        return LozengeFrame(
            points=[LozengePoint(x=100.0 * c, y=100.0 * s,
                                 left_x=0.0, left_y=0.0,
                                 right_x=0.0, right_y=0.0)],
            bone=((0.0, 0.0), (100.0 * c, 100.0 * s)),
        )

    def test_the_hierarchical_path_uses_radians(self):
        sv = _state_vector_for_frame(self._frame(30.0), body_scale=26.0,
                                     angle_radians=True)
        assert abs(abs(sv[2]) - math.radians(30.0)) < 1e-9, sv[2]

    def test_the_legacy_default_is_still_degrees(self):
        # Scoped deliberately: the legacy pixel-metric tolerances (2 / 5 / 10
        # / 25) were tuned against degrees, so the default must not move.
        sv = _state_vector_for_frame(self._frame(30.0))
        assert abs(abs(sv[2]) - 30.0) < 1e-9, sv[2]

    def test_degrees_would_saturate_an_articulation_tolerance(self):
        # Why it mattered. Whichever component of the state vector is largest
        # governs the whole thing, so a 1deg bone rotation must not on its own
        # exceed the coarsest articulation tolerance -- in radians it is 0.017,
        # comfortably inside 0.1; in degrees it is 1.0, ten times outside.
        one_deg_rad = abs(_state_vector_for_frame(
            self._frame(1.0), body_scale=26.0, angle_radians=True)[2])
        one_deg_deg = abs(_state_vector_for_frame(self._frame(1.0))[2])
        assert one_deg_rad < PRESET_COARSE.articulation_tolerance
        assert one_deg_deg > PRESET_COARSE.articulation_tolerance
        assert one_deg_deg / one_deg_rad > 50.0      # the 57x unit inflation
