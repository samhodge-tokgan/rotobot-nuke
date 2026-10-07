# Real-plate hierarchical-undersampler benchmark

**Source**: `pexels_11054569_3840x2160_12s` (first 31 frames of the UHD action clip from the PR #1 wedge sweep). A 12s input but the current run produced only 31 output frames despite a `--last=60` request — tracked separately.

**Produced by**: Rotobot-Next on `feature/279-track-plate-stage` (merged as PR #282 → `d2ebb83`). Phase A1 (schema v3) + Phase A2 (`track_plate` ECC) both live. End-to-end wall-clock: **329 s** on skylab (UHD, 31 frames, GPU on fp16 tracker weights).

**Verified**: v3 schema populated — `camera` block has 31 frames with ECC-sourced `H2d` (frame 30 shows a 2.4-pixel translation vs frame 1, non-linear), `persons` block has 31 frames with pelvis-pixel + cam-t data.

```
schema_version: 3    resolution: 3840×2160    fps: 24
objects: 41    total keyframes: 1177 (median 31/object)
camera frames: 31 (source="ecc"/"ecc-seed")
persons frames: 31 (pelvis_px + cam_t + 3D)
```

## Preset retention (synthetic-tuned defaults, applied to real data)

| preset | kept | % | wall-clock |
|---|---:|---:|---:|
| single-pass `tolerance=5.0` | 1117 | 94.9% | 41 ms |
| hierarchical **COARSE** `(2.0, 4.0, 1.0)` | 1176 | 99.9% | 43 ms |
| hierarchical **BALANCED** `(1.0, 2.0, 0.4)` | 1177 | 100.0% | 43 ms |
| hierarchical **FINE** `(0.5, 1.0, 0.15)` | 1177 | 100.0% | 43 ms |

**Headline finding**: on real UHD action footage, the hierarchical presets tuned on synthetic 2-pixel signals **retain more keyframes than single-pass**. Counter-intuitive but mechanically correct — the hierarchical UNION rule keeps a frame whenever **any** of the three passes sees above-tolerance change, and on action footage the actor's articulation exceeds 0.4 px every single frame.

## Why: sweep each pass to find the real-data knee

**Single-pass** (legacy, PR #1):

| tolerance | kept | % |
|---:|---:|---:|
| 0.5 | 1177 | 100.0% |
| 1.0 | 1177 | 100.0% |
| 2.0 | 1172 | 99.6% |
| 5.0 | 1117 | 94.9% |
| 10.0 | 950 | 80.7% |
| 25.0 | 594 | 50.5% |
| 50.0 | 349 | 29.7% |

The useful band on real UHD action is **tol = 10 → 25**, where single-pass drops to 80% → 50% retention.

**Hierarchical with camera + person held at BALANCED, varying articulation tolerance**:

| articulation_tol | kept | % |
|---:|---:|---:|
| 0.5 | 1177 | 100.0% |
| 1.0 | 1176 | 99.9% |
| 2.0 | 1174 | 99.7% |
| 5.0 | 1166 | 99.1% |
| 10.0 | 1134 | 96.3% |
| 25.0 | 1072 | 91.1% |
| 50.0 | 1028 | 87.3% |
| 100.0 | 1000 | 85.0% |

Even at `articulation_tolerance=100`, hierarchical only drops to 85% retention — the camera + person passes together still demand 85% of frames be kept (because camera H2d is non-linear per-frame on a handheld UHD plate and the actor's pelvis moves non-linearly).

**Hierarchical with all three scaled together** (showing the real knee):

| (cam, person, articulation) | kept | % |
|---|---:|---:|
| (1.0, 2.0, 10.0) | 1134 | 96.3% |
| (2.5, 5.0, 25.0) | 870 | 73.9% |
| (5.0, 10.0, 50.0) | **500** | **42.5%** |
| (10.0, 20.0, 100.0) | **203** | **17.2%** |
| (25.0, 50.0, 250.0) | 114 | 9.7% |
| (50.0, 100.0, 500.0) | 83 | 7.1% |

## Honest conclusions

1. **Current presets are undersized by ~1 order of magnitude** for real UHD action footage. They were tuned on the synthetic 2 px sinusoid in `benchmarks/synthetic_hierarchy.py` scenario C, which under-represents the per-frame articulation magnitude in a real plate (likely 2–5 px/frame for a walking actor).

2. **On this clip, single-pass at tol=10 (80.7%) beats hierarchical BALANCED (100%)** at keyframe reduction — a direct consequence of the UNION composition rule. Union is faithful by design (keep a frame whenever any dimension changed) but conservative in practice.

3. **The sweet spot for this clip looks like** `(5.0, 10.0, 50.0)` or `(10.0, 20.0, 100.0)` depending on how aggressive the operator wants to be — 42% or 17% retention respectively. Both would be reasonable preset candidates **retuned for real data**.

## Follow-ups for the roadmap

- **Retune the preset values** based on this real-data knee. Candidate:
  ```
  REAL_COARSE   = (2.5, 5.0, 25.0)   # 74% retention
  REAL_BALANCED = (5.0, 10.0, 50.0)  # 42% retention
  REAL_FINE     = (1.0, 2.0, 10.0)   # 96% retention — "very safe"
  ```
  The synthetic presets stay around as `SYNTHETIC_*` for the sub-pixel case.

- **Investigate alternate composition rules** beyond union. Candidates:
  - **Weighted sum** of normalised state vectors (one tolerance, three weights). The scale-mixing concern that killed this in the plan-agent discussion is tractable if we normalise by state-vector dim.
  - **Majority / 2-of-3** — keep a frame when at least two passes agree it's non-interpolable. More aggressive than union, less than intersection.
  - **Articulation-first with camera/person as outlier-rescue** — only keep a camera/person-flagged frame if it's spaced far enough from the next articulation-kept frame to introduce visible motion-lag.

- **Validate on more clips.** This is one shot. The other three in the PR #1 wedge sweep (`pexels_11054573`, `pexels_33191756`, `pexels_33191774`) should get the same treatment to see whether the real-data knee is consistent.

- **Investigate the `--last=60 → 31 output frames` discrepancy** in `rotobot_next`. Separate issue.
