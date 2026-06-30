import bpy
import bmesh


class MTOOLS_OT_select_boundary_vertices(bpy.types.Operator):
    """From face selection, select the boundary vertices of the selected faces"""
    bl_idname = "mtools.select_boundary_vertices"
    bl_label = "Select Boundary Vertices"
    bl_description = "Convert face selection to boundary vertex selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
        )

    def execute(self, context):
        obj = context.edit_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)

        # Collect boundary edges: edges with exactly one selected face
        boundary_edges = []
        for edge in bm.edges:
            sel_face_count = sum(1 for f in edge.link_faces if f.select)
            if sel_face_count == 1:
                boundary_edges.append(edge)

        if not boundary_edges:
            self.report({'WARNING'}, "No boundary found from face selection")
            return {'CANCELLED'}

        # Clear current selection
        for v in bm.verts:
            v.select = False
        for e in bm.edges:
            e.select = False
        for f in bm.faces:
            f.select = False

        # Select boundary vertices
        for edge in boundary_edges:
            for vert in edge.verts:
                vert.select = True

        bm.select_flush_mode()
        bmesh.update_edit_mesh(me)

        # Switch to vertex select mode
        bpy.ops.mesh.select_mode(type='VERT')

        self.report({'INFO'}, f"Selected {sum(1 for v in bm.verts if v.select)} boundary vertices")
        return {'FINISHED'}


class MTOOLS_OT_select_boundary_edges(bpy.types.Operator):
    """From face selection, select the boundary edges of the selected faces"""
    bl_idname = "mtools.select_boundary_edges"
    bl_label = "Select Boundary Edges"
    bl_description = "Convert face selection to boundary edge selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
        )

    def execute(self, context):
        obj = context.edit_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)

        # Collect boundary edges: edges with exactly one selected face
        boundary_edges = []
        for edge in bm.edges:
            sel_face_count = sum(1 for f in edge.link_faces if f.select)
            if sel_face_count == 1:
                boundary_edges.append(edge)

        if not boundary_edges:
            self.report({'WARNING'}, "No boundary found from face selection")
            return {'CANCELLED'}

        # Clear current selection
        for v in bm.verts:
            v.select = False
        for e in bm.edges:
            e.select = False
        for f in bm.faces:
            f.select = False

        # Select boundary edges
        for edge in boundary_edges:
            edge.select = True

        bm.select_flush_mode()
        bmesh.update_edit_mesh(me)

        # Switch to edge select mode
        bpy.ops.mesh.select_mode(type='EDGE')

        self.report({'INFO'}, f"Selected {len(boundary_edges)} boundary edges")
        return {'FINISHED'}


classes = [
    MTOOLS_OT_select_boundary_vertices,
    MTOOLS_OT_select_boundary_edges,
]
