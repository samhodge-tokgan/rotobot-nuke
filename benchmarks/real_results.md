# Real-plate hierarchical-undersampler benchmark

Source: `pexels_11054569_3840x2160_12s__1791342654907220.json`  (11.3 MB)  
Load + parse: 97 ms

- schema_version: 3  - resolution: 3840×2160  - fps: 24
- objects: **41**
- total keyframes: **1177** (median 31/object)
- camera block present: **True**  (frames: 31)
- persons block present: **True**  (frames: 31)

## Retention ratios

| preset | kept keyframes | % kept | wall-clock |
|---|---:|---:|---:|
| single-pass tol=5.0 | 1117 | 94.9% | 41 ms |
| hierarchical COARSE | 1176 | 99.9% | 43 ms |
| hierarchical BALANCED (default) | 1177 | 100.0% | 43 ms |
| hierarchical FINE | 1177 | 100.0% | 43 ms |
