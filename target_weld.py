bl_info = {
    "name": "Target Weld",
    "author": "Marc",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "Mesh > Vertex > Target Weld (or Shift+W in Edit Mode)",
    "description": "Weld a vertex to another vertex by clicking on them sequentially, 3ds Max style",
    "category": "Mesh",
}

import bpy
import bmesh
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from bpy_extras.view3d_utils import (
    region_2d_to_location_3d,
    location_3d_to_region_2d,
)


def get_nearest_vertex(context, mouse_pos, threshold=20):
    """Find the nearest vertex to the mouse position in screen space."""
    obj = context.edit_object
    me = obj.data
    bm = bmesh.from_edit_mesh(me)
    bm.verts.ensure_lookup_table()

    region = context.region
    rv3d = context.region_data
    world_mat = obj.matrix_world

    best_vert = None
    best_dist = threshold

    for v in bm.verts:
        if v.hide:
            continue
        co_world = world_mat @ v.co
        co_2d = location_3d_to_region_2d(region, rv3d, co_world)
        if co_2d is None:
            continue
        dist = (Vector(mouse_pos) - co_2d).length
        if dist < best_dist:
            best_dist = dist
            best_vert = v.index

    return best_vert


class TARGET_WELD_OT_operator(bpy.types.Operator):
    """Target Weld: click a source vertex, then click a destination vertex to weld"""
    bl_idname = "mesh.target_weld"
    bl_label = "Target Weld"
    bl_options = {'REGISTER', 'UNDO'}

    # ---- state ----
    _first_vert_index: int = -1
    _first_vert_co_world: Vector = None
    _mouse_pos: tuple = (0, 0)
    _draw_handler = None

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
        )

    # ---- drawing ----

    def _draw_callback(self, context):
        if self._first_vert_co_world is None:
            return

        region = context.region
        rv3d = context.region_data

        start_2d = location_3d_to_region_2d(region, rv3d, self._first_vert_co_world)
        if start_2d is None:
            return

        end_2d = Vector(self._mouse_pos)

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(2.0)

        coords = [start_2d, end_2d]
        batch = batch_for_shader(shader, 'LINES', {"pos": coords})
        shader.bind()
        shader.uniform_float("color", (1.0, 0.6, 0.0, 0.9))
        batch.draw(shader)

        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

    def _add_draw_handler(self, context):
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_callback, (context,), 'WINDOW', 'POST_PIXEL'
        )

    def _remove_draw_handler(self):
        if self._draw_handler is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_handler, 'WINDOW')
            self._draw_handler = None

    # ---- modal logic ----

    def invoke(self, context, event):
        if not self.poll(context):
            self.report({'WARNING'}, "Must be in Edit Mode on a mesh")
            return {'CANCELLED'}

        self._first_vert_index = -1
        self._first_vert_co_world = None
        self._mouse_pos = (event.mouse_region_x, event.mouse_region_y)

        self._add_draw_handler(context)
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Target Weld: click source vertex")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        context.area.tag_redraw()

        if event.type == 'MOUSEMOVE':
            self._mouse_pos = (event.mouse_region_x, event.mouse_region_y)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            mouse = (event.mouse_region_x, event.mouse_region_y)
            vert_idx = get_nearest_vertex(context, mouse)

            if vert_idx is None:
                self.report({'WARNING'}, "No vertex under cursor")
                return {'RUNNING_MODAL'}

            if self._first_vert_index == -1:
                # --- first pick ---
                obj = context.edit_object
                bm = bmesh.from_edit_mesh(obj.data)
                bm.verts.ensure_lookup_table()
                self._first_vert_index = vert_idx
                self._first_vert_co_world = obj.matrix_world @ bm.verts[vert_idx].co
                context.area.header_text_set("Target Weld: click destination vertex")
                return {'RUNNING_MODAL'}
            else:
                # --- second pick ---
                if vert_idx == self._first_vert_index:
                    self.report({'WARNING'}, "Same vertex selected, pick a different one")
                    return {'RUNNING_MODAL'}

                self._weld(context, self._first_vert_index, vert_idx)
                self._finish(context)
                return {'FINISHED'}

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._finish(context)
            self.report({'INFO'}, "Target Weld cancelled")
            return {'CANCELLED'}

        return {'PASS_THROUGH'}

    # ---- weld ----

    def _weld(self, context, src_idx, dst_idx):
        obj = context.edit_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        bm.verts.ensure_lookup_table()

        src = bm.verts[src_idx]
        dst = bm.verts[dst_idx]

        # Move source to destination position then merge
        src.co = dst.co.copy()

        # Merge by distance at that position with a tiny threshold
        bmesh.ops.remove_doubles(bm, verts=[src, dst], dist=0.0001)

        bmesh.update_edit_mesh(me)

    # ---- cleanup ----

    def _finish(self, context):
        self._remove_draw_handler()
        context.area.header_text_set(None)


# ---- menu & keymap ----

def menu_func(self, context):
    self.layout.operator(TARGET_WELD_OT_operator.bl_idname, text="Target Weld")


addon_keymaps = []


def register():
    bpy.utils.register_class(TARGET_WELD_OT_operator)
    bpy.types.VIEW3D_MT_edit_mesh_vertices.append(menu_func)

    wm = bpy.context.window_manager
    if wm.keyconfigs.addon:
        km = wm.keyconfigs.addon.keymaps.new(name='Mesh', space_type='EMPTY')
        kmi = km.keymap_items.new(
            TARGET_WELD_OT_operator.bl_idname,
            type='T',
            value='PRESS',
            shift=True,
        )
        addon_keymaps.append((km, kmi))


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    bpy.types.VIEW3D_MT_edit_mesh_vertices.remove(menu_func)
    bpy.utils.unregister_class(TARGET_WELD_OT_operator)


if __name__ == "__main__":
    register()
