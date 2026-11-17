# MIT License. Copyright (c) 2026-present K. S. Ernest (iFire) Lee
# (Permission notice as in the other files of this package.)
"""
A mesh as the viewers read it: ASCII USD (Y up) chunked by `desync make` into the SeaweedFS filer's chunk store, with the
index stored under its own SHA-256 (that digest is the mesh's name in an entity packet's payload). No `bpy` here.
"""

import hashlib
import os
import subprocess
import tempfile
from typing import List, Sequence, Tuple

from mixer.broadcaster import s3
from mixer.broadcaster.entity import DIGEST_LEN


def usda(
    name: str, points_blender: Sequence[Tuple[float, float, float]], tris: Sequence[Tuple[int, int, int]]
) -> bytes:
    """One Mesh prim. Blender (x, y, z) is written as (x, z, -y), the wire's frame."""
    n = len(points_blender)
    if any(not 0 <= i < n for t in tris for i in t):
        raise ValueError("a triangle names a point that does not exist")
    f = lambda v: "(%.6f, %.6f, %.6f)" % v  # noqa: E731
    lines = [
        "#usda 1.0",
        "(",
        '    defaultPrim = "%s"' % name,
        '    upAxis = "Y"',
        "    metersPerUnit = 1",
        ")",
        "",
        'def Mesh "%s"' % name,
        "{",
        "    int[] faceVertexCounts = [%s]" % ", ".join("3" for _ in tris),
        "    int[] faceVertexIndices = [%s]" % ", ".join(str(i) for t in tris for i in t),
        "    point3f[] points = [%s]" % ", ".join(f((x, z, -y)) for x, y, z in points_blender),
        "}",
        "",
    ]
    return "\n".join(lines).encode()


def publish(data: bytes, desync: str, endpoint: str, bucket: str = "mixer") -> bytes:
    """desync make into SeaweedFS's S3 (`bucket`/chunks), put the index at `bucket`/index/<digest>.caibx; return the
    32-byte digest. Credentials come from AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."""
    store = "s3+" + endpoint.rstrip("/") + f"/{bucket}/chunks"
    with tempfile.TemporaryDirectory() as d:
        src, idx = os.path.join(d, "m.usda"), os.path.join(d, "m.caibx")
        open(src, "wb").write(data)
        subprocess.run([desync, "make", "-s", store, idx, src], check=True, capture_output=True)
        index = open(idx, "rb").read()
    digest = hashlib.sha256(index).digest()
    assert len(digest) == DIGEST_LEN
    s3.put(endpoint, bucket, f"index/{digest.hex()}.caibx", index)
    return digest
