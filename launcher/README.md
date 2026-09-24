# TL2 Lobby Launcher

A small Windows launcher: choose which online lobby server Torchlight II connects to,
choose your mods, then start the game.

- Edits `Documents\My Games\Runic Games\Torchlight 2\local_settings.txt`. It writes
  `LOBBYHOST`/`LOBBYPORT` for 1.25.x and `LOBBYHOST_XD`/`LOBBYPORT_XD` for 1.26.x,
  as UTF-16LE with BOM, the same way the game writes the file.
- **Official / don't change** removes only the lines this launcher wrote. A host you
  typed in by hand stays.
- For the **Steam 1.26.0.1** build, when the server has an *Auth URL*, it starts the game
  suspended and rewrites the two built-in TapTap login URLs in the game's memory to that
  URL, then lets the game run. The exe on disk is never modified. See `../tap-auth`.
- On 1.26 with one of your servers selected, it swaps the TapTap login box (title, button,
  notice, and their Chinese translations) and adds a "Server: host" line to the online game
  list, by patching a few entries of `PAKS\DATA.PAK` in place for that session only. See
  `PakPatch.cs` and `pakpatch/build.py`; the originals go back when the game exits.
- For Steam builds it creates `steam_appid.txt` next to the exe if it's missing, so the
  game can start without Steam's Play button (Steam still has to be running).

- **Mods**: lists every `.MOD` in `Documents\My Games\Runic Games\Torchlight 2\mods`
  (subfolders included). Tick the ones you want and set the load order with ▲ ▼. The choice
  is saved to `modlauncher.sch`, the same file the official ModLauncher uses, and the game
  is started with `MODSCHEME=MODLAUNCHER.SCH`. With nothing ticked the game starts unmodded.
  To join someone's game you need the same mods as the host.
- **Allow more than 10**: the game refuses to activate an 11th mod. With this ticked, and
  more than 10 mods enabled, the launcher changes that limit to 100 in the game's memory:
  - 1.25.x: while the game is still suspended, before it runs anything;
  - 1.26 (Steam stub): as soon as the stub has decrypted the code, with the game suspended
    for the write. Measured on 1.26.0.1: about 0.6 s after start, about 0.5 s before the
    game's data loading can begin.
  Steam Workshop subscriptions are not downloaded; use `.MOD` files already in the folder.

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

Needs the .NET 10 SDK on Windows. One project builds two targets:

- `net48`: the small exe. .NET Framework 4.8 comes with Windows 10 1903+ and 11, so players
  install nothing. System.Text.Json (the same library the net10 build uses, so both read and
  write `config.json` alike) and its dependencies are embedded by Costura: still one file.
- `net10.0-windows`: published self-contained, for PCs where the small one does not start.

Code that differs between them is under `#if NETFRAMEWORK` (`Zlib.cs`, the folder picker,
file replace). `Compat.cs` supplies the few compiler types .NET Framework lacks.

```powershell
dotnet build -c Release                     # both targets
# the small exe (the dlls publish copies next to it are embedded already; ship only the exe):
dotnet publish -c Release -f net48 -o publish-fx
# one self-contained exe, no .NET install needed on the player's PC:
dotnet publish -c Release -f net10.0-windows -r win-x64 --self-contained true -p:PublishSingleFile=true -o publish
```
