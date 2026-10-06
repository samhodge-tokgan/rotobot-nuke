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

(The install only pulls Python-stdlib dependencies. The `nuke` module is
provided by your Nuke install at runtime; it is not fetched from PyPI.)

### Nuke menu entry

Add one line to `~/.nuke/init.py` (or your facility `init.py`):

```python
import rotobot_nuke.menu  # registers "File → Import → Rotobot JSON…"
```

If `rotobot-nuke` is pip-installed to the same Python Nuke uses, that's all
you need. If Nuke's embedded Python can't see your site-packages, append the
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

Running this from `nuke -t` works; running it outside Nuke does not — it
imports `nuke.rotopaint`, which only exists inside The Foundry's bundled
Python.

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
