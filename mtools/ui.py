"""
ui.py - the MTools sidebar panels (View3D > N-panel > 'MTools' tab).

Panels only lay out existing Scene properties and operator buttons; they hold
no logic. Each panel is a bpy.types.Panel; children attach via bl_parent_id.
"""

import bpy


class VIEW3D_PT_mtools_main(bpy.types.Panel):
    """Root MTools panel - holds the Reload Scripts button and child panels."""
    bl_label = "MTools"
    bl_idname = "VIEW3D_PT_mtools_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MTools"

    def draw(self, context):
        # Dev helper: reload all add-on code after editing the source files.
        self.layout.operator("mtools.reload_scripts", icon="FILE_REFRESH")


class VIEW3D_PT_mtools_export_rig(bpy.types.Panel):
    """Export the armature + skinned meshes as an FBX (rig + model)."""
    bl_label = "Export Rig + Model"
    bl_idname = "VIEW3D_PT_mtools_export_rig"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MTools"
    bl_parent_id = "VIEW3D_PT_mtools_main"

    def draw(self, context):
        scene = context.scene
        col = self.layout.column(align=True)
        col.prop(scene, "mtools_fbx_name", text="Name")

        row = col.row(align=True)                     # path field + folder browser
        row.prop(scene, "mtools_fbx_path", text="")
        row.operator("mtools.set_fbx_path", text="", icon="FILEBROWSER")

        col.prop(scene, "mtools_fbx_armature", text="Armature")
        col.separator()
        col.operator("mtools.export_rig", icon="EXPORT")


class VIEW3D_PT_mtools_export_anim(bpy.types.Panel):
    """Export the picked action as a custom .anim XML file."""
    bl_label = "Export Animation"
    bl_idname = "VIEW3D_PT_mtools_export_anim"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MTools"
    bl_parent_id = "VIEW3D_PT_mtools_main"

    def draw(self, context):
        scene = context.scene
        col = self.layout.column(align=True)
        col.prop(scene, "mtools_anim_armature", text="Armature")
        col.prop(scene, "mtools_anim_action", text="Action")
        col.prop(scene, "mtools_anim_name", text="Clip Name")

        row = col.row(align=True)
        row.prop(scene, "mtools_anim_path", text="")
        row.operator("mtools.set_anim_path", text="", icon="FILEBROWSER")

        col.separator()
        col.operator("mtools.export_anim", icon="EXPORT")


class VIEW3D_PT_mtools_extra(bpy.types.Panel):
    """Extra options: coordinate systems (for debugging) and loop mode."""
    bl_label = "Extra Options"
    bl_idname = "VIEW3D_PT_mtools_extra"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MTools"
    bl_parent_id = "VIEW3D_PT_mtools_main"
    bl_options = {"DEFAULT_CLOSED"}                   # collapsed by default

    def draw(self, context):
        scene = context.scene
        layout = self.layout

        # FBX coordinate system (+ custom remap when CUSTOM is chosen).
        # A plain dropdown keeps the option names readable in the narrow sidebar
        # (expand=True would squeeze them into unreadable icon-width buttons).
        box = layout.box()
        box.label(text="FBX Coordinate System")
        box.prop(scene, "mtools_fbx_coord", text="")
        if scene.mtools_fbx_coord == "CUSTOM":
            box.prop(scene, "mtools_fbx_coord_custom", text="Remap")

        # Animation coordinate system (independent of the FBX one).
        box = layout.box()
        box.label(text="Anim Coordinate System")
        box.prop(scene, "mtools_anim_coord", text="")
        if scene.mtools_anim_coord == "CUSTOM":
            box.prop(scene, "mtools_anim_coord_custom", text="Remap")

        layout.prop(scene, "mtools_anim_loop", text="Loop Mode")


classes = [
    VIEW3D_PT_mtools_main,
    VIEW3D_PT_mtools_export_rig,
    VIEW3D_PT_mtools_export_anim,
    VIEW3D_PT_mtools_extra,
]
