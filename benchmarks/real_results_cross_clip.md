# Real-plate hierarchical-undersampler — cross-clip aggregate

Four UHD clips from the PR #1 wedge sweep, each run through the full `rotobot_next` pipeline (Phase A + Phase A2 merged) on skylab. 60-frame slice per clip (`--first 1 --last 60`; actual output frames per clip may be smaller due to the known `rotobot_next` 60→N discrepancy, tracked separately).

## Clip provenance

| clip | resolution | objects | kf total | kf per-obj median | camera frames | persons frames |
|---|---|---:|---:|---:|---:|---:|
| `pexels_11054569_3840x2160_12s` | 3840×2160 | 41 | 1177 | 31 | 31 | 31 |
| `pexels_11054573_3840x2160_12s` | 3840×2160 | 42 | 1247 | 31 | 31 | 31 |
| `pexels_33191756_3840x2160_6s` | 3840×2160 | 134 | 5478 | 53 | 60 | 60 |
| `pexels_33191774_3840x2160_10s` | 3840×2160 | 323 | 7539 | 10 | 60 | 60 |

## Single-pass (legacy, PR #1) retention

| clip | tol=5.0 | tol=10.0 | tol=25.0 |
|---|---:|---:|---:|
| `pexels_11054569_3840x2160_12s` | 1117 (94.9%) | 950 (80.7%) | 594 (50.5%) |
| `pexels_11054573_3840x2160_12s` | 1217 (97.6%) | 1116 (89.5%) | 862 (69.1%) |
| `pexels_33191756_3840x2160_6s` | 4940 (90.2%) | 4236 (77.3%) | 3206 (58.5%) |
| `pexels_33191774_3840x2160_10s` | 7110 (94.3%) | 6126 (81.3%) | 4216 (55.9%) |

## Hierarchical retention @ candidate triples

| clip | BODY_FINE | BODY_BALANCED | BODY_COARSE | BODY_AGGRESSIVE | BODY_VERY_AGGRESSIVE |
|:---|:---:|:---:|:---:|:---:|:---:|
| `pexels_11054569_3840x2160_12s` | 97.1% | 89.8% | 65.2% | 35.9% | 15.8% |
| `pexels_11054573_3840x2160_12s` | 99.8% | 88.4% | 68.1% | 31.0% | 23.7% |
| `pexels_33191756_3840x2160_6s` | 96.6% | 88.2% | 76.4% | 64.3% | 46.0% |
| `pexels_33191774_3840x2160_10s` | 99.1% | 91.2% | 80.6% | 64.0% | 49.2% |

## Consistency check across clips

- **BODY_FINE** `(0.5, 1.0, 0.015)`: 98.1% mean, range 96.6%–99.8%
- **BODY_BALANCED** `(1.0, 2.0, 0.05)`: 89.4% mean, range 88.2%–91.2%
- **BODY_COARSE** `(2.5, 5.0, 0.1)`: 72.6% mean, range 65.2%–80.6%
- **BODY_AGGRESSIVE** `(5.0, 10.0, 0.25)`: 48.8% mean, range 31.0%–64.3%
- **BODY_VERY_AGGRESSIVE** `(10.0, 20.0, 0.5)`: 33.7% mean, range 15.8%–49.2%

## Interpretation

* **`BODY_FINE` and `BODY_BALANCED`** are the usable fixed-preset band: ~98% and ~89% mean retention with 3 pp spread. The body-local articulation metric (PR #5) collapses the cross-clip variance here from 25.7 pp (old pixel metric at `REAL_BALANCED`) to 3.0 pp — a 10x improvement.
* **`BODY_AGGRESSIVE` and `BODY_VERY_AGGRESSIVE`** widen back to 33 pp of spread because at loose articulation tolerance the articulation pass stops contributing to the UNION, and the retention is dominated by the camera + person passes whose retention is scene-complexity-dependent (more objects → more UNION hits).
* **Recommendation**: ship `BODY_FINE` + `BODY_BALANCED` as fixed presets; present `BODY_COARSE` through `BODY_VERY_AGGRESSIVE` as 'needs per-shot calibration' until either the composition rule changes or per-scene adaptive camera/person tolerances land.

