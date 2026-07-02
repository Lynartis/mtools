"""export_anim.py - operators for the 'Export Animation' (.anim) panel."""

import os

import bpy

from ..core import anim_reader, anim_xml, coordinates


# Loop-mode enum items (identifier, UI label, tooltip). The UI label is also the
# exact text written to <metadata LoopMode="..."> so the engine reads it back.
LOOP_ITEMS = [
    ("ONCE", "Once", "Play through a single time"),
    ("LOOP", "Loop", "Repeat from the start"),
    ("ONE_FRAME", "One Frame", "Hold on a single frame"),
]

# identifier -> UI label (used for the LoopMode metadata attribute).
_LOOP_LABEL = {identifier: label for identifier, label, _tip in LOOP_ITEMS}


def _resolve_anim_remap(scene):
    """The remap string chosen in the animation coordinate selector."""
    return coordinates.resolve_remap(scene.mtools_anim_coord, scene.mtools_anim_coord_custom)


def _root_bone_name(skeleton):
    """The RootName for <Skeleton> = the first bone with no parent."""
    for bone in skeleton:
        if not bone.parent:
            return bone.name
    return skeleton[0].name if skeleton else ""


class MTOOLS_OT_set_anim_path(bpy.types.Operator):
    """Pick the folder to export the .anim file into."""
    bl_idname = "mtools.set_anim_path"
    bl_label = "Choose Anim Folder"
    bl_options = {"REGISTER"}

    directory: bpy.props.StringProperty(subtype="DIR_PATH")

    def execute(self, context):
        context.scene.mtools_anim_path = self.directory
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class MTOOLS_OT_export_anim(bpy.types.Operator):
    """Export the picked action as a custom .anim XML file."""
    bl_idname = "mtools.export_anim"
    bl_label = "Export Animation"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return scene.mtools_anim_armature is not None and scene.mtools_anim_action is not None

    def execute(self, context):
        scene = context.scene
        armature = scene.mtools_anim_armature
        action = scene.mtools_anim_action

        if armature is None or armature.type != "ARMATURE":
            self.report({"ERROR"}, "Pick an armature")
            return {"CANCELLED"}
        if action is None:
            self.report({"ERROR"}, "Pick an animation action")
            return {"CANCELLED"}

        # Build the output path: <folder>/<clip name>.anim
        clip_name = (scene.mtools_anim_name or action.name).strip()
        folder = bpy.path.abspath(scene.mtools_anim_path or "//")
        if not folder:
            self.report({"ERROR"}, "Set an export folder")
            return {"CANCELLED"}
        os.makedirs(folder, exist_ok=True)
        filepath = os.path.join(folder, clip_name + ".anim")

        remap = _resolve_anim_remap(scene)
        try:
            coordinates.validate_remap(remap)
            # 1) read the curves, 2) read the rest skeleton, 3) write the XML.
            bone_anims, euler_order, warnings = anim_reader.read_animation(armature, action, remap)
            skeleton = anim_reader.read_skeleton(armature, remap)
            start, end = anim_reader.frame_range(action)

            metadata = {
                "name": clip_name,
                "frame_size": end,          # last frame of the clip (e.g. 200)
                "loop_mode": _LOOP_LABEL.get(scene.mtools_anim_loop, scene.mtools_anim_loop),
                "path": clip_name + ".anim",
                "euler_order": euler_order,
                "fps": scene.render.fps,
                "coord_system": remap,
            }
            anim_xml.write_animation_xml(filepath, metadata, bone_anims, skeleton,
                                         _root_bone_name(skeleton))
        except Exception as error:
            self.report({"ERROR"}, "Animation export failed: %s" % error)
            return {"CANCELLED"}

        # Surface any sampling/eased-curve warnings, otherwise report success.
        if warnings:
            self.report({"WARNING"}, "Exported with %d note(s): %s"
                        % (len(warnings), " | ".join(warnings)))
        else:
            self.report({"INFO"}, "Exported %d animated bone(s) -> %s"
                        % (len(bone_anims), filepath))
        return {"FINISHED"}


classes = [MTOOLS_OT_set_anim_path, MTOOLS_OT_export_anim]
