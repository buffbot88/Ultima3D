# Foliage colour diagnostic — where the greens wash out

**Status:** investigation complete, no fix applied. Companion to
`foliage-cluster-builder-spec.md`.
**Subject:** `village_tree_v2b` (GLB 2.14 MB, 1024² atlas, 7 baked materials) plus its
predecessors `village_tree` (v1) and `village_tree_v2`.
**Question:** the canopy was authored in four greens and renders as pale sage in Blender *and*
in the Three.js prototype. Which stage desaturates it?

## Method

Five checkpoints, each measured rather than assumed:

1. **Authored** — the `color` triple in the recipe JSON.
2. **Baked albedo** — texel values read back from `output/export/village_tree_v2b_albedo.png`.
3. **Exported GLB** — `materials[].pbrMetallicRoughness` parsed straight out of the binary.
4. **Blender render** — canopy pixels sampled from the `render_asset` turntable PNGs.
5. **Three.js** — canopy pixels sampled from the in-prototype capture.

Canopy sampling kept only green-dominant pixels (`g > r+6`, `g > b+6`), so background,
bark and stone do not pollute the averages.

## Result: the chain, stage by stage

| Stage | What was asked for | What came out | Verdict |
|---|---|---|---|
| Recipe (`leaf_mid`) | `[0.25, 0.48, 0.20]` | — | mid sage, not a leaf green, **if** read as display values |
| Blender Base Color | linear `0.25, 0.48, 0.20` | displays as sRGB **`136, 183, 122`** | ⚠️ **divergence starts here** |
| Baked albedo atlas | must equal the material colour | most common leaf texel **`136, 183, 122`** | ✅ faithful |
| Exported GLB | base colour texture | `baseColorFactor` absent (= 1,1,1), `baseColorTexture` present, `metallicFactor 0` | ✅ faithful |
| Blender turntable | asset colour | **`134, 159, 124`** | ⚠️ −43 % chroma |
| Three.js prototype | asset colour | **`114, 156, 125`** | ⚠️ −49 % chroma |

Chroma (`max−min`) on the same leaf: **71 intended → 61 in the atlas → 43 in Blender → 31 in
the game.**

Two things follow immediately. The bake and the GLB are clean — they are not the problem.
And the asset was *already* pale before any renderer touched it.

## Root causes, ranked

### 1. Authored colours are treated as linear, but are written like display values (primary)

Blender's `Base Color` socket is **linear scene-referred**, and `_set_principled()` assigns the
recipe numbers to it verbatim. A colour author who writes `(0.25, 0.48, 0.20)` is thinking in
sRGB — that reads as a medium forest green — but the renderer interprets it as linear and
displays it as `(137, 184, 124)`: a light sage whose luminance is **1.6× brighter** and whose
hue has drifted toward yellow-grey.

Measured for the current palette (`sRGB` encode of the authored linear triple):

| Material | Authored (linear) | Actually displayed (sRGB) | If authored as sRGB |
|---|---|---|---|
| `leaf_deep` | 0.16, 0.32, 0.14 | 111, 153, 105 | 41, 82, 36 |
| `leaf_mid` | 0.25, 0.48, 0.20 | 137, 184, 124 | 64, 122, 51 |
| `leaf_warm` | 0.38, 0.58, 0.24 | 166, 200, 134 | 97, 148, 61 |
| `leaf_gold` | 0.52, 0.68, 0.30 | 191, 215, 149 | 133, 173, 76 |
| `leaf_mid` (v1/v2) | 0.31, 0.54, 0.25 | 151, 194, 137 | 79, 138, 64 |

Every green in the library is roughly one "sRGB stop" too light. This is a **units bug**, not a
taste problem, and it affects bark and stone too — the whole asset set is authored pale.

### 2. Review renders use Blender's default AgX view transform (secondary, review-only)

`_setup_render()` never sets `scene.view_settings.view_transform`, and Blender 5.2.3 defaults to
**AgX** (verified on this install, factory startup). AgX is a filmic-style transform with a
deliberate "desaturate the highlights, roll off the top" behaviour. Applied to a canopy that
already sits near the top of the range, it takes `136,183,122` down to `134,159,124` — the green
channel drops 24 points while red and blue barely move, which is precisely a step toward grey.

Practical consequence: **the turntable renders understate the saturation of the asset.** Judging
colour from `render_asset` is currently judging AgX, not the model.

Compounding it, the render world is a `0.85` grey background at strength `1.0` — a large,
omnidirectional fill that lifts every shadow. The under-canopy, which should be the darkest part
of the tree, is filled to near mid-grey.

### 3. The game adds a second filmic transform plus warm/cool fill (secondary, engine-only)

The prototype is configured as:

```ts
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.12;
// plazaStage.ts
new THREE.HemisphereLight(0xfff0d8, 0x3f6136, 0.75); // warm sky / green ground
new THREE.DirectionalLight(0xffdfae, 2.4);           // warm key
new THREE.DirectionalLight(0xbcd0e8, 0.35);          // cool fill
```

ACES compresses saturated bright values toward white, and the warm hemisphere lifts red while the
cool fill lifts blue — both directions away from green. Measured canopy in-engine:
`114, 156, 125`, chroma 31.

The most telling comparison is against the prototype's own ground: the lawn there measures
`95, 129, 45` (chroma **84**). **The tree is 2.7× less saturated than the grass it stands on**,
which is exactly why it reads as a plastic prop rather than vegetation.

### 4. Not a cause (ruled out)

- **Bake**: albedo texels equal the sRGB encoding of the authored linear colour to within ±1/255.
  Diffuse bake with `use_pass_direct/indirect = False` is a pure colour pass, as intended.
- **GLB export**: no stray `baseColorFactor`, no metallic tint, texture bound correctly.
- **UV/atlas**: the leaf texels sampled are the ones the canopy actually uses; the atlas is 64 %
  empty (670k black pixels of 1M) which wastes memory but changes no colour.
- **Texture colour space**: the atlas is an sRGB PNG and both Blender and `GLTFLoader` read it as
  sRGB; no double-decode is happening.
- **Geometry**: see below.

## What geometry cannot fix

The canopy is pale at the moment it leaves the recipe. A leaf-shaped silhouette, more clusters, or
a dedicated foliage builder will all render in the same washed sage, because the wash happens
after the vertices exist. **Fix the colour contract first, then judge foliage geometry against a
correctly-coloured plate** — otherwise the builder will be tuned against a lie and over-corrected
later.

## Proposed fixes

### P0 — the units bug (asset-level, affects every material in the library)

Add `color_srgb` to the material spec, accepted alongside `color`:

```
"material": { "name": "leaf_mid", "color_srgb": [64, 122, 51], "roughness": 0.9 }
```

`_set_principled()` converts with the sRGB EOTF (`((c/255 + 0.055)/1.055)^2.4` above 0.04045)
before assigning `Base Color`. Keep `color` as the linear form for backwards compatibility, and
reject specs that set both. Migrate the tree, Post Office and Café palettes to `color_srgb` — the
buildings have the same latent paleness and will need re-baking, which is a deliberate separate
step, not a side effect of the foliage work.

Palette target pulled from the reference art: deep shade `≈ 41, 82, 36`, body `≈ 64, 122, 51`,
lit `≈ 97, 148, 61`, highlight `≈ 133, 173, 76`, all in sRGB.

### P1 — make review renders tell the truth

In `_setup_render()`: pin `scene.view_settings.view_transform = "Standard"` (or keep AgX and add
`look = "AgX - Punchy"` for review), and drop the world fill from `0.85 @ 1.0` to something like
`0.55 @ 0.35` so the under-canopy can actually be dark. Also add a "colour plate" render mode: the
same asset on neutral grey, no lighting, so an albedo problem is visible at a glance.

### P2 — engine-side foliage response

Leaves in the reference are backlit and translucent. Two cheap options worth testing, in order:

1. Lower the ACES exposure to 1.0 and re-measure the same canopy pixels; and/or give foliage
   materials a low `emissive` tint of their own base colour (~0.06) to stand in for subsurface
   light. No extra draw call.
2. If the palette still reads flat, add a dedicated `foliage` material variant with a slight
   `sheen`/normal-based rim, evaluated on the gameplay camera only.

Do not reach for physical transmission: it is per-pixel expensive and this tree will be repeated.

### P3 — automatic guard so this cannot silently regress

Add a validate check, `albedo_colour_fidelity`: after bake, sample the UV centroid of each material
region and fail if ΔE against the authored colour exceeds a threshold (≈3). It costs one image
read and would catch a future colour-space or bake-path change on any asset, not just foliage.

## How to verify the fix (same instrumentation, so it is comparable)

1. Re-bake one tree with `color_srgb` and sample the atlas: expect `64,122,51` for `leaf_mid`.
2. Render with `Standard` view transform and sample canopy pixels: expect within ~5/255.
3. Re-capture the prototype and compare against the lawn `95,129,45` — the canopy must be *darker
   and more saturated* than the grass, not less.
4. Only then re-evaluate whether the leaf geometry reads.

## Verification after the P0 fix (implemented)

`color_srgb` now exists in the material contract (`material_color()` in `blender/worker.py`), and
`color` keeps its historical linear meaning, so no existing recipe changes behaviour. Setting both
is a hard error rather than a silent guess.

Baked atlas of Village Tree v3 (`output/export/village_tree_v3_albedo.png`), sampled by pixel count:

| Palette entry | Authored `color_srgb` | Atlas texel | Delta |
|---|---|---|---|
| `leaf_deep` | 41, 82, 36 | **41, 81, 36** | 1 |
| `leaf_mid` | 64, 122, 51 | **63, 121, 50** | 1 |
| `leaf_warm` | 97, 148, 61 | **96, 147, 60** | 1 |
| `leaf_gold` | 133, 173, 76 | **132, 171, 75** | 1 |
| `bark` | 110, 72, 46 | **109, 71, 46** | 1 |

The 1/255 delta is the bake's colour pass rounding; the units bug is gone. For comparison, v2's
`leaf_mid` baked to `137, 184, 124` from the same intent.

Also verified: 7 authored material slots collapse to **1 material / 1 primitive** in the exported
GLB, because after baking every slot samples the same atlas and each colour lives in its own UV
patch. That is a draw-call win with no visual change.

**Deliberately unchanged, per review:** the game's ACES tone mapping, exposure, hemisphere/sun
setup and the review renderer's AgX transform. Those are global art-direction decisions, not
foliage fixes; the tree now reads correctly inside them because its albedo is correct.

## Note on the v2b experiment

v2b's structural failures (floating cards, blobby masses) are real and separately documented in the
builder spec. This diagnostic shows the colour failure is independent of them: v2b was pale in the
recipe, pale in the atlas, pale in the render, and pale in the game — four consistent measurements
of one upstream cause. Do not let a future foliage builder be judged before that is fixed.
