"""
core package - the exporter's pure logic, independent of UI and operators.

Modules:
    coordinates  - axis/coordinate-system conversion (the documented hard part)
    anim_reader  - read a Blender action into a neutral animation data model
    anim_xml     - render that data model into the .anim XML file
    fbx_export   - export the rig + model as FBX

The `if "bpy" in locals()` block re-imports submodules on Reload Scripts. Order
matters: reload dependencies (coordinates) before the modules that use them.
"""

if "bpy" in locals():
    import importlib
    coordinates = importlib.reload(coordinates)
    anim_reader = importlib.reload(anim_reader)
    anim_xml = importlib.reload(anim_xml)
    fbx_export = importlib.reload(fbx_export)
else:
    from . import coordinates, anim_reader, anim_xml, fbx_export

import bpy  # noqa: F401  - referenced only so the hot-reload guard above works
