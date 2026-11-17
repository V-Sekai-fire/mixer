# MIT. A pose built the way a sender builds it, applied to an object that is elsewhere: prints PROBE {...}. Headless.
import json, math, os, sys
sys.path.insert(0, os.environ["REPO"])
import bpy, mathutils
spec = json.loads(os.environ["POSE"])  # {"loc":[x,y,z], "euler":[a,b,c], "scale":[sx,sy,sz]}
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(); ob = bpy.context.object; ob.name = "Thing"
ob.location, ob.rotation_euler, ob.scale = spec["loc"], spec["euler"], spec["scale"]
bpy.context.view_layer.update()
want = ob.matrix_world.copy()
from mixer import entity_sync
from mixer.broadcaster import entity
from mixer.share_data import share_data
loc, quat, scale = want.decompose()
raw = entity.pose_entity(entity_sync.gid_of("Ghost" if spec.get("ghost") else "Thing"), tuple(loc), tuple(quat), tuple(scale), 1).pack()
ob.location, ob.rotation_euler, ob.scale = (9, 9, 9), (0, 0, 0), (1, 1, 1)  # somewhere else
bpy.context.view_layer.update()
sent = []
class C:
    skip_next_depsgraph_update = False
    def add_command(self, c): sent.append(c)
share_data.client = C(); entity_sync._active = lambda: True
entity_sync._mesh_pieces = lambda ob, name, changed: [(entity_sync.gid_of(name), bytes(32))]
ok = entity_sync.apply_state(raw)
bpy.context.view_layer.update()
got = ob.matrix_world
err_pos = (got.translation - want.translation).length
err_rot = (got.to_quaternion().rotation_difference(want.to_quaternion())).angle
err_scale = max(abs(a - b) / max(abs(b), 1e-9) for a, b in zip(got.to_scale(), want.to_scale()))
entity_sync.send_pose(ob, "Thing")
print("PROBE", json.dumps({"moved": (got.translation - mathutils.Vector((9, 9, 9))).length > 1e-6, "applied": ok, "pos": err_pos, "rot": err_rot, "scale": err_scale, "echoed": len(sent)}))
