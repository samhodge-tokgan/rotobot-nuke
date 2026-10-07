"""Nuke-side half of rotobot-nuke: turn a :class:`LozengeDoc` into a Roto node.

Imports ``nuke`` + ``nuke.rotopaint`` at module load; must be run inside
Nuke's embedded Python (or with those modules mocked, as the test suite
does).

Two build modes available on :func:`build_roto`:

* ``mode="legacy"`` (default) — flat ``person/region/side/part`` layer
  hierarchy; every spline knot keyframed at its plate-pixel position.
  Original PR #1 shape.
* ``mode="hierarchical"`` — three transform-cascaded layers
  (``camera_track → pelvis → body-part``) carrying H2d / pelvis_px /
  bone pose respectively, with spline knots stored in bone-local
  coordinates so the leaf curves move only when articulation
  genuinely changes. See ``docs/design/hierarchical-import.md``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import nuke
import nuke.rotopaint

from .reader import CameraFrame, LozengeDoc, LozengeFrame, LozengeObject, PersonFrame
from .undersample import undersample_doc


FINGER_ORDER = ("A_thumb", "B_index", "C_middle", "D_ring", "E_pinky")


# ---------------------------------------------------------------------------
# Shared layer / name helpers.

def _get_or_create_layer(parent, curves, name):
    """Return the existing child Layer with this name, or create one."""
    for item in parent:
        if isinstance(item, nuke.rotopaint.Layer) and item.name == name:
            return item
    layer = nuke.rotopaint.Layer(curves)
    layer.name = name
    parent.append(layer)
    return layer


def _parse_object_name(obj_name: str):
    parts = obj_name.split(":")
    while len(parts) < 4:
        parts.append("unknown")
    return parts[:4]


def _split_hand_part(part_name: str):
    for finger in FINGER_ORDER:
        if part_name.startswith(finger):
            return "fingers", finger
    return None, None


# ---------------------------------------------------------------------------
# Legacy build path (PR #1 shape, unchanged).

def _build_shape_for_object_legacy(
    obj: LozengeObject,
    curves,
    part_layer,
    H: int,
    curve_type: str,
) -> None:
    """Build one Roto Shape for ``obj`` under ``part_layer`` (legacy mode)."""
    shape = nuke.rotopaint.Shape(curves, type=curve_type)
    shape.name = f"{obj.key}:Shape"
    part_layer.append(shape)

    frames = obj.frames
    if not frames:
        return

    first_frame = min(frames.keys())
    first_pts = frames[first_frame].points

    for p in first_pts:
        x = p.x
        y = H - p.y
        cp = nuke.rotopaint.AnimControlPoint(x, y)
        shape.append(cp)

    all_frames = sorted(frames.keys())
    shape.setVisible(all_frames[0] - 1, False)
    for frame, vis_value in obj.visibility.items():
        shape.setVisible(frame, bool(vis_value))
    shape.setVisible(all_frames[-1] + 1, False)

    use_bspline = curve_type == "bspline"
    for frame, frame_data in frames.items():
        pts = frame_data.points
        vis = obj.visibility.get(frame)
        if vis is None:
            shape.setVisible(frame, False)
        else:
            shape.setVisible(frame, bool(vis))

        for cp, p in zip(shape, pts):
            x = p.x
            y = H - p.y
            cp.center.addPositionKey(frame, (x, y))
            if not use_bspline:
                lx = p.left_x
                ly = H - p.left_y
                rx = p.right_x
                ry = H - p.right_y
                cp.leftTangent.addPositionKey(frame, (lx - x, ly - y))
                cp.rightTangent.addPositionKey(frame, (rx - x, ry - y))


def _build_roto_legacy(doc: LozengeDoc, roto_name: str, curve_type: str):
    """PR #1 flat-hierarchy build path."""
    _, H = doc.resolution

    roto = nuke.nodes.Roto(name=roto_name)
    curves = roto["curves"]
    root = curves.rootLayer

    # Historical +1/-1 identity translation on the root transform.
    trans = root.getTransform()
    trans.getTranslationAnimCurve(0).addKey(0, 1.0)
    trans.getTranslationAnimCurve(1).addKey(0, -1.0)
    curves.changed()

    for obj_name, obj in doc.objects.items():
        person, region, side, part = _parse_object_name(obj_name)
        side_layer_name = f"{person}_{region}_{side}"

        person_layer = _get_or_create_layer(root, curves, person)
        region_layer = _get_or_create_layer(
            person_layer, curves, f"{person}_{region}"
        )
        side_layer = _get_or_create_layer(region_layer, curves, side_layer_name)

        if region == "hand":
            group, finger = _split_hand_part(part)
            if group == "fingers":
                fingers_layer = _get_or_create_layer(
                    side_layer, curves, f"{person}_fingers_{side}"
                )
                finger_layer = _get_or_create_layer(
                    fingers_layer, curves, f"{person}_{finger}_{side}"
                )
                part_layer = _get_or_create_layer(
                    finger_layer, curves, f"{person}_{part}"
                )
            else:
                part_layer = _get_or_create_layer(
                    side_layer, curves, f"{person}_{part}"
                )
        else:
            part_layer = _get_or_create_layer(
                side_layer, curves, f"{person}_{part}"
            )

        _build_shape_for_object_legacy(obj, curves, part_layer, H, curve_type)

    _label_roto(roto, doc)
    return roto


# ---------------------------------------------------------------------------
# Hierarchical build path (three transform cascade; design in
# docs/design/hierarchical-import.md).

def _conjugate_h2d_yflip(H2d: Tuple[float, ...], H: int) -> Tuple[float, ...]:
    """Conjugate a Y-down plate-space homography by the Y-flip so the
    resulting matrix, applied in Nuke's Y-up frame, has the same visual
    effect. Returns a 9-tuple (row-major).

    Y-flip ``F`` as an affine 3x3: ``[[1,0,0], [0,-1,H], [0,0,1]]``.
    F is self-inverse. ``H2d_yup = F * H2d * F``.
    """
    a, b, tx, c, d, ty, p, q, r = H2d
    # Row-by-row of F @ H2d @ F:
    #   F @ H2d yields rows [[a, b, tx], [-c + p*H, -d + q*H, -ty + r*H],
    #                         [p, q, r]]
    # (F @ H2d) @ F yields:
    #   row 0: [a,                -b,              a*0 - b*(-H) + tx] = [a, -b, b*H + tx]
    # Working it out term by term is error-prone. Use direct matmul:
    F = ((1.0, 0.0, 0.0),
         (0.0, -1.0, float(H)),
         (0.0, 0.0, 1.0))
    M = ((a, b, tx), (c, d, ty), (p, q, r))

    def _mm(X, Y):
        return tuple(
            tuple(sum(X[i][k] * Y[k][j] for k in range(3)) for j in range(3))
            for i in range(3)
        )

    FM = _mm(F, M)
    FMF = _mm(FM, F)
    return (FMF[0][0], FMF[0][1], FMF[0][2],
            FMF[1][0], FMF[1][1], FMF[1][2],
            FMF[2][0], FMF[2][1], FMF[2][2])


def _affine_decompose(H2d_yup: Tuple[float, ...]) -> Tuple[float, float, float, float, float]:
    """Decompose a Y-up 3x3 affine homography to (tx, ty, rotation_deg, sx, sy).

    The perspective row (p, q) is DROPPED. Callers that care about
    perspective (fish-eye, extreme-tilt plates) should log a WARNING.
    """
    a, b, tx, c, d, ty = H2d_yup[0], H2d_yup[1], H2d_yup[2], H2d_yup[3], H2d_yup[4], H2d_yup[5]
    rotation_deg = math.degrees(math.atan2(c, a))
    scale_x = math.hypot(a, c)
    scale_y = math.hypot(b, d)
    return tx, ty, rotation_deg, scale_x, scale_y


def _perspective_magnitude(H2d: Tuple[float, ...]) -> float:
    """Length of the (p, q) row of a 3x3 homography — how much
    perspective the matrix carries. Near-zero on affine ECC output."""
    return math.hypot(H2d[6], H2d[7])


def _set_layer_affine(
    layer,
    frame: int,
    tx: float,
    ty: float,
    rotation_deg: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> None:
    """Keyframe a Layer's Transform knob: translate + rotation + scale.

    Nuke's ``AnimCTransform`` indexes rotation + scale by **3-axis**
    XYZ, not by 2-axis XY. For a 2D roto the only meaningful rotation
    is around the view axis — Z — which is index ``2``. Scale uses
    indices 0 (X) and 1 (Y). Confirmed on skylab 2026-10-07 via
    ``dir(nuke.rotopaint.Layer.getTransform())`` after a bug-report
    screenshot showed body-parts translated correctly but at wildly
    wrong orientations (the rotations were being applied to the X
    axis, flipping body parts out of the plate plane).

    Delegates to AnimCurve getters that must exist on real Nuke's
    ``AnimCTransform`` and on the test-suite fake-Nuke ``_Transform``.
    """
    xform = layer.getTransform()
    xform.getTranslationAnimCurve(0).addKey(frame, tx)
    xform.getTranslationAnimCurve(1).addKey(frame, ty)
    # Rotation + scale are optional per the design doc (Nuke's
    # AnimCTransform always has them, but a fake-Nuke test harness
    # may skip them). Guard with hasattr for robustness.
    if hasattr(xform, "getRotationAnimCurve"):
        # Z-axis rotation only (index 2). X=0 and Y=1 stay at 0.
        xform.getRotationAnimCurve(2).addKey(frame, rotation_deg)
    if hasattr(xform, "getScaleAnimCurve"):
        xform.getScaleAnimCurve(0).addKey(frame, scale_x)
        xform.getScaleAnimCurve(1).addKey(frame, scale_y)


def _invert_affine(
    x: float, y: float,
    tx: float, ty: float,
    rotation_deg: float,
    sx: float, sy: float,
) -> Tuple[float, float]:
    """Apply the INVERSE of (translate ∘ rotate ∘ scale) to (x, y).

    Forward composition: ``out = T(R(S(in)))``. Inverse: scale⁻¹ then
    rotate⁻¹ then subtract translate.
    """
    rx = x - tx
    ry = y - ty
    cos_r = math.cos(-math.radians(rotation_deg))
    sin_r = math.sin(-math.radians(rotation_deg))
    irx = rx * cos_r - ry * sin_r
    iry = rx * sin_r + ry * cos_r
    return (irx / sx if sx else irx, iry / sy if sy else iry)


def _invert_translate_rotate(
    x: float, y: float,
    tx: float, ty: float,
    rotation_deg: float,
) -> Tuple[float, float]:
    """Apply the inverse of (translate ∘ rotate) to (x, y). Scale = 1."""
    return _invert_affine(x, y, tx, ty, rotation_deg, 1.0, 1.0)


def _build_roto_hierarchical(
    doc: LozengeDoc, roto_name: str, curve_type: str
) -> "nuke.Node":
    """Three-transform-cascade build (per docs/design/hierarchical-import.md).

    Layer hierarchy:

        root (identity-ish)
        └── camera_track           T1 = Y-up-conjugated affine of H2d
            ├── p0_pelvis          T2 = pelvis_px (Y-flipped), per frame
            │   ├── p0:leg:R:thigh T3 = bone pose (origin + angle)
            │   │   └── Shape      knots in bone-local coords
            │   └── ... (one Layer per body-part, flat under pelvis)
            └── p1_pelvis          same shape for a second person
                └── ...

    Spline knots at the leaf are stored in bone-local coords; the
    cascaded T1 × T2 × T3 at render time recovers plate pixel space.
    """
    _, H = doc.resolution

    roto = nuke.nodes.Roto(name=roto_name)
    curves = roto["curves"]
    root = curves.rootLayer

    # Historical identity translate on the root transform (kept for
    # bit-identity with legacy's root).
    root_xform = root.getTransform()
    root_xform.getTranslationAnimCurve(0).addKey(0, 1.0)
    root_xform.getTranslationAnimCurve(1).addKey(0, -1.0)
    curves.changed()

    # ---- T1: camera_track Layer with per-frame H2d-decomposed affine.
    camera_layer = _get_or_create_layer(root, curves, "camera_track")
    camera_params_by_frame: Dict[int, Tuple[float, float, float, float, float]] = {}
    perspective_warn_frames: List[int] = []
    if doc.camera:
        for frame, cf in sorted(doc.camera.items()):
            H2d = cf.H2d if cf.H2d is not None else (
                1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0
            )
            if _perspective_magnitude(H2d) > 1e-4:
                perspective_warn_frames.append(frame)
            yup = _conjugate_h2d_yflip(H2d, H)
            tx, ty, rot_deg, sx, sy = _affine_decompose(yup)
            _set_layer_affine(camera_layer, frame, tx, ty, rot_deg, sx, sy)
            camera_params_by_frame[frame] = (tx, ty, rot_deg, sx, sy)
    else:
        # v2 doc: T1 stays identity. Still create the Layer so the
        # hierarchy shape is uniform across schemas.
        _set_layer_affine(camera_layer, 0, 0.0, 0.0, 0.0, 1.0, 1.0)

    def _t1_identity_params():
        return (0.0, 0.0, 0.0, 1.0, 1.0)

    # ---- T2: one pelvis Layer per person, keyed from persons[f][pid].
    #        Pelvis_px is a plate-pixel coord (Y-down). To key T2 as the
    #        pelvis's position inside the camera-stabilised frame, we
    #        apply T1⁻¹ to the Y-flipped pelvis_px.
    persons_present = {o.person_id for o in doc.objects.values()}
    pelvis_layer_by_pid: Dict[int, object] = {}
    pelvis_in_cf_by_frame_pid: Dict[Tuple[int, int], Tuple[float, float]] = {}

    for pid in sorted(persons_present):
        pelvis_layer = _get_or_create_layer(
            camera_layer, curves, f"p{pid}_pelvis"
        )
        pelvis_layer_by_pid[pid] = pelvis_layer

        wrote_any = False
        if doc.persons:
            for frame, per in sorted(doc.persons.items()):
                pf = per.get(pid)
                if pf is None:
                    continue
                pelvis_px_x, pelvis_px_y = pf.pelvis_px
                pelvis_yup = (pelvis_px_x, H - pelvis_px_y)
                t1 = camera_params_by_frame.get(frame, _t1_identity_params())
                # Invert T1 to put pelvis in camera-stabilised coords.
                px, py = _invert_affine(pelvis_yup[0], pelvis_yup[1], *t1)
                _set_layer_affine(pelvis_layer, frame, px, py, 0.0, 1.0, 1.0)
                pelvis_in_cf_by_frame_pid[(frame, pid)] = (px, py)
                wrote_any = True
        if not wrote_any:
            # v2 / absent persons data: pelvis stays at identity. Still
            # key one frame so the Transform exists.
            _set_layer_affine(pelvis_layer, 0, 0.0, 0.0, 0.0, 1.0, 1.0)

    # ---- T3: one body-part Layer per object, keyed from bone (pt0, pt1).
    for obj_name, obj in doc.objects.items():
        pid = obj.person_id
        pelvis_layer = pelvis_layer_by_pid.get(pid)
        if pelvis_layer is None:
            # Object references a pid without a parent — fall back to
            # placing the body-part layer directly under camera_track.
            pelvis_layer = camera_layer

        part_layer = _get_or_create_layer(pelvis_layer, curves, obj_name)

        if not obj.frames:
            continue

        # Record T3 params per frame so we can invert them when placing
        # knots in bone-local coords.
        t3_params_by_frame: Dict[int, Tuple[float, float, float]] = {}
        for frame, frame_data in sorted(obj.frames.items()):
            if frame_data.bone is None:
                continue
            (p0x, p0y), (p1x, p1y) = frame_data.bone
            # Y-flip bone endpoints into Nuke coords.
            p0_yup = (p0x, H - p0y)
            p1_yup = (p1x, H - p1y)
            # Put the bone origin into camera-stabilised coords (apply T1⁻¹).
            t1 = camera_params_by_frame.get(frame, _t1_identity_params())
            p0_cf = _invert_affine(p0_yup[0], p0_yup[1], *t1)
            p1_cf = _invert_affine(p1_yup[0], p1_yup[1], *t1)
            # Then into pelvis-local coords (apply T2⁻¹). T2 is a pure
            # translate, so this is just a subtraction.
            pelvis_cf = pelvis_in_cf_by_frame_pid.get((frame, pid), (0.0, 0.0))
            p0_pf = (p0_cf[0] - pelvis_cf[0], p0_cf[1] - pelvis_cf[1])
            p1_pf = (p1_cf[0] - pelvis_cf[0], p1_cf[1] - pelvis_cf[1])
            # Bone angle in pelvis-local.
            dx = p1_pf[0] - p0_pf[0]
            dy = p1_pf[1] - p0_pf[1]
            # Nuke rotation is in degrees, counter-clockwise positive.
            bone_angle_deg = math.degrees(math.atan2(dy, dx))
            _set_layer_affine(
                part_layer, frame, p0_pf[0], p0_pf[1], bone_angle_deg, 1.0, 1.0
            )
            t3_params_by_frame[frame] = (p0_pf[0], p0_pf[1], bone_angle_deg)

        # ---- Shape with knots in bone-local coords.
        shape = nuke.rotopaint.Shape(curves, type=curve_type)
        shape.name = f"{obj_name}:Shape"
        part_layer.append(shape)

        sorted_frames = sorted(obj.frames.keys())
        first_frame = sorted_frames[0]
        first_pts = obj.frames[first_frame].points
        t3_first = t3_params_by_frame.get(first_frame, (0.0, 0.0, 0.0))
        for p in first_pts:
            # Transform the plate-pixel knot into bone-local coords by
            # applying T1⁻¹, T2⁻¹, T3⁻¹ in sequence.
            knot_yup = (p.x, H - p.y)
            t1 = camera_params_by_frame.get(first_frame, _t1_identity_params())
            knot_cf = _invert_affine(knot_yup[0], knot_yup[1], *t1)
            pelvis_cf = pelvis_in_cf_by_frame_pid.get(
                (first_frame, pid), (0.0, 0.0)
            )
            knot_pf = (knot_cf[0] - pelvis_cf[0], knot_cf[1] - pelvis_cf[1])
            knot_bl = _invert_translate_rotate(
                knot_pf[0], knot_pf[1], t3_first[0], t3_first[1], t3_first[2]
            )
            cp = nuke.rotopaint.AnimControlPoint(knot_bl[0], knot_bl[1])
            shape.append(cp)

        # Visibility — same convention as legacy.
        shape.setVisible(sorted_frames[0] - 1, False)
        for frame, vis_value in obj.visibility.items():
            shape.setVisible(frame, bool(vis_value))
        shape.setVisible(sorted_frames[-1] + 1, False)

        # Per-frame knot updates (articulation signal — flat whenever
        # the limb shape doesn't change).
        for frame, frame_data in obj.frames.items():
            vis = obj.visibility.get(frame)
            if vis is None:
                shape.setVisible(frame, False)
            else:
                shape.setVisible(frame, bool(vis))

            t3 = t3_params_by_frame.get(frame)
            if t3 is None:
                continue
            t1 = camera_params_by_frame.get(frame, _t1_identity_params())
            pelvis_cf = pelvis_in_cf_by_frame_pid.get((frame, pid), (0.0, 0.0))
            for cp, p in zip(shape, frame_data.points):
                knot_yup = (p.x, H - p.y)
                knot_cf = _invert_affine(knot_yup[0], knot_yup[1], *t1)
                knot_pf = (knot_cf[0] - pelvis_cf[0], knot_cf[1] - pelvis_cf[1])
                knot_bl = _invert_translate_rotate(
                    knot_pf[0], knot_pf[1], t3[0], t3[1], t3[2]
                )
                cp.center.addPositionKey(frame, (knot_bl[0], knot_bl[1]))

    if perspective_warn_frames:
        nuke.tprint(
            f"[rotobot-nuke] hierarchical import: dropped perspective component "
            f"from H2d on {len(perspective_warn_frames)} frame(s): "
            f"{perspective_warn_frames[:8]}{'…' if len(perspective_warn_frames) > 8 else ''}"
        )

    _label_roto(roto, doc, hierarchical=True)
    return roto


# ---------------------------------------------------------------------------
# Common helpers.

def _label_roto(roto, doc: LozengeDoc, hierarchical: bool = False) -> None:
    """Set the Roto node's label knob."""
    people = sorted({k.split(":")[0] for k in doc.objects.keys()})
    mode = "hierarchical" if hierarchical else "legacy"
    roto["label"].setValue(
        "Tokgan JSON Import\n"
        f"Schema: {doc.schema_version}\n"
        f"People: {len(people)}\n"
        f"Mode: {mode}"
    )


def build_roto(
    doc: LozengeDoc,
    *,
    roto_name: str = "Tokgan_Roto",
    curve_type: str = "bspline",
    set_project_fps: bool = False,
    undersample_tolerance: Optional[float] = None,
    mode: str = "legacy",
    cache_path: Optional[str] = None,
):
    """Build a Nuke ``Roto`` node from the parsed document and return it.

    Args:
        doc: parsed Rotobot JSON (see :func:`rotobot_nuke.reader.load_json`).
        roto_name: name hint for the created Roto node (Nuke uniquifies).
        curve_type: ``"bspline"`` (default) or ``"bezier"``. Bezier mode
            additionally keyframes tangent handles. **``bezier`` is only
            supported in ``mode="legacy"``** today; hierarchical mode
            currently writes bspline shapes only (bezier tangents in
            bone-local coords is a follow-up).
        set_project_fps: when ``True``, pokes ``nuke.root()["fps"]`` to the
            doc's FPS. Default ``False``.
        undersample_tolerance: when set to a positive number, run
            :func:`rotobot_nuke.undersample.undersample_doc` with that
            tolerance before building keyframes.
        mode: ``"legacy"`` (default, PR #1 shape) or ``"hierarchical"``
            (three transform cascade per issue #279 design doc). The
            default stays ``"legacy"`` for one release; flip after
            real-Nuke validation lands.
        cache_path: optional path to the source JSON. When supplied, the
            built roto's ``curves`` knob text is cached under
            ``~/.cache/rotobot_nuke/`` on first import, and subsequent
            imports of the same (path, mtime, mode, curve_type,
            undersample_tolerance) tuple reload the cached text via
            ``fromScript()`` — a measured **188× speedup** on a dense
            323-object UHD plate (82s → 0.44s). The slow Python-API
            build runs once; later imports are near-instant.

    Returns:
        The newly created ``nuke.Node`` (Roto).
    """
    if curve_type not in {"bspline", "bezier"}:
        raise ValueError(
            f"curve_type must be 'bspline' or 'bezier'; got {curve_type!r}"
        )
    if mode not in {"legacy", "hierarchical"}:
        raise ValueError(
            f"mode must be 'legacy' or 'hierarchical'; got {mode!r}"
        )

    if undersample_tolerance is not None and undersample_tolerance > 0:
        doc = undersample_doc(doc, tolerance=float(undersample_tolerance))

    if set_project_fps:
        nuke.root()["fps"].setValue(doc.fps)

    # Cache fast-path. When the cached curves-text exists for this
    # (path, mtime, build-params) tuple, create an empty Roto and
    # install the whole thing with one ``fromScript()`` call.
    cache_key = None
    if cache_path:
        from . import cache as _cache

        cache_key = _cache.make_key(
            cache_path,
            mode=mode,
            curve_type=curve_type,
            undersample_tolerance=undersample_tolerance,
        )
        cached_text = _cache.get(cache_key)
        if cached_text is not None:
            roto = nuke.nodes.Roto(name=roto_name)
            roto["curves"].fromScript(cached_text)
            _label_roto(roto, doc, hierarchical=(mode == "hierarchical"))
            return roto

    if mode == "legacy":
        roto = _build_roto_legacy(
            doc, roto_name=roto_name, curve_type=curve_type
        )
    else:
        roto = _build_roto_hierarchical(
            doc, roto_name=roto_name, curve_type=curve_type
        )

    if cache_key is not None:
        from . import cache as _cache

        try:
            _cache.put(cache_key, roto["curves"].toScript())
        except Exception:
            # Cache miss is non-fatal — we still have the built roto.
            pass

    return roto
