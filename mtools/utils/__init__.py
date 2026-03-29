if "bpy" in locals():
    import importlib
    mesh = importlib.reload(mesh)
else:
    from . import mesh

from .mesh import get_nearest_vertex, get_nearest_edge
