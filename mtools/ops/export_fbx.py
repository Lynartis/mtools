"""export_fbx.py - operators for the 'Export Rig + Model' panel."""

import os

import bpy

from ..core import fbx_export, coordinates


def _resolve_fbx_remap(scene):
    """The remap string chosen in the FBX coordinate selector."""
    return coordinates.resolve_remap(scene.mtools_fbx_coord, scene.mtools_fbx_coord_custom)


class MTOOLS_OT_set_fbx_path(bpy.types.Operator):
    """Pick the folder to export the FBX into."""
    bl_idname = "mtools.set_fbx_path"
    bl_label = "Choose FBX Folder"
    bl_options = {"REGISTER"}

    # A directory-only file browser (no filename field).
    directory: bpy.props.StringProperty(subtype="DIR_PATH")

    def execute(self, context):
        context.scene.mtools_fbx_path = self.directory
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)     # opens the browser
        return {"RUNNING_MODAL"}


class MTOOLS_OT_export_rig(bpy.types.Operator):
    """Export the picked armature + its skinned meshes as an FBX (rig + model)."""
    bl_idname = "mtools.export_rig"
    bl_label = "Export Rig + Model"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        # The button is only clickable once an armature has been picked.
        return context.scene.mtools_fbx_armature is not None

    def execute(self, context):
        scene = context.scene
        armature = scene.mtools_fbx_armature

        if armature is None or armature.type != "ARMATURE":
            self.report({"ERROR"}, "Pick an armature to export")
            return {"CANCELLED"}

        # Build the output path: <folder>/<name>.fbx
        name = (scene.mtools_fbx_name or armature.name).strip()
        folder = bpy.path.abspath(scene.mtools_fbx_path or "//")
        if not folder:
            self.report({"ERROR"}, "Set an export folder")
            return {"CANCELLED"}
        os.makedirs(folder, exist_ok=True)
        filepath = os.path.join(folder, name + ".fbx")

        remap = _resolve_fbx_remap(scene)
        try:
            coordinates.validate_remap(remap)                       # catch typos early
            meshes = fbx_export.export_rig(context, armature, filepath, remap)
        except Exception as error:
            self.report({"ERROR"}, "FBX export failed: %s" % error)
            return {"CANCELLED"}

        self.report({"INFO"}, "Exported '%s' + %d mesh(es) -> %s"
                    % (armature.name, len(meshes), filepath))
        return {"FINISHED"}


classes = [MTOOLS_OT_set_fbx_path, MTOOLS_OT_export_rig]
