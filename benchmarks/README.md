# benchmarks/

Fact-based measurements of how `rotobot_nuke.undersample` behaves on
reproducible synthetic inputs.

## synthetic_hierarchy.py

Five 60-frame scenarios that isolate individual sources of per-frame
signal, run both the legacy single-pass undersampler (PR #1) and the
three hierarchical presets (PR #2), and emit a markdown retention
table. Scenarios A/B exercise the degenerate linear-motion case; C/D
exercise the mixed-scale case that the hierarchical redesign is for;
E is a realistic 24 fps UHD-shaped mix.

Run from the venv:

```bash
source .venv/bin/activate
python benchmarks/synthetic_hierarchy.py > benchmarks/results.md
```

The committed `results.md` is the output of this script against the
current defaults; regenerate it whenever presets or RDP internals
change. The table and prose are a correctness check, not a performance
benchmark — numbers are keyframe retention percentages, not wall-clock.

## Related

A real-plate benchmark is planned as a follow-up (same scenarios but
sourced from a Rotobot-Next v3 output of one of the PR #1 wedge-sweep
UHD clips). Needs an actual Phase A2 run on skylab; tracked
separately in the #279 roadmap.
