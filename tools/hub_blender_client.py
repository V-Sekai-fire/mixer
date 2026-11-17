# MIT. Blender client of the mixer-broadcaster hub: join a room over WebTransport (aioquic), take the ColorChecker
# snapshot, build 24 flat planes in bpy, render a real frame and print the pixel it drew for every patch.
#   python hub_blender_client.py PORT CERT ROOM [--set id,r,g,b] [--watch seconds]
import asyncio
import json
import struct
import sys
import time
import zlib

import bpy
from aioquic.asyncio import QuicConnectionProtocol, connect
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import DatagramReceived, HeadersReceived, WebTransportStreamDataReceived
from aioquic.quic.configuration import QuicConfiguration

COLS, ROWS, STEP, W, H = 6, 4, 1.2, 480, 320


class Hub(QuicConnectionProtocol):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.h3 = H3Connection(self._quic, enable_webtransport=True)
        self.ready = asyncio.get_event_loop().create_future()
        self.session = None
        self.replies = {}
        self.datagrams = asyncio.Queue()

    def quic_event_received(self, event):
        for e in self.h3.handle_event(event):
            if isinstance(e, HeadersReceived) and dict(e.headers).get(b":status") == b"200" and not self.ready.done():
                self.ready.set_result(e.stream_id)
            elif isinstance(e, WebTransportStreamDataReceived):
                self.replies[e.stream_id] = self.replies.get(e.stream_id, b"") + e.data
            elif isinstance(e, DatagramReceived):
                self.datagrams.put_nowait(json.loads(e.data))

    async def open(self, host, port, room):
        sid = self._quic.get_next_available_stream_id()
        self.h3.send_headers(
            sid,
            [
                (b":method", b"CONNECT"),
                (b":scheme", b"https"),
                (b":authority", f"{host}:{port}".encode()),
                (b":path", f"/room/{room}".encode()),
                (b":protocol", b"webtransport"),
                (b"sec-webtransport-http3-draft02", b"1"),
            ],
        )
        self.transmit()
        self.session = await asyncio.wait_for(self.ready, 8)

    async def send(self, obj):
        sid = self.h3.create_webtransport_stream(self.session)
        self._quic.send_stream_data(sid, json.dumps(obj).encode(), end_stream=True)
        self.transmit()
        for _ in range(60):
            await asyncio.sleep(0.05)
            if sid in self.replies:
                return json.loads(self.replies[sid])
        return None


def to_linear(c8):
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def build_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = W, H, 100
    sc.display.shading.light = "FLAT"
    sc.display.shading.color_type = "OBJECT"
    sc.display.render_aa = "OFF"
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.display_settings.display_device = "sRGB"
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.render.image_settings.color_depth = "8"
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.sensor_fit = "VERTICAL"
    cam_data.ortho_scale = ROWS * STEP + 0.4
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = (0, 0, 5)
    sc.collection.objects.link(cam)
    sc.camera = cam
    quads = []
    for i in range(24):
        mesh = bpy.data.meshes.new(f"p{i}")
        mesh.from_pydata([(-0.5, -0.5, 0), (0.5, -0.5, 0), (0.5, 0.5, 0), (-0.5, 0.5, 0)], [], [(0, 1, 2, 3)])
        ob = bpy.data.objects.new(f"patch{i}", mesh)
        ob.location = ((i % COLS - 2.5) * STEP, (1.5 - i // COLS) * STEP, 0)
        sc.collection.objects.link(ob)
        quads.append(ob)
    return sc, quads


def paint(quads, patches):
    for ob, (r, g, b) in zip(quads, patches):
        ob.color = (to_linear(r), to_linear(g), to_linear(b), 1.0)  # Object.color is scene-linear


def read_png(path):
    data = open(path, "rb").read()
    pos, idat, w, h, ct = 8, b"", 0, 0, 0
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, ct = struct.unpack(">IIBB", body[:10])
        elif typ == b"IDAT":
            idat += body
        pos += 12 + ln
    bpp = {2: 3, 6: 4}[ct]
    raw, stride, rows, prev = zlib.decompress(idat), w * bpp, [], bytearray(w * bpp)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, bpp, rows


def render_pixels(sc, tag):
    sc.render.filepath = f"/tmp/blender_client_{tag}.png"
    bpy.ops.render.render(write_still=True)
    w, h, bpp, rows = read_png(sc.render.filepath)
    scale = h / (ROWS * STEP + 0.4)
    out = []
    for i in range(24):
        x = int(w / 2 + (i % COLS - 2.5) * STEP * scale)
        y = int(h / 2 - (1.5 - i // COLS) * STEP * scale)
        out.append(list(rows[y][x * bpp : x * bpp + 3]))
    return out


async def main(port, cert, room, set_arg, watch):
    cfg = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN, max_datagram_frame_size=65536)
    cfg.load_verify_locations(cert)
    cfg.server_name = "localhost"
    async with connect("127.0.0.1", port, configuration=cfg, create_protocol=Hub) as hub:
        await hub.wait_connected()
        await hub.open("127.0.0.1", port, room)
        await hub.send({"t": "hello"})
        snap = await asyncio.wait_for(hub.datagrams.get(), 5)
        patches = snap["patches"]
        print("BLENDER snapshot:", len(patches), "patches, first", patches[0], flush=True)
        sc, quads = build_scene()
        paint(quads, patches)
        print("BLENDER RENDER PIXELS", json.dumps(render_pixels(sc, "render")), flush=True)
        print("BLENDER RENDER SNAPSHOT", json.dumps(patches), flush=True)
        if set_arg:
            i, r, g, b = (int(v) for v in set_arg.split(","))
            print(
                "BLENDER sent set", set_arg, "->", await hub.send({"t": "set", "id": i, "rgb": [r, g, b]}), flush=True
            )
            patches[i] = [r, g, b]
        if watch:
            end = time.time() + watch
            while time.time() < end:
                try:
                    msg = await asyncio.wait_for(hub.datagrams.get(), 0.3)
                except asyncio.TimeoutError:
                    continue
                if msg.get("t") == "set":
                    print("BLENDER heard set", msg, flush=True)
                    patches[msg["id"]] = msg["rgb"]
            paint(quads, patches)
            print("BLENDER AFTER_WATCH PIXELS", json.dumps(render_pixels(sc, "after")), flush=True)
            print("BLENDER AFTER_WATCH SNAPSHOT", json.dumps(patches), flush=True)


argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
set_arg = argv[argv.index("--set") + 1] if "--set" in argv else None
watch = float(argv[argv.index("--watch") + 1]) if "--watch" in argv else 0
asyncio.run(main(int(argv[0]), argv[1], argv[2], set_arg, watch))
