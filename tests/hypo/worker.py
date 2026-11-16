"""
Blender-side worker: `blender -b --python worker.py -- --port N`.

Listens on 127.0.0.1:N for newline-delimited JSON requests, replies with one JSON line each.
Works headless on macOS, Linux and Windows: bpy.app.timers do not fire in background mode,
so the worker pumps Mixer's network consumer and the depsgraph itself.
"""
import json
import socket
import sys
import time
import traceback

import bpy

# `blender -b --python worker.py -- --port=N`  or  `python worker.py --port=N` (bpy PyPI module)
PORT = int(next(a for a in sys.argv if a.startswith("--port=")).split("=")[1])


def pump(seconds: float):
    from mixer.connection import network_consumer_timer
    from mixer.share_data import share_data

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if share_data.client is not None and share_data.client.is_connected():
            network_consumer_timer()
        bpy.context.view_layer.update()
        time.sleep(0.02)


def snapshot():
    """Order independent, name keyed description of the synchronized scene content."""
    objs = {}
    for o in bpy.data.objects:
        objs[o.name] = {
            "type": o.type,
            "loc": [round(v, 5) for v in o.location],
            "scale": [round(v, 5) for v in o.scale],
            "parent": o.parent.name if o.parent else None,
        }
    cols = {c.name: sorted(o.name for o in c.objects) for c in bpy.data.collections}
    return {"objects": objs, "collections": cols}


def handle(req):
    op = req["op"]
    if op == "enable":
        import addon_utils

        addon_utils.enable("mixer", default_set=True)
        return "ok"
    if op == "empty_scene":
        # Gall's law: start from the simplest scene, grow the synchronized content test by test
        for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras,
                     bpy.data.worlds, bpy.data.collections):
            for item in list(coll):
                coll.remove(item)
        return "ok"
    if op == "set_server":
        prefs = bpy.context.preferences.addons["mixer"].preferences
        prefs.host = req["host"]
        prefs.port = req["port"]
        return "ok"
    if op == "connect":
        from mixer.share_data import share_data
        from mixer.connection import connect

        connect()
        return "ok"
    if op == "join":
        from mixer.connection import join_room
        from mixer.share_data import share_data

        if not req.get("create"):
            # Joiner: drop our own content first or both sides would send a scene of the same name (cross join),
            # then wait until the creator's room is joinable.
            from mixer.blender_client.client import clear_scene_content
            from mixer.broadcaster.common import RoomAttributes

            clear_scene_content()
            end = time.monotonic() + 30
            while time.monotonic() < end:
                share_data.client.send_list_rooms()
                pump(0.2)
                attrs = share_data.client.rooms_attributes.get(req["room"])
                if attrs is not None and attrs.get(RoomAttributes.JOINABLE, False):
                    break
            else:
                raise RuntimeError(f"room {req['room']} never became joinable")
        join_room(req["room"], False, [], True)
        pump(req.get("pump", 1.0))
        return "ok"
    if op == "exec":
        scope = {"bpy": bpy}
        exec(compile(req["code"], "<hypo>", "exec"), scope)
        pump(req.get("pump", 0.5))
        # code may leave a JSON-able `result` for the caller
        return scope.get("result", "ok")
    if op == "pump":
        pump(req["seconds"])
        return "ok"
    if op == "snapshot":
        return snapshot()
    if op == "quit":
        return "bye"
    raise ValueError(op)


def main():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(5)
    # A connection can be a probe that is reset on Windows (WinError 10054) or dropped by a retrying client:
    # only an explicit "quit" ends the worker, anything else goes back to accept().
    while True:
        conn, _ = srv.accept()
        f = conn.makefile("rw", encoding="utf-8", newline="\n")
        try:
            for line in f:
                req = json.loads(line)
                try:
                    res = {"ok": True, "result": handle(req)}
                except Exception:
                    res = {"ok": False, "error": traceback.format_exc()}
                f.write(json.dumps(res) + "\n")
                f.flush()
                if req["op"] == "quit":
                    return
        except (ConnectionError, OSError):
            pass
        finally:
            conn.close()


main()
