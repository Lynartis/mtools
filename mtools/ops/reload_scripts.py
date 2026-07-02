"""reload_scripts.py - one button to hot-reload all add-on code during dev."""

import bpy


class MTOOLS_OT_reload_scripts(bpy.types.Operator):
    """Reload all Blender scripts (including MTools) to pick up code changes."""
    bl_idname = "mtools.reload_scripts"
    bl_label = "Reload Scripts"
    bl_options = {"REGISTER"}

    def execute(self, context):
        # Blender's built-in reload re-imports every add-on, which triggers our
        # `if "bpy" in locals()` hot-reload blocks in each package __init__.
        bpy.ops.script.reload()
        self.report({"INFO"}, "Scripts reloaded")
        return {"FINISHED"}


classes = [MTOOLS_OT_reload_scripts]
