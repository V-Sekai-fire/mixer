# MIT. A big mesh through the add-on's publisher with a fake store: prints PROBE {...}. Headless Blender, no network.
import json, os, sys
sys.path.insert(0, os.environ["REPO"])
import bpy
from collections import Counter
spec = json.loads(os.environ["SPLIT"])  # {"n": grid cells per side, "max": limit}
os.environ["MB_DESYNC"], os.environ["MB_S3"] = "x", "x"
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=spec["n"], y_subdivisions=spec["n"], size=4)
ob = bpy.context.object; ob.name = "Big"; ob.location = (1, 2, 3)
bpy.context.view_layer.update()
from mixer import entity_sync
from mixer.share_data import share_data
entity_sync.MAX_TRIS = spec["max"]
sent, store = [], []
class C:
    skip_next_depsgraph_update = False
    def add_command(self, c): sent.append(c)
share_data.client = C(); entity_sync._active = lambda: True
from mixer.broadcaster import geo_publish
import hashlib
entity_sync._cache.digest = lambda name, data: (store.append((name, data)), hashlib.sha256(data).digest())[1]
entity_sync.send_pose(ob.evaluated_get(bpy.context.evaluated_depsgraph_get()), "Big", True)
from mixer.broadcaster import entity
poses = [entity.Entity.unpack(c.data) for c in sent if c.type.name == "ENTITY_STATE"]
tris = sum(d.count(b"faceVertexCounts") and d.split(b"faceVertexCounts")[1].split(b"[")[1].split(b"]")[0].count(b"3") for _, d in store)
print("PROBE", json.dumps({"entities": len(poses), "gids": len({p.gid for p in poses}), "stored": len(store),
    "same_pose": len({p.pos for p in poses}) == 1, "digests": len({p.payload[:32] for p in poses}), "tris": tris,
    "expect": 2 * spec["n"] * spec["n"]}))
