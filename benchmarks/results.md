# Synthetic hierarchical-undersampler benchmark

Five 60-frame scenarios. Numbers are percent of keyframes retained (lower = more aggressive reduction; 3.3% = {first, last} only).

| scenario | single-pass tol=5.0 | hierarchical COARSE | hierarchical BALANCED | hierarchical FINE |
|---|---:|---:|---:|---:|
| A — pure linear camera pan | 3.3% | 3.3% | 3.3% | 3.3% |
| B — pure linear walk | 3.3% | 3.3% | 3.3% | 3.3% |
| C — sinusoidal articulation | 3.3% | 5.0% | 23.3% | 23.3% |
| D — mixed-scale signal | 3.3% | 5.0% | 23.3% | 23.3% |
| E — realistic mix | 3.3% | 8.3% | 31.7% | 33.3% |

## Reading the table

* **A (linear camera pan)** — degenerate for RDP: linear motion IS perfectly interpolable from endpoints. Both approaches correctly collapse to 2 keyframes. Not a differentiator; included as a sanity check.
* **B (linear walk)** — same degenerate shape as A, same expected 3.3% from both. Confirms linear whole-body translation collapses as it should.
* **C (sinusoidal articulation at 2 px)** — the headline case. The 2 px wiggle is below single-pass's 5 px tolerance, so the signal is quietly erased (drops to 3.3%). Hierarchical FINE's `articulation_tolerance=0.15` catches each oscillation half-cycle; BALANCED's 0.4 does similarly.
* **D (mixed-scale)** — slow linear pan dominates the single-pass state-vector norm, so RDP sees the whole series as 'linear enough' and collapses. Hierarchical separates the two signals: the pan is absorbed under `camera_tolerance`, the sub-pixel wiggle survives under `articulation_tolerance`. This is the design justification for the three-pass redesign.
* **E (realistic mix)** — non-linear everything, at magnitudes representative of a 24 fps UHD plate with slow camera drift, a walking subject, and a hand gesture. Hierarchical BALANCED picks out meaningfully fewer keyframes than it would without the camera + person subtraction.
