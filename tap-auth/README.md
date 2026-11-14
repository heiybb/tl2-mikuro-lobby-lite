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
| GET | `/capabilities` | returns `TL2AUTH\|1\|200710\|<env>\|<lobby host>\|<lobby port>\|1\|0\|` |
| POST | `/steam/exchange` | returns `TL2GRANT\|1\|<connection key>\|<credential>\|` |

Both are also served under an `/auth/v1/` prefix, so either base URL shape works.

### The worker must be paired with its lobby

The `capabilities` reply carries the lobby host and port, and the game checks them against the
`LOBBYHOST_XD` / `LOBBYPORT_XD` the launcher wrote for the selected server. So **`LOBBY_HOST` /
`LOBBY_PORT` in `wrangler.jsonc` must exactly match the lobby this worker's Auth URL is paired
with** (same string the server's `host` uses, same port), or the game abandons login before it
reaches the lobby. The shared `tl2-auth.chr.moe` therefore cannot serve an arbitrary
self-hosted lobby — deploy your own worker per lobby.

The game also needs Steam running and the account to own Torchlight II: it gets a real Steam
auth ticket before calling `steam/exchange`. The worker just doesn't check the ticket.

## Deploy

You need a domain in your Cloudflare account. **`https://<domain>` can be at most 34
characters**: the launcher overwrites the game's Steam auth base in place and appends `/`, and
that slot is 35 characters. For example, `auth.example.net` fits easily. `*.workers.dev`
hostnames are almost always too long.

1. Put your domain in `wrangler.jsonc` → `routes[0].pattern`.
2. Set `vars.LOBBY_HOST` / `vars.LOBBY_PORT` in `wrangler.jsonc` to the lobby this worker is
   paired with (see "The worker must be paired with its lobby" above). Leave `ENVIRONMENT` as
   `production`.
3. Deploy:
   ```bash
   bun install            # or npm install
   bunx wrangler login
   bun run deploy
   ```
4. Check it: `https://<domain>/capabilities` returns the pipe-delimited line with your lobby
   host/port.
5. Players enter `https://<domain>` as the **Auth URL** of your server in the launcher.
   If you ship a `servers.json` next to the launcher, put it in there.

Plain HTTP won't work. The game forces TLS on both requests, so the certificate must be valid.
Cloudflare issues one for the custom domain on its own.

Local test: `bun run dev`.

## What this is not

This is not an access gate. The lobby server trusts whatever name arrives in the credential,
and anyone can talk to the lobby directly. The Worker only exists because the 1.27 client
won't reach the lobby without finishing the Steam exchange. If you need to restrict access,
use the lobby's `ALLOW_STEAM` / `LOBBY_PASSWORD` options.
