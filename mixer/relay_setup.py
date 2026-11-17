# MIT License. Copyright (c) 2026-present K. S. Ernest (iFire) Lee
"""
Joins the closed relay when Blender is started with its settings in the environment (the demo's `tools/zt_stack.py`
prints them): MB_CERT (the relay's certificate), MB_ZT_MINTER (the minter's URL), MB_PORT, MB_ROOM. Without MB_CERT
nothing changes and the add-on talks to the Python server as before.
"""

import base64
import json
import os
import urllib.request

from mixer.broadcaster.client import Client


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _credentials():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    room = os.environ.get("MB_ROOM", "live")
    req = urllib.request.Request(
        os.environ["MB_ZT_MINTER"] + "/mint",
        data=json.dumps({"rooms": [room], "key": _b64(pub)}).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        token = base64.urlsafe_b64decode(json.load(r)["token"] + "==")

    def sign(nonce: bytes) -> bytes:
        r_, s_ = decode_dss_signature(priv.sign(b"mixer-session-v1" + nonce, ec.ECDSA(hashes.SHA256())))
        return r_.to_bytes(32, "big") + s_.to_bytes(32, "big")

    return token, pub, sign


def apply() -> bool:
    cert = os.environ.get("MB_CERT")
    if not cert or "MB_ZT_MINTER" not in os.environ:
        return False
    from mixer.bl_utils import get_mixer_prefs

    prefs = get_mixer_prefs()
    prefs.host, prefs.port = "127.0.0.1", int(os.environ.get("MB_PORT", "4466"))
    Client.relay_cafile = cert
    Client.relay_credentials = _credentials
    return True
