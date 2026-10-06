"""Nuke-side half of rotobot-nuke: turn a :class:`LozengeDoc` into a Roto node.

Imports ``nuke`` + ``nuke.rotopaint`` at module load; must be run inside
Nuke's embedded Python (or with those modules mocked, as the test suite
does).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import nuke
import nuke.rotopaint

from .reader import LozengeDoc, LozengeObject

if TYPE_CHECKING:  # pragma: no cover
    pass


FINGER_ORDER = ("A_thumb", "B_index", "C_middle", "D_ring", "E_pinky")


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


def _build_shape_for_object(
    obj: LozengeObject,
    curves,
    part_layer,
    H: int,
    curve_type: str,
) -> None:
    """Build one Roto Shape for ``obj`` under ``part_layer`` (Nuke-side only)."""
    shape = nuke.rotopaint.Shape(curves, type=curve_type)
    shape.name = f"{obj.key}:Shape"
    part_layer.append(shape)

    frames = obj.frames
    if not frames:
        return

    # Control points from the first frame — Nuke needs them existing before
    # we can keyframe their centers.
    first_frame = min(frames.keys())
    first_pts = frames[first_frame].points

    for p in first_pts:
        x = p.x
        y = H - p.y  # Y-down (image) → Y-up (Nuke)
        cp = nuke.rotopaint.AnimControlPoint(x, y)
        shape.append(cp)

    # Visibility — hide outside the shape's defined frame range, honour
    # explicit per-frame visibility keys inside it.
    all_frames = sorted(frames.keys())
    shape.setVisible(all_frames[0] - 1, False)
    for frame, vis_value in obj.visibility.items():
        shape.setVisible(frame, bool(vis_value))
    shape.setVisible(all_frames[-1] + 1, False)

    # Animate per-frame.
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


def build_roto(
    doc: LozengeDoc,
    *,
    roto_name: str = "Tokgan_Roto",
    curve_type: str = "bspline",
    set_project_fps: bool = False,
):
    """Build a Nuke ``Roto`` node from the parsed document and return it.

    Args:
        doc: parsed Rotobot JSON (see :func:`rotobot_nuke.reader.load_json`).
        roto_name: name hint for the created Roto node (Nuke uniquifies).
        curve_type: ``"bspline"`` (default) or ``"bezier"``. Bezier mode
            additionally keyframes tangent handles.
        set_project_fps: when ``True``, pokes ``nuke.root()["fps"]`` to the
            doc's FPS. Default ``False`` — do not mutate project settings.

    Returns:
        The newly created ``nuke.Node`` (Roto).
    """
    if curve_type not in {"bspline", "bezier"}:
        raise ValueError(
            f"curve_type must be 'bspline' or 'bezier'; got {curve_type!r}"
        )

    if set_project_fps:
        nuke.root()["fps"].setValue(doc.fps)

    _, H = doc.resolution

    roto = nuke.nodes.Roto(name=roto_name)
    curves = roto["curves"]
    root = curves.rootLayer

    # Preserve the historical +1/-1 identity translation on the root transform.
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

        _build_shape_for_object(obj, curves, part_layer, H, curve_type)

    people = sorted({k.split(":")[0] for k in doc.objects.keys()})
    roto["label"].setValue(
        "Tokgan JSON Import\n"
        f"Schema: {doc.schema_version}\n"
        f"People: {len(people)}"
    )
    return roto
