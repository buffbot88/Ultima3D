# `foliage_cluster` — technical specification

**Status:** implemented in `blender/worker.py` (`build_foliage_cluster`, registered as
`foliage_cluster`) together with `color_srgb` material support, the three foliage validate checks
and optional post-bake material merging. First asset built from it: Village Tree v3
(Paws & Parcels). Sections §4–§13 describe the shipped behaviour; §14 open questions stand.
**Author context:** three primitive-based trees (`village_tree` v1, `v2`, `v2b` experiment) were
built, measured and rendered in Paws & Parcels' prototype before writing this. Their results are
the evidence base, and all three are preserved with their recipes.
**Companions:** `foliage-color-diagnostic.md` (why the greens are pale — a separate, upstream
defect this builder must not be blamed for), `../skills/ultima3d/SKILL.md` (tool surface).

---

## 1. Why a new builder instead of more primitives

| Attempt | Construction | Measured | Outcome |
|---|---|---|---|
| v1 | 7 sphere puffs, 5 beams | 7,412 tris | sphere cluster reads as separate balls |
| v2 | 11 smaller sphere puffs, 8 beams | 7,904 tris | blobs, but branch tips read |
| v2b | 5 rock cores (subdiv + displace) + 40 extrusion leaf cards | 15,720 tris | continuous mass, **but leaves visibly float** and the core still dominates |

The failure is structural, not a matter of tuning. The primitives compose *volumes*; foliage is a
*surface phenomenon* — thousands of small, thin, overlapping, surface-hugging elements whose
silhouette is the read. Hand-placing cards in a recipe cannot know where the displaced core surface
actually is, which is exactly the v2b floating-leaf failure: 40 cards at 0.03 thickness, placed
analytically against an ellipsoid, then the core was displaced ±12 % underneath them.

The single thing a builder can do that a recipe cannot: **own the displacement field and place the
leaves against it**, so a leaf is proud of the surface by construction and can never float.

Second structural point, quantified from v2b: each rock core cost ≈ **2,240 tris** for a shape
completely hidden under foliage, while each leaf card cost ≈ 50. The budget was spent on the part
nobody sees. The new builder inverts that ratio — cheap cores, many cheap leaves.

## 2. What it must not repeat

- No sphere or icosphere "puff" as the visible canopy element.
- No leaf geometry placed by the recipe; leaves are builder-generated against the builder's own
  surface.
- No single shared material for four greens (see §9 — palette lives in the atlas, not in slots).
- No silent parameter drops: unrecognised keys surface in `ignored_params` (existing behaviour,
  must be honoured).

## 3. Position in the toolchain

One new entry in `blender/worker.py`:

```python
BUILDERS = { ..., "foliage_cluster": build_foliage_cluster }
```

It is a **recipe node type**, exactly like `rock` or `barrel`: addressable by node name, usable by
`create_3d`, patchable by `refine_asset`, saved by `save_recipe`, joined and baked by
`finalize_asset`. No new tool, no new workflow step, no daemon protocol change.

**Contract:**

1. Builds into the existing ASSET collection via the existing `_place()` helper.
2. **Emits exactly one object.** Internally it generates many parts, then joins them, applies
   modifiers, and returns a single mesh. This directly fixes two observed defects: `render_asset`'s
   `_fit_camera()` frames only the *first* asset object (which is why v1's turntable framed the
   trunk base and missed the canopy), and `inspect_asset` hangs on high-node-count tree scenes.
3. **Export-root rule compliance:** the object's origin is at ground level (x=0, y=0, z=0) with
   identity rotation, so a joined asset keeps a translation-only root. The builder does not require
   an origin-footing node the way hand-built recipes do.
4. Deterministic: identical `seed` + parameters ⇒ byte-identical geometry, run to run.
5. Uses the existing `_hash01` / `_value_noise` lattice functions rather than a new RNG, so results
   survive a Blender or Python version change.

## 4. Parameters

```jsonc
{
  "builder": "foliage_cluster",
  "center":      [0, 0, 4.6],      // canopy centre in recipe space
  "radii":       [2.6, 2.4, 2.0],  // envelope half-extents (x, y, z)
  "seed":        17,               // drives every random decision
  "layers":      3,                // 1..3 strata: skirt, body, crown
  "cluster_count": 14,             // shell clusters, split across layers by weight
  "cluster_size":  [0.55, 1.0],    // per-cluster radius multiplier range
  "overlap":     0.88,             // target centre-distance / sum-of-radii (see §5.2)
  "silhouette_jitter": 0.35,       // 0..1 envelope noise amplitude
  "noise_scale": 1.6,              // base frequency, cycles per world unit
  "openness":    0.35,             // 0..1 branch corridors kept clear of foliage
  "leaf": {
    "count_per_cluster": 10,       // rosettes on each cluster's outward surface
    "leaves_per_rosette": 5,       // 3..7
    "length": 0.22,
    "width":  0.13,
    "curl":   0.35,                // 0..1 bend along length + cup across width
    "thickness": 0.012,
    "inset": 0.25,                 // fraction of length the leaf base is buried (see §6.3)
    "sides": 6                     // leaf card polygon sides; 6 = silhoutte read, 4 = background
  },
  "palette": [                     // 2..5 entries; weights must sum to 1
    {"name": "leaf_deep",  "color_srgb": [41, 82, 36],    "roughness": 0.92, "weight": 0.35, "where": "skirt"},
    {"name": "leaf_mid",   "color_srgb": [64, 122, 51],   "roughness": 0.9,  "weight": 0.4,  "where": "body"},
    {"name": "leaf_warm",  "color_srgb": [97, 148, 61],   "roughness": 0.88, "weight": 0.18, "where": "crown"},
    {"name": "leaf_gold",  "color_srgb": [133, 173, 76],  "roughness": 0.85, "weight": 0.07, "where": "crown"}
  ],
  "skirt": {"enabled": false, "count": 6, "length": 0.9},   // hanging leaf chains at the rim
  "branches": {"mode": "reference", "nodes": ["04_branch_l", "05_branch_r"]},
  "detail": "hero",                // draft | game | hero (existing profile key)
  "subdiv": 2                      // builder-local core resolution — NOT the reserved `detail`
}
```

**Reserved-key warning, from our own incident:** `detail` is intercepted by `_resolve_detail()` and
must be `draft|game|hero`. A numeric `detail` aborts the build *partway* and leaves a partial scene
that still validates. The builder therefore exposes its own numeric knob as `subdiv` (0–2) and must
document that. Same trap, documented, not repeated.

### 4.1 `branches` modes

- `reference` — the recipe has already built `beam` nodes; the builder queries their endpoints
  (via the existing manifest/`STATE` lookup that `scatter` uses) and attaches clusters and cuffs to
  their tips. Preferred: keeps the trunk authored by the artist and the canopy derived.
- `inline` — the builder emits its own tapered branch stubs from a `trunk_top` point into the
  envelope, for the case where no beams exist.
- `none` — floating canopy (used by `draft`; visually acceptable only because it is never shipped).

## 5. Construction

Five stages, all seeded from `seed` and fully deterministic.

### 5.1 Envelope — what makes the silhouette organic

The envelope is an ellipsoid (`radii`) modulated by two octaves of the existing `_value_noise`:

```
n = 0.65 * noise(p * noise_scale) + 0.35 * noise(p * noise_scale * 2.3 + 11.7)
r_dir = base_radius * (1 + silhouette_jitter * (n - 0.5) * 2)     // ±jitter
```

Two additional *shape* terms, because noise alone still reads as a lumpy ball:

- **crown lift:** z-scaled shear, `p.z += crown_lift * (p.x² + p.y²)` — the canopy widens and
  flattens toward the top, matching the reference's umbrella form.
- **sector bias:** per-octant weight (8 directions from `seed`) at 0.85–1.15, so no two sides of the
  tree match. This is what kills radial symmetry; a noise field alone is statistically symmetric.

### 5.2 Cluster placement — the "no isolated blobs" rule

Clusters are centres on the envelope surface, placed by a jittered golden-angle spiral:

```
for i in 0..cluster_count:
    theta = acos(1 - 2*(i+0.5)/cluster_count)        // uniform on sphere
    phi   = i * 2.39996 + seed_jitter(i)
    dir   = spherical(theta, phi) * per_sector_bias
    C[i]  = center + dir * envelope_radius(dir) * layer_radial_factor
```

with `layer_radial_factor` ≈ 0.72 / 0.92 / 1.0 for skirt / body / crown.

**Adjacency is enforced, not hoped for:** after placement, cluster radii are grown until
`distance(Ci, Cj) <= overlap * (ri + rj)` for every neighbour pair, capped by `cluster_size[1]`.
A cluster that cannot reach any neighbour is deleted and re-seeded at the largest gap. The rule is
checked at the end of placement and reported as `coverage: {paired, orphans}`.

This is the parameter that v1, v2 and v2b all lacked. It turns "N blobs" into one mass: with
`overlap ≥ 0.85` the union silhouette has no interior seams.

### 5.3 Cluster cores — cheap, hidden, and never the read

Each cluster is an icosphere at `subdiv` (0/1/2 → 20/80/320 tris), displaced radially by the same
envelope noise field sampled at the cluster centre, then squashed along its outward axis by
`0.65` (foliage clumps are flatter than spheres). Vertex normals are smoothed.

Cores are deliberately low-resolution because **they exist only to occlude and to receive the
under-canopy shadow**. Their silhouette is broken up by leaves in §6. A `subdiv` of 0 is legitimate
at `game` tier.

### 5.4 Interior darkening (requirement: darker beneath)

Optional inner shell per cluster at 0.8 × radius, assigned the darkest palette entry, normals
inverted. It gives the mass an interior value without any baked ambient occlusion, which keeps the
atlas cheap and the look stable under the game's lighting. `skirt` layer clusters default to it
enabled.

### 5.5 Rosette placement — where the leaves go

For each cluster `c`:

1. Sample `k = leaf.count_per_cluster` directions distributed over the **outward hemisphere**
   (dot(dir, outward) > −0.2), so the trunk-facing side stays open and the branch reads through.
2. For each direction, the attachment point is the *displaced* surface point:
   `P = C + dir * (radius_effective(c, dir) + inset*0)` — computed with the same noise call the
   core used, so it lies exactly on the core, not on the ideal ellipsoid.
3. Cull attachment points inside a branch corridor when `openness > 0`: if `P` is within
   `openness * 0.35` units of a branch axis segment, skip that rosette.

## 6. Leaf geometry

### 6.1 The card

A light, 2-segment "spine" card, solidified:

```
profile (in card-local XY, +Y = tip):
  (0, -0.18L)            base root, behind the attachment (buried, see 6.3)
  (±0.30W, -0.10L)
  (±0.50W,  0.10L)
  (±0.34W,  0.55L)
  (0, 1.0L)              pointed tip
```

6-gon: 2 caps (4 tris each) + 6 rim quads (2 tris each) = **20 tris per leaf** at `thickness`
0.012, versus 50 for v2b's 12-gon. Leaf cost must be cheap because there are many.

`curl` bends the spine along +Z by `curl * L` at the tip and cups the width by
`curl * 0.35 * W`, so the card is convex toward the sky and catches a highlight rather than
rendering as a flat decal.

### 6.2 Rosette orientation

Each rosette places `leaves_per_rosette` cards around its local normal `n`:

```
t1 = normalize(cross(n, up))      // up = world Z, or seed axis if n ≈ ±Z
t2 = cross(n, t1)
for j in 0..k:
    az  = j * 2.39996 + seed_jitter          // golden angle, no clumping
    tilt = splay * (0.55 + 0.45*rand)        // splay ≈ 0.55 rad default
    dir = normalize(cos(az)*t1 + sin(az)*t2) * sin(tilt) + n * cos(tilt)
    card axis +Y = dir,  card normal ≈ dir   // so the face points outward
```

Variation the reference demands and v2b lacked: **length 0.7–1.35 × base, tilt 0.35–0.85 rad,
azimuth fully random, and 15 % of leaves flipped to fold back against the surface** (a drooping
leaf). `density_bias: "toward_crown"` multiplies `count_per_cluster` by a factor rising from 0.7 at
the skirt to 1.35 at the crown.

### 6.3 The anti-floating rule (the v2b defect, mechanised)

`splay` and `curl` move the *tip*, never the base. The base root sits at
`P − dir * (inset * L)`, i.e. **inside the core by `inset × length`** (default 25 %). Since `P` is
computed from the same displacement field as the core, and the base is buried behind it, every leaf
is attached by construction — there is no configuration in which a card floats free.

This is asserted, not just designed: see `leaf_contact` in §11.

### 6.4 Skirt and drapes

Optional hanging chains at the rim: 5–8 leaves per chain, each successive leaf rotated and offset
downward, forming the drooping fringe in the reference. Cheap (≈ 8 × 20 = 160 tris per chain) and
high value for the silhouette read at gameplay distance.

## 7. Branching and attachment (requirement 4)

- Branch beams remain authored (or emitted in `inline` mode) with `beam`, and are *drawn before*
  the canopy in the recipe. The builder reads their endpoints from the manifest and:
  - biases cluster placement along the branch direction near each tip (`+0.15` radial weight), so
    foliage gathers where branches actually end;
  - generates an **attachment cuff** at each tip: a 3-ring tapered collar (≈ 36 tris) wrapped around
    the branch, plus 3–4 short braceleaves. This is the visible join between wood and leaf mass.
- With `openness > 0`, rosettes within the corridor are culled, so the eye finds a wood-coloured
  channel under the canopy — the reference's most legible depth cue.

## 8. Seeds and variant families (requirement 5)

Everything derives from `seed`; no other input is random. Documented recipe-family pattern:

```jsonc
// one recipe body, N seeds
{"04_branch_l": {...}, "09_canopy": {"builder": "foliage_cluster", "seed": 17, ...}}
```

- Seeds 17 / 42 / 91 produce visibly different canopies from the same recipe text (sector bias,
  cluster placement, leaf jitter).
- `save_recipe` stores the seed, so a variant is exactly reproducible.
- For scattering, `scatter`'s per-instance yaw already breaks repetition; distinct *seeds* are only
  needed when two trees sit in the same frame.
- Recommendation for the village kit: 3 hero seeds, 2 background seeds. Not a forest.

## 9. Materials, textures, instancing (requirement 7)

**Two materials per tree: `bark` and `foliage`.** Not one per green.

The palette lives in the atlas, not in material slots. Each palette entry gets a patch in a
1024² leaf sheet (4 × 256² for four greens); every leaf card of that colour maps to its patch's UV
rect. Because the colour is uniform per patch, overlapping UVs are harmless — the bake writes the
same value. Result:

- 4 greens, 1 foliage material, 1 bark material → **2 primitives, 2 draw calls per tree**;
- an `InstancedMesh` of any tree variant is then **2 draw calls for the whole village**;
- one 1024² albedo + normal + roughness set shared by every variant that uses this layout;
- atlas occupancy ≈ 75 %, versus 36 % in v2b.

Trade-off to accept knowingly: per-leaf colour *variation* below the four-patch level is lost.
If variation is needed later, add ±8 % value noise inside each patch at bake time — still one
material.

*(Alternative considered and rejected for now: vertex colours carrying the palette. It would drop
to one material, but we have not verified that `COLOR_0` survives join → bake → GLB. Listed as an
open question in §14.)*

## 10. Quality tiers and measured budgets (requirement 6)

Measured anchors from this session — these are measurements, not estimates:

| Element | Tris |
|---|---|
| icosphere core, `subdiv=2` | 320 |
| icosphere core, `subdiv=1` | 80 |
| 6-gon solid leaf card | 20 |
| 12-gon solid leaf card (v2b) | ~50 |
| rock core as used in v2b (subdiv 2 + subsurf baked) | ~2,240 |
| `beam` (12-vert cylinder) | ~44 |
| sphere puff `segments=12` (v1/v2) | ~144 |
| v1 / v2 / v2b whole tree | 7,412 / 7,904 / 15,720 |

Proposed compositions (arithmetic from the anchors above):

| Tier | Cores | Leaves | Branches | Total | Use |
|---|---|---|---|---|---|
| `draft` | 5 × 80 = 400 | 24 × 20 = 480 | 4 × 44 = 176 | **≈ 1.2k** | blockout, silhoutte checks |
| `game` (background) | 9 × 80 = 720 | 60 × 20 = 1,200 | 6 × 44 = 264 | **≈ 2.4–3.2k** | repeated village planting |
| `hero` | 16 × 320 = 5,120 | 150 × 20 = 3,000 + skirt 960 | 10 × 44 = 440 | **≈ 9.5–12k** | plaza, orchard, quest tree |

The headline: a **background tree costs less than v2 did** (2.4k vs 7.9k) and carries ~60 leaf
cards against v2's 11 puffs. The 8,000-triangle ceiling was never the constraint; where the
triangles went was.

Repetition math to validate against the game's own budget: 30 background trees × 3k = 90k tris
in 2 instanced draw calls; 6 hero trees × 12k = 72k tris in 12 draw calls.

Texture: 1024² × 3 channels, ≈ 1.5–2 MB GLB per variant, one shared atlas per tier.

## 11. Workflow compatibility (requirement 8)

| Tool | Behaviour with this builder |
|---|---|
| `create_3d` | builds one node → one object; `ignored_params` must be empty in the happy path |
| `render_asset` | frames the whole tree because it is one object (`_fit_camera` defect avoided) |
| `inspect_asset` | reports one object with known tri count; no 100+ node scene to hang on |
| `refine_asset` | patches e.g. `09_canopy.silhouette_jitter` and rebuilds deterministically |
| `validate_asset` | existing 7 checks must pass; leaves are closed solidified shells ⇒ 0 non-manifold, and each card's UVs are the atlas patch |
| `finalize_asset` | joins, bakes per material region, writes GLB + collision. **Cores make the collision hull meaningful**; propose `collision: "canopy_trunk"` mode that unions the trunk plus a coarse envelope ellipsoid rather than the leaf soup |
| `save_recipe` / `load_recipe` | unchanged; the node is 5 KB of JSON, not 40 hand-placed cards (v2b's recipe was 52 KB) |

**New validate checks (additive, opt-in by node presence):**

1. `leaf_contact` — every leaf's base-root vertex must be within `0.02` of the nearest core
   surface or inside it. Catches the v2b defect permanently.
2. `canopy_coverage` — from 8 yaw angles, the canopy silhouette must form ≤ 2 connected runs and
   cover ≥ 60 % of its own convex hull. Catches "blobs on sticks" (v1/v2 would fail this).
3. `silhouette_irregularity` — boundary radius sampled at 64 angles around the canopy centroid;
   report σ/μ. Target 0.15–0.30. Baselines to be measured from v2 and v2b at first implementation
   and recorded in the manifest.

## 12. Performance protocol

Measured with the existing prototype probe (`tmp/cafe-biscuit/fps_probe.mjs`), which records
`renderer.info` (draw calls, triangles) alongside a 3-second frame count:

- 2 draw calls per tree instance; instanced village ≤ 4 calls for all background trees.
- Headless software-rendered baseline from this session: 45.7 fps (no tree) → 42.8 fps (v2b,
  15.7k tris). Deltas of this size are noise-level in software rendering; **the number that matters
  is draw calls and texel fetch count**, both of which this design reduces.
- Ship gate: measure the tier on the actual gameplay camera, compare against the same baseline, and
  record the numbers in the asset manifest rather than in prose.

## 13. Acceptance criteria

Technical (machine-checkable):

- [ ] one object, translation-only root, bounds within ±2 % of the requested `radii`
- [ ] all 7 existing validate checks pass, plus the 3 new ones
- [ ] `ignored_params` empty
- [ ] tier tri count within ±15 % of the table in §10
- [ ] 2 materials, 1 atlas, 2 primitives
- [ ] same seed ⇒ identical tri count and bounds across two runs

Visual (**requires human/browser verification** — automated geometry checks cannot approve a look):

- [ ] silhouette is asymmetric from the gameplay camera; no two 45° yaws read the same
- [ ] no isolated foliage mass: every cluster touches ≥ 2 neighbours at silhouette level
- [ ] branches visibly connect into the mass; ≥ 2 attachment cuffs legible at gameplay distance
- [ ] under-canopy visibly darker than the crown
- [ ] leaves read as leaves, not as fragment confetti — judged at 1.5× gameplay zoom
- [ ] compared against Tree v2 and v2b in the same frame, same camera

## 14. Open questions for review

1. **Vertex colours vs atlas patches** for the palette (§9). Atlas is proven in our pipeline;
   vertex colours would need a `COLOR_0` survival test before adoption.
2. **Collision**: union envelope + trunk, or per-cluster capsules? Envelope is 1 hull and cheap;
   capsules are more honest for a canopy you can walk under.
3. **Skirt on by default** for hero only? It is the reference's strongest silhouette cue but adds
   ~160 tris per chain.
4. **Wind/animation**: not in scope, but the leaf card's spine already supports a vertex-shader
   sway; worth reserving a per-leaf phase attribute in the UV1 channel now rather than retrofitting.
5. **Trunk fidelity**: the reference trunk is a carved, gnarled mass, far beyond our tapered
   cylinders. A separate `gnarled_trunk` builder is probably the next capability after this one —
   out of scope here, but the canopy should not be tuned so densely that it hides the trunk.

## 15. Implementation plan (when approved)

| Step | File | Size |
|---|---|---|
| Envelope + cluster placement + coverage rule | `blender/worker.py` | ~180 lines |
| Core meshes + palette/UV patch assignment | `blender/worker.py` | ~120 lines |
| Leaf card generation + rosette orientation + cuffs | `blender/worker.py` | ~200 lines |
| Register builder; `detail`/`subdiv` knob mapping | `blender/worker.py` | ~15 lines |
| 3 new validate checks | `blender/worker.py` | ~120 lines |
| SKILL.md builder docs + tier table | `skills/ultima3d/SKILL.md` | ~40 lines |
| Determinism + geometry tests | `tests/` | ~150 lines |
| **P0 colour fix** (`color_srgb`) — prerequisite, from the diagnostic | `blender/worker.py` | ~30 lines |

Estimated one working session for geometry + checks, plus a separate session to tune the silhouette
against the reference with human review. The colour fix (§P0 of the diagnostic) should land
**first**, so the builder is judged on correct colour.

## 16. Explicitly out of scope

- Forest/scatter generation, biome kits, more than the 2 seeds × 2 tiers needed to prove the
  builder.
- Any change to Paws & Parcels game code, deployment, or publishing.
- Re-baking the Post Office or Café palettes (follow-on work from the diagnostic).
