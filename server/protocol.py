"""
TL2 lobby wire protocol: framing and primitives.

Reverse-engineered from Torchlight2.exe (1.25.9.5, cross-checked on Steam 1.25.5.6
and 1.26.0.1).

Frame layout (lobby and peer connections share it):
    [type:u8] [length:u16 big-endian = payload bytes] [payload]

Primitives:
    u8 / u16 / u32 / u64   little-endian (x86 native)
    str8                   [u8 len][len bytes UTF-8]
    str16                  [u16 len][len bytes UTF-8]
    blob(n)                n raw bytes
Only the frame length header is big-endian; IP/port fields inside some messages
are also network order (see lobby_server.send_attempt_*).
"""
from __future__ import annotations

import struct


class Msg:
    """Lobby message type ids (the constant each client Serialize writes first)."""
    # --- NetManager handshake layer (the lobby connection starts with these) ---
    NET_CONNECT            = 0    # u64 peer, u32 key, u32 protocol
    NET_CONNECT_OK         = 1    # u64 peer, u32 protocol (echo), u8, u32 key, u32 relay key
    NET_DISCONNECT         = 2    # u8 reason
    SRV_CONNECT_CALLBACK   = 3
    # --- lobby layer ---
    START_LOGIN            = 8
    LOGIN_CHALLENGE        = 9
    LOGIN_RESPONSE         = 10
    LOGIN_RESULT           = 11
    GAME_SERVERS_LIST      = 13
    GAME_SERVERS_LIST_END  = 14
    CREATE_GAME_SERVER     = 15
    CREATE_GAME_RESPONSE   = 16
    UPDATE_GAME_SERVER     = 17
    REMOVE_GAME_SERVER     = 18
    GET_SERVER_DETAILS     = 19
    SERVER_DETAILS         = 20
    REQUEST_CONNECT        = 21
    ATTEMPT_CONNECT        = 22
    REQUEST_RELAY_CONNECT  = 23
    ATTEMPT_RELAY_CONNECT  = 24
    ATTEMPT_CONNECT_FAILED = 25
    JOINED_GAME            = 26
    LEFT_GAME              = 27
    ADD_FRIEND             = 28
    REMOVE_FRIEND          = 29
    ADD_ENEMY              = 30
    REMOVE_ENEMY           = 31
    LOOKUP_USER            = 32
    LOOKUP_USER_RESPONSE   = 33
    TEST_NAT_REQUEST       = 34
    FIREWALL_TEST_SUCCESS  = 35
    BEGIN_FRIENDS          = 36
    SEND_FRIEND            = 37
    ADMIN_MESSAGE          = 38
    SET_CHARACTER_DATA     = 39
    VERIFY_NEW_KEY         = 40
    VERIFY_NEW_KEY_RESULT  = 41
    ADD_FRIEND_BY_NAME     = 42
    KEEPALIVE              = 44
    REQUEST_GAME           = 45
    REPORT_CONNECTION      = 46
    SET_LANGUAGE           = 47
    SET_MOD_HASH           = 49
    SEND_MOD_COUNTS        = 50
    CREATE_MOD_GAME_SERVER = 51
    GET_MOD_GAME_SERVERS   = 52
    MOD_GAME_SERVERS_LIST  = 53
    # --- added in 1.26.0.1 (TapTap login) ---
    # Same layout as START_LOGIN except the first string is str16 (credential, <=1024
    # bytes) instead of str8 (user name).
    START_TAP_LOGIN        = 54


NAME = {v: k for k, v in vars(Msg).items() if isinstance(v, int) and not k.startswith("_")}


def msg_name(t: int) -> str:
    return NAME.get(t, f"UNKNOWN_{t}")


class Reader:
    """Reads fields from a payload in order. Raises EOFError on a short payload."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def _take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise EOFError(f"need {n} bytes, have {self.remaining()}")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        return self._take(1)[0]

    def u16(self) -> int:
        return struct.unpack_from("<H", self._take(2))[0]

    def u32(self) -> int:
        return struct.unpack_from("<I", self._take(4))[0]

    def u64(self) -> int:
        return struct.unpack_from("<Q", self._take(8))[0]

    def u16_be(self) -> int:
        return struct.unpack_from(">H", self._take(2))[0]

    def u32_be(self) -> int:
        return struct.unpack_from(">I", self._take(4))[0]

    def blob(self, n: int) -> bytes:
        return self._take(n)

    def str8(self) -> str:
        n = self.u8()
        return self._take(n).decode("utf-8", "replace")

    def str16(self) -> str:
        n = self.u16()
        return self._take(n).decode("utf-8", "replace")


class Writer:
    """Builds a payload; frame() prepends the [type][len] header."""

    def __init__(self):
        self.buf = bytearray()

    def u8(self, v: int):       self.buf += struct.pack("<B", v & 0xFF); return self
    def u16(self, v: int):      self.buf += struct.pack("<H", v & 0xFFFF); return self
    def u32(self, v: int):      self.buf += struct.pack("<I", v & 0xFFFFFFFF); return self
    def u64(self, v: int):      self.buf += struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF); return self
    def u16_be(self, v: int):   self.buf += struct.pack(">H", v & 0xFFFF); return self
    def u32_be(self, v: int):   self.buf += struct.pack(">I", v & 0xFFFFFFFF); return self
    def blob(self, b: bytes):   self.buf += b; return self

    def str8(self, s: str):
        b = s.encode("utf-8")[:255]
        self.buf += struct.pack("<B", len(b)) + b
        return self

    def str16(self, s: str):
        b = s.encode("utf-8")[:0xFFFF]
        self.buf += struct.pack("<H", len(b)) + b
        return self

    def frame(self, msg_type: int) -> bytes:
        payload = bytes(self.buf)
        return struct.pack(">BH", msg_type & 0xFF, len(payload)) + payload


HEADER_LEN = 3  # type(1) + length(2, big-endian)


def parse_header(hdr: bytes) -> tuple[int, int]:
    """Returns (msg_type, payload_len)."""
    msg_type, payload_len = struct.unpack(">BH", hdr)
    return msg_type, payload_len


def hexdump(data: bytes, limit: int = 64) -> str:
    b = data[:limit]
    s = " ".join(f"{x:02x}" for x in b)
    return s + (" …" if len(data) > limit else "")
