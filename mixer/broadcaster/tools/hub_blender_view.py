# MIT. Blender as a relay client that draws what it receives. Joins a room, records 100-byte entity packets for N
# seconds, then fetches each mesh by the SHA-256 in its payload (desync index from the CDN, chunks from the same CDN),
# builds it in bpy and renders the recorded poses at 10 frames a second with Workbench, unlit, per-face chart colours.
#   python hub_blender_view.py PORT CERT ROOM GEODIR OUTDIR SECONDS
import asyncio
import re
import struct
import subprocess
import sys
import time
import urllib.request

import bpy
import mathutils
from aioquic.asyncio import QuicConnectionProtocol, connect
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import DatagramReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration

FILER = "http://127.0.0.1:8888"
W, H, FPS = 480, 360, 10
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


class Hub(QuicConnectionProtocol):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.h3 = H3Connection(self._quic, enable_webtransport=True)
        self.ready = asyncio.get_event_loop().create_future()
        self.got = []

    def quic_event_received(self, event):
        for e in self.h3.handle_event(event):
            if isinstance(e, HeadersReceived) and dict(e.headers).get(b":status") == b"200" and not self.ready.done():
                self.ready.set_result(e.stream_id)
            elif isinstance(e, DatagramReceived):
                self.got.append((time.monotonic(), e.data))


async def record(port, cert, room, seconds):
    cfg = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN, max_datagram_frame_size=65536)
    cfg.load_verify_locations(cert)
    cfg.server_name = "localhost"
    async with connect("127.0.0.1", port, configuration=cfg, create_protocol=Hub) as hub:
        await hub.wait_connected()
        sid = hub._quic.get_next_available_stream_id()
        hub.h3.send_headers(
            sid,
            [
                (b":method", b"CONNECT"),
                (b":scheme", b"https"),
                (b":authority", f"127.0.0.1:{port}".encode()),
                (b":path", f"/room/{room}".encode()),
                (b":protocol", b"webtransport"),
                (b"sec-webtransport-http3-draft02", b"1"),
            ],
        )
        hub.transmit()
        await asyncio.wait_for(hub.ready, 8)
        print("BLENDER VIEW connected", flush=True)
        await asyncio.sleep(seconds)
        return hub.got


def arr(text, key):
    i = text.index(key)
    a = text.index("[", i)
    depth = 0
    for j in range(a, len(text)):
        depth += (text[j] == "[") - (text[j] == "]")
        if depth == 0:
            return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", text[a + 1 : j])]


def mesh_for(hexd, geodir, work):
    idx = f"{work}/{hexd}.caibx"
    open(idx, "wb").write(urllib.request.urlopen(f"{FILER}/index/{hexd}.caibx").read())
    usda = f"{work}/{hexd}.usda"
    subprocess.run([f"{geodir}/desync", "extract", "-s", f"{FILER}/chunks", idx, usda], check=True, capture_output=True)
    text = open(usda).read()
    pts, tri, patch = arr(text, "points = "), arr(text, "faceVertexIndices = "), arr(text, "primvars:patch = ")
    verts = [(pts[i], -pts[i + 2], pts[i + 1]) for i in range(0, len(pts), 3)]  # file is Y-up; Blender is Z-up
    faces = [tuple(int(v) for v in tri[i : i + 3]) for i in range(0, len(tri), 3)]
    me = bpy.data.meshes.new(hexd[:8])
    me.from_pydata(verts, [], faces)
    for p in range(24):
        m = bpy.data.materials.new(f"c{p}")
        m.diffuse_color = (lin(CHART[p][0]), lin(CHART[p][1]), lin(CHART[p][2]), 1.0)
        me.materials.append(m)
    for poly, p in zip(me.polygons, patch):
        poly.material_index = int(p)
    ob = bpy.data.objects.new(hexd[:8], me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def main(port, cert, room, geodir, out, seconds):
    got = asyncio.run(record(port, cert, room, seconds))
    pk = []
    for t, d in got:
        if len(d) == 100:
            (gid,) = struct.unpack_from("<I", d, 0)
            x, y, z = struct.unpack_from("<qqq", d, 4)
            (twist,) = struct.unpack_from("<h", d, 60)
            pk.append((t, gid, x / 1e6, y / 1e6, z / 1e6, twist, d[62:94].hex()))
    print(f"BLENDER VIEW recorded {len(pk)} packets of {len(got)} datagrams", flush=True)
    if not pk:
        sys.exit(2)
    t0 = pk[0][0]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = W, H, 100
    sc.display.shading.light = "FLAT"
    sc.display.shading.color_type = "MATERIAL"
    sc.display.render_aa = "OFF"
    sc.view_settings.view_transform = "Standard"
    sc.render.image_settings.file_format = "PNG"
    sc.world = bpy.data.worlds.new("w")
    sc.world.color = (lin(20), lin(20), lin(20))
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.sensor_fit = "VERTICAL"
    cam.data.angle = 1.3089969  # 75 degrees, as the other two
    cam.location = (0, -5.5, 0)  # Godot's (0, 0, 5.5) in Blender's axes
    cam.rotation_euler = (mathutils.Vector((0, 5.5, 0)).to_track_quat("-Z", "Y")).to_euler()
    sc.collection.objects.link(cam)
    sc.camera = cam
    work = "/tmp/blender_view_work"
    subprocess.run(["mkdir", "-p", work, out], check=True)
    objs = {}
    for hexd in sorted({p[6] for p in pk}):
        objs[hexd] = mesh_for(hexd, geodir, work)
        print(f"BLENDER VIEW mesh {hexd[:12]}: {len(objs[hexd].data.polygons)} triangles", flush=True)
    last = max(p[0] for p in pk) - t0
    n = int(last * FPS)
    for f in range(n):
        t = f / FPS + t0
        latest = {}
        for p in pk:
            if p[0] <= t:
                latest[p[6]] = p
        for hexd, p in latest.items():
            ob = objs[hexd]
            ob.location = (p[2], -p[4], p[3])
            ob.rotation_euler = (0, 0, p[5] * 6.283185307179586 / 65536)
        sc.render.filepath = f"{out}/b_{f:04d}.png"
        bpy.ops.render.render(write_still=True)
    print(f"BLENDER VIEW rendered {n} frames to {out}", flush=True)


argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
main(int(argv[0]), argv[1], argv[2], argv[3], argv[4], float(argv[5]))
