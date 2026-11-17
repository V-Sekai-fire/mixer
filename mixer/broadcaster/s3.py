# MIT License. Copyright (c) 2026-present K. S. Ernest (iFire) Lee
"""
The few S3 calls the add-on needs (PUT and GET one object) against SeaweedFS's S3 gateway, signed with AWS Signature
V4, standard library only. Keys come from AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, never from the wire.
"""

import datetime
import hashlib
import hmac
import os
import urllib.parse
import urllib.request
from typing import Optional

REGION, SERVICE = "us-east-1", "s3"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign(
    method: str, host: str, path: str, payload: bytes, access: str, secret: str, now: Optional[datetime.datetime] = None
):
    """The headers that sign `method path` for `host`. `path` is the already encoded absolute path."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    ph = hashlib.sha256(payload).hexdigest()
    canon_headers = f"host:{host}\nx-amz-content-sha256:{ph}\nx-amz-date:{amz}\n"
    signed = "host;x-amz-content-sha256;x-amz-date"
    canon = "\n".join([method, path, "", canon_headers, signed, ph])
    scope = f"{day}/{REGION}/{SERVICE}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
    k = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), day), REGION), SERVICE), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "x-amz-date": amz,
        "x-amz-content-sha256": ph,
        "Authorization": f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed}, Signature={sig}",
    }


def _request(method: str, endpoint: str, bucket: str, key: str, payload: bytes = b""):
    access, secret = os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"]
    host = urllib.parse.urlsplit(endpoint).netloc
    path = "/" + urllib.parse.quote(f"{bucket}/{key}")
    req = urllib.request.Request(
        endpoint.rstrip("/") + path,
        data=payload if method == "PUT" else None,
        method=method,
        headers=sign(method, host, path, payload, access, secret),
    )
    return urllib.request.urlopen(req)


def put(endpoint: str, bucket: str, key: str, data: bytes) -> None:
    _request("PUT", endpoint, bucket, key, data).read()


def get(endpoint: str, bucket: str, key: str) -> bytes:
    return _request("GET", endpoint, bucket, key).read()
