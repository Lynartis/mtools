import os
import re
import math
import bpy
from mathutils import Matrix


# ════════════════════════════════════════════════════════════════════════════
# FORMAT
# ════════════════════════════════════════════════════════════════════════════
# Custom plain-text animation format. Sections common to both export modes:
#   [META]    key = value metadata (armature, take, mode, coord, rotation,
#             frame range, fps, counts)
#   [BONES]   bone table: index name parent_index   (parents always first)
#   [REST]    bind pose, parent-relative, one row per bone
#
# Then one mode-specific section:
#   mode=baked      [ANIMATION]  per-frame parent-relative pose (the FINAL pose,
#                   includes constraints/drivers), grouped under "FRAME n".
#   mode=keyframes  [CURVES]     raw fcurve data per bone channel, for engine-
#                   side curve evaluation: co, both handles + handle types,
#                   interpolation, easing, easing params, extrapolation, key
#                   type. Channel values are the pose bone's LOCAL transform
#                   relative to rest (what fcurves store), NOT the composed pose;
#                   reconstruct as REST x TRS(location, rotation, scale).
#
# REST / ANIMATION rotation columns follow the `rotation` toggle:
#   quaternion -> quat(w x y z) only,  euler -> euler_deg(x y z) only (XYZ),
#   both -> both.  loc(x y z) and scale(x y z) are always present.
#
# COORDINATE SPACE
# ----------------
# `coord` selects the output axis convention (see the COORDINATE CONVERSION
# section below). NATIVE is Blender's Z-up right-handed space. TARGET is the
# engine space X+ right / Y+ up / Z- forward (Y-up right-handed, Houdini-like),
# i.e. Blender (x, y, z) -> (x, z, -y). CUSTOM uses `coord_remap`.
#   - BAKED transforms are fully converted (matrix similarity C*M*C^-1).
#   - In KEYFRAMES, location & scale curves ARE converted (the axis remap is a
#     signed permutation, so it is an exact per-channel relabel + sign, no
#     resampling). Rotation curves are NOT converted -- converting a bezier
#     rotation curve needs resampling and loses the authored tangents -- so they
#     stay in native Blender space. Use BAKED mode for target-space rotation.
#
# ════════════════════════════════════════════════════════════════════════════
# DATA ACCESS REFERENCE  (where each datum is read from in bpy)
# ════════════════════════════════════════════════════════════════════════════
#   armature object        context.scene.mtools_anim_armature  (type 'ARMATURE')
#   action / take          armature.animation_data.action  (or picked override)
#   frame range            action.frame_range -> (start, end)
#   fps                    scene.render.fps / scene.render.fps_base
#   bone list + hierarchy  armature.pose.bones ; parent via pbone.parent (by name)
#   rest / bind matrix     bone.matrix_local  (parent-relative via parent.inverted())
#   final posed matrix     pose_bone.matrix   (sampled at scene.frame_set(frame))
#   loc / rot / scale       Matrix.decompose() ; euler via Matrix.to_euler('XYZ')
#   raw curves             action.fcurves  (Blender <= 4.3)
#                          or action.layers->strips->channelbags->fcurves  (4.4+)
#   curve channel id       fcurve.data_path  +  fcurve.array_index
#   keyframes              fcurve.keyframe_points -> kp
#   key value / time       kp.co  (frame, value)
#   bezier handles         kp.handle_left / kp.handle_right  (+ *_type)
#   interpolation          kp.interpolation / kp.easing / kp.amplitude/back/period
#   extrapolation          fcurve.extrapolation
#   keyframe type          kp.type
# ════════════════════════════════════════════════════════════════════════════
FORMAT_VERSION = 3

MODE_ITEMS = [
    ('BAKED', "Baked", "Sample every frame: final parent-relative pose, includes constraints"),
    ('KEYFRAMES', "Keyframes", "Raw fcurve keyframes + handles, for engine-side curve evaluation"),
]

COORD_ITEMS = [
    ('NATIVE', "Native", "Blender's native Z-up right-handed space (no conversion)"),
    ('TARGET', "Target", "Engine space: X+ right, Y+ up, Z- forward (Y-up RH); remap 'X Z -Y'"),
    ('CUSTOM', "Custom", "Use the Axis Remap field to define the conversion yourself"),
]

ROTATION_ITEMS = [
    ('QUATERNION', "Quaternion", "Write quaternion (w x y z) only"),
    ('EULER', "Euler", "Write euler XYZ in degrees only"),
    ('BOTH', "Both", "Write both quaternion and euler"),
]

# Built-in remap presets. A remap is three signed axis tokens naming the signed
# Blender axis that becomes target X, Y, Z (see build_conversion_matrix).
CONVERSIONS = {
    'NATIVE': "X Y Z",   # identity
    'TARGET': "X Z -Y",  # Blender Z-up RH -> engine Y-up RH  (x, y, z) -> (x, z, -y)
}

# pose.bones["Name"].channel  ->  ("Name", "channel"); non-greedy so nested
# paths (e.g. constraints) still resolve the bone name correctly.
_BONE_PATH_RE = re.compile(r'pose\.bones\["(.*?)"\]\.(.+)')


# ════════════════════════════════════════════════════════════════════════════
# COORDINATE CONVERSION  (the axis-conversion toolkit)
# ════════════════════════════════════════════════════════════════════════════
# An axis convention is captured as a basis-change matrix C, built from a remap
# of three signed axis tokens. Token i names the signed Blender axis that becomes
# target axis i, so  C @ blender_vec = target_vec. For axis remaps C is always a
# signed permutation matrix, which is what makes location/scale CURVE conversion
# an exact per-channel relabel-and-sign (no mixing across axes).

_AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}


def _parse_axis_token(token):
    """'X' / '+X' / '-Y' -> (axis_index, sign)."""
    token = token.strip().upper()
    sign = 1.0
    if token.startswith('-'):
        sign, token = -1.0, token[1:]
    elif token.startswith('+'):
        token = token[1:]
    if token not in _AXIS_INDEX:
        raise ValueError(f"bad axis token '{token}' (expected X, Y or Z with optional sign)")
    return _AXIS_INDEX[token], sign


def build_conversion_matrix(remap):
    """Build the 3x3 basis-change matrix C from a remap like 'X Z -Y'.

    Token i is the signed Blender axis that becomes target axis i, so
    C @ blender_vec = target_vec. 'X Z -Y' yields (x, y, z) -> (x, z, -y),
    Blender's Z-up RH to the engine's Y-up RH space.

    The remap must use each Blender axis exactly once (a signed permutation);
    we validate that so a typo can't silently produce a degenerate/shearing
    matrix. |det| == 1 always; det may be -1 for handedness-flipping targets.
    """
    tokens = remap.split()
    if len(tokens) != 3:
        raise ValueError(f"coord remap '{remap}' needs exactly 3 axis tokens, e.g. 'X Z -Y'")
    rows = [[0.0, 0.0, 0.0] for _ in range(3)]
    used = set()
    for i, tok in enumerate(tokens):
        axis, sign = _parse_axis_token(tok)
        if axis in used:
            raise ValueError(f"coord remap '{remap}' reuses Blender axis {'XYZ'[axis]}")
        used.add(axis)
        rows[i][axis] = sign
    C = Matrix(rows)
    if round(abs(C.determinant()), 6) != 1.0:
        raise ValueError(f"coord remap '{remap}' is not a valid axis permutation")
    return C


def convert_point(C, v):
    """Re-express a point/vector in the target basis: C @ v."""
    return C @ v


def convert_matrix(C4, M):
    """Re-express a transform in the target basis: C4 @ M @ C4^-1.

    Valid for world OR parent-relative (local) matrices: if every bone's local
    matrix is converted this way, reconstruction `world = parent_world @ local`
    still holds in target space, and the translation part reduces to C @ t.
    """
    return C4 @ M @ C4.inverted()


def _axis_map_from_matrix(C):
    """For each target axis i, the (blender_axis, sign) feeding it.

    Recovered from the signed-permutation C (each row has one nonzero entry);
    used to convert location/scale curve channels.
    """
    mapping = []
    for i in range(3):
        for j in range(3):
            if abs(C[i][j]) > 0.5:
                mapping.append((j, 1.0 if C[i][j] > 0.0 else -1.0))
                break
    return mapping


class Conversion:
    """Precomputed axis conversion threaded through the export.

    Holds the basis matrix `C`, its 4x4 form + inverse (cached so per-bone
    per-frame conversion is cheap), and the per-target-axis (blender_axis, sign)
    map used for location/scale curves.
    """

    def __init__(self, remap):
        self.remap = remap
        self.C = build_conversion_matrix(remap)
        self.C4 = self.C.to_4x4()
        self._C4inv = self.C4.inverted()
        self.axis_map = _axis_map_from_matrix(self.C)
        self.is_identity = self.remap.split() == ["X", "Y", "Z"]

    def matrix(self, M):
        """C4 @ M @ C4^-1 with a cached inverse (see convert_matrix)."""
        return self.C4 @ M @ self._C4inv


# ════════════════════════════════════════════════════════════════════════════
# TRANSFORM HELPERS
# ════════════════════════════════════════════════════════════════════════════

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


def _transform_legend(rotation):
    """Column legend for a transform row, matching the `rotation` toggle."""
    cols = ["loc(x y z)"]
    if rotation in ('QUATERNION', 'BOTH'):
        cols.append("quat(w x y z)")
    if rotation in ('EULER', 'BOTH'):
        cols.append("euler_deg(x y z)")
    cols.append("scale(x y z)")
    return " ".join(cols)


def _fmt_transform(matrix, conv, rotation):
    """Convert `matrix` into the target basis, decompose, format selected columns.

    Always emits loc and scale. Rotation columns follow `rotation`:
    quaternion (w x y z) and/or euler XYZ in degrees. Euler is taken from the
    converted matrix so it stays consistent with the quaternion.
    """
    m = conv.matrix(matrix)
    loc, quat, scale = m.decompose()
    parts = [f"{loc.x:.6f} {loc.y:.6f} {loc.z:.6f}"]
    if rotation in ('QUATERNION', 'BOTH'):
        parts.append(f"{quat.w:.6f} {quat.x:.6f} {quat.y:.6f} {quat.z:.6f}")
    if rotation in ('EULER', 'BOTH'):
        e = m.to_euler('XYZ')
        parts.append(f"{math.degrees(e.x):.6f} {math.degrees(e.y):.6f} {math.degrees(e.z):.6f}")
    parts.append(f"{scale.x:.6f} {scale.y:.6f} {scale.z:.6f}")
    return " ".join(parts)


# ════════════════════════════════════════════════════════════════════════════
# SECTION WRITERS
# ════════════════════════════════════════════════════════════════════════════

def _common_header(armature, action, bones, index_of, start, end, fps, mode,
                   coord, conv, rotation):
    """[META] + [BONES] + [REST] -- shared by both export modes."""
    lines = [f"# MTools Animation Export v{FORMAT_VERSION}", ""]

    lines.append("# === META ===")
    lines.append("[META]")
    lines.append(f"armature = {armature.name}")
    lines.append(f"take = {action.name}")
    lines.append(f"mode = {mode.lower()}")
    lines.append(f"coord = {coord.lower()}")
    lines.append(f"coord_remap = {conv.remap}")
    lines.append(f"rotation = {rotation.lower()}")
    lines.append("euler_order = XYZ")
    lines.append(f"frame_start = {start}")
    lines.append(f"frame_end = {end}")
    lines.append(f"frame_count = {end - start + 1}")
    lines.append(f"fps = {fps:g}")
    lines.append(f"bone_count = {len(bones)}")
    lines.append("")

    lines.append("# === BONES ===")
    lines.append("[BONES]")
    lines.append("# index name parent_index   (parents always listed before children)")
    for i, pbone in enumerate(bones):
        parent_idx = index_of[pbone.parent.name] if pbone.parent else -1
        lines.append(f"{i} {pbone.name} {parent_idx}")
    lines.append("")

    lines.append("# === REST (parent-relative bind pose) ===")
    lines.append("[REST]")
    lines.append(f"# name {_transform_legend(rotation)}")
    for pbone in bones:
        lines.append(f"{pbone.name} {_fmt_transform(_local_rest_matrix(pbone.bone), conv, rotation)}")
    lines.append("")
    return lines


def _baked_section(scene, bones, start, end, conv, rotation):
    """[ANIMATION]: sample the parent-relative pose at every frame."""
    lines = ["# === ANIMATION (baked, parent-relative pose per frame) ==="]
    lines.append("[ANIMATION]")
    lines.append(f"# per frame: name {_transform_legend(rotation)}")
    for frame in range(start, end + 1):
        scene.frame_set(frame)
        lines.append(f"FRAME {frame}")
        for pbone in bones:
            lines.append(f"{pbone.name} {_fmt_transform(_local_pose_matrix(pbone), conv, rotation)}")
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


def _emit_channel(prop, array_index, unit, fc, value_sign=1.0, to_deg=None):
    """Format one CHANNEL block + its KEY lines.

    `value_sign` multiplies value-axis numbers (value, handle Y, amplitude) --
    used to negate a location channel under axis conversion. `to_deg` converts
    radians->degrees for euler value-axis numbers (defaults from unit=='deg').
    Time-axis numbers (frame, handle X, period) are never touched.
    """
    if to_deg is None:
        to_deg = unit == 'deg'

    def conv(v):
        v = v * value_sign
        return math.degrees(v) if to_deg else v

    kps = fc.keyframe_points
    lines = [f"CHANNEL {prop} {array_index} {unit} {fc.extrapolation} {len(kps)}"]
    for kp in kps:
        frame, value = kp.co
        hl, hr = kp.handle_left, kp.handle_right
        lines.append(
            f"KEY {frame:.6f} {conv(value):.6f} {kp.interpolation} {kp.easing} "
            f"{hl.x:.6f} {conv(hl.y):.6f} {kp.handle_left_type} "
            f"{hr.x:.6f} {conv(hr.y):.6f} {kp.handle_right_type} "
            f"{conv(kp.amplitude):.6f} {kp.back:.6f} {kp.period:.6f} {kp.type}"
        )
    return lines, len(kps)


def _keyframe_section(action, anim_data, bones, index_of, conv):
    """[CURVES]: full raw fcurve data per bone channel.

    Channels are the pose bone's local-to-rest transform (location, rotation_*,
    scale, ...) exactly as authored -- no constraints/drivers. Every field
    needed to rebuild the curve any way is emitted.

    Coordinate conversion: location & scale channels are remapped to the target
    axes (exact -- the axis remap is a signed permutation, so it's a per-channel
    relabel + sign, no resampling). Rotation channels stay in native Blender
    space (converting a bezier rotation curve would need resampling and lose the
    authored tangents); use BAKED mode for target-space rotation.
    """
    by_bone = {}
    for fc in _action_fcurves(action, anim_data):
        m = _BONE_PATH_RE.match(fc.data_path)
        if not m:
            continue  # object-level or non-bone channel
        bone_name, prop = m.group(1), m.group(2)
        if bone_name in index_of:  # ignore curves for bones not in this armature
            by_bone.setdefault(bone_name, []).append((prop, fc))

    lines = ["# === CURVES (keyframes, raw fcurve data) ==="]
    lines.append("[CURVES]")
    lines.append("# BONE index rotation_mode name")
    lines.append("# CHANNEL property array_index unit extrapolation key_count")
    lines.append("# KEY frame value interp easing "
                 "hl_x hl_y hl_type hr_x hr_y hr_type amplitude back period keytype")
    lines.append("# location/scale channels ARE axis-converted (array_index + sign follow "
                 "coord_remap); rotation channels are NOT (use Baked mode for target rotation).")
    lines.append("# unit=deg: value, hl_y, hr_y and amplitude are in degrees; "
                 "frame, hl_x, hr_x and period are in frames")

    animated = channels = keys = 0
    for pbone in bones:
        fcurves = by_bone.get(pbone.name)
        if not fcurves:
            continue
        animated += 1
        lines.append(f"BONE {index_of[pbone.name]} {pbone.rotation_mode} {pbone.name}")

        fc_by_key = {(prop, fc.array_index): fc for prop, fc in fcurves}

        # location & scale: emit in target-axis order with signed conversion.
        # The remap is a permutation, so every existing loc/scale fcurve is
        # emitted exactly once (relabelled to its target array_index).
        for prop in ('location', 'scale'):
            for tgt_axis, (src_axis, sign) in enumerate(conv.axis_map):
                fc = fc_by_key.get((prop, src_axis))
                if fc is None:
                    continue
                s = 1.0 if prop == 'scale' else sign  # never negate scale
                clines, n = _emit_channel(prop, tgt_axis, _CHANNEL_UNIT[prop], fc, value_sign=s)
                lines += clines
                channels += 1
                keys += n

        # everything else (rotation_*, custom props): native, unchanged.
        for prop, fc in sorted(fcurves, key=lambda pf: (pf[0], pf[1].array_index)):
            if prop in ('location', 'scale'):
                continue  # already emitted above
            unit = _CHANNEL_UNIT.get(prop, 'raw')
            clines, n = _emit_channel(prop, fc.array_index, unit, fc)
            lines += clines
            channels += 1
            keys += n

    lines.append("")
    return lines, f"{animated} animated bones, {channels} channels, {keys} keys"


def write_animation(context, filepath, armature, action, mode, coord, remap, rotation):
    """Write `action` on `armature` to the text file.

    Kept as a standalone function so the export format is easy to tweak and
    experiment with independently of the operator/UI plumbing. `coord` is the
    enum label (for [META]); `remap` is the resolved axis-remap string; both
    `mode` and `rotation` are enum labels. Returns a short human-readable
    summary for the status report.
    """
    scene = context.scene
    bones = _ordered_bones(armature)
    index_of = {pbone.name: i for i, pbone in enumerate(bones)}
    start, end = (int(round(v)) for v in action.frame_range)
    fps = scene.render.fps / scene.render.fps_base

    conv = Conversion(remap)  # raises ValueError on a bad remap (surfaced by the operator)

    lines = _common_header(armature, action, bones, index_of, start, end, fps,
                           mode, coord, conv, rotation)

    # Assign the chosen take so baked sampling poses the rig and keyframe mode
    # can resolve which action slot the armature uses; restore state after.
    anim_data = armature.animation_data or armature.animation_data_create()
    saved_action = anim_data.action
    saved_frame = scene.frame_current
    anim_data.action = action
    try:
        if mode == 'KEYFRAMES':
            section, summary = _keyframe_section(action, anim_data, bones, index_of, conv)
        else:
            section, summary = _baked_section(scene, bones, start, end, conv, rotation)
        lines += section
    finally:
        anim_data.action = saved_action
        scene.frame_set(saved_frame)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return summary


# ════════════════════════════════════════════════════════════════════════════
# OPERATORS
# ════════════════════════════════════════════════════════════════════════════

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

    @staticmethod
    def _resolve_remap(scene):
        """Resolve the coord enum + custom field to a concrete remap string."""
        coord = scene.mtools_anim_coord
        if coord == 'CUSTOM':
            return coord, scene.mtools_anim_coord_remap
        return coord, CONVERSIONS[coord]

    def invoke(self, context, event):
        stored = context.scene.mtools_anim_export_path
        if stored:
            self.filepath = stored
            return self.execute(context)
        self.filepath = _default_filepath(self._resolve_action(context))
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        scene = context.scene
        armature = scene.mtools_anim_armature
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
        scene.mtools_anim_export_path = path  # remember for next time

        coord, remap = self._resolve_remap(scene)
        try:
            summary = write_animation(context, path, armature, action,
                                      scene.mtools_anim_mode, coord, remap,
                                      scene.mtools_anim_rotation)
        except Exception as exc:  # surface bad remap / sampling / IO failure to the user
            self.report({'ERROR'}, f"Animation export failed: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Exported {summary} to {path}")
        return {'FINISHED'}


classes = [
    MTOOLS_OT_set_anim_export_path,
    MTOOLS_OT_export_animation,
]


# ════════════════════════════════════════════════════════════════════════════
# REGISTRATION
# ════════════════════════════════════════════════════════════════════════════

def register_props():
    bpy.types.Scene.mtools_anim_mode = bpy.props.EnumProperty(
        name="Mode",
        description="How animation data is written to the file",
        items=MODE_ITEMS,
        default='BAKED',
    )
    bpy.types.Scene.mtools_anim_coord = bpy.props.EnumProperty(
        name="Coords",
        description="Coordinate system the exported transforms are written in",
        items=COORD_ITEMS,
        default='TARGET',
    )
    bpy.types.Scene.mtools_anim_coord_remap = bpy.props.StringProperty(
        name="Axis Remap",
        description="Custom remap: signed Blender axis that becomes target X Y Z (e.g. 'X Z -Y')",
        default="X Z -Y",
    )
    bpy.types.Scene.mtools_anim_rotation = bpy.props.EnumProperty(
        name="Rotation",
        description="Rotation representation written for baked/rest transforms",
        items=ROTATION_ITEMS,
        default='BOTH',
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
    for attr in ("mtools_anim_export_path", "mtools_anim_action", "mtools_anim_armature",
                 "mtools_anim_rotation", "mtools_anim_coord_remap", "mtools_anim_coord",
                 "mtools_anim_mode"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
