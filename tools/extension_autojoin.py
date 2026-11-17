# MIT. Startup script for the demo: the installed Mixer extension connects to the relay and joins MB_ROOM once the file
# is loaded, then touches each object so its pose and mesh go out. All the work is the add-on's own.
import os
import bpy

ROOM = os.environ.get("MB_ROOM", "live")


def go():
    from mixer import connection, relay_log
    from mixer.share_data import share_data

    if "bl_ext.user_default.mixer" not in bpy.context.preferences.addons:
        return 1.0
    if not hasattr(go, "stage"):
        connection.connect()
        go.stage = 1
        return 2.0
    if go.stage == 1:
        connection.join_room(ROOM, vrtist_protocol=False)
        go.stage = 2
        return 2.0
    for ob in bpy.data.objects:
        ob.location = ob.location
    relay_log.note(f"autojoin: room {share_data.client.current_room}")
    print("AUTOJOIN joined", share_data.client.current_room, flush=True)
    return None


bpy.app.timers.register(go, first_interval=3.0, persistent=True)
