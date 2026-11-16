"""Dump the RNA schema of the running Blender to JSON, so versions can be diffed.

Run inside Blender:  blender -b --factory-startup --python tools/rna_schema.py -- out.json
No add-on code is imported: the snapshot describes Blender, not Mixer.
"""
import json
import os
import sys

import bpy


def prop_info(p):
    info = {"type": p.type, "ro": p.is_readonly}
    if p.subtype != "NONE":
        info["subtype"] = p.subtype
    if getattr(p, "array_length", 0):
        info["array"] = p.array_length
    if p.type == "POINTER" or p.type == "COLLECTION":
        fixed = p.fixed_type
        info["to"] = fixed.identifier if fixed else None
    if p.type == "ENUM":
        info["items"] = sorted(i.identifier for i in p.enum_items)
    return info


def struct_info(s):
    return {
        "base": s.base.identifier if s.base else None,
        "props": {p.identifier: prop_info(p) for p in s.properties if p.identifier != "rna_type"},
        "funcs": sorted(f.identifier for f in s.functions),
    }


def main(out):
    structs = {}
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name)
        rna = getattr(cls, "bl_rna", None)
        if rna is None or rna.identifier != name:
            continue  # aliases and Python-only classes
        structs[name] = struct_info(rna)
    data = {
        "blender": bpy.app.version_string,
        "version": list(bpy.app.version),
        "structs": structs,
        "bpy_data": sorted(p.identifier for p in bpy.types.BlendData.bl_rna.properties if p.type == "COLLECTION"),
    }
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        json.dump(data, f, indent=0, sort_keys=True)
    print(f"rna_schema: {bpy.app.version_string}: {len(structs)} structs -> {out}")


argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
main(argv[0] if argv else "rna_schema.json")
