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

* **Current synth-tuned BALANCED** should land close to 100% on every clip (confirms the per-clip finding from PR #4).
* **`REAL_BALANCED` and 'aggressive'** should be the useful band: 40%ish and 15%ish retention respectively. If the knee is clip-dependent (wide range), fixed presets are inadequate and we need per-clip adaptive tolerance.
* **Narrow range per preset** (say ≤10% spread) across the four clips means the retune in PR #4's conclusions is a reasonable fixed-preset choice. **Wide range** points to needing the composition-rule rethink (weighted sum / majority).

