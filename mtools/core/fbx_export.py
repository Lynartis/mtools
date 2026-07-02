"""
fbx_export.py - Export an armature + its skinned meshes as an FBX (rig + model).
==============================================================================

Thin, well-labelled wrapper around Blender's built-in `bpy.ops.export_scene.fbx`.
The operator layer only picks the armature and the file path; all the actual
export tuning lives in the single FBX_PARAMS dict below so it is easy to find
and change.

What the engine needs from the FBX and how we get it:
  * correct bone names   -> add_leaf_bones=False (no extra invented tip bones)
  * skinning / weights   -> exporting the Armature + meshes keeps vertex groups
  * material IDs          -> Blender writes each face's material-slot index into
                            the FBX automatically; we just keep the meshes' slots
The animation is intentionally NOT baked here (bake_anim=False) - it ships in
the separate .anim file.
"""

import bpy

from . import coordinates


# ===========================================================================
# The ONE place to tune the FBX export. Coordinate axes are added per-export.
# ===========================================================================
FBX_PARAMS = dict(
    use_selection=True,              # export only what we select (rig + its meshes)
    object_types={"ARMATURE", "MESH"},
    use_mesh_modifiers=True,         # apply modifiers so the mesh matches the viewport
    mesh_smooth_type="FACE",         # per-face smoothing -> clean normals on import
    add_leaf_bones=False,            # keep the exact bone list & names
    primary_bone_axis="Y",           # bone forward axis (tweak if bones import rotated)
    secondary_bone_axis="X",
    use_armature_deform_only=False,  # export all bones, not just deform bones
    bake_anim=False,                 # animation is exported separately as .anim
    path_mode="AUTO",
)


# ===========================================================================
# Find the meshes skinned to the armature
# ===========================================================================
def collect_bound_meshes(armature):
    """
    Return all mesh objects bound to `armature` - either through an Armature
    modifier pointing at it, or parented to it. These ride along in the export.
    """
    meshes = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        via_modifier = any(m.type == "ARMATURE" and m.object == armature
                           for m in obj.modifiers)
        via_parent = obj.parent == armature
        if via_modifier or via_parent:
            meshes.append(obj)
    return meshes


# ===========================================================================
# The export itself
# ===========================================================================
def export_rig(context, armature, filepath, remap):
    """
    Export `armature` + its skinned meshes to `filepath` in the coordinate
    system described by `remap`. Returns the list of meshes that were exported.
    Restores the user's selection afterwards.
    """
    # Translate our remap into the FBX exporter's axis_forward / axis_up options.
    axes = coordinates.remap_to_fbx_axes(remap)
    if axes is None:
        raise ValueError(
            "Coordinate system %r can't be written as FBX axes (it flips "
            "handedness). Choose a non-mirrored system for the FBX export." % remap)
    axis_forward, axis_up = axes

    meshes = collect_bound_meshes(armature)

    # Remember the current selection / active object so we can put it back.
    view_layer = context.view_layer
    previous_active = view_layer.objects.active
    previous_selection = list(context.selected_objects)

    try:
        # Select exactly the armature + its meshes (use_selection=True exports these).
        # Deselect by walking the view layer directly - more robust than the
        # select_all operator, which needs a specific context to run.
        for obj in view_layer.objects:
            obj.select_set(False)
        armature.select_set(True)
        for mesh in meshes:
            mesh.select_set(True)
        view_layer.objects.active = armature

        # Blender's built-in FBX exporter does the heavy lifting.
        bpy.ops.export_scene.fbx(
            filepath=filepath,
            axis_forward=axis_forward,
            axis_up=axis_up,
            **FBX_PARAMS,
        )
    finally:
        # Restore whatever the user had selected before we started.
        for obj in view_layer.objects:
            obj.select_set(False)
        for obj in previous_selection:
            try:
                obj.select_set(True)
            except (RuntimeError, ReferenceError):
                pass
        view_layer.objects.active = previous_active

    return meshes
