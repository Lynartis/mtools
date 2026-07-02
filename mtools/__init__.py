"""
MTools - a Blender 4.2+ extension that exports a rig + model as FBX and bone
animation as a custom .anim XML file for a custom game engine.

Package layout:
    core/   - pure logic (coordinates, curve reading, XML, FBX export)
    ops/    - operators + all Scene properties
    ui.py   - the sidebar panels

Reload Scripts (bpy.ops.script.reload) re-runs this file. The
`if "bpy" in locals()` guard below hot-reloads the submodules bottom-up so
code edits show up without restarting Blender.
"""

if "bpy" in locals():
    import importlib
    core = importlib.reload(core)         # reload logic first...
    ops = importlib.reload(ops)           # ...then operators (they import core)
    ops.reload()                          # rebuild the operator class list
    ui = importlib.reload(ui)             # ...then the panels
else:
    from . import core, ops, ui

import bpy


def register():
    # 1) operators, 2) their Scene properties, 3) the UI panels.
    for cls in ops.classes:
        bpy.utils.register_class(cls)
    ops.register_props()
    for cls in ui.classes:
        bpy.utils.register_class(cls)


def unregister():
    # Exact reverse of register().
    for cls in reversed(ui.classes):
        bpy.utils.unregister_class(cls)
    ops.unregister_props()
    for cls in reversed(ops.classes):
        bpy.utils.unregister_class(cls)
