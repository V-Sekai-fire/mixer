# MIT. Dumps each mesh object of the open .blend (points, triangles, world matrix) to $REAL_OUT/<name>.json, the real
# meshes tests/hypo/test_explode_rule.py runs through explode.gd and the port. Blender -b --factory-startup file --python this.
import json, os, bpy

out = os.environ["REAL_OUT"]
os.makedirs(out, exist_ok=True)
for ob in bpy.data.objects:
    if ob.type != "MESH":
        continue
    m = ob.to_mesh()
    m.calc_loop_triangles()
    w = ob.matrix_world
    json.dump(
        {
            "points": [list(v.co) for v in m.vertices],
            "tris": [list(t.vertices) for t in m.loop_triangles],
            "xform": [
                w[0][0],
                w[0][1],
                w[0][2],
                w[1][0],
                w[1][1],
                w[1][2],
                w[2][0],
                w[2][1],
                w[2][2],
                w[0][3],
                w[1][3],
                w[2][3],
            ],
        },
        open(os.path.join(out, ob.name + ".json"), "w"),
    )
    ob.to_mesh_clear()
