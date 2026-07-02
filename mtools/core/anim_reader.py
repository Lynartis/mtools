"""
anim_reader.py - Read a Blender Action into a neutral animation data model.
==========================================================================

This module knows about Blender (bpy / mathutils) but NOTHING about XML. It
turns an armature + action into plain data classes (Keys, Segments, Bones)
that anim_xml.py later renders. Keeping the two apart means the tricky curve
reading and the XML formatting can each be understood on their own.

Interpolation mapping (matches how the artist works in Blender):
    LINEAR  keyframe (the common default)     -> LinearSegment
    BEZIER  keyframe (curved / tweaked)       -> HermiteSegment  (slopes from handles)
    CONSTANT keyframe (step, rarely used)     -> stepSegment
    eased (SINE/QUAD/BACK/BOUNCE/ELASTIC ...) -> sparse HermiteSegment (slopes sampled
                                                 at the keys only - never per-frame)

Keys stay SPARSE. We never write one key per frame: Euler-XYZ curves are
relabelled key-for-key, and Quaternion / non-XYZ-Euler bones are converted to
Euler only at their existing keyframe times - each converted key keeping its
source Bezier/Linear/Constant kind (Bezier tangents are measured from the real
converted motion).
"""

import re
from dataclasses import dataclass, field

import bpy                                          # Blender data access
from mathutils import Euler, Quaternion            # rotation sampling

from . import coordinates                           # the axis-math module


# ===========================================================================
# Neutral data model - no bpy, no XML. Just numbers.
# ===========================================================================
@dataclass
class Key:
    """One keyframe. Slopes are only set for Hermite keys (else None)."""
    frame: int
    value: float
    in_slope: float = None
    out_slope: float = None


@dataclass
class Segment:
    """A run of keys sharing one interpolation kind: LINEAR / HERMITE / STEP."""
    kind: str
    keys: list


@dataclass
class AxisChannel:
    """The animation of a single axis (one of X / Y / Z) = a list of segments."""
    segments: list = field(default_factory=list)


@dataclass
class ComponentAnim:
    """Scale, Rotate OR Translate animation = three AxisChannels (X, Y, Z)."""
    x: AxisChannel
    y: AxisChannel
    z: AxisChannel


@dataclass
class BoneAnim:
    """All animated transform components of one bone (any may be None)."""
    name: str
    scale: ComponentAnim = None
    rotate: ComponentAnim = None
    translate: ComponentAnim = None


@dataclass
class BoneRest:
    """A bone's rest-pose transform (parent-local, already in engine space)."""
    name: str
    parent: str
    has_skin: bool
    scale: tuple
    rotate: tuple      # euler radians, in the exported euler order
    translate: tuple


# ===========================================================================
# Interpolation kinds
# ===========================================================================
# Blender's per-keyframe `interpolation` -> our segment kind. Anything not in
# this table (SINE, QUAD, CUBIC, EXPO, CIRC, BACK, BOUNCE, ELASTIC ...) has no
# Hermite/Step equivalent, so we mark it SAMPLE and bake it to linear keys.
INTERP_TO_KIND = {
    "LINEAR": "LINEAR",
    "CONSTANT": "STEP",
    "BEZIER": "HERMITE",
}


# ===========================================================================
# F-curve discovery (works on legacy AND Blender 4.4+ slotted actions)
# ===========================================================================
def iter_action_fcurves(action):
    """
    Yield every F-curve in an action.

    Blender <=4.3 (and legacy actions in 4.4+) store curves on `action.fcurves`.
    Blender 4.4+ "slotted" actions store them on layers -> strips -> channelbags.
    We try the legacy list first and fall back to the slotted structure.
    """
    legacy = getattr(action, "fcurves", None)
    if legacy is not None and len(legacy) > 0:
        for fcurve in legacy:
            yield fcurve
        return

    layers = getattr(action, "layers", None)         # slotted actions (4.4+)
    if layers:
        for layer in layers:
            for strip in layer.strips:
                for channelbag in getattr(strip, "channelbags", []):
                    for fcurve in channelbag.fcurves:
                        yield fcurve


# Matches  pose.bones["BoneName"].location / .rotation_euler / .scale  etc.
_BONE_PATH_RE = re.compile(
    r'pose\.bones\["(?P<name>.+?)"\]\.'
    r'(?P<channel>location|rotation_euler|rotation_quaternion|scale)$'
)


def parse_bone_path(data_path):
    """Return (bone_name, channel) for a bone F-curve, or None if not one."""
    match = _BONE_PATH_RE.match(data_path)
    if not match:
        return None
    return match.group("name"), match.group("channel")


# ===========================================================================
# Turning one Blender F-curve into a list of Segments
# ===========================================================================
def hermite_slopes(keyframe):
    """
    Geometric tangent slopes (value-per-frame) from a Bezier keyframe's handles.

        out_slope = (right_handle.y - key.y) / (right_handle.x - key.x)
        in_slope  = (key.y - left_handle.y)  / (key.x - left_handle.x)

    This is the ONE place the Bezier->Hermite convention is defined. If the
    engine expects a different tangent scaling, change it here only.
    """
    kx, ky = keyframe.co
    lx, ly = keyframe.handle_left
    rx, ry = keyframe.handle_right
    in_slope = (ky - ly) / (kx - lx) if (kx - lx) != 0.0 else 0.0
    out_slope = (ry - ky) / (rx - kx) if (rx - kx) != 0.0 else 0.0
    return in_slope, out_slope


def _make_key(keyframe, kind):
    """Build a neutral Key from a Blender keyframe for the given segment kind."""
    frame = int(round(keyframe.co[0]))
    value = float(keyframe.co[1])
    if kind == "HERMITE":
        in_slope, out_slope = hermite_slopes(keyframe)
        return Key(frame, value, in_slope, out_slope)
    return Key(frame, value)                          # LINEAR / STEP carry no slopes


def _finite_diff_slopes(fcurve, frame, step=0.5):
    """
    Estimate a curve's in/out slopes at `frame` by sampling just either side of
    it. Lets an eased interval (Sine/Quad/...) become a Hermite key instead of a
    key on every frame.
    """
    value = fcurve.evaluate(frame)
    in_slope = (value - fcurve.evaluate(frame - step)) / step
    out_slope = (fcurve.evaluate(frame + step) - value) / step
    return in_slope, out_slope


def _warn_once(warnings, message):
    if message not in warnings:
        warnings.append(message)


def segments_from_fcurve(fcurve, warnings):
    """
    Split one F-curve into Segments, grouping consecutive keyframes that share
    an interpolation kind. A keyframe's interpolation governs the interval that
    STARTS at it, so adjacent segments share their boundary key.
    """
    keys = sorted(fcurve.keyframe_points, key=lambda k: k.co[0])
    count = len(keys)
    if count == 0:
        return AxisChannel([])
    if count == 1:                                    # a lone key: emit it as linear
        return AxisChannel([Segment("LINEAR", [_make_key(keys[0], "LINEAR")])])

    segments = []
    i = 0
    while i < count - 1:                              # walk intervals, not keys
        kind = INTERP_TO_KIND.get(keys[i].interpolation, "SAMPLE")

        if kind == "SAMPLE":                          # eased -> sparse Hermite keys
            _warn_once(warnings, "Eased interpolation '%s' on %s approximated as Hermite"
                       % (keys[i].interpolation, fcurve.data_path))
            # Gather the whole run of eased intervals and emit ONE Hermite
            # segment over just their keyframes (tangents sampled at the keys).
            j = i
            while j < count - 1 and INTERP_TO_KIND.get(keys[j].interpolation, "SAMPLE") == "SAMPLE":
                j += 1
            hermite_keys = []
            for t in range(i, j + 1):
                in_slope, out_slope = _finite_diff_slopes(fcurve, keys[t].co[0])
                hermite_keys.append(Key(int(round(keys[t].co[0])), float(keys[t].co[1]),
                                        in_slope, out_slope))
            segments.append(Segment("HERMITE", hermite_keys))
            i = j
            continue

        # Extend the segment while the same kind keeps going.
        j = i
        while j < count - 1 and INTERP_TO_KIND.get(keys[j].interpolation, "SAMPLE") == kind:
            j += 1
        segments.append(Segment(kind, [_make_key(keys[t], kind) for t in range(i, j + 1)]))
        i = j
    return AxisChannel(segments)


# ===========================================================================
# Applying the coordinate remap to a single axis channel
# ===========================================================================
def scale_axis_channel(axis_channel, factor):
    """
    Multiply every value and slope in a channel by `factor` (used to flip a
    channel's sign when the remap negates it). Frames and kinds are untouched,
    so Hermite tangents survive the conversion exactly.
    """
    if factor == 1.0:
        return axis_channel
    out_segments = []
    for seg in axis_channel.segments:
        out_keys = []
        for k in seg.keys:
            in_slope = None if k.in_slope is None else k.in_slope * factor
            out_slope = None if k.out_slope is None else k.out_slope * factor
            out_keys.append(Key(k.frame, k.value * factor, in_slope, out_slope))
        out_segments.append(Segment(seg.kind, out_keys))
    return AxisChannel(out_segments)


def _constant_channel(frame, value):
    """A channel that never moves: one linear key holding a default value."""
    return AxisChannel([Segment("LINEAR", [Key(frame, value)])])


# ===========================================================================
# Building Scale / Translate components (exact per-axis relabel)
# ===========================================================================
def _build_linear_component(channel_fcurves, default, remap, is_scale, frame_start, warnings):
    """
    Build a ComponentAnim for Scale or Translate. Each ENGINE axis maps to
    exactly one Blender axis (signed permutation), so we relabel per axis:
    convert the mapped Blender curve, or fall back to a constant default when
    that axis is not animated.
    """
    if not channel_fcurves:
        return None                                   # component not animated at all

    # parse_remap gives, per engine axis, (blender_axis_index, sign).
    axis_map = coordinates.parse_remap(remap)
    engine_axes = []
    for blender_axis, sign in axis_map:
        factor = 1.0 if is_scale else float(sign)     # scale ignores sign (magnitude)
        fcurve = channel_fcurves.get(blender_axis)
        if fcurve is None:
            engine_axes.append(_constant_channel(frame_start, default))
        else:
            channel = segments_from_fcurve(fcurve, warnings)
            engine_axes.append(scale_axis_channel(channel, factor))
    return ComponentAnim(*engine_axes)


# ===========================================================================
# Building the Rotation component (exact for Euler-XYZ, sampled otherwise)
# ===========================================================================
def _eval_channel(fcurves, index, default, frame):
    """Evaluate one indexed F-curve at a frame, or return a default."""
    fcurve = fcurves.get(index) if fcurves else None
    return float(fcurve.evaluate(frame)) if fcurve else default


def _rotation_keyframe_times(fcurves):
    """Sorted union of integer keyframe frames across a channel dict."""
    times = set()
    if fcurves:
        for fcurve in fcurves.values():
            for keyframe in fcurve.keyframe_points:
                times.add(int(round(keyframe.co[0])))
    return sorted(times)


def _engine_euler_at(euler_fcurves, quat_fcurves, is_quat, rotation_mode,
                     remap, order, frame, compat):
    """
    Evaluate the CONVERTED engine-space Euler at `frame`. `compat` (an Euler
    from a nearby frame, or None) keeps the result continuous so successive
    angles don't jump by +/-360 at gimbal boundaries.
    """
    if is_quat:
        rot3 = Quaternion((
            _eval_channel(quat_fcurves, 0, 1.0, frame),   # W (default 1)
            _eval_channel(quat_fcurves, 1, 0.0, frame),   # X
            _eval_channel(quat_fcurves, 2, 0.0, frame),   # Y
            _eval_channel(quat_fcurves, 3, 0.0, frame),   # Z
        )).to_matrix()
    else:                                              # non-XYZ euler order
        rot3 = Euler((
            _eval_channel(euler_fcurves, 0, 0.0, frame),
            _eval_channel(euler_fcurves, 1, 0.0, frame),
            _eval_channel(euler_fcurves, 2, 0.0, frame),
        ), rotation_mode).to_matrix()
    engine = coordinates.convert_rotation_matrix(rot3, remap)
    return engine.to_euler(order, compat) if compat else engine.to_euler(order)


def _source_interp_kinds(fcurves, frames):
    """
    The segment kind for each keyframe, read from the SOURCE curve's own
    interpolation (Quaternion components are keyed together, so any one is
    representative). Bezier -> HERMITE, Linear -> LINEAR, Constant -> STEP;
    eased types have no exact form so they also become HERMITE (sampled slopes).
    """
    interp_at = {}
    for fcurve in fcurves.values():
        for keyframe in fcurve.keyframe_points:
            interp_at.setdefault(int(round(keyframe.co[0])), keyframe.interpolation)
    return [INTERP_TO_KIND.get(interp_at.get(f, "BEZIER"), "HERMITE") for f in frames]


def _convert_rotation_sparse(euler_fcurves, quat_fcurves, rotation_mode, remap):
    """
    Convert Quaternion (or non-XYZ Euler) rotation to Euler radians at the
    ORIGINAL keyframe times only - never one key per frame. Each keyframe KEEPS
    the interpolation kind of its source curve: Bezier -> Hermite (tangents
    measured from the real converted motion), Linear -> Linear, Constant -> Step.
    Angles are kept continuous across keys to avoid gimbal flips.
    """
    order = coordinates.permuted_euler_order(remap)
    is_quat = rotation_mode == "QUATERNION"
    source = quat_fcurves if is_quat else euler_fcurves
    frames = _rotation_keyframe_times(source)
    if not frames:
        return None

    # Converted Euler at every keyframe, made continuous key-to-key.
    values = []
    previous = None
    for frame in frames:
        euler = _engine_euler_at(euler_fcurves, quat_fcurves, is_quat, rotation_mode,
                                 remap, order, frame, previous)
        values.append(euler)
        previous = euler

    kinds = _source_interp_kinds(source, frames)

    def slopes_at(index, axis, step=0.25):
        """In/out tangents at a keyframe, sampled just either side of it (kept
        continuous with the key's own value) - the real converted-motion slope."""
        frame = frames[index]
        base = values[index]
        value = base[axis]
        before = _engine_euler_at(euler_fcurves, quat_fcurves, is_quat, rotation_mode,
                                  remap, order, frame - step, base)[axis]
        after = _engine_euler_at(euler_fcurves, quat_fcurves, is_quat, rotation_mode,
                                 remap, order, frame + step, base)[axis]
        return (value - before) / step, (after - value) / step

    return ComponentAnim(*[
        _converted_axis_channel(frames, values, kinds, axis, slopes_at)
        for axis in range(3)
    ])


def _converted_axis_channel(frames, values, kinds, axis, slopes_at):
    """
    Build one X/Y/Z channel from converted rotation, grouping keyframes into
    segments of the same interpolation kind (an interval's kind = its left
    keyframe's kind), so the sparse Bezier/Linear/Step structure is preserved.
    """
    count = len(frames)
    if count == 1:
        return AxisChannel([Segment("LINEAR", [Key(frames[0], values[0][axis])])])

    segments = []
    i = 0
    while i < count - 1:
        kind = kinds[i]
        j = i
        while j < count - 1 and kinds[j] == kind:
            j += 1
        keys = []
        for t in range(i, j + 1):
            value = values[t][axis]
            if kind == "HERMITE":
                in_slope, out_slope = slopes_at(t, axis)
                keys.append(Key(frames[t], value, in_slope, out_slope))
            else:
                keys.append(Key(frames[t], value))
        segments.append(Segment(kind, keys))
        i = j
    return AxisChannel(segments)


def _build_rotation_component(channels, rotation_mode, remap, frame_start,
                              warnings, bone_name):
    """
    Rotation is always exported as Euler radians.
      - Euler XYZ bone : relabel the three curves exactly (keeps Hermite/Step).
      - anything else  : convert at existing keyframes, keeping each key's
                         Bezier->Hermite / Linear / Step kind (still sparse).
    """
    euler_fcurves = channels.get("rotation_euler")
    quat_fcurves = channels.get("rotation_quaternion")
    if not euler_fcurves and not quat_fcurves:
        return None                                    # rotation not animated

    if rotation_mode == "XYZ" and euler_fcurves:
        # Exact path: read the three Blender axes, then relabel/sign per remap.
        blender_axes = []
        for axis in range(3):
            fcurve = euler_fcurves.get(axis)
            if fcurve is None:
                blender_axes.append(_constant_channel(frame_start, 0.0))
            else:
                blender_axes.append(segments_from_fcurve(fcurve, warnings))
        # rotation_channel_map: per engine axis -> (blender axis, signed factor).
        engine_axes = [scale_axis_channel(blender_axes[src], factor)
                       for src, factor in coordinates.rotation_channel_map(remap)]
        return ComponentAnim(*engine_axes)

    # Converted path (Quaternion / non-XYZ Euler): sparse keys at keyframe times.
    _warn_once(warnings, "Bone '%s' rotation (%s) converted to Euler %s at its keyframes"
               % (bone_name, rotation_mode, coordinates.permuted_euler_order(remap)))
    return _convert_rotation_sparse(euler_fcurves, quat_fcurves, rotation_mode, remap)


# ===========================================================================
# Ordering bones parents-before-children
# ===========================================================================
def ordered_bone_names(armature):
    """Depth-first bone names so a parent always precedes its children."""
    names = []

    def walk(bone):
        names.append(bone.name)
        for child in bone.children:
            walk(child)

    for bone in armature.data.bones:
        if bone.parent is None:                        # start from each root
            walk(bone)
    return names


# ===========================================================================
# Top-level: read the whole animation and the whole skeleton
# ===========================================================================
def read_animation(armature, action, remap):
    """
    Read `action` on `armature` into a list of BoneAnim (engine coordinates).
    Returns (bone_anims, euler_order, warnings).
    """
    warnings = []
    euler_order = coordinates.permuted_euler_order(remap)
    frame_start = frame_range(action)[0]              # default frame for gap-filling

    # Group every bone F-curve by bone name and channel.
    grouped = {}
    for fcurve in iter_action_fcurves(action):
        parsed = parse_bone_path(fcurve.data_path)
        if not parsed:
            continue                                   # object-level curve, skip
        bone_name, channel = parsed
        by_channel = grouped.setdefault(bone_name, {})
        by_channel.setdefault(channel, {})[fcurve.array_index] = fcurve

    bone_anims = []
    for bone_name in ordered_bone_names(armature):
        channels = grouped.get(bone_name)
        if not channels:
            continue                                   # bone not animated
        pose_bone = armature.pose.bones.get(bone_name)
        rotation_mode = pose_bone.rotation_mode if pose_bone else "XYZ"

        scale = _build_linear_component(channels.get("scale"), 1.0, remap,
                                        True, frame_start, warnings)
        translate = _build_linear_component(channels.get("location"), 0.0, remap,
                                            False, frame_start, warnings)
        rotate = _build_rotation_component(channels, rotation_mode, remap,
                                           frame_start, warnings, bone_name)

        bone_anims.append(BoneAnim(bone_name, scale, rotate, translate))
    return bone_anims, euler_order, warnings


def read_skeleton(armature, remap):
    """
    Read every bone's rest pose (parent-local, converted to engine space) into
    a list of BoneRest, ordered parents-before-children.
    """
    order = coordinates.permuted_euler_order(remap)
    skin_groups = _skinning_group_names(armature)
    bones = armature.data.bones

    skeleton = []
    for bone_name in ordered_bone_names(armature):
        bone = bones[bone_name]
        parent = bone.parent.name if bone.parent else ""

        # Rest transform relative to the parent (Blender space).
        if bone.parent:
            local = bone.parent.matrix_local.inverted() @ bone.matrix_local
        else:
            local = bone.matrix_local.copy()

        # Decompose first, then convert each part into engine space. Decomposing
        # BEFORE conversion keeps scale from corrupting the Euler extraction.
        loc, quat, scl = local.decompose()
        translate = coordinates.convert_point(loc, remap)
        scale = coordinates.convert_scale(scl, remap)
        rotate = tuple(coordinates.convert_rotation_matrix(quat.to_matrix(), remap).to_euler(order))

        has_skin = bool(bone.use_deform) and (bone_name in skin_groups)
        skeleton.append(BoneRest(bone_name, parent, has_skin, scale, rotate, translate))
    return skeleton


def _skinning_group_names(armature):
    """Vertex-group names of meshes skinned to `armature` (for HasSkiningMatrix)."""
    names = set()
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        bound = any(m.type == "ARMATURE" and m.object == armature
                    for m in obj.modifiers)
        if bound:
            names.update(vg.name for vg in obj.vertex_groups)
    return names


def frame_range(action):
    """Integer (start, end) frame range of an action."""
    start, end = action.frame_range
    return int(round(start)), int(round(end))
