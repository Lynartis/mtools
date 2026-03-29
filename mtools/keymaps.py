import bpy

addon_keymaps = []

# ────────────────────────────────────────────────────────────────
# Central shortcut registry -- ALL tool shortcuts go here.
#
# Each entry:
#   "idname"     : operator bl_idname
#   "label"      : display name in preferences
#   "category"   : group heading in preferences (e.g. "Mesh", "UV", "Animation")
#   "key"        : default key
#   "shift/ctrl/alt" : modifier flags (optional, default False)
#   "km_name"    : Blender keymap context (e.g. "Mesh", "Object Mode", "UV Editor")
#   "space_type" : space type for the keymap (usually "EMPTY")
# ────────────────────────────────────────────────────────────────

KEYMAP_ENTRIES = [
    # ── Mesh Tools ──
    {
        "idname": "mtools.target_weld",
        "label": "Target Weld",
        "category": "Mesh",
        "key": "T",
        "shift": True,
        "km_name": "Mesh",
        "space_type": "EMPTY",
    },
    # Add new tools here, for example:
    # {
    #     "idname": "mtools.loop_cutter",
    #     "label": "Smart Loop Cut",
    #     "category": "Mesh",
    #     "key": "L",
    #     "shift": True,
    #     "ctrl": True,
    #     "km_name": "Mesh",
    #     "space_type": "EMPTY",
    # },
    # {
    #     "idname": "mtools.uv_straighten",
    #     "label": "Straighten UVs",
    #     "category": "UV",
    #     "key": "S",
    #     "shift": True,
    #     "km_name": "UV Editor",
    #     "space_type": "EMPTY",
    # },
]


def get_categories():
    """Return ordered list of unique categories."""
    seen = []
    for entry in KEYMAP_ENTRIES:
        cat = entry.get("category", "Other")
        if cat not in seen:
            seen.append(cat)
    return seen


def get_entries_by_category(category):
    """Return all keymap entries for a given category."""
    return [e for e in KEYMAP_ENTRIES if e.get("category", "Other") == category]


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:
        return
    for entry in KEYMAP_ENTRIES:
        km = kc.keymaps.new(name=entry["km_name"], space_type=entry["space_type"])
        kmi = km.keymap_items.new(
            entry["idname"],
            type=entry["key"],
            value="PRESS",
            shift=entry.get("shift", False),
            ctrl=entry.get("ctrl", False),
            alt=entry.get("alt", False),
        )
        addon_keymaps.append((km, kmi, entry))


def unregister():
    for km, kmi, _entry in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
