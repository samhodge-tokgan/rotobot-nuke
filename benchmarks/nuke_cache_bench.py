"""Real-Nuke validation of the `.nk` curves-knob import cache.

Imports the 323-object UHD plate twice via `build_roto(cache_path=...)`:
- First call: cache miss, slow Python-API build, cache populated.
- Second call: cache hit, `curves.fromScript(cached_text)` fast path.

Reports wall-clock timings for miss / hit and prints a correctness
sanity check (layer+shape counts match across the two Rotos).

Run on skylab:

    /media/sam/projects/gitlab/tokgan/Nuke14.1v8/Nuke14.1 \\
      -it benchmarks/nuke_cache_bench.py
"""
import os
import shutil
import sys
import time

sys.path.insert(0, "/media/sam/projects/github/rotobot-nuke/src")

import nuke  # noqa: E402
from rotobot_nuke import build_roto, cache, load_json  # noqa: E402

JSON_PATH = os.environ.get(
    "ROTOBOT_CACHE_BENCH_JSON",
    "/tmp/claude-1000/"
    "-media-sam-projects-gitlab-tokgan-tokgan-wedge-runner/"
    "fbd20970-306d-4411-b771-63874d0eb0d7/scratchpad/"
    "real_plate_bench_pexels_33191774_3840x2160_10s_v2/"
    "pexels_33191774_3840x2160_10s__1791345305558137.json",
)
MODE = "hierarchical"
CURVE_TYPE = "bspline"


def _count(curves_knob):
    layers = 0
    shapes = 0

    def walk(node):
        nonlocal layers, shapes
        for c in node:
            tn = type(c).__name__
            if tn == "Layer":
                layers += 1
                walk(c)
            elif tn == "Shape":
                shapes += 1

    walk(curves_knob.rootLayer)
    return layers, shapes


def _entry_bytes(key):
    try:
        return cache._entry_path(key).stat().st_size
    except OSError:
        return 0


def main():
    assert os.path.exists(JSON_PATH), f"missing fixture: {JSON_PATH}"
    print(f"JSON: {JSON_PATH}")
    print(f"      {os.path.getsize(JSON_PATH) / 1e6:.2f} MB")
    print(f"mode={MODE}  curve_type={CURVE_TYPE}")
    print()

    # Build the key up front so we can invalidate / size it.
    key = cache.make_key(
        JSON_PATH, mode=MODE, curve_type=CURVE_TYPE,
        undersample_tolerance=None,
    )
    print(f"cache dir : {cache._cache_root()}")
    print(f"cache file: {cache._entry_path(key).name}")
    print()

    # Clean start: wipe any prior entry for this key.
    cache.invalidate(key)
    assert cache.get(key) is None

    doc = load_json(JSON_PATH)
    print(
        f"doc: {len(doc.objects)} objects  "
        f"resolution={doc.resolution}"
    )

    # ---- First import: cache miss, slow Python-API build.
    t0 = time.perf_counter()
    roto1 = build_roto(
        doc, roto_name="Miss", mode=MODE, curve_type=CURVE_TYPE,
        cache_path=JSON_PATH,
    )
    t_miss = time.perf_counter() - t0
    layers_m, shapes_m = _count(roto1["curves"])
    entry_bytes = _entry_bytes(key)
    print()
    print(f"MISS: {t_miss:7.3f} s   layers={layers_m}  shapes={shapes_m}")
    print(f"      cache populated: {entry_bytes / 1024:.1f} KB")

    # ---- Second import: cache hit, fromScript() fast path.
    t0 = time.perf_counter()
    roto2 = build_roto(
        doc, roto_name="Hit", mode=MODE, curve_type=CURVE_TYPE,
        cache_path=JSON_PATH,
    )
    t_hit = time.perf_counter() - t0
    layers_h, shapes_h = _count(roto2["curves"])
    print(f"HIT:  {t_hit:7.3f} s   layers={layers_h}  shapes={shapes_h}")

    # ---- Report.
    print()
    print("=" * 60)
    print(f"speedup: {t_miss / t_hit:7.2f}×")
    print(f"saved:   {t_miss - t_hit:7.3f} s")
    print(
        f"layers equal:  {layers_m == layers_h}   "
        f"shapes equal:  {shapes_m == shapes_h}"
    )
    print("=" * 60)

    n, total = cache.cache_stats()
    print(f"\ncache_stats: {n} entries, {total / 1024:.1f} KB total")

    # Leave the cache populated so a human can inspect; comment this
    # out if you want a clean filesystem after the run.
    # cache.invalidate(key)

    nuke.scriptClear()


main()
