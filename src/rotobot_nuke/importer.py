"""Nuke-side half of rotobot-nuke: turn a :class:`LozengeDoc` into a Roto node.

Imports ``nuke`` + ``nuke.rotopaint`` at module load; must be run inside
Nuke's embedded Python (or with those modules mocked, as the test suite
does).

Two build modes available on :func:`build_roto`:

* ``mode="legacy"`` (default) — flat ``person/region/side/part`` layer
  hierarchy; every spline knot keyframed at its plate-pixel position.
  Original PR #1 shape.
* ``mode="hierarchical"`` — three transform-cascaded layers
  (``camera_track → pelvis → body-part``) carrying the exact H2d
  homography / pelvis / bone pose respectively, with spline knots stored
  in bone-local coordinates so the leaf curves move only when
  articulation genuinely changes. Built on
  :func:`rotobot_nuke.hierarchy.decompose`, which holds missing data.
* ``mode="auto"`` — hierarchical for a v3 JSON with ``camera`` /
  ``persons`` data, legacy otherwise.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import nuke
import nuke.rotopaint

import dataclasses

from .hierarchy import ROUND_TRIP_TOLERANCE_PX, Hierarchy, decompose, round_trip_error
from .reader import (
    CameraFrame,
    LozengeDoc,
    LozengeFrame,
    LozengeObject,
    PersonFrame,
    source_frame_offset,
)
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

def _key_visibility(shape, obj: LozengeObject, offset: int) -> None:
    """Key ``shape`` visible on the frames the JSON has ``obj`` on, and hidden
    on the frame before, the frame after, and every frame in between that
    it is missing from.

    A part that leaves the plate (fingers out of frame) simply has no entry
    for those frames, and Nuke holds a visibility key until the next one,
    so without an explicit hidden key on each missing frame the shape stayed
    on, drifting between its last and next keys. ``obj.visibility`` is the
    record of which frames the part is on: undersampling drops keyframes
    from ``obj.frames`` but keeps ``visibility`` whole, so a thinned-out
    frame stays visible.

    Every key lands on ``JSON frame + offset``.
    """
    span = set(obj.frames) | set(obj.visibility)
    if not span:
        return
    for frame in range(min(span) - 1, max(span) + 2):
        vis = obj.visibility.get(frame)
        shape.setVisible(frame + offset, False if vis is None else bool(vis))


def _build_shape_for_object_legacy(
    obj: LozengeObject,
    curves,
    part_layer,
    H: int,
    curve_type: str,
    offset: int = 0,
) -> None:
    """Build one Roto Shape for ``obj`` under ``part_layer`` (legacy mode).

    Every key lands on ``JSON frame + offset`` (see
    :func:`rotobot_nuke.reader.source_frame_offset`).
    """
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

    _key_visibility(shape, obj, offset)

    use_bspline = curve_type == "bspline"
    for frame, frame_data in frames.items():
        pts = frame_data.points
        key = frame + offset

        for cp, p in zip(shape, pts):
            x = p.x
            y = H - p.y
            cp.center.addPositionKey(key, (x, y))
            if not use_bspline:
                lx = p.left_x
                ly = H - p.left_y
                rx = p.right_x
                ry = H - p.right_y
                cp.leftTangent.addPositionKey(key, (lx - x, ly - y))
                cp.rightTangent.addPositionKey(key, (rx - x, ry - y))


def _build_roto_legacy(doc: LozengeDoc, roto_name: str, curve_type: str):
    """PR #1 flat-hierarchy build path."""
    _, H = doc.resolution
    offset = source_frame_offset(doc)

    roto = nuke.nodes.Roto(name=roto_name)
    curves = roto["curves"]
    root = curves.rootLayer

    # No root offset: a historical (+1, -1) px translation was keyed here. Measured through Nuke 16's own
    # evaluation against the source matte (Rotobot-Next, 5362065, 2026-10-17) it put every shape ~1.4 px
    # off. The JSON is in plate pixels already.

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

        _build_shape_for_object_legacy(
            obj, curves, part_layer, H, curve_type, offset
        )

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


def _set_layer_matrix(layer, frame: int, M: Tuple[float, ...]) -> None:
    """Keyframe a Layer's 4x4 extra matrix with a 2D homography ``M``
    (3x3, row-major, Y-up).

    Nuke stores the extra matrix row-major (element ``4i+j``) and applies
    it projectively to the layer's children; a 2D homography sits in rows
    and columns 0, 1 and 3. Measured in Nuke 16.0v8 (2026-10-29) by
    rendering a shape under a perspective matrix: the transposed layout
    draws nothing.
    """
    a, b, tx, c, d, ty, p, q, r = M
    rows = ((a, b, 0.0, tx), (c, d, 0.0, ty), (0.0, 0.0, 1.0, 0.0), (p, q, 0.0, r))
    xform = layer.getTransform()
    for i in range(4):
        for j in range(4):
            xform.getExtraMatrixAnimCurve(i, j).addKey(frame, rows[i][j])


def _build_roto_hierarchical(
    doc: LozengeDoc, hier: Hierarchy, roto_name: str, curve_type: str
) -> "nuke.Node":
    """Three-transform-cascade build on :func:`rotobot_nuke.hierarchy.decompose`.

    Layer hierarchy:

        root
        └── camera_track           T1 = the exact homography H2d (extra matrix)
            ├── p0_pelvis          T2 = pelvis in stabilised pixels, per frame
            │   ├── p0:leg:R:thigh T3 = bone origin (rel. pelvis) + bone angle
            │   │   └── Shape      knots in bone-local coords
            │   └── ... (one Layer per body-part, flat under pelvis)
            └── p1_pelvis          same shape for a second person
                └── ...

    ``decompose`` holds missing camera / pelvis / bone frames rather than
    dropping to identity, so composed plate positions never jump. Its
    hierarchy is Y-down; Nuke is Y-up. With ``F(x, y) = (x, H - y)`` and
    ``S = diag(1, -1)``: T1 becomes ``F·H2d·F``, T2 ``F(pelvis)``, T3
    translates by ``S·t`` and rotates by ``-angle``, and a local knot ``l``
    becomes ``S·l``.

    Nuke evaluates a shape's curve in its layer's space and then maps it
    through the cascade, so in bspline mode the control points are solved
    in bone-local space: the drawn curve passes through every JSON point
    exactly, perspective included. Between points a projected cubic is
    not the plate-space cubic legacy draws; measured in Nuke 16.0v8
    (2026-10-29) on ``tests/fixtures/v3_real_cut.json`` the mattes agree
    to IoU 0.9996 / 1 px (Bezier 0.9999 / 1 px), and to 4 px at ten times
    that perspective.
    Solving in plate space and carrying the control points through drew
    11 px off instead.

    Every key lands on ``JSON frame + offset`` (see
    :func:`rotobot_nuke.reader.source_frame_offset`).
    """
    _, H = doc.resolution
    offset = source_frame_offset(doc)

    roto = nuke.nodes.Roto(name=roto_name)
    curves = roto["curves"]
    root = curves.rootLayer
    # (no +1/-1 root offset -- see _build_roto_legacy)
    curves.changed()

    # ---- T1: camera_track, the exact (Y-up) homography on every frame.
    camera_layer = _get_or_create_layer(root, curves, "camera_track")
    for frame in hier.frames:
        _set_layer_matrix(
            camera_layer, frame + offset, _conjugate_h2d_yflip(hier.camera[frame], H)
        )

    # ---- T2: one pelvis Layer per person, in stabilised pixels.
    pelvis_layer_by_pid: Dict[int, object] = {}
    for pid in hier.pids:
        pelvis_layer = _get_or_create_layer(camera_layer, curves, f"p{pid}_pelvis")
        pelvis_layer_by_pid[pid] = pelvis_layer
        for frame in hier.frames:
            px, py = hier.pelvis[pid][frame]
            _set_layer_affine(pelvis_layer, frame + offset, px, H - py)

    use_bspline = curve_type == "bspline"
    for obj_name, obj in doc.objects.items():
        part_layer = _get_or_create_layer(
            pelvis_layer_by_pid[obj.person_id], curves, obj_name
        )
        if not obj.frames:
            continue

        # ---- T3: bone origin relative to the pelvis, rotated by the bone.
        for frame, part in sorted(hier.parts[obj_name].items()):
            _set_layer_affine(
                part_layer, frame + offset, part.tx, -part.ty, -part.angle
            )

        # ---- Shape with knots in bone-local coords (Y-up).
        knots: Dict[int, List[Tuple[Tuple[float, float], ...]]] = {}
        for frame, pts in hier.knots[obj_name].items():
            if use_bspline:
                C = interpolating_control_points([(p.x, p.y) for p in pts])
                knots[frame] = [((x, -y),) for x, y in C]
            else:
                knots[frame] = [
                    ((p.x, -p.y), (p.left_x - p.x, p.y - p.left_y),
                     (p.right_x - p.x, p.y - p.right_y))
                    for p in pts
                ]

        shape = nuke.rotopaint.Shape(curves, type=curve_type)
        shape.name = f"{obj_name}:Shape"
        part_layer.append(shape)

        sorted_frames = sorted(obj.frames)
        for k in knots[sorted_frames[0]]:
            shape.append(nuke.rotopaint.AnimControlPoint(*k[0]))

        _key_visibility(shape, obj, offset)

        for frame in sorted_frames:
            key = frame + offset
            for cp, k in zip(shape, knots[frame]):
                cp.center.addPositionKey(key, k[0])
                if not use_bspline:
                    cp.leftTangent.addPositionKey(key, k[1])
                    cp.rightTangent.addPositionKey(key, k[2])

    if hier.held:
        nuke.tprint(
            f"[rotobot-nuke] hierarchical import: held {len(hier.held)} missing "
            f"camera / pelvis / bone value(s): {hier.held[:8]}"
            f"{'…' if len(hier.held) > 8 else ''}"
        )

    _label_roto(roto, doc, hierarchical=True)
    return roto


# ---------------------------------------------------------------------------
# Common helpers.

def _json_last_frame(doc: LozengeDoc) -> Optional[int]:
    """The last JSON frame keyed by any object, or ``None`` when none is."""
    return max((f for obj in doc.objects.values() for f in obj.frames), default=None)


def _set_project_frame_range(doc: LozengeDoc) -> None:
    """Put the project's frame range on the plate's own frame numbers.

    Only when the JSON says what they are (``frame_start``): the timeline
    then opens on the shot rather than on 1..N with every key off the end.
    An older JSON without it leaves the project range alone, as before.
    """
    last = _json_last_frame(doc)
    if doc.frame_start is None or last is None:
        return
    offset = source_frame_offset(doc)
    root = nuke.root()
    # Last first: moving first_frame to 1001 while last_frame is still a
    # 1-based default would ask for a backwards range on the way there.
    # JSON frame 1 is frame_start by definition, so the range opens there.
    root["last_frame"].setValue(last + offset)
    root["first_frame"].setValue(1 + offset)


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


# ---------------------------------------------------------------------------
# B-spline control points
# ---------------------------------------------------------------------------
#
# Nuke's rotopaint "bspline" is a closed UNIFORM CUBIC B-spline (measured through shape.evaluate in
# Nuke 16: an 8-point circle of radius 200 draws at 180.47 = (C[i-1] + 4 C[i] + C[i+1]) / 6). Its
# control points are NOT on the curve, so writing the JSON's on-curve points as B-spline points pulled
# every shape inside the matte (up to 10.4 px on real shapes). In bspline mode the importer now writes
# the control points whose curve passes THROUGH the JSON's points. For a shape the engine fitted as a
# B-spline ("spline": "bspline3_uniform_closed") those points are exactly (C[i-1] + 4 C[i] + C[i+1])/6,
# so this recovers its control points exactly -- Nuke draws the fitted curve; for any other shape the
# curve interpolates its knots.


def interpolating_control_points(points: List[Tuple[float, float]], iters: int = 60) -> List[Tuple[float, float]]:
    """Closed uniform cubic B-spline control points C with (C[i-1] + 4 C[i] + C[i+1]) / 6 = P[i].
    Jacobi iteration on a strictly diagonally dominant system (error halves per step); plain Python."""
    n = len(points)
    if n < 3:
        return list(points)
    C = [tuple(p) for p in points]
    for _ in range(iters):
        C = [((6 * points[i][0] - C[i - 1][0] - C[(i + 1) % n][0]) / 4.0,
              (6 * points[i][1] - C[i - 1][1] - C[(i + 1) % n][1]) / 4.0) for i in range(n)]
    return C


def bspline_control_doc(doc: LozengeDoc) -> LozengeDoc:
    """A copy of ``doc`` whose frame points are the B-spline control points that pass through them."""
    objects = {}
    for key, obj in doc.objects.items():
        frames = {}
        for f, fr in obj.frames.items():
            C = interpolating_control_points([(p.x, p.y) for p in fr.points])
            pts = [dataclasses.replace(p, x=cx, y=cy, left_x=cx, left_y=cy, right_x=cx, right_y=cy)
                   for p, (cx, cy) in zip(fr.points, C)]
            frames[f] = dataclasses.replace(fr, points=pts)
        objects[key] = dataclasses.replace(obj, frames=frames)
    return dataclasses.replace(doc, objects=objects)


def build_roto(
    doc: LozengeDoc,
    *,
    roto_name: str = "Tokgan_Roto",
    curve_type: str = "bspline",
    set_project_fps: bool = False,
    undersample_tolerance: Optional[float] = None,
    mode: str = "legacy",
    cache_path: Optional[str] = None,
    set_frame_range: bool = True,
):
    """Build a Nuke ``Roto`` node from the parsed document and return it.

    Args:
        doc: parsed Rotobot JSON (see :func:`rotobot_nuke.reader.load_json`).
        roto_name: name hint for the created Roto node (Nuke uniquifies).
        curve_type: ``"bspline"`` (default) or ``"bezier"``. Bezier mode
            additionally keyframes tangent handles.
        set_project_fps: when ``True``, pokes ``nuke.root()["fps"]`` to the
            doc's FPS. Default ``False``.
        undersample_tolerance: when set to a positive number, run
            :func:`rotobot_nuke.undersample.undersample_doc` with that
            tolerance before building keyframes.
        mode: ``"legacy"`` (default, PR #1 shape: every knot keyed in
            plate pixels), ``"hierarchical"`` (``camera_track → pelvis →
            body-part`` layers carrying the v3 camera, pelvis and bones;
            see :func:`_build_roto_hierarchical`) or ``"auto"``
            (hierarchical when the JSON has v3 ``camera`` / ``persons``
            data, legacy otherwise -- what the menu uses). A hierarchy
            that does not recompose the JSON to within
            :data:`rotobot_nuke.hierarchy.ROUND_TRIP_TOLERANCE_PX` falls
            back to legacy, with a message.
        cache_path: optional path to the source JSON. When supplied, the
            built roto's ``curves`` knob text is cached under
            ``~/.cache/rotobot_nuke/`` on first import, and subsequent
            imports of the same (path, mtime, mode, curve_type,
            undersample_tolerance, frame_start) tuple reload the cached text via
            ``fromScript()`` — a measured **188× speedup** on a dense
            323-object UHD plate (82s → 0.44s). The slow Python-API
            build runs once; later imports are near-instant.
        set_frame_range: when ``True`` (default) and the JSON records
            ``frame_start``, sets the project's ``first_frame`` /
            ``last_frame`` to the shot's plate frame numbers. A JSON
            without ``frame_start`` never touches the project range.

    Frame numbering: JSON frames are 1..N. When the JSON records
    ``frame_start`` (the plate frame number of JSON frame 1), every key
    lands on the plate's own numbers -- JSON frame ``f`` on Nuke frame
    ``f + frame_start - 1`` -- so a shot read from ``shot.1001.exr`` keys
    1001.. and its hide key sits on 1000. Without it, keys stay on 1..N.

    Returns:
        The newly created ``nuke.Node`` (Roto).
    """
    if curve_type not in {"bspline", "bezier"}:
        raise ValueError(
            f"curve_type must be 'bspline' or 'bezier'; got {curve_type!r}"
        )
    if mode not in {"legacy", "hierarchical", "auto"}:
        raise ValueError(
            f"mode must be 'legacy', 'hierarchical' or 'auto'; got {mode!r}"
        )
    if mode == "auto":
        mode = "hierarchical" if (doc.camera or doc.persons) else "legacy"

    # Before undersampling, so the range always spans the whole shot.
    if set_frame_range:
        _set_project_frame_range(doc)

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
            # "@cp2": control points through the shape + no root offset (2026-10-17); "h2": the
            # hierarchy is built on hierarchy.decompose (2026-10-29); "v2": hidden on frames a
            # part is missing from (2026-10-30). Older cached curves must not be reused
            curve_type=curve_type + ("@cp2h2" if mode == "hierarchical" else "@cp2") + "v2",
            undersample_tolerance=undersample_tolerance,
            # Keys are baked onto plate frame numbers, so a different
            # frame_start must never reuse them.
            frame_start=doc.frame_start,
        )
        cached_text = _cache.get(cache_key)
        if cached_text is not None:
            roto = nuke.nodes.Roto(name=roto_name)
            roto["curves"].fromScript(cached_text)
            _label_roto(roto, doc, hierarchical=(mode == "hierarchical"))
            return roto

    hier = None
    if mode == "hierarchical":
        hier = decompose(doc)
        err = round_trip_error(doc, hier)
        if err > ROUND_TRIP_TOLERANCE_PX:
            nuke.tprint(
                f"[rotobot-nuke] hierarchy recomposes the JSON {err:.3f} px off "
                f"(tolerance {ROUND_TRIP_TOLERANCE_PX} px); importing flat (legacy) instead"
            )
            hier = None
            cache_key = None  # the key says hierarchical

    if hier is not None:
        roto = _build_roto_hierarchical(
            doc, hier, roto_name=roto_name, curve_type=curve_type
        )
    else:
        if curve_type == "bspline":
            doc = bspline_control_doc(doc)
        roto = _build_roto_legacy(
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
