"""
ops package - operators + the one place all Scene properties are declared.

Every module here exposes a module-level `classes` list of Operator classes.
This package collects them into `classes` for register(), and owns
register_props()/unregister_props() so all `mtools_*` Scene properties live in
a single, easy-to-find place.
"""

if "bpy" in locals():
    import importlib
    reload_scripts = importlib.reload(reload_scripts)
    export_fbx = importlib.reload(export_fbx)
    export_anim = importlib.reload(export_anim)
else:
    from . import reload_scripts, export_fbx, export_anim

import bpy

from ..core import coordinates


def _collect_classes():
    return [*reload_scripts.classes, *export_fbx.classes, *export_anim.classes]


classes = _collect_classes()


def reload():
    """Rebuild the `classes` list after a hot-reload (called by the package)."""
    global classes
    classes = _collect_classes()


# ===========================================================================
# Scene properties - ALL of the add-on's UI state, declared in one place.
# ===========================================================================
def _is_armature(self, obj):
    """PointerProperty filter: only allow armature objects to be picked."""
    return obj is not None and obj.type == "ARMATURE"


def register_props():
    scene = bpy.types.Scene

    # --- Export Rig + Model (FBX) ---
    scene.mtools_fbx_name = bpy.props.StringProperty(
        name="Name", description="FBX file name (without extension)", default="")
    scene.mtools_fbx_path = bpy.props.StringProperty(
        name="Path", description="Folder to export the FBX into",
        subtype="DIR_PATH", default="//")
    scene.mtools_fbx_armature = bpy.props.PointerProperty(
        name="Armature", description="Armature (rig) to export",
        type=bpy.types.Object, poll=_is_armature)

    # --- Export Animation (.anim) ---
    scene.mtools_anim_name = bpy.props.StringProperty(
        name="Clip Name", description="Animation clip name (defaults to the action name)",
        default="")
    scene.mtools_anim_path = bpy.props.StringProperty(
        name="Path", description="Folder to export the .anim into",
        subtype="DIR_PATH", default="//")
    scene.mtools_anim_armature = bpy.props.PointerProperty(
        name="Armature", description="Armature whose action to export",
        type=bpy.types.Object, poll=_is_armature)
    scene.mtools_anim_action = bpy.props.PointerProperty(
        name="Action", description="Animation action to export", type=bpy.types.Action)
    scene.mtools_anim_loop = bpy.props.EnumProperty(
        name="Loop Mode", items=export_anim.LOOP_ITEMS, default="LOOP")

    # --- Extra Options: two independent coordinate systems ---
    scene.mtools_fbx_coord = bpy.props.EnumProperty(
        name="FBX Coordinates", items=coordinates.COORD_ITEMS, default="ENGINE")
    scene.mtools_fbx_coord_custom = bpy.props.StringProperty(
        name="FBX Custom Remap", description="Axis remap, e.g. 'X Z -Y'", default="X Z -Y")
    scene.mtools_anim_coord = bpy.props.EnumProperty(
        name="Anim Coordinates", items=coordinates.COORD_ITEMS, default="ENGINE")
    scene.mtools_anim_coord_custom = bpy.props.StringProperty(
        name="Anim Custom Remap", description="Axis remap, e.g. 'X Z -Y'", default="X Z -Y")


def unregister_props():
    scene = bpy.types.Scene
    for prop in (
        "mtools_fbx_name", "mtools_fbx_path", "mtools_fbx_armature",
        "mtools_anim_name", "mtools_anim_path", "mtools_anim_armature",
        "mtools_anim_action", "mtools_anim_loop",
        "mtools_fbx_coord", "mtools_fbx_coord_custom",
        "mtools_anim_coord", "mtools_anim_coord_custom",
    ):
        if hasattr(scene, prop):
            delattr(scene, prop)
