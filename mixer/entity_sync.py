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
from typing import Any, Dict

import bpy

from mixer import relay_log
from mixer.broadcaster import common, entity, geo_publish
from mixer.broadcaster.common import MessageType
from mixer.share_data import share_data

logger = logging.getLogger(__name__)

_digests: Dict[str, bytes] = {}  # object name -> digest of the mesh last published
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


def _mesh_digest(ob_evaluated: Any, name: str, geometry_changed: bool) -> bytes:
    """Publish the object's mesh (local space, as USD through desync) when it changed; else the last digest."""
    desync, filer = os.environ.get("MB_DESYNC"), os.environ.get("MB_S3")
    if not (desync and filer) or ob_evaluated.type != "MESH":
        return _digests.get(name, bytes(entity.DIGEST_LEN))
    if geometry_changed or name not in _digests:
        mesh = ob_evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            pts = [tuple(v.co) for v in mesh.vertices]
            tris = [tuple(t.vertices) for t in mesh.loop_triangles]
            _digests[name] = geo_publish.publish(geo_publish.usda(name, pts, tris), desync, filer)
            relay_log.note(
                f"published mesh of {name}: {len(pts)} points, {len(tris)} triangles, digest {_digests[name].hex()[:8]}"
            )
        except Exception:
            logger.exception("could not publish the mesh of %s", name)
            relay_log.note(f"could not publish the mesh of {name}")
        finally:
            ob_evaluated.to_mesh_clear()
    return _digests.get(name, bytes(entity.DIGEST_LEN))


def send_pose(ob_evaluated: Any, name: str, geometry_changed: bool = False) -> None:
    loc, quat, scale = ob_evaluated.matrix_world.decompose()
    gid = gid_of(name)
    digest = _mesh_digest(ob_evaluated, name, geometry_changed)
    pose = entity.pose_entity(gid, tuple(loc), tuple(quat), tuple(scale), _next_hlc(), digest=digest)
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


def reset() -> None:
    _announced.clear()
    _logged.clear()
    _digests.clear()
