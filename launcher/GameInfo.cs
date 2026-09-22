using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace TL2LobbyLauncher;

public enum GameBuild { Unknown, DrmFree, Steam125, Steam126 }

/// <summary>Finds Torchlight2.exe and tells the builds apart by file version.</summary>
public static class GameInfo
{
    public const string ExeName = "Torchlight2.exe";
    public const string SteamAppId = "200710";

    /// <summary>First existing exe: next to the launcher, the saved folder, then Steam libraries.</summary>
    public static string? FindExe(string? savedDir)
    {
        foreach (var dir in CandidateDirs(savedDir))
        {
            try
            {
                string exe = Path.Combine(dir, ExeName);
                if (File.Exists(exe)) return exe;
            }
            catch { /* bad path in config */ }
        }
        return null;
    }

    private static IEnumerable<string> CandidateDirs(string? savedDir)
    {
        yield return AppContext.BaseDirectory;
        if (!string.IsNullOrWhiteSpace(savedDir)) yield return savedDir;
        foreach (var lib in SteamLibraries())
            yield return Path.Combine(lib, "steamapps", "common", "Torchlight II");
    }

    private static IEnumerable<string> SteamLibraries()
    {
        string? steam = null;
        try { steam = Registry.GetValue(@"HKEY_CURRENT_USER\Software\Valve\Steam", "SteamPath", null) as string; }
        catch { }
        if (string.IsNullOrEmpty(steam)) yield break;
        steam = steam.Replace('/', '\\');
        yield return steam;

        string vdf = Path.Combine(steam, "steamapps", "libraryfolders.vdf");
        string text;
        try { text = File.Exists(vdf) ? File.ReadAllText(vdf) : ""; }
        catch { text = ""; }
        foreach (Match m in Regex.Matches(text, "\"path\"\\s+\"([^\"]+)\""))
            yield return m.Groups[1].Value.Replace(@"\\", @"\");
    }

    public static string Version(string exe)
    {
        try { return (FileVersionInfo.GetVersionInfo(exe).FileVersion ?? "").Trim(); }
        catch { return ""; }
    }

    public static GameBuild Build(string exe)
    {
        string v = Version(exe);
        if (v.StartsWith("1.26", StringComparison.Ordinal)) return GameBuild.Steam126;
        if (v == "1.25.5.6") return GameBuild.Steam125;
        return v.Length > 0 ? GameBuild.DrmFree : GameBuild.Unknown;
    }

    public static bool IsSteam(GameBuild b) => b is GameBuild.Steam125 or GameBuild.Steam126;

    /// <summary>A steam_appid.txt next to the exe lets the Steam build start without going
    /// through the Steam client's Play button (Steam must still be running).</summary>
    public static void EnsureSteamAppId(string gameDir)
    {
        string path = Path.Combine(gameDir, "steam_appid.txt");
        if (!File.Exists(path)) File.WriteAllText(path, SteamAppId);
    }

    public static bool IsSteamRunning()
    {
        var procs = Process.GetProcessesByName("steam");
        try { return procs.Length > 0; }
        finally { foreach (var p in procs) p.Dispose(); }
    }
}
