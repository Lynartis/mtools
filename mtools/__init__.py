bl_info = {
    "name": "MTools",
    "author": "Marc",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > MTools",
    "description": "Collection of mesh tools for Blender (Target Weld, and more)",
    "category": "Mesh",
}

# Hot-reload support: reload submodules bottom-up (utils first, then ops, etc.)
if "bpy" in locals():
    import importlib
    from .utils import mesh as _mesh
    importlib.reload(_mesh)
    utils = importlib.reload(utils)
    ops = importlib.reload(ops)
    ops.reload()  # refresh classes list after submodule reload
    keymaps = importlib.reload(keymaps)
    preferences = importlib.reload(preferences)
    ui = importlib.reload(ui)
else:
    from . import utils, ops, keymaps, preferences, ui

import bpy


def register():
    for cls in ops.classes:
        bpy.utils.register_class(cls)
    for cls in ui.classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_class(preferences.MToolsPreferences)
    keymaps.register()


def unregister():
    keymaps.unregister()
    bpy.utils.unregister_class(preferences.MToolsPreferences)
    for cls in reversed(ui.classes):
        bpy.utils.unregister_class(cls)
    for cls in reversed(ops.classes):
        bpy.utils.unregister_class(cls)
