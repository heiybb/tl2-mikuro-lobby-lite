"""
TL2 client login hash chain.

On LoginChallenge{str8 c1, str8 c2} the client answers with:

    h = SHA256(utf8(password) + c1)
    repeat 19 times: h = SHA256(hex_lower(h))
    LoginResponse = SHA256(h + c2)          # 32 raw bytes on the wire

Both challenges come from the server. The lite server uses this for the optional
shared server password (LOBBY_PASSWORD): c1 is a fresh random salt per login and
c2 a one-time challenge, so the password itself never crosses the wire.

Limits, stated plainly: the chain is only 20 rounds of SHA-256 and the lobby is
plain TCP. Someone who sniffs (c1, c2, response) can brute-force a weak password
offline. Use a long random server password, and treat it as a "keep strangers
out" gate, not as strong authentication.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

ROUNDS = 19
_HEX64 = re.compile(rb"[0-9a-fA-F]{64}")


def derive_verifier(password: str, salt: str) -> bytes:
    """SHA256(pw + c1), then 19 rounds of SHA256(hex(h)). Returns 32 bytes."""
    h = hashlib.sha256(password.encode("utf-8") + salt.encode("utf-8")).digest()
    for _ in range(ROUNDS):
        h = hashlib.sha256(h.hex().encode("ascii")).digest()
    return h


def expected_response(verifier: bytes, challenge2: str) -> str:
    """SHA256(verifier + c2) as 64 lowercase hex chars."""
    return hashlib.sha256(verifier + challenge2.encode("utf-8")).hexdigest()


def client_response(password: str, salt: str, challenge2: str) -> str:
    """The whole client-side computation (used by the test clients)."""
    return expected_response(derive_verifier(password, salt), challenge2)


def new_salt(nbytes: int = 12) -> str:
    return secrets.token_hex(nbytes)


def new_challenge(nbytes: int = 16) -> str:
    # the client passes the c2 length as a u8, so it must stay <= 255 bytes
    return secrets.token_hex(nbytes)


def extract_response_hex(payload: bytes) -> str | None:
    """Pull the response out of a LoginResponse payload.

    Real clients send the 32-byte digest raw. A 64-char hex string (with or without a
    length prefix) is accepted too, which is what the bundled test clients send.
    """
    m = _HEX64.search(payload)
    if m:
        return m.group(0).decode("ascii").lower()
    if len(payload) == 32:
        return payload.hex()
    return None


def verify(verifier: bytes, challenge2: str, response_hex: str | None) -> bool:
    if not response_hex:
        return False
    return hmac.compare_digest(expected_response(verifier, challenge2), response_hex.lower())
