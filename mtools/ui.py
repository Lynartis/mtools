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
        # Add new mesh tool buttons here


classes = [
    VIEW3D_PT_mtools_main,
    VIEW3D_PT_mtools_mesh,
]
