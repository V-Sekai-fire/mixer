# MIT. Blender as a live relay client: whatever you move in the 3D viewport moves in every other client.
# Run inside Blender (a window or --background):
#   BLENDER_SITE=<dir with aioquic> GEO_DIR=<geo dir> MB_PORT=4466 MB_CERT=<cert.pem> MB_ROOM=live \
#   Blender --python tools/blender_live_sender.py
# It builds two objects from the .usda meshes (Suzanne, and the 24-patch chart behind her), publishes both through
# desync into the chunk store, then every 1/64 s reads each object's world transform and sends it as a 100-byte
# entity packet over HTTP/3 WebTransport datagrams. The packet's payload names the mesh by the SHA-256 of its index.
import asyncio
import hashlib
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.environ.get("BLENDER_SITE", ""))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.environ["TOOLS_DIR"])

import bpy
import mathutils
from aioquic.asyncio import QuicConnectionProtocol, connect
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import HeadersReceived
from aioquic.quic.configuration import QuicConfiguration

import zt
from swing_twist import swing_twist_codes
from xr_grid_entity_packet import Packet, encode

GEO = os.environ["GEO_DIR"]
PORT = int(os.environ.get("MB_PORT", "4466"))
CERT = os.environ["MB_CERT"]
ROOM = os.environ.get("MB_ROOM", "live")
FILER = "http://127.0.0.1:8888"
HZ = 64  # the entity server's rate (RFD 2443)
CM = mathutils.Matrix(
    ((1, 0, 0), (0, 0, 1), (0, -1, 0))
)  # Blender (x, y, z) -> wire (x, z, -y): Z up -> Y up, -Y forward
CHART = [
    [115, 82, 68],
    [194, 150, 130],
    [98, 122, 157],
    [87, 108, 67],
    [133, 128, 177],
    [103, 189, 170],
    [214, 126, 44],
    [80, 91, 166],
    [193, 90, 99],
    [94, 60, 108],
    [157, 188, 64],
    [224, 163, 46],
    [56, 61, 150],
    [70, 148, 73],
    [175, 54, 60],
    [231, 199, 31],
    [187, 86, 149],
    [8, 133, 161],
    [243, 243, 242],
    [200, 200, 200],
    [160, 160, 160],
    [122, 122, 121],
    [85, 85, 85],
    [52, 52, 52],
]


def lin(c8):
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def publish(name):
    idx = f"{GEO}/{name}.caibx"
    subprocess.run(
        [f"{GEO}/desync", "make", "-s", f"{FILER}/chunks", idx, f"{GEO}/usd/{name}.usda"],
        check=True,
        capture_output=True,
    )
    data = open(idx, "rb").read()
    digest = hashlib.sha256(data).digest()
    urllib.request.urlopen(
        urllib.request.Request(f"{FILER}/index/{digest.hex()}.caibx", data=data, method="PUT")
    ).read()
    pub = os.path.join(tempfile.gettempdir(), "mixer_geo_usd")
    os.makedirs(pub, exist_ok=True)
    shutil.copy(f"{GEO}/usd/{name}.usda", os.path.join(pub, digest.hex() + ".usda"))
    return digest


def arr(text, key):
    i = text.index(key)
    a = text.index("[", i)
    depth = 0
    for j in range(a, len(text)):
        depth += (text[j] == "[") - (text[j] == "]")
        if depth == 0:
            return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", text[a + 1 : j])]


def make_object(name, usda):
    text = open(f"{GEO}/usd/{usda}.usda").read()
    pts, tri, patch = arr(text, "points = "), arr(text, "faceVertexIndices = "), arr(text, "primvars:patch = ")
    verts = [(pts[i], -pts[i + 2], pts[i + 1]) for i in range(0, len(pts), 3)]  # the file is Y-up, Blender is Z-up
    faces = [tuple(int(v) for v in tri[i : i + 3]) for i in range(0, len(tri), 3)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    for p in range(24):
        m = bpy.data.materials.new(f"{name}_c{p}")
        m.diffuse_color = (lin(CHART[p][0]), lin(CHART[p][1]), lin(CHART[p][2]), 1.0)
        me.materials.append(m)
    for poly, p in zip(me.polygons, patch):
        poly.material_index = int(p)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


class Hub(zt.ZTHub, QuicConnectionProtocol):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.zt_init()
        self.h3 = H3Connection(self._quic, enable_webtransport=True)
        self.ready = asyncio.get_event_loop().create_future()

    def quic_event_received(self, event):
        for e in self.h3.handle_event(event):
            self.zt_event(e)
            if isinstance(e, HeadersReceived) and dict(e.headers).get(b":status") == b"200" and not self.ready.done():
                self.ready.set_result(e.stream_id)


OUT = queue.Queue()
STATS = {"sent": 0, "connected": False, "error": None}


async def net():
    cfg = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN, max_datagram_frame_size=65536)
    cfg.load_verify_locations(CERT)
    cfg.server_name = "localhost"
    async with connect("127.0.0.1", PORT, configuration=cfg, create_protocol=Hub) as hub:
        await hub.wait_connected()
        sid = hub._quic.get_next_available_stream_id()
        hub.h3.send_headers(
            sid,
            [
                (b":method", b"CONNECT"),
                (b":scheme", b"https"),
                (b":authority", f"127.0.0.1:{PORT}".encode()),
                (b":path", f"/room/{ROOM}".encode()),
                (b":protocol", b"webtransport"),
                (b"sec-webtransport-http3-draft02", b"1"),
            ],
        )
        hub.transmit()
        session = hub.session = await asyncio.wait_for(hub.ready, 8)
        if not os.environ.get("MB_ZT_MINTER"):
            raise RuntimeError("no MB_ZT_MINTER: tests join the closed relay (tools/zt_stack.py), never the open demo")
        await zt.admit(hub, ROOM)
        STATS["connected"] = True
        print(f"LIVE admitted by the relay", flush=True)
        print(f"LIVE connected to room {ROOM} on {PORT}", flush=True)
        while True:
            try:
                data = OUT.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.004)
                continue
            hub.h3.send_datagram(session, data)
            hub.transmit()
            STATS["sent"] += 1


def net_thread():
    try:
        asyncio.run(net())
    except Exception as e:  # shown in the console; the viewport keeps working
        STATS["error"] = repr(e)
        print("LIVE network error:", repr(e), flush=True)


tracked = []  # (gid, object name, digest)
NEXT_GID = [3]
MOVERS = []
hlc = [0, 0]


def pose_packet(gid, ob, digest):
    dg = bpy.context.evaluated_depsgraph_get()
    loc, quat, _ = ob.evaluated_get(dg).matrix_world.decompose()
    p = CM @ loc
    r = (CM @ quat.to_matrix() @ CM.transposed()).to_quaternion()  # the same rotation in the wire frame
    rx, rz, tw = swing_twist_codes((r.x, r.y, r.z, r.w))
    now = int(time.time() * 1000)
    hlc[0], hlc[1] = (now, 0) if now > hlc[0] else (hlc[0], hlc[1] + 1)
    return encode(
        Packet(
            gid=gid,
            pos_um_x=int(round(p.x * 1e6)),
            pos_um_y=int(round(p.y * 1e6)),
            pos_um_z=int(round(p.z * 1e6)),
            hlc=(hlc[0] << 16) | hlc[1],
            class_owner=(1 << 24) | 1,
            sub_index=0,
            swing_x=rx,
            swing_z=rz,
            twist=tw,
            payload=digest + bytes(4),
        )
    )


LAST = {}


def export_usda(ob):
    """Write a mesh object as .usda in the form the receivers read: Y-up points, triangles, a chart patch per triangle."""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    me.calc_loop_triangles()
    sc = ob.matrix_world.to_scale()
    pts = [(v.co.x * sc.x, v.co.z * sc.z, -v.co.y * sc.y) for v in me.vertices]  # Blender -> file: (x, z, -y)
    tri = [tuple(t.vertices) for t in me.loop_triangles]
    patches = [t.polygon_index % 24 for t in me.loop_triangles]
    ev.to_mesh_clear()
    safe = re.sub(r"[^A-Za-z0-9_]", "_", ob.name)
    lines = [
        "#usda 1.0",
        "(",
        f'    defaultPrim = "{safe}"',
        '    upAxis = "Y"',
        "    metersPerUnit = 1",
        ")",
        "",
        f'def Mesh "{safe}"',
        "{",
        "    int[] faceVertexCounts = [%s]" % ", ".join("3" for _ in tri),
        "    int[] faceVertexIndices = [%s]" % ", ".join(str(i) for t in tri for i in t),
        "    point3f[] points = [%s]" % ", ".join("(%.6f, %.6f, %.6f)" % p for p in pts),
        "    int[] primvars:patch = [%s] (" % ", ".join(str(p) for p in patches),
        '        interpolation = "uniform"',
        "    )",
        "}",
        "",
    ]
    os.makedirs(f"{GEO}/usd", exist_ok=True)
    open(f"{GEO}/usd/live_{safe}.usda", "w").write("\n".join(lines))
    return f"live_{safe}", len(tri)


def scan():
    """Publish and start tracking any mesh object that is new in the scene."""
    known = {n for _, n, _ in tracked}
    for ob in list(bpy.context.scene.objects):
        if ob.type == "MESH" and ob.name not in known:
            name, n = export_usda(ob)
            if len(ob.data.materials) == 0:
                for p in range(24):
                    m = bpy.data.materials.new(f"{ob.name}_c{p}")
                    m.diffuse_color = (lin(CHART[p][0]), lin(CHART[p][1]), lin(CHART[p][2]), 1.0)
                    ob.data.materials.append(m)
                for poly in ob.data.polygons:
                    poly.material_index = poly.index % 24
            digest = publish(name)
            tracked.append((NEXT_GID[0], ob.name, digest))
            print(
                f"LIVE new object {ob.name}: {n} triangles, published as {digest.hex()[:16]}, entity {NEXT_GID[0]}",
                flush=True,
            )
            NEXT_GID[0] += 1


def tick():
    try:
        scan()
        for gid, name, digest in tracked:
            ob = bpy.data.objects.get(name)  # by name: undo and file operations replace the Python objects
            if ob is None:
                continue
            OUT.put(pose_packet(gid, ob, digest))
            dg = bpy.context.evaluated_depsgraph_get()
            loc = tuple(round(c, 3) for c in ob.evaluated_get(dg).matrix_world.translation)
            if LAST.get(gid) != loc:
                LAST[gid] = loc
                print(
                    f"LIVE {name} at {loc}; connected={STATS['connected']} sent={STATS['sent']} error={STATS['error']}",
                    flush=True,
                )
    except Exception as e:  # never let the timer die: report and carry on
        print("LIVE tick error:", repr(e), flush=True)
    return 1.0 / HZ


def start():
    suz = make_object("Suzanne", "monkey")
    chart = make_object("Chart", "chart")
    chart.location = (0.0, 2.0, 0.0)  # wire z = -2: behind Suzanne
    suz.location = (0.0, 0.0, 0.0)
    for name, ob in (("monkey", suz), ("chart", chart)):
        print(f"LIVE published {name}: {publish(name).hex()[:16]}...", flush=True)
    tracked.append((1, "Suzanne", publish("monkey")))
    tracked.append((2, "Chart", publish("chart")))
    threading.Thread(target=net_thread, daemon=True).start()
    if os.environ.get(
        "LIVE_MOVE"
    ):  # a stand-in for dragging: three poses, each held 5 s, so the other clients can be checked
        poses = [
            ((0, 0, 0), (0, 0, 0)),
            ((2.0, 0.0, 1.0), (0, 0, 0.9)),
            ((-2.0, 0.0, -1.0), (0.7, 0, 0)),
            ((0, 0, 0), (0, 0, 0)),
        ]
        t0 = time.time()

        added = []

        def add_shapes():  # stand-in for Add > Mesh in the viewport: a cube, then a cone, once each
            if time.time() - t0 > 6 and not added:
                bpy.ops.mesh.primitive_cube_add(size=1.2, location=(3.0, 0.0, 1.5))
                added.append("cube")
            if time.time() - t0 > 14 and len(added) == 1:
                bpy.ops.mesh.primitive_cone_add(radius1=0.8, depth=1.6, location=(-3.0, 0.0, -1.5))
                added.append("cone")

        def move():
            add_shapes()
            suz = bpy.data.objects.get("Suzanne")
            if suz is None:
                return 0.25
            k = min(int((time.time() - t0 - 12) // 5) + 1, len(poses) - 1) if time.time() - t0 > 12 else 0
            loc, rot = poses[k]
            if tuple(suz.location) != loc or tuple(round(a, 6) for a in suz.rotation_euler) != tuple(
                round(a, 6) for a in rot
            ):
                suz.location, suz.rotation_euler = loc, rot
                print(f"LIVE move {k}: location {loc} rotation {rot}", flush=True)
            return 0.25

        MOVERS.append(move)
        bpy.app.timers.register(move, first_interval=0.5, persistent=True)
    bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
    print("LIVE tracking Suzanne and Chart: move them in the viewport", flush=True)
    if bpy.app.background and os.environ.get("LIVE_RENDER_DIR"):
        sc = bpy.context.scene
        sc.render.engine = "BLENDER_WORKBENCH"
        sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = 480, 360, 100
        sc.display.shading.light, sc.display.shading.color_type, sc.display.render_aa = "FLAT", "MATERIAL", "OFF"
        sc.view_settings.view_transform = "Standard"
        sc.world = bpy.data.worlds.new("w")
        sc.world.color = (lin(20), lin(20), lin(20))
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        cam.data.sensor_fit, cam.data.angle = "VERTICAL", 1.3089969
        cam.location = (0, -5.5, 0)
        cam.rotation_euler = mathutils.Vector((0, 5.5, 0)).to_track_quat("-Z", "Y").to_euler()
        sc.collection.objects.link(cam)
        sc.camera = cam
        os.makedirs(os.environ["LIVE_RENDER_DIR"], exist_ok=True)
    if bpy.app.background:
        frame_no, next_render = 0, time.time()
        end = time.time() + float(os.environ.get("LIVE_SECONDS", "60"))
        while time.time() < end:
            tick()
            if os.environ.get("LIVE_MOVE"):
                for fn in MOVERS:
                    fn()
            if os.environ.get("LIVE_RENDER_DIR") and time.time() >= next_render:
                sc.render.filepath = f"{os.environ['LIVE_RENDER_DIR']}/b_{frame_no:04d}.png"
                bpy.ops.render.render(write_still=True)
                frame_no += 1
                next_render += 0.1
            time.sleep(1.0 / HZ)


start()
