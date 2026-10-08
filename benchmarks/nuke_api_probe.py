"""Narrower probe: inspect the real AnimCurve + AnimControlPoint API shapes."""
import sys
sys.path.insert(0, "/media/sam/projects/github/rotobot-nuke/src")

import nuke, nuke.rotopaint
from rotobot_nuke import load_json, build_roto

JSON_PATH = "/tmp/claude-1000/-media-sam-projects-gitlab-tokgan-tokgan-wedge-runner/fbd20970-306d-4411-b771-63874d0eb0d7/scratchpad/real_plate_bench/pexels_11054569_3840x2160_12s__1791342654907220.json"

doc = load_json(JSON_PATH)
hier = build_roto(doc, roto_name="Hier_Probe", mode="hierarchical")

root = hier["curves"].rootLayer
camera = next(x for x in root if hasattr(x, "name") and x.name == "camera_track")
pelvis = next(x for x in camera if hasattr(x, "name") and x.name.endswith("_pelvis"))
part = next(x for x in pelvis if hasattr(x, "name") and ":" in x.name)
xform = part.getTransform()

print("=== AnimCurve API (translate.x of T3)")
tx = xform.getTranslationAnimCurve(0)
print("type:", type(tx).__name__)
for a in sorted(a for a in dir(tx) if not a.startswith("_")):
    print(" ", a)

shape = next(x for x in part if type(x).__name__ == "Shape")
cp = list(shape)[0]
print("\n=== AnimControlPoint API")
print("type:", type(cp).__name__)
for a in sorted(a for a in dir(cp) if not a.startswith("_")):
    print(" ", a)

print("\n=== cp.center API")
print("type:", type(cp.center).__name__)
for a in sorted(a for a in dir(cp.center) if not a.startswith("_")):
    print(" ", a)
nuke.scriptClear()
