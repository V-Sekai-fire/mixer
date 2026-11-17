# GPLv3, like the rest of the add-on. Copyright (c) 2026-present K. S. Ernest (iFire) Lee
#
# Poses for the relay. When the add-on's depsgraph handler sees an object's transform change, this sends it as
# ENTITY_STATE (one 100-byte entity packet, a datagram) to the room, and ENTITY_REMOVE when an object we announced is
# gone. It runs only when the client is connected to the relay (`Client.relay_cafile` is set); the Python server has
# no use for it. Changes that came in from the room are not echoed (`skip_next_depsgraph_update`).
#
# The wire format and the Z-up to Y-up conversion are in `mixer.broadcaster.entity`, which has no `bpy` and is tested
# on its own.

import logging
import os
import time
import zlib
from typing import Any, Dict, Set

import bpy
import mathutils

from mixer import relay_log
from mixer.broadcaster import common, entity, geo_publish, mesh_split
from mixer.broadcaster.common import MessageType
from mixer.share_data import share_data

logger = logging.getLogger(__name__)

_logged: Dict[int, tuple] = {}  # gid -> position last written to the log
_announced: Dict[int, str] = {}  # gid -> object name, for the objects whose pose we have sent
_hlc = [0, 0]


def gid_of(name: str) -> int:
    """A stable entity id for an object: 32 bits of the CRC of its name, never 0. Two objects could collide; a clash
    would make them one entity for the other clients, which is visible and rare (1 in 4 billion per pair)."""
    return zlib.crc32(name.encode("utf-8")) or 1


def _next_hlc() -> int:
    now = int(time.time() * 1000)
    _hlc[0], _hlc[1] = (now, 0) if now > _hlc[0] else (_hlc[0], (_hlc[1] + 1) & 0xFFFF)
    return (_hlc[0] << 16) | _hlc[1]


def _active() -> bool:
    client = share_data.client
    return (
        client is not None
        and getattr(client, "relay_cafile", None) is not None
        and client.is_connected()
        and client.current_room is not None
    )


_dirty: Set[str] = set()  # objects edited faster than the publish interval allows
_flush_armed = False


def _publisher(data: bytes) -> bytes:
    return geo_publish.publish(data, os.environ["MB_DESYNC"], os.environ["MB_S3"])


_cache = geo_publish.PublishCache(_publisher, float(os.environ.get("MB_PUBLISH_INTERVAL", "1.0")))


def _arm_flush() -> None:
    global _flush_armed
    if not _flush_armed:
        _flush_armed = True
        bpy.app.timers.register(_flush, first_interval=_cache._interval + 0.1)


def _flush() -> None:
    """Publish what changed while the interval held it back, once, from the object's latest state."""
    global _flush_armed
    _flush_armed = False
    for name in list(_dirty):
        ob = bpy.data.objects.get(name)
        if ob is None:
            _dirty.discard(name)
            continue
        try:
            send_pose(ob.evaluated_get(bpy.context.evaluated_depsgraph_get()), name, True)
        except Exception:
            logger.exception("could not publish the held-back mesh of %s", name)
            _dirty.discard(name)


MAX_TRIS = int(os.environ.get("MB_MAX_TRIS", "20000"))  # a mesh over this goes out in pieces a viewer can fetch at once
_pieces: Dict[str, list] = {}  # object name -> [(gid, digest)] of the pieces last published


def piece_gid(name: str, k: int, count: int) -> int:
    """A one-piece object keeps its own id; the pieces of a cut one are `name#k`."""
    return gid_of(name) if count == 1 else gid_of(f"{name}#{k}")


def _mesh_pieces(ob_evaluated: Any, name: str, geometry_changed: bool) -> list:
    """[(gid, digest)] for the object: publish its mesh (local space, USD through desync) when it changed, in pieces of at
    most MAX_TRIS triangles when it is large; else what was last published."""
    desync, filer = os.environ.get("MB_DESYNC"), os.environ.get("MB_S3")
    last = _pieces.get(name) or [(gid_of(name), bytes(entity.DIGEST_LEN))]
    if not (desync and filer) or ob_evaluated.type != "MESH":
        return last
    if geometry_changed or name not in _pieces:
        if not _cache.ready(name):  # also holds back a retry after a failed publish
            _dirty.add(name)  # edited again within the interval: published once, by the timer below
            _arm_flush()
            return last
        _dirty.discard(name)
        mesh = ob_evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            pts = [tuple(v.co) for v in mesh.vertices]
            tris = [tuple(t.vertices) for t in mesh.loop_triangles]
            mw = ob_evaluated.matrix_world
            world = [tuple(mw @ v.co) for v in mesh.vertices]  # explode.gd welds in world space
            groups = mesh_split.split(pts, tris, MAX_TRIS, world) or [[]]
            made = []
            for k, ids in enumerate(groups):
                p, t = (pts, tris) if len(groups) == 1 else mesh_split.piece(pts, tris, ids)
                label = name if len(groups) == 1 else f"{name}#{k}"
                made.append((piece_gid(name, k, len(groups)), _cache.digest(label, geo_publish.usda(label, p, t))))
            for gid, _ in _pieces.get(name, []):
                if gid not in {g for g, _ in made}:  # the mesh needs fewer pieces than before
                    share_data.client.add_command(common.Command(MessageType.ENTITY_REMOVE, gid.to_bytes(4, "little")))
                    _announced.pop(gid, None)
            _pieces[name] = made
            relay_log.note(
                f"published mesh of {name}: {len(pts)} points, {len(tris)} triangles in {len(made)} piece(s), "
                f"digest {made[0][1].hex()[:8]}"
            )
        except Exception:
            logger.exception("could not publish the mesh of %s", name)
            relay_log.note(f"could not publish the mesh of {name}")
        finally:
            ob_evaluated.to_mesh_clear()
    return _pieces.get(name) or last


_applied: Dict[int, tuple] = {}  # gid -> (pos, rot, scale) of the last pose from the room, so it is not sent back


def _fields(e: Any) -> tuple:
    return (e.pos, e.rot, e.payload[entity.DIGEST_LEN :])


def _same(a: Any, b: Any) -> bool:
    """Two (pos, rot, scale) the same pose to within the wire's own rounding (2 um, 2 rotation codes, 2 scale steps)."""
    if a is None:
        return False
    return (
        all(abs(x - y) <= 2 for x, y in zip(a[0], b[0]))
        and all(abs(x - y) <= 2 for x, y in zip(a[1], b[1]))
        and all(abs(x - y) <= 2 for x, y in zip(a[2], b[2]))
    )


def apply_state(raw: bytes) -> bool:
    """A pose from another player (Godot, three.js, another Blender): move the object with that id. The pose is
    remembered, and the depsgraph update this causes sends nothing back while the object still holds it."""
    try:
        e = entity.Entity.unpack(raw)
    except ValueError:
        return False
    if (e.class_owner >> 24) == 128:  # a command in a packet's clothes: not a pose
        return False
    ob = next((o for o in bpy.data.objects if gid_of(o.name) == e.gid), None)
    if ob is None:
        return False  # an entity this scene has no object for (the viewers' own); the Mixer session creates objects
    wx, wy, wz = (c / 1_000_000 for c in e.pos)
    qx, qy, qz, qw = entity.quat_of_codes(*e.rot)
    sx, sy, sz = entity.decode_scale(e.payload[entity.DIGEST_LEN :])
    _applied[e.gid] = _fields(e)
    ob.location = (wx, -wz, wy)  # the wire's (x, y, z) is Blender's (x, -z, y)
    quat = mathutils.Quaternion((qw, qx, -qz, qy))  # the wire's (x, y, z, w) is Blender's (w, x, -z, y)
    if ob.rotation_mode == "QUATERNION":
        ob.rotation_quaternion = quat
    else:
        ob.rotation_euler = quat.to_euler(ob.rotation_mode if len(ob.rotation_mode) == 3 else "XYZ")
    ob.scale = (sx, sy, sz)
    return True


def send_pose(ob_evaluated: Any, name: str, geometry_changed: bool = False) -> None:
    loc, quat, scale = ob_evaluated.matrix_world.decompose()
    for gid, digest in _mesh_pieces(ob_evaluated, name, geometry_changed):  # every piece has the object's pose
        pose = entity.pose_entity(gid, tuple(loc), tuple(quat), tuple(scale), _next_hlc(), digest=digest)
        if not geometry_changed and _same(_applied.get(gid), _fields(pose)):
            continue  # this is the pose the room just gave this object
        _applied.pop(gid, None)
        _announced[gid] = name
        last = _logged.get(gid)
        if last is None or sum((a - b) ** 2 for a, b in zip(last, loc)) > 1e-4:
            _logged[gid] = tuple(loc)
            relay_log.note(f"sent {name} at ({loc[0]:.2f}, {loc[1]:.2f}, {loc[2]:.2f})")
        share_data.client.add_command(common.Command(MessageType.ENTITY_STATE, pose.pack()))


def on_depsgraph(depsgraph: Any) -> None:
    """Called from the add-on's depsgraph handler with the updates Blender reports."""
    if not _active() or share_data.client.skip_next_depsgraph_update:
        return
    for update in depsgraph.updates:
        ob = update.id
        if isinstance(ob, bpy.types.Object) and (update.is_updated_transform or update.is_updated_geometry):
            send_pose(ob, ob.name, update.is_updated_geometry)
    names = {o.name for o in bpy.data.objects}
    for gid, name in list(_announced.items()):
        if name not in names:
            del _announced[gid]
            relay_log.note(f"removed {name}")
            share_data.client.add_command(common.Command(MessageType.ENTITY_REMOVE, gid.to_bytes(4, "little")))


_matrices: Dict[str, tuple] = {}  # object name -> the world matrix last seen while an animation played


def on_frame_change(scene: Any, depsgraph: Any = None) -> None:
    """A frame changed (playback, scrubbing, a frame set by script). Blender calls no depsgraph handler for that, so
    the poses of what moved are sent here. Only a changed world matrix is sent, and never a mesh: the digest of the mesh
    last published stays in the pose, so playback costs one packet per moving object and no upload."""
    if not _active() or share_data.client.skip_next_depsgraph_update:
        return
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    for ob in scene.objects:
        ob_eval = ob.evaluated_get(depsgraph)
        key = tuple(v for row in ob_eval.matrix_world for v in row)
        before = _matrices.get(ob.name)
        if before == key:
            continue
        _matrices[ob.name] = key
        if before is None and ob.animation_data is None:
            continue  # first sight of something that is not animated: the depsgraph path has sent it already
        if ob.name not in _announced.values():
            continue  # not shared yet: the depsgraph path announces it together with its mesh
        send_pose(ob_eval, ob.name)


def reset() -> None:
    _applied.clear()
    _matrices.clear()
    _announced.clear()
    _logged.clear()
    _pieces.clear()
    _dirty.clear()
