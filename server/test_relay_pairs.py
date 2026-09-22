"""
Pair-port relay regression test (no game needed):

    python test_relay_pairs.py

Three local UDP sockets play host / joiner1 / joiner2 and check that:
  - two joiners of one host get different ports, and each port only reaches its joiner
  - key@13 is rewritten to the sender's key
  - with all ends on one IP, sides are told apart by hello / keyed target / first-speaker
  - a repeated match reuses the port; an exhausted pool refuses; released ports are reused FIFO
  - a wrong same-IP guess is corrected by the next hello
  - timeouts and the lobby-disconnect grace period release the right sessions
"""
from __future__ import annotations

import asyncio
import logging
import socket
import sys

from lobby_server import UdpRelay
from pair_relay import PairPool, PairSession, parse_range

LOCAL = "127.0.0.1"
MAIN_PORT = 24549
RANGE = "24550-24552"


def hello(key: int) -> bytes:
    return bytes([0x90]) + b"\0" * 12 + key.to_bytes(4, "big")


def keyed(target_key: int, payload: bytes, seq: int = 1) -> bytes:
    return bytes([0x12]) + seq.to_bytes(4, "big") + b"\0" * 8 + target_key.to_bytes(4, "big") + payload


def plain(payload: bytes, seq: int = 1) -> bytes:
    return bytes([0x02]) + seq.to_bytes(4, "big") + b"\0" * 8 + payload


class Sock:
    def __init__(self):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind((LOCAL, 0))
        self.s.setblocking(False)
        self.addr = self.s.getsockname()

    def send(self, port: int, data: bytes) -> None:
        self.s.sendto(data, (LOCAL, port))

    async def recv(self, timeout: float = 0.5):
        try:
            return await asyncio.wait_for(asyncio.get_running_loop().sock_recvfrom(self.s, 2048), timeout)
        except asyncio.TimeoutError:
            return None

    async def expect(self, payload: bytes, from_port: int, key: int | None = None):
        r = await self.recv()
        assert r is not None, f"{self.addr} did not receive {payload!r} (from port {from_port})"
        data, src = r
        assert src[1] == from_port, f"packet came from port {src[1]}, expected {from_port}"
        assert data.endswith(payload), f"payload mismatch: {data!r}"
        if key is not None:
            got = int.from_bytes(data[13:17], "big")
            assert got == key, f"key@13={got:#x}, expected {key:#x}"

    async def expect_nothing(self):
        r = await self.recv(0.3)
        assert r is None, f"{self.addr} should receive nothing, got {r}"

    def close(self):
        self.s.close()


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    loop = asyncio.get_running_loop()
    _, relay = await loop.create_datagram_endpoint(UdpRelay, local_addr=(LOCAL, MAIN_PORT))
    relay.pairs = PairPool(relay)
    assert await relay.pairs.bind(LOCAL, parse_range(RANGE)) == 3

    H, J1, J2 = Sock(), Sock(), Sock()
    KH, K1, K2 = 0xA0000001, 0xB0000001, 0xB0000002
    s1 = relay.pairs.open(KH, LOCAL, K1, LOCAL, "host", "j1")
    s2 = relay.pairs.open(KH, LOCAL, K2, LOCAL, "host", "j2")
    assert s1 and s2 and s1.port != s2.port
    assert relay.pairs.open(KH, LOCAL, K1, LOCAL) is s1, "a repeated match must reuse the session"
    P1, P2 = s1.port, s2.port
    print(f"[1] host<->j1 on {P1}, host<->j2 on {P2}")

    J1.send(P1, hello(K1)); H.send(P1, hello(KH))
    H.send(P2, hello(KH)); J2.send(P2, hello(K2))
    await asyncio.sleep(0.1)
    assert s1.active and s2.active
    assert s1.addr_host == H.addr and s1.addr_joiner == J1.addr
    assert s2.addr_host == H.addr and s2.addr_joiner == J2.addr
    await H.expect_nothing()
    print("[2] hello identifies sides and is not forwarded")

    J1.send(P1, keyed(KH, b"j1->h"))
    await H.expect(b"j1->h", P1, key=K1)
    H.send(P1, keyed(K1, b"h->j1")); H.send(P2, keyed(K2, b"h->j2"))
    await J1.expect(b"h->j1", P1, key=KH)
    await J2.expect(b"h->j2", P2, key=KH)
    await J1.expect_nothing(); await J2.expect_nothing()
    print("[3] keyed packets split by port, key@13 rewritten to the sender")

    H.send(P1, plain(b"nk1")); H.send(P2, plain(b"nk2")); J2.send(P2, plain(b"nk3"))
    await J1.expect(b"nk1", P1)
    await J2.expect(b"nk2", P2)
    await H.expect(b"nk3", P2)
    await J1.expect_nothing()
    print("[4] keyless packets split by port (the reason single-port relays drop at 3 players)")

    s3 = relay.pairs.open(0xC0000001, LOCAL, 0xD0000001, LOCAL, "h3", "j3")
    HC, JD = Sock(), Sock()
    JD.send(s3.port, plain(b"first"))
    await asyncio.sleep(0.05)
    assert s3.addr_joiner == JD.addr and s3.addr_host is None
    HC.send(s3.port, plain(b"h-first"))
    await JD.expect(b"h-first", s3.port)
    JD.send(s3.port, plain(b"j-again"))
    await HC.expect(b"j-again", s3.port)
    print("[5] same IP, no hello: first speaker is the joiner, forwarding works both ways")

    assert relay.pairs.open(0xE0000001, LOCAL, 0xF0000001, LOCAL) is None
    print("[6] exhausted pool refuses")

    relay.pairs.release(s1, "test")
    assert P1 in relay.pairs.free and relay.pairs.in_use == 2
    H.send(P1, plain(b"dead"))
    await J1.expect_nothing()
    s4 = relay.pairs.open(0xE0000001, LOCAL, 0xF0000001, LOCAL)
    assert s4.port == P1
    relay.pairs.release(s2, "test"); relay.pairs.release(s3, "test")
    got = relay.pairs.open(0xE0000002, LOCAL, 0xF0000002, LOCAL)
    assert got.port == P2, f"the earliest released port {P2} should come first, got {got.port}"
    relay.pairs.release(got, "test")
    print("[7] released ports return to the pool, reused first-in first-out")

    relay.pairs.HANDSHAKE_TIMEOUT = 0.2
    relay.pairs.IDLE_RELEASE = 0.4
    never = relay.pairs.open(0xE0000003, LOCAL, 0xF0000003, LOCAL)
    HX, JX = Sock(), Sock()
    JX.send(s4.port, hello(0xF0000001)); HX.send(s4.port, hello(0xE0000001))
    await asyncio.sleep(0.25)
    assert s4.active
    relay.pairs.sweep()
    assert never.port in relay.pairs.free and s4.port not in relay.pairs.free
    await asyncio.sleep(0.45)
    relay.pairs.sweep()
    assert s4.port in relay.pairs.free
    print("[8] handshake timeout / idle timeout release")

    relay.CLEANUP_GRACE = 0.3
    sa = relay.pairs.open(0xE0000004, LOCAL, 0xF0000004, LOCAL)
    sb = relay.pairs.open(0xE0000004, LOCAL, 0xF0000005, LOCAL)
    HA, JA, JB = Sock(), Sock(), Sock()
    JA.send(sa.port, hello(0xF0000004)); HA.send(sa.port, hello(0xE0000004))
    JB.send(sb.port, hello(0xF0000005)); HA.send(sb.port, hello(0xE0000004))
    await asyncio.sleep(0.1)
    relay.schedule_cleanup(0xE0000004)
    for _ in range(6):
        await asyncio.sleep(0.1)
        JA.send(sa.port, plain(b"alive"))
    await asyncio.sleep(0.1)
    assert sa.port not in relay.pairs.free, "an active session must survive the grace cleanup"
    assert sb.port in relay.pairs.free, "an idle session must be released after the grace period"
    print("[9] lobby-disconnect grace cleanup: active kept, idle released")

    # wrong same-IP guess (host speaks first) -> corrected by the host's hello
    KH5, KJ5 = 0xE0000005, 0xF0000006
    s5 = relay.pairs.open(KH5, LOCAL, KJ5, LOCAL, "h5", "j5")
    H5, J5 = Sock(), Sock()
    H5.send(s5.port, plain(b"host-first"))
    await asyncio.sleep(0.05)
    assert s5.addr_joiner == H5.addr
    J5.send(s5.port, plain(b"joiner-second"))
    await asyncio.sleep(0.05)
    assert s5.addr_host == J5.addr
    await H5.expect(b"joiner-second", s5.port)
    H5.send(s5.port, hello(KH5))
    await asyncio.sleep(0.05)
    assert s5.swaps == 1 and s5.addr_host == H5.addr and s5.addr_joiner == J5.addr
    H5.send(s5.port, keyed(KJ5, b"h5->j5"))
    await J5.expect(b"h5->j5", s5.port, key=KH5)
    print("[10] wrong same-IP guess corrected by hello")

    assert s5.hello_seen("host") and not s5.hello_seen("joiner")
    old_ttl, PairSession.HELLO_TTL = PairSession.HELLO_TTL, 0.15
    try:
        await asyncio.sleep(0.2)
        assert not s5.hello_seen("host")
    finally:
        PairSession.HELLO_TTL = old_ttl
    before = (s5.addr_host, s5.addr_joiner, s5.swaps)
    H5.send(s5.port, hello(0x1234ABCD))
    await asyncio.sleep(0.05)
    assert (s5.addr_host, s5.addr_joiner, s5.swaps) == before, "a foreign-key hello must not rebind"
    print("[11] hello marker + TTL; foreign-key hello ignored")

    for s in (H, J1, J2, HC, JD, HX, JX, HA, JA, JB, H5, J5):
        s.close()
    print("\n[OK] pair-port relay checks passed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as e:
        print(f"[FAIL] {e}")
        sys.exit(1)
