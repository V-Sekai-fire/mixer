# MIT. Build a mesh in Blender, give each face a patch of the 24-patch chart, and write ASCII USD (.usda).
#   python export_usda.py OUT_DIR   (run with the bpy env)
# Writes tet.usda and monkey.usda, and prints, per file, the triangle count and the sRGB patch of each face.
import json
import sys

import bpy

CHART = [
    [115, 82, 68],
    [194, 150, 130],
    [98, 122, 157],
    [87, 108, 67],
    [133, 128, 177],
    [103, 189, 170],
    [214, 126, 44],
    [80, 91, 166],
    [193, 90, 99],
    [94, 60, 108],
    [157, 188, 64],
    [224, 163, 46],
    [56, 61, 150],
    [70, 148, 73],
    [175, 54, 60],
    [231, 199, 31],
    [187, 86, 149],
    [8, 133, 161],
    [243, 243, 242],
    [200, 200, 200],
    [160, 160, 160],
    [122, 122, 121],
    [85, 85, 85],
    [52, 52, 52],
]


def lin(c8):
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def write_usda(path, name, verts, tris, patch_of_face):
    """One Mesh prim: points, faceVertexCounts, faceVertexIndices and a per-face displayColor in linear."""
    cols = [CHART[patch_of_face[i]] for i in range(len(tris))]
    f = lambda v: "(%.6f, %.6f, %.6f)" % tuple(v)
    up = lambda v: (v[0], v[2], -v[1])  # Blender is Z-up; the file says upAxis = "Y": (x, y, z) -> (x, z, -y)
    out = [
        "#usda 1.0",
        "(",
        '    defaultPrim = "%s"' % name,
        '    upAxis = "Y"',
        "    metersPerUnit = 1",
        ")",
        "",
        'def Mesh "%s"' % name,
        "{",
        "    int[] faceVertexCounts = [%s]" % ", ".join("3" for _ in tris),
        "    int[] faceVertexIndices = [%s]" % ", ".join(str(i) for t in tris for i in t),
        "    point3f[] points = [%s]" % ", ".join(f(up(v)) for v in verts),
        "    color3f[] primvars:displayColor = [%s] (" % ", ".join(f([lin(c) for c in col]) for col in cols),
        '        interpolation = "uniform"',
        "    )",
        "    int[] primvars:patch = [%s] (" % ", ".join(str(p) for p in patch_of_face),
        '        interpolation = "uniform"',
        "    )",
        "}",
        "",
    ]
    open(path, "w").write("\n".join(out))
    return len(tris)


def mesh_of(ob):
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.quads_convert_to_tris()
    bpy.ops.object.mode_set(mode="OBJECT")
    me = ob.data
    verts = [tuple(v.co) for v in me.vertices]
    tris = [tuple(p.vertices) for p in me.polygons]
    return verts, tris


out = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else sys.argv[1]
report = {}
bpy.ops.wm.read_factory_settings(use_empty=True)

# a tetrahedron, made by hand: four vertices, four faces
tv = [(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)]
tt = [(0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2)]
report["tet"] = write_usda(out + "/tet.usda", "Tet", tv, tt, [0, 7, 18, 23])

# Suzanne, triangulated; the patch of a face cycles through all 24
bpy.ops.mesh.primitive_monkey_add(size=2)
mv, mt = mesh_of(bpy.context.active_object)
report["monkey"] = write_usda(out + "/monkey.usda", "Suzanne", mv, mt, [i % 24 for i in range(len(mt))])
print("USDA", json.dumps(report))
