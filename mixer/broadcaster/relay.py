# MIT License
#
# Copyright (c) 2026-present K. S. Ernest (iFire) Lee
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
# Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""
A client of the relay's Mixer mode (`/mixer`). No `bpy`, no `mixer.*` import: this module is on the MIT side.

  * One WebTransport session over HTTP/3 (aioquic).
  * The first bidirectional stream carries the login as JSON (`hello`, `challenge`, `auth`). The proof is made by the
    `sign` function the caller passes in, so the key can live in a guest ELF and never in this module.
  * Commands travel as 100-byte packets on one more bidirectional stream (`entity.pack_command`). Bulk data that does not
    fit a packet goes to the chunk store (SeaweedFS S3) under its SHA-256 and the packet holds the digest.
  * Poses are 100-byte datagrams.
"""

import asyncio
import base64
import hashlib
import json
import time
import urllib.request
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from aioquic.asyncio import QuicConnectionProtocol, connect
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import DatagramReceived, HeadersReceived, WebTransportStreamDataReceived
from aioquic.quic.configuration import QuicConfiguration

from . import entity, s3

PATH = b"/mixer"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class S3Store:
    """The chunk store: SeaweedFS's S3 gateway, objects at `<bucket>/blob/<hex digest>` named by their SHA-256.
    Keys: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY. The same layout as the relay's `Store.S3`."""

    def __init__(self, endpoint: str = "http://127.0.0.1:8333", bucket: str = "mixer") -> None:
        self.endpoint, self.bucket = endpoint, bucket

    def put(self, data: bytes) -> bytes:
        digest = hashlib.sha256(data).digest()
        s3.put(self.endpoint, self.bucket, f"blob/{digest.hex()}", data)
        return digest

    def get(self, digest: bytes) -> bytes:
        data = s3.get(self.endpoint, self.bucket, f"blob/{digest.hex()}")
        if hashlib.sha256(data).digest() != digest:
            raise ValueError("the store returned other bytes than the digest names")
        return data


class RelayClient(QuicConnectionProtocol):
    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.h3 = H3Connection(self._quic, enable_webtransport=True)
        self.session: Optional[int] = None
        self.store = S3Store()
        self.owner = 0
        self.commands: "asyncio.Queue[Tuple[int, int, bytes]]" = asyncio.Queue()  # (type, owner, data)
        self.poses: "asyncio.Queue[bytes]" = asyncio.Queue()
        self.errors: List[str] = []
        self._ready: "asyncio.Future[int]" = asyncio.get_event_loop().create_future()
        self._replies: Dict[int, bytes] = {}
        self._packet_stream: Optional[int] = None
        self._inbuf = bytearray()
        self._hlc_counter = 0

    # ---- events ----

    def quic_event_received(self, event) -> None:
        for e in self.h3.handle_event(event):
            if isinstance(e, HeadersReceived) and dict(e.headers).get(b":status") == b"200" and not self._ready.done():
                self._ready.set_result(e.stream_id)
            elif isinstance(e, DatagramReceived):
                self.poses.put_nowait(e.data)
            elif isinstance(e, WebTransportStreamDataReceived):
                if e.stream_id == self._packet_stream:
                    self._packets(e.data)
                else:
                    self._replies[e.stream_id] = self._replies.get(e.stream_id, b"") + e.data

    def _packets(self, data: bytes) -> None:
        self._inbuf += data
        while len(self._inbuf) >= entity.PACKET_SIZE:
            raw = bytes(self._inbuf[: entity.PACKET_SIZE])
            del self._inbuf[: entity.PACKET_SIZE]
            try:
                self.commands.put_nowait(entity.unpack_command(raw, self.store.get))
            except (ValueError, OSError) as err:
                self.errors.append(str(err))

    # ---- the door ----

    async def _ask(self, obj: dict) -> Optional[dict]:
        sid = self.h3.create_webtransport_stream(self.session)
        self._quic.send_stream_data(sid, json.dumps(obj).encode(), end_stream=True)
        self.transmit()
        for _ in range(200):
            await asyncio.sleep(0.05)
            if sid in self._replies:
                return json.loads(self._replies[sid])
        return None

    async def login(self, token: bytes, pub: bytes, sign: Callable[[bytes], bytes]) -> None:
        """hello, challenge, auth. `sign(nonce)` returns the 64-byte r||s proof for "mixer-session-v1" + nonce."""
        ch = await self._ask({"t": "hello"})
        if not ch or ch.get("t") != "challenge":
            raise RuntimeError(f"no challenge from the relay: {ch}")
        nonce = base64.urlsafe_b64decode(ch["nonce"] + "==")
        ok = await self._ask({"t": "auth", "token": _b64(token), "key": _b64(pub), "sig": _b64(sign(nonce))})
        if not ok or ok.get("t") != "ready":
            raise RuntimeError(f"the relay refused: {ok}")

    # ---- commands and poses ----

    def _hlc(self) -> int:
        self._hlc_counter = (self._hlc_counter + 1) & 0xFFFF
        return (int(time.time() * 1000) << 16) | self._hlc_counter

    def send_command(self, command_type: int, data: bytes) -> None:
        """One Mixer command as a packet. Data over 77 bytes goes to the chunk store first."""
        if self._packet_stream is None:
            self._packet_stream = self.h3.create_webtransport_stream(self.session)
        raw = entity.pack_command(command_type, data, self.owner, self.store.put, self._hlc())
        self._quic.send_stream_data(self._packet_stream, raw)
        self.transmit()

    def send_pose(self, packet: bytes) -> None:
        if len(packet) != entity.PACKET_SIZE:
            raise ValueError("a pose is one 100-byte packet")
        self.h3.send_datagram(self.session, packet)
        self.transmit()

    async def next_command(self, timeout: float = 5.0) -> Tuple[int, int, bytes]:
        return await asyncio.wait_for(self.commands.get(), timeout)

    async def next_pose(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self.poses.get(), timeout)


class open_relay:
    """`async with open_relay(host, port, cafile) as client:` opens the WebTransport session at `/mixer`."""

    def __init__(self, host: str, port: int, cafile: str, server_name: str = "localhost") -> None:
        self.host, self.port = host, port
        c = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN, max_datagram_frame_size=65536)
        c.load_verify_locations(cafile)
        c.server_name = server_name
        self._config = c
        self._ctx = None

    async def __aenter__(self) -> RelayClient:
        self._ctx = connect(self.host, self.port, configuration=self._config, create_protocol=RelayClient)
        client = await self._ctx.__aenter__()
        await client.wait_connected()
        sid = client._quic.get_next_available_stream_id()
        client.h3.send_headers(
            sid,
            [
                (b":method", b"CONNECT"),
                (b":scheme", b"https"),
                (b":authority", f"{self.host}:{self.port}".encode()),
                (b":path", PATH),
                (b":protocol", b"webtransport"),
                (b"sec-webtransport-http3-draft02", b"1"),
            ],
        )
        client.transmit()
        client.session = await asyncio.wait_for(client._ready, 8)
        return client

    async def __aexit__(self, *exc) -> None:
        await self._ctx.__aexit__(*exc)
