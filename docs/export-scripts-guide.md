# MTools — The Export Scripts, Explained

A guided, block-by-block tour of the three files that make up MTools' export
feature:

> **Files this document teaches**
> - `mtools/ops/export_fbx.py` — exports the **mesh + rig** as an FBX
> - `mtools/ops/export_anim_xml.py` — exports the **animation** as a structured XML `.anim` file
> - `mtools/ui.py` — the **sidebar panels** that drive both exporters
>
> It also touches the shared toolkit `mtools/ops/export_anim.py` (imported by both)
> and how the add-on wires everything together in `mtools/__init__.py`.

**Who this is for:** you know Python, but you may be new to writing Blender
add-ons and to the 3D math involved. Every Blender-specific idea and every piece
of math is explained the first time it appears. Where the coordinate math gets
deep, this guide gives you the working understanding and points you at
[`coordinate-systems.md`](./coordinate-systems.md) for the full derivations.

---

## Table of contents

1. [The big picture — what these scripts produce and why](#1-the-big-picture)
2. [Blender add-on concepts you need first](#2-blender-add-on-concepts-you-need-first)
3. [How the modules fit together](#3-how-the-modules-fit-together)
4. [The UI layer — `ui.py`](#4-the-ui-layer--uipy)
5. [The FBX exporter — `export_fbx.py`](#5-the-fbx-exporter--export_fbxpy)
6. [The XML animation exporter — `export_anim_xml.py`](#6-the-xml-animation-exporter--export_anim_xmlpy)
7. [The coordinate math, in one page](#7-the-coordinate-math-in-one-page)
8. [Gotchas, collected](#8-gotchas-collected)
9. [Blender/Python API cheat sheet](#9-blenderpython-api-cheat-sheet)

---

## 1. The big picture

A game engine needs two separate things to play an animated character:

1. **The rig and the model** — the skeleton (bones) and the skinned mesh, with
   which vertices each bone moves. This ships as an **FBX** file (an industry
   interchange format every engine imports).
2. **The animation** — how each bone moves over time. MTools ships this
   *separately* as a custom **XML `.anim`** file, so animations can be swapped,
   streamed, or shared between characters without re-exporting geometry.

```
  Blender scene
  ┌───────────────────────────┐
  │  Armature  +  skinned mesh │──▶  export_fbx.py      ──▶  character.fbx   (rig + model)
  │  Action (keyframes)        │──▶  export_anim_xml.py ──▶  Run.anim        (motion only)
  └───────────────────────────┘
             ▲
             │ both read ONE shared setting:
             │   the coordinate system ("which way is up?")
```

The **crucial design constraint**: both files must describe the same character
in the **same coordinate system**, or the animation won't line up with the mesh
in-engine. So the coordinate system is chosen **once** (in the UI's *Extra
Options*) and both exporters read it. Making the FBX exporter and the `.anim`
exporter agree on axes — despite FBX using a totally different axis mechanism —
is one of the more subtle jobs in this code, and we'll see exactly how it's done.

**Why a custom XML format instead of FBX animation?** FBX *bakes* animation to
one sample per frame, which is large and throws away the authored curve shape.
The `.anim` format keeps the **sparse keyframes** and their **tangents** (the
Bézier handle slopes), so the engine can evaluate smooth motion from just a few
keys — much smaller, and faithful to what the animator authored.

Here's a trimmed example of what `export_anim_xml.py` produces, so you have the
target in mind while reading:

```xml
<?xml version='1.0' encoding='utf-8'?>
<Animation>
    <metadata Name="Run" FrameSize="24" LoopMode="LOOP" path="Run.anim"
              EulerOrder="XZY" CoordSystem="X Z -Y" Fps="24.000000"/>
    <BoneAnimationData>
        <Path>Skeleton.Bones["Hips"].AnimatedTransform</Path>
        <transformAnimation>
            <ScaleFloat3Animation>    …X/Y/Z scale curves…    </ScaleFloat3Animation>
            <RotateFloat3Animation>   …X/Y/Z euler curves…    </RotateFloat3Animation>
            <TranslateFloat3Animation>…X/Y/Z position curves… </TranslateFloat3Animation>
        </transformAnimation>
    </BoneAnimationData>
    <!-- …one BoneAnimationData per bone… -->
    <Skeleton RootName="Hips" scalingRule="Standard">
        <Bones>
            <Bone Name="Hips" Parent="" HasSkiningMatrix="true">
                <Transform>
                    <Scale     x="1.000000" y="1.000000" z="1.000000"/>
                    <Rotate    x="0.000000" y="0.000000" z="0.000000"/>
                    <Translate x="0.000000" y="0.000000" z="0.000000"/>
                </Transform>
            </Bone>
            <!-- …one Bone (rest/bind pose) per bone… -->
        </Bones>
    </Skeleton>
</Animation>
```

Two top-level parts: **`BoneAnimationData`** blocks (the moving curves, one per
bone) and a **`Skeleton`** block (the static rest pose, one `Bone` per bone). The
engine reconstructs each bone's animated pose as `restLocal · AnimatedTransform`.

---

## 2. Blender add-on concepts you need first

If you've only *scripted* Blender (typing into the console), writing an *add-on*
introduces a handful of new object types. Every one of them shows up in these
files, so let's define them once.

### `bpy` — the Blender Python module
The single gateway to everything: `bpy.data` (all the datablocks — objects,
meshes, actions), `bpy.context` (what's currently active/selected), `bpy.ops`
(operators = the same actions you trigger from menus), and `bpy.types` (the base
classes you subclass to make your own operators, panels, and properties).

### Operators — `bpy.types.Operator`
An **operator** is a command the user can run (a button, a menu item, a
shortcut). You make one by subclassing `bpy.types.Operator`. The important parts:

| Member | Role |
|---|---|
| `bl_idname` | The string ID, e.g. `"mtools.export_rig"`. This is how it's called: `bpy.ops.mtools.export_rig()`. Must be `lowercase.with_a_dot`. |
| `bl_label` | Human name shown on the button. |
| `bl_options` | A set of flags; `{'REGISTER'}` means it shows in the info log / can be reported. |
| `poll(cls, context)` | *Class method.* Returns `True` if the operator is allowed to run right now. If `False`, Blender greys the button out. This is where "you must select an armature first" lives. |
| `execute(self, context)` | Does the work. Must return a **set**: `{'FINISHED'}`, `{'CANCELLED'}`, etc. |
| `invoke(self, context, event)` | Optional. Called *first* when a user clicks (it has access to the mouse `event`). Use it to pop a dialog (like a file browser) before `execute`. Returns `{'RUNNING_MODAL'}` while a dialog is open, or delegates to `execute`. |
| `self.report({'INFO'}, "msg")` | Shows a status message in Blender's header/log. Levels: `INFO`, `WARNING`, `ERROR`. |

The **invoke → modal → execute** flow matters for the exporters: the *first*
export opens a file browser (`invoke`), and once a path is remembered, later
exports skip straight to `execute`.

### Panels — `bpy.types.Panel`
A **panel** is a box of UI in a region of Blender's window. You subclass
`bpy.types.Panel` and set where it lives:

| Member | Role |
|---|---|
| `bl_space_type = 'VIEW_3D'` | Which editor: the 3D viewport. |
| `bl_region_type = 'UI'` | Which region: the **N-panel** sidebar (toggle with `N`). |
| `bl_category = "MTools"` | The vertical tab the panel sits under. |
| `bl_label` | The panel's header text. |
| `bl_parent_id` | If set, this panel nests *inside* another panel (a collapsible sub-section). |
| `draw(self, context)` | Called every redraw to lay out the widgets. |

Inside `draw` you build the layout with `self.layout`: `.column()`, `.row()`,
`.prop(...)` (a widget bound to a property), `.operator(...)` (a button that runs
an operator), `.label(...)`, `.separator()`.

### Properties — where the UI state lives
A widget needs somewhere to store its value. MTools stores export settings on the
**Scene** by adding custom properties to `bpy.types.Scene`:

```python
bpy.types.Scene.mtools_fbx_armature = bpy.props.PointerProperty(...)
```

That one line adds a new field, `scene.mtools_fbx_armature`, to *every* scene.
The property types used here:

- **`PointerProperty(type=bpy.types.Object)`** — a reference to a datablock (an
  object, an action…). Optionally filtered by a `poll` lambda so the dropdown
  only offers valid choices (e.g. only armatures).
- **`StringProperty(subtype='FILE_PATH')`** — text; the `FILE_PATH` subtype makes
  Blender show a little file-picker affordance.
- **`EnumProperty(items=[...])`** — a fixed set of choices (a dropdown or, with
  `expand=True`, a row of toggle buttons). Each item is
  `(IDENTIFIER, "Label", "Tooltip")`.

Storing state on the scene (rather than on the operator) means it **persists in
the `.blend` file** and is shared between the UI and every operator — which is
exactly how the coordinate system set in one panel reaches both exporters.

### Registration — turning classes into live UI
Defining a class does nothing on its own. Blender only knows about it after
`bpy.utils.register_class(...)`. The add-on's `register()` function
(`mtools/__init__.py`) registers every operator, every panel, and calls each
module's `register_props()` to attach the scene properties. `unregister()` undoes
all of it in reverse — Blender requires clean teardown so add-ons can be disabled
or reloaded. We'll see the exact wiring in [§3](#3-how-the-modules-fit-together).

### `mathutils` — Blender's math types
Bundled with Blender. Provides `Vector`, `Matrix`, `Quaternion`, `Euler`. These
match Blender's internal math exactly, so results are identical to what the
viewport shows. Key operations you'll see:

- `Matrix @ Matrix` / `Matrix @ Vector` — the `@` operator is matrix
  multiplication.
- `Matrix.inverted()` — the inverse matrix.
- `Matrix.decompose()` → `(location: Vector, rotation: Quaternion, scale: Vector)`
  — splits a transform into its translate/rotate/scale parts.
- `Quaternion.to_euler(order, compat)` — convert a rotation to three Euler angles.

That's the whole vocabulary. Now the code.

---

## 3. How the modules fit together

```
mtools/
├── __init__.py            register()/unregister(): wires up the whole add-on
├── ui.py                  the sidebar panels (buttons + property widgets)
└── ops/
    ├── __init__.py        collects every operator class; handles hot-reload
    ├── export_anim.py     SHARED TOOLKIT: Conversion class, f-curve access,
    │                        bone ordering, rest/pose matrices  (+ a .txt exporter)
    ├── export_fbx.py      the FBX (rig + model) exporter
    └── export_anim_xml.py the XML .anim (animation) exporter
```

**The dependency arrows that matter:**

- `export_anim_xml.py` **imports** `export_anim` and reuses its math and
  data-access helpers (`Conversion`, `_action_fcurves`, `_ordered_bones`,
  `_local_rest_matrix`, `_BONE_PATH_RE`). *One source of truth* for that logic.
- `export_fbx.py` imports **both** `export_anim` (for `build_conversion_matrix`)
  and `export_anim_xml` (for `resolve_export_remap`) — so the FBX export reads the
  *same* coordinate setting the animation export does.
- `export_anim_xml.py` also **owns the shared coordinate properties**
  (`mtools_export_coord`, `mtools_export_coord_remap`). Both exporters read them.

Here is the registration wiring in `mtools/__init__.py`, annotated:

```python
def register():
    for cls in ops.classes:                 # every operator (FBX + anim + …)
        bpy.utils.register_class(cls)
    ops.export_fbx.register_props()          # add scene.mtools_fbx_* properties
    ops.export_anim.register_props()         # add scene.mtools_anim_* (the .txt exporter)
    ops.export_anim_xml.register_props()     # add scene.mtools_animx_* AND the shared coord props
    for cls in ui.classes:                   # every panel
        bpy.utils.register_class(cls)
    bpy.utils.register_class(preferences.MToolsPreferences)
    keymaps.register()
```

`unregister()` mirrors this in reverse order (panels before operators, props
removed last) — order matters because a panel referencing an unregistered
property would error.

**Hot-reload.** Both `__init__.py` files start with `if "bpy" in locals():` and
`importlib.reload(...)`. The first time the add-on loads, `bpy` is *not* yet in
the module's local namespace, so the `else` branch runs a normal import. On a
*re-run* (the "Reload Scripts" button), `bpy` *is* present, so the modules are
reloaded in dependency order — note the explicit comment that `export_anim_xml`
must reload **after** `export_anim`, because it imports from it. This lets you
edit a script and see changes without restarting Blender.

---

## 4. The UI layer — `ui.py`

The whole file is panels — no logic, just layout. It builds a nested tree so the
sidebar reads as a tidy outline:

```
MTools (tab)
└── MTools                    VIEW3D_PT_mtools_main       [Reload Scripts]
    ├── Mesh Tools            VIEW3D_PT_mtools_mesh        [Target Weld] […]
    └── Export                VIEW3D_PT_mtools_export      (heading only)
        ├── Export Rig + Model   VIEW3D_PT_mtools_export_rig
        ├── Animation            VIEW3D_PT_mtools_export_anim_xml
        └── Extra Options        VIEW3D_PT_mtools_extra_options
```

Nesting is achieved purely with `bl_parent_id`: a child panel names its parent's
`bl_idname`. Let's read the export-relevant ones.

### The container panel

```python
class VIEW3D_PT_mtools_export(bpy.types.Panel):
    """Container that groups the three export sections below it."""
    bl_label = "Export"
    bl_idname = "VIEW3D_PT_mtools_export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MTools"
    bl_parent_id = "VIEW3D_PT_mtools_main"

    def draw(self, context):
        pass  # heading only; the sections are child panels
```

**What to notice:** `draw` does *nothing*. This panel exists only to be a
collapsible heading that the three real sections nest under (via their
`bl_parent_id = "VIEW3D_PT_mtools_export"`). An empty `draw` with `pass` is the
idiomatic way to make a pure grouping header.

### The FBX section

```python
class VIEW3D_PT_mtools_export_rig(bpy.types.Panel):
    bl_label = "Export Rig + Model"
    bl_parent_id = "VIEW3D_PT_mtools_export"
    # …space/region/category as above…

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)
        col.prop(scene, "mtools_fbx_name", text="Name")

        row = col.row(align=True)
        row.prop(scene, "mtools_fbx_export_path", text="")
        row.operator("mtools.set_fbx_export_path", text="", icon='FILEBROWSER')

        col.separator()
        col.prop(scene, "mtools_fbx_armature", text="Armature")

        col.separator()
        col.operator("mtools.export_rig", text="Export Rig + Model", icon='EXPORT')
```

Line by line, this is the whole grammar of Blender UI code:

- **`layout.column(align=True)`** — a vertical stack. `align=True` glues the
  widgets together with no gaps (they look like one control group).
- **`col.prop(scene, "mtools_fbx_name", text="Name")`** — draw a widget bound to
  `scene.mtools_fbx_name`. Editing the field writes straight back to that scene
  property. `text=""` (used on the path field) hides the label to save width.
- **`row = col.row(align=True)`** — a horizontal strip. Here it places the path
  text field and a little folder button side by side.
- **`row.operator("mtools.set_fbx_export_path", text="", icon='FILEBROWSER')`** —
  a button that runs an operator by its `bl_idname`. `icon=...` picks a built-in
  Blender icon; an empty `text` makes it icon-only.
- **`col.separator()`** — a bit of vertical space.

So the panel offers: an optional name override, a path field with a browse
button, an armature picker, and the big **Export** button. Nothing here *does* the
export — the button just calls the operator we'll read in §5.

### The Animation section

```python
def draw(self, context):
    layout = self.layout
    scene = context.scene

    col = layout.column(align=True)
    col.prop(scene, "mtools_animx_armature", text="Armature")
    col.prop(scene, "mtools_animx_action", text="Take")

    col.separator()
    row = col.row(align=True)
    row.prop(scene, "mtools_animx_export_path", text="")
    row.operator("mtools.set_anim_xml_export_path", text="", icon='FILEBROWSER')

    col.separator()
    col.operator("mtools.export_animation_xml", text="Export Animation", icon='ARMATURE_DATA')
```

Same shape as the FBX section, but the pickers are an **armature** and an
**action** (a "take"). Because these props are `PointerProperty`s pointing at
`bpy.types.Object` and `bpy.types.Action`, Blender automatically renders them as
searchable dropdowns of the matching datablocks.

### The shared Extra Options section

```python
def draw(self, context):
    layout = self.layout
    scene = context.scene

    col = layout.column(align=True)
    col.label(text="Coordinate System (FBX + Animation)")
    col.prop(scene, "mtools_export_coord", expand=True)
    if scene.mtools_export_coord == 'CUSTOM':
        col.prop(scene, "mtools_export_coord_remap", text="")

    col.separator()
    col.prop(scene, "mtools_animx_loop_mode", text="Loop")
```

Two teaching points here:

1. **`col.prop(scene, "mtools_export_coord", expand=True)`** — because
   `mtools_export_coord` is an `EnumProperty`, `expand=True` renders its options
   as a **row of toggle buttons** instead of a dropdown. Nicer for a 3-way choice.
2. **Conditional UI.** `draw` is plain Python that runs on every redraw, so you
   can branch on state: the custom axis-remap text field only appears *when* the
   user has chosen `'CUSTOM'`. This is how dynamic, context-sensitive panels are
   built — there's no special API, you just don't emit the widget.

That this single panel drives **both** exporters is the entire point: it edits
the shared `mtools_export_coord` property that §5 and §6 both read.

### The class list

```python
classes = [
    VIEW3D_PT_mtools_main,
    VIEW3D_PT_mtools_mesh,
    VIEW3D_PT_mtools_export,
    VIEW3D_PT_mtools_export_rig,
    VIEW3D_PT_mtools_export_anim_xml,
    VIEW3D_PT_mtools_extra_options,
]
```

Every module that defines Blender classes exposes a `classes` list, and
`register()` walks it calling `register_class`. **Order matters for panels:** a
parent must be registered before the children that name it in `bl_parent_id`,
which is why `VIEW3D_PT_mtools_export` appears before its three sub-sections.

---

## 5. The FBX exporter — `export_fbx.py`

This module wraps Blender's *built-in* FBX exporter (`bpy.ops.export_scene.fbx`)
with three conveniences: it remembers a per-`.blend` output path, it
automatically selects "the armature plus everything skinned to it," and — the
clever part — it translates the shared coordinate setting into FBX's own axis
options so the FBX and the `.anim` end up in the same space.

### Path helpers

```python
def _default_filepath():
    """Suggest a path next to the current .blend, or a relative fallback."""
    blend = bpy.data.filepath
    if blend:
        return os.path.splitext(blend)[0] + ".fbx"
    return "//untitled.fbx"

def _ensure_fbx_ext(path):
    if path and not path.lower().endswith(".fbx"):
        path += ".fbx"
    return path
```

- **`bpy.data.filepath`** is the path of the saved `.blend` — or an **empty
  string** if the file was never saved. That's the case both helpers guard
  against. `os.path.splitext(blend)[0]` strips `.blend`, so `Char.blend` →
  `Char.fbx` sitting next to it.
- The **`"//"`** prefix is a Blender convention meaning "relative to the `.blend`
  file." It's the sensible fallback when there's no known folder yet.
- `_ensure_fbx_ext` is defensive: whatever the user typed, force a `.fbx`
  extension. (`.lower()` so `.FBX` counts too.)

*Leading-underscore names* (`_default_filepath`) are a Python convention for
"module-private helper" — not enforced, just a signal it's internal.

### The central FBX settings

```python
FBX_PARAMS = {
    "use_selection": True,               # export only what we select below
    "object_types": {'ARMATURE', 'MESH'},
    "add_leaf_bones": False,             # most engines don't want Blender leaf bones
    "bake_anim": False,                  # animation lives in the .anim file
    "use_armature_deform_only": False,   # keep control/non-deform bones too
    "mesh_smooth_type": 'FACE',          # export smoothing as face flags
    "use_mesh_modifiers": True,          # apply modifiers (armature deform is preserved)
    "use_custom_props": False,
}
```

This dict is the **single place** to tune how the FBX is written; it's later
splatted into the operator call with `**FBX_PARAMS`. The load-bearing choices:

- **`bake_anim: False`** — *do not* write animation into the FBX. This is the
  whole architecture: motion ships in the separate `.anim`. Leaving it on would
  duplicate (and bloat) the data.
- **`add_leaf_bones: False`** — Blender likes to append a zero-length "leaf" bone
  at the tip of each bone chain for its own round-tripping; game engines usually
  treat these as junk bones. Off.
- **`use_armature_deform_only: False`** — keep *all* bones, including control/IK
  bones that don't directly skin the mesh. An engine that reconstructs the full
  rig wants them; turn this on only if you want a deform-only skeleton.
- **`use_mesh_modifiers: True`** — apply modifiers (Subdivision, Mirror, …) at
  export. The comment flags the subtlety: Blender's FBX exporter treats the
  **Armature** modifier specially — it exports skin weights and does **not**
  "apply" (freeze) the deform — so turning this on is safe for a rigged mesh and
  you still get a movable skeleton.
- **`mesh_smooth_type: 'FACE'`** — export shading smoothness as per-face flags
  (widely compatible), rather than edge/normal-based smoothing.

### Matching FBX axes to the `.anim` — `resolve_fbx_axes`

This is the heart of "FBX and `.anim` agree." Read the whole thing first:

```python
def resolve_fbx_axes(remap):
    from bpy_extras.io_utils import axis_conversion   # Blender's own axis maths
    from . import export_anim                          # shared conversion toolkit

    target = export_anim.build_conversion_matrix(remap)  # 3x3 matrix C (blender->engine)
    tokens = ('X', 'Y', 'Z', '-X', '-Y', '-Z')
    for forward in tokens:
        for up in tokens:
            try:
                m = axis_conversion(to_forward=forward, to_up=up)
            except (ValueError, RuntimeError):
                continue  # forward and up on the same axis -> invalid combo
            if all(abs(m[i][j] - target[i][j]) < 1e-6 for i in range(3) for j in range(3)):
                return forward, up
    return None
```

**The problem it solves.** The `.anim` exporter describes the coordinate system
as a **remap string** like `"X Z -Y"`, which becomes a 3×3 matrix `C`. But
Blender's FBX exporter doesn't take a matrix — it takes two axis *labels*,
`axis_forward` and `axis_up` (e.g. `-Z` forward, `Y` up). We need the
`(forward, up)` pair whose effect is **exactly** `C`, so both files reorient data
identically.

**How it solves it — a brute-force search.** There are only 6 signed axes
(`X, Y, Z, -X, -Y, -Z`), so only 6×6 = 36 forward/up combinations. The function:

1. Builds the target matrix `C` from the remap
   (`export_anim.build_conversion_matrix` — see [§7](#7-the-coordinate-math-in-one-page)).
2. Asks Blender's own **`axis_conversion(to_forward, to_up)`** to produce the
   matrix for each combination. (Reusing Blender's function guarantees the FBX
   exporter and this check agree on what a given pair *means*.)
3. Compares every entry of that matrix to `C` with a tolerance of `1e-6`
   (never compare floats with `==`). The nested `all(...)` over `i, j` is
   "all 9 entries match."
4. Returns the first `(forward, up)` that matches, or **`None`** if none does.

**The `try/except`.** Some combinations are invalid — `forward` and `up` can't be
the same axis (you can't build a coordinate frame from two parallel directions).
`axis_conversion` raises for those, and we just `continue`.

**Why it can return `None` — an important gotcha.** FBX's forward/up mechanism can
only express **right-handed reorientations** (rotations, `det = +1`). If your
remap *mirrors* handedness (`det = -1`, e.g. targeting a left-handed engine like
Unity or Unreal), **no** forward/up pair reproduces it, and the function returns
`None`. The operator handles that by warning the user and falling back to native
axes — see below. (The full story is in
[`coordinate-systems.md` §6.4](./coordinate-systems.md).)

`bpy_extras.io_utils` is a standard Blender helper module bundled for exactly
this kind of I/O add-on work.

### Finding the meshes to export — `collect_bound_meshes`

```python
def collect_bound_meshes(armature, objects):
    meshes = []
    for obj in objects:
        if obj.type != 'MESH':
            continue
        bound = any(mod.type == 'ARMATURE' and mod.object == armature
                    for mod in obj.modifiers)
        if not bound and obj.parent == armature:
            bound = True
        if bound:
            meshes.append(obj)
    return meshes
```

Given the chosen armature, which meshes belong to it? Two ways a mesh can be
"skinned" to an armature, and this checks both:

1. **An Armature modifier pointing at it.** `obj.modifiers` is the modifier
   stack; `mod.type == 'ARMATURE' and mod.object == armature` finds a mesh that's
   deformed by *this* armature. The `any(...)` generator is a clean "does at least
   one modifier match?" test.
2. **Direct parenting.** Failing a modifier, a mesh parented straight to the
   armature (`obj.parent == armature`) is also taken along.

`objects` is passed in (the caller hands it the view layer's objects) rather than
hard-coded, which keeps the function testable and explicit about *what* it scans.

### Resolving the final path — `_resolve_fbx_path`

```python
def _resolve_fbx_path(scene):
    path = scene.mtools_fbx_export_path
    name = scene.mtools_fbx_name.strip()
    if name:
        directory = os.path.dirname(path) if path else ""
        path = os.path.join(directory, name)
    return _ensure_fbx_ext(path)
```

The user can optionally type a **Name** that overrides just the filename while
keeping the saved **folder**. `os.path.dirname(path)` grabs the directory of the
remembered path; `os.path.join` recombines it with the new name; `_ensure_fbx_ext`
guarantees the extension. If no name override is given, the saved path is used
as-is.

### The main operator — `MTOOLS_OT_export_rig`

This is where it all comes together. We'll read `execute` in stages.

```python
class MTOOLS_OT_export_rig(bpy.types.Operator):
    bl_idname = "mtools.export_rig"
    bl_label = "Export Rig + Model"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene.mtools_fbx_armature is not None
```

`poll` disables the button until an armature is picked — the UI can't run an
export with nothing to export.

**Stage 1 — validate inputs:**

```python
def execute(self, context):
    scene = context.scene
    armature = scene.mtools_fbx_armature
    if armature is None or armature.type != 'ARMATURE':
        self.report({'WARNING'}, "Pick an armature to export")
        return {'CANCELLED'}

    path = _resolve_fbx_path(scene)
    if not path:
        self.report({'WARNING'}, "No export path set")
        return {'CANCELLED'}
    scene.mtools_fbx_export_path = path            # remember for next time

    if not hasattr(bpy.ops.export_scene, "fbx"):
        self.report({'ERROR'}, "FBX exporter add-on is not enabled")
        return {'CANCELLED'}
```

Each guard reports a message and returns `{'CANCELLED'}` (which tells Blender the
operator stopped cleanly). Note the last check: **`bpy.ops.export_scene.fbx` only
exists if Blender's "Import-Export: FBX" add-on is enabled** — it's a built-in
add-on but *can* be turned off. `hasattr` checks for it before we rely on it,
turning a cryptic crash into a clear message. The resolved path is written back to
the scene so it's remembered.

**Stage 2 — translate the shared coordinate system to FBX axes:**

```python
    from . import export_anim_xml
    _coord, remap = export_anim_xml.resolve_export_remap(scene)
    try:
        axes = resolve_fbx_axes(remap)
    except ValueError as exc:                      # invalid custom remap string
        self.report({'ERROR'}, f"Invalid coordinate remap: {exc}")
        return {'CANCELLED'}
    if axes is None:
        self.report({'WARNING'}, f"Coordinate system '{remap}' has no matching FBX "
                    "axis preset (mirrored/handedness-flipped target); exporting "
                    "with Blender-native axes -- FBX and .anim may not match")
        axis_kwargs = {}
    else:
        axis_kwargs = {"axis_forward": axes[0], "axis_up": axes[1]}
```

- **`export_anim_xml.resolve_export_remap(scene)`** reads the shared enum + custom
  field and returns the concrete remap string (defined in §6). This is the
  *single line* that makes the FBX export obey the same coordinate choice as the
  animation export.
- `resolve_fbx_axes(remap)` finds the matching FBX `(forward, up)` pair. Two
  failure modes are handled distinctly:
  - **`ValueError`** — the *remap string itself* was malformed (a bad custom
    entry like `"X X Z"`); reported as an error, export aborts.
  - **returns `None`** — the remap is valid but *can't be expressed* as FBX axes
    (a handedness flip). Here the code chooses to **warn and continue** with empty
    `axis_kwargs` (Blender's native axes), because a mismatched FBX is often still
    useful and the user is told plainly. This is a deliberate
    warn-don't-block decision.
- `**` note: `axis_kwargs` is either `{}` or `{"axis_forward": …, "axis_up": …}`,
  and gets splatted into the export call — a tidy way to *conditionally* pass
  keyword arguments.

**Stage 3 — prepare the selection (and remember the old one):**

```python
    if context.active_object and context.active_object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    view_objects = context.view_layer.objects
    meshes = collect_bound_meshes(armature, view_objects)

    saved_selection = [o for o in view_objects if o.select_get()]
    saved_active = view_objects.active

    bpy.ops.object.select_all(action='DESELECT')
    armature.select_set(True)
    for mesh in meshes:
        mesh.select_set(True)
    view_objects.active = armature
```

Because `FBX_PARAMS` uses **`use_selection: True`**, the exporter writes exactly
what's *selected*. So the operator must:

1. **Get into Object Mode.** Selection and export operators require it; if the
   user is in Edit/Pose mode, `bpy.ops.object.mode_set(mode='OBJECT')` switches
   out. (Guarded by `context.active_object` existing.)
2. **Save the current selection and active object** so the user's workspace can be
   restored afterward. `o.select_get()` reads selection state; `view_objects.active`
   is the "active" object (the last-clicked one).
3. **Select precisely the armature + its bound meshes.** Deselect everything, then
   `select_set(True)` on exactly what should export, and make the armature active.

This "mutate global state, then restore it" pattern is common in Blender
operators because so much of the API works on the *current selection* rather than
on explicit arguments.

**Stage 4 — export, then always restore:**

```python
    try:
        bpy.ops.export_scene.fbx(filepath=path, **FBX_PARAMS, **axis_kwargs)
    except RuntimeError as exc:
        self.report({'ERROR'}, f"FBX export failed: {exc}")
        return {'CANCELLED'}
    finally:
        bpy.ops.object.select_all(action='DESELECT')
        for o in saved_selection:
            o.select_set(True)
        view_objects.active = saved_active

    self.report({'INFO'}, f"Exported rig + {len(meshes)} mesh(es) to {path}")
    return {'FINISHED'}
```

- The actual export is one call: `bpy.ops.export_scene.fbx(filepath=path,
  **FBX_PARAMS, **axis_kwargs)`. Both dicts are unpacked into keyword arguments —
  the fixed settings plus the computed axes.
- **`try/finally` guarantees restoration.** Whether the export succeeds, raises,
  or the code returns early inside the `try`, the `finally` block *always* runs
  and puts the user's selection back. This is the right tool whenever you've
  temporarily changed global state: the cleanup can't be skipped.
- A `RuntimeError` from the exporter (bad path, write failure) becomes a friendly
  error message. On success, `{'FINISHED'}` and an info report with the mesh
  count.

### The two smaller operators

**`MTOOLS_OT_set_fbx_export_path`** just opens a file browser to pick and remember
the path:

```python
def invoke(self, context, event):
    stored = context.scene.mtools_fbx_export_path
    self.filepath = stored if stored else _default_filepath()
    context.window_manager.fileselect_add(self)   # opens the file browser
    return {'RUNNING_MODAL'}

def execute(self, context):
    context.scene.mtools_fbx_export_path = _ensure_fbx_ext(self.filepath)
    self.report({'INFO'}, f"FBX export path set: {context.scene.mtools_fbx_export_path}")
    return {'FINISHED'}
```

This is the canonical **file-picker operator pattern**:

- `filepath` and `filter_glob` are declared as `StringProperty`s on the operator
  (not shown). Blender's file browser writes the chosen path into `self.filepath`
  and uses `filter_glob="*.fbx"` to filter the listing.
- **`invoke`** seeds `self.filepath` with the remembered path (or a default), then
  **`context.window_manager.fileselect_add(self)`** opens the modal file browser.
  Returning `{'RUNNING_MODAL'}` hands control to that browser.
- When the user confirms, Blender calls **`execute`**, where the picked path is
  saved to the scene. (When they cancel, `execute` isn't called.)

**`MTOOLS_OT_export_fbx_selected`** is a simpler "export whatever I've selected"
button, demonstrating the *remember-the-path* flow end to end:

```python
def invoke(self, context, event):
    stored = context.scene.mtools_fbx_export_path
    if stored:
        self.filepath = stored
        return self.execute(context)   # path known -> export immediately
    self.filepath = _default_filepath()
    context.window_manager.fileselect_add(self)  # first time -> ask, then export
    return {'RUNNING_MODAL'}
```

The **"ask once, then remember"** UX lives here: if a path is already stored,
`invoke` calls `execute` directly (no dialog); otherwise it opens the browser the
first time. Its `execute` just calls `bpy.ops.export_scene.fbx(filepath=path,
use_selection=True)` with defaults — no coordinate handling, because it's the
quick raw-selection exporter, distinct from the coordinate-aware rig exporter.

### Registration

```python
classes = [
    MTOOLS_OT_set_fbx_export_path,
    MTOOLS_OT_export_fbx_selected,
    MTOOLS_OT_export_rig,
]

def register_props():
    bpy.types.Scene.mtools_fbx_export_path = bpy.props.StringProperty(subtype='FILE_PATH', default="")
    bpy.types.Scene.mtools_fbx_name = bpy.props.StringProperty(default="")
    bpy.types.Scene.mtools_fbx_armature = bpy.props.PointerProperty(
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE',   # dropdown shows only armatures
    )

def unregister_props():
    for attr in ("mtools_fbx_armature", "mtools_fbx_name", "mtools_fbx_export_path"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
```

Three scene properties back the three FBX widgets. The **`poll` lambda** on the
armature pointer is what makes its dropdown offer *only* armature objects —
Blender calls it for each candidate object and hides the ones returning `False`.
`unregister_props` deletes them cleanly (guarded by `hasattr` so a partial
registration can still be torn down).

---

## 6. The XML animation exporter — `export_anim_xml.py`

This is the large, math-heavy module. Its job: read an **Action** (a set of
keyframed curves) on an armature, convert it into the engine's coordinate system,
and write a structured XML `.anim` file that keeps the sparse keyframes and their
tangents.

The design has three layers, and we'll read them in order:

1. **Configuration & small types** — name registries, enums, formatters.
2. **Data extraction** — pull f-curves out of Blender and turn them into
   coordinate-converted `Segment`/`Key` objects. *This is where the math lives.*
3. **XML building & orchestration** — turn those objects into an XML tree, plus
   the operators and registration.

### 6.1 The name registries — `TAG` and `ATTR`

```python
TAG = {
    "root":           "Animation",
    "bone_anim":      "BoneAnimationData",
    "transform_anim": "transformAnimation",
    "scale_anim":     "ScaleFloat3Animation",
    …
    "step_seg":       "stepSegment",        # note the intentional lower-case
    …
}
ATTR = {
    "meta_euler_order": "EulerOrder",
    "meta_coord":       "CoordSystem",
    "frame":            "Frame",
    "in_slope":         "InSlope",
    …
    "vx": "x", "vy": "y", "vz": "z",
}
```

Every XML tag name and attribute name lives in one of these two dicts, and every
builder looks names up (`TAG["bone_anim"]`) instead of hard-coding the string.
**Why this matters:** the target format has *intentionally mixed* casing —
`transformAnimation` and `stepSegment` are camel/lower, but `BoneAnimationData`
and `HasSkiningMatrix` are Pascal-case (and `HasSkiningMatrix` is even
mis-spelled, to match the engine that consumes it). Centralizing the strings
means (a) you can't typo a tag differently in two places, and (b) renaming a tag
is a one-line edit. This is a small, powerful maintainability pattern — a "single
source of truth" for strings.

Supporting constants:

```python
PATH_TEMPLATE = 'Skeleton.Bones["{bone}"].AnimatedTransform'
ROOT_SCALING_RULE = "Standard"
BOOL_STR = {True: "true", False: "false"}   # Python's True -> XML's "true"
NUM_FMT = "{:.6f}"                            # fixed 6-decimal floats
```

`BOOL_STR` is a neat idiom: Python's `str(True)` is `"True"` (capital T), but XML
wants lowercase `"true"`, so a tiny dict maps them. `NUM_FMT` fixes every numeric
attribute to 6 decimals for stable, diff-friendly output.

### 6.2 Segment and key kinds

The animation model has three kinds of curve **segment**, each named by a
constant and mapped to its XML tags:

```python
STEP, LINEAR, HERMITE = "STEP", "LINEAR", "HERMITE"

SEGMENT_TAG_KEY = {STEP: "step_seg",  LINEAR: "linear_seg",  HERMITE: "hermite_seg"}
KEY_TAG_KEY     = {STEP: "step_key",  LINEAR: "linear_key",  HERMITE: "hermite_key"}
AXIS_SEG_KEY    = ["x_seg", "y_seg", "z_seg"]   # indexed 0=X, 1=Y, 2=Z
```

- **STEP** — the value holds constant, then jumps (a staircase).
- **LINEAR** — straight lines between keys.
- **HERMITE** — a smooth curve defined by each key's value *and its slopes* (in
  and out). This is how Bézier easing is represented.

Now the bridge from Blender's interpolation modes to these kinds:

```python
INTERP_TO_SEGMENT = {
    'CONSTANT': STEP,
    'LINEAR':   LINEAR,
    'BEZIER':   HERMITE,
}
_SAMPLED_INTERP = {
    'SINE', 'QUAD', 'CUBIC', 'QUART', 'QUINT',
    'EXPO', 'CIRC', 'BACK', 'BOUNCE', 'ELASTIC',
}

def _interval_type(interp):
    return INTERP_TO_SEGMENT.get(interp, LINEAR)
```

Blender stores an **interpolation mode on each keyframe**, governing the curve
shape from that key to the *next* one. The three clean cases map directly:
`CONSTANT→STEP`, `LINEAR→LINEAR`, `BEZIER→HERMITE`. But Blender also has a dozen
**eased** interpolations (`SINE`, `BOUNCE`, `ELASTIC`, …) that have no simple
slope form. Those fall through `.get(interp, LINEAR)` to **LINEAR**, and — as
we'll see in `_make_segment` — each eased interval is **sampled** into many small
linear keys so a straight-line engine evaluator still traces the eased shape.
That's what the `_SAMPLED_INTERP` set flags.

### 6.3 The coordinate enums and `resolve_export_remap`

```python
COORD_ITEMS = [
    ('NATIVE', "Blender Native", "Blender's native Z-up right-handed space (no conversion)"),
    ('ENGINE', "Custom Engine",  "Engine: X+ right, Y+ up, Z- forward (remap 'X Z -Y')"),
    ('CUSTOM', "Custom Input",   "Use the Axis Remap field to define the conversion yourself"),
]
CONVERSIONS = {
    'NATIVE': "X Y Z",   # identity
    'ENGINE': "X Z -Y",  # Blender Z-up RH -> engine Y-up RH  (x, y, z) -> (x, z, -y)
}
LOOP_ITEMS = [
    ('ONCE', "Once", "Play once and stop"),
    ('LOOP', "Loop", "Loop continuously"),
    ('PINGPONG', "Ping Pong", "Play forward then backward"),
]

def resolve_export_remap(scene):
    coord = scene.mtools_export_coord
    if coord == 'CUSTOM':
        return coord, scene.mtools_export_coord_remap
    return coord, CONVERSIONS[coord]
```

- `COORD_ITEMS` populates the coordinate `EnumProperty` (the toggle row in Extra
  Options). Each tuple is `(id, label, tooltip)`.
- `CONVERSIONS` maps the two *preset* ids to their remap strings. `NATIVE` is the
  identity (`"X Y Z"`, no change); `ENGINE` is `"X Z -Y"`.
- **`resolve_export_remap`** is the shared translator both exporters call: if the
  user chose `CUSTOM`, hand back the free-text remap field; otherwise look up the
  preset. It returns `(coord_id, remap_string)`. Recall from §5 the FBX exporter
  imports and calls this exact function — that's the single point of agreement.

> The meaning of a remap string (`"X Z -Y"` → matrix `C`) is covered in
> [§7](#7-the-coordinate-math-in-one-page) and, in depth, in
> [`coordinate-systems.md`](./coordinate-systems.md).

### 6.4 Coordinate helpers for rotation

Two functions here handle the two subtle ways rotation differs from
translation/scale under a coordinate change.

```python
def rotation_axis_map(conv):
    det = round(conv.C.determinant())
    return [(src, sign * det) for (src, sign) in conv.axis_map]
```

`conv.axis_map` (from the shared `Conversion` class) says, for each target axis,
`(source_blender_axis, sign)` — the relabel used for translation. **Rotation needs
one extra factor:** the determinant of `C`.

The math (spelled out fully in
[`coordinate-systems.md` §4.2](./coordinate-systems.md)): conjugating a rotation
by the basis change `C` sends *"rotate about axis `a` by angle θ"* to *"rotate
about axis `C·a` by angle `det(C)·θ`."* When `C` **flips handedness**
(`det = -1`, a mirror), the rotation angle **reverses**. So rotation uses the same
axis relabel as translation, with every sign multiplied by `det(C)`. For the
built-in presets `det = +1`, so this is a no-op — but it makes custom left-handed
remaps correct too. `round(...)` cleans a `±1.0` float to an exact `±1` int.

```python
def permuted_euler_order(conv, source_order='XYZ'):
    sigma = {}
    for tgt, (src, _sign) in enumerate(conv.axis_map):
        sigma['XYZ'[src]] = 'XYZ'[tgt]
    return ''.join(sigma[ch] for ch in source_order)
```

An **Euler order** is the sequence in which three per-axis rotations are composed
(Blender's default is `XYZ`). Because a coordinate remap **relabels the axes**, it
also relabels the order string. This builds a permutation `sigma` mapping each
*source* axis letter to the *target* axis letter it feeds, then rewrites the order
string through it. For `"X Z -Y"`: Blender X→X, Z→Y, Y→Z, so `'XYZ'` becomes
`'XZY'` — which is why the default engine export writes `EulerOrder="XZY"` into
the metadata. The result is always one of Blender's 6 valid orders because the
remap is a signed permutation. The engine reads this attribute to compose the
three rotation channels correctly.

### 6.5 Value types and formatters

```python
Key = namedtuple("Key", ["frame", "value", "in_slope", "out_slope"])
Key.__new__.__defaults__ = (None, None)

Segment = namedtuple("Segment", ["type", "keys"])
```

A **`namedtuple`** is a lightweight, immutable record — like a tuple you can
access by name (`key.frame`) instead of by index. `Key.__new__.__defaults__ =
(None, None)` gives the *last two* fields defaults, so `Key(frame, value)` works
for step/linear keys (no slopes) while `Key(frame, value, in_s, out_s)` is used
for Hermite keys. A `Segment` bundles a kind with its list of `Key`s. Using tiny
value types like this keeps the extraction code (which produces them) cleanly
separated from the XML code (which consumes them).

```python
def _fmt_num(v):
    return NUM_FMT.format(v)          # e.g. 1.5 -> "1.500000"

def _fmt_frame(f):
    if abs(f - round(f)) < 1e-6:
        return str(int(round(f)))     # whole frames print as ints: 200
    return NUM_FMT.format(f)          # sub-frame samples keep decimals
```

`_fmt_num` formats a value/slope to fixed 6 decimals. `_fmt_frame` is smarter:
frame numbers are usually integers, so it prints `"200"` not `"200.000000"`, but
it *keeps* decimals for the fractional sub-frame samples the rotation sampler
produces. The `abs(f - round(f)) < 1e-6` test is the safe way to ask "is this
float a whole number?" without exact float equality.

### 6.6 Path helpers

```python
def _default_filepath(action):
    blend = bpy.data.filepath                     # "" until the .blend is saved
    take = action.name if action else "animation"
    if blend:
        return os.path.join(os.path.dirname(blend), take + ".anim")
    return "//" + take + ".anim"                  # Blender-relative fallback

def _ensure_anim_ext(path):
    if path and not path.lower().endswith(".anim"):
        path += ".anim"
    return path
```

Same idea as the FBX path helpers, but the suggested filename is the **action's
name** (`Run.anim`) rather than the blend's, since a `.blend` may hold many
takes. The `"//"` fallback and forced `.anim` extension mirror §5.

### 6.7 Pulling curves out of Blender

```python
def resolve_frame_range(action):
    start, end = (int(round(v)) for v in action.frame_range)
    return start, end
```

**`action.frame_range`** is a `Vector(float, float)` — the first and last
keyframe of the action. This rounds it to integer `(start, end)`. (A generator
expression feeding tuple unpacking — compact, but just two `int(round(...))`
calls.)

```python
def group_fcurves_by_bone(action, anim_data, index_of):
    by_bone = {}
    for fc in export_anim._action_fcurves(action, anim_data):
        m = export_anim._BONE_PATH_RE.match(fc.data_path)   # pose.bones["X"].prop
        if not m:
            continue                                        # object-level channel
        bone_name, prop = m.group(1), m.group(2)
        if bone_name in index_of:
            by_bone.setdefault(bone_name, {})[(prop, fc.array_index)] = fc
    return by_bone
```

This is the key data-access step, and it leans on two reused helpers from
`export_anim`:

- **`_action_fcurves(action, anim_data)`** — yields the action's f-curves *across
  Blender versions*. This is a real compatibility headache the shared helper hides:
  before Blender 4.4, curves live in a flat `action.fcurves`; from 4.4 they're
  nested in `layers → strips → channelbags` (one bag per animation "slot"); and
  Blender 5.0 removed `action.fcurves` entirely. The helper yields from whichever
  API exists. **Lesson:** wrap version-fragile API access in one helper so the
  rest of the code stays clean.
- **`_BONE_PATH_RE`** — a compiled regex, `pose\.bones\["(.*?)"\]\.(.+)`. Every
  f-curve has a **`data_path`** string like `pose.bones["Hips"].location`. This
  regex pulls out the **bone name** (group 1) and the **property** (group 2,
  e.g. `location`, `rotation_euler`). Curves whose path doesn't match (object-level
  channels, non-bone data) are skipped. The `.*?` is *non-greedy* so a bone name
  containing brackets still parses correctly.

An f-curve is identified by **`(prop, array_index)`** — `array_index` is which
component: `location`'s index 0/1/2 are X/Y/Z. So `by_bone` ends up as
`{bone_name: {(prop, index): fcurve}}`, a two-level lookup used throughout the
rest of the module. `dict.setdefault(bone_name, {})` is the idiom for "get the
sub-dict, creating an empty one on first sight."

`index_of` is a `{bone_name: order_index}` map; the `if bone_name in index_of`
check drops curves for bones that aren't part of *this* armature.

### 6.8 Bézier handles → Hermite slopes (the first real math)

```python
def hermite_slopes(kp, value_sign):
    EPS = 1e-9
    dxl = kp.co.x - kp.handle_left.x
    dyl = kp.co.y - kp.handle_left.y
    in_slope = (dyl / dxl) if abs(dxl) > EPS else 0.0
    dxr = kp.handle_right.x - kp.co.x
    dyr = kp.handle_right.y - kp.co.y
    out_slope = (dyr / dxr) if abs(dxr) > EPS else 0.0
    return in_slope * value_sign, out_slope * value_sign
```

Blender's Bézier keyframes carry two **handles** — control points that shape the
curve entering and leaving the key. Each is an absolute `(frame, value)` point:

- `kp.co` — the keyframe itself: `.x` is the frame (time axis), `.y` is the value.
- `kp.handle_left` / `kp.handle_right` — the two handle points, same `.x`/`.y`.

A **slope** is *value change per frame* — rise over run:

```
InSlope  = (co.y − handle_left.y)  / (co.x − handle_left.x)     # slope coming IN
OutSlope = (handle_right.y − co.y) / (handle_right.x − co.x)    # slope going OUT
```

Three subtleties, each handled:

1. **Only the value axis carries a coordinate sign.** Under an axis flip, values
   can negate but *time never does* — so `value_sign` multiplies the slope
   (because slope = Δvalue/Δframe, and only Δvalue flips), and the frame axis is
   untouched.
2. **Division-by-zero guard.** A vertical or coincident handle gives a ~0
   denominator; `if abs(dx) > EPS else 0.0` flattens those to slope 0 instead of
   crashing.
3. **A known approximation** (called out in the module docstring): Blender Béziers
   are *weighted* — handle **length** also bends the curve — while a plain Hermite
   uses only the endpoint slopes. So the interior of a segment can differ slightly,
   but the **endpoint value and slope are reproduced exactly**. For animation this
   is visually faithful.

### 6.9 Building one segment — `_make_segment`

```python
def _make_segment(fcurve, kps, start, end, seg_type, value_sign):
    if seg_type == HERMITE:
        keys = []
        for i in range(start, end + 1):
            kp = kps[i]
            in_s, out_s = hermite_slopes(kp, value_sign)
            keys.append(Key(kp.co.x, kp.co.y * value_sign, in_s, out_s))
        return Segment(HERMITE, keys)

    if seg_type == STEP:
        keys = [Key(kps[i].co.x, kps[i].co.y * value_sign) for i in range(start, end + 1)]
        return Segment(STEP, keys)

    # LINEAR: emit the start key, then walk each interval to the end key.
    keys = [Key(kps[start].co.x, kps[start].co.y * value_sign)]
    for i in range(start, end):
        left, right = kps[i], kps[i + 1]
        if left.interpolation in _SAMPLED_INTERP:
            lo = int(math.floor(left.co.x)) + 1
            hi = int(math.ceil(right.co.x)) - 1
            for f in range(lo, hi + 1):
                if left.co.x < f < right.co.x:
                    keys.append(Key(float(f), fcurve.evaluate(f) * value_sign))
        keys.append(Key(right.co.x, right.co.y * value_sign))
    return Segment(LINEAR, keys)
```

Builds one `Segment` from keyframe indices `start..end` (inclusive). Three
branches:

- **HERMITE** — for each key, compute in/out slopes (§6.8) and store
  `Key(frame, value·sign, in, out)`. Values scale by `value_sign`; frames don't.
- **STEP** — just the `(frame, value·sign)` pairs; no slopes needed.
- **LINEAR** — the interesting one. It emits the first key, then walks each
  interval. **If an interval uses an eased interpolation** (`left.interpolation in
  _SAMPLED_INTERP`), it can't be a straight line, so it **inserts per-frame sample
  keys**: it evaluates the real curve with **`fcurve.evaluate(f)`** (Blender
  computes the true eased value at frame `f`) for every whole frame strictly
  inside the interval, then appends the interval's end key. A linear engine
  evaluator connecting those dense samples closely follows the eased shape.
  `math.floor(...)+1` / `math.ceil(...)-1` compute the first/last *interior*
  whole frames; the `left.co.x < f < right.co.x` guard is belt-and-suspenders
  against edge cases.

`fcurve.evaluate(frame)` is the workhorse: it returns the curve's value at *any*
frame, honoring whatever interpolation Blender uses — so we don't have to
re-implement easing math.

### 6.10 Splitting a curve into segments — `segments_from_fcurve`

```python
def segments_from_fcurve(fcurve, value_sign):
    kps = fcurve.keyframe_points
    n = len(kps)
    if n == 0:
        return []
    if n == 1:
        return [Segment(LINEAR, [Key(kps[0].co.x, kps[0].co.y * value_sign)])]

    segments = []
    run_start = 0
    run_type = _interval_type(kps[0].interpolation)
    for i in range(1, n - 1):                        # interior keys only
        itype = _interval_type(kps[i].interpolation)
        if itype != run_type:
            segments.append(_make_segment(fcurve, kps, run_start, i, run_type, value_sign))
            run_start = i                            # next run starts AT the shared key
            run_type = itype
    segments.append(_make_segment(fcurve, kps, run_start, n - 1, run_type, value_sign))
    return segments
```

A single f-curve can mix interpolation types (some keys Bézier, some linear). This
splits it into consecutive **runs of the same kind**. The algorithm is a classic
**run-length grouping**:

- Edge cases first: no keys → no segments; one key → a single held linear key
  (a constant channel).
- Otherwise, remember the kind of the interval *leaving* key 0 (`run_type`).
  Interpolation is stored on the **left** key of each interval, so we scan the
  *interior* keys; whenever a key's interval kind differs from the current run,
  **close the current segment** at that key and open a new run starting *at the
  same key*.
- **The shared boundary key is repeated** in both neighboring segments (the old
  run ends at index `i`, the new run starts at index `i`). That makes each segment
  self-contained — the engine can evaluate any segment without peeking at its
  neighbor.

The final `_make_segment(..., n - 1, ...)` closes the last run at the final key.

### 6.11 Translation & scale channels — `_authored_component_segments`

```python
def _authored_component_segments(fc_by_key, prop, axis_map, apply_sign, default, start):
    axis_segments = []
    for tgt in range(3):
        src_axis, sign = axis_map[tgt]
        value_sign = sign if apply_sign else 1.0
        fc = fc_by_key.get((prop, src_axis))
        if fc is None or len(fc.keyframe_points) == 0:
            held = default * value_sign
            axis_segments.append([Segment(LINEAR, [Key(float(start), held)])])
        else:
            axis_segments.append(segments_from_fcurve(fc, value_sign))
    return axis_segments
```

This produces `[x_segments, y_segments, z_segments]` for a translate, scale, *or*
euler-rotation component by **relabeling channels** — the exact, tangent-preserving
coordinate conversion. For each **target** axis:

- `axis_map[tgt]` gives the **source** Blender axis and sign feeding it. (Callers
  pass `conv.axis_map` for translate/scale, `rotation_axis_map(conv)` for
  rotation.)
- `apply_sign` decides whether the sign is used: **True** for translation and
  rotation (they can negate under a flip), **False** for scale (a magnitude is
  never negative — you never flip a scale's sign).
- It fetches the source f-curve `fc_by_key[(prop, src_axis)]`. If there's **no
  curve** for that axis, the channel holds a **neutral default** — `0` for
  translate/rotation, `1` for scale — so the engine always gets a valid value.
  Otherwise it converts the real curve with `segments_from_fcurve`.

Why is a per-channel relabel *exact*? Because the remap matrix `C` is a **signed
permutation** — each target axis is fed by exactly one source axis with a ±1
factor, no blending. So converting the whole matrix and converting each channel
separately give identical results, *and* the channel version preserves the
authored keyframe tangents (no re-sampling). This equivalence is verified
numerically and documented in
[`coordinate-systems.md` §4](./coordinate-systems.md).

### 6.12 Rotation — the hard case

Rotation is the one component that *can't* always be a clean relabel. The module
handles it in two paths.

First, a helper to collect all rotation keyframe times:

```python
def _rotation_key_frames(fc_by_key):
    frames = set()
    for (prop, _idx), fc in fc_by_key.items():
        if prop.startswith('rotation'):
            for kp in fc.keyframe_points:
                frames.add(kp.co.x)
    return sorted(frames)
```

The **union** of every rotation channel's keyframe frames (a `set` de-dupes,
`sorted` orders them). Used only for the sampling path below.

Sampling a bone's converted rotation at a moment in time:

```python
_ROT_SLOPE_DELTA = 0.5   # half-width (frames) of the finite difference

def _sample_euler(scene, pbone, conv, order, frame, prev):
    whole = int(math.floor(frame))
    scene.frame_set(whole, subframe=frame - whole)
    _, quat, _ = conv.matrix(pbone.matrix_basis).decompose()
    return quat.to_euler(order, prev) if prev is not None else quat.to_euler(order)
```

A dense little function; every piece earns its place:

- **`scene.frame_set(whole, subframe=…)`** — move the scene to `frame`. Because
  `frame` can be *fractional* (the sampler uses half-frame offsets), the integer
  part is the frame and the remainder is passed as `subframe`. Setting the frame
  makes Blender **evaluate the rig** — constraints, drivers, everything — so the
  pose is real.
- **`pbone.matrix_basis`** — the pose bone's **local transform relative to its
  rest pose**: exactly the delta the f-curves drive. (The `.anim`'s
  `AnimatedTransform` is this, so the engine reconstructs `restLocal ·
  AnimatedTransform`.)
- **`conv.matrix(...)`** — re-express that matrix in engine axes (the `C·M·C⁻¹`
  conjugation from the `Conversion` class).
- **`.decompose()`** returns `(loc, quat, scale)`; we keep only the **quaternion**
  (`_, quat, _`) so scale/translation can't skew the rotation.
- **`quat.to_euler(order, prev)`** — convert to Euler angles in the permuted
  `order`. Passing **`prev`** (the previous frame's Euler) is crucial: Euler
  angles are ambiguous (an angle and angle ± 360° describe the same rotation), and
  `to_euler`'s "compatibility" argument picks the representation **closest to
  `prev`**, keeping the curve continuous instead of jumping by 2π or flipping at
  gimbal-lock boundaries.

Now the dispatcher:

```python
def rotation_segments(scene, pbone, fc_by_key, conv, order, start):
    if pbone.rotation_mode == 'XYZ':
        return _authored_component_segments(fc_by_key, 'rotation_euler',
                                            rotation_axis_map(conv), apply_sign=True,
                                            default=0.0, start=start)

    key_frames = _rotation_key_frames(fc_by_key)
    if not key_frames:
        e = _sample_euler(scene, pbone, conv, order, float(start), None)
        return [[Segment(LINEAR, [Key(float(start), e[axis_i])])] for axis_i in range(3)]

    d = _ROT_SLOPE_DELTA
    frames = sorted({f + off for f in key_frames for off in (-d, 0.0, d)})
    euler_at = {}
    prev = None
    for f in frames:
        prev = _sample_euler(scene, pbone, conv, order, f, prev)
        euler_at[f] = prev

    axis_keys = ([], [], [])
    for f in key_frames:
        here, before, after = euler_at[f], euler_at[f - d], euler_at[f + d]
        for axis_i in range(3):
            slope = (after[axis_i] - before[axis_i]) / (2.0 * d)   # value per frame
            axis_keys[axis_i].append(Key(float(f), here[axis_i], slope, slope))
    return [[Segment(HERMITE, axis_keys[axis_i])] for axis_i in range(3)]
```

**Path A — the bone is in Euler XYZ mode** (`pbone.rotation_mode == 'XYZ'`, the
intended setup). Then the bone already has `rotation_euler` f-curves, and rotation
is *just another relabel* — the same `_authored_component_segments` used for
translation, with `rotation_axis_map(conv)` (the sign×det map from §6.4). This is
**exact**: sparse keys, Bézier tangents, everything preserved. Fast and faithful.

**Path B — any other rotation mode** (Quaternion, Axis-Angle, or a non-XYZ Euler
order). There are *no* `rotation_euler` curves to relabel, and converting a
quaternion curve to Euler can't be done channel-by-channel. So the code **samples**:

1. `key_frames` = the authored key times (still **sparse** — we never bake every
   frame).
2. For a **constant rotation** (no rotation keys at all), emit one held linear key.
3. Otherwise, sample each key time **and its two neighbors** at ±`d` (`d = 0.5`
   frames). Building `frames` as the sorted set of `{f-d, f, f+d}` and iterating in
   order lets us thread `prev` through the *entire* sequence once — so Euler
   continuity (the `prev` trick in `_sample_euler`) is maintained globally.
4. For each real key, estimate the tangent by a **central finite difference**:
   `slope = (euler(f+d) − euler(f−d)) / (2d)`. This is the standard numerical
   approximation of a derivative — the average rate of change across a small
   window centered on the key. The same slope is used for both `in` and `out`,
   producing a smooth **Hermite** key. (A plain line between sampled keys would
   flatten the rotation's easing; the finite-difference tangents restore the curve
   shape.)

**Why two paths?** The pipeline is *designed* for Euler-XYZ bones (Path A, exact).
Path B is a correct fallback so nothing breaks, but it's an approximation — which
is why (see §6.17) the operator **warns** you which bones took it, so you can fix
their rotation mode.

### 6.13 The rest (bind) pose — `rest_transform`

```python
def rest_transform(bone, conv, order):
    m = conv.matrix(export_anim._local_rest_matrix(bone))
    loc, quat, scale = m.decompose()
    return scale, quat.to_euler(order), loc
```

The `<Skeleton>` block needs each bone's **rest pose** — its parent-relative bind
transform, the pose with no animation applied.

- **`_local_rest_matrix(bone)`** (reused from `export_anim`) computes the data
  bone's parent-relative bind matrix: `parent.matrix_local.inverted() @
  bone.matrix_local` (or just `matrix_local` for a root bone). "Parent-relative"
  means it's expressed *in the parent's frame*, which is exactly what a hierarchy
  reconstructs from.
- Convert it (`conv.matrix`), decompose, and return `(scale, euler, translate)`
  with the Euler in the **same permuted `order`** the animation uses — so rest and
  animation compose consistently in the engine.

Note the returned tuple order is `(scale, euler, loc)` to match how the XML
`<Transform>` lists them, even though `decompose()` yields `(loc, quat, scale)`.

### 6.14 The XML builders

With all data prepared as `Segment`/`Key` objects, the rest is mechanical
tree-building with **`xml.etree.ElementTree`** (aliased `ET`), Python's standard
XML library. There's one small builder per XML element, composed bottom-up. The
core `ET` calls:

- `ET.Element("Tag")` — make a standalone element.
- `ET.SubElement(parent, "Tag")` — make an element *and* append it to `parent`.
- `el.set("attr", "value")` — set an attribute (values must be strings).
- `el.text = "..."` — set the text between the tags.
- `parent.append(child)` — attach a pre-built element.

Starting from a single key:

```python
def build_key(seg_type, key):
    el = ET.Element(TAG[KEY_TAG_KEY[seg_type]])       # <LinearKey>/<HermiteKey>/<StepKey>
    el.set(ATTR["frame"], _fmt_frame(key.frame))
    el.set(ATTR["value"], _fmt_num(key.value))
    if seg_type == HERMITE:                            # slopes only on Hermite keys
        el.set(ATTR["in_slope"], _fmt_num(key.in_slope))
        el.set(ATTR["out_slope"], _fmt_num(key.out_slope))
    return el
```

The element's tag is looked up through the double indirection
`TAG[KEY_TAG_KEY[seg_type]]` — `KEY_TAG_KEY` maps the kind to a `TAG` key, and
`TAG` maps that to the actual string. Every key gets `Frame` and `Value`; only
Hermite keys also get `InSlope`/`OutSlope`.

The wrappers nest outward, each one trivial:

```python
def build_segment(segment):
    seg_el = ET.Element(TAG[SEGMENT_TAG_KEY[segment.type]])   # <LinearSegment> etc.
    keys_el = ET.SubElement(seg_el, TAG["keys"])              # <keys>
    for key in segment.keys:
        keys_el.append(build_key(segment.type, key))
    return seg_el

def build_axis_segments(axis_index, segments):
    axis_el = ET.Element(TAG[AXIS_SEG_KEY[axis_index]])       # <XSegmentsFloat3A> etc.
    for segment in segments:
        axis_el.append(build_segment(segment))
    return axis_el

def build_component_animation(comp_tag_key, axis_segments_list):
    comp_el = ET.Element(TAG[comp_tag_key])                   # <ScaleFloat3Animation> etc.
    for axis_index, segments in enumerate(axis_segments_list):
        comp_el.append(build_axis_segments(axis_index, segments))
    return comp_el
```

Read bottom-up, the nesting is: `component → axis (X/Y/Z) → segments → <keys> →
keys`. `build_component_animation` takes the `[x_segs, y_segs, z_segs]` list from
the extraction layer and wraps each axis in its `X/Y/Z SegmentsFloat3A` element.

Assembling the three components for a bone:

```python
def build_transform_animation(pbone, fc_by_key, conv, order, scene, start):
    trans_anim = ET.Element(TAG["transform_anim"])

    scale_segs = _authored_component_segments(fc_by_key, 'scale', conv.axis_map,
                                              apply_sign=False, default=1.0, start=start)
    trans_anim.append(build_component_animation("scale_anim", scale_segs))

    rot_segs = rotation_segments(scene, pbone, fc_by_key, conv, order, start)
    trans_anim.append(build_component_animation("rotate_anim", rot_segs))

    trans_segs = _authored_component_segments(fc_by_key, 'location', conv.axis_map,
                                              apply_sign=True, default=0.0, start=start)
    trans_anim.append(build_component_animation("translate_anim", trans_segs))
    return trans_anim
```

This ties the whole extraction layer together. `<transformAnimation>` holds three
component animations, each fed by the right extractor:

- **Scale** — authored curves, axis relabel only (`apply_sign=False`), neutral
  default `1`.
- **Rotate** — via `rotation_segments` (the two-path logic from §6.12), Euler
  radians in engine space.
- **Translate** — authored curves, relabel *and* sign (`apply_sign=True`), neutral
  default `0`.

The per-bone wrapper adds the `<Path>` that names the channel:

```python
def build_path(bone_name):
    el = ET.Element(TAG["path"])
    el.text = PATH_TEMPLATE.format(bone=bone_name)   # Skeleton.Bones["X"].AnimatedTransform
    return el

def build_bone_animation(pbone, fc_by_key, conv, order, scene, start):
    bone_anim = ET.Element(TAG["bone_anim"])          # <BoneAnimationData>
    bone_anim.append(build_path(pbone.name))
    bone_anim.append(build_transform_animation(pbone, fc_by_key, conv, order, scene, start))
    return bone_anim
```

Now the **skeleton** (rest pose) side:

```python
def _set_xyz(el, vec):
    el.set(ATTR["vx"], _fmt_num(vec[0]))
    el.set(ATTR["vy"], _fmt_num(vec[1]))
    el.set(ATTR["vz"], _fmt_num(vec[2]))
    return el

def build_transform(scale, euler_rad, translate):
    tf = ET.Element(TAG["transform"])
    _set_xyz(ET.SubElement(tf, TAG["scale"]), scale)
    _set_xyz(ET.SubElement(tf, TAG["rotate"]), euler_rad)   # radians
    _set_xyz(ET.SubElement(tf, TAG["translate"]), translate)
    return tf

def build_bone(pbone, conv, order):
    bone_el = ET.Element(TAG["bone"])
    bone_el.set(ATTR["bone_name"], pbone.name)
    bone_el.set(ATTR["bone_parent"], pbone.parent.name if pbone.parent else "")
    bone_el.set(ATTR["bone_skin"], BOOL_STR[bool(pbone.bone.use_deform)])
    scale, euler, loc = rest_transform(pbone.bone, conv, order)
    bone_el.append(build_transform(scale, euler, loc))
    return bone_el
```

- `_set_xyz` writes three `x`/`y`/`z` attributes onto a `<Scale>`/`<Rotate>`/
  `<Translate>` element — used for the *static* rest pose (single values, not
  curves).
- `build_bone` records the bone's **name**, its **parent's name** (empty string
  for a root — the `... if pbone.parent else ""` guard), and **`HasSkiningMatrix`**
  from `pbone.bone.use_deform`. `use_deform` is Blender's per-bone "Deform"
  checkbox: `True` means the bone actually skins vertices, so the engine should
  allocate a skinning matrix for it. `BOOL_STR[...]` converts the Python bool to
  `"true"`/`"false"`.

Rotation in the rest transform is written as **radians** (Blender's native angle
unit) — matching the animation's rotation channels, which are also radians.

```python
def build_skeleton(bones, conv, order):
    skel = ET.Element(TAG["skeleton"])
    root_name = next((pb.name for pb in bones if pb.parent is None),
                     bones[0].name if bones else "")
    skel.set(ATTR["skel_root"], root_name)
    skel.set(ATTR["skel_scaling"], ROOT_SCALING_RULE)
    bones_el = ET.SubElement(skel, TAG["bones"])
    for pbone in bones:
        bones_el.append(build_bone(pbone, conv, order))
    return skel
```

The `<Skeleton>` names a **root bone** — the first bone with no parent
(`next((... for ... if pb.parent is None), fallback)` returns the first match or
the fallback default) — sets the `scalingRule` attribute, and appends one
`<Bone>` per bone.

Finally the metadata:

```python
def build_metadata(action, start, end, fps, conv, order, loop_mode, path):
    meta = ET.Element(TAG["metadata"])
    meta.set(ATTR["meta_name"], action.name)
    meta.set(ATTR["meta_frame_size"], str(end - start))   # clip span in frames
    meta.set(ATTR["meta_loop_mode"], loop_mode)
    meta.set(ATTR["meta_path"], path)
    meta.set(ATTR["meta_euler_order"], order)              # e.g. "XZY"
    meta.set(ATTR["meta_coord"], conv.remap)               # e.g. "X Z -Y"
    meta.set(ATTR["meta_fps"], _fmt_num(fps))
    return meta
```

The `<metadata>` element is the engine's decoder ring. The two most important
attributes are **`CoordSystem`** (the remap that was used) and **`EulerOrder`**
(how to compose the three rotation channels) — an engine *must* read `EulerOrder`
or rotations will be wrong (see the gotcha in §8). `FrameSize` is the span
`end − start` (the comment notes you'd use `end − start + 1` if you wanted a
frame *count* instead).

### 6.15 Orchestration — `build_animation_document`

```python
def build_animation_document(context, armature, action, conv, loop_mode, path):
    scene = context.scene
    bones = export_anim._ordered_bones(armature)             # parents before children
    index_of = {pb.name: i for i, pb in enumerate(bones)}
    start, end = resolve_frame_range(action)
    fps = scene.render.fps / scene.render.fps_base
    order = permuted_euler_order(conv)                        # 'XYZ' native, 'XZY' engine

    non_euler_bones = [pb.name for pb in bones if pb.rotation_mode != 'XYZ']

    root = ET.Element(TAG["root"])
    root.append(build_metadata(action, start, end, fps, conv, order, loop_mode, path))

    anim_data = armature.animation_data or armature.animation_data_create()
    saved_action = anim_data.action
    saved_frame = scene.frame_current
    anim_data.action = action
    try:
        by_bone = group_fcurves_by_bone(action, anim_data, index_of)
        for pbone in bones:
            fc_by_key = by_bone.get(pbone.name, {})
            root.append(build_bone_animation(pbone, fc_by_key, conv, order, scene, start))
    finally:
        anim_data.action = saved_action
        scene.frame_set(saved_frame)

    root.append(build_skeleton(bones, conv, order))
    summary = f"{len(bones)} bones, take '{action.name}', frames {start}-{end}"
    return root, summary, non_euler_bones
```

This assembles the whole document. Points worth calling out:

- **`_ordered_bones(armature)`** (reused) returns pose bones **topologically
  sorted** so every parent precedes its children. An engine reconstructing world
  transforms needs parents resolved first, and Blender's native `pose.bones`
  order isn't guaranteed hierarchical. (The helper matches parent/child links **by
  name**, because Blender hands back a fresh Python wrapper on each access, so
  `is`-identity comparison of pose bones is unreliable — a genuine Blender gotcha.)
- **`index_of`** — a `{name: index}` map built with a dict comprehension over the
  ordered bones.
- **`fps = scene.render.fps / scene.render.fps_base`** — Blender stores frame rate
  as an integer `fps` divided by an `fps_base`, which together express fractional
  rates like 23.976 (`24 / 1.001`). Always divide the two.
- **`order = permuted_euler_order(conv)`** — computed once and threaded everywhere.
- **`non_euler_bones`** — collected up front for the warning (§6.17).
- **The `try/finally` around the sampling loop** is critical. To sample rotation
  (Path B in §6.12), the code must *assign the action to the rig* and *move the
  playhead* — mutating the scene. It first saves the current action and frame,
  assigns our action, does the work, and in `finally` **restores both**. So even if
  a bone fails mid-sample, the user's scene is left exactly as it was.
  `armature.animation_data or armature.animation_data_create()` gets the
  animation-data block, creating one if the armature has none yet.
- Assembly order matches the XML: metadata, then all `BoneAnimationData`, then the
  `Skeleton`.

### 6.16 Writing the file — `write_animation_xml`

```python
def write_animation_xml(context, filepath, armature, action, remap, loop_mode, path):
    conv = export_anim.Conversion(remap)
    root, summary, non_euler = build_animation_document(context, armature, action,
                                                        conv, loop_mode, path)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")                    # pretty-print (Python 3.9+)
    tree.write(filepath, encoding="utf-8", xml_declaration=True)
    return summary, non_euler
```

The standalone entry point (deliberately independent of the operator/UI so the
format is easy to test in isolation):

- **`export_anim.Conversion(remap)`** builds the conversion object — and **raises
  `ValueError` on a bad remap string**, which the operator catches and reports.
- `ET.ElementTree(root)` wraps the root element in a writable tree.
- **`ET.indent(tree, space="    ")`** pretty-prints with 4-space indents (added in
  Python 3.9; Blender ships a new enough Python).
- **`tree.write(filepath, encoding="utf-8", xml_declaration=True)`** writes the
  file, including the `<?xml version='1.0' encoding='utf-8'?>` declaration line.

### 6.17 The operators

**`MTOOLS_OT_set_anim_xml_export_path`** is the file-picker, identical in shape to
the FBX one (§5) — `invoke` seeds the path and opens the browser, `execute` stores
the chosen path (with `_ensure_anim_ext`).

**`MTOOLS_OT_export_animation_xml`** is the main export. Its `poll` requires an
armature; its `invoke` implements the same "ask once, then remember" flow (if a
path is stored, export immediately; otherwise open the browser). The interesting
work is in `execute`:

```python
def execute(self, context):
    scene = context.scene
    armature = scene.mtools_animx_armature
    if armature is None or armature.type != 'ARMATURE':
        self.report({'WARNING'}, "Pick an armature to export")
        return {'CANCELLED'}

    action = self._resolve_action(context)
    if action is None:
        self.report({'WARNING'}, "Pick a take (action), or assign one to the armature")
        return {'CANCELLED'}

    if not self.filepath:
        self.report({'WARNING'}, "No export path set")
        return {'CANCELLED'}

    path = _ensure_anim_ext(self.filepath)
    scene.mtools_animx_export_path = path

    _coord, remap = resolve_export_remap(scene)
    loop_mode = scene.mtools_animx_loop_mode
    meta_path = os.path.basename(path)                 # <metadata path="...">
    try:
        summary, non_euler = write_animation_xml(context, path, armature, action,
                                                 remap, loop_mode, meta_path)
    except Exception as exc:
        self.report({'ERROR'}, f"Animation export failed: {exc}")
        return {'CANCELLED'}

    if non_euler:
        shown = ", ".join(non_euler[:5]) + ("..." if len(non_euler) > 5 else "")
        self.report({'WARNING'}, f"{len(non_euler)} bone(s) not in Euler XYZ mode "
                    f"(set them to Euler XYZ for exact curves): {shown}")

    self.report({'INFO'}, f"Exported {summary} to {path}")
    return {'FINISHED'}
```

The flow: **validate → resolve settings → write → report**.

- **`_resolve_action`** (a small method) uses the picked "Take" if set, otherwise
  falls back to the armature's currently-assigned action — a convenience so you
  don't *have* to pick if the rig already has one active.
- **`resolve_export_remap(scene)`** — the shared coordinate resolver, so this
  export uses the same axes as the FBX.
- **`meta_path = os.path.basename(path)`** — only the filename (not the full disk
  path) goes into `<metadata path>`, which is what an engine wants.
- **Broad `except Exception`** — a bad remap, a sampling failure, or an I/O error
  all become one clean `{'ERROR'}` report rather than an add-on traceback. This is
  appropriate at the *operator boundary* (the outermost layer the user triggers);
  deeper code still raises specific exceptions.
- **The non-Euler warning** — if any bone wasn't Euler XYZ (so it took the
  approximate Path B), the operator lists up to 5 of them and tells you to switch
  them to Euler XYZ for exact curves. `non_euler[:5]` slices the first five,
  `"..."` is appended when there are more. This is the promised feedback loop that
  turns a silent approximation into an actionable message.

### 6.18 Registration — and who owns the shared props

```python
def register_props():
    bpy.types.Scene.mtools_animx_armature = bpy.props.PointerProperty(
        type=bpy.types.Object, poll=lambda self, obj: obj.type == 'ARMATURE')
    bpy.types.Scene.mtools_animx_action = bpy.props.PointerProperty(type=bpy.types.Action)
    bpy.types.Scene.mtools_animx_export_path = bpy.props.StringProperty(subtype='FILE_PATH', default="")
    bpy.types.Scene.mtools_animx_loop_mode = bpy.props.EnumProperty(items=LOOP_ITEMS, default='ONCE')

    # --- shared coordinate props (FBX + animation) ---
    bpy.types.Scene.mtools_export_coord = bpy.props.EnumProperty(items=COORD_ITEMS, default='ENGINE')
    bpy.types.Scene.mtools_export_coord_remap = bpy.props.StringProperty(default="X Z -Y")
```

The animation-specific props (armature, action, path, loop mode) plus — crucially
— **the two shared coordinate props**. They're registered here, in the animation
module, but read by the FBX exporter too. That's a deliberate ownership decision:
one module owns the coordinate system, and everything else consumes it. Default
coordinate is `'ENGINE'` (`"X Z -Y"`), the common Blender→Y-up-engine conversion.

`unregister_props` deletes every property (in a tuple, guarded by `hasattr`) so
the add-on tears down cleanly.

---

## 7. The coordinate math, in one page

The exporters share one coordinate abstraction. Here's the minimum to follow the
code; the full treatment (with proofs and troubleshooting) is in
[`coordinate-systems.md`](./coordinate-systems.md).

**A remap string → a matrix `C`.** `"X Z -Y"` means "engine X = Blender X, engine
Y = Blender Z, engine Z = −Blender Y." `build_conversion_matrix` turns it into a
3×3 matrix `C` with one ±1 per row/column (a **signed permutation matrix**) such
that `C @ blender_vector = engine_vector`.

```
              Bx By Bz
   engine X [  1  0  0 ]
C =  … Y   [  0  0  1 ]        C @ (x, y, z) = (x, z, −y)
     … Z   [  0 −1  0 ]
```

**Three properties make the code clean:**
- `C` is **orthogonal**, so `C⁻¹ = Cᵀ`.
- `|det C| = 1` always. `det = +1` preserves handedness; **`det = −1` mirrors**
  (targets a left-handed engine, flips winding — see §8).
- It's a **permutation**, so converting each channel independently equals
  converting the whole matrix.

**Converting different data:**
| Data | Method | Where |
|---|---|---|
| a point/vector | `C · v` | (rest translation) |
| a full transform | `C · M · C⁻¹` (conjugation) | `Conversion.matrix()` — rest pose, rotation sampling |
| a translate/scale curve | per-channel **relabel** (+ sign for translate) | `_authored_component_segments` |
| an euler-rotation curve | relabel, sign × `det` | `rotation_axis_map` + `_authored_component_segments` |
| the euler **order** | permute the letters | `permuted_euler_order` |

The `Conversion` class (in `export_anim.py`) precomputes `C`, its 4×4 form, its
cached inverse, and the `axis_map`, so per-bone/per-frame conversion is cheap.

**Why the relabel is exact and the conjugation is the reference:** decomposing
`C·M·C⁻¹` gives translation `C·t`, permuted scale (no sign), and rotation
`C·R·C⁻¹` = "rotate about `C·axis` by `det·θ`." Each result is a per-channel
relabel — proven equal to the matrix method, and it preserves keyframe tangents
because nothing is re-sampled. That equivalence is why animation curves (relabel)
and the rest pose (conjugation) always land in the same space.

---

## 8. Gotchas, collected

A checklist of the non-obvious things these scripts get right — and that you must
respect if you modify them or write the engine-side importer.

1. **FBX axis settings can't express a mirror.** `resolve_fbx_axes` returns `None`
   for a `det = −1` remap (left-handed target). The FBX operator then *warns and
   falls back* to native axes — FBX and `.anim` won't match. For left-handed
   engines, export with a `det = +1` remap and let the engine apply the flip.
   ([coordinate-systems.md §6.4](./coordinate-systems.md))

2. **The engine must read `EulerOrder` from metadata.** The default engine export
   writes `XZY`, not `XYZ`. If the importer hard-codes `XYZ`, rotations animate the
   wrong axes even though translation looks fine.

3. **Non-Euler-XYZ bones are approximated.** Quaternion/Axis-Angle/non-XYZ bones
   take the sampling path (finite-difference Hermite tangents), not exact curves.
   The operator warns you; fix by setting the bone's Rotation Mode to Euler XYZ.

4. **Scale is never sign-flipped.** A magnitude can't be negative; only
   translation and rotation carry the coordinate sign. `apply_sign=False` enforces
   this for scale.

5. **Selection is mutated and restored.** The FBX exporter changes the active
   object and selection to export exactly the rig + bound meshes, then restores
   your selection in a `finally`. Likewise the animation exporter temporarily
   assigns the action and moves the playhead, then restores both.

6. **`fps` is `fps / fps_base`.** Never read `scene.render.fps` alone — fractional
   rates (23.976, 29.97) live in `fps_base`.

7. **Pose bones can't be compared with `is`.** Blender returns a fresh wrapper each
   access, so `_ordered_bones` matches parent/child **by name**.

8. **F-curve access is version-dependent.** `_action_fcurves` hides the pre-4.4
   flat API vs. the 4.4+ layered/slotted API vs. 5.0's removal of
   `action.fcurves`. Don't access `action.fcurves` directly in new code.

9. **The FBX add-on can be disabled.** Both FBX operators check
   `hasattr(bpy.ops.export_scene, "fbx")` before exporting.

10. **Bézier→Hermite is an endpoint-exact approximation.** Blender's weighted
    handles (length matters) become plain Hermite slopes (endpoints only), so a
    segment's *interior* can differ slightly; endpoints match exactly.

---

## 9. Blender/Python API cheat sheet

Everything used across these three files, in one place.

### Blender data & context
| Expression | Meaning |
|---|---|
| `bpy.data.filepath` | Path of the saved `.blend` (`""` if unsaved) |
| `context.scene` | The active scene (where MTools stores its settings) |
| `context.view_layer.objects` | Objects in the current view layer (selectable/exportable) |
| `context.active_object` | The last-clicked object |
| `context.selected_objects` | Currently selected objects |
| `obj.type` | `'ARMATURE'`, `'MESH'`, … |
| `obj.modifiers` | The modifier stack; `mod.type`, `mod.object` |
| `obj.parent` | Parent object (or `None`) |
| `obj.select_set(True)` / `obj.select_get()` | Set / read selection |
| `armature.pose.bones` | Pose bones (animated); `pbone.parent`, `pbone.matrix_basis`, `pbone.rotation_mode` |
| `pbone.bone` | The underlying data bone; `bone.matrix_local`, `bone.use_deform` |
| `armature.animation_data` / `.animation_data_create()` | Animation block (action, slot) |
| `action.frame_range` | `Vector(start, end)` of the action |
| `action.name` | The take's name |
| `scene.render.fps` / `.fps_base` | Frame rate = `fps / fps_base` |
| `scene.frame_set(f, subframe=…)` | Move the playhead (evaluates the rig) |
| `fcurve.data_path`, `.array_index` | Channel id (e.g. `pose.bones["X"].location`, index 1) |
| `fcurve.keyframe_points` | The keys; `kp.co`, `kp.handle_left/right`, `kp.interpolation` |
| `fcurve.evaluate(frame)` | The curve's value at any frame |

### Blender operators & UI
| Expression | Meaning |
|---|---|
| `bpy.ops.export_scene.fbx(...)` | Built-in FBX exporter (needs the add-on enabled) |
| `bpy.ops.object.mode_set(mode='OBJECT')` | Switch mode |
| `bpy.ops.object.select_all(action='DESELECT')` | Select/deselect all |
| `context.window_manager.fileselect_add(self)` | Open the modal file browser |
| `self.report({'INFO'/'WARNING'/'ERROR'}, msg)` | Status message |
| `layout.column/row/prop/operator/label/separator` | Build panel UI |
| `bpy.props.PointerProperty/StringProperty/EnumProperty` | Declare scene properties |
| `bpy.utils.register_class` / `unregister_class` | Make a class live / remove it |

### mathutils
| Expression | Meaning |
|---|---|
| `Matrix @ Matrix` / `@ Vector` | Matrix multiply |
| `Matrix.inverted()` | Inverse |
| `Matrix.decompose()` | → `(location, quaternion, scale)` |
| `Matrix.determinant()` | Handedness sign for a basis change (±1) |
| `Quaternion.to_euler(order, prev)` | Rotation → Euler; `prev` keeps continuity |

### Standard library
| Expression | Meaning |
|---|---|
| `xml.etree.ElementTree` (`ET`) | Build/write XML: `Element`, `SubElement`, `.set`, `.text`, `.indent`, `.write` |
| `collections.namedtuple` | Lightweight records (`Key`, `Segment`) |
| `re.compile(...)`, `.match`, `.group` | The bone-path regex |
| `os.path` (`join`, `dirname`, `basename`, `splitext`) | Path manipulation |
| `math.floor/ceil/degrees/radians` | Frame/sub-frame and angle math |

---

### See also
- [`coordinate-systems.md`](./coordinate-systems.md) — the deep dive on the axis
  math: the matrix `C`, conjugation vs. relabel, handedness, the "forward" gotcha,
  and a full troubleshooting guide.
- `mtools/ops/export_anim.py` — the shared toolkit (`Conversion`,
  `_action_fcurves`, `_ordered_bones`, rest/pose matrices) and the sibling `.txt`
  animation exporter.
- `mtools/utils/coord_convert.py` — a standalone experimentation toolbox for the
  coordinate math (nothing imports it; load it in Blender's console to test ideas).
