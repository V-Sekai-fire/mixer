# MIT. Survey: what the add-on sends while an animation plays. Headless Blender, a stub client, no network.
#   Blender -b --python tools/anim_survey.py   (REPO = the checkout)
import os, sys
sys.path.insert(0, os.environ.get("REPO", os.getcwd()))
import bpy, math
from collections import Counter

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.frame_start, sc.frame_end = 1, 24

# 1. object keyframes
bpy.ops.mesh.primitive_cube_add(); cube = bpy.context.object; cube.name = "KeyCube"
for f, x in ((1, 0), (24, 5)):
    cube.location.x = x; cube.keyframe_insert("location", frame=f)
# 2. skinned: armature + mesh with vertex group
bpy.ops.mesh.primitive_cylinder_add(vertices=16, depth=2); skin = bpy.context.object; skin.name = "Skinned"
bpy.ops.object.armature_add(); arm = bpy.context.object; arm.name = "Rig"
skin.vertex_groups.new(name="Bone").add(range(len(skin.data.vertices)), 1.0, "REPLACE")
skin.parent = arm; skin.modifiers.new("A", "ARMATURE").object = arm
bpy.context.view_layer.objects.active = arm; bpy.ops.object.mode_set(mode="POSE")
pb = arm.pose.bones["Bone"]; pb.rotation_mode = "XYZ"
for f, r in ((1, 0), (24, math.radians(60))):
    pb.rotation_euler.x = r; pb.keyframe_insert("rotation_euler", frame=f)
bpy.ops.object.mode_set(mode="OBJECT")
# 3. shape key
bpy.ops.mesh.primitive_uv_sphere_add(); sk = bpy.context.object; sk.name = "Morph"
sk.shape_key_add(name="Basis"); k = sk.shape_key_add(name="Big")
for v in k.data: v.co *= 1.5
for f, val in ((1, 0), (24, 1)):
    k.value = val; k.keyframe_insert("value", frame=f)

from mixer import entity_sync
from mixer.share_data import share_data
sent, published = Counter(), Counter()

class Client:
    skip_next_depsgraph_update = False
    relay_cafile = "x"
    def add_command(self, c): sent[c.type.name] += 1
share_data.client = Client()
entity_sync._active = lambda: True
orig = entity_sync._mesh_digest
def spy(ob, name, changed):
    published[name] += 1 if changed else 0
    return bytes(32)
entity_sync._mesh_digest = spy
poses = Counter(); op = entity_sync.send_pose
def sp(ob, name, g=False): poses[name] += 1; return op(ob, name, g)
entity_sync.send_pose = sp

seen = Counter()
def handler(scene, depsgraph):
    seen["calls"] += 1
    for u in depsgraph.updates:
        seen[f"{type(u.id).__name__}:{'T' if u.is_updated_transform else ''}{'G' if u.is_updated_geometry else ''}"] += 1
    entity_sync.on_depsgraph(depsgraph)
bpy.app.handlers.depsgraph_update_post.append(handler)
bpy.app.handlers.frame_change_post.append(entity_sync.on_frame_change)
for ob in list(bpy.data.objects):
    entity_sync._announced[entity_sync.gid_of(ob.name)] = ob.name  # as if each were shared already
for f in range(1, 25):
    sc.frame_set(f)
print("SURVEY frames 24; ENTITY_STATE sent:", sent["ENTITY_STATE"])
for n in ("KeyCube", "Skinned", "Rig", "Morph"):
    print(f"SURVEY {n:8s} poses {poses[n]:3d}  mesh publishes asked {published[n]:3d}")
print("SURVEY handler", dict(seen))
