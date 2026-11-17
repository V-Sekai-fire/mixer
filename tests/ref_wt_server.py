"""Smallest aioquic WebTransport server: accepts CONNECT and echoes datagrams. A reference to probe browsers against."""

import asyncio
import sys

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import DatagramReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration


class Proto(QuicConnectionProtocol):
    def quic_event_received(self, event):
        if not hasattr(self, "h3"):
            self.h3 = H3Connection(self._quic, enable_webtransport=True)
        for e in self.h3.handle_event(event):
            if isinstance(e, HeadersReceived):
                h = dict(e.headers)
                if h.get(b":method") == b"CONNECT" and h.get(b":protocol") == b"webtransport":
                    self.h3.send_headers(
                        e.stream_id, [(b":status", b"200"), (b"sec-webtransport-http3-draft", b"draft02")]
                    )
                    self.session = e.stream_id
                    print("CONNECT accepted", flush=True)
            elif isinstance(e, DatagramReceived):
                self.h3.send_datagram(e.stream_id, e.data)
        self.transmit()


async def main(cert, key, port):
    cfg = QuicConfiguration(is_client=False, alpn_protocols=H3_ALPN, max_datagram_frame_size=65536)
    cfg.load_cert_chain(cert, key)
    await serve("0.0.0.0", port, configuration=cfg, create_protocol=Proto)
    print("ref server up", flush=True)
    await asyncio.Future()


asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
