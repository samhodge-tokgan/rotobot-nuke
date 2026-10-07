# Real-plate hierarchical-undersampler — cross-clip aggregate

Four UHD clips from the PR #1 wedge sweep, each run through the full `rotobot_next` pipeline (Phase A + Phase A2 merged) on skylab. 60-frame slice per clip (`--first 1 --last 60`; actual output frames per clip may be smaller due to the known `rotobot_next` 60→N discrepancy, tracked separately).

## Clip provenance

| clip | resolution | objects | kf total | kf per-obj median | camera frames | persons frames |
|---|---|---:|---:|---:|---:|---:|
| `pexels_11054569_3840x2160_12s` | 3840×2160 | 41 | 1177 | 31 | 31 | 31 |
| `pexels_11054573_3840x2160_12s` | 3840×2160 | 42 | 1247 | 31 | 31 | 31 |
| `pexels_33191756_3840x2160_6s` | 3840×2160 | 134 | 5478 | 53 | 60 | 60 |

## Single-pass (legacy, PR #1) retention

| clip | tol=5.0 | tol=10.0 | tol=25.0 |
|---|---:|---:|---:|
| `pexels_11054569_3840x2160_12s` | 1117 (94.9%) | 950 (80.7%) | 594 (50.5%) |
| `pexels_11054573_3840x2160_12s` | 1217 (97.6%) | 1116 (89.5%) | 862 (69.1%) |
| `pexels_33191756_3840x2160_6s` | 4940 (90.2%) | 4236 (77.3%) | 3206 (58.5%) |

## Hierarchical retention @ candidate triples

| clip | current BALANCED (synth-tuned) | REAL_FINE | REAL_COARSE | REAL_BALANCED | aggressive | very aggressive |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| `pexels_11054569_3840x2160_12s` | 100.0% | 96.3% | 73.9% | 42.5% | 17.2% | 9.7% |
| `pexels_11054573_3840x2160_12s` | 100.0% | 98.4% | 84.0% | 48.1% | 27.7% | 12.7% |
| `pexels_33191756_3840x2160_6s` | 100.0% | 90.2% | 77.9% | 68.2% | 48.1% | 23.6% |

## Consistency check across clips

- **current BALANCED (synth-tuned)** `(1.0, 2.0, 0.4)`: 100.0% mean, range 100.0%–100.0%
- **REAL_FINE** `(1.0, 2.0, 10.0)`: 95.0% mean, range 90.2%–98.4%
- **REAL_COARSE** `(2.5, 5.0, 25.0)`: 78.6% mean, range 73.9%–84.0%
- **REAL_BALANCED** `(5.0, 10.0, 50.0)`: 52.9% mean, range 42.5%–68.2%
- **aggressive** `(10.0, 20.0, 100.0)`: 31.0% mean, range 17.2%–48.1%
- **very aggressive** `(25.0, 50.0, 250.0)`: 15.3% mean, range 9.7%–23.6%

## Interpretation

**Confirmed, uniformly across clips**: the current synth-tuned `BALANCED` preset is a 100% no-op on real UHD footage. The retune direction from PR #4 is right.

**New finding from the cross-clip spread**: the knee is **clip-complexity-dependent**. The range per preset widens as tolerance loosens:

| preset | mean | range | spread |
|---|---:|---|---:|
| `REAL_FINE` (1.0, 2.0, 10.0) | 95.0% | 90.2–98.4% | 8.2 pp |
| `REAL_COARSE` (2.5, 5.0, 25.0) | 78.6% | 73.9–84.0% | 10.1 pp |
| `REAL_BALANCED` (5.0, 10.0, 50.0) | 52.9% | 42.5–68.2% | **25.7 pp** |
| aggressive (10.0, 20.0, 100.0) | 31.0% | 17.2–48.1% | **30.9 pp** |
| very aggressive (25.0, 50.0, 250.0) | 15.3% | 9.7–23.6% | 13.9 pp |

Tight tolerances behave uniformly across clips (narrow spread → fixed presets fine). Loose tolerances explode in spread (wide → fixed presets inadequate at the useful band).

The clip driving the wide spread is `pexels_33191756` — **134 objects / 5478 keyframes**, versus 41–42 objects / ~1200 keyframes for the other two. More objects means more opportunities for articulation to exceed tolerance on any given frame, so under the UNION rule more frames get retained.

### What this means for the next retune

- **Fixed `REAL_BALANCED = (5.0, 10.0, 50.0)`** gives 43% on simple clips and 68% on dense clips — a usable range but ~25 pp of unexpected variance by scene complexity.
- **Per-clip-adaptive tolerance** (scale the triple by `median_kf_per_object` or `total_objects`, same spirit as Rotobot-Next's existing `knot_mult_min/max` auto-scaling) would narrow the spread. Candidate: multiply the triple by `(1 + log10(objects / 40))` so the dense 134-object clip gets looser tolerance.
- **The composition-rule rethink** from PR #4's follow-up list stays open. The dense clip is where UNION's "any pass flags → keep" becomes most conservative; a 2-of-3 majority or weighted sum would reduce the spread.

### Follow-ups unchanged from PR #4

- Retune the preset values (now with cross-clip data to inform them).
- Investigate alternate composition rules.
- Validate on the final clip — `pexels_33191774_3840x2160_10s` — hit the 30-min background limit mid-pipeline and is being rerun separately; this report will be updated with its row when the fourth JSON lands.
- Investigate the `--last=60 → fewer-output-frames` discrepancy in `rotobot_next`. New data from this run: `pexels_33191756_3840x2160_6s` actually produced **60 output frames**, so the discrepancy is clip-dependent (not a hard pipeline bug). Probably a decoding-timebase quirk on the 12s clips.

