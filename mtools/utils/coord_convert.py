"""Coordinate-system & rotation math toolkit  (reference / experimentation).

This module is a STANDALONE library of conversion helpers. Nothing in MTools
imports it -- it is here so you can try different ways of converting axes,
matrices, and rotations if the exporter's built-in conversion doesn't match your
engine. Import it in Blender's Python console and experiment, e.g.:

    from mtools.utils import coord_convert as cc
    C = cc.remap_to_matrix("X Z -Y")          # Blender -> engine basis change
    cc.convert_vector(C, some_vector)
    cc.convert_euler(C, some_euler, 'XYZ')

Conventions used throughout
---------------------------
* A "remap" is three signed axis tokens, e.g. "X Z -Y". Token i names the signed
  SOURCE (Blender) axis that becomes TARGET axis i. So the built matrix C obeys
  `C @ source_vec = target_vec`. "X Z -Y" means (x, y, z) -> (x, z, -y), i.e.
  Blender's Z-up right-handed space to a Y-up right-handed engine space.
* Blender itself is right-handed, Z up, and looks down -Y (forward = -Y).
* A basis change for a pure axis remap is a SIGNED PERMUTATION matrix: orthogonal,
  |det| == 1. det == +1 keeps handedness; det == -1 mirrors it.
* Rotations use radians. Euler order strings are Blender's: one of
  'XYZ','XZY','YXZ','YZX','ZXY','ZYX'.

Everything is built on Blender's `mathutils`, so results match Blender's own
Matrix/Euler/Quaternion behaviour exactly.
"""

import math
from mathutils import Matrix, Vector, Quaternion, Euler


# ════════════════════════════════════════════════════════════════════════════
# 1. AXIS TOKENS  ('X', '+X', '-Y', ...)
# ════════════════════════════════════════════════════════════════════════════

AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}     # letter -> column/row index
AXIS_NAME = ('X', 'Y', 'Z')               # index  -> letter


def parse_axis_token(token):
    """'X' / '+X' / '-Y' -> (axis_index, sign).  e.g. '-Y' -> (1, -1.0)."""
    token = token.strip().upper()
    sign = 1.0
    if token.startswith('-'):
        sign, token = -1.0, token[1:]
    elif token.startswith('+'):
        token = token[1:]
    if token not in AXIS_INDEX:
        raise ValueError(f"bad axis token '{token}' (expected X/Y/Z with optional sign)")
    return AXIS_INDEX[token], sign


def axis_token_to_vector(token):
    """'-Y' -> Vector((0, -1, 0)) -- a signed unit axis."""
    index, sign = parse_axis_token(token)
    v = Vector((0.0, 0.0, 0.0))
    v[index] = sign
    return v


def vector_to_axis_token(v, eps=1e-6):
    """Nearest signed axis token for a (near) axis-aligned vector: (0,0,-1) -> '-Z'.

    Raises if the vector is not close to a single signed axis.
    """
    for i in range(3):
        if abs(abs(v[i]) - 1.0) < eps and all(abs(v[j]) < eps for j in range(3) if j != i):
            return ('-' if v[i] < 0 else '') + AXIS_NAME[i]
    raise ValueError(f"vector {tuple(v)} is not axis-aligned")


# ════════════════════════════════════════════════════════════════════════════
# 2. REMAP STRING  <->  BASIS-CHANGE MATRIX C
# ════════════════════════════════════════════════════════════════════════════

def remap_to_matrix(remap):
    """Build the 3x3 basis-change matrix C from a remap like 'X Z -Y'.

    Token i is the signed source axis that becomes target axis i, so
    `C @ source_vec = target_vec`. The remap must use each source axis exactly
    once (a signed permutation); this is validated so a typo can't silently make a
    shearing/degenerate matrix.
    """
    tokens = remap.split()
    if len(tokens) != 3:
        raise ValueError(f"remap '{remap}' needs exactly 3 axis tokens, e.g. 'X Z -Y'")
    rows = [[0.0, 0.0, 0.0] for _ in range(3)]
    used = set()
    for i, tok in enumerate(tokens):
        axis, sign = parse_axis_token(tok)
        if axis in used:
            raise ValueError(f"remap '{remap}' reuses source axis {AXIS_NAME[axis]}")
        used.add(axis)
        rows[i][axis] = sign
    C = Matrix(rows)
    if round(abs(C.determinant()), 6) != 1.0:
        raise ValueError(f"remap '{remap}' is not a valid axis permutation")
    return C


def matrix_to_remap(C, eps=1e-6):
    """Inverse of remap_to_matrix: a signed-permutation 3x3 -> 'X Z -Y' string."""
    tokens = []
    for i in range(3):                         # row i = target axis i
        for j in range(3):                     # column j = source axis j
            if abs(C[i][j]) > eps:
                tokens.append(('-' if C[i][j] < 0 else '') + AXIS_NAME[j])
                break
        else:
            raise ValueError("matrix is not a signed permutation (empty row)")
    return " ".join(tokens)


def axis_map(C, eps=0.5):
    """Per-target-axis (source_index, sign) that feeds it.

    For a signed permutation each row has one non-zero entry; this recovers it.
    Used to convert location/scale channels one axis at a time (a relabel + sign).
    """
    mapping = []
    for i in range(3):
        for j in range(3):
            if abs(C[i][j]) > eps:
                mapping.append((j, 1.0 if C[i][j] > 0 else -1.0))
                break
    return mapping


# ════════════════════════════════════════════════════════════════════════════
# 3. BASIS FROM FORWARD / UP / RIGHT DIRECTIONS
# ════════════════════════════════════════════════════════════════════════════
# Sometimes it's easier to describe a convention by where "forward" and "up"
# point than by a remap string. These build a basis matrix from directions.

def basis_matrix(right, up, forward):
    """3x3 whose COLUMNS are the right/up/forward axes of a convention.

    If (right, up, forward) are the convention's axes expressed in a shared
    reference frame, then `B @ v_convention = v_reference`, i.e. B maps coordinates
    written in this convention back into the reference frame.
    """
    r, u, f = Vector(right), Vector(up), Vector(forward)
    # columns = axes -> transpose of the row layout
    return Matrix((
        (r.x, u.x, f.x),
        (r.y, u.y, f.y),
        (r.z, u.z, f.z),
    ))


def basis_change(from_forward, from_up, to_forward, to_up):
    """Matrix C converting coordinates FROM one forward/up convention TO another.

    Each argument is an axis token ('X', '-Z', ...). Right is derived as
    forward x up (right-handed). `C @ from_vec = to_vec`. This mirrors the idea
    behind Blender's `bpy_extras.io_utils.axis_conversion`, but is spelled out
    here so you can see and tweak the maths.
    """
    def frame(fwd_tok, up_tok):
        fwd = axis_token_to_vector(fwd_tok)
        up = axis_token_to_vector(up_tok)
        right = fwd.cross(up)                  # right-handed: right = forward x up
        return basis_matrix(right, up, fwd)

    B_from = frame(from_forward, from_up)
    B_to = frame(to_forward, to_up)
    # v_ref = B_from @ v_from  and  v_ref = B_to @ v_to  ->  v_to = B_to^-1 @ B_from @ v_from
    return B_to.inverted() @ B_from


# ════════════════════════════════════════════════════════════════════════════
# 4. NAMED CONVENTIONS  (remap FROM Blender TO the engine)
# ════════════════════════════════════════════════════════════════════════════
# Commonly used engine conventions, as a remap applied to Blender data. These are
# starting points -- ALWAYS confirm against your actual target, especially the
# handedness (det == -1 means the preset mirrors, which also flips winding order).
#
#   det +1 -> right-handed target (no mirror)   det -1 -> left-handed (mirror)
CONVENTION_REMAPS = {
    'BLENDER':     "X Y Z",    # identity: Z up, -Y forward, right-handed
    'GLTF':        "X Z -Y",   # Y up, -Z forward, right-handed (glTF / three.js / OpenGL)
    'OPENGL':      "X Z -Y",   # same as glTF
    'MAYA_YUP':    "X Z -Y",   # Maya Y-up, right-handed
    'UNITY':       "X Z Y",    # Y up, +Z forward, LEFT-handed (det -1, mirrors)
    'UNREAL':      "X -Y Z",   # Z up, +X forward, LEFT-handed (det -1, mirrors)
}


def convention_matrix(name):
    """Basis-change matrix from Blender to a named convention (see CONVENTION_REMAPS)."""
    key = name.strip().upper()
    if key not in CONVENTION_REMAPS:
        raise ValueError(f"unknown convention '{name}' (known: {sorted(CONVENTION_REMAPS)})")
    return remap_to_matrix(CONVENTION_REMAPS[key])


def conversion_between(from_name, to_name):
    """Basis change from one named convention to another (both relative to Blender).

    C = C_to @ C_from^-1, so `C @ from_vec = to_vec`.
    """
    return convention_matrix(to_name) @ convention_matrix(from_name).inverted()


# ════════════════════════════════════════════════════════════════════════════
# 5. HANDEDNESS
# ════════════════════════════════════════════════════════════════════════════

def determinant(M):
    """Determinant of a 3x3 or 4x4 matrix (uses the 3x3 rotation part)."""
    return M.to_3x3().determinant()


def is_right_handed(M):
    """True if the basis keeps right-handedness (det > 0)."""
    return determinant(M) > 0.0


def handedness_sign(M):
    """+1 if right-handed, -1 if the basis mirrors handedness."""
    return 1.0 if determinant(M) > 0.0 else -1.0


def make_handedness_flip(axis='Z'):
    """3x3 that negates one axis (a mirror, det -1), e.g. flip Z for a LH target."""
    index, _sign = parse_axis_token(axis)
    diag = [1.0, 1.0, 1.0]
    diag[index] = -1.0
    return Matrix(((diag[0], 0, 0), (0, diag[1], 0), (0, 0, diag[2])))


# ════════════════════════════════════════════════════════════════════════════
# 6. APPLYING A BASIS CHANGE  (several equivalent ways -- pick what works)
# ════════════════════════════════════════════════════════════════════════════

def convert_vector(C, v):
    """Re-express a point or direction in the target basis: C @ v."""
    return C.to_3x3() @ Vector(v)


def convert_matrix(C, M):
    """Re-express a transform matrix in the target basis: C4 @ M @ C4^-1.

    This SIMILARITY transform is the reference (always-correct) way to convert a
    full transform. Valid for world or parent-relative (local) matrices: if every
    local matrix is converted this way, `world = parent @ local` still holds in the
    target space. Handles any 4x4 M.
    """
    C4 = C.to_4x4()
    return C4 @ M.to_4x4() @ C4.inverted()


def convert_quaternion_via_matrix(C, q):
    """Convert a rotation by going through its matrix: (C R C^-1) as a quaternion.

    The clearest correct method -- identical result to convert_matrix on a pure
    rotation.
    """
    return convert_matrix(C, q.to_matrix().to_4x4()).to_quaternion()


def convert_quaternion_direct(C, q):
    """Convert a rotation by rotating its axis and flipping angle by det(C).

    A rotation about axis n by angle a becomes a rotation about C@n by
    det(C)*a (conjugating by an orthogonal matrix; det -1 flips the angle). Same
    result as convert_quaternion_via_matrix, offered as an alternative to try.
    """
    axis, angle = q.to_axis_angle()
    new_axis = convert_vector(C, axis)
    return Quaternion(new_axis, angle * handedness_sign(C))


def convert_euler(C, euler, order='XYZ'):
    """Convert an Euler rotation; returns (new_euler, new_order).

    Uses the reference matrix method: build the rotation, conjugate it, and read
    it back as an Euler in the PERMUTED order (an axis remap permutes the euler
    order, e.g. 'XYZ' -> 'XZY' under 'X Z -Y'). See also relabel_euler for the
    exact per-channel version that preserves keyframe tangents.
    """
    new_order = permute_euler_order(C, order)
    R = Euler(euler, order).to_matrix().to_4x4()
    return convert_matrix(C, R).to_euler(new_order), new_order


def convert_trs_relabel(C, loc, euler, scale, order='XYZ'):
    """Convert a (location, euler, scale) triple by the fast per-channel RELABEL.

    This is the exact method the animation exporter uses (and which is proven equal
    to convert_matrix): translation and euler channels are relabelled to new axes
    with a sign (euler sign also carries det(C)); scale is relabelled without sign.
    Returns (new_loc, new_euler, new_scale, new_order). Handy to compare against
    convert_matrix(compose_trs(...)).
    """
    amap = axis_map(C)
    det = handedness_sign(C)
    new_loc = Vector((0, 0, 0))
    new_scale = Vector((0, 0, 0))
    new_ang = [0.0, 0.0, 0.0]
    for tgt in range(3):
        src, sign = amap[tgt]
        new_loc[tgt] = sign * loc[src]
        new_scale[tgt] = scale[src]                 # magnitude: never signed
        new_ang[tgt] = sign * det * euler[src]      # rotation: sign carries det
    new_order = permute_euler_order(C, order)
    return new_loc, Euler(new_ang, new_order), new_scale, new_order


# ════════════════════════════════════════════════════════════════════════════
# 7. EULER ORDER UNDER A BASIS CHANGE
# ════════════════════════════════════════════════════════════════════════════

def permute_euler_order(C, source_order='XYZ'):
    """The euler order after a basis change, e.g. 'XYZ' -> 'XZY' under 'X Z -Y'.

    Each source axis letter is relabelled to the target axis it feeds; the order
    string's letters are relabelled in place. Always one of Blender's 6 orders
    because C is a signed permutation.
    """
    amap = axis_map(C)
    sigma = {}                                      # source letter -> target letter
    for tgt, (src, _sign) in enumerate(amap):
        sigma[AXIS_NAME[src]] = AXIS_NAME[tgt]
    return ''.join(sigma[ch] for ch in source_order)


def relabel_euler(C, euler, source_order='XYZ'):
    """Exact per-channel euler relabel (value only) -> (new_euler, new_order).

    target angle for axis i = sign_i * det(C) * source angle of the axis feeding i.
    This preserves authored curve values/tangents (no resampling); the composed
    rotation matches convert_euler / convert_matrix.
    """
    amap = axis_map(C)
    det = handedness_sign(C)
    ang = [0.0, 0.0, 0.0]
    for tgt in range(3):
        src, sign = amap[tgt]
        ang[tgt] = sign * det * euler[src]
    order = permute_euler_order(C, source_order)
    return Euler(ang, order), order


# ════════════════════════════════════════════════════════════════════════════
# 8. ROTATION REPRESENTATION CONVERSIONS  (thin, explicit wrappers)
# ════════════════════════════════════════════════════════════════════════════
# mathutils already does these; wrapped here with clear names so the whole toolbox
# reads in one place.

def euler_to_matrix(euler, order='XYZ'):
    """Euler angles (radians) -> 3x3 rotation matrix."""
    return Euler(euler, order).to_matrix()


def matrix_to_euler(M, order='XYZ', compat=None):
    """3x3/4x4 rotation matrix -> Euler (radians). `compat` keeps angles continuous."""
    R = M.to_3x3()
    return R.to_euler(order, compat) if compat is not None else R.to_euler(order)


def euler_to_quaternion(euler, order='XYZ'):
    """Euler angles (radians) -> Quaternion."""
    return Euler(euler, order).to_quaternion()


def quaternion_to_euler(q, order='XYZ', compat=None):
    """Quaternion -> Euler (radians)."""
    return q.to_euler(order, compat) if compat is not None else q.to_euler(order)


def quaternion_to_matrix(q):
    """Quaternion -> 3x3 rotation matrix."""
    return q.to_matrix()


def matrix_to_quaternion(M):
    """3x3/4x4 rotation matrix -> Quaternion."""
    return M.to_3x3().to_quaternion()


def axis_angle_to_quaternion(axis, angle):
    """Axis (vector) + angle (radians) -> Quaternion."""
    return Quaternion(Vector(axis), angle)


def quaternion_to_axis_angle(q):
    """Quaternion -> (axis Vector, angle radians)."""
    return q.to_axis_angle()


def axis_angle_to_matrix(axis, angle):
    """Axis + angle -> 3x3 rotation matrix."""
    return Matrix.Rotation(angle, 3, Vector(axis))


# ════════════════════════════════════════════════════════════════════════════
# 9. TRS COMPOSE / DECOMPOSE
# ════════════════════════════════════════════════════════════════════════════

def compose_trs(loc, rot, scale):
    """Build a 4x4 from translation, rotation, and scale.

    `rot` may be a Quaternion, Euler, or 3x3 Matrix. Order is T @ R @ S (Blender's).
    """
    if isinstance(rot, Euler):
        rot_m = rot.to_matrix().to_4x4()
    elif isinstance(rot, Quaternion):
        rot_m = rot.to_matrix().to_4x4()
    else:                                            # assume a 3x3/4x4 matrix
        rot_m = rot.to_4x4()
    t = Matrix.Translation(Vector(loc))
    s = Matrix.Diagonal(Vector((scale[0], scale[1], scale[2], 1.0)))
    return t @ rot_m @ s


def decompose_trs(M):
    """4x4 -> (location Vector, rotation Quaternion, scale Vector)."""
    return M.decompose()


def matrix_translation(M):
    return M.to_translation()


def matrix_scale(M):
    return M.to_scale()


def matrix_rotation(M):
    """The pure rotation (as a Quaternion), scale removed."""
    return M.to_quaternion()


# ════════════════════════════════════════════════════════════════════════════
# 10. DEGREE / RADIAN HELPERS
# ════════════════════════════════════════════════════════════════════════════

def vec_to_degrees(v):
    """Per-component radians -> degrees (Euler or Vector in)."""
    return Vector((math.degrees(v[0]), math.degrees(v[1]), math.degrees(v[2])))


def vec_to_radians(v):
    """Per-component degrees -> radians."""
    return Vector((math.radians(v[0]), math.radians(v[1]), math.radians(v[2])))


# ════════════════════════════════════════════════════════════════════════════
# 11. FBX AXIS SETTINGS THAT MATCH A BASIS CHANGE
# ════════════════════════════════════════════════════════════════════════════

def fbx_axes_for_matrix(C):
    """Find (axis_forward, axis_up) whose FBX transform equals C, or None.

    The FBX exporter reorients data with `axis_conversion(to_forward, to_up)`. This
    searches the 6x6 combinations for the pair matching C, so an FBX export can be
    put in the SAME space as a C-based conversion. Returns None for a target FBX
    axis settings can't express (e.g. a mirrored/handedness-flipped C).
    """
    from bpy_extras.io_utils import axis_conversion
    tokens = ('X', 'Y', 'Z', '-X', '-Y', '-Z')
    for forward in tokens:
        for up in tokens:
            try:
                m = axis_conversion(to_forward=forward, to_up=up)
            except (ValueError, RuntimeError):
                continue                             # forward/up on the same axis
            if all(abs(m[i][j] - C[i][j]) < 1e-6 for i in range(3) for j in range(3)):
                return forward, up
    return None


def fbx_axes_for_remap(remap):
    """Convenience: FBX (axis_forward, axis_up) for a remap string, or None."""
    return fbx_axes_for_matrix(remap_to_matrix(remap))
