import bpy


class MTOOLS_OT_reload_scripts(bpy.types.Operator):
    bl_idname = "mtools.reload_scripts"
    bl_label = "Reload Scripts"
    bl_description = "Reload all Blender scripts (including MTools) to pick up external changes"

    def execute(self, context):
        bpy.ops.script.reload()
        self.report({'INFO'}, "All scripts reloaded")
        return {'FINISHED'}


classes = [MTOOLS_OT_reload_scripts]
