if "bpy" in locals():
    import importlib
    reload_scripts = importlib.reload(reload_scripts)
    target_weld = importlib.reload(target_weld)
else:
    from . import reload_scripts, target_weld


def _collect_classes():
    return [*reload_scripts.classes, *target_weld.classes]


classes = _collect_classes()


def reload():
    """Called by the parent package to refresh the classes list after reload."""
    global classes, reload_scripts, target_weld
    import importlib
    reload_scripts = importlib.reload(reload_scripts)
    target_weld = importlib.reload(target_weld)
    classes = _collect_classes()
