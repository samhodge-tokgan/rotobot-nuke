# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — 2026-10-08

First PyPI release. Bundles all the work shipped since the v0.1.0
scaffold: the `.nk` curves-knob import cache, the hierarchical
three-transform cascade import mode (issue #279), the v3 reader
(camera + persons blocks) and composed-tolerance undersampler, retuned
`PRESET_*` triples, body-local articulation metric, and the
`rotobot-undersample` CLI with per-level tolerance flags. Full details
below.
### Added — host-agnostic hierarchy core (`rotobot_nuke.hierarchy`, #279)
- `decompose(doc)` splits every knot of a v3 document into
  `plate = T1(T2(T3(local)))`: T1 the camera as the **exact** ECC
  homography (not reduced to an affine — on a real 4K clip the affine
  reduction was off by up to 236 px at the plate corners), T2 the person's
  pelvis in stabilised pixels, T3 the body part (bone pt0 relative to the
  pelvis, plus the bone angle, unwrapped), and the knots and tangent
  handles in bone-local pixels.
- No `nuke` import: the After Effects and Silhouette converters use it.
- Missing or bad data is held at the last good value, never replaced by
  identity or the origin: `ecc-failed` cameras, `identity` cameras after
  the track has started, absent camera/person frames, `pelvis_px == [0,0]`
  and frames without a bone. Holds are listed in `Hierarchy.held`.
- T1 and T2 are keyed on every frame of the range, so a host never
  interpolates a value the knots were not computed with.
- `round_trip_error(doc, hier)` re-composes the hierarchy and reports the
  worst plate-pixel error; writers emit only within
  `ROUND_TRIP_TOLERANCE_PX` (0.05). `corner_pin(H, w, h)` gives the four
  corners that reproduce H in a host's corner-pin transform.
- New fixture `tests/fixtures/v3_real_cut.json`, cut from a real v0.10.0
  run (projective camera, real pelvis track).
- Not yet used by this package's own Nuke importer, whose hierarchical
  mode still uses the affine reduction and treats missing frames as
  identity; moving it onto this core is a follow-up.

### Added — `.nk` curves-knob import cache
- New `rotobot_nuke.cache` module. When `build_roto(..., cache_path=...)`
  is called, the roto's `curves` knob text is written under
  `$XDG_CACHE_HOME/rotobot_nuke/` (default `~/.cache/rotobot_nuke/`) on
  first import; subsequent imports of the same JSON (same mtime, mode,
  curve_type, undersample_tolerance) reload via
  `curves.fromScript(text)` — a measured **188× speedup** on a dense
  323-object UHD plate (82.87s Python-API build → 0.44s cache-load).
- The slow per-knot `Shape.append(AnimControlPoint)` path (where ≥95%
  of build time is spent on dense plates) runs once per unique JSON;
  later imports skip it entirely.
- Any build-parameter or mtime change invalidates automatically.
  Public helpers: `make_key`, `get`, `put`, `invalidate`, `cache_stats`.
- `cache_path` is opt-in; omitting it preserves the pre-cache behaviour
  (no file reads, no file writes).

### Added — v3 reader + hierarchical undersampler (issue #279)
- `rotobot_nuke.reader` now parses the `lozenge_bezier_anim` v3
  additions: top-level `camera` → `CameraFrame` and `persons` → nested
  `PersonFrame`. Both are optional; v2 JSONs still parse with
  `doc.camera is None` and `doc.persons is None`.
- `undersample_doc` grows a hierarchical composed-tolerance path. Pass
  any of `camera_tolerance`, `person_tolerance`, `articulation_tolerance`
  (default to `PRESET_BALANCED` individually) and the function runs
  three sequential RDP passes (camera, person root, articulation) and
  keeps the union of their retained frames per object. The legacy
  single-pass `tolerance=` kwarg still works and is still the default
  when no hierarchical kwarg is passed.
- `TolerancePreset` named-tuple + `PRESET_COARSE` / `PRESET_BALANCED` /
  `PRESET_FINE` ready-made triples. Unpack with `**PRESET_BALANCED._asdict()`.
- Articulation state vector now subtracts the person's pelvis pixel
  position before measuring, so the per-object RDP sees articulation
  only — bone-origin drift from whole-body translation no longer
  dominates.

### Added — v0.1 undersampler (initial)
- `rotobot_nuke.undersample` — RDP keyframe reduction on bone-local
  state vectors. Public API: `undersample_doc(doc, tolerance)`,
  `undersample_object(obj, tolerance)`, `undersample_json(in, out,
  tolerance)`, `rdp_reduction(points, tolerance)`.
- `build_roto(doc, undersample_tolerance=5.0)` kwarg — reduce
  keyframes inline during Nuke import.
- `rotobot-undersample` console script for file-to-file batch use.
- Tolerance preset constants `TOLERANCE_CONSERVATIVE`,
  `TOLERANCE_BALANCED` (default), `TOLERANCE_AGGRESSIVE`,
  `TOLERANCE_VERY_AGGRESSIVE`, picked from a wedge sweep across four
  real UHD/HD production JSONs.
- Nuke menu entries for the balanced + aggressive presets and a
  "prompt for tolerance" variant.

Algorithm ported from the `key_reduction` branch of
[`tokgan_silhouette_import`](https://github.com/samhodge-aiml/tokgan_silhouette_import/tree/key_reduction)
(MIT); re-shaped to operate on `LozengeDoc` in memory rather than
file-to-file.

## [0.1.0] — 2026-10-07

### Added
- `rotobot_nuke.reader.load_json(path, *, resolution=None)` — pure Python parser
  for the `lozenge_bezier_anim` schema (v1 + v2). No `nuke` import; testable
  without a Nuke license.
- `rotobot_nuke.importer.build_roto(doc, *, roto_name, curve_type, set_project_fps)`
  — turns a parsed `LozengeDoc` into a Nuke `Roto` node with proper hierarchy
  (person → body → side → part, with per-finger grouping for hands).
- `rotobot_nuke.menu` — Nuke menu hook `File → Import → Rotobot JSON…`.
- `MissingResolutionError` raised when a v1 JSON (no `resolution`/`width`/`height`
  fields) is loaded without an explicit `resolution=(w, h)` kwarg. **The importer
  no longer silently falls back to Nuke's project `format`** — the artist's
  project settings do not have to match the plate.

### Fixed
- Y-flip resolution policy now never silently uses the wrong value (prior
  behaviour fell through to `nuke.root()["format"].value().height()` and, if
  that missed, produced `None` with no error).
- Duplicate `shape.name = …; part_layer.append(shape)` emission that attached
  every shape twice to its parent layer.
