using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace TL2LobbyLauncher;

public sealed class ServerEntry
{
    public string Name { get; set; } = "";
    public string Host { get; set; } = "";
    public int Port { get; set; } = LobbySettings.DefaultPort;

    /// <summary>1.26.0.1 only: the tap-auth Worker, e.g. "https://auth.example.net". Empty = none.</summary>
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
    /// <summary>The launcher overwrites the game's built-in TapTap URL strings in place; the
    /// shortest is "https://www.taptapauth.com" (26 bytes), so ours cannot be longer.</summary>
    public const int MaxAuthUrlLength = 26;

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

    public static bool IsAuthUrl(string u)
    {
        if (u.Length > MaxAuthUrlLength || u.Any(c => c > 0x7E || c < 0x21)) return false;
        if (!u.StartsWith("https://", StringComparison.Ordinal)) return false;
        return IsHost(u["https://".Length..]);   // scheme + host only: no path, port or trailing slash
    }
}
