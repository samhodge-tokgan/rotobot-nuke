# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
