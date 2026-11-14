"""
StartSteamLogin(55) checks: the 1.27.0.0 Steam Web Ticket login handshake.

    python test_steam_login.py                  # offline format checks only
    python test_steam_login.py --port 4549      # plus a full handshake against a server

1.27.0.0 logs in through a Steam auth-ticket exchange. The game first trades its Steam
ticket for a credential at the auth endpoint (tap-auth/), then sends StartSteamLogin to
the lobby. Its payload, after the [type][len] frame header, is a fixed 81-byte block
plus a name:

    u16 | u64 guid | u32 | u16 (lobbyPort ^ connKey) | blob(64) credential |
    u8 nameLen | name

The first three fields have the same shape as classic StartLogin (character info; the u64
is the character GUID, not a SteamID, as seen from a real 1.27 client).

Unlike classic login there is NO challenge-response: the real client never answers a
LoginChallenge here and just waits for LoginResult (success check at 0x0069BE80), so the
lobby must reply LoginResult directly. This test acts exactly like that client: it never
sends LoginResponse.
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys

from protocol import Msg, Reader, Writer

NAME = "Player_abc123"
GUID = 0x01B2DE0B0000B2EB
CRED = bytes(range(64))            # the 64-byte credential is opaque to the lobby


def build_start_steam_login(f0=1, guid=GUID, f2=0, obf_port=0x1234,
                            credential=CRED, name=NAME) -> bytes:
    cred = bytes(credential)[:64].ljust(64, b"\0")
    return (Writer().u16(f0).u64(guid).u32(f2).u16(obf_port)
            .blob(cred).str8(name).frame(Msg.START_STEAM_LOGIN))


def test_frame_layout():
    frame = build_start_steam_login()
    assert frame[0] == 55 == Msg.START_STEAM_LOGIN
    assert struct.unpack_from(">H", frame, 1)[0] == len(frame) - 3
    r = Reader(frame[3:])
    assert r.u16() == 1
    assert r.u64() == GUID
    assert r.u32() == 0
    assert r.u16() == 0x1234
    assert r.blob(64) == CRED
    assert r.str8() == NAME and r.remaining() == 0


def test_fixed_block_is_81_bytes():
    # 2 + 8 + 4 + 2 + 64 + 1(nameLen) = 81, matching the client's minimum-size check
    for name in ("", NAME, "x" * 255):
        frame = build_start_steam_login(name=name)
        assert len(frame) == 3 + 81 + len(name)


def run_handshake(host: str, port: int) -> None:
    from test_client import expect

    s = socket.create_connection((host, port), timeout=5)
    s.sendall(Writer().u64(0x1001b00000000).u32(0).u32(0x1).frame(Msg.NET_CONNECT))
    expect(s, Msg.NET_CONNECT_OK)
    s.sendall(build_start_steam_login())
    # Like the real client: no LoginResponse is ever sent. The very next message must be
    # LoginResult; a LoginChallenge here would leave the real client hanging for 30 s.
    code = Reader(expect(s, Msg.LOGIN_RESULT)).u8()
    print(f"  LoginResult code={code} ({'allowed' if code == 0 else 'refused'})")
    s.close()
    if code != 0:
        raise SystemExit("[FAIL] Steam login refused (ALLOW_STEAM=0?)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="given: also run a full handshake")
    a = ap.parse_args()
    test_frame_layout()
    test_fixed_block_is_81_bytes()
    print("[OK] StartSteamLogin format checks")
    if a.port:
        run_handshake(a.host, a.port)
        print("[OK] Steam handshake")
    return 0


if __name__ == "__main__":
    sys.exit(main())
