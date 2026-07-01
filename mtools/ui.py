import bpy


class VIEW3D_PT_mtools_main(bpy.types.Panel):
    bl_label = "MTools"
    bl_idname = "VIEW3D_PT_mtools_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MTools"

    def draw(self, context):
        layout = self.layout
        layout.operator("mtools.reload_scripts", text="Reload Scripts", icon='FILE_REFRESH')


class VIEW3D_PT_mtools_mesh(bpy.types.Panel):
    bl_label = "Mesh Tools"
    bl_idname = "VIEW3D_PT_mtools_mesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MTools"
    bl_parent_id = "VIEW3D_PT_mtools_main"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("mtools.target_weld", text="Target Weld", icon='VERTEXSEL')
        col.separator()
        col.operator("mtools.select_boundary_vertices", text="Boundary Verts", icon='VERTEXSEL')
        col.operator("mtools.select_boundary_edges", text="Boundary Edges", icon='EDGESEL')


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


class VIEW3D_PT_mtools_export_rig(bpy.types.Panel):
    """Export the picked armature + its skinned meshes as an FBX (rig + model)."""
    bl_label = "Export Rig + Model"
    bl_idname = "VIEW3D_PT_mtools_export_rig"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MTools"
    bl_parent_id = "VIEW3D_PT_mtools_export"

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


class VIEW3D_PT_mtools_export_anim_xml(bpy.types.Panel):
    """Export the picked armature + take to an XML .anim file."""
    bl_label = "Animation"
    bl_idname = "VIEW3D_PT_mtools_export_anim_xml"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MTools"
    bl_parent_id = "VIEW3D_PT_mtools_export"

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


class VIEW3D_PT_mtools_extra_options(bpy.types.Panel):
    """Shared options for both exporters (coordinate system, loop mode)."""
    bl_label = "Extra Options"
    bl_idname = "VIEW3D_PT_mtools_extra_options"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MTools"
    bl_parent_id = "VIEW3D_PT_mtools_export"

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


classes = [
    VIEW3D_PT_mtools_main,
    VIEW3D_PT_mtools_mesh,
    VIEW3D_PT_mtools_export,
    VIEW3D_PT_mtools_export_rig,
    VIEW3D_PT_mtools_export_anim_xml,
    VIEW3D_PT_mtools_extra_options,
]
