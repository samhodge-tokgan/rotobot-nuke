# rotobot-nuke

[![PyPI](https://img.shields.io/pypi/v/rotobot-nuke.svg)](https://pypi.org/project/rotobot-nuke/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![tests](https://github.com/samhodge-tokgan/rotobot-nuke/actions/workflows/test.yml/badge.svg)](https://github.com/samhodge-tokgan/rotobot-nuke/actions/workflows/test.yml)

Import `lozenge_bezier_anim` JSON output
([Tokgan](https://tokgan.com)) into The Foundry's **Nuke** as a `Roto`
node — one shape per anatomical segment, keyframed per frame, with a tidy
`person → body → side → part` layer hierarchy.

**You no longer have to pre-set your Nuke project format to the plate size.**
The importer takes the base resolution from the JSON (or from an explicit
`resolution=(w, h)` kwarg); if neither is available it raises a clear error
rather than silently Y-flipping with the wrong height.

## Install

```bash
pip install rotobot-nuke
```

Until the first PyPI release, install from GitHub instead:

```bash
pip install git+https://github.com/samhodge-tokgan/rotobot-nuke
```

The package is pure Python with no dependencies. The `nuke` module comes from
your Nuke install at runtime and is not fetched from PyPI. Only the Nuke
import needs Nuke: reading and undersampling a JSON work in any Python 3.9+
(see [Reducing keyframes without Nuke](#reducing-keyframes-without-nuke)).

### Nuke menu entry

Add one line to `~/.nuke/menu.py` (or your facility `menu.py`):

```python
import rotobot_nuke.menu  # adds a "Rotobot" menu to Nuke's menu bar
```

The **Rotobot** menu then offers:

| Command | What it builds |
|---|---|
| Import Rotobot JSON (B-spline)… | a `Roto` node, every frame keyed |
| Import Rotobot JSON (Bezier)… | the same with Bezier shapes |
| Import Rotobot JSON — balanced undersample… | B-spline, keyframes reduced at tolerance 5 |
| Import Rotobot JSON — aggressive undersample… | B-spline, keyframes reduced at tolerance 10 |
| Import Rotobot JSON — undersample (prompt for tolerance)… | asks for the tolerance |

Each one asks for the JSON and builds a `Roto` node named `Tokgan_Roto`, with
a `person → body → side → part` layer hierarchy.

If `rotobot-nuke` is installed into the same Python that Nuke uses, that's
all you need. If Nuke's embedded Python can't see your site-packages, append the
install prefix explicitly:

```python
import sys
sys.path.append("/path/to/your/site-packages")
import rotobot_nuke.menu
```

### Programmatic use (Python API)

```python
from rotobot_nuke import load_json, build_roto

doc = load_json("/path/to/clip.json")         # v2 JSON: resolution read from file
# or:
doc = load_json("/path/to/legacy_v1.json",     # v1 JSON: resolution must be supplied
                resolution=(1920, 1080))

node = build_roto(doc, curve_type="bspline")   # or "bezier"
print(node.name())                              # => "Tokgan_Roto" (or Nuke-uniquified)
```

#### Camera / person hierarchy (v3 JSON, experimental)

A v3 JSON (Rotobot Next 0.10.0 and later) carries the plate camera and each
person's pelvis. `mode="hierarchical"` builds nested Roto layers from them:

```python
node = build_roto(doc, mode="hierarchical")
# camera_track            the plate camera
#   p0_pelvis             each person's root
#     p0:arm:L:forearm    each body part, positioned and rotated by its bone
```

It is not in the menu yet, for two known reasons:

* the camera layer uses an affine (translate/rotate/scale) approximation of
  the camera solve, so it does not fully stabilise a shot with perspective;
* frames with missing camera or pelvis data are not held, so shapes can jump
  on those frames.

The plate positions of the shapes are still correct. The Silhouette and
After Effects importers already use the exact camera and hold missing data,
through `rotobot_nuke.hierarchy`. Moving this importer onto that code is
planned.

### Undersampling (RDP keyframe reduction)

Rotobot Next writes one keyframe per video frame. A 6 s UHD clip with about
140 segments can carry 8,000+ keyframes, which an artist then scrubs through
for every adjustment. `rotobot-nuke` removes the ones a straight line between
their neighbours already reproduces, using
[Ramer-Douglas-Peucker](https://en.wikipedia.org/wiki/Ramer%E2%80%93Douglas%E2%80%93Peucker_algorithm)
on each part's bone-local state. The algorithm is ported from the
[`key_reduction` branch of `tokgan_silhouette_import`](https://github.com/samhodge-aiml/tokgan_silhouette_import/tree/key_reduction)
(MIT).

#### Reducing keyframes without Nuke

The `rotobot-undersample` command and `undersample_doc()` are pure Python:
no Nuke needed. They write a smaller JSON that **any** importer reads,
including the Silhouette `.fxs` and After Effects converters, and their
camera / person hierarchies.

```bash
rotobot-undersample shapes.json shapes_reduced.json --preset balanced
```

Two kinds of setting, used one at a time:

* **`--preset fine | balanced | coarse`** — for v3 JSON. It measures the
  error in three parts: camera, person root, and each part's motion relative
  to its body. A camera pan alone therefore does not keep every keyframe.
  It also works on v2 JSON, where it measures the body-relative part only.
* **`--tolerance N`** — the original single measurement, in pixel-equivalents
  (default 5). Works on any schema version.

Measured on a real Rotobot Next 0.10.0 shot (24 4K frames, 38 parts,
874 keyframes):

| Setting | Keyframes kept |
|---|---:|
| `--preset fine` | 100% |
| `--preset balanced` | 92% |
| `--preset coarse` | 59% |
| `--tolerance 5` | 91% |
| `--tolerance 10` | 73% |
| `--tolerance 25` | 50% |

Short shots keep proportionally more, because every part keeps its first and
last frame. Across four longer real clips (see
[`benchmarks/real_results_cross_clip.md`](benchmarks/real_results_cross_clip.md))
the presets kept about 98% / 89% / 73%. Review an aggressive reduction
before handing it on. The camera and person blocks are copied through
unchanged, and parts with fewer than three frames, or without per-frame
bones (v1), are left alone.

The `--camera-tolerance`, `--person-tolerance` and `--articulation-tolerance`
options override one part of a preset, e.g.
`--preset balanced --articulation-tolerance 0.1`.

From Python:

```python
from rotobot_nuke import load_json, undersample_doc, PRESET_BALANCED

doc = load_json("shapes.json")
reduced = undersample_doc(doc, **PRESET_BALANCED._asdict())   # or tolerance=5.0
```

#### Reducing keyframes while importing into Nuke

The menu's *undersample* commands do this as part of the import, and so does
`build_roto`:

```python
from rotobot_nuke import load_json, build_roto, TOLERANCE_BALANCED

build_roto(load_json("shapes.json"), undersample_tolerance=TOLERANCE_BALANCED)   # 5.0
```

This uses the single `--tolerance` measurement. For a preset, reduce the JSON
first, then import the reduced file. Building the Roto node needs Nuke
(including `nuke -t`): it imports `nuke.rotopaint`, which only exists in
Nuke's bundled Python.

#### Tolerance values

| Constant | Value | Keyframes kept (four real UHD/HD clips) |
|---|---:|---|
| `TOLERANCE_CONSERVATIVE` | 2.0 | 99% or more: barely trims |
| `TOLERANCE_BALANCED` (default) | 5.0 | 87–99%: a useful middle ground |
| `TOLERANCE_AGGRESSIVE` | 10.0 | about 75%: faster to scrub, still close |
| `TOLERANCE_VERY_AGGRESSIVE` | 25.0 | 45–80%: review before use |

The unit mixes pixels and degrees, and bone-origin pixel positions dominate it
on a typical plate. So `tolerance = n` roughly means: keep a frame if any part
of its state is more than `n` pixel-equivalents away from a straight line
between the frames kept either side of it.

## What it imports

`rotobot-nuke` understands the `lozenge_bezier_anim` schema.

- **Schema v2** (`"schema_version": 2`) — current output. Carries
  `resolution`, `width`, `height`, `fps`, per-object `visibility`, per-frame
  Bezier `points`, optional `person_depth`, optional `bone` capsule
  endpoints.
- **Schema v1** — pre-2026-02 captures that lack any in-band resolution.
  Pass `resolution=(w, h)` explicitly when loading these.

Coordinate space is **absolute pixels, Y-down** (image / OpenCV convention).
The importer flips to Nuke's Y-up on the way in.

Curves are **closed cubic Beziers** with absolute tangent handles
(`left_x`, `left_y`, `right_x`, `right_y`); the Nuke-side half converts
to the vertex-relative deltas Nuke's RotoPaint API expects.

## What's intentionally not imported

- **Silhouette** output (`tokgan_json_to_fxs.py`) — belongs in a sibling repo
  if there's demand.
- **Signals-JSON** sidecar (`signals_<clip>.json`) — out of scope for v0.1.
- **`person_depth`** — read into `LozengeDoc.person_depth` but not applied
  to the Roto node. Open an issue if you want a layer-ordering convention
  on top of it.

## Related

- [Tokgan](https://tokgan.com) — the broader VFX / AI-roto product this
  JSON schema comes from.

## License

MIT. See [LICENSE](./LICENSE).
