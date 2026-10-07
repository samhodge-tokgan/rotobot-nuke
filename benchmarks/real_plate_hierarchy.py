"""Real-plate hierarchical-undersampler benchmark.

Takes an actual Rotobot-Next v3 output JSON (produced by a full
`rotobot_next` run on a plate, which includes the merged Phase A +
Phase A2 so the `camera` + `persons` blocks are populated), runs it
through both the legacy single-pass undersampler and the three
hierarchical presets, and emits a markdown retention table.

Usage:

    source .venv/bin/activate
    python benchmarks/real_plate_hierarchy.py \\
        --json /path/to/pexels_11054569__XXX.json \\
        > benchmarks/real_results.md

Prints per-object + aggregate retention ratios so an outlier object
doesn't disappear into the average.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rotobot_nuke import (
    PRESET_BALANCED,
    PRESET_COARSE,
    PRESET_FINE,
    TOLERANCE_BALANCED,
    load_json,
    undersample_doc,
)


def _total_kf(doc) -> int:
    return sum(len(o.frames) for o in doc.objects.values())


def _run_single_pass(doc, tol: float):
    t0 = time.perf_counter()
    reduced = undersample_doc(doc, tolerance=tol)
    return _total_kf(reduced), time.perf_counter() - t0


def _run_hierarchical(doc, preset):
    t0 = time.perf_counter()
    reduced = undersample_doc(doc, **preset._asdict())
    return _total_kf(reduced), time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip())
    ap.add_argument("--json", required=True, help="Rotobot-Next v3 shapes.json")
    ap.add_argument(
        "--out",
        default=None,
        help="Write the markdown report here instead of stdout",
    )
    args = ap.parse_args()

    path = Path(args.json)
    if not path.is_file():
        print(f"Error: {path} not found", file=sys.stderr)
        return 2

    size_mb = path.stat().st_size / 1_000_000
    t0 = time.perf_counter()
    doc = load_json(path)
    load_s = time.perf_counter() - t0

    orig_total = _total_kf(doc)
    kf_per_object = [len(o.frames) for o in doc.objects.values()]
    median_kf = int(statistics.median(kf_per_object)) if kf_per_object else 0

    has_camera = doc.camera is not None and len(doc.camera) > 0
    has_persons = doc.persons is not None and len(doc.persons) > 0

    single_total, single_s = _run_single_pass(doc, TOLERANCE_BALANCED)
    coarse_total, coarse_s = _run_hierarchical(doc, PRESET_COARSE)
    balanced_total, balanced_s = _run_hierarchical(doc, PRESET_BALANCED)
    fine_total, fine_s = _run_hierarchical(doc, PRESET_FINE)

    def _pct(n):
        return 100.0 * n / orig_total if orig_total else 0.0

    lines = []
    out = lines.append

    out(f"# Real-plate hierarchical-undersampler benchmark\n")
    out(f"Source: `{path.name}`  ({size_mb:.1f} MB)  ")
    out(f"Load + parse: {load_s * 1000:.0f} ms\n")
    out(
        f"- schema_version: {doc.schema_version}  "
        f"- resolution: {doc.resolution[0]}×{doc.resolution[1]}  "
        f"- fps: {doc.fps:g}"
    )
    out(f"- objects: **{len(doc.objects)}**")
    out(f"- total keyframes: **{orig_total}** (median {median_kf}/object)")
    out(f"- camera block present: **{has_camera}**  "
        f"(frames: {len(doc.camera) if has_camera else 0})")
    out(f"- persons block present: **{has_persons}**  "
        f"(frames: {len(doc.persons) if has_persons else 0})\n")

    out("## Retention ratios\n")
    out("| preset | kept keyframes | % kept | wall-clock |")
    out("|---|---:|---:|---:|")
    out(f"| single-pass tol={TOLERANCE_BALANCED} | {single_total} | "
        f"{_pct(single_total):.1f}% | {single_s * 1000:.0f} ms |")
    out(f"| hierarchical COARSE | {coarse_total} | {_pct(coarse_total):.1f}% "
        f"| {coarse_s * 1000:.0f} ms |")
    out(f"| hierarchical BALANCED (default) | {balanced_total} | "
        f"{_pct(balanced_total):.1f}% | {balanced_s * 1000:.0f} ms |")
    out(f"| hierarchical FINE | {fine_total} | {_pct(fine_total):.1f}% "
        f"| {fine_s * 1000:.0f} ms |")

    if not has_camera and not has_persons:
        out("\n> **Note**: this JSON lacks v3 camera + persons blocks, so "
            "the hierarchical passes degenerate to articulation-only "
            "(same as single-pass applied to bone-local state vectors). "
            "Re-run against a v3 JSON produced by Rotobot-Next with the "
            "merged Phase A + Phase A2 (track_plate stage) to see the "
            "camera + person-root tolerances actually contribute.")

    report = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(report)
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
