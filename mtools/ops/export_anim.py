import os
import re
import math
import bpy


# ──────────────────────────── format ────────────────────────────
# Custom text animation format, version 2. Sections common to both modes:
#   [META]       key = value metadata (armature, take, mode, range, fps, counts)
#   [BONES]      bone table: index name parent_index   (parents always first)
#   [REST]       bind pose, parent-relative:
#                  name loc(x y z) quat(w x y z) euler_deg(x y z) scale(x y z)
#                euler is XYZ order, in degrees -- a convenience alongside quat.
#
# Then one mode-specific section:
#   mode=baked      [ANIMATION] -- per-frame parent-relative pose (final pose,
#                   includes constraints/drivers), same column layout as [REST],
#                   grouped under "FRAME n".
#   mode=keyframes  [CURVES] -- raw fcurve data per bone channel, for engine-side
#                   curve evaluation. Everything needed to rebuild a curve any
#                   way: co, both handles + handle types, interpolation, easing,
#                   easing params (amplitude/back/period), fcurve extrapolation,
#                   and keyframe type. Values are the pose bone's LOCAL transform
#                   relative to rest (what fcurves store), NOT the composed pose;
#                   reconstruct as REST x TRS(location, rotation, scale).
#                   rotation_euler channels are emitted in DEGREES (unit=deg);
#                   rotation_quaternion channels stay quaternion components.
#
# All transforms are in Blender's native space (Z-up, right-handed).
# Converting to a target engine's axes is left as a later step.
FORMAT_VERSION = 2

MODE_ITEMS = [
    ('BAKED', "Baked", "Sample every frame: final parent-relative pose, includes constraints"),
    ('KEYFRAMES', "Keyframes", "Raw fcurve keyframes + handles, for engine-side curve evaluation"),
]

# pose.bones["Name"].channel  ->  ("Name", "channel"); non-greedy so nested
# paths (e.g. constraints) still resolve the bone name correctly.
_BONE_PATH_RE = re.compile(r'pose\.bones\["(.*?)"\]\.(.+)')


def _default_filepath(action):
    """Suggest a path next to the current .blend, named after the take."""
    blend = bpy.data.filepath
    take = action.name if action else "animation"
    if blend:
        return os.path.join(os.path.dirname(blend), take + ".txt")
    return "//" + take + ".txt"


def _ensure_txt_ext(path):
    if path and not path.lower().endswith(".txt"):
        path += ".txt"
    return path


def _ordered_bones(armature):
    """Pose bones ordered so every parent appears before its children.

    A game engine reconstructing world transforms needs parents resolved
    first; Blender's pose.bones order isn't guaranteed to be hierarchical.

    Parent/child links are matched by name -- Blender hands back a fresh
    Python wrapper on each RNA access, so identity (`is`) comparison of
    pose bones is unreliable.
    """
    pose_bones = armature.pose.bones
    children = {}
    roots = []
    for pbone in pose_bones:
        if pbone.parent is None:
            roots.append(pbone.name)
        else:
            children.setdefault(pbone.parent.name, []).append(pbone.name)

    ordered = []

    def visit(name):
        ordered.append(pose_bones[name])
        for child_name in children.get(name, []):
            visit(child_name)

    for name in roots:
        visit(name)
    return ordered


def _fmt_transform(matrix):
    """Decompose a 4x4 matrix into
    'loc(x y z) quat(w x y z) euler_deg(x y z) scale(x y z)' (euler is XYZ)."""
    loc, quat, scale = matrix.decompose()
    e = matrix.to_euler('XYZ')
    return (
        f"{loc.x:.6f} {loc.y:.6f} {loc.z:.6f} "
        f"{quat.w:.6f} {quat.x:.6f} {quat.y:.6f} {quat.z:.6f} "
        f"{math.degrees(e.x):.6f} {math.degrees(e.y):.6f} {math.degrees(e.z):.6f} "
        f"{scale.x:.6f} {scale.y:.6f} {scale.z:.6f}"
    )


def _local_pose_matrix(pbone):
    """Parent-relative pose matrix (armature space if the bone is a root)."""
    if pbone.parent is not None:
        return pbone.parent.matrix.inverted() @ pbone.matrix
    return pbone.matrix


def _local_rest_matrix(bone):
    """Parent-relative rest (bind) matrix for a data bone."""
    if bone.parent is not None:
        return bone.parent.matrix_local.inverted() @ bone.matrix_local
    return bone.matrix_local


def _common_header(armature, action, bones, index_of, start, end, fps, mode):
    """[META] + [BONES] + [REST] -- shared by both export modes."""
    lines = [f"# MTools Animation Export v{FORMAT_VERSION}", ""]

    lines.append("[META]")
    lines.append(f"armature = {armature.name}")
    lines.append(f"take = {action.name}")
    lines.append(f"mode = {mode.lower()}")
    lines.append(f"frame_start = {start}")
    lines.append(f"frame_end = {end}")
    lines.append(f"frame_count = {end - start + 1}")
    lines.append(f"fps = {fps:g}")
    lines.append(f"bone_count = {len(bones)}")
    lines.append("")

    lines.append("[BONES]")
    lines.append("# index name parent_index")
    for i, pbone in enumerate(bones):
        parent_idx = index_of[pbone.parent.name] if pbone.parent else -1
        lines.append(f"{i} {pbone.name} {parent_idx}")
    lines.append("")

    lines.append("[REST]")
    lines.append("# name loc(x y z) quat(w x y z) euler_deg(x y z) scale(x y z)")
    for pbone in bones:
        lines.append(f"{pbone.name} {_fmt_transform(_local_rest_matrix(pbone.bone))}")
    lines.append("")
    return lines


def _baked_section(scene, bones, start, end):
    """[ANIMATION]: sample the parent-relative pose at every frame."""
    lines = ["[ANIMATION]"]
    lines.append("# per frame: name loc(x y z) quat(w x y z) euler_deg(x y z) scale(x y z)")
    for frame in range(start, end + 1):
        scene.frame_set(frame)
        lines.append(f"FRAME {frame}")
        for pbone in bones:
            lines.append(f"{pbone.name} {_fmt_transform(_local_pose_matrix(pbone))}")
    lines.append("")
    return lines, f"{len(bones)} bones x {end - start + 1} frames (baked)"


def _action_fcurves(action, anim_data):
    """Yield the action's fcurves across Blender's legacy and slotted APIs.

    Pre-4.4 actions expose a flat `action.fcurves`. From Blender 4.4 the data
    lives in layers -> strips -> channelbags (one bag per slot), and 5.0 dropped
    `action.fcurves` entirely. We keep only the bag for the slot the armature is
    using (anim_data.action_slot); if that can't be resolved we take them all.
    """
    layers = getattr(action, "layers", None)
    if not layers:
        for fc in action.fcurves:  # legacy action (Blender <= 4.3)
            yield fc
        return

    slot = getattr(anim_data, "action_slot", None)
    slot_handle = slot.handle if slot is not None else None
    for layer in layers:
        for strip in layer.strips:
            bags = getattr(strip, "channelbags", None)
            if not bags:
                continue
            for bag in bags:
                if slot_handle is None or bag.slot_handle == slot_handle:
                    for fc in bag.fcurves:
                        yield fc


# Per-channel unit tag. 'deg' channels have their value-axis numbers (value,
# handle Y, amplitude) emitted in degrees instead of Blender's native radians.
_CHANNEL_UNIT = {
    'location': 'loc',
    'rotation_quaternion': 'quat',
    'rotation_euler': 'deg',
    'rotation_axis_angle': 'axisangle',
    'scale': 'scale',
}


def _keyframe_section(action, anim_data, bones, index_of):
    """[CURVES]: full raw fcurve data per bone channel.

    Channels are the pose bone's local-to-rest transform (location,
    rotation_*, scale, ...) exactly as authored -- no constraints/drivers.
    Every field needed to rebuild the curve any way is emitted.
    """
    by_bone = {}
    for fc in _action_fcurves(action, anim_data):
        m = _BONE_PATH_RE.match(fc.data_path)
        if not m:
            continue  # object-level or non-bone channel
        bone_name, prop = m.group(1), m.group(2)
        if bone_name in index_of:  # ignore curves for bones not in this armature
            by_bone.setdefault(bone_name, []).append((prop, fc))

    lines = ["[CURVES]"]
    lines.append("# BONE index rotation_mode name")
    lines.append("# CHANNEL property array_index unit extrapolation key_count")
    lines.append("# KEY frame value interp easing "
                 "hl_x hl_y hl_type hr_x hr_y hr_type amplitude back period keytype")
    lines.append("# unit=deg: value, hl_y, hr_y and amplitude are in degrees; "
                 "frame, hl_x, hr_x and period are in frames")

    animated = channels = keys = 0
    for pbone in bones:
        fcurves = by_bone.get(pbone.name)
        if not fcurves:
            continue
        animated += 1
        lines.append(f"BONE {index_of[pbone.name]} {pbone.rotation_mode} {pbone.name}")
        for prop, fc in sorted(fcurves, key=lambda pf: (pf[0], pf[1].array_index)):
            unit = _CHANNEL_UNIT.get(prop, 'raw')
            to_deg = unit == 'deg'

            def conv(v):  # radians -> degrees only for euler value-axis numbers
                return math.degrees(v) if to_deg else v

            kps = fc.keyframe_points
            lines.append(f"CHANNEL {prop} {fc.array_index} {unit} {fc.extrapolation} {len(kps)}")
            channels += 1
            for kp in kps:
                frame, value = kp.co
                hl, hr = kp.handle_left, kp.handle_right
                lines.append(
                    f"KEY {frame:.6f} {conv(value):.6f} {kp.interpolation} {kp.easing} "
                    f"{hl.x:.6f} {conv(hl.y):.6f} {kp.handle_left_type} "
                    f"{hr.x:.6f} {conv(hr.y):.6f} {kp.handle_right_type} "
                    f"{conv(kp.amplitude):.6f} {kp.back:.6f} {kp.period:.6f} {kp.type}"
                )
                keys += 1
    lines.append("")
    return lines, f"{animated} animated bones, {channels} channels, {keys} keys"


def write_animation(context, filepath, armature, action, mode):
    """Write `action` on `armature` to the text file in the given `mode`.

    Kept as a standalone function so the export format is easy to tweak and
    experiment with independently of the operator/UI plumbing. Returns a short
    human-readable summary for the status report.
    """
    scene = context.scene
    bones = _ordered_bones(armature)
    index_of = {pbone.name: i for i, pbone in enumerate(bones)}
    start, end = (int(round(v)) for v in action.frame_range)
    fps = scene.render.fps / scene.render.fps_base

    lines = _common_header(armature, action, bones, index_of, start, end, fps, mode)

    # Assign the chosen take so baked sampling poses the rig and keyframe mode
    # can resolve which action slot the armature uses; restore state after.
    anim_data = armature.animation_data or armature.animation_data_create()
    saved_action = anim_data.action
    saved_frame = scene.frame_current
    anim_data.action = action
    try:
        if mode == 'KEYFRAMES':
            section, summary = _keyframe_section(action, anim_data, bones, index_of)
        else:
            section, summary = _baked_section(scene, bones, start, end)
        lines += section
    finally:
        anim_data.action = saved_action
        scene.frame_set(saved_frame)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return summary


# ──────────────────────────── operators ────────────────────────────

class MTOOLS_OT_set_anim_export_path(bpy.types.Operator):
    """Choose and remember the animation .txt export location for this .blend file"""
    bl_idname = "mtools.set_anim_export_path"
    bl_label = "Set Animation Export Path"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.txt", options={'HIDDEN'})

    def invoke(self, context, event):
        stored = context.scene.mtools_anim_export_path
        self.filepath = stored if stored else _default_filepath(context.scene.mtools_anim_action)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        context.scene.mtools_anim_export_path = _ensure_txt_ext(self.filepath)
        self.report({'INFO'}, f"Animation export path set: {context.scene.mtools_anim_export_path}")
        return {'FINISHED'}


class MTOOLS_OT_export_animation(bpy.types.Operator):
    """Export the chosen armature + take to a custom .txt animation file.
    The first export asks where to save and remembers it for next time."""
    bl_idname = "mtools.export_animation"
    bl_label = "Export Animation"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.txt", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.scene.mtools_anim_armature is not None

    def _resolve_action(self, context):
        """Use the picked take, else fall back to the armature's active action."""
        action = context.scene.mtools_anim_action
        if action is not None:
            return action
        armature = context.scene.mtools_anim_armature
        if armature.animation_data:
            return armature.animation_data.action
        return None

    def invoke(self, context, event):
        stored = context.scene.mtools_anim_export_path
        if stored:
            self.filepath = stored
            return self.execute(context)
        self.filepath = _default_filepath(self._resolve_action(context))
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        armature = context.scene.mtools_anim_armature
        if armature is None or armature.type != 'ARMATURE':
            self.report({'WARNING'}, "Pick an armature to export")
            return {'CANCELLED'}

        action = self._resolve_action(context)
        if action is None:
            self.report({'WARNING'}, "Pick a take (action), or assign one to the armature")
            return {'CANCELLED'}

        if not self.filepath:
            self.report({'WARNING'}, "No export path set")
            return {'CANCELLED'}

        path = _ensure_txt_ext(self.filepath)
        context.scene.mtools_anim_export_path = path  # remember for next time

        try:
            summary = write_animation(context, path, armature, action, context.scene.mtools_anim_mode)
        except Exception as exc:  # surface any sampling/IO failure to the user
            self.report({'ERROR'}, f"Animation export failed: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Exported {summary} to {path}")
        return {'FINISHED'}


classes = [
    MTOOLS_OT_set_anim_export_path,
    MTOOLS_OT_export_animation,
]


def register_props():
    bpy.types.Scene.mtools_anim_mode = bpy.props.EnumProperty(
        name="Mode",
        description="How animation data is written to the file",
        items=MODE_ITEMS,
        default='BAKED',
    )
    bpy.types.Scene.mtools_anim_armature = bpy.props.PointerProperty(
        name="Armature",
        description="Armature whose animation will be exported",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE',
    )
    bpy.types.Scene.mtools_anim_action = bpy.props.PointerProperty(
        name="Take",
        description="Action (take) to export; leave empty to use the armature's active action",
        type=bpy.types.Action,
    )
    bpy.types.Scene.mtools_anim_export_path = bpy.props.StringProperty(
        name="Animation Export Path",
        description="Where 'Export Animation' saves the .txt for this .blend file",
        subtype='FILE_PATH',
        default="",
    )


def unregister_props():
    for attr in ("mtools_anim_export_path", "mtools_anim_action", "mtools_anim_armature", "mtools_anim_mode"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
