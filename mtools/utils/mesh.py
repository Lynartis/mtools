import bmesh
from mathutils import Vector
from bpy_extras.view3d_utils import location_3d_to_region_2d


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


def _point_to_segment_dist_2d(p, a, b):
    """Return the distance from point p to line segment a-b in 2D."""
    ab = b - a
    ap = p - a
    ab_sq = ab.dot(ab)
    if ab_sq < 1e-8:
        return (p - a).length
    t = max(0.0, min(1.0, ap.dot(ab) / ab_sq))
    proj = a + ab * t
    return (p - proj).length


def get_nearest_edge(context, mouse_pos, threshold=20):
    """Find the nearest edge to the mouse position in screen space.

    Returns the edge index or None.
    """
    obj = context.edit_object
    me = obj.data
    bm = bmesh.from_edit_mesh(me)
    bm.edges.ensure_lookup_table()

    region = context.region
    rv3d = context.region_data
    world_mat = obj.matrix_world
    mouse_vec = Vector(mouse_pos)

    best_edge = None
    best_dist = threshold

    for e in bm.edges:
        if e.hide:
            continue
        co_a = location_3d_to_region_2d(region, rv3d, world_mat @ e.verts[0].co)
        co_b = location_3d_to_region_2d(region, rv3d, world_mat @ e.verts[1].co)
        if co_a is None or co_b is None:
            continue
        dist = _point_to_segment_dist_2d(mouse_vec, co_a, co_b)
        if dist < best_dist:
            best_dist = dist
            best_edge = e.index

    return best_edge
