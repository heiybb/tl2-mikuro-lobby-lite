using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace TL2LobbyLauncher;

public sealed class ServerEntry
{
    public string Name { get; set; } = "";
    public string Host { get; set; } = "";
    public int Port { get; set; } = LobbySettings.DefaultPort;

    /// <summary>1.27.0.0 only: the tap-auth Worker paired with this lobby, e.g.
    /// "https://auth.example.net" or "https://auth.example.net/au". Empty = none.</summary>
    public string AuthUrl { get; set; } = "";

    [JsonIgnore]
    public string Display => Port == LobbySettings.DefaultPort ? $"{Name}  ({Host})" : $"{Name}  ({Host}:{Port})";
}

/// <summary>
/// Launcher settings in %APPDATA%\TL2LobbyLauncher\config.json. A servers.json next to the
/// exe (same "servers" array) is merged in on every start, so a server operator can hand
/// out the launcher with their server pre-filled; entries are matched by host.
/// </summary>
public sealed class AppConfig
{
    public string? GameDir { get; set; }

    /// <summary>Host of the selected server; null/empty = "official / don't change".</summary>
    public string? SelectedHost { get; set; }

    public List<ServerEntry> Servers { get; set; } = new();

    /// <summary>Lift the game's 10-mod limit to 100 when launching (in-memory patch).</summary>
    public bool AllowOver10 { get; set; }

    /// <summary>Which revision of <see cref="Defaults"/> has been added to this config. Defaults
    /// are added once per revision, so a player who deletes one doesn't get it back.</summary>
    public int DefaultsRevision { get; set; }

    /// <summary>Public servers run by the project author (they run the full edition of the
    /// lobby, not this lite one; see README). They share one auth Worker (tap-auth/), each on
    /// its own path, because a worker path is paired with exactly one lobby.</summary>
    private const int DefaultsRev = 2;
    private static readonly ServerEntry[] Defaults =
    {
        new() { Name = "Mikuro Australia", Host = "tl2-mikuro-server-au.chr.moe", AuthUrl = "https://steam-auth.chr.moe/au" },
        new() { Name = "Mikuro US", Host = "tl2-mikuro-server-us.chr.moe", AuthUrl = "https://steam-auth.chr.moe/us" },
    };

    /// <summary>The shared worker's old address (no longer resolves). Revision 2 moves the
    /// default servers off it.</summary>
    private const string RetiredAuthUrl = "https://tl2-auth.chr.moe";

    private static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };

    private static string ConfigPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "TL2LobbyLauncher", "config.json");

    private static string BundledPath => Path.Combine(AppContext.BaseDirectory, "servers.json");

    public static AppConfig Load()
    {
        AppConfig cfg;
        try
        {
            cfg = File.Exists(ConfigPath)
                ? JsonSerializer.Deserialize<AppConfig>(File.ReadAllText(ConfigPath), Json) ?? new AppConfig()
                : new AppConfig();
        }
        catch { cfg = new AppConfig(); }

        if (cfg.DefaultsRevision < DefaultsRev)
        {
            foreach (var d in Defaults)
            {
                var have = cfg.Servers.FirstOrDefault(x => string.Equals(x.Host, d.Host, StringComparison.OrdinalIgnoreCase));
                if (have == null)
                {
                    // revision 1 added the defaults; a player who deleted one since keeps it deleted
                    if (cfg.DefaultsRevision < 1)
                        cfg.Servers.Add(new ServerEntry { Name = d.Name, Host = d.Host, Port = d.Port, AuthUrl = d.AuthUrl });
                }
                else if (string.IsNullOrEmpty(have.AuthUrl)
                         || string.Equals(have.AuthUrl, RetiredAuthUrl, StringComparison.OrdinalIgnoreCase))
                {
                    have.AuthUrl = d.AuthUrl;   // revision 2: the retired shared address -> this server's path
                }
            }
            cfg.DefaultsRevision = DefaultsRev;
            cfg.Save();
        }

        try
        {
            if (File.Exists(BundledPath))
            {
                var bundled = JsonSerializer.Deserialize<AppConfig>(File.ReadAllText(BundledPath), Json);
                foreach (var s in bundled?.Servers ?? new())
                {
                    if (ServerValidation.Validate(s) != null) continue;
                    if (cfg.Servers.Any(x => string.Equals(x.Host, s.Host, StringComparison.OrdinalIgnoreCase))) continue;
                    cfg.Servers.Add(s);
                }
            }
        }
        catch { /* a broken servers.json just adds nothing */ }

        cfg.Servers.RemoveAll(s => ServerValidation.Validate(s) != null);
        return cfg;
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(ConfigPath)!);
            File.WriteAllText(ConfigPath, JsonSerializer.Serialize(this, Json));
        }
        catch { /* non-fatal */ }
    }
}

public static class ServerValidation
{
    /// <summary>The launcher overwrites the game's built-in Steam auth base in place and appends
    /// "/", so the Auth URL must fit in that slot ("https://tl2-auth.xdgtw.com/auth/v1/", 35
    /// chars) minus the trailing slash.</summary>
    public const int MaxAuthUrlLength = 34;

    /// <summary>null = valid, otherwise a Loc key describing the problem.</summary>
    public static string? Validate(ServerEntry s)
    {
        if (string.IsNullOrWhiteSpace(s.Name)) return "Err_Name";
        if (!IsHost(s.Host)) return "Err_Host";
        if (s.Port is < 1 or > 65535) return "Err_Port";
        if (!string.IsNullOrEmpty(s.AuthUrl) && !IsAuthUrl(s.AuthUrl)) return "Err_AuthUrl";
        return null;
    }

    private static bool IsHost(string h) =>
        !string.IsNullOrWhiteSpace(h) && h.Length <= 253
        && Uri.CheckHostName(h) is UriHostNameType.Dns or UriHostNameType.IPv4;

    /// <summary>"https://host" or "https://host/path" (path segments of letters, digits, "-" and
    /// "_"; one worker serves several lobbies on different paths). No port, query or trailing slash.</summary>
    public static bool IsAuthUrl(string u)
    {
        if (u.Length > MaxAuthUrlLength || u.Any(c => c > 0x7E || c < 0x21)) return false;
        if (!u.StartsWith("https://", StringComparison.Ordinal)) return false;
        string[] parts = u.Substring("https://".Length).Split('/');
        if (!IsHost(parts[0])) return false;
        return parts.Skip(1).All(seg => seg.Length > 0
            && seg.All(c => c is (>= 'a' and <= 'z') or (>= 'A' and <= 'Z') or (>= '0' and <= '9') or '-' or '_'));
    }
}
