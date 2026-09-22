"""
Smoke-test client: plays a TL2 client's lobby handshake against a running server.
Not the real game, just enough to check a deployment.

    python test_client.py --host your.server --port 4549
    python test_client.py --password secret                    # server with LOBBY_PASSWORD
    python test_client.py --password wrong --expect-reject
"""
from __future__ import annotations

import argparse
import socket

import login_hash
from protocol import Msg, Reader, Writer, msg_name, parse_header, HEADER_LEN


def recv_frame(sock: socket.socket) -> tuple[int, bytes]:
    t, plen = parse_header(_recvn(sock, HEADER_LEN))
    return t, _recvn(sock, plen)


def _recvn(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("server closed the connection")
        buf += chunk
    return buf


UNSOLICITED = {Msg.FIREWALL_TEST_SUCCESS}   # pushed by the server, skip while waiting for replies


def expect(sock, want: int) -> bytes:
    while True:
        t, p = recv_frame(sock)
        if t == want:
            return p
        if t in UNSOLICITED:
            continue
        raise AssertionError(f"expected {msg_name(want)}, got {msg_name(t)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4549)
    ap.add_argument("--user", default="Tester")
    ap.add_argument("--password", default="")
    ap.add_argument("--expect-reject", action="store_true")
    args = ap.parse_args()

    s = socket.create_connection((args.host, args.port), timeout=5)
    s.sendall(Writer().u64(0x1001900090005).u32(0).u32(0x101).frame(Msg.NET_CONNECT))
    expect(s, Msg.NET_CONNECT_OK)

    s.sendall(Writer().u16(1).u64(0x1122334455).u32(125).u16(0)
              .str8(args.user).str8("").frame(Msg.START_LOGIN))
    chal = Reader(expect(s, Msg.LOGIN_CHALLENGE))
    c1, c2 = chal.str8(), chal.str8()
    # real clients send the 32 raw bytes
    digest = bytes.fromhex(login_hash.client_response(args.password, c1, c2))
    s.sendall(Writer().blob(digest).frame(Msg.LOGIN_RESPONSE))
    code = Reader(expect(s, Msg.LOGIN_RESULT)).u8()
    if args.expect_reject:
        if code == 0:
            raise SystemExit("[FAIL] expected a refusal, but the login succeeded")
        print("[OK] login refused as expected")
        return
    if code != 0:
        raise SystemExit(f"[FAIL] login refused (code={code}); does the server have a password?")

    s.sendall(Writer().u32(0x13E5EF50).frame(Msg.SET_MOD_HASH))
    s.sendall(Writer().frame(Msg.KEEPALIVE))
    expect(s, Msg.KEEPALIVE)

    s.sendall(Writer().u64(0).u16(6500).u16(0).u16(0).u8(0).u8(0).u8(0).u8(0)
              .str8("test room").str8("act1").str8("").frame(Msg.CREATE_GAME_SERVER))
    expect(s, Msg.CREATE_GAME_RESPONSE)

    s.sendall(Writer().u8(0).u8(0).u16(0).u8(0).u8(0).u32(0).str8("").frame(Msg.GET_MOD_GAME_SERVERS))
    rooms = []
    while True:
        t, p = recv_frame(s)
        if t == Msg.GAME_SERVERS_LIST_END:
            break
        if t == Msg.MOD_GAME_SERVERS_LIST:
            rr = Reader(p)
            rr.u8(); rr.u8(); rr.u16(); rr.u16(); rr.u16(); rr.u8(); rr.u8()
            rr.u32(); rr.u64(); rr.u16(); rr.u16(); rr.u8()
            mod = rr.u32()
            rooms.append((rr.str8(), mod))
    assert ("test room", 0x13E5EF50) in rooms, rooms
    print(f"[OK] handshake, login, hosting and browsing all work ({len(rooms)} room(s) listed)")
    s.close()


if __name__ == "__main__":
    main()
