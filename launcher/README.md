# TL2 Lobby Launcher

A small Windows launcher that does one job: choose which online lobby server
Torchlight II connects to, then start the game.

- Edits `Documents\My Games\Runic Games\Torchlight 2\local_settings.txt`. It writes
  `LOBBYHOST`/`LOBBYPORT` for 1.25.x and `LOBBYHOST_XD`/`LOBBYPORT_XD` for 1.26.x,
  as UTF-16LE with BOM, the same way the game writes the file.
- **Official / don't change** removes only the lines this launcher wrote. A host you
  typed in by hand stays.
- For the **Steam 1.26.0.1** build, when the server has an *Auth URL*, it starts the game
  suspended and rewrites the two built-in TapTap login URLs in the game's memory to that
  URL, then lets the game run. The exe on disk is never modified. See `../tap-auth`.
- For Steam builds it creates `steam_appid.txt` next to the exe if it's missing, so the
  game can start without Steam's Play button (Steam still has to be running).

It sends nothing anywhere. Its only network effect is the lobby address the game
will use.

## For players

1. Put `TL2LobbyLauncher.exe` anywhere. Next to `Torchlight2.exe` is easiest. It also
   finds Steam installs on its own, and **Browse…** covers everything else.
2. Pick one of the built-in public servers (Mikuro Australia / Mikuro US), or **Add**
   your own: a name, the host (domain or IPv4) and the port (default 4549). The Auth URL
   (1.26 only) defaults to the shared `https://tl2-auth.chr.moe`, which works with any
   server. Change it only if your server admin runs their own.
3. Select it and click **Launch game**.

Settings are stored in `%APPDATA%\TL2LobbyLauncher\config.json`.

## For server operators

Put a `servers.json` next to the exe before you hand it out (format:
[`servers.example.json`](servers.example.json)). Its entries are added to the player's
list on every start. Entries are matched by host, so the player's own edits are kept.

## Build

Needs the .NET 10 SDK on Windows.

```powershell
dotnet build -c Release
# one self-contained exe, no .NET install needed on the player's PC:
dotnet publish -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o publish
# smaller exe that needs the .NET 10 Desktop Runtime installed
dotnet publish -c Release -r win-x64 --self-contained false -p:PublishSingleFile=true -o publish-fd
```
