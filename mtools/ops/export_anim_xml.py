"""XML `.anim` animation exporter for MTools.

Produces a structured XML animation file:

    <Animation>
      <metadata Name FrameSize LoopMode path EulerOrder CoordSystem Fps/>
      <BoneAnimationData>                       (one per bone)
        <Path>Skeleton.Bones["Name"].AnimatedTransform</Path>
        <transformAnimation>
          <ScaleFloat3Animation>    X/Y/Z segments of Linear/Hermite/step keys
          <RotateFloat3Animation>   X/Y/Z segments (euler angles, radians)
          <TranslateFloat3Animation>X/Y/Z segments
      ...
      <Skeleton RootName scalingRule>           parent-relative rest/bind pose
        <Bones><Bone Name Parent HasSkiningMatrix><Transform>...

Design notes
------------
* Modular: there is one small `build_*` function per XML part, and every tag /
  attribute name lives in the central `TAG` / `ATTR` registries below -- renaming
  a tag or adding a new part is a one-line change.
* `AnimatedTransform` is each bone's LOCAL pose RELATIVE TO ITS REST pose
  (Blender's `pose_bone.matrix_basis`, i.e. the delta the f-curves drive). The
  engine reconstructs the animated local matrix as `restLocal * AnimatedTransform`
  (rest comes from the <Skeleton> block). To switch to the full parent-relative
  pose instead, decompose `conv.matrix(export_anim._local_pose_matrix(pbone))`
  per bone (the key-time sampling in `rotation_segments` shows the pattern).
* Rotation keeps the authored SPARSE keyframes too (Linear/Hermite/step), output
  as euler angles in radians -- no per-frame baking. Euler-XYZ bones relabel their
  euler f-curves exactly under the coordinate conversion (tangents preserved);
  quaternion / axis-angle / non-XYZ-euler bones have no euler curves, so they are
  converted to euler only at their AUTHORED key times. The euler axis order
  permutes with the coordinate remap (e.g. XZY for the engine preset) and is
  written to <metadata EulerOrder>.
* The coordinate-conversion toolkit and the bpy data-access helpers are REUSED
  from the older text exporter (`export_anim`) so there is a single source of
  truth for that maths.
"""

import os
import math
from collections import namedtuple

import bpy
import xml.etree.ElementTree as ET

# Reuse the axis-conversion toolkit + Blender data helpers from the text exporter.
# (Underscore-prefixed names are module-private by convention but importable.)
from . import export_anim


# ════════════════════════════════════════════════════════════════════════════
# NAME REGISTRY  -- the single knob for renaming tags/attributes or adding parts
# ════════════════════════════════════════════════════════════════════════════
# Every builder looks names up here instead of hard-coding strings, so the exact
# (intentionally mixed) casing -- e.g. `stepSegment`, `transformAnimation`,
# `HasSkiningMatrix`, lower-case `path` -- is defined in exactly one place.

TAG = {
    "root":           "Animation",              # single XML root wrapping everything
    "metadata":       "metadata",
    "bone_anim":      "BoneAnimationData",
    "path":           "Path",
    "transform_anim": "transformAnimation",
    "scale_anim":     "ScaleFloat3Animation",
    "rotate_anim":    "RotateFloat3Animation",
    "translate_anim": "TranslateFloat3Animation",
    "x_seg":          "XSegmentsFloat3A",
    "y_seg":          "YSegmentsFloat3A",
    "z_seg":          "ZSegmentsFloat3A",
    "linear_seg":     "LinearSegment",
    "hermite_seg":    "HermiteSegment",
    "step_seg":       "stepSegment",
    "keys":           "keys",
    "linear_key":     "LinearKey",
    "hermite_key":    "HermiteKey",
    "step_key":       "StepKey",
    "skeleton":       "Skeleton",
    "bones":          "Bones",
    "bone":           "Bone",
    "transform":      "Transform",
    "scale":          "Scale",
    "rotate":         "Rotate",
    "translate":      "Translate",
}

ATTR = {
    # <metadata>
    "meta_name":        "Name",
    "meta_frame_size":  "FrameSize",
    "meta_loop_mode":   "LoopMode",
    "meta_path":        "path",
    "meta_euler_order": "EulerOrder",
    "meta_coord":       "CoordSystem",
    "meta_fps":         "Fps",
    # keys
    "frame":            "Frame",
    "value":            "Value",
    "in_slope":         "InSlope",
    "out_slope":        "OutSlope",
    # <Skeleton> / <Bone>
    "skel_root":        "RootName",
    "skel_scaling":     "scalingRule",
    "bone_name":        "Name",
    "bone_parent":      "Parent",
    "bone_skin":        "HasSkiningMatrix",
    # <Scale>/<Rotate>/<Translate> component values
    "vx":               "x",
    "vy":               "y",
    "vz":               "z",
}

# Path text template for each bone's animated-transform channel.
PATH_TEMPLATE = 'Skeleton.Bones["{bone}"].AnimatedTransform'

# Default value of the <Skeleton scalingRule="..."> attribute.
ROOT_SCALING_RULE = "Standard"

# How booleans are written as attribute text (e.g. HasSkiningMatrix).
BOOL_STR = {True: "true", False: "false"}

# Numeric attribute format (fixed 6-decimal). Frames are handled by `_fmt_frame`.
NUM_FMT = "{:.6f}"


# ════════════════════════════════════════════════════════════════════════════
# SEGMENT / KEY TYPES  -- internal kinds mapped to the XML tags above
# ════════════════════════════════════════════════════════════════════════════
STEP, LINEAR, HERMITE = "STEP", "LINEAR", "HERMITE"

# Segment kind -> TAG key for its wrapping element and its key element. Adding a
# new segment kind is: a new constant, one entry in each of these two maps, and
# one branch in `build_key` / `_make_segment`.
SEGMENT_TAG_KEY = {STEP: "step_seg",  LINEAR: "linear_seg",  HERMITE: "hermite_seg"}
KEY_TAG_KEY     = {STEP: "step_key",  LINEAR: "linear_key",  HERMITE: "hermite_key"}

# The three axis-segments wrappers, indexed 0=X, 1=Y, 2=Z.
AXIS_SEG_KEY = ["x_seg", "y_seg", "z_seg"]

# Blender's f-curve interpolation mode (stored on the LEFT key of an interval) ->
# our coarse segment kind. CONSTANT holds its value; LINEAR is a straight line;
# BEZIER becomes a Hermite segment (slopes derived from the bezier handles).
INTERP_TO_SEGMENT = {
    'CONSTANT': STEP,
    'LINEAR':   LINEAR,
    'BEZIER':   HERMITE,
}
# Eased interpolations (SINE/QUAD/.../ELASTIC) have no simple slope form; each
# such interval is SAMPLED to per-frame linear keys so a linear engine evaluator
# follows the eased shape closely.
_SAMPLED_INTERP = {
    'SINE', 'QUAD', 'CUBIC', 'QUART', 'QUINT',
    'EXPO', 'CIRC', 'BACK', 'BOUNCE', 'ELASTIC',
}


def _interval_type(interp):
    """Coarse segment kind for a Blender interpolation mode.

    LINEAR covers both true-linear intervals and eased intervals; the eased ones
    are turned into extra sampled keys inside `_make_segment`.
    """
    return INTERP_TO_SEGMENT.get(interp, LINEAR)


# ════════════════════════════════════════════════════════════════════════════
# COORDINATE / LOOP ENUMS  (shared by the FBX and animation exporters)
# ════════════════════════════════════════════════════════════════════════════
# A remap is three signed axis tokens naming the signed Blender axis that becomes
# target X, Y, Z -- see export_anim.build_conversion_matrix. 'X Z -Y' turns
# Blender's Z-up right-handed space into the engine's X+ right / Y+ up / Z- forward.
COORD_ITEMS = [
    ('NATIVE', "Blender Native", "Blender's native Z-up right-handed space (no conversion)"),
    ('ENGINE', "Custom Engine",  "Engine: X+ right, Y+ up, Z- forward (remap 'X Z -Y')"),
    ('CUSTOM', "Custom Input",   "Use the Axis Remap field to define the conversion yourself"),
]
CONVERSIONS = {
    'NATIVE': "X Y Z",   # identity
    'ENGINE': "X Z -Y",  # Blender Z-up RH -> engine Y-up RH  (x, y, z) -> (x, z, -y)
}

LOOP_ITEMS = [
    ('ONCE',     "Once",      "Play once and stop"),
    ('LOOP',     "Loop",      "Loop continuously"),
    ('PINGPONG', "Ping Pong", "Play forward then backward"),
]


def resolve_export_remap(scene):
    """Turn the shared coord enum + custom field into (coord_label, remap_string)."""
    coord = scene.mtools_export_coord
    if coord == 'CUSTOM':
        return coord, scene.mtools_export_coord_remap
    return coord, CONVERSIONS[coord]


def rotation_axis_map(conv):
    """Per-target-axis (blender_axis, sign) for ROTATION channels.

    Same relabel as translation, but each sign is multiplied by det(C): conjugating
    a rotation by the basis change C sends a rotation about blender axis `a` to a
    rotation about axis `C*a` by the same angle, with an extra global sign flip when
    C flips handedness (det == -1). For the built-in remaps det == +1.
    """
    det = round(conv.C.determinant())
    return [(src, sign * det) for (src, sign) in conv.axis_map]


def permuted_euler_order(conv, source_order='XYZ'):
    """The euler axis order after the coordinate remap (e.g. 'XYZ' -> 'XZY').

    conv.axis_map says which blender axis feeds each target axis; we relabel the
    source order letters accordingly. The result is one of Blender's 6 valid euler
    orders because the remap is a signed permutation.
    """
    sigma = {}
    for tgt, (src, _sign) in enumerate(conv.axis_map):
        sigma['XYZ'[src]] = 'XYZ'[tgt]
    return ''.join(sigma[ch] for ch in source_order)


# ════════════════════════════════════════════════════════════════════════════
# SMALL VALUE TYPES + FORMATTERS
# ════════════════════════════════════════════════════════════════════════════
# One key. `in_slope`/`out_slope` are only used by Hermite keys (None otherwise).
Key = namedtuple("Key", ["frame", "value", "in_slope", "out_slope"])
Key.__new__.__defaults__ = (None, None)

# One segment: a kind (STEP/LINEAR/HERMITE) plus its list of Keys.
Segment = namedtuple("Segment", ["type", "keys"])


def _fmt_num(v):
    """Format a value/slope attribute (fixed decimals)."""
    return NUM_FMT.format(v)


def _fmt_frame(f):
    """Format a frame attribute -- whole numbers print as ints (e.g. "200")."""
    if abs(f - round(f)) < 1e-6:
        return str(int(round(f)))
    return NUM_FMT.format(f)


# ════════════════════════════════════════════════════════════════════════════
# PATH HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _default_filepath(action):
    """Suggest a path next to the current .blend, named after the take."""
    blend = bpy.data.filepath                     # "" until the .blend is saved
    take = action.name if action else "animation"
    if blend:
        return os.path.join(os.path.dirname(blend), take + ".anim")
    return "//" + take + ".anim"                  # Blender-relative fallback


def _ensure_anim_ext(path):
    """Force a .anim extension."""
    if path and not path.lower().endswith(".anim"):
        path += ".anim"
    return path


# ════════════════════════════════════════════════════════════════════════════
# DATA EXTRACTION
# ════════════════════════════════════════════════════════════════════════════

def resolve_frame_range(action):
    """Integer (start, end) from the action's own frame range."""
    # action.frame_range is a Vector(float, float) of the action's extent.
    start, end = (int(round(v)) for v in action.frame_range)
    return start, end


def group_fcurves_by_bone(action, anim_data, index_of):
    """Group the take's bone f-curves: {bone_name: {(prop, array_index): fcurve}}.

    Reuses export_anim._action_fcurves (which transparently handles both the
    legacy flat `action.fcurves` and Blender 4.4+ slotted layers) and the
    bone-path regex. F-curves for objects/bones outside this armature are skipped.
    """
    by_bone = {}
    for fc in export_anim._action_fcurves(action, anim_data):
        m = export_anim._BONE_PATH_RE.match(fc.data_path)   # pose.bones["X"].prop
        if not m:
            continue                                        # object-level channel
        bone_name, prop = m.group(1), m.group(2)
        if bone_name in index_of:
            by_bone.setdefault(bone_name, {})[(prop, fc.array_index)] = fc
    return by_bone


def hermite_slopes(kp, value_sign):
    """Bezier keyframe handles -> Hermite (InSlope, OutSlope), value per frame.

    Blender stores handles as ABSOLUTE (frame, value) control points on `kp`:
    `kp.co` is the key, `kp.handle_left` / `kp.handle_right` the two handles, each
    exposing `.x` (frame/time axis) and `.y` (value axis). The slope is the value
    change per frame:
        InSlope  = (co.y - handle_left.y)  / (co.x - handle_left.x)
        OutSlope = (handle_right.y - co.y) / (handle_right.x - co.x)
    Only the value axis carries the coordinate sign, so slopes scale by
    `value_sign`; the frame axis is never touched. A near-vertical/coincident
    handle (~0 denominator) is flattened to slope 0.

    Note: Blender beziers are weighted (handle length shapes the curve) while a
    plain Hermite uses only endpoint slopes -- so a segment's INTERIOR may differ
    slightly, but the endpoint value and slope are reproduced exactly.
    """
    EPS = 1e-9
    dxl = kp.co.x - kp.handle_left.x
    dyl = kp.co.y - kp.handle_left.y
    in_slope = (dyl / dxl) if abs(dxl) > EPS else 0.0
    dxr = kp.handle_right.x - kp.co.x
    dyr = kp.handle_right.y - kp.co.y
    out_slope = (dyr / dxr) if abs(dxr) > EPS else 0.0
    return in_slope * value_sign, out_slope * value_sign


def _make_segment(fcurve, kps, start, end, seg_type, value_sign):
    """Build one Segment covering keyframe indices `start`..`end` (inclusive).

    `value_sign` applies the coordinate sign to the value axis (never the frame).
    """
    if seg_type == HERMITE:
        keys = []
        for i in range(start, end + 1):
            kp = kps[i]
            in_s, out_s = hermite_slopes(kp, value_sign)
            keys.append(Key(kp.co.x, kp.co.y * value_sign, in_s, out_s))
        return Segment(HERMITE, keys)

    if seg_type == STEP:
        keys = [Key(kps[i].co.x, kps[i].co.y * value_sign) for i in range(start, end + 1)]
        return Segment(STEP, keys)

    # LINEAR: emit the start key, then walk each interval to the end key. An eased
    # interval gets extra per-frame samples so the straight-line engine follows it.
    keys = [Key(kps[start].co.x, kps[start].co.y * value_sign)]
    for i in range(start, end):
        left, right = kps[i], kps[i + 1]
        if left.interpolation in _SAMPLED_INTERP:
            lo = int(math.floor(left.co.x)) + 1
            hi = int(math.ceil(right.co.x)) - 1
            for f in range(lo, hi + 1):
                if left.co.x < f < right.co.x:
                    # fcurve.evaluate(frame) returns the curve's value at any frame.
                    keys.append(Key(float(f), fcurve.evaluate(f) * value_sign))
        keys.append(Key(right.co.x, right.co.y * value_sign))
    return Segment(LINEAR, keys)


def segments_from_fcurve(fcurve, value_sign):
    """Split one f-curve into consecutive same-kind Segments.

    Blender stores the interpolation mode on the LEFT key of each interval, and it
    governs the shape up to the NEXT key. We open a new segment whenever the
    interval kind changes and repeat the shared boundary key in both neighbours,
    so every segment is self-contained for the engine.
    """
    kps = fcurve.keyframe_points
    n = len(kps)
    if n == 0:
        return []
    if n == 1:
        # A lone key holds its value -> a single linear key (constant channel).
        return [Segment(LINEAR, [Key(kps[0].co.x, kps[0].co.y * value_sign)])]

    segments = []
    run_start = 0                                   # first key index of the current run
    run_type = _interval_type(kps[0].interpolation)  # kind of interval leaving key 0
    for i in range(1, n - 1):                        # interior keys only
        itype = _interval_type(kps[i].interpolation)
        if itype != run_type:
            segments.append(_make_segment(fcurve, kps, run_start, i, run_type, value_sign))
            run_start = i                           # next run starts AT the shared key
            run_type = itype
    segments.append(_make_segment(fcurve, kps, run_start, n - 1, run_type, value_sign))
    return segments


def _rotation_key_frames(fc_by_key):
    """Sorted union of keyframe frames across all rotation_* channels of a bone."""
    frames = set()
    for (prop, _idx), fc in fc_by_key.items():
        if prop.startswith('rotation'):
            for kp in fc.keyframe_points:
                frames.add(kp.co.x)
    return sorted(frames)


# Half-width (in frames) of the finite difference used to estimate rotation slopes
# for bones we have to sample (see rotation_segments).
_ROT_SLOPE_DELTA = 0.5


def _sample_euler(scene, pbone, conv, order, frame, prev):
    """Euler of the bone's converted local rotation at `frame` (fractional ok).

    `pbone.matrix_basis` is the local delta-from-rest transform; `conv.matrix(...)`
    re-expresses it in engine axes; `Matrix.decompose()` isolates the rotation so
    scale can't skew it; `Quaternion.to_euler(order, prev)` passes the previous
    euler so successive angles stay continuous (no +-2*pi / gimbal jumps).
    """
    whole = int(math.floor(frame))                  # frame_set wants an int frame;
    scene.frame_set(whole, subframe=frame - whole)  # the remainder is the subframe
    _, quat, _ = conv.matrix(pbone.matrix_basis).decompose()
    return quat.to_euler(order, prev) if prev is not None else quat.to_euler(order)


def rotation_segments(scene, pbone, fc_by_key, conv, order, start):
    """[x_segments, y_segments, z_segments] of euler-radian rotation, kept sparse.

    Euler-XYZ bones: relabel their authored `rotation_euler` curves under the
    coordinate conversion -- exact, so the sparse keys and Linear/Hermite/step
    segments (and their tangents) are preserved, just like translate/scale.

    Other bones (quaternion / axis-angle / non-XYZ euler): there are no euler
    curves to relabel, so we convert to euler ONLY at the authored key times (still
    sparse -- never per frame) and give each key a Hermite tangent estimated from a
    central finite difference, so the curve stays smooth (a plain line between keys
    would flatten the rotation's easing).
    """
    if pbone.rotation_mode == 'XYZ':
        return _authored_component_segments(fc_by_key, 'rotation_euler',
                                            rotation_axis_map(conv), apply_sign=True,
                                            default=0.0, start=start)

    key_frames = _rotation_key_frames(fc_by_key)
    if not key_frames:
        # Constant rotation -> one held linear key (nothing to interpolate).
        e = _sample_euler(scene, pbone, conv, order, float(start), None)
        return [[Segment(LINEAR, [Key(float(start), e[axis_i])])] for axis_i in range(3)]

    # Sample each key AND its two finite-difference neighbours, in frame order, so
    # euler continuity (prev) is threaded through the whole sequence exactly once.
    d = _ROT_SLOPE_DELTA
    frames = sorted({f + off for f in key_frames for off in (-d, 0.0, d)})
    euler_at = {}
    prev = None
    for f in frames:
        prev = _sample_euler(scene, pbone, conv, order, f, prev)
        euler_at[f] = prev

    axis_keys = ([], [], [])
    for f in key_frames:
        here, before, after = euler_at[f], euler_at[f - d], euler_at[f + d]
        for axis_i in range(3):
            slope = (after[axis_i] - before[axis_i]) / (2.0 * d)  # value per frame
            axis_keys[axis_i].append(Key(float(f), here[axis_i], slope, slope))
    return [[Segment(HERMITE, axis_keys[axis_i])] for axis_i in range(3)]


def rest_transform(bone, conv, order):
    """Parent-relative rest (bind) transform in engine space.

    Returns (scale, euler_radians, translate) with the euler in `order` (the same
    permuted order the animation uses). `_local_rest_matrix` gives the data bone's
    parent-relative bind matrix; we convert then decompose it.
    """
    m = conv.matrix(export_anim._local_rest_matrix(bone))
    loc, quat, scale = m.decompose()
    return scale, quat.to_euler(order), loc


def _authored_component_segments(fc_by_key, prop, axis_map, apply_sign, default, start):
    """[x_segs, y_segs, z_segs] for a translate/scale/euler-rotation component.

    For each TARGET axis, `axis_map` gives the source Blender axis + sign (use
    conv.axis_map for translate/scale, rotation_axis_map for rotation). `apply_sign`
    is True for translate/rotation and False for scale (magnitude is sign-free).
    An axis with no f-curve holds `default` -- the neutral value of a delta
    transform (0 for translate/rotation, 1 for scale) -- so the engine always has
    valid data.
    """
    axis_segments = []
    for tgt in range(3):
        src_axis, sign = axis_map[tgt]
        value_sign = sign if apply_sign else 1.0
        fc = fc_by_key.get((prop, src_axis))
        if fc is None or len(fc.keyframe_points) == 0:
            held = default * value_sign
            axis_segments.append([Segment(LINEAR, [Key(float(start), held)])])
        else:
            axis_segments.append(segments_from_fcurve(fc, value_sign))
    return axis_segments


# ════════════════════════════════════════════════════════════════════════════
# XML BUILDERS  (one function per XML part; each returns an ElementTree Element)
# ════════════════════════════════════════════════════════════════════════════

def build_key(seg_type, key):
    """One <LinearKey>/<HermiteKey>/<StepKey> element."""
    el = ET.Element(TAG[KEY_TAG_KEY[seg_type]])
    el.set(ATTR["frame"], _fmt_frame(key.frame))
    el.set(ATTR["value"], _fmt_num(key.value))
    if seg_type == HERMITE:                          # slopes only on Hermite keys
        el.set(ATTR["in_slope"], _fmt_num(key.in_slope))
        el.set(ATTR["out_slope"], _fmt_num(key.out_slope))
    return el


def build_segment(segment):
    """One segment element wrapping a <keys> list of key elements."""
    seg_el = ET.Element(TAG[SEGMENT_TAG_KEY[segment.type]])
    keys_el = ET.SubElement(seg_el, TAG["keys"])
    for key in segment.keys:
        keys_el.append(build_key(segment.type, key))
    return seg_el


def build_axis_segments(axis_index, segments):
    """One <XSegmentsFloat3A>/<Y...>/<Z...> holding this axis's segments."""
    axis_el = ET.Element(TAG[AXIS_SEG_KEY[axis_index]])
    for segment in segments:
        axis_el.append(build_segment(segment))
    return axis_el


def build_component_animation(comp_tag_key, axis_segments_list):
    """One <ScaleFloat3Animation>/<RotateFloat3Animation>/<TranslateFloat3Animation>.

    `axis_segments_list` is [x_segments, y_segments, z_segments].
    """
    comp_el = ET.Element(TAG[comp_tag_key])
    for axis_index, segments in enumerate(axis_segments_list):
        comp_el.append(build_axis_segments(axis_index, segments))
    return comp_el


def build_transform_animation(pbone, fc_by_key, conv, order, scene, start):
    """<transformAnimation> = scale + rotate + translate component animations."""
    trans_anim = ET.Element(TAG["transform_anim"])

    # SCALE: authored curves, axis relabel only (never negated), neutral = 1.
    scale_segs = _authored_component_segments(fc_by_key, 'scale', conv.axis_map,
                                              apply_sign=False, default=1.0, start=start)
    trans_anim.append(build_component_animation("scale_anim", scale_segs))

    # ROTATE: authored sparse euler curves (radians), engine space -- see
    # rotation_segments for the euler-XYZ relabel vs key-time conversion paths.
    rot_segs = rotation_segments(scene, pbone, fc_by_key, conv, order, start)
    trans_anim.append(build_component_animation("rotate_anim", rot_segs))

    # TRANSLATE: authored curves, axis relabel + sign, neutral = 0.
    trans_segs = _authored_component_segments(fc_by_key, 'location', conv.axis_map,
                                              apply_sign=True, default=0.0, start=start)
    trans_anim.append(build_component_animation("translate_anim", trans_segs))
    return trans_anim


def build_path(bone_name):
    """<Path>Skeleton.Bones["Name"].AnimatedTransform</Path>."""
    el = ET.Element(TAG["path"])
    el.text = PATH_TEMPLATE.format(bone=bone_name)
    return el


def build_bone_animation(pbone, fc_by_key, conv, order, scene, start):
    """<BoneAnimationData> = <Path> + <transformAnimation> for one bone."""
    bone_anim = ET.Element(TAG["bone_anim"])
    bone_anim.append(build_path(pbone.name))
    bone_anim.append(build_transform_animation(pbone, fc_by_key, conv, order, scene, start))
    return bone_anim


def _set_xyz(el, vec):
    """Write x/y/z attributes onto a <Scale>/<Rotate>/<Translate> element."""
    el.set(ATTR["vx"], _fmt_num(vec[0]))
    el.set(ATTR["vy"], _fmt_num(vec[1]))
    el.set(ATTR["vz"], _fmt_num(vec[2]))
    return el


def build_transform(scale, euler_rad, translate):
    """<Transform> with child <Scale>/<Rotate>/<Translate> (x/y/z attributes)."""
    tf = ET.Element(TAG["transform"])
    _set_xyz(ET.SubElement(tf, TAG["scale"]), scale)
    _set_xyz(ET.SubElement(tf, TAG["rotate"]), euler_rad)   # radians
    _set_xyz(ET.SubElement(tf, TAG["translate"]), translate)
    return tf


def build_bone(pbone, conv, order):
    """<Bone Name Parent HasSkiningMatrix> + rest <Transform>."""
    bone_el = ET.Element(TAG["bone"])
    bone_el.set(ATTR["bone_name"], pbone.name)
    bone_el.set(ATTR["bone_parent"], pbone.parent.name if pbone.parent else "")
    # data-bone `use_deform` == "this bone drives skinning" -> has a skinning matrix.
    bone_el.set(ATTR["bone_skin"], BOOL_STR[bool(pbone.bone.use_deform)])
    scale, euler, loc = rest_transform(pbone.bone, conv, order)
    bone_el.append(build_transform(scale, euler, loc))
    return bone_el


def build_skeleton(bones, conv, order):
    """<Skeleton RootName scalingRule> containing <Bones> of <Bone>."""
    skel = ET.Element(TAG["skeleton"])
    root_name = next((pb.name for pb in bones if pb.parent is None),
                     bones[0].name if bones else "")
    skel.set(ATTR["skel_root"], root_name)
    skel.set(ATTR["skel_scaling"], ROOT_SCALING_RULE)
    bones_el = ET.SubElement(skel, TAG["bones"])
    for pbone in bones:
        bones_el.append(build_bone(pbone, conv, order))
    return skel


def build_metadata(action, start, end, fps, conv, order, loop_mode, path):
    """<metadata Name FrameSize LoopMode path EulerOrder CoordSystem Fps/>."""
    meta = ET.Element(TAG["metadata"])
    meta.set(ATTR["meta_name"], action.name)
    # FrameSize = clip length in frames (span). Change to end-start+1 for a count.
    meta.set(ATTR["meta_frame_size"], str(end - start))
    meta.set(ATTR["meta_loop_mode"], loop_mode)
    meta.set(ATTR["meta_path"], path)
    meta.set(ATTR["meta_euler_order"], order)
    meta.set(ATTR["meta_coord"], conv.remap)
    meta.set(ATTR["meta_fps"], _fmt_num(fps))
    return meta


# ════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION
# ════════════════════════════════════════════════════════════════════════════

def build_animation_document(context, armature, action, conv, loop_mode, path):
    """Assemble the whole <Animation> tree.

    Returns (root_element, summary, non_euler_bones). Rotation is always written as
    euler radians; Euler-XYZ bones export their curves exactly, while any other
    rotation mode (Quaternion / Axis-Angle / non-XYZ euler) is converted and its
    name collected in `non_euler_bones` so the operator can warn -- the pipeline is
    Euler-XYZ by design, so those usually flag a bone set up wrong.
    """
    scene = context.scene
    bones = export_anim._ordered_bones(armature)             # parents before children
    index_of = {pb.name: i for i, pb in enumerate(bones)}
    start, end = resolve_frame_range(action)
    fps = scene.render.fps / scene.render.fps_base
    order = permuted_euler_order(conv)               # e.g. 'XYZ' native, 'XZY' engine

    non_euler_bones = [pb.name for pb in bones if pb.rotation_mode != 'XYZ']

    root = ET.Element(TAG["root"])
    root.append(build_metadata(action, start, end, fps, conv, order, loop_mode, path))

    # Assign the take so the rig evaluates it while we sample rotation, then
    # restore the previous action + frame afterwards (create anim data if needed).
    anim_data = armature.animation_data or armature.animation_data_create()
    saved_action = anim_data.action
    saved_frame = scene.frame_current
    anim_data.action = action
    try:
        by_bone = group_fcurves_by_bone(action, anim_data, index_of)
        for pbone in bones:
            fc_by_key = by_bone.get(pbone.name, {})
            root.append(build_bone_animation(pbone, fc_by_key, conv, order, scene, start))
    finally:
        anim_data.action = saved_action
        scene.frame_set(saved_frame)

    root.append(build_skeleton(bones, conv, order))
    summary = f"{len(bones)} bones, take '{action.name}', frames {start}-{end}"
    return root, summary, non_euler_bones


def write_animation_xml(context, filepath, armature, action, remap, loop_mode, path):
    """Build the .anim document and write it as pretty-printed UTF-8 XML.

    Kept standalone (independent of the operator/UI) so the format is easy to
    tweak in isolation. `remap` is the resolved axis-remap string; a bad remap
    raises ValueError, surfaced by the operator. Returns (summary, non_euler_bones).
    """
    conv = export_anim.Conversion(remap)
    root, summary, non_euler = build_animation_document(context, armature, action,
                                                        conv, loop_mode, path)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")                    # pretty-print (Python 3.9+)
    tree.write(filepath, encoding="utf-8", xml_declaration=True)
    return summary, non_euler


# ════════════════════════════════════════════════════════════════════════════
# OPERATORS
# ════════════════════════════════════════════════════════════════════════════

class MTOOLS_OT_set_anim_xml_export_path(bpy.types.Operator):
    """Choose and remember the .anim export location for this .blend file"""
    bl_idname = "mtools.set_anim_xml_export_path"
    bl_label = "Set Animation Export Path"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.anim", options={'HIDDEN'})

    def invoke(self, context, event):
        stored = context.scene.mtools_animx_export_path
        self.filepath = stored if stored else _default_filepath(context.scene.mtools_animx_action)
        context.window_manager.fileselect_add(self)   # opens the file browser
        return {'RUNNING_MODAL'}

    def execute(self, context):
        context.scene.mtools_animx_export_path = _ensure_anim_ext(self.filepath)
        self.report({'INFO'}, f"Animation export path set: {context.scene.mtools_animx_export_path}")
        return {'FINISHED'}


class MTOOLS_OT_export_animation_xml(bpy.types.Operator):
    """Export the chosen armature + take to an XML .anim file.
    The first export asks where to save and remembers it for next time."""
    bl_idname = "mtools.export_animation_xml"
    bl_label = "Export Animation"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.anim", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.scene.mtools_animx_armature is not None

    def _resolve_action(self, context):
        """Use the picked take, else the armature's active action."""
        action = context.scene.mtools_animx_action
        if action is not None:
            return action
        armature = context.scene.mtools_animx_armature
        if armature and armature.animation_data:
            return armature.animation_data.action
        return None

    def invoke(self, context, event):
        stored = context.scene.mtools_animx_export_path
        if stored:
            self.filepath = stored
            return self.execute(context)              # path known -> export now
        self.filepath = _default_filepath(self._resolve_action(context))
        context.window_manager.fileselect_add(self)   # first time -> ask for a path
        return {'RUNNING_MODAL'}

    def execute(self, context):
        scene = context.scene
        armature = scene.mtools_animx_armature
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

        path = _ensure_anim_ext(self.filepath)
        scene.mtools_animx_export_path = path          # remember for next time

        _coord, remap = resolve_export_remap(scene)
        loop_mode = scene.mtools_animx_loop_mode
        meta_path = os.path.basename(path)             # <metadata path="...">
        try:
            summary, non_euler = write_animation_xml(context, path, armature, action,
                                                     remap, loop_mode, meta_path)
        except Exception as exc:  # bad remap / sampling / IO -> report to the user
            self.report({'ERROR'}, f"Animation export failed: {exc}")
            return {'CANCELLED'}

        # The pipeline is Euler-XYZ by design; flag any bone that wasn't, since it
        # took the approximate conversion path instead of exact euler curves.
        if non_euler:
            shown = ", ".join(non_euler[:5]) + ("..." if len(non_euler) > 5 else "")
            self.report({'WARNING'}, f"{len(non_euler)} bone(s) not in Euler XYZ mode "
                        f"(set them to Euler XYZ for exact curves): {shown}")

        self.report({'INFO'}, f"Exported {summary} to {path}")
        return {'FINISHED'}


classes = [
    MTOOLS_OT_set_anim_xml_export_path,
    MTOOLS_OT_export_animation_xml,
]


# ════════════════════════════════════════════════════════════════════════════
# REGISTRATION
# ════════════════════════════════════════════════════════════════════════════
# This module owns the animation props AND the SHARED coordinate props (read by
# the FBX exporter too), so the coordinate system in "Extra Options" drives both.

def register_props():
    bpy.types.Scene.mtools_animx_armature = bpy.props.PointerProperty(
        name="Armature",
        description="Armature whose animation will be exported",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE',
    )
    bpy.types.Scene.mtools_animx_action = bpy.props.PointerProperty(
        name="Take",
        description="Action (take) to export; leave empty to use the armature's active action",
        type=bpy.types.Action,
    )
    bpy.types.Scene.mtools_animx_export_path = bpy.props.StringProperty(
        name="Animation Export Path",
        description="Where 'Export Animation' saves the .anim for this .blend file",
        subtype='FILE_PATH',
        default="",
    )
    bpy.types.Scene.mtools_animx_loop_mode = bpy.props.EnumProperty(
        name="Loop Mode",
        description="How the engine should loop this clip (written to <metadata>)",
        items=LOOP_ITEMS,
        default='ONCE',
    )
    # --- shared coordinate props (FBX + animation) ---
    bpy.types.Scene.mtools_export_coord = bpy.props.EnumProperty(
        name="Coordinate System",
        description="Coordinate system the exported data is written in (FBX + animation)",
        items=COORD_ITEMS,
        default='ENGINE',
    )
    bpy.types.Scene.mtools_export_coord_remap = bpy.props.StringProperty(
        name="Axis Remap",
        description="Custom remap: signed Blender axis that becomes target X Y Z (e.g. 'X Z -Y')",
        default="X Z -Y",
    )


def unregister_props():
    for attr in ("mtools_export_coord_remap", "mtools_export_coord",
                 "mtools_animx_loop_mode", "mtools_animx_export_path",
                 "mtools_animx_action", "mtools_animx_armature"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
