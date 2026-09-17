"""Registration proofs for the live CAD Link session.

WG and the add-in prove to each other that they read the same
``registrationSecret`` from WG's endpoint file, without either sending it
(docs/reference/CADLINK-LIVE-PROTOCOL.md, "Registration and mutual proof"):

    clientProof = HMAC-SHA256(secret, "wglink-client\\n" + clientNonce + "\\n" + instanceId + "\\n" + installationId)
    serverProof = HMAC-SHA256(secret, "wglink-server\\n" + clientNonce + "\\n" + instanceId + "\\n" + installationId)

The key is the UTF-8 bytes of the secret exactly as the file holds it, the
message the UTF-8 bytes of the labelled string. Nonces and proofs are unpadded
base64url of exactly 32 bytes; anything else does not verify.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re


CLIENT_LABEL = "wglink-client"
SERVER_LABEL = "wglink-server"
#: 32 bytes as unpadded base64url is 43 characters.
_ENCODED_32_BYTES = re.compile(r"[A-Za-z0-9_-]{43}")


def encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_32(text: object) -> bytes | None:
    """The 32 bytes an unpadded base64url string holds, or None.

    Canonical encodings only: a string whose unused trailing bits are set
    decodes to the same bytes as another, so it is refused.
    """

    if not isinstance(text, str) or _ENCODED_32_BYTES.fullmatch(text) is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(text + "=")
    except (binascii.Error, ValueError):
        return None
    if len(raw) != 32 or encode(raw) != text:
        return None
    return raw


def _mac(secret: str, label: str, nonce: str, instance_id: str, installation_id: str) -> bytes:
    message = f"{label}\n{nonce}\n{instance_id}\n{installation_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()


def client_proof(secret: str, nonce: str, instance_id: str, installation_id: str) -> str:
    return encode(_mac(secret, CLIENT_LABEL, nonce, instance_id, installation_id))


def server_proof(secret: str, nonce: str, instance_id: str, installation_id: str) -> str:
    return encode(_mac(secret, SERVER_LABEL, nonce, instance_id, installation_id))


def verify_client_proof(
    secret: str, nonce: object, proof: object, instance_id: str, installation_id: str
) -> bool:
    """Whether ``proof`` is the client proof for this nonce, instance and installation."""

    if decode_32(nonce) is None:
        return False
    presented = decode_32(proof)
    if presented is None:
        return False
    expected = _mac(secret, CLIENT_LABEL, str(nonce), instance_id, installation_id)
    return hmac.compare_digest(expected, presented)


__all__ = [
    "CLIENT_LABEL",
    "SERVER_LABEL",
    "client_proof",
    "decode_32",
    "encode",
    "server_proof",
    "verify_client_proof",
]
