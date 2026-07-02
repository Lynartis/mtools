"""
anim_xml.py - Render the neutral animation data model into the .anim XML file.
=============================================================================

This module knows about XML but NOTHING about Blender - it only sees the plain
data classes from anim_reader.py. Every tag and attribute name is declared once
in the TAG / ATTR registries below, and there is one small `build_*` function
per XML element, so the document structure is easy to read and change.

Document shape (matches the user's designed .anim format):

    <Animation>
      <metadata Name FrameSize LoopMode Path EulerOrder Fps CoordSystem/>
      <BoneAnimationData>
        <Path>Skeleton.Bones["Name"].AnimatedTransform</Path>
        <transformAnimation>
          <ScaleFloat3Animation>   <XSegmentsFloat3A>...</> <Y...> <Z...> </>
          <RotateFloat3Animation>  ... </>
          <TranslateFloat3Animation> ... </>
        </transformAnimation>
      </BoneAnimationData>
      ...
      <Skeleton RootName scalingRule="Standard">
        <Bones><Bone Name Parent HasSkiningMatrix><Transform>...</></Bone>...</Bones>
      </Skeleton>
    </Animation>
"""

import xml.etree.ElementTree as ET


# ===========================================================================
# Name registries - every tag / attribute string defined exactly once.
# ===========================================================================
TAG = {
    "root": "Animation",
    "metadata": "metadata",
    # per-bone animation
    "bone_anim": "BoneAnimationData",
    "path": "Path",
    "transform_anim": "transformAnimation",
    "scale_anim": "ScaleFloat3Animation",
    "rotate_anim": "RotateFloat3Animation",
    "translate_anim": "TranslateFloat3Animation",
    "x_segs": "XSegmentsFloat3A",
    "y_segs": "YSegmentsFloat3A",
    "z_segs": "ZSegmentsFloat3A",
    "keys": "keys",
    # skeleton
    "skeleton": "Skeleton",
    "bones": "Bones",
    "bone": "Bone",
    "transform": "Transform",
    "scale": "Scale",
    "rotate": "Rotate",
    "translate": "Translate",
}

ATTR = {
    # metadata
    "name": "Name",
    "frame_size": "FrameSize",
    "loop_mode": "LoopMode",
    "path": "Path",
    "euler_order": "EulerOrder",
    "fps": "Fps",
    "coord_system": "CoordSystem",
    # keys
    "frame": "Frame",
    "value": "Value",
    "in_slope": "InSlope",
    "out_slope": "OutSlope",
    # skeleton / bone
    "root_name": "RootName",
    "scaling_rule": "scalingRule",
    "bone_name": "Name",
    "parent": "Parent",
    "has_skin": "HasSkiningMatrix",
    "x": "x",
    "y": "y",
    "z": "z",
}

# A segment kind maps to its segment tag and its key tag.
SEGMENT_TAG = {"LINEAR": "LinearSegment", "HERMITE": "HermiteSegment", "STEP": "stepSegment"}
KEY_TAG = {"LINEAR": "LinearKey", "HERMITE": "HermiteKey", "STEP": "StepKey"}


# ===========================================================================
# Value formatting helpers
# ===========================================================================
def _fmt(value):
    """Compact float text: no trailing zeros, no scientific notation, no '-0'."""
    text = ("%.6f" % float(value)).rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _bool(flag):
    """XML boolean text for HasSkiningMatrix (change here to match the engine)."""
    return "true" if flag else "false"


# ===========================================================================
# Keys and segments
# ===========================================================================
def build_key(key, kind):
    """One <LinearKey/> / <HermiteKey/> / <StepKey/> element."""
    element = ET.Element(KEY_TAG[kind])
    element.set(ATTR["frame"], str(int(key.frame)))
    element.set(ATTR["value"], _fmt(key.value))
    if kind == "HERMITE":                             # only Hermite carries slopes
        element.set(ATTR["in_slope"], _fmt(key.in_slope or 0.0))
        element.set(ATTR["out_slope"], _fmt(key.out_slope or 0.0))
    return element


def build_segment(segment):
    """A segment wrapping its <keys> list."""
    element = ET.Element(SEGMENT_TAG[segment.kind])
    keys_element = ET.SubElement(element, TAG["keys"])
    for key in segment.keys:
        keys_element.append(build_key(key, segment.kind))
    return element


def build_axis_segments(axis_tag, axis_channel):
    """One <XSegmentsFloat3A>/<Y...>/<Z...> holding that axis's segments."""
    element = ET.Element(axis_tag)
    for segment in axis_channel.segments:
        element.append(build_segment(segment))
    return element


# ===========================================================================
# Components and per-bone animation
# ===========================================================================
def build_component_animation(component_tag, component):
    """<ScaleFloat3Animation>/<Rotate...>/<Translate...> with its X/Y/Z axes."""
    element = ET.Element(component_tag)
    element.append(build_axis_segments(TAG["x_segs"], component.x))
    element.append(build_axis_segments(TAG["y_segs"], component.y))
    element.append(build_axis_segments(TAG["z_segs"], component.z))
    return element


def build_transform_animation(bone_anim):
    """<transformAnimation> holding whichever of Scale/Rotate/Translate exist."""
    element = ET.Element(TAG["transform_anim"])
    if bone_anim.scale:
        element.append(build_component_animation(TAG["scale_anim"], bone_anim.scale))
    if bone_anim.rotate:
        element.append(build_component_animation(TAG["rotate_anim"], bone_anim.rotate))
    if bone_anim.translate:
        element.append(build_component_animation(TAG["translate_anim"], bone_anim.translate))
    return element


def build_path(bone_name):
    """<Path>Skeleton.Bones["Name"].AnimatedTransform</Path> (the engine target)."""
    element = ET.Element(TAG["path"])
    element.text = 'Skeleton.Bones["%s"].AnimatedTransform' % bone_name
    return element


def build_bone_animation(bone_anim):
    """<BoneAnimationData> = the path plus the transform animation."""
    element = ET.Element(TAG["bone_anim"])
    element.append(build_path(bone_anim.name))
    element.append(build_transform_animation(bone_anim))
    return element


# ===========================================================================
# Skeleton (rest pose)
# ===========================================================================
def _vec3(parent, tag, vec3):
    """Append a <Scale/Rotate/Translate x= y= z=/> element under `parent`."""
    element = ET.SubElement(parent, tag)
    element.set(ATTR["x"], _fmt(vec3[0]))
    element.set(ATTR["y"], _fmt(vec3[1]))
    element.set(ATTR["z"], _fmt(vec3[2]))
    return element


def build_transform(scale, rotate, translate):
    """<Transform> with rest Scale, Rotate (euler radians) and Translate."""
    element = ET.Element(TAG["transform"])
    _vec3(element, TAG["scale"], scale)
    _vec3(element, TAG["rotate"], rotate)
    _vec3(element, TAG["translate"], translate)
    return element


def build_bone(bone_rest):
    """One <Bone> with its name, parent, skin flag and rest transform."""
    element = ET.Element(TAG["bone"])
    element.set(ATTR["bone_name"], bone_rest.name)
    element.set(ATTR["parent"], bone_rest.parent)
    element.set(ATTR["has_skin"], _bool(bone_rest.has_skin))
    element.append(build_transform(bone_rest.scale, bone_rest.rotate, bone_rest.translate))
    return element


def build_skeleton(root_name, skeleton):
    """<Skeleton RootName scalingRule><Bones>...</Bones></Skeleton>."""
    element = ET.Element(TAG["skeleton"])
    element.set(ATTR["root_name"], root_name)
    element.set(ATTR["scaling_rule"], "Standard")
    bones_element = ET.SubElement(element, TAG["bones"])
    for bone_rest in skeleton:
        bones_element.append(build_bone(bone_rest))
    return element


# ===========================================================================
# Metadata
# ===========================================================================
def build_metadata(meta):
    """<metadata .../> from a plain dict of clip settings."""
    element = ET.Element(TAG["metadata"])
    element.set(ATTR["name"], str(meta.get("name", "")))
    element.set(ATTR["frame_size"], str(int(meta.get("frame_size", 0))))
    element.set(ATTR["loop_mode"], str(meta.get("loop_mode", "")))
    element.set(ATTR["path"], str(meta.get("path", "")))
    element.set(ATTR["euler_order"], str(meta.get("euler_order", "XYZ")))
    element.set(ATTR["fps"], str(int(meta.get("fps", 0))))
    element.set(ATTR["coord_system"], str(meta.get("coord_system", "")))
    return element


# ===========================================================================
# Assemble and write the whole document
# ===========================================================================
def build_document(meta, bone_anims, skeleton, root_name):
    """Assemble the full <Animation> element tree (returns the root element)."""
    root = ET.Element(TAG["root"])
    root.append(build_metadata(meta))
    for bone_anim in bone_anims:
        root.append(build_bone_animation(bone_anim))
    root.append(build_skeleton(root_name, skeleton))
    return root


def write_animation_xml(filepath, meta, bone_anims, skeleton, root_name):
    """Build the document and write it to `filepath` as pretty-printed UTF-8 XML."""
    root = build_document(meta, bone_anims, skeleton, root_name)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")                     # pretty-print (Python 3.9+)
    tree.write(filepath, encoding="utf-8", xml_declaration=True)
