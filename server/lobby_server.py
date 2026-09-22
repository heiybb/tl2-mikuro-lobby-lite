"""
TL2 Mikuro Lobby Lite: a self-hosted Torchlight II online lobby + UDP relay.

Point the game's LOBBYHOST (LOBBYHOST_XD on 1.26.x) at this machine and players can
log in, host, browse and join games through it. The game exe is not modified.

What it does:
  - handshake + login (classic StartLogin, and the 1.26.0.1 TapTap StartTapLogin)
  - optional shared server password (LOBBY_PASSWORD), checked with the client's own
    challenge-response chain (login_hash.py); otherwise any name is let in
  - game hosting and the server browser (incl. the mod-hash list entries)
  - matchmaking, with a UDP relay that gives every pair of players its own port
    (pair_relay.py), so 3+ player games work behind any NAT

What it deliberately does NOT do: no accounts, no statistics, no telemetry, no
outbound connections (except the optional public-IP lookup, see RELAY_IP), nothing
written to disk. Logs go to stdout only and mask IP addresses unless LOG_IPS=1.

Usage:
    python lobby_server.py                      # reads .env next to this file if present
    python lobby_server.py --relay-ip 203.0.113.10
    python lobby_server.py --env /etc/tl2-lobby.env -v
Command-line flags win over environment variables, which win over the .env file.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import logging
import os
import socket
import struct
import time
from pathlib import Path

import login_hash
import pair_relay
from pair_relay import PairPool, parse_range, show_addr, show_ip
from protocol import Msg, Reader, Writer, msg_name, parse_header, hexdump, HEADER_LEN

log = logging.getLogger("lobby")

RELAY_FLAG = 0x10
HELLO_FLAGS = 0x90        # exactly 0x90 (control 0x80 | relay 0x10); 0x94 etc. are not hello

MAX_NAME = 32             # display names longer than this are cut


# ---------------------------------------------------------------------------
# shared state
# ---------------------------------------------------------------------------
class GameServer:
    """A game hosted by a player (fields from CreateGameServer)."""
    __slots__ = ("server_id", "owner", "name", "desc", "host_port", "level_min", "level_max",
                 "diff_flags", "reroll", "max_players", "game_rank", "mod_hash")

    def __init__(self, server_id: int, owner: "Client"):
        self.server_id = server_id          # u64 game id (the client's GUID)
        self.owner = owner
        self.name = ""
        self.desc = ""
        self.host_port = 0
        self.level_min = 1
        self.level_max = 0xFFFF
        self.diff_flags = 0                 # bits 0-3 difficulty, 0x10 elite, 0x20 password/friends
        self.reroll = 0
        self.max_players = 6
        self.game_rank = 0
        # The host's mod hash (from SetModHash). The mod list entry (msg 53) carries it and
        # a joiner compares it with its own; only a match shows the "Join" button.
        self.mod_hash = owner.mod_hash if owner else 0


class Hub:
    # A member only counts toward a room's player count if it had relay traffic this
    # recently. TL2 sends 15-40 packets/s in game, so 30s is a wide margin; too long
    # would keep people who left and could make a room look full.
    PLAYER_ACTIVE_WINDOW = 30.0

    def __init__(self, password: str = "", allow_taptap: bool = True):
        self.clients: dict[int, "Client"] = {}
        self.servers: dict[int, GameServer] = {}
        self._uid = itertools.count(1)
        self._sid = itertools.count(1)
        self.relay_ip = 0x7F000001          # host-order int
        self.relay_port = 4549
        self.relay: UdpRelay | None = None
        self.password = password
        self.allow_taptap = allow_taptap

    def new_user_id(self) -> int:
        return next(self._uid)

    def new_server_id(self) -> int:
        return next(self._sid)

    def new_relay_key(self) -> int:
        """A random, non-enumerable, unused non-zero relay key. Must not be derived from
        anything guessable, or anyone could send a hello with it and hijack the mapping."""
        used = {c.relay_key for c in self.clients.values()}
        while True:
            k = int.from_bytes(os.urandom(4), "big")
            if k and k not in used:
                return k

    def client_by_relay_key(self, key: int) -> "Client | None":
        if not key:
            return None
        for c in self.clients.values():
            if c.relay_key == key:
                return c
        return None

    def player_count(self, gs: GameServer) -> int:
        """Players in a room, host included: members by Client.game_id, filtered by
        recent relay activity (game_id alone would keep players who went back to the lobby)."""
        n = 1
        relay = self.relay
        for c in self.clients.values():
            if c is gs.owner or c.game_id != gs.server_id:
                continue
            if relay is None or relay.active_since(c.relay_key, self.PLAYER_ACTIVE_WINDOW):
                n += 1
        return min(n, gs.max_players) if gs.max_players else n

    def add_server(self, gs: GameServer):
        self.servers[gs.server_id] = gs
        gs.owner.game_id = gs.server_id     # the host is a member too (mesh matchmaking checks it)
        log.info("game registered id=%d name=%r host=%r (%d total)",
                 gs.server_id, gs.name, gs.owner.username, len(self.servers))

    def remove_server(self, server_id: int):
        gs = self.servers.pop(server_id, None)
        if gs:
            for c in self.clients.values():
                if c.game_id == server_id:
                    c.game_id = 0
            log.info("game removed id=%d name=%r", server_id, gs.name)

    def remove_owner_servers(self, owner: "Client"):
        for sid in [s for s, gs in self.servers.items() if gs.owner is owner]:
            self.remove_server(sid)


# ---------------------------------------------------------------------------
# one lobby connection
# ---------------------------------------------------------------------------
class Client:
    # Allowed before login: handshake, the login itself, keepalive, and the two harmless
    # client-state reports (a mod hash sent early must not be lost, or modded games lose
    # their "Join" button). Hosting/browsing/matchmaking are dropped until login succeeds.
    _PRE_LOGIN_OK = frozenset({Msg.NET_CONNECT, Msg.NET_DISCONNECT, Msg.SRV_CONNECT_CALLBACK,
                               Msg.START_LOGIN, Msg.START_TAP_LOGIN, Msg.LOGIN_RESPONSE,
                               Msg.KEEPALIVE, Msg.SET_LANGUAGE, Msg.SET_MOD_HASH})

    def __init__(self, hub: Hub, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.hub = hub
        self.reader = reader
        self.writer = writer
        self.peer = writer.get_extra_info("peername")
        self.user_id = hub.new_user_id()
        self.session_key = (self.user_id * 2654435761) & 0xFFFFFFFF
        self.username = ""
        self.logged_in = False
        self.login_fails = 0                # consecutive failures on this connection (>=5 -> disconnect)
        self.pending_chal1 = ""
        self.pending_chal2 = ""
        self.login_mode = "classic"         # "classic" = StartLogin(8), "taptap" = StartTapLogin(54)
        self.mod_hash = 0
        # Game this client is in (0 = none). Set for the host in add_server and for a joiner
        # when matchmaking succeeds. Only used to check that mesh peers are in the same game.
        self.game_id = 0
        # Relay key: handed out in NetConnectOk @36, the client routes relay traffic with it.
        self.relay_key = hub.new_relay_key()
        hub.clients[self.user_id] = self

    @property
    def ip(self) -> str:
        return self.peer[0] if self.peer else ""

    async def send(self, frame: bytes, what: str = ""):
        self.writer.write(frame)
        await self.writer.drain()
        if log.isEnabledFor(logging.DEBUG):
            log.debug("-> %-22s len=%d %s", what or "?", len(frame) - HEADER_LEN, hexdump(frame))

    async def run(self):
        log.info("connect %s user_id=%d", show_addr(self.peer), self.user_id)
        try:
            while True:
                hdr = await self.reader.readexactly(HEADER_LEN)
                msg_type, plen = parse_header(hdr)
                payload = await self.reader.readexactly(plen) if plen else b""
                if log.isEnabledFor(logging.DEBUG):
                    log.debug("<- %-22s len=%d %s", msg_name(msg_type), plen, hexdump(payload))
                await self.dispatch(msg_type, payload)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception:
            log.exception("error while handling %s", show_addr(self.peer))
        finally:
            await self.close()

    async def close(self):
        self.logged_in = False
        self.hub.clients.pop(self.user_id, None)
        self.hub.remove_owner_servers(self)
        # Relay cleanup waits out a grace period: a dropped lobby TCP connection does not
        # mean the game ended (NAT idle timeouts happen while the relay is still in use).
        if self.hub.relay:
            self.hub.relay.schedule_cleanup(self.relay_key)
        try:
            self.writer.close()
        except Exception:
            pass
        log.info("disconnect %s user_id=%d", show_addr(self.peer), self.user_id)

    async def dispatch(self, t: int, p: bytes):
        if not self.logged_in and t not in self._PRE_LOGIN_OK:
            log.debug("not logged in, dropping %s(%d)", msg_name(t), t)
            return
        h = HANDLERS.get(t)
        if h:
            await h(self, Reader(p), p)
        else:
            log.debug("unhandled %s(%d) len=%d %s", msg_name(t), t, len(p), hexdump(p))

    # ===================== handlers =====================

    async def on_net_connect(self, r: Reader, raw: bytes):
        # NetConnect: [u64 peer][u32 key][u32 protocol]
        try:
            peer = r.u64(); key = r.u32(); proto = r.u32()
        except EOFError:
            peer = key = proto = 0
        log.debug("NetConnect peer=%#x key=%#x protocol=%#x -> relay_key=%#x",
                  peer, key, proto, self.relay_key)
        # NetConnectOk: [u64 peer][u32 protocol echo][u8 0][u32 key echo][u32 relay key]
        w = Writer().u64(peer).u32(proto).u8(0).u32(key).u32(self.relay_key)
        await self.send(w.frame(Msg.NET_CONNECT_OK), "NetConnectOk")

    async def on_ignore(self, r: Reader, raw: bytes):
        pass

    async def _send_challenge(self):
        # c1 is a fresh salt, c2 a one-time challenge. The client hashes its password
        # with them (login_hash.py); the password itself is never sent.
        self.pending_chal1 = login_hash.new_salt()
        self.pending_chal2 = login_hash.new_challenge()
        await self.send(Writer().str8(self.pending_chal1).str8(self.pending_chal2)
                        .frame(Msg.LOGIN_CHALLENGE), "LoginChallenge")

    async def on_start_login(self, r: Reader, raw: bytes):
        # [u16][u64 guid][u32 ver][u16][str8 user][str8 token]
        try:
            r.u16(); r.u64(); ver = r.u32(); r.u16()
            user = r.str8(); r.str8()
        except EOFError:
            user, ver = "", 0
        self.login_mode = "classic"
        self.username = clean_name(user)
        log.info("login request user=%r ver=%d", self.username, ver)
        await self._send_challenge()

    async def on_start_tap_login(self, r: Reader, raw: bytes):
        # StartTapLogin(54), 1.26.0.1: [u16][u64 guid][u32 ver][u16][str16 credential][str8 token]
        # credential = kid + "," + mac_key, both issued by the auth endpoint the launcher
        # points the game at (tap-auth/). kid is used as the display name.
        try:
            r.u16(); r.u64(); ver = r.u32(); r.u16()
            credential = r.str16(); r.str8()
        except EOFError:
            credential, ver = "", 0
        self.login_mode = "taptap"
        kid = credential.partition(",")[0]
        self.username = clean_name(kid)
        log.info("login request (TapTap) user=%r ver=%d", self.username, ver)
        await self._send_challenge()

    async def on_login_response(self, r: Reader, raw: bytes):
        resp_hex = login_hash.extract_response_hex(raw)
        if self.login_mode == "taptap":
            # 1.26 clears the password field in TapTap mode, so it cannot carry a server
            # password. Allowed or refused as a whole by ALLOW_TAPTAP.
            ok = self.hub.allow_taptap
            reason = "taptap_disabled"
            if log.isEnabledFor(logging.DEBUG):
                empty = login_hash.verify(login_hash.derive_verifier("", self.pending_chal1),
                                          self.pending_chal2, resp_hex)
                log.debug("TapTap LoginResponse: empty-password chain %s", "matches" if empty else "differs")
        elif self.hub.password:
            ok = login_hash.verify(login_hash.derive_verifier(self.hub.password, self.pending_chal1),
                                   self.pending_chal2, resp_hex)
            reason = "bad_password"
        else:
            ok, reason = True, ""

        if not ok:
            self.login_fails += 1
            log.info("login refused user=%r ip=%s reason=%s", self.username, show_ip(self.ip), reason)
            # LoginResult code != 0 = failure (client shows invalid name/password); connection stays open
            await self.send(Writer().u8(1).u32(0).u16(0).u8(0).frame(Msg.LOGIN_RESULT), "LoginResult(refused)")
            if self.login_fails >= 5:
                log.info("%d failed logins in a row, closing %s", self.login_fails, show_addr(self.peer))
                self.writer.close()
            return

        self.logged_in = True
        log.info("login ok user=%r ip=%s", self.username, show_ip(self.ip))
        # LoginResult: u8 code (0 = success), u32 sessionKey, u16, u8 count = 0
        await self.send(Writer().u8(0).u32(self.session_key).u16(0).u8(0).frame(Msg.LOGIN_RESULT),
                        "LoginResult")
        # FirewallTestSuccess(35){u8}: clears the client's "firewall test pending/failed"
        # state; without it the client reports "Firewall Errors Detected".
        await asyncio.sleep(0.2)
        await self.send(Writer().u8(1).frame(Msg.FIREWALL_TEST_SUCCESS), "FirewallTestSuccess")

    async def on_keepalive(self, r: Reader, raw: bytes):
        await self.send(Writer().frame(Msg.KEEPALIVE), "Keepalive")

    async def on_set_mod_hash(self, r: Reader, raw: bytes):
        self.mod_hash = r.u32() if r.remaining() >= 4 else 0
        log.debug("SetModHash %r hash=%#x", self.username, self.mod_hash)

    # ----- hosting -----
    async def on_create_game(self, r: Reader, raw: bytes):
        # CreateGameServer(15) / CreateModGameServer(51):
        #   u64 gameId, u16 hostPort, u16 lvlMin, u16 lvlMax,
        #   u8 diffFlags, u8 reroll, u8 maxPlayers, u8 gameRank, str8 name, str8 desc, str8 blob
        gs = GameServer(self.hub.new_server_id(), self)
        try:
            client_game_id = r.u64()
            if client_game_id:
                gs.server_id = client_game_id
            gs.host_port = r.u16()
            gs.level_min = r.u16()
            gs.level_max = r.u16()
            gs.diff_flags = r.u8()
            gs.reroll = r.u8()
            gs.max_players = r.u8()
            gs.game_rank = r.u8()
            gs.name = r.str8()
            gs.desc = r.str8() if r.remaining() else ""
        except EOFError:
            log.warning("CreateGameServer truncated: %s", hexdump(raw))
        self.hub.add_server(gs)
        # CreateGameResponse: u8 code, 0 = success
        await self.send(Writer().u8(0).frame(Msg.CREATE_GAME_RESPONSE), "CreateGameResponse")

    async def on_remove_game(self, r: Reader, raw: bytes):
        sid = r.u64() if r.remaining() >= 8 else 0
        gs = self.hub.servers.get(sid)
        if gs is not None and gs.owner is self:     # only the host may remove its own game
            self.hub.remove_server(sid)

    # ----- browser / details -----
    async def on_get_servers(self, r: Reader, raw: bytes):
        # The client browses with GetModGameServers(52); the reply must be mod list entries
        # (msg 53), which carry the host's mod hash, or the "Join" button never appears.
        for gs in list(self.hub.servers.values()):
            await self.send(self._mod_server_list_entry(gs), "ModGameServersList")
        await self.send(Writer().u16(0).frame(Msg.GAME_SERVERS_LIST_END), "GameServersListEnd")

    async def on_get_details(self, r: Reader, raw: bytes):
        sid = r.u64() if r.remaining() >= 8 else 0
        gs = self.hub.servers.get(sid)
        await self.send(Writer().u64(sid).str16(gs.desc if gs else "").frame(Msg.SERVER_DETAILS),
                        "ServerDetails")

    def _mod_server_list_entry(self, gs: GameServer) -> bytes:
        # Same as GameServersList(13) plus a u32 mod hash between gameRank and the name.
        w = (Writer()
             .u8(gs.diff_flags)
             .u8(0)
             .u16(gs.level_min)
             .u16(gs.level_max)
             .u16(0)
             .u8(1)
             .u8(gs.max_players)
             .u32(gs.owner.relay_key)            # joiner sends RequestConnect to this key
             .u64(gs.server_id)
             .u16(gs.host_port)
             .u16(self.hub.player_count(gs))     # current players
             .u8(gs.game_rank)
             .u32(gs.mod_hash)
             .str8(gs.name)
             .str8(gs.desc)
             .str16(""))
        return w.frame(Msg.MOD_GAME_SERVERS_LIST)

    # ----- matchmaking -----
    async def on_request_connect(self, r: Reader, raw: bytes):
        # RequestConnect(21) is overloaded; the wire format is always u32 a2 | u32 a3 | u32 a4:
        #   1) join a game:            a2 = misc,             a3:a4 = gameId (u64)
        #   2) connect to a mesh peer: a2 = peer's relay key, a3 = a4 = 0
        # TL2 online is a full mesh: after reaching the host, a joiner must also connect to
        # every other player already in the game, and it asks the lobby to arrange that
        # with form 2. Refusing form 2 is what breaks every game at the third player.
        f0 = r.u32() if r.remaining() >= 4 else 0
        sid = r.u64() if r.remaining() >= 8 else 0
        if sid == 0:
            await self.on_peer_connect_request(f0)
            return
        gs = self.hub.servers.get(sid)
        if not gs or gs.owner is self:
            log.info("RequestConnect user=%r game=%d: %s", self.username, sid,
                     "own game" if gs else "unknown game")
            await self.send(Writer().u32(f0).u8(1).frame(Msg.ATTEMPT_CONNECT_FAILED), "AttemptConnectFailed")
            return
        self.game_id = sid
        pair = await self._open_pair(gs.owner, f0)
        if pair is not None:
            await self._deal_pair(gs.owner, *pair, f"join game {sid}")

    async def on_peer_connect_request(self, peer_key: int):
        """RequestConnect form 2: connect me to another player in my game. Both must be
        online, logged in and in the same live game (the relay key is random, and this
        same-game check is the second lock against connecting to arbitrary players)."""
        peer = self.hub.client_by_relay_key(peer_key)
        ok = (peer is not None and peer is not self and peer.logged_in
              and self.game_id and self.game_id == peer.game_id and self.game_id in self.hub.servers)
        if not ok:
            log.info("mesh request user=%r -> key=%#010x refused", self.username, peer_key)
            await self.send(Writer().u32(peer_key).u8(1).frame(Msg.ATTEMPT_CONNECT_FAILED),
                            "AttemptConnectFailed")
            return
        pair = await self._open_pair(peer, peer_key)
        if pair is not None:
            await self._deal_pair(peer, *pair, f"mesh peer in game {self.game_id}")

    async def _open_pair(self, other: "Client", f0: int):
        """Get a pair port for self <-> other. Returns (session, relay_ip, relay_port), or None
        after replying AttemptConnectFailed. "host"/"joiner" are just the two ends' names in
        pair_relay; in the mesh case `other` is not necessarily the game's host."""
        ri, rp = self.hub.relay_ip, self.hub.relay_port
        relay = self.hub.relay
        sess = None
        if relay and relay.pairs:
            sess = relay.pairs.open(other.relay_key, other.ip, self.relay_key, self.ip,
                                    other.username, self.username)
            if sess is None:
                await self.send(Writer().u32(f0).u8(1).frame(Msg.ATTEMPT_CONNECT_FAILED),
                                "AttemptConnectFailed(pool exhausted)")
                return None
            rp = sess.port
        elif relay:
            # single-port fallback (RELAY_PORT_RANGE=0): one joiner per game at most
            relay.reset_session(self.relay_key)
            relay.expect_pair(self.relay_key, other.relay_key)
        return sess, ri, rp

    async def _deal_pair(self, other: "Client", sess, ri: int, rp: int, why: str):
        """Send (1) AttemptConnect + (2) AttemptRelayConnect to both ends.

        (1) makes the client create the game peer (keyed by peerKey); (2) attaches a relayer
        to it. Without (1) the relayer is orphaned; without (2) the peer's connection is
        missing its relay link.

        (2) is not idempotent on the client: every copy creates another relayer. So it is
        only sent to an end that has not yet registered with a hello on this pair port.
        (1) goes through the real relay endpoint and is reused by the client, so it is
        always resent.
        """
        me = sess.side_of_key(self.relay_key) if sess is not None else None
        them = sess.side_of_key(other.relay_key) if sess is not None else None
        me_needs = sess is None or me is None or not sess.hello_seen(me)
        other_needs = sess is None or them is None or not sess.hello_seen(them)
        log.info("match (%s): %r <-> %r relay port %d", why, self.username, other.username, rp)
        await self.send_attempt_connect(ri, rp, peer_key=other.relay_key)
        if me_needs:
            await self.send_attempt_relay(ri, rp, peer_id=other.relay_key)
        await other.send_attempt_connect(ri, rp, peer_key=self.relay_key)
        if other_needs:
            await other.send_attempt_relay(ri, rp, peer_id=self.relay_key)

    async def on_request_relay(self, r: Reader, raw: bytes):
        # A client whose direct connection failed asks for a relay; point it back at the pair port.
        tgt = r.u32() if r.remaining() >= 4 else self.relay_key
        rp = self.hub.relay_port
        relay = self.hub.relay
        sess = relay.pairs.find(self.relay_key, tgt) if relay and relay.pairs else None
        if sess is not None:
            rp = sess.port
            side = sess.side_of_key(self.relay_key)
            if side is not None and sess.hello_seen(side):
                return      # relayer already attached; a second (2) would create a duplicate
        await self.send_attempt_relay(self.hub.relay_ip, rp, peer_id=tgt)

    async def send_attempt_relay(self, relay_ip: int, relay_port: int, peer_id: int, flag: int = 1):
        # AttemptRelayConnect(24): u32 ip, u16 port (both XOR peerId, network order), u32 peerId, u8 flag
        w = (Writer()
             .u32_be((peer_id ^ relay_ip) & 0xFFFFFFFF).u16_be((peer_id ^ relay_port) & 0xFFFF)
             .u32(peer_id)
             .u8(flag))
        await self.send(w.frame(Msg.ATTEMPT_RELAY_CONNECT), "AttemptRelayConnect")

    async def send_attempt_connect(self, relay_ip: int, relay_port: int, peer_key: int, flag: int = 1):
        # AttemptConnect(22) with the peer address set to our relay (transparent proxy):
        # the client creates a real game peer and connects to us, and we forward.
        # Address B @16/@20, address A @24/@28 (both XOR peerKey), u8, u8, u32 peerKey, u8 flag
        wire_ip = (peer_key ^ relay_ip) & 0xFFFFFFFF
        wire_port = (peer_key ^ relay_port) & 0xFFFF
        w = (Writer()
             .u32_be(wire_ip).u16_be(wire_port)
             .u32_be(wire_ip).u16_be(wire_port)
             .u8(0).u8(0)
             .u32(peer_key)
             .u8(flag))
        await self.send(w.frame(Msg.ATTEMPT_CONNECT), "AttemptConnect")

    async def on_lookup_user(self, r: Reader, raw: bytes):
        # LookupUser(32){u32 key}: the host looks up a joiner. It must be answered or the
        # host never sends the join verification and the join hangs.
        key = r.u32() if r.remaining() >= 4 else 0
        target = self.hub.client_by_relay_key(key)
        name = target.username if target else (self.username or "Player")
        # LookupUserResponse(33): u32 key, u32 data (non-zero = found), u8, str8 name
        await self.send(Writer().u32(key).u32(key or 1).u8(0).str8(name).frame(Msg.LOOKUP_USER_RESPONSE),
                        "LookupUserResponse")


def clean_name(name: str) -> str:
    name = "".join(ch for ch in name.strip() if ch.isprintable())[:MAX_NAME]
    return name or "Player"


HANDLERS = {
    Msg.NET_CONNECT:            Client.on_net_connect,
    Msg.NET_DISCONNECT:         Client.on_ignore,
    Msg.SRV_CONNECT_CALLBACK:   Client.on_ignore,
    Msg.START_LOGIN:            Client.on_start_login,
    Msg.START_TAP_LOGIN:        Client.on_start_tap_login,
    Msg.LOGIN_RESPONSE:         Client.on_login_response,
    Msg.KEEPALIVE:              Client.on_keepalive,
    Msg.SET_LANGUAGE:           Client.on_ignore,
    Msg.SET_MOD_HASH:           Client.on_set_mod_hash,
    Msg.SEND_MOD_COUNTS:        Client.on_ignore,
    Msg.CREATE_GAME_SERVER:     Client.on_create_game,
    Msg.CREATE_MOD_GAME_SERVER: Client.on_create_game,
    Msg.UPDATE_GAME_SERVER:     Client.on_ignore,
    Msg.REMOVE_GAME_SERVER:     Client.on_remove_game,
    Msg.GET_MOD_GAME_SERVERS:   Client.on_get_servers,
    Msg.GET_SERVER_DETAILS:     Client.on_get_details,
    Msg.REQUEST_CONNECT:        Client.on_request_connect,
    Msg.REQUEST_RELAY_CONNECT:  Client.on_request_relay,
    Msg.REPORT_CONNECTION:      Client.on_ignore,
    Msg.LOOKUP_USER:            Client.on_lookup_user,
    # friends / social: accepted and ignored
    Msg.REQUEST_GAME:           Client.on_ignore,
    Msg.JOINED_GAME:            Client.on_ignore,
    Msg.LEFT_GAME:              Client.on_ignore,
    Msg.ADD_FRIEND:             Client.on_ignore,
    Msg.REMOVE_FRIEND:          Client.on_ignore,
    Msg.ADD_ENEMY:              Client.on_ignore,
    Msg.REMOVE_ENEMY:           Client.on_ignore,
    Msg.ADD_FRIEND_BY_NAME:     Client.on_ignore,
    Msg.SET_CHARACTER_DATA:     Client.on_ignore,
}


# ---------------------------------------------------------------------------
# UDP relay, main port (hello registration + single-port fallback)
#   header: flags@0, seq@1, ack@5, field@9; flags & 0x10 -> relay key @13 (u32 BE)
#   hello (flags == 0x90) carries the sender's own key -> learn addr <-> key
#   keyed packets carry the target key -> look it up, rewrite key@13 to the sender's key
# The pair ports (pair_relay.py) handle almost all traffic; this port matters for
# RELAY_PORT_RANGE=0 and as the fallback target.
# ---------------------------------------------------------------------------
class UdpRelay(asyncio.DatagramProtocol):
    SWEEP_INTERVAL = 30.0
    CLEANUP_GRACE = 90.0     # after a lobby disconnect, keep a key this long while it is still active

    def __init__(self):
        self.transport = None
        self.key_to_addr: dict[int, tuple] = {}
        self.addr_to_key: dict[tuple, int] = {}
        self.peer_of: dict[tuple, tuple] = {}
        self.expected_peer: dict[int, int] = {}   # matchmaking: key <-> expected peer key
        self.last_activity: dict[int, float] = {}
        self.pairs: PairPool | None = None

    def connection_made(self, transport):
        self.transport = transport
        asyncio.get_running_loop().call_later(self.SWEEP_INTERVAL, self._sweep)

    def _sweep(self) -> None:
        if self.pairs:
            self.pairs.sweep()
        asyncio.get_running_loop().call_later(self.SWEEP_INTERVAL, self._sweep)

    def _touch(self, key: int | None):
        if key:
            self.last_activity[key] = time.monotonic()

    def active_since(self, key: int, window: float) -> bool:
        ts = self.last_activity.get(key)
        return ts is not None and (time.monotonic() - ts) < window

    def schedule_cleanup(self, key: int):
        if key:
            asyncio.get_running_loop().call_later(self.CLEANUP_GRACE, self._deferred_cleanup, key)

    def _deferred_cleanup(self, key: int):
        now = time.monotonic()
        busy = False
        if key in self.key_to_addr and now - self.last_activity.get(key, 0.0) < self.CLEANUP_GRACE:
            busy = True
        if self.pairs:
            for s in self.pairs.sessions_of(key):
                if now - s.last_activity < self.CLEANUP_GRACE:
                    busy = True
                else:
                    self.pairs.release(s, "lobby disconnected + idle")
        if busy:
            asyncio.get_running_loop().call_later(self.CLEANUP_GRACE, self._deferred_cleanup, key)
            return
        self.reset_session(key)

    def reset_session(self, *keys):
        for k in keys:
            self.last_activity.pop(k, None)
            peerk = self.expected_peer.pop(k, None)
            if peerk is not None and self.expected_peer.get(peerk) == k:
                self.expected_peer.pop(peerk, None)
            addr = self.key_to_addr.pop(k, None)
            if addr:
                self.addr_to_key.pop(addr, None)
                peer = self.peer_of.pop(addr, None)
                if peer is not None:
                    self.peer_of.pop(peer, None)

    def expect_pair(self, key_a: int, key_b: int):
        if key_a and key_b:
            self.expected_peer[key_a] = key_b
            self.expected_peer[key_b] = key_a

    def datagram_received(self, data: bytes, addr):
        flags = data[0] if data else 0
        has_key = bool(flags & RELAY_FLAG) and len(data) >= 17
        key = int.from_bytes(data[13:17], "big") if has_key else 0

        # 1) hello: register or re-bind key -> addr (NAT remaps must be followed)
        if flags == HELLO_FLAGS and has_key:
            bound = self.key_to_addr.get(key)
            if bound == addr:
                return
            if bound is None:
                self.peer_of.pop(addr, None)
            else:
                log.info("relay re-bind key=%#010x: %s -> %s", key, show_addr(bound), show_addr(addr))
                self.addr_to_key.pop(bound, None)
                peer = self.peer_of.pop(bound, None)
                if peer is not None:
                    self.peer_of[addr] = peer
                    self.peer_of[peer] = addr
            self.key_to_addr[key] = addr
            self.addr_to_key[addr] = key
            self._touch(key)
            return

        # 2) keyed packet -> forward to the target key, rewriting key@13 to the sender's key
        if has_key:
            dst = self.key_to_addr.get(key)
            if dst and dst != addr:
                self.peer_of[addr] = dst
                self.peer_of[dst] = addr
                sender_key = self.addr_to_key.get(addr)
                self._touch(sender_key)
                out = data if sender_key is None else data[:13] + sender_key.to_bytes(4, "big") + data[17:]
                self.transport.sendto(out, dst)
            return

        # 3) keyless packet -> cached peer, or the peer registered at matchmaking
        dst = self.peer_of.get(addr)
        if dst is None:
            mykey = self.addr_to_key.get(addr)
            peerkey = self.expected_peer.get(mykey) if mykey else None
            dst = self.key_to_addr.get(peerkey) if peerkey else None
            if dst and dst != addr:
                self.peer_of[addr] = dst
                self._touch(mykey)
        if dst and dst != addr:
            self.transport.sendto(data, dst)


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------
def detect_lan_ip() -> str:
    """The outbound interface address (no packet is actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("192.0.2.1", 9))     # TEST-NET-1, never routed anywhere real
            return sk.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def detect_public_ip() -> str:
    """RELAY_IP=public only: ask a what-is-my-IP service. This is the one outbound
    request the server can make; set RELAY_IP to a fixed address to avoid it."""
    import urllib.request
    for url in ("https://api.ipify.org", "https://icanhazip.com"):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ip = resp.read().decode().strip()
            socket.inet_aton(ip)
            if ip.count(".") == 3:
                return ip
        except Exception:
            continue
    log.warning("public IP lookup failed, falling back to the interface address")
    return detect_lan_ip()


async def main_async(host: str, port: int, relay_ip: str, relay_port: int, relay_port_range: str,
                     password: str, allow_taptap: bool):
    hub = Hub(password=password, allow_taptap=allow_taptap)
    if relay_ip == "public":
        relay_ip = detect_public_ip()
    elif relay_ip == "auto":
        relay_ip = detect_lan_ip()
    try:
        hub.relay_ip = struct.unpack(">I", socket.inet_aton(relay_ip))[0]
    except OSError:
        raise SystemExit(f"RELAY_IP is not an IPv4 address: {relay_ip!r}")
    hub.relay_port = relay_port

    async def handle(reader, writer):
        await Client(hub, reader, writer).run()

    server = await asyncio.start_server(handle, host, port)
    log.info("lobby listening on TCP %s:%d", host, port)

    loop = asyncio.get_running_loop()
    _, hub.relay = await loop.create_datagram_endpoint(UdpRelay, local_addr=(host, relay_port))
    # the relay address is what players' games are told to send to; always print it in full
    log.info("UDP relay main port %d, advertised relay address %s", relay_port, relay_ip)
    if relay_ip.startswith(("127.", "10.", "192.168.")) or relay_ip.startswith(
            tuple(f"172.{i}." for i in range(16, 32))):
        log.warning("relay address %s is private/loopback: players outside this network cannot reach it. "
                    "Set RELAY_IP to the public IP (or 'public').", relay_ip)

    ports = [p for p in parse_range(relay_port_range) if p != relay_port]
    if ports:
        hub.relay.pairs = PairPool(hub.relay)
        n_ok = await hub.relay.pairs.bind(host, ports)
        if n_ok:
            log.info("pair port pool UDP %d-%d: %d/%d bound (open this whole UDP range in your firewall)",
                     ports[0], ports[-1], n_ok, len(ports))
        else:
            hub.relay.pairs = None
            log.error("no pair port could be bound (%s): single-port mode, games of 3+ will drop", relay_port_range)
    else:
        log.warning("RELAY_PORT_RANGE is off: single-port mode, only one joiner per game")

    if password:
        log.info("server password is ON for classic logins; 1.26 TapTap logins are %s",
                 "allowed" if allow_taptap else "refused")
    else:
        log.info("no server password: any player name is let in")

    async with server:
        await server.serve_forever()


def load_env_file(path: Path) -> list[str]:
    """Minimal .env loader: KEY=VALUE lines, # comments, optional quotes. Does not
    override variables already in the environment. Returns the keys it set."""
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] in "'\"" and v[0] in v[1:]:
            v = v[1:v.index(v[0], 1)]          # quoted: taken literally, '#' included
        else:
            v = v.split(" #", 1)[0].strip()    # unquoted: " #" starts a comment
        if k and k not in os.environ:
            os.environ[k] = v
            loaded.append(k)
    return loaded


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def main():
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env", default=os.environ.get("LOBBY_ENV") or None, metavar="FILE",
                     help=".env file (default: .env next to this script)")
    pre_args, _ = pre.parse_known_args()
    env_path = Path(pre_args.env) if pre_args.env else Path(__file__).resolve().with_name(".env")
    loaded = load_env_file(env_path)

    ap = argparse.ArgumentParser(description="TL2 Mikuro Lobby Lite", parents=[pre])
    ap.add_argument("--host", default=os.environ.get("LOBBY_HOST") or "0.0.0.0",
                    help="listen address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("LOBBY_PORT") or 4549),
                    help="lobby TCP port (default 4549)")
    ap.add_argument("--relay-ip", default=os.environ.get("RELAY_IP") or "auto",
                    help="address players' games send relay UDP to: a fixed public IPv4 (recommended), "
                         "'public' (look it up at startup via ipify), or 'auto' (interface address, LAN only)")
    ap.add_argument("--relay-port", type=int, default=int(os.environ.get("RELAY_PORT") or 4549),
                    help="UDP relay main port (default 4549)")
    ap.add_argument("--relay-port-range", default=os.environ.get("RELAY_PORT_RANGE", "4550-4999"),
                    metavar="A-B", help="pair port pool, one UDP port per connected pair (default 4550-4999). "
                                        "0 = single-port mode")
    ap.add_argument("-v", "--verbose", action="store_true", default=env_bool("LOBBY_VERBOSE"),
                    help="debug log with a hexdump of every frame")
    args = ap.parse_args()

    pair_relay.LOG_IPS = env_bool("LOG_IPS")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    if loaded:
        log.info("loaded %s: %s", env_path, ", ".join(sorted(loaded)))    # key names only
    try:
        asyncio.run(main_async(args.host, args.port, args.relay_ip, args.relay_port, args.relay_port_range,
                               os.environ.get("LOBBY_PASSWORD", ""), env_bool("ALLOW_TAPTAP", True)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
