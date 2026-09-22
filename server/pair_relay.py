"""
Pair-port UDP relay: every connected pair of players gets its own UDP port.

Why: matchmaking uses a "transparent proxy" trick. AttemptConnect tells each client
that its peer lives at relay_ip:P, so the client builds a normal peer connection to
the relay, and many of those packets carry no relay key. With a single relay port a
host would see all of its joiners at the same relay:4549 sockaddr; neither the relay
nor the host's connection table can tell them apart, and the moment a third player
joins everyone drops. With one port per pair, every remote peer has a distinct
sockaddr and the relay never has to guess.

TL2 online play is a full mesh, not a star: a joiner must connect to every player
already in the game, not just the host. So an N-player game needs N*(N-1)/2 pairs
(3 players = 3 ports, 4 = 6, 6 = 15). Size RELAY_PORT_RANGE accordingly.

How a session tells its two ends apart (in priority order):
  1. an address it has already learned;
  2. hello (flags == 0x90, key@13 = the sender's own key);
  3. a keyed data packet (key@13 = the target key): target is host -> sender is joiner;
  4. a keyless packet: compare the source IP with each side's lobby TCP IP. If both
     sides share one public IP (same LAN), the first keyless packet is taken as the
     joiner (the joiner initiates). If that guess is wrong, the next hello from either
     side proves which side it is and the session swaps both addresses.

Lifetime: either side's lobby TCP drops -> UdpRelay's grace-period cleanup (kept while
still active); both sides learned and 10 min without packets -> released; never fully
handshaken after 3 min -> released. Released ports go back to the pool.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

log = logging.getLogger("lobby")

RELAY_FLAG = 0x10
HELLO_FLAGS = 0x90
KEY_OFF = 13

# Set from lobby_server at startup (LOG_IPS). False = mask the last two octets in logs.
LOG_IPS = False


def show_ip(ip: str) -> str:
    if LOG_IPS or not ip:
        return ip
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.x.x"
    return "(ipv6)"


def show_addr(addr) -> str:
    if not addr:
        return "-"
    return f"{show_ip(addr[0])}:{addr[1]}"


def parse_range(spec: str | None) -> list[int]:
    """'4550-4649' -> [4550 .. 4649]; '4550' -> [4550]; ''/'0'/None -> [] (pair mode off)."""
    if not spec:
        return []
    spec = spec.strip()
    if spec in ("", "0", "off", "none"):
        return []
    if "-" in spec:
        a, b = spec.split("-", 1)
        lo, hi = int(a), int(b)
        if lo > hi:
            lo, hi = hi, lo
        if hi - lo > 2000:
            raise ValueError(f"port range too large ({hi - lo + 1}), max 2000")
        return list(range(lo, hi + 1))
    return [int(spec)]


class PairSession:
    # How long a hello keeps counting as "that side's relayer is attached". Used to avoid
    # resending AttemptRelayConnect (which is not idempotent on the client).
    HELLO_TTL = 60.0

    __slots__ = ("port", "host_key", "joiner_key", "host_ip", "joiner_ip", "host_name", "joiner_name",
                 "addr_host", "addr_joiner", "created", "last_activity", "requested", "_warned_nodst",
                 "hello_host", "hello_joiner", "swaps")

    def __init__(self, port: int, host_key: int, host_ip: str, joiner_key: int, joiner_ip: str,
                 host_name: str = "", joiner_name: str = ""):
        self.port = port
        self.host_key, self.joiner_key = host_key, joiner_key
        self.host_ip, self.joiner_ip = host_ip, joiner_ip
        self.host_name, self.joiner_name = host_name, joiner_name
        self.addr_host: Optional[tuple] = None
        self.addr_joiner: Optional[tuple] = None
        self.created = time.monotonic()
        self.last_activity = self.created
        self.requested = 1
        self._warned_nodst = False
        self.hello_host = 0.0
        self.hello_joiner = 0.0
        self.swaps = 0

    @property
    def active(self) -> bool:
        return self.addr_host is not None and self.addr_joiner is not None

    def has_key(self, key: int) -> bool:
        return key == self.host_key or key == self.joiner_key

    def side_of(self, addr: tuple) -> Optional[str]:
        if addr == self.addr_host:
            return "host"
        if addr == self.addr_joiner:
            return "joiner"
        return None

    def key_of(self, side: str) -> int:
        return self.host_key if side == "host" else self.joiner_key

    def other_key(self, side: str) -> int:
        return self.joiner_key if side == "host" else self.host_key

    def other_addr(self, side: str) -> Optional[tuple]:
        return self.addr_joiner if side == "host" else self.addr_host

    def learned_keys(self) -> list[int]:
        out = []
        if self.addr_host is not None:
            out.append(self.host_key)
        if self.addr_joiner is not None:
            out.append(self.joiner_key)
        return out

    def describe(self) -> str:
        return (f"port={self.port} {self.host_name!r}(key={self.host_key:#010x}) "
                f"<-> {self.joiner_name!r}(key={self.joiner_key:#010x})")

    def classify(self, addr: tuple, flags: int, has_key: bool, key: int) -> Optional[str]:
        """First packet from an unknown address: host side or joiner side? None = unknown (drop)."""
        if has_key and flags == HELLO_FLAGS:            # hello: key = sender
            return self.side_of_key(key)
        if has_key:                                     # keyed data: key = target
            if key == self.host_key:
                return "joiner"
            if key == self.joiner_key:
                return "host"
            return None
        ip = addr[0]
        hm, jm = ip == self.host_ip, ip == self.joiner_ip
        if hm and not jm:
            return "host"
        if jm and not hm:
            return "joiner"
        if hm and jm:                                   # same public IP: joiner speaks first
            if self.addr_joiner is None:
                return "joiner"
            if self.addr_host is None:
                return "host"
            return None
        return None                                     # matches neither lobby IP (CGNAT etc.): wait for hello/keyed

    def learn(self, side: str, addr: tuple) -> Optional[tuple]:
        """Remember/update one side's address. Returns the replaced address (None = first time)."""
        if side == "host":
            old, self.addr_host = self.addr_host, addr
        else:
            old, self.addr_joiner = self.addr_joiner, addr
        return old

    def side_of_key(self, key: int) -> Optional[str]:
        if key == self.host_key:
            return "host"
        if key == self.joiner_key:
            return "joiner"
        return None

    def swap_sides(self) -> None:
        """Swap both addresses. The same-IP guess in classify() can only be wrong in pairs
        (the second address necessarily went to the other side), so swapping restores it."""
        self.addr_host, self.addr_joiner = self.addr_joiner, self.addr_host
        self.hello_host, self.hello_joiner = self.hello_joiner, self.hello_host
        self.swaps += 1

    def mark_hello(self, side: str) -> None:
        if side == "host":
            self.hello_host = time.monotonic()
        else:
            self.hello_joiner = time.monotonic()

    def hello_seen(self, side: str) -> bool:
        ts = self.hello_host if side == "host" else self.hello_joiner
        return bool(ts) and (time.monotonic() - ts) < self.HELLO_TTL


class PairEndpoint(asyncio.DatagramProtocol):
    def __init__(self, pool: "PairPool", port: int):
        self.pool = pool
        self.port = port
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        self.pool.on_datagram(self, data, addr)

    def error_received(self, exc):
        log.debug("pair port %d socket error: %s", self.port, exc)


class PairPool:
    IDLE_RELEASE = 600.0          # both sides learned, this long without packets -> release
    HANDSHAKE_TIMEOUT = 180.0     # never fully handshaken, this long after creation -> release

    def __init__(self, relay):
        # relay = the UdpRelay; only its _touch() (activity tracking) is used here
        self.relay = relay
        self.endpoints: dict[int, PairEndpoint] = {}
        self.free: list[int] = []
        self.sessions: dict[int, PairSession] = {}
        self.by_pair: dict[tuple[int, int], PairSession] = {}

    async def bind(self, host: str, ports: list[int]) -> int:
        loop = asyncio.get_running_loop()
        for p in ports:
            try:
                _, proto = await loop.create_datagram_endpoint(lambda p=p: PairEndpoint(self, p),
                                                               local_addr=(host, p))
            except OSError as e:
                log.warning("pair port %d failed to bind, skipped: %s", p, e)
                continue
            self.endpoints[p] = proto
            self.free.append(p)
        self.free.sort()
        return len(self.free)

    @property
    def capacity(self) -> int:
        return len(self.endpoints)

    @property
    def in_use(self) -> int:
        return len(self.sessions)

    def open(self, host_key: int, host_ip: str, joiner_key: int, joiner_ip: str,
             host_name: str = "", joiner_name: str = "") -> Optional[PairSession]:
        """Called on matchmaking. A repeated request for the same pair (client retry)
        reuses the same port so both ends get a consistent address."""
        s = self.by_pair.get((host_key, joiner_key))
        if s is not None:
            s.requested += 1
            log.info("pair port reused (request #%d): %s", s.requested, s.describe())
            return s
        if not self.free:
            log.warning("pair port pool exhausted (%d in use), refusing match %#010x <-> %#010x",
                        len(self.endpoints), host_key, joiner_key)
            return None
        port = self.free.pop(0)
        s = PairSession(port, host_key, host_ip, joiner_key, joiner_ip, host_name, joiner_name)
        self.sessions[port] = s
        self.by_pair[(host_key, joiner_key)] = s
        log.info("pair port assigned: %s (%d/%d in use)", s.describe(), len(self.sessions), len(self.endpoints))
        return s

    def find(self, key_a: int, key_b: int) -> Optional[PairSession]:
        return self.by_pair.get((key_a, key_b)) or self.by_pair.get((key_b, key_a))

    def sessions_of(self, key: int) -> list[PairSession]:
        return [s for s in self.sessions.values() if s.has_key(key)]

    def release(self, s: PairSession, reason: str) -> None:
        if self.sessions.pop(s.port, None) is None:
            return
        self.by_pair.pop((s.host_key, s.joiner_key), None)
        # FIFO reuse: a freshly released port waits behind every other free port, so the
        # previous pair's last retransmits land on a port with no session and get dropped.
        self.free.append(s.port)
        log.info("pair port released (%s): %s, lived %.0fs", reason, s.describe(),
                 time.monotonic() - s.created)

    def sweep(self, now: float | None = None) -> None:
        now = now or time.monotonic()
        for s in list(self.sessions.values()):
            idle = now - s.last_activity
            if s.active:
                if idle > self.IDLE_RELEASE:
                    self.release(s, f"idle {idle:.0f}s")
            elif now - s.created > self.HANDSHAKE_TIMEOUT and idle > self.HANDSHAKE_TIMEOUT:
                self.release(s, "handshake timeout")

    def active_keys(self) -> set[int]:
        out: set[int] = set()
        for s in self.sessions.values():
            out.update(s.learned_keys())
        return out

    def active_sessions(self) -> int:
        return sum(1 for s in self.sessions.values() if s.active)

    # -- data path ------------------------------------------------------------
    def on_datagram(self, ep: PairEndpoint, data: bytes, addr: tuple) -> None:
        s = self.sessions.get(ep.port)
        if s is None:
            return
        n = len(data)
        flags = data[0] if data else 0
        has_key = bool(flags & RELAY_FLAG) and n >= KEY_OFF + 4
        key = int.from_bytes(data[KEY_OFF:KEY_OFF + 4], "big") if has_key else 0

        side = s.side_of(addr)
        if side is None:
            side = s.classify(addr, flags, has_key, key)
            if side is None:
                log.debug("pair port %d: cannot tell which side %s is (flags=%#04x key=%#010x), dropped",
                          ep.port, show_addr(addr), flags, key)
                return
            old = s.learn(side, addr)
            if old is None:
                log.info("pair port %d: %s = %s%s", ep.port, side, show_addr(addr),
                         " (both sides ready)" if s.active else "")
            else:
                log.info("pair port %d: %s rebound %s -> %s (NAT remap/reconnect)",
                         ep.port, side, show_addr(old), show_addr(addr))
        elif has_key and flags == HELLO_FLAGS and key != s.key_of(side):
            # A hello carries the sender's own key, so it is proof of identity.
            true_side = s.side_of_key(key)
            if true_side is None:
                # not a key of this session: another player behind the same NAT reused the port
                log.info("pair port %d: hello from %s with foreign key %#010x, ignored",
                         ep.port, show_addr(addr), key)
                return
            # the key is the OTHER side's: the same-IP guess put both sides the wrong way round
            s.swap_sides()
            side = true_side
            log.info("pair port %d: hello proved key=%#010x, sides were swapped -> corrected (#%d)",
                     ep.port, key, s.swaps)

        sender_key = s.key_of(side)
        self.relay._touch(sender_key)
        s.last_activity = time.monotonic()

        if has_key and flags == HELLO_FLAGS:
            # hello registers the relayer with us; it is not forwarded
            s.mark_hello(side)
            return
        dst = s.other_addr(side)
        if dst is None:
            if not s._warned_nodst:
                s._warned_nodst = True
                log.info("pair port %d: %s is sending but the other side has not shown up yet, dropping",
                         ep.port, side)
            return
        out = data
        if has_key:
            # the receiver expects key@13 = its peer's (the sender's) key; the client wrote the target key
            out = data[:KEY_OFF] + sender_key.to_bytes(4, "big") + data[KEY_OFF + 4:]
        ep.transport.sendto(out, dst)
