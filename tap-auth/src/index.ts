// tl2-tap-auth: a stand-in Steam login endpoint for Torchlight II 1.27.0.0
// ============================================================================
// The Steam 1.27.0.0 update logs into the online lobby through a Steam Web Ticket
// exchange. The launcher (../launcher) repoints the game's STEAM_AUTH_BASE_URL (a .data
// wide string) to this Worker, and the game then does:
//
//   1. GET  {base}capabilities        -> a pipe-delimited capability line
//   2. POST {base}steam/exchange      with the Steam ticket + lobby host/port/connection_key
//      -> "TL2GRANT|1|<connection_key>|<credential hex>|", which becomes the lobby credential
//         carried in lobby message 55 (StartSteamLogin). The lobby ignores the credential.
//
// Verified against the 1.27.0.0 client and the official server:
//   - Both endpoints require TLS (WINHTTP_FLAG_SECURE is hard-coded), so this must be served
//     over https with a valid certificate (Cloudflare provides it).
//   - capabilities is parsed into exactly 8 "|"-separated fields with a trailing "|":
//       TL2AUTH | "1" | <appid> | <environment> | <lobby host> | <lobby port> | "1" | "0"
//     The client checks fields 0-5 against its own appid / STEAM_AUTH_ENVIRONMENT /
//     LOBBYHOST_XD / LOBBYPORT_XD, so they MUST match the lobby the player is connecting to,
//     and field 6 must be "1" or the login is abandoned before the exchange. Field 7 is an
//     extra flag ("0" is fine).
//   - steam/exchange returns 4 fields: "TL2GRANT" | "1" | <connection_key echoed back> |
//     <credential>. The credential must be an even-length hex string of >= 64 chars (the
//     client copies its first 64 bytes verbatim into the lobby message); its content is not
//     used by this lite lobby.
//   - The Steam ticket in the POST body is NOT checked. This is not an access gate: anyone can
//     talk to the lobby directly with any name. Use the lobby's own options for access control.
//
// One worker can serve several lobbies. capabilities is a bare GET, so the only thing that
// tells the worker which lobby the player is heading for is the Auth URL itself: each lobby
// gets its own path prefix ("https://auth.example.net/au" -> "/au/capabilities") listed in
// LOBBIES, and the bare root ("https://auth.example.net") maps to LOBBY_HOST / LOBBY_PORT.
//
// No accounts, no storage and no logging. The player's name comes from their Steam persona
// (the game puts it in the lobby message), not from here.

interface Env {
	// Path prefix -> "host" or "host:port" of the lobby that prefix is paired with, e.g.
	// { "au": "lobby-au.example.net", "us": "lobby-us.example.net:4549" }. The host/port must be
	// exactly what the launcher writes for that server (its Host / Port), because the client
	// compares them with its own LOBBYHOST_XD / LOBBYPORT_XD. Set in wrangler.jsonc -> vars
	// (an object, or the same thing as a JSON string).
	LOBBIES?: Record<string, string> | string;
	// The lobby for the bare root (Auth URL with no path). Optional.
	LOBBY_HOST?: string;
	LOBBY_PORT?: string;
	ENVIRONMENT?: string; // defaults to "production", matching the client's STEAM_AUTH_ENVIRONMENT
}

interface Lobby {
	host: string;
	port: string;
}

const APPID = "200710";
const DEFAULT_PORT = "4549";

// "/capabilities", "/au/capabilities", "/steam/exchange", "/au/steam/exchange"
const ROUTE = /^\/(?:([A-Za-z0-9_-]{1,32})\/)?(capabilities|steam\/exchange)$/;

function pipeLine(fields: string[], status = 200): Response {
	return new Response(fields.join("|") + "|", {
		status,
		headers: {
			"content-type": "text/plain;charset=utf-8",
			"X-Content-Type-Options": "nosniff",
			"Cache-Control": "no-store",
		},
	});
}

function lobbyFor(prefix: string, env: Env): Lobby | null {
	if (prefix === "") {
		return env.LOBBY_HOST ? { host: env.LOBBY_HOST, port: env.LOBBY_PORT || DEFAULT_PORT } : null;
	}
	let map: Record<string, string> = {};
	try {
		map = typeof env.LOBBIES === "string" ? JSON.parse(env.LOBBIES) : env.LOBBIES || {};
	} catch {
		return null;
	}
	// own keys only, compared case-insensitively ("AU" and "au" are the same lobby)
	const key = Object.keys(map).find(k => k.toLowerCase() === prefix.toLowerCase());
	const value = key === undefined ? "" : String(map[key]).trim();
	if (!value) return null;
	const colon = value.lastIndexOf(":");
	return colon > 0
		? { host: value.slice(0, colon), port: value.slice(colon + 1) }
		: { host: value, port: DEFAULT_PORT };
}

function capabilitiesLine(lobby: Lobby, env: Env): Response {
	const environment = env.ENVIRONMENT || "production";
	// field6 = "1" (login enabled) is required; field7 is a spare flag ("0").
	return pipeLine(["TL2AUTH", "1", APPID, environment, lobby.host, lobby.port, "1", "0"]);
}

async function steamExchange(req: Request): Promise<Response> {
	const form = new URLSearchParams(await req.text());
	// echo the client's connection_key back as field2; the client parses it as an integer and
	// requires it to equal the one it sent, so echo it verbatim (digits, optional sign only)
	const connKey = (form.get("connection_key") || "").trim();
	if (!/^-?[0-9]{1,19}$/.test(connKey)) return pipeLine(["TL2GRANT", "0", "0", "-"], 400);
	// credential: any even-length hex string of >= 64 chars. The lobby ignores it, so derive
	// a stable 64-hex-char value from the connection key for traceability.
	const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(connKey));
	const cred = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join(""); // 64 hex chars
	return pipeLine(["TL2GRANT", "1", connKey, cred]);
}

export default {
	async fetch(req: Request, env: Env): Promise<Response> {
		const m = ROUTE.exec(new URL(req.url).pathname);
		const lobby = m ? lobbyFor(m[1] || "", env) : null;
		if (!m || !lobby) return new Response("not found", { status: 404 });

		if (m[2] === "capabilities" && req.method === "GET") return capabilitiesLine(lobby, env);
		if (m[2] === "steam/exchange" && req.method === "POST") return steamExchange(req);
		return new Response("method not allowed", { status: 405 });
	},
};
