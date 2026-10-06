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
from typing import List, Sequence, Tuple

from .reader import LozengeDoc, LozengeFrame, LozengeObject, LozengePoint

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


def _state_vector_for_frame(frame: LozengeFrame) -> Sequence[float] | None:
    """Encode one frame as an N-dim state vector.

    Shape: ``[OriginX, OriginY, RotationDegrees, lx_0, ly_0, ..., lx_N, ly_N]``
    where ``(OriginX, OriginY) == bone.pt0`` and local coords are
    computed in a frame-aligned basis built from ``bone.pt1 - bone.pt0``.

    Returns ``None`` if the frame has no ``bone`` or no points — such
    frames cannot be compared across time and the caller should keep
    them unchanged.
    """
    if frame.bone is None or not frame.points:
        return None

    (p0x, p0y), (p1x, p1y) = frame.bone
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
        rel_x = p.x - p0x
        rel_y = p.y - p0y
        lx = rel_x * ux[0] + rel_y * ux[1]
        ly = rel_x * uy[0] + rel_y * uy[1]
        out.append(lx)
        out.append(ly)
    return out


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


def undersample_doc(
    doc: LozengeDoc, tolerance: float = DEFAULT_TOLERANCE
) -> LozengeDoc:
    """Return a copy of ``doc`` with each object's keyframes RDP-reduced.

    Shape geometry is untouched — only which frames carry a keyframe
    changes. Combine with :func:`rotobot_nuke.importer.build_roto` to
    turn the reduced doc into a Roto node.
    """
    if tolerance <= 0:
        return doc

    new_objects: dict = {}
    for key, obj in doc.objects.items():
        new_obj, _before, _after = undersample_object(obj, tolerance)
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
