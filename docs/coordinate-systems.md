# Tools — Coordinate Systems & Export Math

A complete guide to how MTools converts Blender data into your engine's
coordinate system, the math behind it, what to check when axes don't line up,
and how to change things safely.

> **Files this document describes**
> - `mtools/ops/export_anim.py` — the reusable **coordinate toolkit** (`Conversion`, `build_conversion_matrix`, …)
> - `mtools/ops/export_anim_xml.py` — the **XML `.anim` exporter** (rotation/translate/scale conversion, metadata)
> - `mtools/ops/export_fbx.py` — the **FBX exporter** (`resolve_fbx_axes`)
> - `mtools/utils/coord_convert.py` — a standalone **experimentation toolkit** (not wired into the add‑on)

---

## 0. TL;DR

- The coordinate system is chosen once in **MTools ▸ Export ▸ Extra Options** and drives **both** the FBX and the `.anim` export.
- Three modes (`scene.mtools_export_coord`): **Blender Native**, **Custom Engine** (default), **Custom Input**.
- A mode is just a **remap string** like `"X Z -Y"`. It builds a 3×3 matrix `C` with **`C @ blender_vector = engine_vector`**.
- Default **Custom Engine** = `"X Z -Y"` = Blender's Z‑up right‑handed space → a **Y‑up right‑handed** engine (X+ right, Y+ up, Z− forward).
- The `.anim` records what it used: `<metadata CoordSystem="X Z -Y" EulerOrder="XZY" …>`.
- **If something looks wrong, jump to [§6 Troubleshooting](#6-troubleshooting--my-axes-dont-match).**

---

## 1. The mental model

### Blender's native space
- **Right‑handed**, **Z up**, and the viewport looks down **−Y** (so "forward" = −Y, "right" = +X).

### Your engine's space
- The default target ("Custom Engine") is **X+ right, Y+ up, Z− forward**, **right‑handed**.
- Converting means **re‑labelling the same physical directions** onto the engine's axes: what was *up* in Blender (Z) must become *up* in the engine (Y), etc.

### A "remap" string
Three signed axis tokens, e.g. `"X Z -Y"`. **Token *i* is the signed Blender axis that becomes engine axis *i*.**

```
remap  = "X   Z   -Y"
engineX = +BlenderX      (token 0)
engineY = +BlenderZ      (token 1)
engineZ = −BlenderY      (token 2)
```

So a Blender point `(x, y, z)` becomes `(x, z, −y)` in the engine.

---

## 2. The conversion matrix `C`

The remap is turned into a 3×3 matrix by `build_conversion_matrix` (in `export_anim.py`, mirrored as `remap_to_matrix` in `coord_convert.py`). Row *i* has a single ±1 in the column of the Blender axis that feeds engine axis *i*.

For `"X Z -Y"`:

```
       Bx By Bz
Ex  [  1  0  0 ]
C = Ey  [  0  0  1 ]
Ez  [  0 -1  0 ]
```

Check it: `C @ (x, y, z) = (x, z, −y)`. ✔  Worked example: `C @ (1, 2, 3) = (1, 3, −2)`.

**Key properties (why the math is clean):**
- `C` is a **signed permutation matrix**: exactly one ±1 per row and per column.
- It is **orthogonal** ⇒ `C⁻¹ = Cᵀ`.
- `|det C| = 1` always. **`det C = +1` keeps handedness; `det C = −1` mirrors it** (see [§5](#5-handedness--the-most-common-source-of-error)).

`build_conversion_matrix` validates that every axis is used once and `|det| = 1`, so a typo like `"X X Z"` raises instead of silently shearing.

---

## 3. Converting each kind of data

There are two mathematically **equivalent** ways to apply `C`, and the exporter uses each where it fits.

### 3.1 Points / directions — `C · v`
A location, a normal, a bone head: just multiply. `convert_vector(C, v)`.

### 3.2 A full transform — the **similarity transform** `C · M · C⁻¹`
To re‑express a 4×4 transform `M` in the new basis you **conjugate** it:

```
M' = C4 · M · C4⁻¹          (C4 = C promoted to 4×4)
```

This is `convert_matrix` / `Conversion.matrix()`. Why conjugation and not just `C · M`? Because `M` *acts on* vectors. A converted vector is `C·v`; to make `M'` act on converted vectors the same way `M` acted on originals, you must undo the basis change first, apply `M`, then re‑apply it: `M'(Cv) = C·M·(C⁻¹·Cv) = C·M·v`. ✔

**It works for local (parent‑relative) matrices too.** If every bone's local matrix is conjugated by the same `C`, the hierarchy still composes: `world' = parent' · local' = (C·parent·C⁻¹)(C·local·C⁻¹) = C·(parent·local)·C⁻¹`. This is why the rest pose and the animation can both be converted per‑bone and stay consistent.

### 3.3 Decomposing the converted transform into T / R / S
`C · M · C⁻¹` decomposes cleanly because `C` is orthogonal:

| Component | Result | In words |
|---|---|---|
| **Translation** | `C · t` | relabel axes **with sign** |
| **Scale** | permuted `(sx, sy, sz)` | relabel axes, **no sign** (scale is a magnitude) |
| **Rotation** | `C · R · C⁻¹` | a rotation about the relabelled axis; **euler order permutes** |

---

## 4. The relabel shortcut (and why it's exact)

Decomposing a matrix every keyframe would destroy authored curve tangents. Instead the exporter reads the Blender **f‑curves** directly and relabels each channel. The crucial fact — **verified numerically over thousands of random cases**, and easy to re‑check yourself with the equivalence functions in `coord_convert.py` — is:

> The per‑channel **relabel** produces the **identical** result to the full `C · M · C⁻¹` conjugation — for translation, scale, **and** euler rotation.

So the animation curves (relabel, exact tangents) and the rest pose (conjugation) always land in the same space.

### 4.1 Translation & scale
`axis_map(C)` gives, for each engine axis, the `(blender_axis, sign)` feeding it. For `"X Z -Y"`:

```
axis_map = [ (0, +1),   # engineX ← BlenderX
             (2, +1),   # engineY ← BlenderZ
             (1, −1) ]  # engineZ ← −BlenderY
```

- **Translate:** `engine_channel[i] = sign · blender_channel[src]`
- **Scale:** `engine_channel[i] = blender_channel[src]` (sign forced to +1 — never negate a scale)

Implemented in `_authored_component_segments` (`export_anim_xml.py`).

### 4.2 Rotation — the conjugation identity
Conjugating a rotation by an orthogonal matrix is a standard identity:

```
C · Rot(axis, θ) · C⁻¹ = Rot(C·axis, det(C)·θ)
```

- `C·axis` sends each Blender rotation axis to its engine axis (with the `axis_map` sign).
- `det(C)·θ`: a **handedness flip (det −1) reverses the angle**.

So each euler channel is relabelled just like translation, but the sign also carries `det(C)`. That's `rotation_axis_map(conv)` = `axis_map` with every sign × `det`.

### 4.3 Euler **order** permutes
Because each per‑axis rotation is relabelled to a different axis, the euler *order string* is relabelled too. `permuted_euler_order` does this:

```
"X Z -Y":  BlenderX→engineX,  BlenderZ→engineY,  BlenderY→engineZ
source order 'XYZ'  →  'X'→'X', 'Y'→'Z', 'Z'→'Y'  →  engine order 'XZY'
```

This is why the default engine export writes **`EulerOrder="XZY"`**, and it is recorded in `<metadata>` so your engine knows how to compose the three euler channels. For **Blender Native** the order stays `XYZ`.

> ⚠️ **Your engine must read `EulerOrder` from the metadata.** If it hard‑codes `XYZ`, see [Recipe 7.4](#74-force-a-fixed-xyz-euler-order).

---

## 5. Handedness — the most common source of error

`det(C)` tells you everything:

| `det C` | Meaning | Example remap | Effect |
|---|---|---|---|
| **+1** | orientation preserved | `"X Z -Y"` (Blender→glTF/OpenGL) | rotation, no mirror |
| **−1** | **mirrored** (handedness flipped) | `"X Z Y"` (Blender→Unity) | geometry mirrored, **winding order flips**, faces may look inside‑out |

**Blender and the default engine are both right‑handed**, so the correct conversion `"X Z -Y"` has `det = +1` — no mirror. Some engines (Unity, Unreal, DirectX) are **left‑handed**; converting to them *requires* `det = −1`, which mirrors the mesh. That's expected for those engines but has consequences (normals/winding), and — importantly — **FBX axis settings cannot express a mirror** ([§6.4](#64-fbx-and-anim-dont-agree)).

Detect it with `coord_convert.handedness_sign(C)` (returns +1 or −1).

### The "forward" gotcha (up aligns, facing doesn't)
`"X Z -Y"` is a −90° rotation about X: it aligns **up** (Blender Z → engine Y) and keeps handedness, **but it does not align "forward".** Blender's forward (−Y) lands on engine **+Z** (which is *backward*, since engine forward is −Z). So a character authored facing Blender‑forward imports **facing away** from the camera.

You **cannot** fix this with a plain remap without mirroring (aligning both up and forward from a RH Z‑up source to a RH Y‑up target forces `det = −1`). Options:

- **Rotate the armature 180° about the up axis** before export (a real re‑orientation, not a basis relabel), or handle facing in‑engine.
- Accept the convention and orient your camera/logic accordingly.

This is not a bug — it's inherent to right‑handed Z‑up → Y‑up conversion. See [§6.2](#62-the-character-faces-the-wrong-way).

---

## 6. Troubleshooting — "my axes don't match"

Work top‑down. First identify the **symptom**, then apply the fix.

### 6.1 The whole model is lying on its side / rotated 90°
**Cause:** up axis not converted (using Native when you wanted engine, or wrong remap).
**Check:** open the `.anim`, read `<metadata CoordSystem="…">`. Is it what you expect?
**Fix:** set **Extra Options ▸ Coordinate System** to *Custom Engine*, or fix the custom remap so the up axis maps correctly (Blender **Z** must land on your engine's up). For a Y‑up engine that's `"X Z -Y"` (RH) or `"X Z Y"` (LH).

### 6.2 The character faces the wrong way
**Cause:** the "forward" gotcha from [§5](#the-forward-gotcha-up-aligns-facing-doesnt) — up and handedness are right, but forward is 180° off.
**Fix:** rotate the armature 180° about the engine up‑axis (apply the rotation before exporting), or compensate in‑engine. A remap alone can't fix this without mirroring.

### 6.3 The model is mirrored / inside‑out / normals wrong
**Cause:** `det(C) = −1`. You're targeting a left‑handed engine (or the custom remap accidentally flips handedness).
**Check:** `coord_convert.handedness_sign(remap_to_matrix("<your remap>"))`. If it's −1 and you did **not** intend a LH engine, one of your tokens has the wrong sign.
**Fix:** if the engine really is left‑handed, this mirror is required — make sure mesh winding/normals are handled on import. If it's accidental, correct the sign so `det = +1`.

### 6.4 FBX and `.anim` don't agree
**Cause:** FBX axis settings (`axis_forward`/`axis_up`) can only express **right‑handed reorientations** (`det = +1`). The `.anim` remap can be anything.
**How the exporter handles it:** `resolve_fbx_axes` searches Blender's own `axis_conversion` for the pair whose matrix **equals your remap's `C`**, so FBX and `.anim` share one space *by construction*. If no pair matches (a `det = −1` remap), it **warns** and falls back to Blender‑native FBX axes — at which point FBX and `.anim` genuinely differ.
**Fix:** for left‑handed targets, don't rely on FBX axis settings for the mirror — export the FBX with a `det = +1` remap and let the **engine's importer** apply the handedness flip, keeping FBX and `.anim` in the same (right‑handed) space. Or mirror the mesh in Blender deliberately.

### 6.5 Rotation animates the wrong axis, or is 90°/180° off, but translation is fine
**Cause:** almost always an **euler order** mismatch. Translation is a plain relabel (hard to get subtly wrong), but rotation depends on the engine reading `EulerOrder` correctly.
**Check:** does your engine read `<metadata EulerOrder="…">`? With *Custom Engine* it will be `XZY`, **not** `XYZ`.
**Fix:** make the engine honour the metadata order, **or** switch rotation to a fixed order ([Recipe 7.4](#74-force-a-fixed-xyz-euler-order)).

### 6.6 A bone's rotation is jittery / not exact
**Cause:** the bone isn't **Euler XYZ**. Euler‑XYZ bones export their curves exactly; Quaternion / Axis‑Angle / non‑XYZ‑euler bones are converted at key times (an approximation), and the exporter reports a **warning** listing them.
**Fix:** set those bones to **Euler XYZ** (Bone ▸ Rotation Mode) for exact curves.

### 6.7 Values look right but everything is subtly scaled
**Cause:** unit/scale settings, not axis conversion. Check `FBX_PARAMS` (`export_fbx.py`) and Blender's Unit Scale. Axis conversion never changes magnitudes.

### Quick diagnosis snippet (run in Blender's console)
```python
from mtools.utils import coord_convert as cc
C = cc.remap_to_matrix("X Z -Y")          # your remap
print("det / handedness :", cc.handedness_sign(C))     # +1 right-handed, -1 mirror
print("euler order      :", cc.permute_euler_order(C)) # what EulerOrder the .anim uses
print("FBX axes         :", cc.fbx_axes_for_matrix(C)) # (forward, up) or None if unmatchable
print("(1,2,3) becomes  :", tuple(cc.convert_vector(C, (1,2,3))))
```

---

## 7. Recipes — changing things safely

### 7.1 Use a one‑off custom remap
Extra Options ▸ Coordinate System ▸ **Custom Input**, then type a remap in the field (`scene.mtools_export_coord_remap`), e.g. `"X Z Y"` for Unity‑style Y‑up left‑handed. It's validated on export.

### 7.2 Change the default engine convention
Edit the `CONVERSIONS` dict (it appears in **both** `export_anim_xml.py` and `export_anim.py`; the XML exporter uses the one in `export_anim_xml.py`):
```python
CONVERSIONS = {
    'NATIVE': "X Y Z",   # identity
    'ENGINE': "X Z -Y",  # <-- change this string to your engine's remap
}
```
Also update the label/description in `COORD_ITEMS` if you like.

### 7.3 Add a new named convention
Add an entry to `CONVENTION_REMAPS` in `coord_convert.py` (reference only), and/or add a new enum item to `COORD_ITEMS` + `CONVERSIONS` in `export_anim_xml.py` to expose it in the UI.

### 7.4 Force a fixed `XYZ` euler order
If your engine can't read the permuted `EulerOrder`, rotation must be **re‑decomposed** to a fixed order — which **cannot preserve Bézier tangents** (they'd need resampling). The change, in `rotation_segments` (`export_anim_xml.py`): instead of the exact relabel for Euler‑XYZ bones, sample each authored key time, compute `conv.matrix(pbone.matrix_basis)`, `decompose()`, `to_euler('XYZ')`, and emit keys with finite‑difference Hermite tangents (the exact code the quaternion path already uses — point every bone at it and hard‑code order `'XYZ'`). Set `EulerOrder` in `build_metadata` to `"XYZ"`. **Tradeoff:** curves become smooth approximations, not exact authored tangents.

### 7.5 Keep rotation in Blender‑native axes (convert only translate/scale)
Not recommended (rest pose and rotation would live in different spaces), but if your engine wants it: in `build_transform_animation` pass an **identity** conversion to the rotation builder while keeping the real `conv` for translate/scale. Only do this if the engine explicitly re‑orients rotations itself.

### 7.6 Change FBX export settings
Edit `FBX_PARAMS` in `export_fbx.py` (bake_anim, leaf bones, smoothing, modifiers, …). The axis settings are computed automatically by `resolve_fbx_axes` to match the `.anim`; don't hard‑code `axis_forward`/`axis_up` unless you deliberately want FBX to differ.

### 7.7 Rename XML tags / attributes
All names live in the `TAG` and `ATTR` dictionaries at the top of `export_anim_xml.py` — one edit renames a tag/attribute everywhere. `<metadata>` attribute keys (`CoordSystem`, `EulerOrder`, `Fps`, …) are in `ATTR`.

---

## 8. Experimenting with `coord_convert.py`

`mtools/utils/coord_convert.py` is a standalone toolbox — **nothing imports it**, so you can try ideas without touching the exporter. Load it in Blender's Python console:

```python
from mtools.utils import coord_convert as cc

C = cc.remap_to_matrix("X Z -Y")

# --- three equivalent ways to convert a rotation (they must agree) ---
q = some_object.rotation_quaternion
print(cc.convert_quaternion_via_matrix(C, q))   # conjugation C·R·C⁻¹
print(cc.convert_quaternion_direct(C, q))        # rotate axis, angle × det
e, order = cc.convert_euler(C, some_object.rotation_euler, 'XYZ')
print(e, order)                                  # euler in the permuted order

# --- describe a convention by forward/up instead of a remap ---
C2 = cc.basis_change(from_forward='-Y', from_up='Z', to_forward='-Z', to_up='Y')
print(cc.matrix_to_remap(C2))

# --- what FBX axis settings match this remap? ---
print(cc.fbx_axes_for_remap("X Z -Y"))           # (forward, up) or None
```

Because `convert_matrix`, `convert_quaternion_*`, and `relabel_euler` are all provably equal, comparing their outputs is a good way to sanity‑check a new convention before wiring it into the exporter.

---

## 9. Reference

### 9.1 Common conventions (remap **from Blender**)
| Target | Up | Forward | Handed | Remap | `det` |
|---|---|---|---|---|---|
| Blender (identity) | Z | −Y | R | `X Y Z` | +1 |
| glTF / OpenGL / three.js | Y | −Z | R | `X Z -Y` | +1 |
| Maya (Y‑up) | Y | −Z | R | `X Z -Y` | +1 |
| Unity | Y | +Z | **L** | `X Z Y` | −1 |
| Unreal | Z | +X | **L** | `X -Y Z` | −1 |

> These are common starting points — **always verify against your actual engine**, especially handedness. Left‑handed targets mirror the mesh.

### 9.2 Where each value comes from
| `.anim` field | Source |
|---|---|
| `CoordSystem` | the resolved remap string (`resolve_export_remap`) |
| `EulerOrder` | `permuted_euler_order(conv)` |
| `Fps` | `scene.render.fps / scene.render.fps_base` |
| `FrameSize` | `end − start` (clip span; `build_metadata`) |
| `<Bone HasSkiningMatrix>` | `pose_bone.bone.use_deform` |

### 9.3 Function map
| Task | Live exporter | Reference toolkit |
|---|---|---|
| remap → matrix | `export_anim.build_conversion_matrix` | `coord_convert.remap_to_matrix` |
| matrix → remap | — | `coord_convert.matrix_to_remap` |
| per‑axis map | `Conversion.axis_map` / `_axis_map_from_matrix` | `coord_convert.axis_map` |
| convert a matrix | `Conversion.matrix` / `convert_matrix` | `coord_convert.convert_matrix` |
| rotation sign×det | `export_anim_xml.rotation_axis_map` | (folded into `relabel_euler`) |
| euler order permute | `export_anim_xml.permuted_euler_order` | `coord_convert.permute_euler_order` |
| translate/scale/rot curves | `export_anim_xml._authored_component_segments` / `rotation_segments` | `coord_convert.convert_trs_relabel` |
| FBX axis match | `export_fbx.resolve_fbx_axes` | `coord_convert.fbx_axes_for_matrix` |

### 9.4 Glossary
- **Basis change / remap:** relabels axes; does **not** move geometry. Captured by `C`.
- **Similarity transform / conjugation:** `C·M·C⁻¹`, re‑expresses a transform in a new basis.
- **Signed permutation matrix:** one ±1 per row/column; what every axis remap is.
- **Handedness:** `det C`; +1 preserves, −1 mirrors.
- **Euler order:** the sequence the three rotation channels are composed in; permutes under a remap.
- **`matrix_basis`:** a pose bone's local transform relative to its rest pose (what the f‑curves drive). The `.anim`'s `AnimatedTransform` is this, converted; the engine reconstructs `restLocal · AnimatedTransform`.
