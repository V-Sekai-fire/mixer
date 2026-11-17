# MIT. Plays keyframes (JSON in env ANIM) through the add-on's frame hook; prints PROBE {...}. Headless, no network.
import json, os, sys

sys.path.insert(0, os.environ["REPO"])
import bpy
from collections import Counter

spec = json.loads(os.environ["ANIM"])  # {"keys": [[frame, x], ...], "frames": n, "shared": bool}
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
bpy.ops.mesh.primitive_cube_add()
mover = bpy.context.object
mover.name = "Mover"
bpy.ops.mesh.primitive_cube_add()
still = bpy.context.object
still.name = "Still"
for f, x in spec["keys"]:
    mover.location.x = x
    mover.keyframe_insert("location", index=0, frame=f)
for fc in (
    mover.animation_data.action.layers[0].strips[0].channelbags[0].fcurves
    if hasattr(mover.animation_data.action, "layers")
    else mover.animation_data.action.fcurves
):
    for kp in fc.keyframe_points:
        kp.interpolation = "CONSTANT"

from mixer import entity_sync
from mixer.share_data import share_data

sent = Counter()


class Client:
    skip_next_depsgraph_update = False

    def add_command(self, c):
        sent[c.type.name] += 1


share_data.client = Client()
entity_sync._active = lambda: True
published = []
entity_sync._mesh_pieces = lambda ob, name, changed: (published.append(name) if changed else None) or [
    (entity_sync.gid_of(name), bytes(32))
]
poses = Counter()
op = entity_sync.send_pose
entity_sync.send_pose = lambda ob, name, g=False: (poses.update([name]), op(ob, name, g))[1]
if spec["shared"]:
    for ob in (mover, still):
        entity_sync._announced[entity_sync.gid_of(ob.name)] = ob.name
bpy.app.handlers.frame_change_post.append(entity_sync.on_frame_change)
xs = []
for f in range(1, spec["frames"] + 1):
    sc.frame_set(f)
    xs.append(round(mover.matrix_world[0][3], 6))
moved = sum(1 for i in range(len(xs)) if i == 0 or xs[i] != xs[i - 1])
print(
    "PROBE",
    json.dumps(
        {
            "moved": moved,
            "mover": poses["Mover"],
            "still": poses["Still"],
            "published": len(published),
            "state": sent["ENTITY_STATE"],
        }
    ),
)
