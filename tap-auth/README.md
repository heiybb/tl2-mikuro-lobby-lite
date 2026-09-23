# tl2-tap-auth

Only needed for the **Steam 1.26.0.1** build of Torchlight II. Older builds (1.25.x) log
in with a plain name and password and don't use this at all.

The 1.26.0.1 update logs into the online lobby through TapTap OAuth. This Cloudflare Worker
answers that OAuth flow instead of TapTap: it sends the game straight back with a
code (the player doesn't click anything) and returns a player name as `kid`. The
launcher (`../launcher`) rewrites the game's two built-in TapTap URLs in memory so they
point here. The game's exe file on disk is not changed.

It has no accounts, no database and no logging. The player name lives in a cookie in
the player's own browser. It starts as a random `Player_xxxxxx`, and players can change
it at `https://<your-domain>/`.

You don't have to deploy this. The launcher uses a shared instance at
`https://tl2-auth.chr.moe` by default, and it works with any lobby server. Deploy your own
if you don't want to rely on it.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/authorize` | 302 back to the game's `http://127.0.0.1:<port>/...` `redirect_uri` with `code` and the original `state` (non-loopback redirects are refused) |
| POST | `/oauth2/v1/token` | checks the code, returns `{"kid":"<name>","mac_key":"-"}` |
| GET/POST | `/`, `/name` | optional page for choosing the lobby name |

## Deploy

You need a domain in your Cloudflare account. **`https://<domain>` can be at most 26
characters**, so the domain itself can have at most 18. The launcher overwrites the
game's URL in place, and the shortest built-in URL is 26 bytes. For example,
`auth.example.net` (16 characters) fits. `*.workers.dev` hostnames are almost always
too long.

1. Put your domain in `wrangler.jsonc` → `routes[0].pattern`.
2. Deploy:
   ```bash
   bun install            # or npm install
   bunx wrangler login
   bun run secret         # CODE_SECRET: any long random string
   bun run deploy
   ```
3. Check it: `https://<domain>/` should show the name page.
4. Players enter `https://<domain>` as the **Auth URL** of your server in the launcher.
   If you ship a `servers.json` next to the launcher, put it in there.

Plain HTTP won't work. The game forces TLS on the token request, so the certificate
must be valid. Cloudflare issues one for the custom domain on its own.

Local test: `bun run dev` (put `CODE_SECRET=anything` in `.dev.vars`).

## What this is not

This is not an access gate. The lobby server trusts whatever name arrives in the
credential, and anyone can talk to the lobby directly. The Worker only exists because
the 1.26 client won't reach the lobby without finishing an OAuth round trip. If you
need to restrict access, use the lobby's `ALLOW_TAPTAP` / `LOBBY_PASSWORD` options.
