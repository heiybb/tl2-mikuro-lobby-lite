"""
Matchmaking regression over the lobby TCP: a host and two joiners.

    python lobby_server.py --port 14549 --relay-port 14549 --relay-port-range 24600-24610 --relay-ip 127.0.0.1 --env none
    python test_matchmaking.py --port 14549

Decodes the XOR-obfuscated address in AttemptConnect(22)/AttemptRelayConnect(24) and checks
that each host<->joiner pair and the joiner<->joiner mesh pair get their own port, that
retries reuse the port, that AttemptRelayConnect is not resent to an end that already said
hello, that the same-game check refuses outsiders, and the live room player count.
"""
from __future__ import annotations

import argparse
import socket
import time

import login_hash
from protocol import Msg, Reader, Writer, msg_name
from test_client import recv_frame


def login(host: str, port: int, name: str) -> tuple[socket.socket, int]:
    s = socket.create_connection((host, port), timeout=5)
    s.sendall(Writer().u64(0x1001900090005).u32(0).u32(0x101).frame(Msg.NET_CONNECT))
    t, p = recv_frame(s)
    assert t == Msg.NET_CONNECT_OK, msg_name(t)
    r = Reader(p); r.u64(); r.u32(); r.u8(); r.u32()
    relay_key = r.u32()
    s.sendall(Writer().u16(1).u64(0x1122334455).u32(125).u16(0).str8(name).str8("").frame(Msg.START_LOGIN))
    t, p = recv_frame(s)
    assert t == Msg.LOGIN_CHALLENGE, msg_name(t)
    c = Reader(p); c1, c2 = c.str8(), c.str8()
    s.sendall(Writer().blob(bytes.fromhex(login_hash.client_response("", c1, c2))).frame(Msg.LOGIN_RESPONSE))
    t, p = recv_frame(s)
    assert t == Msg.LOGIN_RESULT and Reader(p).u8() == 0, "login failed (server has a password?)"
    return s, relay_key


def collect(s: socket.socket, seconds: float = 1.0) -> dict[int, list[bytes]]:
    got: dict[int, list[bytes]] = {}
    s.settimeout(seconds)
    try:
        while True:
            t, p = recv_frame(s)
            got.setdefault(t, []).append(p)
    except (socket.timeout, TimeoutError):
        pass
    return got


def drain(s: socket.socket, want: set[int], timeout: float = 2.0) -> dict[int, list[bytes]]:
    got: dict[int, list[bytes]] = {}
    s.settimeout(timeout)
    try:
        while not want.issubset(got):
            t, p = recv_frame(s)
            got.setdefault(t, []).append(p)
    except (socket.timeout, TimeoutError):
        pass
    missing = want - set(got)
    assert not missing, f"missing {[msg_name(m) for m in missing]}, got {[msg_name(m) for m in got]}"
    return got


def decode_attempt_connect(p: bytes) -> tuple[int, int, int]:
    r = Reader(p)
    wire_ip = r.u32_be(); wire_port = r.u16_be()
    r.u32_be(); r.u16_be(); r.u8(); r.u8()
    peer_key = r.u32()
    return (wire_ip ^ peer_key) & 0xFFFFFFFF, (wire_port ^ peer_key) & 0xFFFF, peer_key


def decode_attempt_relay(p: bytes) -> tuple[int, int, int]:
    r = Reader(p)
    wire_ip = r.u32_be(); wire_port = r.u16_be()
    peer_id = r.u32()
    return (wire_ip ^ peer_id) & 0xFFFFFFFF, (wire_port ^ peer_id) & 0xFFFF, peer_id


def hello(k: int) -> bytes:
    return bytes([0x90]) + bytes(12) + k.to_bytes(4, "big")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=14549)
    ap.add_argument("--range", default="24600-24610")
    args = ap.parse_args()
    lo, hi = (int(x) for x in args.range.split("-"))

    H, hk = login(args.host, args.port, "Host")
    J1, k1 = login(args.host, args.port, "JoinerOne")
    J2, k2 = login(args.host, args.port, "JoinerTwo")

    H.sendall(Writer().u64(0).u16(6500).u16(0).u16(0).u8(0).u8(0).u8(0).u8(0)
              .str8("three").str8("act1").str8("").frame(Msg.CREATE_GAME_SERVER))
    drain(H, {Msg.CREATE_GAME_RESPONSE})

    def browse() -> Reader:
        J1.sendall(Writer().u8(0).u8(0).u16(0).u8(0).u8(0).u32(0).str8("").frame(Msg.GET_MOD_GAME_SERVERS))
        g = drain(J1, {Msg.MOD_GAME_SERVERS_LIST, Msg.GAME_SERVERS_LIST_END})
        r = Reader(g[Msg.MOD_GAME_SERVERS_LIST][0])
        r.u8(); r.u8(); r.u16(); r.u16(); r.u16(); r.u8(); r.u8(); r.u32()
        return r

    game_id = browse().u64()

    def join(sock: socket.socket, my_key: int, label: str) -> int:
        sock.sendall(Writer().u32(0).u64(game_id).frame(Msg.REQUEST_CONNECT))
        mine = drain(sock, {Msg.ATTEMPT_CONNECT, Msg.ATTEMPT_RELAY_CONNECT})
        theirs = drain(H, {Msg.ATTEMPT_CONNECT, Msg.ATTEMPT_RELAY_CONNECT})
        a = decode_attempt_connect(mine[Msg.ATTEMPT_CONNECT][-1])
        b = decode_attempt_relay(mine[Msg.ATTEMPT_RELAY_CONNECT][-1])
        c = decode_attempt_connect(theirs[Msg.ATTEMPT_CONNECT][-1])
        d = decode_attempt_relay(theirs[Msg.ATTEMPT_RELAY_CONNECT][-1])
        assert a[2] == hk and b[2] == hk, f"{label} should get the host's peerKey"
        assert c[2] == my_key and d[2] == my_key, f"host should get {label}'s peerKey"
        assert a[1] == b[1] == c[1] == d[1], f"{label}: ports differ"
        assert a[0] == b[0] == c[0] == d[0], "relay IPs differ"
        assert lo <= a[1] <= hi, f"{label} port {a[1]} outside {lo}-{hi}"
        return a[1]

    p1 = join(J1, k1, "joiner1")
    p2 = join(J2, k2, "joiner2")
    assert p1 != p2, "two joiners got the same port"
    assert join(J1, k1, "joiner1 retry") == p1, "a retry must reuse the port"
    J2.sendall(Writer().u32(hk).frame(Msg.REQUEST_RELAY_CONNECT))
    got = drain(J2, {Msg.ATTEMPT_RELAY_CONNECT})
    assert decode_attempt_relay(got[Msg.ATTEMPT_RELAY_CONNECT][-1])[1] == p2
    print(f"[1] joiner1 -> {p1}, joiner2 -> {p2}, retry reuses, relay fallback points at the pair port")

    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.sendto(hello(k1), (args.host, p1))
    time.sleep(0.3)
    J1.sendall(Writer().u32(0).u64(game_id).frame(Msg.REQUEST_CONNECT))
    mine, theirs = collect(J1, 1.5), collect(H, 1.5)
    assert Msg.ATTEMPT_CONNECT in mine
    assert Msg.ATTEMPT_RELAY_CONNECT not in mine, "joiner1 said hello, must not get AttemptRelayConnect again"
    assert Msg.ATTEMPT_RELAY_CONNECT in theirs, "host has not said hello, still gets it"
    J1.sendall(Writer().u32(hk).frame(Msg.REQUEST_RELAY_CONNECT))
    assert Msg.ATTEMPT_RELAY_CONNECT not in collect(J1, 1.0)
    u.close()
    print("[2] AttemptRelayConnect not resent to an end that already said hello")

    J2.sendall(Writer().u32(k1).u32(0).u32(0).frame(Msg.REQUEST_CONNECT))
    got = collect(J2, 2.0)
    assert Msg.ATTEMPT_CONNECT_FAILED not in got and Msg.ATTEMPT_CONNECT in got
    ip_m, port_m, pk_m = decode_attempt_connect(got[Msg.ATTEMPT_CONNECT][-1])
    theirs = collect(J1, 2.0)
    _, port_t, pk_t = decode_attempt_connect(theirs[Msg.ATTEMPT_CONNECT][-1])
    assert pk_m == k1 and pk_t == k2 and port_m == port_t and port_m not in (p1, p2)
    print(f"[3] mesh joiner2 <-> joiner1 on its own port {port_m}")

    X, _ = login(args.host, args.port, "Outsider")
    X.sendall(Writer().u32(k1).u32(0).u32(0).frame(Msg.REQUEST_CONNECT))
    got = collect(X, 2.0)
    assert Msg.ATTEMPT_CONNECT_FAILED in got and Msg.ATTEMPT_CONNECT not in got
    J2.sendall(Writer().u32(0xDEADBEEF).u32(0).u32(0).frame(Msg.REQUEST_CONNECT))
    got = collect(J2, 2.0)
    assert Msg.ATTEMPT_CONNECT_FAILED in got and Msg.ATTEMPT_CONNECT not in got
    X.close()
    print("[4] outsiders and unknown keys are refused")

    def players() -> int:
        r = browse(); r.u64(); r.u16()
        return r.u16()

    assert players() == 2, "joiner2 has no relay traffic yet and must not be counted"
    u2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u2.sendto(hello(k2), (args.host, p2))
    time.sleep(0.4)
    assert players() == 3
    u2.close()
    print("[5] live room player count")

    for s in (H, J1, J2):
        s.close()
    print("\n[OK] matchmaking checks passed")


if __name__ == "__main__":
    main()
