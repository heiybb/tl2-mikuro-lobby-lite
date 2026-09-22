"""
StartTapLogin(54) checks: the 1.26.0.1 TapTap login handshake.

    python test_tap_login.py                  # offline format checks only
    python test_tap_login.py --port 4549      # plus a full handshake against a server

StartTapLogin and StartLogin(8) serialize identically except for the type byte and
the first string: StartLogin writes the user name as str8, StartTapLogin writes the
credential ("kid,mac_key") as str16. The client's size functions give fixed overheads
of 0x15 and 0x16 bytes respectively, which the size assertions below check.
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys

import login_hash
from protocol import Msg, Reader, Writer, msg_name

KID = "Player_abc123"
CRED = f"{KID},-"
TOKEN = "abcdef0123"


def build_start_tap_login(f0=1, guid=0x1122334455, ver=126, f3=0, credential=CRED, token=TOKEN) -> bytes:
    return Writer().u16(f0).u64(guid).u32(ver).u16(f3).str16(credential).str8(token).frame(Msg.START_TAP_LOGIN)


def build_start_login(f0=1, guid=0x1122334455, ver=125, f3=0, user="tester", token=TOKEN) -> bytes:
    return Writer().u16(f0).u64(guid).u32(ver).u16(f3).str8(user).str8(token).frame(Msg.START_LOGIN)


def test_frame_layout():
    frame = build_start_tap_login()
    assert frame[0] == 54 == Msg.START_TAP_LOGIN
    assert struct.unpack_from(">H", frame, 1)[0] == len(frame) - 3
    r = Reader(frame[3:])
    assert (r.u16(), r.u64(), r.u32(), r.u16()) == (1, 0x1122334455, 126, 0)
    assert r.str16() == CRED and r.str8() == TOKEN and r.remaining() == 0


def test_size_formula_matches_client():
    for cred, token in (("", ""), (CRED, TOKEN), ("x" * 1024, "y" * 255)):
        assert len(build_start_tap_login(credential=cred, token=token)) == len(cred) + len(token) + 0x16
        assert len(build_start_login(user=cred[:255], token=token)) == len(cred[:255]) + len(token) + 0x15


def test_only_first_string_differs():
    same = dict(f0=1, guid=0x1122334455, ver=125, f3=0, token=TOKEN)
    tap = build_start_tap_login(credential="abc", **same)
    classic = build_start_login(user="abc", **same)
    assert tap[3:19] == classic[3:19]
    assert tap[19:24] == struct.pack("<H", 3) + b"abc"
    assert classic[19:23] == bytes([3]) + b"abc"
    assert tap[24:] == classic[23:]


def run_handshake(host: str, port: int) -> None:
    from test_client import expect

    s = socket.create_connection((host, port), timeout=5)
    s.sendall(Writer().u64(0x1001900090005).u32(0).u32(0x101).frame(Msg.NET_CONNECT))
    expect(s, Msg.NET_CONNECT_OK)
    s.sendall(build_start_tap_login())
    c = Reader(expect(s, Msg.LOGIN_CHALLENGE))
    c1, c2 = c.str8(), c.str8()
    # TapTap mode clears the password, so the client answers with the empty-password chain
    s.sendall(Writer().blob(bytes.fromhex(login_hash.client_response("", c1, c2))).frame(Msg.LOGIN_RESPONSE))
    code = Reader(expect(s, Msg.LOGIN_RESULT)).u8()
    print(f"  LoginResult code={code} ({'allowed' if code == 0 else 'refused'})")
    s.close()
    if code != 0:
        raise SystemExit("[FAIL] TapTap login refused (ALLOW_TAPTAP=0?)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="given: also run a full handshake")
    a = ap.parse_args()
    test_frame_layout()
    test_size_formula_matches_client()
    test_only_first_string_differs()
    print("[OK] StartTapLogin format checks")
    if a.port:
        run_handshake(a.host, a.port)
        print("[OK] TapTap handshake")
    return 0


if __name__ == "__main__":
    sys.exit(main())
