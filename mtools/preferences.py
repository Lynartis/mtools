import bpy
from . import keymaps


class MToolsPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__  # "mtools"

    def draw(self, context):
        layout = self.layout

        # Shortcut configuration, grouped by category
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.user

        for category in keymaps.get_categories():
            box = layout.box()
            box.label(text=f"{category} Shortcuts", icon='KEYINGSET')

            entries = keymaps.get_entries_by_category(category)
            for entry in entries:
                kmi = self._find_user_kmi(kc, entry)
                if kmi is None:
                    row = box.row()
                    row.label(text=entry["label"], icon='ERROR')
                    row.label(text="(shortcut not found)")
                    continue

                row = box.row(align=True)
                row.prop(kmi, "active", text="", emboss=False)
                row.label(text=entry["label"])
                row.prop(kmi, "type", text="", full_event=True)

    def _find_user_kmi(self, kc, entry):
        """Find the user-editable keymap item for a given entry."""
        km = kc.keymaps.get(entry["km_name"])
        if km is None:
            return None
        for kmi in km.keymap_items:
            if kmi.idname == entry["idname"]:
                return kmi
        return None
