"""
coordinates.py - The ONE place where coordinate-system conversion lives.
=========================================================================

This is the hardest part of the exporter, so it is isolated here and heavily
commented. Everything else (FBX export, .anim export) asks this module to do
the axis math; nothing else re-derives it.

--------------------------------------------------------------------------
The two coordinate systems
--------------------------------------------------------------------------
Blender  : right-handed, +Z up,   -Y forward, +X right.
Engine   : right-handed, +Y up,   -Z forward, +X right.  (like glTF / OpenGL)

To send Blender data to the engine we relabel axes. We describe that relabel
with a short "remap" string of three signed tokens, e.g.  "X Z -Y".

    remap = "X Z -Y"   means:
        engine.x =  blender.x     (token 0 -> engine axis 0)
        engine.y =  blender.z     (token 1 -> engine axis 1)
        engine.z = -blender.y     (token 2 -> engine axis 2)

So token i is "the signed Blender axis that becomes engine axis i".
The matrix C built from a remap satisfies:   C @ blender_vec = engine_vec.

C is always a "signed permutation matrix": exactly one non-zero (+1 or -1)
per row and per column, so |det(C)| == 1.
    det(C) == +1  -> a pure rotation      (no mirroring, keeps handedness)
    det(C) == -1  -> a mirror/flip        (handedness swap - usually unwanted)

--------------------------------------------------------------------------
Why "X Z -Y" is the default ENGINE remap
--------------------------------------------------------------------------
    right   : blender +X  ->  engine +X   (token 0 = "X")
    up      : blender +Z  ->  engine +Y   (token 1 = "Z")
    forward : blender -Y  ->  engine -Z   ->  blender +Y -> engine +Z
              (token 2 = "-Y")
det("X Z -Y") == +1, so the mesh is rotated, never mirrored.

NOTE on facing direction: whether the character ends up facing +Z or -Z in the
engine depends on the engine's own convention. If it comes in backwards, that
is a *debugging* concern - flip it by choosing a different remap in the panel
(e.g. "-X Z Y"), which is exactly why the coordinate system is user-selectable.
"""

# ---------------------------------------------------------------------------
# Presets exposed in the UI. NATIVE = keep Blender axes (no conversion).
# ENGINE = the default target for the custom engine. CUSTOM = user free text.
# ---------------------------------------------------------------------------
PRESETS = {
    "NATIVE": "X Y Z",   # identity - useful to compare against raw Blender data
    "ENGINE": "X Z -Y",  # Blender (Z-up) -> engine (Y-up), proper rotation
}

# EnumProperty items for the UI coordinate selectors (plain data, no bpy needed).
# The identifiers line up with PRESETS keys plus "CUSTOM" (free-text remap).
COORD_ITEMS = [
    ("ENGINE", "Engine (Y-up)", "X+ right, Y+ up, Z- forward  (remap 'X Z -Y')"),
    ("NATIVE", "Blender (Z-up)", "Keep Blender axes unchanged  (remap 'X Y Z')"),
    ("CUSTOM", "Custom", "Use the custom remap text field below"),
]

# Map an axis letter to its column index in a Blender (x, y, z) vector.
_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}
_INDEX_AXIS = ("X", "Y", "Z")


# ===========================================================================
# Parsing a remap string into a 3x3 matrix (pure Python - no Blender needed)
# ===========================================================================
def parse_remap(remap):
    """
    Turn a remap string like "X Z -Y" into a list of (axis_index, sign) tuples,
    one per engine axis (row).

    "X Z -Y" -> [(0, +1), (2, +1), (1, -1)]
        engine.x = +blender[0], engine.y = +blender[2], engine.z = -blender[1]
    """
    tokens = remap.replace(",", " ").split()
    if len(tokens) != 3:
        raise ValueError("Remap must have 3 axis tokens, got: %r" % remap)

    parsed = []
    for tok in tokens:
        tok = tok.strip().upper()
        sign = -1 if tok.startswith("-") else 1     # leading '-' means negate
        letter = tok.lstrip("+-")                   # drop the sign, keep letter
        if letter not in _AXIS_INDEX:
            raise ValueError("Bad axis token %r in remap %r" % (tok, remap))
        parsed.append((_AXIS_INDEX[letter], sign))
    return parsed


def remap_to_matrix(remap):
    """
    Build the 3x3 signed-permutation matrix C (list of rows) from a remap.
    C satisfies:  engine_vec = C @ blender_vec.

    "X Z -Y" -> [[1, 0,  0],
                 [0, 0,  1],
                 [0,-1,  0]]
    """
    parsed = parse_remap(remap)
    matrix = [[0.0, 0.0, 0.0] for _ in range(3)]    # start with zeros
    for row, (axis_index, sign) in enumerate(parsed):
        matrix[row][axis_index] = float(sign)       # one non-zero per row
    return matrix


def matrix_determinant(matrix):
    """Determinant of a 3x3 matrix (used to validate / detect mirroring)."""
    m = matrix
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def validate_remap(remap):
    """Raise if the remap is not a valid signed permutation (|det| must be 1)."""
    det = matrix_determinant(remap_to_matrix(remap))
    if abs(abs(det) - 1.0) > 1e-6:
        raise ValueError("Remap %r is not a signed permutation (det=%s)"
                         % (remap, det))
    return det


def resolve_remap(preset, custom):
    """
    Turn a UI choice into a remap string.
        preset == "CUSTOM" -> use the custom text field.
        otherwise          -> look the preset up in PRESETS.
    """
    if preset == "CUSTOM":
        return custom.strip()
    return PRESETS[preset]


# ===========================================================================
# Converting positions and scales (exact, per-channel - never sampled)
# ===========================================================================
def convert_point(vec3, remap):
    """
    Convert a position/translation:  engine_vec = C @ blender_vec.
    Signs matter here (a point at blender +Y may move to engine -Z, etc.).
    """
    C = remap_to_matrix(remap)
    x, y, z = vec3
    return (
        C[0][0] * x + C[0][1] * y + C[0][2] * z,
        C[1][0] * x + C[1][1] * y + C[1][2] * z,
        C[2][0] * x + C[2][1] * y + C[2][2] * z,
    )


def convert_scale(vec3, remap):
    """
    Convert a scale. Scale is a magnitude per axis, so we permute the axes but
    IGNORE the sign (a negative scale would mean a mirror, not a relabel).
    """
    parsed = parse_remap(remap)                     # [(axis_index, sign), ...]
    return tuple(vec3[axis_index] for axis_index, _sign in parsed)


# ===========================================================================
# Converting ROTATION channels without sampling (Euler-XYZ curves only)
# ===========================================================================
# A signed permutation C acts on a rotation R as a similarity transform
# C @ R @ C^-1. For elementary rotations this just relabels which axis each
# Euler angle turns about, flips some signs, and reorders the Euler sequence.
# That means we can convert Euler-XYZ curves EXACTLY by relabel + sign, which
# keeps every keyframe and every Hermite tangent intact (no resampling).
#
# The one subtlety: a rotation is an *axial* (pseudo) vector, so under a mirror
# (det == -1) its sign gets an extra flip. We fold det(C) into the sign factor.
# ===========================================================================
def rotation_channel_map(remap):
    """
    For each ENGINE rotation axis (X, Y, Z) return (blender_axis_index, factor):
    engine.rot[axis] = factor * blender.rot[blender_axis_index].

    For "X Z -Y" (det = +1):
        engine.rotX = +blender.rotX
        engine.rotY = +blender.rotZ
        engine.rotZ = -blender.rotY
    """
    C = remap_to_matrix(remap)
    det = matrix_determinant(C)
    result = []
    for row in range(3):                            # row = engine axis
        for col in range(3):                        # col = blender axis
            if C[row][col] != 0.0:
                # Axial-vector sign = geometric sign * det(C).
                result.append((col, C[row][col] * det))
                break
    return result


def permuted_euler_order(remap, base_order="XYZ"):
    """
    A Blender bone in Euler '<base_order>' turns about its axes in that order.
    After the remap those axes become different engine axes, so the *order*
    string the engine must replay changes too.

        permuted_euler_order("X Z -Y", "XYZ") == "XZY"

    The engine reads this from <metadata EulerOrder="..."> and applies the
    X/Y/Z angle channels in that sequence.
    """
    C = remap_to_matrix(remap)
    letters = []
    for src_letter in base_order:                   # walk blender axes in order
        src_col = _AXIS_INDEX[src_letter]
        for row in range(3):                        # find the engine axis it maps to
            if C[row][src_col] != 0.0:
                letters.append(_INDEX_AXIS[row])
                break
    return "".join(letters)


def convert_euler_keys(euler_xyz_channels, remap):
    """
    Relabel three Blender Euler-XYZ value channels into engine X/Y/Z channels.

    `euler_xyz_channels` is (x_values, y_values, z_values) - any per-key data
    that scales linearly (key values AND Hermite slopes both qualify, because
    the frame axis is untouched).

    Returns (engine_x, engine_y, engine_z) where each is the relabelled &
    sign-flipped source channel. Use permuted_euler_order() for the order tag.
    """
    channel_map = rotation_channel_map(remap)       # per engine axis: (src, factor)
    out = []
    for src_index, factor in channel_map:
        src = euler_xyz_channels[src_index]
        out.append([factor * v for v in src])
    return tuple(out)


# ===========================================================================
# Converting rotation by SAMPLING (quaternion / non-XYZ bones)
# ===========================================================================
def convert_rotation_matrix(rot3, remap):
    """
    Convert a rotation from Blender space to engine space via the similarity
    transform  C @ R @ C^-1  (C^-1 == C^T for a signed permutation).

    `rot3` is a 3x3 mathutils.Matrix (extract it with `.to_3x3()` on a bone
    matrix, or `quaternion.to_matrix()`). Returns a 3x3 mathutils.Matrix in
    engine space; the caller extracts angles with
    `.to_euler(permuted_euler_order(remap))`.

    This is only used for bones we cannot convert by relabel (quaternion or a
    non-XYZ Euler order), so it lives behind the sampling path and needs
    Blender's mathutils. Everything above is pure Python and unit-testable.
    """
    from mathutils import Matrix                    # Blender-only; imported late

    C3 = remap_to_matrix(remap)
    C = Matrix((C3[0], C3[1], C3[2]))               # plain rows -> mathutils 3x3
    return C @ rot3 @ C.inverted()


# ===========================================================================
# Mapping a remap to Blender's FBX exporter axis options
# ===========================================================================
def remap_to_fbx_axes(remap):
    """
    Blender's FBX exporter does not take a matrix; it takes an `axis_forward`
    and `axis_up` (each one of '+X -X +Y -Y +Z -Z', written as 'X','-X',...).

    We search all forward/up combinations, build the matrix Blender would use
    (bpy_extras.io_utils.axis_conversion), and return the pair whose matrix
    equals our remap matrix C. Returns (axis_forward, axis_up) or None if the
    target cannot be expressed this way (e.g. a mirrored remap).
    """
    from bpy_extras.io_utils import axis_conversion  # Blender-only; late import

    target = remap_to_matrix(remap)
    options = ("X", "Y", "Z", "-X", "-Y", "-Z")
    for forward in options:
        for up in options:
            if forward[-1] == up[-1]:               # forward and up can't share an axis
                continue
            try:
                conv = axis_conversion(to_forward=forward, to_up=up).to_3x3()
            except Exception:
                continue
            if _matrix_close(conv, target):
                return (forward, up)
    return None


def _matrix_close(mathutils_mat, plain_mat, tol=1e-5):
    """Compare a mathutils 3x3 matrix with our plain list-of-rows matrix."""
    for r in range(3):
        for c in range(3):
            if abs(mathutils_mat[r][c] - plain_mat[r][c]) > tol:
                return False
    return True
