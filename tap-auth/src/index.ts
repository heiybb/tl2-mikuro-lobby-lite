// tl2-tap-auth: a stand-in TapTap OAuth endpoint for Torchlight II 1.26.0.1
// ============================================================================
// The Steam 1.26.0.1 update logs into the online lobby through TapTap OAuth. The
// launcher (../launcher) rewrites the two TapTap hosts in the game's memory to this
// Worker, and the game then does:
//
//   1. opens the browser at  GET  /authorize?...&redirect_uri=http://127.0.0.1:PORT/...&state=...
//   2. this Worker 302s straight back to redirect_uri?code=...&state=...  (no clicks)
//   3. the game's local listener gets the code and POSTs /oauth2/v1/token
//   4. the reply {"kid": "...", "mac_key": "..."} becomes the lobby credential
//      "kid,mac_key" (lobby message 54). The lobby uses kid as the player's name.
//
// Verified against the 1.26.0.1 client:
//   - The token reply is parsed by substring scan: find("kid") -> ':' -> '"' -> next '"'.
//     So the body only has to contain "kid":"<v>" and "mac_key":"<v>" literally.
//   - kid and mac_key must both be non-empty. mac_key is passed through untouched.
//   - credential = kid + "," + mac_key, split at the first comma: no commas in either.
//   - The token request always uses TLS (WINHTTP_FLAG_SECURE is hard-coded), so this
//     must be served over https with a valid certificate (Cloudflare provides it).
//
// No accounts and no storage: the name lives in a cookie in the player's own browser
// (random "Player_xxxxxx" on first use; changeable at https://<your-domain>/ ). Nothing
// is logged by this code. Note that this is NOT an access gate: anyone can talk to the
// lobby directly with any name. Use the lobby's own options for access control.

interface Env {
	CODE_SECRET: string; // wrangler secret put CODE_SECRET
}

const CODE_TTL_MS = 5 * 60 * 1000;
const COOKIE = "tl2id";
const NAME_RE = /^[\p{L}\p{N}_\-]{1,20}$/u;

// base64url: the code travels URL -> local listener -> form-urlencoded body; '+', '/'
// and '=' of plain base64 get mangled on that trip.
function b64url(bytes: Uint8Array): string {
	let s = "";
	for (const b of bytes) s += String.fromCharCode(b);
	return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(s: string): Uint8Array {
	s = s.replace(/-/g, "+").replace(/_/g, "/");
	while (s.length % 4) s += "=";
	const bin = atob(s);
	const out = new Uint8Array(bin.length);
	for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
	return out;
}

async function hmac(secret: string, body: string): Promise<string> {
	const key = await crypto.subtle.importKey(
		"raw", new TextEncoder().encode(secret),
		{ name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
	const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
	return b64url(new Uint8Array(sig)).slice(0, 22);
}

// self-verifying code carrying the player name; nothing is stored server-side
async function signCode(secret: string, player: string): Promise<string> {
	const body = b64url(new TextEncoder().encode(JSON.stringify({ player, exp: Date.now() + CODE_TTL_MS })));
	return `${body}.${await hmac(secret, body)}`;
}

async function verifyCode(secret: string, code: string | null): Promise<string | null> {
	const [body, mac] = (code || "").split(".");
	if (!body || !mac) return null;
	if (mac !== await hmac(secret, body)) return null;
	try {
		const p = JSON.parse(new TextDecoder().decode(b64urlDecode(body)));
		if (Date.now() > p.exp || typeof p.player !== "string" || !NAME_RE.test(p.player)) return null;
		return p.player;
	} catch {
		return null;
	}
}

function cookieName(req: Request): string | null {
	for (const part of (req.headers.get("cookie") || "").split(";")) {
		const i = part.indexOf("=");
		if (i < 0 || part.slice(0, i).trim() !== COOKIE) continue;
		try {
			const v = decodeURIComponent(part.slice(i + 1).trim());
			return NAME_RE.test(v) ? v : null;
		} catch {
			return null;
		}
	}
	return null;
}

function setCookie(name: string): string {
	return `${COOKIE}=${encodeURIComponent(name)}; Max-Age=31536000; Path=/; Secure; HttpOnly; SameSite=Lax`;
}

// The game's listener is on loopback. Refusing anything else keeps this from being an
// open redirect for arbitrary sites.
function loopbackRedirect(raw: string | null): URL | null {
	if (!raw) return null;
	try {
		const u = new URL(raw);
		const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(u.hostname);
		return u.protocol === "http:" && loopback ? u : null;
	} catch {
		return null;
	}
}

function escapeHtml(s: string): string {
	return s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
}

function namePage(current: string | null, note = ""): Response {
	const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>TL2 lobby name</title>
<style>body{font:16px system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem;background:#15120f;color:#eee}
input,button{font:inherit;padding:.5rem .7rem;border-radius:6px;border:1px solid #555;background:#221d18;color:#eee}
button{background:#b5561c;border-color:#b5561c;cursor:pointer}small{color:#aaa}</style></head><body>
<h1>Torchlight II lobby name</h1>
<p>Current name: <b>${current ? escapeHtml(current) : "(not set yet, a random one is picked on first login)"}</b></p>
<form method="post" action="/name">
<input name="name" maxlength="20" required pattern="[\\p{L}\\p{N}_\\-]{1,20}" value="${current ? escapeHtml(current) : ""}">
<button type="submit">Save</button></form>
<p><small>1-20 letters, digits, _ or -. Saved only as a cookie in this browser (the one the game opens).
Log out and back in inside the game to use the new name.</small></p>
${note ? `<p>${escapeHtml(note)}</p>` : ""}
</body></html>`;
	return new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } });
}

export default {
	async fetch(req: Request, env: Env): Promise<Response> {
		const url = new URL(req.url);

		// 1. GET /authorize: straight back to the game with a code
		if (url.pathname === "/authorize" && req.method === "GET") {
			const redirect = loopbackRedirect(url.searchParams.get("redirect_uri"));
			if (!redirect) return new Response("redirect_uri must be a http loopback address", { status: 400 });
			const state = url.searchParams.get("state") || "";

			let player = cookieName(req);
			const headers: Record<string, string> = { "Cache-Control": "no-store" };
			if (!player) {
				player = "Player_" + crypto.randomUUID().slice(0, 6);
				headers["Set-Cookie"] = setCookie(player);
			}
			// append rather than re-serialize: keeps the game's own URL byte-for-byte and
			// encodes state with %20 (not '+'), so it round-trips exactly
			const base = redirect.toString();
			const code = await signCode(env.CODE_SECRET, player);
			headers.Location = `${base}${base.includes("?") ? "&" : "?"}code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`;
			return new Response(null, { status: 302, headers });
		}

		// 2. POST /oauth2/v1/token: code -> {kid, mac_key}
		if (url.pathname === "/oauth2/v1/token" && req.method === "POST") {
			const form = new URLSearchParams(await req.text());
			const player = await verifyCode(env.CODE_SECRET, form.get("code"));
			if (!player) return Response.json({ error: "invalid_grant" }, { status: 400 });
			// kid = the player name (no commas, guaranteed by NAME_RE); mac_key is a placeholder
			return Response.json({ kid: player, mac_key: "-" }, { headers: { "Cache-Control": "no-store" } });
		}

		// 3. optional: let players pick their lobby name
		if (url.pathname === "/name" && req.method === "POST") {
			const name = String((await req.formData()).get("name") || "").trim();
			if (!NAME_RE.test(name)) return namePage(cookieName(req), "Invalid name.");
			const res = namePage(name, "Saved.");
			res.headers.set("Set-Cookie", setCookie(name));
			return res;
		}
		if (url.pathname === "/" && req.method === "GET") return namePage(cookieName(req));

		return new Response("not found", { status: 404 });
	},
};
