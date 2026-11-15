# tl2-tap-auth

Only needed for the **Steam 1.27.0.0** build of Torchlight II. Older builds (1.25.x) log
in with a plain name and password and don't use this at all.

1.27.0.0 logs into the online lobby through a Steam Web Ticket exchange, and this Cloudflare
Worker answers it: it replies to a `capabilities` probe and a `steam/exchange` POST, handing
back a grant so the game proceeds to the lobby. The Steam ticket is not checked (this is not
an access gate).

The launcher (`../launcher`) rewrites the game's built-in Steam auth base in memory so it
points here. The game's exe file on disk is not changed.

It has no accounts, no database and no logging, and stores nothing about players (their name
comes from their Steam persona, which the game sends straight to the lobby).

> The 1.26.0.1 TapTap login this worker used to also answer has been retired; those endpoints
> are gone.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/<prefix>/capabilities` | returns `TL2AUTH\|1\|200710\|<env>\|<lobby host>\|<lobby port>\|1\|0\|` |
| POST | `/<prefix>/steam/exchange` | returns `TL2GRANT\|1\|<connection key>\|<credential>\|` |

`<prefix>` is one of the keys in `LOBBIES` and picks the lobby. Without a prefix
(`/capabilities`) the worker answers for `LOBBY_HOST` / `LOBBY_PORT`. Any other path is a 404.

### Each Auth URL is paired with one lobby

The `capabilities` reply carries the lobby host and port, and the game checks them against the
`LOBBYHOST_XD` / `LOBBYPORT_XD` the launcher wrote for the selected server. `capabilities` is a
bare GET, so the only thing that tells the worker which lobby the player wants is the Auth URL
itself. **Each Auth URL is therefore paired with exactly one lobby**, and the host/port for it
must match that server's Host / Port in the launcher exactly, or the game abandons login before
it reaches the lobby.

One worker can serve several lobbies, one per path:

```jsonc
"vars": {
  "LOBBIES": {
    "au": "lobby-au.example.net:4549",   // Auth URL https://auth.example.net/au
    "us": "lobby-us.example.net"         // Auth URL https://auth.example.net/us (port 4549)
  },
  "LOBBY_HOST": "lobby-au.example.net", // optional: Auth URL https://auth.example.net
  "LOBBY_PORT": "4549"
}
```

The author's shared worker only lists the author's servers; a self-hosted lobby needs its own.

The game also needs Steam running and the account to own Torchlight II: it gets a real Steam
auth ticket before calling `steam/exchange`. The worker just doesn't check the ticket.

## Deploy

You need a domain in your Cloudflare account. **Each Auth URL (`https://<domain>` plus an
optional `/<prefix>`) can be at most 34 characters**: the launcher overwrites the game's Steam
auth base in place and appends `/`, and that slot is 35 characters. For example,
`https://auth.example.net/au` is 27. `*.workers.dev` hostnames are almost always too long.

1. Put your domain in `wrangler.jsonc` → `routes[0].pattern`.
2. List your lobbies in `vars.LOBBIES` (and/or set `vars.LOBBY_HOST` / `vars.LOBBY_PORT` for
   the bare domain), see "Each Auth URL is paired with one lobby" above. Leave `ENVIRONMENT`
   as `production`.
3. Deploy:
   ```bash
   bun install            # or npm install
   bunx wrangler login
   bun run deploy
   ```
4. Check it: `https://<domain>/<prefix>/capabilities` returns the pipe-delimited line with
   that lobby's host/port.
5. Players enter `https://<domain>/<prefix>` as the **Auth URL** of that server in the launcher.
   If you ship a `servers.json` next to the launcher, put it in there.

Plain HTTP won't work. The game forces TLS on both requests, so the certificate must be valid.
Cloudflare issues one for the custom domain on its own.

Local test: `bun run dev`.

## What this is not

This is not an access gate. The lobby server trusts whatever name arrives in the credential,
and anyone can talk to the lobby directly. The Worker only exists because the 1.27 client
won't reach the lobby without finishing the Steam exchange. If you need to restrict access,
use the lobby's `ALLOW_STEAM` / `LOBBY_PASSWORD` options.
