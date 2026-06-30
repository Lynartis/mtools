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
    bl_label = "Export"
    bl_idname = "VIEW3D_PT_mtools_export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MTools"
    bl_parent_id = "VIEW3D_PT_mtools_main"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)

        row = col.row(align=True)
        row.prop(context.scene, "mtools_fbx_export_path", text="")
        row.operator("mtools.set_fbx_export_path", text="", icon='FILEBROWSER')

        col.separator()
        col.operator("mtools.export_fbx_selected", text="Export Selected FBX", icon='EXPORT')


class VIEW3D_PT_mtools_export_anim(bpy.types.Panel):
    bl_label = "Animation"
    bl_idname = "VIEW3D_PT_mtools_export_anim"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MTools"
    bl_parent_id = "VIEW3D_PT_mtools_export"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        col = layout.column(align=True)
        col.prop(scene, "mtools_anim_armature", text="Armature")
        col.prop(scene, "mtools_anim_action", text="Take")

        col.separator()
        col.prop(scene, "mtools_anim_mode", expand=True)

        col.separator()
        col.prop(scene, "mtools_anim_coord", expand=True)
        if scene.mtools_anim_coord == 'CUSTOM':
            col.prop(scene, "mtools_anim_coord_remap", text="")

        col.separator()
        col.prop(scene, "mtools_anim_rotation", expand=True)

        col.separator()
        row = col.row(align=True)
        row.prop(scene, "mtools_anim_export_path", text="")
        row.operator("mtools.set_anim_export_path", text="", icon='FILEBROWSER')

        col.separator()
        col.operator("mtools.export_animation", text="Export Animation", icon='ARMATURE_DATA')


classes = [
    VIEW3D_PT_mtools_main,
    VIEW3D_PT_mtools_mesh,
    VIEW3D_PT_mtools_export,
    VIEW3D_PT_mtools_export_anim,
]
