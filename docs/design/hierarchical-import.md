# Hierarchical Roto import — design

Status: **design review**. Implementation deferred to a follow-up PR
once this is agreed. Target issue: TBD on `samhodge-tokgan/rotobot-nuke`.

## Motivation

Current `rotobot_nuke.importer.build_roto` writes every spline knot's
plate-pixel position as an animated control point on a flat Roto
hierarchy. On a 24 fps action clip that's thousands of per-knot
position keys per spline, carrying three signals mixed together:

1. The plate itself moving (camera pan / shake / dolly).
2. The actor translating across the plate (walk, run).
3. The body part articulating against the actor's root.

To a roto artist that's noise. They know how to key F-curves on
inflection points — hills and valleys — but the inflections of the
articulation signal are hidden inside the sum of all three.

Issue #279's downstream half fixed this at the **statistical** level:
the composed-tolerance undersampler reads v3's `camera` + `persons`
blocks and the body-local metric, trims frames where none of the
three signals changed enough. But the output Roto node is still a
flat hierarchy of per-knot position curves.

This design proposes carrying the same three-level separation into
the **Nuke scene structure** so the artist's manual key-tweak workflow
operates on each signal independently.

## Hierarchy — three transforms, one shape

```
Roto
└── Layer "camera_track"               [T1: plate-stabilisation affine from H2d]
    ├── Layer "p0_pelvis"              [T2: pelvis_px for person 0]
    │   ├── Layer "p0:leg:R:thigh"     [T3: bone pose]
    │   │   └── spline_shape           (knots in bone-local coords)
    │   ├── Layer "p0:leg:R:shin"      [T3: bone pose]
    │   │   └── spline_shape
    │   └── ... (one Layer per body-part, flat under pelvis)
    ├── Layer "p1_pelvis"              [T2 for person 1]
    │   └── ... (parallel subtree)
    └── ...
```

**No region/side grouping layers** (`p0_leg`, `p0_leg_R` etc). Those
were organisational in the current importer and would silently add
transform-cascade levels we don't want. The body-part layer is flat
under pelvis; the colon-separated `p0:leg:R:thigh` key name becomes
the Layer label.

### Three transforms, in cascade

| Level | Nuke Transform knobs set | Driven by | Per-frame math |
|---|---|---|---|
| **T1 camera_track** | translate (x, y), rotate, scale (x, y) | `doc.camera[f].H2d` | Decompose 3×3 to affine (details below). Keyframed per camera frame. |
| **T2 pelvis** | translate (x, y), rotate (optional) | `doc.persons[f][pid].pelvis_px` | Translate directly. Rotation only if `joint_xforms` carries a usable root pose; else 0. |
| **T3 body-part** | translate (x, y), rotate | bone `pt0`, `pt1 - pt0` | `translate = pt0 (in pelvis-local)`, `rotation = -degrees(atan2(dy, dx))`. One Transform per Layer per frame. |

**Spline knots** at the leaf are stored in **bone-local coordinates**:
project each knot's plate-pixel position through the inverse of
`(T1 × T2 × T3)` so the cascaded Nuke transform recovers plate space.
This is deterministic coordinate math — no new signal, just a
representation change.

### Why three and not more

- Deeper nesting multiplies small numerical errors through more
  cascades; three levels is the sweet spot for Nuke's AnimCurve
  precision.
- Each level maps to a conceptual question an artist asks:
    - "did the plate move?" → camera_track
    - "did the actor walk?" → pelvis
    - "did the limb articulate?" → body-part
- Four+ levels would require inventing a mid-level that doesn't
  correspond to a signal an artist cares about.

## Math — the three transforms

### T1 — camera_track from H2d

`H2d` is a 3×3 pixel homography (frame → frame0). Nuke's layer
Transform is 2D affine, which is 6 DoF while the general H2d is 8
DoF. Decompose the top-left 2×2 affine block:

```
H2d = [ a b tx ]
      [ c d ty ]
      [ p q 1  ]

translate.x = tx
translate.y = ty
rotation    = atan2(c, a)      # radians, convert to degrees for Nuke
scale.x     = hypot(a, c)
scale.y     = hypot(b, d)
```

The **perspective component (p, q)** is lost. Fine for ECC /
CoTracker outputs on normal-FOV shots. For fish-eye / extreme-tilt
plates the loss is visible as a residual perspective drift; worth
flagging in a WARNING log when `hypot(p, q) > 1e-4`.

### T2 — pelvis

Translate directly: `pelvis_px` is in plate pixels; after T1 the
plate frame has been stabilised to frame 0, so pelvis_px frame-to-frame
within T1's cascade is still a meaningful "person moved on screen"
signal. Keyframe `(f, pelvis_px.x, pelvis_px.y)` per frame.

Rotation: only populated when the sidecar supplies a usable root
pose. In v3 that comes via `PersonFrame.joint_xforms` — currently
pass-through from Hastur, format mhr70 ordered-quaternion-or-matrix.
Converting that to a 2D rotation of the pelvis in-plane is a Hastur
contract decision; **defer rotation to a follow-up**, use 0 for now.

### T3 — bone pose

For each body-part Layer, per-frame:

```
pt0, pt1 = frame.bone                         # pixels, pelvis-space (T2 removed)
pt0_local = pt0 - pelvis_px(f)                # subtract T2 translate
translate = pt0_local                          # into T3
rotation  = -degrees(atan2(dy, dx))            # bone axis angle
```

The negated angle matches the current `_state_vector_for_frame`'s
convention. Scale stays at 1 — bone-length changes due to foreshortening
are left to T3's translate_y to encode (same as the current
unsegmented importer handles them).

### Spline knots — body-part-local coordinates

Each knot's plate-pixel position gets projected through
`inverse(T1 × T2 × T3)` and stored on the leaf shape as a static
AnimControlPoint:

```
knot_in_T3_local = T3_inverse * T2_inverse * T1_inverse * knot_plate_px
```

**Pure articulation.** The leaf shape's AnimControlPoint keys become
flat whenever the limb isn't flexing — exactly the artist signal
we're carrying through.

Note: when there's **genuine articulation change** between frames,
the leaf-shape per-knot animation remains — the knots do move in
body-local coords because the limb profile changes shape. This is
the signal the undersampler was already measuring.

## Fallbacks

- **v2 JSONs** (no `camera`, no `persons`): T1 and T2 stay at
  identity. The hierarchy collapses to effectively "T3 only". No
  worse than the current flat importer in that case; slightly more
  layers of indirection but same final behaviour.
- **v3 with partial frame coverage**: a camera-less frame → hold
  the previous keyframe. Nuke's AnimCurve default extrapolation is
  "hold" which matches.
- **ECC-source frames flagged `"ecc-failed"`**: emit a WARNING log
  per such frame but still write the (identity-fallback) H2d. The
  surrounding frames carry the true signal.

## Open questions

1. **Perspective loss in T1.** Loud WARNING when `H2d[6]^2 + H2d[7]^2 > 1e-8`?
   Separate output of a Nuke `CornerPin2D` node for extreme-warp shots?
2. **Person root rotation.** Needs a `joint_xforms` → 2D-rotation
   convention agreed with Hastur. **Deferred to a follow-up**.
3. **Multi-person cross-depth tolerances.** With T2 per-person, the
   undersampler's existing per-person body-radius normalisation
   should still work unchanged — but worth validating against the
   `pexels_33191756` 5-person clip from PR #5's benchmark.
4. **Transform compositing precision.** Nuke's AnimCurve is 32-bit
   float. Three-level cascade on a 3840-pixel plate still has
   ≥ 0.01 px headroom; comfortable. Need an assertion in tests.

## Test plan

1. **Mock-Nuke unit tests** (continuation of existing `tests/`
   pattern with the fake `nuke` + `nuke.rotopaint` modules):
   - T1 affine decomposition from an identity H2d → identity transform.
   - T1 from a known translation H2d → correct T1 translate keys only.
   - T2 from static pelvis_px → single-key T2 translate.
   - T3 from a static bone → single-key T3 translate + rotate.
   - Round-trip: build hierarchy, apply cascaded transforms to each
     stored knot, verify we recover the original plate-pixel positions
     to within 0.01 px.
2. **v2 fixture** → verify T1 + T2 are identity, T3 carries all the
   signal (equivalent-to-current behaviour).
3. **Real-Nuke validation** (manual, pre-merge): import a v3 shapes.json
   produced by `rotobot_next` into a real Nuke session; confirm the
   roto node renders identical pixel positions to the current flat
   importer.

## Scope + sequencing

- **PR 1 (this)**: design doc for review. No code changes.
- **PR 2**: implementation behind `mode="hierarchical"` kwarg on
  `build_roto`. Default stays `mode="legacy"` for one release. Mock-Nuke
  unit tests. Known limitations (perspective loss, zero root rotation)
  documented in CHANGELOG.
- **PR 3**: real-Nuke validation report. If it checks out, flip the
  default to `mode="hierarchical"`.
- **Follow-ups** (independent): person root rotation from
  `joint_xforms`; perspective preservation via output CornerPin2D
  for extreme-warp shots.
