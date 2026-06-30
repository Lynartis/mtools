if "bpy" in locals():
    import importlib
    reload_scripts = importlib.reload(reload_scripts)
    target_weld = importlib.reload(target_weld)
    boundary_select = importlib.reload(boundary_select)
    export_fbx = importlib.reload(export_fbx)
    export_anim = importlib.reload(export_anim)
else:
    from . import reload_scripts, target_weld, boundary_select, export_fbx, export_anim


def _collect_classes():
    return [
        *reload_scripts.classes,
        *target_weld.classes,
        *boundary_select.classes,
        *export_fbx.classes,
        *export_anim.classes,
    ]


classes = _collect_classes()


def reload():
    """Called by the parent package to refresh the classes list after reload."""
    global classes, reload_scripts, target_weld, boundary_select, export_fbx, export_anim
    import importlib
    reload_scripts = importlib.reload(reload_scripts)
    target_weld = importlib.reload(target_weld)
    boundary_select = importlib.reload(boundary_select)
    export_fbx = importlib.reload(export_fbx)
    export_anim = importlib.reload(export_anim)
    classes = _collect_classes()
