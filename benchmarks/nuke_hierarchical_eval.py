"""Evaluate hierarchical importer end-to-end against plate ground truth.

For a specific body-part at frame 1, compute where Nuke renders the
stored bone-local knots after cascading T1 * T2 * T3, and compare
against the raw plate pixel positions from the JSON (Y-flipped).

If the cascade math is right the two should match to within ~0.01 px.
If they don't, the delta tells us which transform level is wrong.
"""
import sys
sys.path.insert(0, "/media/sam/projects/github/rotobot-nuke/src")

import nuke, nuke.rotopaint
from rotobot_nuke import load_json, build_roto

JSON_PATH = "/tmp/claude-1000/-media-sam-projects-gitlab-tokgan-tokgan-wedge-runner/fbd20970-306d-4411-b771-63874d0eb0d7/scratchpad/real_plate_bench/pexels_11054569_3840x2160_12s__1791342654907220.json"

doc = load_json(JSON_PATH)
H = doc.resolution[1]
hier = build_roto(doc, roto_name="Hier", mode="hierarchical")

root = hier["curves"].rootLayer
camera = next(x for x in root if hasattr(x, "name") and x.name == "camera_track")
pelvis = next(x for x in camera if hasattr(x, "name") and x.name.endswith("_pelvis"))
part = next(x for x in pelvis if hasattr(x, "name") and ":" in x.name)
shape = next(x for x in part if type(x).__name__ == "Shape")

obj_name = part.name
print(f"=== Probing {obj_name} at frame 1 ===")

# Original plate-space knots per JSON (Y-down).
json_obj = doc.objects[obj_name]
jkeys = sorted(json_obj.frames.keys())
f = jkeys[1] if len(jkeys) > 1 else jkeys[0]
plate_pts = [(p.x, p.y) for p in json_obj.frames[f].points[:3]]  # first 3 knots
print(f"JSON frame {f}, first 3 knot plate positions (Y-down):")
for px, py in plate_pts:
    print(f"  plate=({px:.2f}, {py:.2f})  y-up=({px:.2f}, {H - py:.2f})")

# Where Nuke evaluates each transform at this frame.
def _xform_at(xform, frame):
    tx = xform.getTranslationAnimCurve(0).evaluate(frame)
    ty = xform.getTranslationAnimCurve(1).evaluate(frame)
    rz = xform.getRotationAnimCurve(2).evaluate(frame)
    sx = xform.getScaleAnimCurve(0).evaluate(frame)
    sy = xform.getScaleAnimCurve(1).evaluate(frame)
    return (tx, ty, rz, sx, sy)

t1 = _xform_at(camera.getTransform(), f)
t2 = _xform_at(pelvis.getTransform(), f)
t3 = _xform_at(part.getTransform(), f)
print(f"\nT1 (camera): tx={t1[0]:.4f} ty={t1[1]:.4f} rot={t1[2]:.4f} sx={t1[3]:.4f} sy={t1[4]:.4f}")
print(f"T2 (pelvis): tx={t2[0]:.4f} ty={t2[1]:.4f} rot={t2[2]:.4f} sx={t2[3]:.4f} sy={t2[4]:.4f}")
print(f"T3 (part):   tx={t3[0]:.4f} ty={t3[1]:.4f} rot={t3[2]:.4f} sx={t3[3]:.4f} sy={t3[4]:.4f}")

# What position does Nuke evaluate for the first 3 knots at this frame?
cps = list(shape)[:3]
print(f"\nNuke-rendered knot positions at frame {f}:")
for cp in cps:
    stored = cp.center.getPosition(0)  # bone-local coord at frame 0
    rendered = cp.center.getPosition(f)
    print(f"  stored(f=0): {stored}  stored(f={f}): {rendered}")

# Rendered in bone-local coords is what cp.center.getPosition returns.
# To get the plate-rendered position we need to apply T3 * T2 * T1
# cascade manually (since we're in nuke -t without a Viewer).
import math

def _apply_srt(x, y, tx, ty, rot_deg, sx, sy):
    """SRT forward: output = translate + rotate(scale(input))."""
    sx_in = x * sx
    sy_in = y * sy
    cos_r = math.cos(math.radians(rot_deg))
    sin_r = math.sin(math.radians(rot_deg))
    rx = sx_in * cos_r - sy_in * sin_r
    ry = sx_in * sin_r + sy_in * cos_r
    return (rx + tx, ry + ty)

print("\n=== Cascade: T1 * T2 * T3 applied to stored bone-local knot at frame f ===")
for i, cp in enumerate(cps):
    stored = cp.center.getPosition(f)   # fixed: use the SAME frame as the transforms
    bl_x, bl_y = stored.x if hasattr(stored, 'x') else stored[0], stored.y if hasattr(stored, 'y') else stored[1]
    # Apply T3, T2, T1 in sequence (inside-out).
    af_t3 = _apply_srt(bl_x, bl_y, *t3)
    af_t2 = _apply_srt(af_t3[0], af_t3[1], *t2)
    af_t1 = _apply_srt(af_t2[0], af_t2[1], *t1)
    plate_px, plate_py = plate_pts[i]
    expected_yup = (plate_px, H - plate_py)
    delta = (af_t1[0] - expected_yup[0], af_t1[1] - expected_yup[1])
    print(f"knot {i}: cascade→{af_t1}  expected_yup={expected_yup}  Δ=({delta[0]:+.3f}, {delta[1]:+.3f})")

nuke.scriptClear()
