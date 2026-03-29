import bpy
import bmesh
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from bpy_extras.view3d_utils import location_3d_to_region_2d

from ..utils.mesh import get_nearest_vertex, get_nearest_edge

# Colors
COL_LINE = (1.0, 0.6, 0.0, 0.9)   # orange guide line
COL_HIGHLIGHT = (1.0, 0.1, 0.1, 0.9)  # red hover / midpoint dot
COL_EDGE_HL = (1.0, 0.1, 0.1, 0.9)    # red edge highlight


def _edge_select_mode(context):
    """Return True if the user is in edge select mode."""
    ts = context.tool_settings
    return ts.mesh_select_mode[1]  # (vert, edge, face)


def _match_edge_verts(bm, src_edge_idx, dst_edge_idx):
    """Determine vertex pairing between two edges.

    Returns a list of (src_vert, dst_vert) pairs and the set of shared verts.

    Cases:
      - 4 unique verts: pair by shortest total distance (avoid crossing)
      - 3 unique verts (shared vertex): pair the two non-shared verts
      - same edge or identical vert sets: returns empty list
    """
    bm.edges.ensure_lookup_table()
    src_e = bm.edges[src_edge_idx]
    dst_e = bm.edges[dst_edge_idx]

    src_verts = set(src_e.verts)
    dst_verts = set(dst_e.verts)
    shared = src_verts & dst_verts

    if len(shared) == 2:
        # Same edge or degenerate
        return [], shared

    if len(shared) == 1:
        # 3 unique vertices -- pair the non-shared ones
        sv = (src_verts - shared).pop()
        dv = (dst_verts - shared).pop()
        return [(sv, dv)], shared

    # 4 unique vertices -- pick pairing with shortest total distance
    s0, s1 = src_e.verts[0], src_e.verts[1]
    d0, d1 = dst_e.verts[0], dst_e.verts[1]

    dist_a = (s0.co - d0.co).length + (s1.co - d1.co).length  # s0→d0, s1→d1
    dist_b = (s0.co - d1.co).length + (s1.co - d0.co).length  # s0→d1, s1→d0

    if dist_a <= dist_b:
        return [(s0, d0), (s1, d1)], shared
    else:
        return [(s0, d1), (s1, d0)], shared


class MTOOLS_OT_target_weld(bpy.types.Operator):
    """Target Weld: click source then destination to weld (vertex or edge mode)"""
    bl_idname = "mtools.target_weld"
    bl_label = "Target Weld"
    bl_options = {'REGISTER', 'UNDO'}

    # ---- state ----
    _mode: str = 'VERT'  # 'VERT' or 'EDGE'

    # Vertex mode state
    _first_vert_index: int = -1
    _first_vert_co_world: Vector = None

    # Edge mode state
    _first_edge_index: int = -1
    _first_edge_center_world: Vector = None

    # Shared state
    _mouse_pos: tuple = (0, 0)
    _hover_vert_index: int = -1
    _hover_edge_index: int = -1
    _alt_held: bool = False
    _draw_handler = None

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
        )

    # ──────────────────────────── drawing ────────────────────────────

    def _draw_callback(self, context):
        region = context.region
        rv3d = context.region_data
        obj = context.edit_object
        if obj is None:
            return

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')

        if self._mode == 'VERT':
            self._draw_vert_mode(context, shader, region, rv3d, obj)
        else:
            self._draw_edge_mode(context, shader, region, rv3d, obj)

        gpu.state.blend_set('NONE')

    # ---- vertex mode drawing ----

    def _draw_vert_mode(self, context, shader, region, rv3d, obj):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        world = obj.matrix_world

        # Hover dot
        if self._hover_vert_index != -1 and self._hover_vert_index < len(bm.verts):
            hover_2d = location_3d_to_region_2d(region, rv3d, world @ bm.verts[self._hover_vert_index].co)
            if hover_2d:
                self._draw_point(shader, hover_2d, COL_HIGHLIGHT)

        # Guide line + midpoint preview
        if self._first_vert_co_world is not None:
            start_2d = location_3d_to_region_2d(region, rv3d, self._first_vert_co_world)
            if start_2d:
                self._draw_line(shader, start_2d, Vector(self._mouse_pos), COL_LINE)

            if self._alt_held and self._hover_vert_index != -1 and self._hover_vert_index < len(bm.verts):
                hover_world = world @ bm.verts[self._hover_vert_index].co
                mid_2d = location_3d_to_region_2d(region, rv3d, (self._first_vert_co_world + hover_world) / 2.0)
                if mid_2d:
                    self._draw_point(shader, mid_2d, COL_HIGHLIGHT)

    # ---- edge mode drawing ----

    def _draw_edge_mode(self, context, shader, region, rv3d, obj):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        world = obj.matrix_world

        # Highlight hovered edge
        if self._hover_edge_index != -1 and self._hover_edge_index < len(bm.edges):
            e = bm.edges[self._hover_edge_index]
            a_2d = location_3d_to_region_2d(region, rv3d, world @ e.verts[0].co)
            b_2d = location_3d_to_region_2d(region, rv3d, world @ e.verts[1].co)
            if a_2d and b_2d:
                self._draw_line(shader, a_2d, b_2d, COL_EDGE_HL, width=3.0)
                self._draw_point(shader, a_2d, COL_HIGHLIGHT)
                self._draw_point(shader, b_2d, COL_HIGHLIGHT)

        # Guide line from first edge center to cursor
        if self._first_edge_center_world is not None:
            start_2d = location_3d_to_region_2d(region, rv3d, self._first_edge_center_world)
            if start_2d:
                self._draw_line(shader, start_2d, Vector(self._mouse_pos), COL_LINE)

            # Highlight first edge too
            if self._first_edge_index < len(bm.edges):
                fe = bm.edges[self._first_edge_index]
                fa_2d = location_3d_to_region_2d(region, rv3d, world @ fe.verts[0].co)
                fb_2d = location_3d_to_region_2d(region, rv3d, world @ fe.verts[1].co)
                if fa_2d and fb_2d:
                    self._draw_line(shader, fa_2d, fb_2d, (0.0, 0.8, 1.0, 0.9), width=3.0)

            # Midpoint preview for edge pairs when Alt held
            if self._alt_held and self._hover_edge_index != -1 and self._hover_edge_index < len(bm.edges):
                pairs, _shared = _match_edge_verts(bm, self._first_edge_index, self._hover_edge_index)
                for sv, dv in pairs:
                    mid_world = (world @ sv.co + world @ dv.co) / 2.0
                    mid_2d = location_3d_to_region_2d(region, rv3d, mid_world)
                    if mid_2d:
                        self._draw_point(shader, mid_2d, COL_HIGHLIGHT)

    # ---- drawing helpers ----

    @staticmethod
    def _draw_point(shader, pos_2d, color, size=12.0):
        gpu.state.point_size_set(size)
        batch = batch_for_shader(shader, 'POINTS', {"pos": [pos_2d]})
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
        gpu.state.point_size_set(1.0)

    @staticmethod
    def _draw_line(shader, a, b, color, width=2.0):
        gpu.state.line_width_set(width)
        batch = batch_for_shader(shader, 'LINES', {"pos": [a, b]})
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
        gpu.state.line_width_set(1.0)

    def _add_draw_handler(self, context):
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_callback, (context,), 'WINDOW', 'POST_PIXEL'
        )

    def _remove_draw_handler(self):
        if self._draw_handler is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_handler, 'WINDOW')
            self._draw_handler = None

    # ──────────────────────────── modal ────────────────────────────

    def invoke(self, context, event):
        if not self.poll(context):
            self.report({'WARNING'}, "Must be in Edit Mode on a mesh")
            return {'CANCELLED'}

        self._mode = 'EDGE' if _edge_select_mode(context) else 'VERT'
        self._first_vert_index = -1
        self._first_vert_co_world = None
        self._first_edge_index = -1
        self._first_edge_center_world = None
        self._hover_vert_index = -1
        self._hover_edge_index = -1
        self._alt_held = False
        self._mouse_pos = (event.mouse_region_x, event.mouse_region_y)

        self._add_draw_handler(context)
        context.window_manager.modal_handler_add(self)

        kind = "edge" if self._mode == 'EDGE' else "vertex"
        context.area.header_text_set(f"Target Weld: LMB source {kind} | RMB/Esc cancel")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        context.area.tag_redraw()

        # Track Alt
        if event.type in {'LEFT_ALT', 'RIGHT_ALT'}:
            self._alt_held = event.value == 'PRESS'
            return {'RUNNING_MODAL'}

        if event.type == 'MOUSEMOVE':
            self._mouse_pos = (event.mouse_region_x, event.mouse_region_y)
            self._alt_held = event.alt
            if self._mode == 'EDGE':
                hover = get_nearest_edge(context, self._mouse_pos)
                self._hover_edge_index = hover if hover is not None else -1
            else:
                hover = get_nearest_vertex(context, self._mouse_pos)
                self._hover_vert_index = hover if hover is not None else -1
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if self._mode == 'EDGE':
                return self._handle_click_edge(context, event)
            else:
                return self._handle_click_vert(context, event)

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._finish(context)
            self.report({'INFO'}, "Target Weld cancelled")
            return {'CANCELLED'}

        return {'PASS_THROUGH'}

    # ---- vertex click handling ----

    def _handle_click_vert(self, context, event):
        mouse = (event.mouse_region_x, event.mouse_region_y)
        vert_idx = get_nearest_vertex(context, mouse)

        if vert_idx is None:
            self.report({'WARNING'}, "No vertex under cursor")
            return {'RUNNING_MODAL'}

        if self._first_vert_index == -1:
            obj = context.edit_object
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            self._first_vert_index = vert_idx
            self._first_vert_co_world = obj.matrix_world @ bm.verts[vert_idx].co
            context.area.header_text_set(
                "Target Weld: LMB destination | Alt+LMB merge at midpoint | RMB/Esc cancel"
            )
            return {'RUNNING_MODAL'}
        else:
            if vert_idx == self._first_vert_index:
                self.report({'WARNING'}, "Same vertex selected, pick a different one")
                return {'RUNNING_MODAL'}
            merge_at_mid = event.alt
            self._weld_verts(context, self._first_vert_index, vert_idx, merge_at_mid)
            if merge_at_mid:
                self.report({'INFO'}, "Target Weld: merged at midpoint")
            self._finish(context)
            return {'FINISHED'}

    # ---- edge click handling ----

    def _handle_click_edge(self, context, event):
        mouse = (event.mouse_region_x, event.mouse_region_y)
        edge_idx = get_nearest_edge(context, mouse)

        if edge_idx is None:
            self.report({'WARNING'}, "No edge under cursor")
            return {'RUNNING_MODAL'}

        if self._first_edge_index == -1:
            obj = context.edit_object
            bm = bmesh.from_edit_mesh(obj.data)
            bm.edges.ensure_lookup_table()
            e = bm.edges[edge_idx]
            self._first_edge_index = edge_idx
            center = (e.verts[0].co + e.verts[1].co) / 2.0
            self._first_edge_center_world = obj.matrix_world @ center
            context.area.header_text_set(
                "Target Weld: LMB destination edge | Alt+LMB merge at midpoint | RMB/Esc cancel"
            )
            return {'RUNNING_MODAL'}
        else:
            if edge_idx == self._first_edge_index:
                self.report({'WARNING'}, "Same edge selected, pick a different one")
                return {'RUNNING_MODAL'}
            merge_at_mid = event.alt
            self._weld_edges(context, self._first_edge_index, edge_idx, merge_at_mid)
            self._finish(context)
            return {'FINISHED'}

    # ──────────────────────────── weld operations ────────────────────────────

    def _weld_verts(self, context, src_idx, dst_idx, at_midpoint=False):
        obj = context.edit_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        bm.verts.ensure_lookup_table()

        src = bm.verts[src_idx]
        dst = bm.verts[dst_idx]

        if at_midpoint:
            mid = (src.co + dst.co) / 2.0
            src.co = mid
            dst.co = mid
        else:
            src.co = dst.co.copy()

        bmesh.ops.remove_doubles(bm, verts=[src, dst], dist=0.0001)
        bmesh.update_edit_mesh(me)

    def _weld_edges(self, context, src_edge_idx, dst_edge_idx, at_midpoint=False):
        obj = context.edit_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        pairs, shared = _match_edge_verts(bm, src_edge_idx, dst_edge_idx)

        if not pairs:
            self.report({'WARNING'}, "Edges share both vertices, nothing to merge")
            return

        all_verts = []
        for sv, dv in pairs:
            if at_midpoint:
                mid = (sv.co + dv.co) / 2.0
                sv.co = mid
                dv.co = mid
                self.report({'INFO'}, "Target Weld: edges merged at midpoint")
            else:
                sv.co = dv.co.copy()
            all_verts.extend([sv, dv])

        bmesh.ops.remove_doubles(bm, verts=all_verts, dist=0.0001)
        bmesh.update_edit_mesh(me)

    # ──────────────────────────── cleanup ────────────────────────────

    def _finish(self, context):
        self._hover_vert_index = -1
        self._hover_edge_index = -1
        self._alt_held = False
        self._remove_draw_handler()
        context.area.header_text_set(None)


classes = [MTOOLS_OT_target_weld]
