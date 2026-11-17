# An admitted listener on the closed relay: prints how each entity's pose changes. Usage: zt_listen.py ROOM SECONDS
import asyncio, os, struct, sys, time

sys.path.insert(0, os.path.dirname(__file__))
import zt
from aioquic.asyncio import QuicConnectionProtocol, connect
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import DatagramReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration

PORT, CERT = int(os.environ["MB_PORT"]), os.environ["MB_CERT"]
ROOM, SECONDS = sys.argv[1], float(sys.argv[2])


class Hub(zt.ZTHub, QuicConnectionProtocol):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.zt_init()
        self.h3 = H3Connection(self._quic, enable_webtransport=True)
        self.ready = asyncio.get_event_loop().create_future()
        self.got = []

    def quic_event_received(self, event):
        for e in self.h3.handle_event(event):
            self.zt_event(e)
            if isinstance(e, HeadersReceived) and dict(e.headers).get(b":status") == b"200" and not self.ready.done():
                self.ready.set_result(e.stream_id)
            elif isinstance(e, DatagramReceived):
                self.got.append((time.time(), e.data))


async def main():
    c = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN, max_datagram_frame_size=65536)
    c.load_verify_locations(CERT)
    c.server_name = "localhost"
    async with connect("127.0.0.1", PORT, configuration=c, create_protocol=Hub) as h:
        await h.wait_connected()
        sid = h._quic.get_next_available_stream_id()
        h.h3.send_headers(
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
        h.transmit()
        h.session = await asyncio.wait_for(h.ready, 8)
        await zt.admit(h, ROOM)
        print("LISTEN admitted", flush=True)
        await asyncio.sleep(SECONDS)
    seen, t0 = {}, None
    for t, d in h.got:
        if len(d) != 100:
            continue
        gid = struct.unpack_from("<I", d, 0)[0]
        pos = tuple(round(v / 1e6, 2) for v in struct.unpack_from("<3q", d, 4))
        dg = d[62:94].hex()
        if any(d[62:94]) and seen.get(("dg", gid)) != dg:
            seen[("dg", gid)] = dg
            print("LISTEN digest", dg, flush=True)
        t0 = t0 or t
        if seen.get(gid, [None])[-1] != pos:
            seen.setdefault(gid, []).append(pos)
            print(f"LISTEN t={t - t0:5.1f}s entity {gid} at {pos}", flush=True)
    print(f"LISTEN {sum(1 for _, d in h.got if len(d) == 100)} packets, entities {sorted(seen)}")


asyncio.run(main())
