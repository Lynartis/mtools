# Building a Blender Sidebar Panel Like tools — From Scratch

A complete, well-documented tutorial for building a Blender add-on that puts a
panel in the 3D Viewport sidebar, with a **Reload Scripts** button and a set of
buttons that each run their own script.

This mirrors the **tools** add-on in this repo, so once you finish you'll
understand every file in `tools/`.

---

## Table of Contents

1. [What we're building](#1-what-were-building)
2. [Core concepts (read this first)](#2-core-concepts-read-this-first)
3. [Where Blender finds your code](#3-where-blender-finds-your-code)
4. [Stage 1 — A single-file panel with one button](#4-stage-1--a-single-file-panel-with-one-button)
5. [Stage 2 — The "Reload Scripts" button](#5-stage-2--the-reload-scripts-button)
6. [Stage 3 — One button per script](#6-stage-3--one-button-per-script)
7. [Stage 4 — Growing into a package (the tools layout)](#7-stage-4--growing-into-a-package-the-tools-layout)
8. [Stage 5 — Hot-reload support](#8-stage-5--hot-reload-support)
9. [Stage 6 — Keyboard shortcuts (keymaps)](#9-stage-6--keyboard-shortcuts-keymaps)
10. [Stage 7 — Add-on preferences](#10-stage-7--add-on-preferences)
11. [Installing & testing](#11-installing--testing)
12. [Troubleshooting](#12-troubleshooting)
13. [Quick reference cheat-sheet](#13-quick-reference-cheat-sheet)

---

## 1. What we're building

A panel docked in the **N-sidebar** of the 3D Viewport that looks like this:

```
┌─ tools ────────────────┐   ◄ tab in the sidebar (press N to toggle)
│ [⟳ Reload Scripts]      │   ◄ re-loads all add-on code without restarting Blender
│                         │
│ ▼ Mesh Tools            │   ◄ a sub-panel
│   [ Target Weld   ]     │   ◄ each button runs an operator (a "script")
│   [ Boundary Verts]     │
│   [ Boundary Edges]     │
└─────────────────────────┘
```

The two ingredients you need for *any* button-runs-a-script panel:

- **An Operator** — a class with an `execute()` method. This is "the script". When
  the button is clicked, Blender calls `execute()`.
- **A Panel** — a class with a `draw()` method that adds a button referencing the
  operator by its ID.

Everything else (packages, hot-reload, keymaps, preferences) is convenience layered
on top of those two ideas.

---

## 2. Core concepts (read this first)

### Operators = "scripts you can run"

An operator is a unit of action. `bpy.ops.mesh.subdivide()` is an operator; so is
every button in Blender's UI. You define one by subclassing `bpy.types.Operator`:

```python
import bpy

class tools_OT_hello(bpy.types.Operator):
    bl_idname = "tools.hello"          # how you reference it: bpy.ops.tools.hello()
    bl_label = "Say Hello"              # default button text / search name
    bl_description = "Print a greeting" # tooltip on hover
    bl_options = {'REGISTER', 'UNDO'}   # show in redo panel; support Ctrl+Z

    def execute(self, context):
        print("Hello from tools!")     # ← your script body goes here
        self.report({'INFO'}, "Done")   # status-bar message
        return {'FINISHED'}             # must return a status set
```

**Naming rules that Blender enforces:**

| Part        | Rule                                              | Example                 |
|-------------|---------------------------------------------------|-------------------------|
| `bl_idname` | `category.name`, lowercase, must contain one `.`  | `tools.target_weld`    |
| Class name  | Convention `UPPERCASE_OT_lowercase`               | `tools_OT_target_weld` |

`execute()` **must** return one of:
- `{'FINISHED'}` — it worked.
- `{'CANCELLED'}` — it stopped without doing anything (e.g. nothing selected).

### `poll()` — when is the button allowed to run?

An optional classmethod. If it returns `False`, Blender **greys out** the button
and refuses to run the operator. Use it to guard against bad context:

```python
    @classmethod
    def poll(cls, context):
        # Only enabled in Edit Mode on a mesh
        return (context.active_object is not None
                and context.active_object.type == 'MESH'
                and context.mode == 'EDIT_MESH')
```

### Panels = "where the buttons live"

A panel subclasses `bpy.types.Panel` and defines *where* it appears via three
`bl_*` properties, plus a `draw()` method that builds the UI:

```python
class VIEW3D_PT_tools_main(bpy.types.Panel):
    bl_label = "tools"            # header text of the panel
    bl_idname = "VIEW3D_PT_tools_main"
    bl_space_type = 'VIEW_3D'      # which editor: the 3D Viewport
    bl_region_type = 'UI'          # the 'UI' region = the N-sidebar
    bl_category = "tools"         # the vertical tab label in the sidebar

    def draw(self, context):
        layout = self.layout
        # Add a button that runs operator "tools.hello":
        layout.operator("tools.hello", text="Say Hello", icon='INFO')
```

Key panel properties:

| Property         | Meaning                                                       |
|------------------|---------------------------------------------------------------|
| `bl_space_type`  | `'VIEW_3D'` = 3D Viewport. (Others: `'IMAGE_EDITOR'`, etc.)    |
| `bl_region_type` | `'UI'` = the N-sidebar. `'TOOLS'` = the left toolbar.          |
| `bl_category`    | The sidebar tab name. Same string groups panels under one tab.|
| `bl_parent_id`   | Set to another panel's `bl_idname` to nest as a sub-panel.     |

Panel class name convention: `EDITOR_PT_name` → `VIEW3D_PT_tools_main`
(`_PT_` = Panel Type).

### `register()` / `unregister()` — turning classes on and off

Defining a class isn't enough; Blender only knows about it after you **register**
it. Every add-on exposes two module-level functions:

```python
classes = [tools_OT_hello, VIEW3D_PT_tools_main]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):   # reverse order: panels before operators
        bpy.utils.unregister_class(cls)
```

Blender calls `register()` when the add-on is enabled and `unregister()` when it's
disabled or before a reload. **Always unregister in reverse** so dependents come
off before their dependencies.

### `bl_info` — the add-on's identity card

A dict at the top of the main file. Blender reads it to populate the add-on list
in Preferences:

```python
bl_info = {
    "name": "tools",
    "author": "Marc",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),                 # minimum Blender version
    "location": "View3D > Sidebar > tools",
    "description": "Collection of mesh tools",
    "category": "Mesh",
}
```

> **Note on Blender 4.2+ extensions:** newer Blender also supports a
> `blender_manifest.toml` "extension" format. The `bl_info` dict shown here still
> works as a *legacy add-on* in 4.x and is the simplest way to learn. This repo
> targets Blender 3.6, where `bl_info` is the standard.

---

## 3. Where Blender finds your code

You have two practical options while developing:

**Option A — Run it from the Text Editor (fastest to iterate).**
Paste a single-file add-on into Blender's *Scripting* workspace text editor and
press **Run Script** (▶) or `Alt+P`. The script's `register()` is called at the
bottom (see Stage 1). Great for prototyping.

**Option B — Install as an add-on (how users get it).**
Put your code in Blender's `scripts/addons/` folder, then enable it in
`Edit > Preferences > Add-ons`. A multi-file add-on lives in its own folder:

```
.../Blender/3.6/scripts/addons/
└── tools/                ← folder name = add-on name
    ├── __init__.py        ← must exist; holds bl_info + register()
    ├── ui.py
    └── ops/
        └── ...
```

> **Tip for this repo:** instead of copying files into Blender every edit, create a
> *symlink* from Blender's addons folder to this repo's `tools/` folder. Then the
> **Reload Scripts** button (Stage 2) picks up your edits instantly.
>
> Windows (run terminal as admin):
> ```
> mklink /D "%APPDATA%\Blender Foundation\Blender\3.6\scripts\addons\tools" "C:\dev\BlenderScripting\tools"
> ```

---

## 4. Stage 1 — A single-file panel with one button

Start with the smallest thing that works. Create `tools_mini.py` and paste this
into Blender's Text Editor (or save it to `scripts/addons/`):

```python
bl_info = {
    "name": "tools Mini",
    "author": "You",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > tools",
    "description": "Minimal panel example",
    "category": "Mesh",
}

import bpy


# ── The operator (the "script" the button runs) ──
class tools_OT_hello(bpy.types.Operator):
    bl_idname = "tools.hello"
    bl_label = "Say Hello"
    bl_description = "Print a greeting to the console"
    bl_options = {'REGISTER'}

    def execute(self, context):
        print("Hello from tools!")
        self.report({'INFO'}, "Hello printed to console")
        return {'FINISHED'}


# ── The panel (where the button lives) ──
class VIEW3D_PT_tools_main(bpy.types.Panel):
    bl_label = "tools"
    bl_idname = "VIEW3D_PT_tools_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "tools"

    def draw(self, context):
        layout = self.layout
        layout.operator("tools.hello", text="Say Hello", icon='INFO')


# ── Registration ──
classes = [
    tools_OT_hello,
    VIEW3D_PT_tools_main,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


# Lets you run the file directly from the Text Editor for quick testing.
if __name__ == "__main__":
    register()
```

**Test it:** Run the script, then press **N** in the 3D Viewport. You'll see an
**tools** tab; click it; click **Say Hello**. Open the console
(`Window > Toggle System Console` on Windows) to see the print.

That's the entire core idea. Everything below makes it scale and feel professional.

---

## 5. Stage 2 — The "Reload Scripts" button

The killer feature for development: a button that reloads all your code so you
don't have to disable/enable the add-on (or restart Blender) after every edit.

Blender ships an operator that does exactly this: `bpy.ops.script.reload()`. It
re-imports every registered add-on and re-runs the Text Editor scripts. We wrap it
in our own operator so it gets a nice button:

```python
class tools_OT_reload_scripts(bpy.types.Operator):
    bl_idname = "tools.reload_scripts"
    bl_label = "Reload Scripts"
    bl_description = "Reload all Blender scripts (including tools) to pick up external changes"

    def execute(self, context):
        bpy.ops.script.reload()
        self.report({'INFO'}, "All scripts reloaded")
        return {'FINISHED'}
```

Add it to the panel's `draw()` (note the `FILE_REFRESH` icon — the circular arrows):

```python
    def draw(self, context):
        layout = self.layout
        layout.operator("tools.reload_scripts", text="Reload Scripts", icon='FILE_REFRESH')
```

And add the class to your `classes` list so it gets registered.

> **Why this works:** `script.reload()` un-registers and re-imports your add-on
> from disk. As long as your files are saved (and reachable — see the symlink tip),
> one click applies your latest edits. For it to *fully* refresh sub-modules, you
> need the hot-reload pattern in [Stage 5](#8-stage-5--hot-reload-support).

---

## 6. Stage 3 — One button per script

Now the pattern you actually want: several buttons, each running a different
operator. Each operator is its own self-contained "script."

Here's a real one from this repo — convert a face selection to its boundary edges.
Notice how `poll()` guards it to Edit Mode, and how it uses **bmesh** to read/write
mesh data:

```python
import bpy
import bmesh

class tools_OT_select_boundary_edges(bpy.types.Operator):
    """From face selection, select the boundary edges of the selected faces"""
    bl_idname = "tools.select_boundary_edges"
    bl_label = "Select Boundary Edges"
    bl_description = "Convert face selection to boundary edge selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.active_object is not None
                and context.active_object.type == 'MESH'
                and context.mode == 'EDIT_MESH')

    def execute(self, context):
        obj = context.edit_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)

        # A boundary edge of the selection touches exactly one selected face.
        boundary_edges = [e for e in bm.edges
                          if sum(1 for f in e.link_faces if f.select) == 1]

        if not boundary_edges:
            self.report({'WARNING'}, "No boundary found from face selection")
            return {'CANCELLED'}

        # Clear everything, then select just the boundary edges.
        for v in bm.verts: v.select = False
        for e in bm.edges: e.select = False
        for f in bm.faces: f.select = False
        for e in boundary_edges:
            e.select = True

        bm.select_flush_mode()            # propagate the new selection
        bmesh.update_edit_mesh(me)        # push changes back to the mesh
        bpy.ops.mesh.select_mode(type='EDGE')

        self.report({'INFO'}, f"Selected {len(boundary_edges)} boundary edges")
        return {'FINISHED'}
```

To wire up several buttons, give them structure in `draw()` with a column and
sub-panel. This is straight from `tools/ui.py`:

```python
class VIEW3D_PT_tools_mesh(bpy.types.Panel):
    bl_label = "Mesh Tools"
    bl_idname = "VIEW3D_PT_tools_mesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "tools"
    bl_parent_id = "VIEW3D_PT_tools_main"   # ← nests under the main panel

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)      # align=True = buttons touch, no gaps
        col.operator("tools.target_weld", text="Target Weld", icon='VERTEXSEL')
        col.separator()                      # a little vertical space
        col.operator("tools.select_boundary_vertices", text="Boundary Verts", icon='VERTEXSEL')
        col.operator("tools.select_boundary_edges", text="Boundary Edges", icon='EDGESEL')
```

### Layout building blocks

`self.layout` is the root; you nest containers off it:

```python
layout.label(text="A heading", icon='MESH_DATA')   # non-clickable text + icon
layout.operator("tools.hello")                     # a button
row = layout.row()                                  # horizontal group
row.operator("tools.a"); row.operator("tools.b")  # side by side
col = layout.column(align=True)                     # vertical group, no gaps
box = layout.box()                                  # bordered container
col.separator()                                     # spacer
```

### Passing arguments to an operator from a button

`layout.operator()` returns the operator instance, so you can set its properties.
If your operator declares properties, set them per-button:

```python
# In the operator:
class tools_OT_set_smooth(bpy.types.Operator):
    bl_idname = "tools.set_smooth"
    bl_label = "Set Shading"
    smooth: bpy.props.BoolProperty(default=True)
    def execute(self, context):
        bpy.ops.object.shade_smooth() if self.smooth else bpy.ops.object.shade_flat()
        return {'FINISHED'}

# In draw(): two buttons, same operator, different argument
op = layout.operator("tools.set_smooth", text="Smooth"); op.smooth = True
op = layout.operator("tools.set_smooth", text="Flat");   op.smooth = False
```

### Finding icon names

Enable the built-in **Icon Viewer** add-on (`Edit > Preferences > Add-ons >
"Icon Viewer"`). It adds a browser in the Python console showing every icon's
name (the string you pass to `icon=`).

---

## 7. Stage 4 — Growing into a package (the tools layout)

One file gets unwieldy once you have many tools. The professional layout — and the
one this repo uses — splits the add-on into a **package** (a folder with an
`__init__.py`). Here's the tools tree, annotated:

```
tools/
├── __init__.py          ← bl_info, register()/unregister(), hot-reload orchestration
├── ui.py                ← all Panel classes
├── keymaps.py           ← keyboard shortcut registry (Stage 6)
├── preferences.py       ← the add-on preferences UI (Stage 7)
├── ops/                 ← one operator per file
│   ├── __init__.py      ← collects all operator classes into one list
│   ├── reload_scripts.py
│   ├── target_weld.py
│   └── boundary_select.py
└── utils/               ← shared helpers (no operators/panels)
    ├── __init__.py
    └── mesh.py          ← e.g. get_nearest_vertex(), get_nearest_edge()
```

### How the pieces connect

**Each operator file** ends with its own `classes` list:

```python
# ops/reload_scripts.py
import bpy

class tools_OT_reload_scripts(bpy.types.Operator):
    bl_idname = "tools.reload_scripts"
    bl_label = "Reload Scripts"
    bl_description = "Reload all Blender scripts to pick up external changes"
    def execute(self, context):
        bpy.ops.script.reload()
        self.report({'INFO'}, "All scripts reloaded")
        return {'FINISHED'}

classes = [tools_OT_reload_scripts]   # ← every op module exposes this
```

**`ops/__init__.py`** imports each module and merges their lists into one
`ops.classes`:

```python
from . import reload_scripts, target_weld, boundary_select

def _collect_classes():
    return [*reload_scripts.classes, *target_weld.classes, *boundary_select.classes]

classes = _collect_classes()
```

**The package `__init__.py`** imports the sub-modules and registers everything.
Stripped to the essentials (full hot-reload version in Stage 5):

```python
bl_info = { "name": "tools", "blender": (3, 6, 0), "category": "Mesh", ... }

from . import utils, ops, keymaps, preferences, ui
import bpy

def register():
    for cls in ops.classes:                 # operators first…
        bpy.utils.register_class(cls)
    for cls in ui.classes:                   # …then panels (they reference ops)
        bpy.utils.register_class(cls)
    bpy.utils.register_class(preferences.toolsPreferences)
    keymaps.register()

def unregister():
    keymaps.unregister()                     # reverse order on the way out
    bpy.utils.unregister_class(preferences.toolsPreferences)
    for cls in reversed(ui.classes):
        bpy.utils.unregister_class(cls)
    for cls in reversed(ops.classes):
        bpy.utils.unregister_class(cls)
```

**Order matters:** register operators *before* panels (a panel's button references
an operator that must already exist), and unregister in the reverse order.

### Adding a brand-new tool to this layout

This is the everyday workflow once the scaffolding exists:

1. Create `ops/my_tool.py` with a `tools_OT_my_tool` operator and a
   `classes = [tools_OT_my_tool]` line.
2. In `ops/__init__.py`, add `my_tool` to the import line and to `_collect_classes()`.
3. In `ui.py`, add `col.operator("tools.my_tool", text="My Tool", icon='...')`.
4. (Optional) add a shortcut in `keymaps.py` (Stage 6).
5. Click **Reload Scripts**. Done — no restart.

---

## 8. Stage 5 — Hot-reload support

Here's the subtlety: Python caches imported modules. When `script.reload()` runs,
Blender re-imports your *package*, but Python may hand back the **cached** versions
of your sub-modules (`ops/target_weld.py`, etc.) — so your edits to those files
appear to do nothing.

The fix is the standard Blender hot-reload idiom: at the top of each package
`__init__.py`, detect "am I being re-run?" and force-reload sub-modules with
`importlib.reload()`. The tell-tale is whether `bpy` is already in `locals()` —
on a fresh import it isn't; on a reload it is.

**The package `__init__.py`** (full version, from `tools/__init__.py`):

```python
bl_info = { "name": "tools", "blender": (3, 6, 0), "category": "Mesh", ... }

# Hot-reload: reload submodules bottom-up (utils first, then ops, then UI).
if "bpy" in locals():
    import importlib
    from .utils import mesh as _mesh
    importlib.reload(_mesh)
    utils = importlib.reload(utils)
    ops = importlib.reload(ops)
    ops.reload()                  # ask ops/ to refresh its own submodules + classes
    keymaps = importlib.reload(keymaps)
    preferences = importlib.reload(preferences)
    ui = importlib.reload(ui)
else:
    from . import utils, ops, keymaps, preferences, ui

import bpy

def register(): ...      # as in Stage 4
def unregister(): ...
```

**`ops/__init__.py`** needs the same trick, plus a `reload()` helper the parent
calls so the `classes` list is rebuilt from the freshly-reloaded modules:

```python
if "bpy" in locals():
    import importlib
    reload_scripts = importlib.reload(reload_scripts)
    target_weld = importlib.reload(target_weld)
    boundary_select = importlib.reload(boundary_select)
else:
    from . import reload_scripts, target_weld, boundary_select


def _collect_classes():
    return [*reload_scripts.classes, *target_weld.classes, *boundary_select.classes]

classes = _collect_classes()


def reload():
    """Called by the parent package to refresh the classes list after reload."""
    global classes, reload_scripts, target_weld, boundary_select
    import importlib
    reload_scripts = importlib.reload(reload_scripts)
    target_weld = importlib.reload(target_weld)
    boundary_select = importlib.reload(boundary_select)
    classes = _collect_classes()
```

**`utils/__init__.py`** follows the same shape:

```python
if "bpy" in locals():
    import importlib
    mesh = importlib.reload(mesh)
else:
    from . import mesh

from .mesh import get_nearest_vertex, get_nearest_edge
```

**Reload order is bottom-up.** Reload `utils` before `ops` before `ui`, because a
freshly-reloaded `ops` module must import the *new* `utils`, and `ui` references
the *new* `ops`. Get this backwards and you'll have modules holding references to
stale code.

With this in place, the **Reload Scripts** button truly hot-swaps your whole
add-on from disk in one click.

---

## 9. Stage 6 — Keyboard shortcuts (keymaps)

Buttons are nice; power users want hotkeys. Blender keymaps map a key combo (in a
given context, like "Mesh Edit Mode") to an operator.

tools centralizes every shortcut in one declarative list so they're easy to see
and edit. This is `tools/keymaps.py`:

```python
import bpy

addon_keymaps = []   # keep references so we can remove them on unregister

# Central registry — every shortcut is one dict here.
KEYMAP_ENTRIES = [
    {
        "idname": "tools.target_weld",
        "label": "Target Weld",
        "category": "Mesh",          # used to group them in preferences
        "key": "T",
        "shift": True,               # Shift+T
        "km_name": "Mesh",           # the keymap context (active in Mesh Edit Mode)
        "space_type": "EMPTY",
    },
    {
        "idname": "tools.select_boundary_edges",
        "label": "Select Boundary Edges",
        "category": "Mesh",
        "key": "TWO",                # the '2' key
        "ctrl": True,                # Ctrl+2
        "km_name": "Mesh",
        "space_type": "EMPTY",
    },
]


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon          # the add-on keyconfig (not the user one)
    if kc is None:
        return                        # e.g. headless/background Blender
    for entry in KEYMAP_ENTRIES:
        km = kc.keymaps.new(name=entry["km_name"], space_type=entry["space_type"])
        kmi = km.keymap_items.new(
            entry["idname"],
            type=entry["key"],
            value="PRESS",
            shift=entry.get("shift", False),
            ctrl=entry.get("ctrl", False),
            alt=entry.get("alt", False),
        )
        addon_keymaps.append((km, kmi, entry))


def unregister():
    for km, kmi, _entry in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
```

Then the package's `register()`/`unregister()` call `keymaps.register()` /
`keymaps.unregister()` (see Stage 4).

**Things to know:**

- `key` uses Blender's event type names: letters are `"A"`–`"Z"`; the number-row
  digits are spelled out — `"ONE"`, `"TWO"`, … `"ZERO"`; also `"LEFTMOUSE"`,
  `"SPACE"`, `"X"`, etc.
- `km_name` is the **context**. `"Mesh"` = active only in Mesh Edit Mode;
  `"Object Mode"`, `"3D View"`, `"UV Editor"`, `"Window"` (global), etc.
- Register on `keyconfigs.addon` so you don't clobber the user's config; always
  remove them on unregister or they pile up across reloads.
- Two small helpers (`get_categories()`, `get_entries_by_category()`) let the
  preferences UI list shortcuts grouped by `category` — see next stage.

---

## 10. Stage 7 — Add-on preferences

Add-on preferences are the settings panel shown under your add-on in
`Edit > Preferences > Add-ons`. tools uses it to let users **see and rebind**
the keyboard shortcuts. Subclass `bpy.types.AddonPreferences` with
`bl_idname = __package__` (the package name, `"tools"`):

```python
import bpy
from . import keymaps


class toolsPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__   # must equal the add-on's package/folder name

    def draw(self, context):
        layout = self.layout
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.user        # the *user* keyconfig holds editable bindings

        for category in keymaps.get_categories():
            box = layout.box()
            box.label(text=f"{category} Shortcuts", icon='KEYINGSET')

            for entry in keymaps.get_entries_by_category(category):
                kmi = self._find_user_kmi(kc, entry)
                if kmi is None:
                    row = box.row()
                    row.label(text=entry["label"], icon='ERROR')
                    row.label(text="(shortcut not found)")
                    continue

                row = box.row(align=True)
                row.prop(kmi, "active", text="", emboss=False)        # on/off toggle
                row.label(text=entry["label"])
                row.prop(kmi, "type", text="", full_event=True)       # the key picker

    def _find_user_kmi(self, kc, entry):
        """Find the user-editable keymap item for a given entry."""
        km = kc.keymaps.get(entry["km_name"])
        if km is None:
            return None
        for kmi in km.keymap_items:
            if kmi.idname == entry["idname"]:
                return kmi
        return None
```

Register it like any other class (tools does
`bpy.utils.register_class(preferences.toolsPreferences)` inside `register()`).

**Why `keyconfigs.user` here but `keyconfigs.addon` in `keymaps.py`?** You
*create* bindings in the add-on keyconfig (so they ship with your add-on), but the
*editable* copy the user sees and rebinds lives in the user keyconfig. The
`full_event=True` prop draws Blender's native "press a key" widget — clicking it
lets the user rebind live.

---

## 11. Installing & testing

**During development (recommended):**

1. Symlink or copy `tools/` into Blender's `scripts/addons/` (see Stage 3 tip).
2. `Edit > Preferences > Add-ons`, search "tools", tick the checkbox to enable.
3. Press **N** in the viewport → **tools** tab.
4. Edit code → click **Reload Scripts** → see changes immediately.

**Packaging for others:**

1. Zip the `tools/` **folder** (the zip should contain `tools/__init__.py`, not
   the files at the zip root).
2. They install via `Preferences > Add-ons > Install…` and pick the zip.

**Quick sanity checks in the Python console** (the *Scripting* workspace):

```python
# Is my operator registered?
bpy.ops.tools.reload_scripts()      # should run, no AttributeError

# What's the exact idname of something I clicked? Hover the button → it shows in
# the tooltip, or right-click → "Copy Python Command".
```

Always keep the **system console** open while developing
(`Window > Toggle System Console`) — Python errors and your `print()`s go there.

---

## 12. Troubleshooting

| Symptom                                              | Likely cause / fix                                                                                   |
|------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| Panel tab doesn't appear                             | Add-on not enabled, or `register()` raised. Check the system console for a traceback.                |
| `RuntimeError: register_class(...): already registered` | A class was registered twice. Make sure `unregister()` ran (it does on reload) and you don't list a class twice. |
| Button is greyed out                                 | `poll()` returned `False` for the current context (e.g. not in Edit Mode). Working as intended.      |
| Edits to a sub-module don't take effect on reload    | Missing the `importlib.reload()` hot-reload block (Stage 5), or files aren't where Blender imports from (symlink). |
| `bl_idname` error on register                        | Operator idnames need exactly one `.` and lowercase; panels' `bl_idname` must be unique.             |
| Shortcut does nothing                                | Wrong `km_name` context (e.g. bound to `"Mesh"` but you're in Object Mode), or `keyconfigs.addon` was `None` at register. |
| Changes lost after restart                           | You ran it from the Text Editor only. Install it as an add-on to persist.                            |

---

## 13. Quick reference cheat-sheet

**Minimal operator:**
```python
class tools_OT_x(bpy.types.Operator):
    bl_idname = "tools.x"; bl_label = "X"; bl_options = {'REGISTER', 'UNDO'}
    @classmethod
    def poll(cls, context): return context.object is not None
    def execute(self, context):
        ...                          # your script
        return {'FINISHED'}
```

**Minimal panel:**
```python
class VIEW3D_PT_x(bpy.types.Panel):
    bl_label = "X"; bl_idname = "VIEW3D_PT_x"
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = "tools"
    def draw(self, context):
        self.layout.operator("tools.x", text="Run X", icon='PLAY')
```

**Register block:**
```python
classes = [tools_OT_x, VIEW3D_PT_x]
def register():
    for c in classes: bpy.utils.register_class(c)
def unregister():
    for c in reversed(classes): bpy.utils.unregister_class(c)
```

**Reload-scripts operator:**
```python
def execute(self, context):
    bpy.ops.script.reload(); return {'FINISHED'}
```

**Hot-reload header (top of every package `__init__.py`):**
```python
if "bpy" in locals():
    import importlib
    submod = importlib.reload(submod)
else:
    from . import submod
```

**bmesh edit-mode read/write skeleton:**
```python
bm = bmesh.from_edit_mesh(obj.data)
bm.verts.ensure_lookup_table()       # needed before indexing bm.verts[i]
# ...mutate verts/edges/faces...
bm.select_flush_mode()
bmesh.update_edit_mesh(obj.data)     # push changes back
```

| Common `bl_*` value | Meaning                          |
|---------------------|----------------------------------|
| `bl_space_type='VIEW_3D'` | 3D Viewport                 |
| `bl_region_type='UI'`     | The N-sidebar               |
| `bl_category='tools'`    | Sidebar tab name            |
| `bl_parent_id='...'`      | Nest as a sub-panel         |
| `bl_options={'REGISTER','UNDO'}` | Show in redo panel; undoable |

---

### Where to go next

- **`bpy.ops` documentation** — every built-in operator you can call from
  `execute()`.
- **`bmesh` module** — the right tool for non-trivial mesh editing (see
  `tools/utils/mesh.py` and `ops/target_weld.py` for modal + GPU-drawing examples).
- **Modal operators** — for click-drag interactive tools (`target_weld.py` is a
  full worked example: mouse tracking, viewport overlay drawing, Esc to cancel).

You now have everything needed to build the tools panel from an empty file.
```
