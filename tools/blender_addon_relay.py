# MIT. Copyright (c) 2026-present K. S. Ernest (iFire) Lee
# Blender with the Mixer add-on itself, joined to the closed relay. Run it in Blender (a window, or --background):
#   BLENDER_SITE=<dir with aioquic, cryptography> REPO=<mixer checkout> MB_PORT MB_CERT MB_ZT_MINTER MB_ROOM \
#   Blender [--background] --python tools/blender_addon_relay.py
# The add-on's own connection code (`mixer.connection`) connects, `join_room` joins, and its depsgraph handler sends each
# changed object as ENTITY_STATE. With LIVE_MOVE=1 the script moves two objects on a schedule (in a window you drag them).
import base64
import os
import sys
import time

sys.path.insert(0, os.environ.get("BLENDER_SITE", ""))
REPO = os.environ["REPO"]
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))

import bpy  # noqa: E402

PORT, CERT, ROOM = int(os.environ["MB_PORT"]), os.environ["MB_CERT"], os.environ.get("MB_ROOM", "live")
SECONDS = float(os.environ.get("LIVE_SECONDS", "0"))  # 0: stay until the window closes

import zt  # noqa: E402

bpy.ops.preferences.addon_enable(module="mixer")
import mixer.connection as connection  # noqa: E402
from mixer.bl_utils import get_mixer_prefs  # noqa: E402
from mixer.broadcaster.client import Client  # noqa: E402
from mixer.share_data import share_data  # noqa: E402

prefs = get_mixer_prefs()
prefs.host, prefs.port, prefs.user = "127.0.0.1", PORT, "blender-addon"

Client.relay_cafile = CERT


def credentials():
    priv, pub = zt.gen_key()
    token = base64.urlsafe_b64decode(zt.get_token([ROOM], pub) + "==")
    return token, pub, lambda nonce: zt.sign_session(priv, nonce)


Client.relay_credentials = credentials

if os.environ.get("LIVE_DEBUG") == "1":
    from mixer.broadcaster import relay_transport as _rt

    _orig_read, _orig_write = _rt.RelayTransport.read_all, _rt.RelayTransport.write

    def _read(self):
        got = _orig_read(self)
        if got:
            print("ADDON <-", [c.type.name for c in got], flush=True)
        return got

    def _write(self, command):
        print("ADDON ->", command.type.name, len(command.data), flush=True)
        return _orig_write(self, command)

    _rt.RelayTransport.read_all, _rt.RelayTransport.write = _read, _write

connection.connect()
print("ADDON connected to the relay:", connection.is_client_connected(), flush=True)


# the add-on's own timer pumps the network when Blender runs its main loop; headless, we pump by hand
def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if share_data.client is not None:
            share_data.client.network_consumer()
        time.sleep(0.01)


pump(1.0)
connection.join_room(ROOM, vrtist_protocol=os.environ.get("VRTIST", "0") == "1")
pump(1.5)
print("ADDON joined:", share_data.client.current_room, flush=True)

if os.environ.get("LIVE_MOVE") == "1":
    if not bpy.data.objects:  # --background starts with an empty scene
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    cube = bpy.data.objects.get("Cube") or bpy.data.objects[0]
    cube.name = "Cube"
    bpy.context.view_layer.update()
    pump(float(os.environ.get("MOVE_DELAY", "0")))  # time for the other clients to join first
    t0 = time.time()
    poses = [(2.0, 0.0, 1.0), (0.0, 3.0, 0.0), (-2.0, -1.0, 2.0), (0.0, 0.0, 0.0)]
    k = -1
    while SECONDS == 0 or time.time() - t0 < SECONDS:
        step = int((time.time() - t0) // 2.0)
        if step != k and step < len(poses):
            k = step
            cube.location = poses[k]
            bpy.context.view_layer.update()
            print(f"ADDON moved Cube to {poses[k]}", flush=True)
        pump(0.05)
        if step >= len(poses) + 1 and SECONDS == 0:
            break
    pump(1.0)
    connection.leave_current_room()
    pump(0.5)
    connection.disconnect()
