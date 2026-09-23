# TL2 Mikuro Lobby Lite

A self-hosted online lobby for **Torchlight II**. Run it on any machine with a
public IPv4 address, point your friends' games at it, and host, browse and join
online games the way the official lobby used to work. The game exe is not modified.

[中文说明](README.zh-CN.md)

This is the stripped-down, self-host edition. It has no accounts, no statistics, no
telemetry and no phone-home. The server keeps who is online and which games exist in
memory, and that's all it keeps.

| Folder | What it is |
|---|---|
| [`server/`](server) | The lobby + UDP relay. Python 3.9+, standard library only |
| [`tap-auth/`](tap-auth) | A Cloudflare Worker, **only needed for the Steam 1.26.0.1 build** (its login goes through TapTap OAuth) |
| [`launcher/`](launcher) | A small Windows (WPF) launcher: pick a server, start the game |

Tested with the DRM-free 1.25.9.5 build, Steam 1.25.5.6 and Steam 1.26.0.1.

## Download

Prebuilt files are on the [Releases](../../releases) page:

- `TL2LobbyLauncher.exe`: the launcher, self-contained, no install needed
- `TL2LobbyLauncher-net10-runtime.exe`: the same launcher, much smaller, needs the
  [.NET 10 Desktop Runtime](https://dotnet.microsoft.com/download/dotnet/10.0)
- `tl2-lobby-lite-server.zip`: the server files

## How it works

The game logs into a central lobby server, gets the list of hosted games from it, and
asks it to connect players. The actual gameplay traffic is peer-to-peer between the
players. The lobby address is a plain setting (`LOBBYHOST` in `local_settings.txt`), and
the client doesn't verify who answers, so a compatible server can take over.

Direct peer-to-peer connections fail behind most home routers, so this server also
relays the game traffic over UDP. Each connected pair of players gets its own UDP port.
TL2 online play is a full mesh (every player connects to every other player), so an
N-player game uses N×(N−1)/2 ports and all of its traffic passes through your server.

## Run a server

You need a machine with a public IPv4 address that is reachable on:

- TCP **4549** (lobby)
- UDP **4549** (relay main port)
- UDP **4550–4999** (pair ports, the default range)

On a cloud VM, open these in the provider's firewall / security group too.

```bash
git clone <this repo> && cd tl2-mikuro-lobby-lite/server
cp .env.example .env
nano .env                   # set RELAY_IP to the machine's public IP
```

Then pick one of these:

```bash
sudo ./deploy/install.sh               # systemd service, opens the ports in ufw/firewalld/iptables
docker compose up -d --build           # Docker, host networking
python3 lobby_server.py                # just run it in the foreground
```

Check it from another PC: `python test_client.py --host <server>`. It should print `[OK]`.

If you give the server a domain name and use Cloudflare DNS, the record must be
**DNS only (grey cloud)**. The proxy only carries HTTP and will swallow the game's raw
TCP/UDP.

### Options (`server/.env`)

| Key | Default | Meaning |
|---|---|---|
| `RELAY_IP` | `auto` | Address players' games send relay traffic to. Use the public IP. `public` looks it up at start (the one outbound request the server can make) |
| `LOBBY_PORT` / `RELAY_PORT` | `4549` | TCP lobby port / UDP relay main port |
| `RELAY_PORT_RANGE` | `4550-4999` | Pair ports; a 4-player game uses 6, a 6-player game 15 |
| `LOBBY_PASSWORD` | empty | Optional shared password for 1.25.x clients (any name + this password) |
| `ALLOW_TAPTAP` | `1` | Let 1.26 (TapTap login) clients in. They can't send a password |
| `LOG_IPS` | `0` | `0` masks player IPs in the log as `a.b.x.x` |
| `LOBBY_VERBOSE` | `0` | Hexdump every lobby frame (protocol debugging) |

### Supporting the Steam 1.26.0.1 build

The 1.26.0.1 update replaced the name/password login with TapTap OAuth, so those
players need an *Auth URL* in the launcher. The auth endpoint only hands out a player
name and works with any lobby server. The launcher fills in the shared one,
`https://tl2-auth.chr.moe`, for every new server, so a self-hosted lobby works for 1.26
players without extra setup.

If you'd rather not depend on it, deploy [`tap-auth/`](tap-auth) to your own Cloudflare
account and have players use that URL instead. It can be at most 26 characters
(`https://` plus a domain of up to 18 characters). The tap-auth README explains why.

## Connect as a player

**With the launcher** (`launcher/`): add the server's host, select it, then click
**Launch game**. 1.26.0.1 players have to launch through it.

The launcher ships with two public servers run by the author, **Mikuro Australia** and
**Mikuro US**. Those run the full edition of the lobby, not this lite build, and keep
statistics for server administration (online list, per-player traffic, login log). If that
matters to you, delete them and use your own server.

**By hand** (1.25.x only): quit the game completely, open
`Documents\My Games\Runic Games\Torchlight 2\local_settings.txt` in Notepad, and set:

```
LOBBYHOST :your.server.address
LOBBYPORT :4549
```

Players don't need to open any ports.

## Build from source

```bash
# server: nothing to build; run the tests with
cd server && python run_tests.py

# launcher (Windows, .NET 10 SDK)
dotnet publish launcher -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o publish
# -> publish/TL2LobbyLauncher.exe. Use --self-contained false for the small runtime-dependent exe.

# tap-auth: see tap-auth/README.md (bun install && bun run deploy)
```

Pushing a `v*` tag runs `.github/workflows/release.yml`, which tests the server, builds
both launcher variants and publishes a GitHub Release.

## Privacy and security notes

- **The server** holds names, IP addresses and the game list in memory only. It writes
  no files. Logs go to stdout / the systemd journal, with IPs masked by default. It makes
  no outbound connections unless you set `RELAY_IP=public`.
- **tap-auth**, as shipped here, has no database and logs nothing (Workers observability
  is off in `wrangler.jsonc`). A player's name lives in a cookie in their own browser. The
  shared instance at `tl2-auth.chr.moe` is operated by the author; deploy your own if you
  want full control.
- **The launcher** only edits `local_settings.txt`, writes `steam_appid.txt` for Steam
  builds, and keeps its own settings in `%APPDATA%\TL2LobbyLauncher`.
- The lobby protocol is **plain TCP**, a limit of the game itself. `LOBBY_PASSWORD` uses
  the client's built-in challenge-response, so the password never crosses the wire. Its
  hash chain is weak, though (20 rounds of SHA-256), and anyone who can sniff the traffic
  can brute-force a short password offline. Use a long random one, and treat it as
  "keeps strangers out", not strong authentication.
- tap-auth is **not** an access gate. The lobby accepts whatever name a 1.26 client
  presents. Turn 1.26 clients off with `ALLOW_TAPTAP=0` if that matters to you.
- Relay keys are random 32-bit values, and mesh connections are only arranged between
  players in the same live game.

## Limits

- IPv4 only.
- The server is a relay, not an authoritative game host. The hosting player's game
  still decides everything in the game, as it did with the official lobby.
- Friends lists and other social messages are accepted and ignored.

Released under the [MIT License](LICENSE).

Unofficial project, not affiliated with Runic Games or the publishers of Torchlight II.
No game files are included.
