"""Keyframe undersampling for ``LozengeDoc``.

Many captures produce one JSON frame per video frame — hundreds or
thousands of near-identical control-point sets per shape. Nuke stores
every one as a keyframe, making the resulting Roto node slow to
scrub, slow to render and inconvenient to hand-tweak.

This module reduces the per-object temporal keyframe count by running
Ramer-Douglas-Peucker (RDP) on each object's time-series of state
vectors. A state vector encodes **where the segment is in the plate**
(bone origin) and **how its points sit relative to that bone** (the
local control-point layout, after transforming into bone-local
coordinates). Frames whose state vector is a close linear
interpolation of its neighbours are discarded; the remaining
keyframes still describe the full motion to within ``tolerance``.

The algorithm is a direct port of the ``key_reduction`` branch of
``tokgan_silhouette_import``
(https://github.com/samhodge-aiml/tokgan_silhouette_import/tree/key_reduction),
MIT-licensed, re-shaped to operate on :class:`LozengeDoc` in memory
rather than file-to-file.

Two call shapes are supported on :func:`undersample_doc`:

    Legacy single-pass (PR #1 behaviour) — one bone-local RDP per object
    governed by a single ``tolerance`` kwarg. Useful for v2 JSONs and for
    quick sanity checks.

    Hierarchical composed-tolerance (issue #279) — three sequential RDP
    passes:

      1. camera reference frame over :attr:`LozengeDoc.camera`
      2. person root pose over :attr:`LozengeDoc.persons`
      3. articulation (bone-local with per-frame pelvis subtracted) per
         object

    Each pass gates its own tolerance. The final kept frames per object
    are the **union** of the three passes' retained frames, intersected
    with the object's own frame set — so a frame is kept whenever the
    camera, the person root, or the articulation changed enough to
    warrant a keyframe, and dropped only when all three agree the frame
    is linearly interpolable.

Requirements:
    Each object must carry per-frame ``bone`` endpoints (schema v2 writer
    output). Objects without ``bone`` are left untouched — RDP needs an
    anchor so the state vector tracks "the whole segment moved", not
    "every point jittered by itself".
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple

from .reader import (
    CameraFrame,
    LozengeDoc,
    LozengeFrame,
    LozengeObject,
    LozengePoint,
    PersonFrame,
)

# Tolerance presets, picked from a wedge sweep across four real UHD/HD
# Rotobot-Next outputs (1.1 MB → 57.9 MB; 126 → 8198 keyframes). Units
# are mixed pixel + degree — the state vector norm is dominated by the
# bone-origin pixel coordinates on typical plates, so a tolerance of
# ``n`` is roughly "keep a frame if any aspect of its state vector
# diverged by n pixel-equivalents from a linear interpolation of its
# retained neighbours."
TOLERANCE_CONSERVATIVE = 2.0   # ≥99% keyframes kept on measured plates
TOLERANCE_BALANCED = 5.0       # 87–99% kept; the useful mid-ground
TOLERANCE_AGGRESSIVE = 10.0    # ~75% kept; faster scrubs, keyframes
                               # still coarsely preserve articulation
TOLERANCE_VERY_AGGRESSIVE = 25.0  # 45–80% kept; artist review needed

DEFAULT_TOLERANCE = TOLERANCE_BALANCED


# Composed-tolerance presets for the hierarchical path (issue #279). Each
# number is in state-vector units for its own pass: ``camera_tolerance`` in
# camera-space metres + unitless matrix cells; ``person_tolerance`` mixes
# metres (cam_t) + pixels (pelvis_px); ``articulation_tolerance`` is
# pixel-equivalent in the bone-local frame after pelvis subtraction.
class TolerancePreset(NamedTuple):
    camera_tolerance: float
    person_tolerance: float
    articulation_tolerance: float


PRESET_COARSE = TolerancePreset(2.0, 4.0, 1.0)
PRESET_BALANCED = TolerancePreset(1.0, 2.0, 0.4)
PRESET_FINE = TolerancePreset(0.5, 1.0, 0.15)

DEFAULT_CAMERA_TOLERANCE = PRESET_BALANCED.camera_tolerance
DEFAULT_PERSON_TOLERANCE = PRESET_BALANCED.person_tolerance
DEFAULT_ARTICULATION_TOLERANCE = PRESET_BALANCED.articulation_tolerance


def rdp_reduction(points: Sequence[Sequence[float]], tolerance: float) -> List[int]:
    """Ramer-Douglas-Peucker for an N-dimensional polyline.

    Args:
        points: ordered sequence of state vectors — each is a sequence
            of floats, all the same length.
        tolerance: maximum perpendicular distance (in state-vector
            units) from a kept point to the line connecting its
            retained neighbours.

    Returns:
        Sorted list of indices into ``points`` for the retained
        vertices. Always includes the first and last indices when
        ``points`` is non-empty.
    """
    if not points:
        return []
    if len(points) < 3:
        return list(range(len(points)))
    if tolerance <= 0:
        return list(range(len(points)))

    def _dim(v):
        return len(v)

    width = _dim(points[0])

    start = list(points[0])
    end = list(points[-1])
    line_vec = [end[i] - start[i] for i in range(width)]
    line_len_sq = sum(c * c for c in line_vec)

    max_dist = 0.0
    pivot = -1
    for i in range(1, len(points) - 1):
        p = points[i]
        if line_len_sq == 0.0:
            diff = [p[j] - start[j] for j in range(width)]
            dist = math.sqrt(sum(c * c for c in diff))
        else:
            dot = sum(
                (p[j] - start[j]) * line_vec[j] for j in range(width)
            )
            t = max(0.0, min(1.0, dot / line_len_sq))
            proj = [start[j] + t * line_vec[j] for j in range(width)]
            diff = [p[j] - proj[j] for j in range(width)]
            dist = math.sqrt(sum(c * c for c in diff))
        if dist > max_dist:
            max_dist = dist
            pivot = i

    if max_dist > tolerance and pivot > 0:
        left = rdp_reduction(points[: pivot + 1], tolerance)
        right = rdp_reduction(points[pivot:], tolerance)
        # Left's last index == pivot in local indices; right's first == 0 (also pivot).
        # Rebase right-side indices and dedupe the shared pivot.
        combined = left[:-1] + [pivot + idx for idx in right]
        return combined
    return [0, len(points) - 1]


def _state_vector_for_frame(
    frame: LozengeFrame,
    pelvis_px: Optional[Tuple[float, float]] = None,
) -> Sequence[float] | None:
    """Encode one frame as an N-dim state vector.

    Shape: ``[OriginX, OriginY, RotationDegrees, lx_0, ly_0, ..., lx_N, ly_N]``
    where ``(OriginX, OriginY) == bone.pt0`` and local coords are
    computed in a frame-aligned basis built from ``bone.pt1 - bone.pt0``.

    When ``pelvis_px`` is supplied (issue #279 hierarchical path), the
    bone origin is reported relative to the person's pelvis pixel
    position so the first two components of the state vector carry
    articulation-only drift. The bone-local point layout stays
    unchanged — subtracting pelvis cancels out of the relative
    coordinates — so the signal downstream RDP sees is still the full
    articulation shape, just without the whole-body translation noise.

    Returns ``None`` if the frame has no ``bone`` or no points — such
    frames cannot be compared across time and the caller should keep
    them unchanged.
    """
    if frame.bone is None or not frame.points:
        return None

    (p0x, p0y), (p1x, p1y) = frame.bone
    if pelvis_px is not None:
        p0x -= pelvis_px[0]
        p0y -= pelvis_px[1]
        p1x -= pelvis_px[0]
        p1y -= pelvis_px[1]
    dx = p1x - p0x
    dy = p1y - p0y
    dist = math.hypot(dx, dy)
    if dist > 0.0:
        ux = (dx / dist, dy / dist)
    else:
        ux = (1.0, 0.0)
    uy = (-ux[1], ux[0])
    # Negated to match the port source — direction of frame rotation
    # relative to bone-local X.
    angle = -math.degrees(math.atan2(dy, dx))

    out: List[float] = [p0x, p0y, angle]
    for p in frame.points:
        # Use the ORIGINAL (not pelvis-subtracted) p coords for local
        # projection — pelvis subtraction cancels out of relative
        # coordinates, so this is equivalent to subtracting from both and
        # then taking the difference. Avoids a double-subtract.
        rel_x = p.x - (p0x + (pelvis_px[0] if pelvis_px is not None else 0.0))
        rel_y = p.y - (p0y + (pelvis_px[1] if pelvis_px is not None else 0.0))
        lx = rel_x * ux[0] + rel_y * ux[1]
        ly = rel_x * uy[0] + rel_y * uy[1]
        out.append(lx)
        out.append(ly)
    return out


def _camera_state_vector(cam: CameraFrame) -> Sequence[float]:
    """Encode a plate-camera reference as a 21-dim state vector.

    Shape: ``[tx, ty, tz] ++ R[9] ++ H2d[9]``. Identity matrices stand in
    when the source didn't supply ``R`` or ``H2d`` (locked-off shots,
    sidecars that only provided intrinsics, etc).
    """
    out: List[float] = [cam.t[0], cam.t[1], cam.t[2]]
    out.extend(cam.R if cam.R is not None else (1.0, 0.0, 0.0,
                                                 0.0, 1.0, 0.0,
                                                 0.0, 0.0, 1.0))
    out.extend(cam.H2d if cam.H2d is not None else (1.0, 0.0, 0.0,
                                                     0.0, 1.0, 0.0,
                                                     0.0, 0.0, 1.0))
    return out


def _person_state_vector(pf: PersonFrame) -> Sequence[float]:
    """``[cam_t.x, cam_t.y, cam_t.z, pelvis_px.x, pelvis_px.y]`` — 5 dims."""
    return [pf.cam_t[0], pf.cam_t[1], pf.cam_t[2],
            pf.pelvis_px[0], pf.pelvis_px[1]]


def undersample_object(
    obj: LozengeObject, tolerance: float = DEFAULT_TOLERANCE
) -> Tuple[LozengeObject, int, int]:
    """Return a copy of ``obj`` with redundant keyframes dropped.

    Objects without per-frame ``bone`` data (schema v1, or v2 captures
    that didn't record it) are returned unchanged — RDP needs the bone
    anchor to produce a meaningful state vector.

    Visibility keys are kept intact; a hidden-frame key is a real
    annotation of what the artist should see, not a candidate for
    reduction.

    Returns:
        ``(new_object, frames_before, frames_after)``.
    """
    if not obj.frames or tolerance <= 0:
        return obj, len(obj.frames), len(obj.frames)

    sorted_keys = sorted(obj.frames.keys())
    state_vectors: List[Sequence[float]] = []
    for k in sorted_keys:
        sv = _state_vector_for_frame(obj.frames[k])
        if sv is None:
            # Can't compare without a bone; bail out and keep every
            # frame so we don't silently corrupt timing.
            return obj, len(obj.frames), len(obj.frames)
        state_vectors.append(sv)

    # Enforce uniform width — if the point-count-per-frame varies, pad or
    # bail. Rotobot-Next emits constant point_count per object, so a
    # mismatch means malformed input; keep everything.
    widths = {len(sv) for sv in state_vectors}
    if len(widths) != 1:
        return obj, len(obj.frames), len(obj.frames)

    keep_indices = set(rdp_reduction(state_vectors, tolerance))
    kept_keys = [sorted_keys[i] for i in sorted(keep_indices)]
    new_frames = {k: obj.frames[k] for k in kept_keys}

    new_obj = replace(obj, frames=new_frames)
    return new_obj, len(obj.frames), len(new_frames)


def _hierarchical_undersample(
    doc: LozengeDoc,
    *,
    camera_tolerance: float,
    person_tolerance: float,
    articulation_tolerance: float,
) -> LozengeDoc:
    """Three-pass composed-tolerance keyframe reduction (issue #279).

    Pass 1: RDP over :attr:`LozengeDoc.camera` under ``camera_tolerance``.
    Pass 2: RDP over :attr:`LozengeDoc.persons` per pid under ``person_tolerance``.
    Pass 3: RDP over each object's articulation-only state vector (bone
            origin minus pelvis) under ``articulation_tolerance``.

    Final kept frames per object = union of the three passes' retained
    frames, intersected with the object's own frame set.
    """
    # Pass 1 — camera.
    kept_cam_frames: set = set()
    if doc.camera:
        cam_frames_sorted = sorted(doc.camera.keys())
        if camera_tolerance > 0 and len(cam_frames_sorted) >= 2:
            cam_vectors = [_camera_state_vector(doc.camera[f]) for f in cam_frames_sorted]
            kept_idx = rdp_reduction(cam_vectors, camera_tolerance)
            kept_cam_frames = {cam_frames_sorted[i] for i in kept_idx}
        else:
            kept_cam_frames = set(cam_frames_sorted)

    # Pass 2 — person root per pid. Pivot the frame-major persons dict
    # into pid-major so each pid gets its own time-series.
    kept_person_frames_by_pid: dict = {}
    if doc.persons:
        persons_by_pid: dict = {}
        for f, per_frame in doc.persons.items():
            for pid, pf in per_frame.items():
                persons_by_pid.setdefault(pid, {})[f] = pf
        for pid, frames in persons_by_pid.items():
            sorted_frames = sorted(frames.keys())
            if person_tolerance > 0 and len(sorted_frames) >= 2:
                vectors = [_person_state_vector(frames[f]) for f in sorted_frames]
                kept_idx = rdp_reduction(vectors, person_tolerance)
                kept_person_frames_by_pid[pid] = {sorted_frames[i] for i in kept_idx}
            else:
                kept_person_frames_by_pid[pid] = set(sorted_frames)

    # Pass 3 — per-object articulation with per-frame pelvis subtracted.
    new_objects: dict = {}
    for key, obj in doc.objects.items():
        if not obj.frames:
            new_objects[key] = obj
            continue
        sorted_frames = sorted(obj.frames.keys())
        pid = obj.person_id

        # Collect per-frame pelvis_px (may be empty if persons data absent).
        pelvis_by_frame: dict = {}
        if doc.persons:
            for f in sorted_frames:
                per = doc.persons.get(f)
                if per is None:
                    continue
                pf = per.get(pid)
                if pf is not None:
                    pelvis_by_frame[f] = pf.pelvis_px

        # Build the articulation state vectors. If any frame lacks a bone,
        # bail on this object — can't compare without the anchor.
        articulation_vectors: list = []
        bone_ok = True
        for f in sorted_frames:
            sv = _state_vector_for_frame(
                obj.frames[f], pelvis_px=pelvis_by_frame.get(f)
            )
            if sv is None:
                bone_ok = False
                break
            articulation_vectors.append(sv)

        if not bone_ok or len({len(v) for v in articulation_vectors}) != 1:
            # Bone-less or varying-K object — keep every frame on the
            # articulation pass; the outer passes still prune via the union.
            kept_art_frames: set = set(sorted_frames)
        elif articulation_tolerance <= 0 or len(articulation_vectors) < 3:
            kept_art_frames = set(sorted_frames)
        else:
            kept_idx = rdp_reduction(articulation_vectors, articulation_tolerance)
            kept_art_frames = {sorted_frames[i] for i in kept_idx}

        # Compose: a frame is kept whenever ANY of the three passes
        # retained it (and it exists on this object).
        kept_person_frames = kept_person_frames_by_pid.get(pid, set())
        obj_frames_set = set(sorted_frames)
        final_kept = (kept_cam_frames | kept_person_frames | kept_art_frames) & obj_frames_set
        if not final_kept:
            # Degenerate: no outer-pass frames landed on this object AND
            # the object has only 1-2 articulation frames. Keep everything.
            final_kept = obj_frames_set

        new_frames = {f: obj.frames[f] for f in sorted(final_kept)}
        new_objects[key] = replace(obj, frames=new_frames)

    return replace(doc, objects=new_objects)


def undersample_doc(
    doc: LozengeDoc,
    tolerance: Optional[float] = None,
    *,
    camera_tolerance: Optional[float] = None,
    person_tolerance: Optional[float] = None,
    articulation_tolerance: Optional[float] = None,
) -> LozengeDoc:
    """Return a copy of ``doc`` with each object's keyframes RDP-reduced.

    Two call shapes:

    * **Legacy single-pass** (PR #1): pass ``tolerance=X`` or no kwargs.
      One bone-local RDP per object; identical behaviour to v0.1.
    * **Hierarchical composed-tolerance** (issue #279): pass any of
      ``camera_tolerance``, ``person_tolerance``, ``articulation_tolerance``.
      Three sequential RDP passes; missing kwargs fall back to
      :data:`PRESET_BALANCED`. See :func:`_hierarchical_undersample`.

    Shape geometry is untouched — only which frames carry a keyframe
    changes. Combine with :func:`rotobot_nuke.importer.build_roto` to
    turn the reduced doc into a Roto node.
    """
    hierarchical = any(
        x is not None
        for x in (camera_tolerance, person_tolerance, articulation_tolerance)
    )
    if hierarchical:
        if tolerance is not None:
            raise TypeError(
                "undersample_doc: pass EITHER tolerance= (legacy single-pass) "
                "OR the hierarchical kwargs (camera_tolerance / person_tolerance "
                "/ articulation_tolerance), not both."
            )
        return _hierarchical_undersample(
            doc,
            camera_tolerance=camera_tolerance
            if camera_tolerance is not None
            else DEFAULT_CAMERA_TOLERANCE,
            person_tolerance=person_tolerance
            if person_tolerance is not None
            else DEFAULT_PERSON_TOLERANCE,
            articulation_tolerance=articulation_tolerance
            if articulation_tolerance is not None
            else DEFAULT_ARTICULATION_TOLERANCE,
        )

    # Legacy single-pass path.
    tol = DEFAULT_TOLERANCE if tolerance is None else tolerance
    if tol <= 0:
        return doc

    new_objects: dict = {}
    for key, obj in doc.objects.items():
        new_obj, _before, _after = undersample_object(obj, tol)
        new_objects[key] = new_obj

    return replace(doc, objects=new_objects)


def undersample_json(
    input_path,
    output_path,
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict:
    """File-to-file convenience: load ``input_path``, undersample,
    write to ``output_path``. Returns a per-object before/after report.

    Writes the EXACT original JSON structure back out (frames whose
    keys weren't retained are removed; nothing else is renamed or
    reshaped).
    """
    in_path = Path(input_path)
    out_path = Path(output_path)
    with in_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    objects = raw.get("objects") or {}
    report: dict = {}

    # Need bones to run RDP — but we also need the raw frame dict so
    # we can keep the original indentation / key order. Walk the raw
    # dict, build a LozengeDoc-equivalent view per object, then filter
    # the raw frames dict in-place.
    for obj_id, obj_raw in list(objects.items()):
        frames_raw = obj_raw.get("frames") or {}
        if not frames_raw:
            report[obj_id] = (0, 0)
            continue

        try:
            sorted_keys = sorted(frames_raw.keys(), key=lambda s: int(s))
        except (TypeError, ValueError):
            report[obj_id] = (len(frames_raw), len(frames_raw))
            continue

        state_vectors: List[Sequence[float]] = []
        bail = False
        for k in sorted_keys:
            fv = frames_raw[k]
            if not isinstance(fv, dict):
                bail = True
                break
            bone = fv.get("bone")
            pts = fv.get("points")
            if not isinstance(bone, dict) or not isinstance(pts, list):
                bail = True
                break
            pt0 = bone.get("pt0") or {}
            pt1 = bone.get("pt1") or {}
            try:
                p0x = float(pt0["x"])
                p0y = float(pt0["y"])
                p1x = float(pt1["x"])
                p1y = float(pt1["y"])
            except (KeyError, TypeError, ValueError):
                bail = True
                break
            dx = p1x - p0x
            dy = p1y - p0y
            dist = math.hypot(dx, dy)
            if dist > 0.0:
                ux = (dx / dist, dy / dist)
            else:
                ux = (1.0, 0.0)
            uy = (-ux[1], ux[0])
            angle = -math.degrees(math.atan2(dy, dx))
            sv: List[float] = [p0x, p0y, angle]
            try:
                for p in pts:
                    rx = float(p["x"]) - p0x
                    ry = float(p["y"]) - p0y
                    sv.append(rx * ux[0] + ry * ux[1])
                    sv.append(rx * uy[0] + ry * uy[1])
            except (KeyError, TypeError, ValueError):
                bail = True
                break
            state_vectors.append(sv)

        if bail or len({len(sv) for sv in state_vectors}) != 1:
            report[obj_id] = (len(frames_raw), len(frames_raw))
            continue

        keep_indices = set(rdp_reduction(state_vectors, tolerance))
        keep_keys = {sorted_keys[i] for i in keep_indices}
        obj_raw["frames"] = {k: v for k, v in frames_raw.items() if k in keep_keys}
        report[obj_id] = (len(frames_raw), len(obj_raw["frames"]))

    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2)

    return report


def _cli(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="rotobot-undersample",
        description=(
            "Reduce the per-object temporal keyframe count in a Rotobot-Next "
            "lozenge_bezier_anim JSON via RDP on bone-local state vectors."
        ),
    )
    parser.add_argument("input", help="input JSON path")
    parser.add_argument("output", help="output JSON path")
    parser.add_argument(
        "--tolerance",
        "-t",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"RDP tolerance in state-vector units (default {DEFAULT_TOLERANCE})",
    )
    args = parser.parse_args(argv)

    report = undersample_json(args.input, args.output, tolerance=args.tolerance)
    for obj_id, (before, after) in sorted(report.items()):
        pct = (100.0 * after / before) if before else 0.0
        print(
            f"{obj_id}: {before} -> {after} frames ({pct:.1f}% kept)"
        )
    kept = sum(a for _, a in report.values())
    orig = sum(b for b, _ in report.values())
    if orig:
        print(
            f"TOTAL: {orig} -> {kept} frames "
            f"({100.0 * kept / orig:.1f}% kept) at tolerance={args.tolerance}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
