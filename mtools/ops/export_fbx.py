import os
import bpy


def _default_filepath():
    """Suggest a path next to the current .blend, or a relative fallback."""
    blend = bpy.data.filepath
    if blend:
        return os.path.splitext(blend)[0] + ".fbx"
    return "//untitled.fbx"


def _ensure_fbx_ext(path):
    if path and not path.lower().endswith(".fbx"):
        path += ".fbx"
    return path


# ════════════════════════════════════════════════════════════════════════════
# RIG + MODEL EXPORT
# ════════════════════════════════════════════════════════════════════════════
# Central export settings -- the one place to tweak how the FBX is written.
# bake_anim is OFF: animation ships separately in the .anim file. Blender's FBX
# exporter treats Armature modifiers specially (skin weights are exported, the
# modifier is not "applied"), so use_mesh_modifiers is safe for a rigged mesh.
FBX_PARAMS = {
    "use_selection": True,               # export only what we select below
    "object_types": {'ARMATURE', 'MESH'},
    "add_leaf_bones": False,             # most engines don't want Blender leaf bones
    "bake_anim": False,                  # animation lives in the .anim file
    "use_armature_deform_only": False,   # keep control/non-deform bones too
    "mesh_smooth_type": 'FACE',          # export smoothing as face flags
    "use_mesh_modifiers": True,          # apply modifiers (armature deform is preserved)
    "use_custom_props": False,
}

def resolve_fbx_axes(remap):
    """Find the FBX axis_forward/axis_up whose transform equals the .anim remap.

    The FBX exporter reorients data with `axis_conversion(to_forward=axis_forward,
    to_up=axis_up)`. To guarantee the FBX and the .anim end up in the SAME
    coordinate system, we search the 6x6 forward/up combinations for the one whose
    matrix matches the .anim conversion matrix C (built from the same remap).

    Returns (axis_forward, axis_up), or None if no combination matches -- e.g. a
    mirrored / handedness-flipped target, which FBX axis settings can't express.
    """
    from bpy_extras.io_utils import axis_conversion   # Blender's own axis maths
    from . import export_anim                          # shared conversion toolkit

    target = export_anim.build_conversion_matrix(remap)  # 3x3 matrix C (blender->engine)
    tokens = ('X', 'Y', 'Z', '-X', '-Y', '-Z')
    for forward in tokens:
        for up in tokens:
            try:
                m = axis_conversion(to_forward=forward, to_up=up)
            except (ValueError, RuntimeError):
                continue  # forward and up on the same axis -> invalid combo
            if all(abs(m[i][j] - target[i][j]) < 1e-6 for i in range(3) for j in range(3)):
                return forward, up
    return None


def collect_bound_meshes(armature, objects):
    """Meshes deformed by `armature`: those with an Armature modifier pointing at
    it, plus any mesh directly parented to it. `objects` is the iterable to scan
    (the view layer's objects, so everything is selectable/exportable)."""
    meshes = []
    for obj in objects:
        if obj.type != 'MESH':
            continue
        # An Armature modifier whose target is this armature == this mesh is skinned.
        bound = any(mod.type == 'ARMATURE' and mod.object == armature
                    for mod in obj.modifiers)
        if not bound and obj.parent == armature:
            bound = True
        if bound:
            meshes.append(obj)
    return meshes


def _resolve_fbx_path(scene):
    """Full .fbx path from the saved path, with the optional name override."""
    path = scene.mtools_fbx_export_path
    name = scene.mtools_fbx_name.strip()
    if name:
        directory = os.path.dirname(path) if path else ""
        path = os.path.join(directory, name)
    return _ensure_fbx_ext(path)


class MTOOLS_OT_export_rig(bpy.types.Operator):
    """Export the picked armature and every mesh skinned to it (its rig + model)
    as a single FBX, using the shared coordinate system"""
    bl_idname = "mtools.export_rig"
    bl_label = "Export Rig + Model"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene.mtools_fbx_armature is not None

    def execute(self, context):
        scene = context.scene
        armature = scene.mtools_fbx_armature
        if armature is None or armature.type != 'ARMATURE':
            self.report({'WARNING'}, "Pick an armature to export")
            return {'CANCELLED'}

        path = _resolve_fbx_path(scene)
        if not path:
            self.report({'WARNING'}, "No export path set")
            return {'CANCELLED'}
        scene.mtools_fbx_export_path = path            # remember for next time

        if not hasattr(bpy.ops.export_scene, "fbx"):
            self.report({'ERROR'}, "FBX exporter add-on is not enabled")
            return {'CANCELLED'}

        # Resolve the shared coordinate system to FBX axis settings that match the
        # .anim exporter's conversion, so both exports share one coordinate system.
        from . import export_anim_xml
        _coord, remap = export_anim_xml.resolve_export_remap(scene)
        try:
            axes = resolve_fbx_axes(remap)
        except ValueError as exc:                      # invalid custom remap string
            self.report({'ERROR'}, f"Invalid coordinate remap: {exc}")
            return {'CANCELLED'}
        if axes is None:
            self.report({'WARNING'}, f"Coordinate system '{remap}' has no matching FBX "
                        "axis preset (mirrored/handedness-flipped target); exporting "
                        "with Blender-native axes -- FBX and .anim may not match")
            axis_kwargs = {}
        else:
            axis_kwargs = {"axis_forward": axes[0], "axis_up": axes[1]}

        # Selection + FBX export require object mode.
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        view_objects = context.view_layer.objects
        meshes = collect_bound_meshes(armature, view_objects)

        # Save the current selection/active so we can restore it afterwards.
        saved_selection = [o for o in view_objects if o.select_get()]
        saved_active = view_objects.active

        # Select exactly the armature + its bound meshes for use_selection export.
        bpy.ops.object.select_all(action='DESELECT')
        armature.select_set(True)
        for mesh in meshes:
            mesh.select_set(True)
        view_objects.active = armature

        try:
            bpy.ops.export_scene.fbx(filepath=path, **FBX_PARAMS, **axis_kwargs)
        except RuntimeError as exc:
            self.report({'ERROR'}, f"FBX export failed: {exc}")
            return {'CANCELLED'}
        finally:
            # Restore the user's previous selection/active object.
            bpy.ops.object.select_all(action='DESELECT')
            for o in saved_selection:
                o.select_set(True)
            view_objects.active = saved_active

        self.report({'INFO'}, f"Exported rig + {len(meshes)} mesh(es) to {path}")
        return {'FINISHED'}


class MTOOLS_OT_set_fbx_export_path(bpy.types.Operator):
    """Choose and remember the FBX export location for this .blend file"""
    bl_idname = "mtools.set_fbx_export_path"
    bl_label = "Set FBX Export Path"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.fbx", options={'HIDDEN'})

    def invoke(self, context, event):
        stored = context.scene.mtools_fbx_export_path
        self.filepath = stored if stored else _default_filepath()
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        context.scene.mtools_fbx_export_path = _ensure_fbx_ext(self.filepath)
        self.report({'INFO'}, f"FBX export path set: {context.scene.mtools_fbx_export_path}")
        return {'FINISHED'}


class MTOOLS_OT_export_fbx_selected(bpy.types.Operator):
    """Export the selected objects as FBX to this .blend file's saved path.
    The first export asks where to save and remembers it for next time."""
    bl_idname = "mtools.export_fbx_selected"
    bl_label = "Export Selected FBX"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.fbx", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def invoke(self, context, event):
        stored = context.scene.mtools_fbx_export_path
        if stored:
            # Path already set for this .blend -- export straight away
            self.filepath = stored
            return self.execute(context)
        # No remembered path yet -- prompt for one, then export
        self.filepath = _default_filepath()
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath:
            self.report({'WARNING'}, "No export path set")
            return {'CANCELLED'}

        path = _ensure_fbx_ext(self.filepath)
        context.scene.mtools_fbx_export_path = path  # remember for next time

        if not hasattr(bpy.ops.export_scene, "fbx"):
            self.report({'ERROR'}, "FBX exporter add-on is not enabled")
            return {'CANCELLED'}

        try:
            bpy.ops.export_scene.fbx(filepath=path, use_selection=True)
        except RuntimeError as exc:
            self.report({'ERROR'}, f"FBX export failed: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Exported selection to {path}")
        return {'FINISHED'}


classes = [
    MTOOLS_OT_set_fbx_export_path,
    MTOOLS_OT_export_fbx_selected,
    MTOOLS_OT_export_rig,
]


def register_props():
    bpy.types.Scene.mtools_fbx_export_path = bpy.props.StringProperty(
        name="FBX Export Path",
        description="Where the FBX is saved for this .blend file",
        subtype='FILE_PATH',
        default="",
    )
    bpy.types.Scene.mtools_fbx_name = bpy.props.StringProperty(
        name="FBX Name",
        description="Optional base file name; overrides the name part of the path",
        default="",
    )
    bpy.types.Scene.mtools_fbx_armature = bpy.props.PointerProperty(
        name="Armature",
        description="Armature to export together with the meshes skinned to it",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE',
    )


def unregister_props():
    for attr in ("mtools_fbx_armature", "mtools_fbx_name", "mtools_fbx_export_path"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
