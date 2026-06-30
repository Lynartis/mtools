import os
import bpy


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


class MTOOLS_OT_set_fbx_export_path(bpy.types.Operator):
    """Choose and remember the FBX export location for this .blend file"""
    bl_idname = "mtools.set_fbx_export_path"
    bl_label = "Set FBX Export Path"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.fbx", options={'HIDDEN'})

    def invoke(self, context, event):
        stored = context.scene.mtools_fbx_export_path
        self.filepath = stored if stored else _default_filepath()
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        context.scene.mtools_fbx_export_path = _ensure_fbx_ext(self.filepath)
        self.report({'INFO'}, f"FBX export path set: {context.scene.mtools_fbx_export_path}")
        return {'FINISHED'}


class MTOOLS_OT_export_fbx_selected(bpy.types.Operator):
    """Export the selected objects as FBX to this .blend file's saved path.
    The first export asks where to save and remembers it for next time."""
    bl_idname = "mtools.export_fbx_selected"
    bl_label = "Export Selected FBX"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.fbx", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def invoke(self, context, event):
        stored = context.scene.mtools_fbx_export_path
        if stored:
            # Path already set for this .blend -- export straight away
            self.filepath = stored
            return self.execute(context)
        # No remembered path yet -- prompt for one, then export
        self.filepath = _default_filepath()
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath:
            self.report({'WARNING'}, "No export path set")
            return {'CANCELLED'}

        path = _ensure_fbx_ext(self.filepath)
        context.scene.mtools_fbx_export_path = path  # remember for next time

        if not hasattr(bpy.ops.export_scene, "fbx"):
            self.report({'ERROR'}, "FBX exporter add-on is not enabled")
            return {'CANCELLED'}

        try:
            bpy.ops.export_scene.fbx(filepath=path, use_selection=True)
        except RuntimeError as exc:
            self.report({'ERROR'}, f"FBX export failed: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Exported selection to {path}")
        return {'FINISHED'}


classes = [
    MTOOLS_OT_set_fbx_export_path,
    MTOOLS_OT_export_fbx_selected,
]


def register_props():
    bpy.types.Scene.mtools_fbx_export_path = bpy.props.StringProperty(
        name="FBX Export Path",
        description="Where 'Export Selected FBX' saves geometry for this .blend file",
        subtype='FILE_PATH',
        default="",
    )


def unregister_props():
    if hasattr(bpy.types.Scene, "mtools_fbx_export_path"):
        del bpy.types.Scene.mtools_fbx_export_path
