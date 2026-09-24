using System.IO;
using System.Text;

namespace TL2LobbyLauncher;

/// <summary>
/// Points the game at a lobby server by editing local_settings.txt in the settings folder
/// (Documents\My Games\Runic Games\Torchlight 2). The game reads the file at startup.
///
/// The key names differ by build: 1.25.x uses LOBBYHOST / LOBBYPORT, the 1.26 update uses
/// LOBBYHOST_XD / LOBBYPORT_XD. Both pairs are written every time; each build reads its own
/// and ignores the other, so the build doesn't need to be detected here.
///
/// Line format as the game writes it: "KEY :value", UTF-16LE with BOM, CRLF.
/// </summary>
public static class LobbySettings
{
    public const int DefaultPort = 4549;

    private static readonly (string Host, string Port)[] KeyPairs =
    {
        ("LOBBYHOST", "LOBBYPORT"),
        ("LOBBYHOST_XD", "LOBBYPORT_XD"),
    };

    public static string SettingsDir => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
        "My Games", "Runic Games", "Torchlight 2");

    public static string FilePath => Path.Combine(SettingsDir, "local_settings.txt");

    /// <summary>
    /// server != null: point every lobby key at it. server == null ("official"): remove only
    /// lines whose host is one of <paramref name="knownHosts"/> (plus their port line), so a
    /// host the player typed in by hand is never touched. Throws on I/O errors.
    /// </summary>
    public static void Apply(ServerEntry? server, IEnumerable<string> knownHosts, string? path = null)
    {
        path ??= FilePath;
        var lines = File.Exists(path) ? File.ReadAllLines(path).ToList() : new List<string>();
        var ours = new HashSet<string>(knownHosts, StringComparer.OrdinalIgnoreCase);
        bool changed = false;

        foreach (var (hostKey, portKey) in KeyPairs)
        {
            int hi = IndexOf(lines, hostKey);
            if (server != null)
            {
                Set(lines, hi, $"{hostKey} :{server.Host}");
                Set(lines, IndexOf(lines, portKey), $"{portKey} :{server.Port}");
                changed = true;
            }
            else if (hi >= 0 && ours.Contains(ValueOf(lines[hi])))
            {
                lines.RemoveAt(hi);
                int pi = IndexOf(lines, portKey);
                if (pi >= 0) lines.RemoveAt(pi);
                changed = true;
            }
        }
        if (!changed) return;

        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var sb = new StringBuilder();
        foreach (var l in lines) sb.Append(l).Append("\r\n");
        File.WriteAllText(path, sb.ToString(), new UnicodeEncoding(bigEndian: false, byteOrderMark: true));
    }

    private static void Set(List<string> lines, int idx, string line)
    {
        if (idx >= 0) lines[idx] = line; else lines.Add(line);
    }

    // "LOBBYHOST" must not match "LOBBYHOST_XD": require a space or ':' right after the key
    private static int IndexOf(List<string> lines, string key) => lines.FindIndex(l =>
    {
        string t = l.TrimStart();
        return t.StartsWith(key + " ", StringComparison.OrdinalIgnoreCase)
            || t.StartsWith(key + ":", StringComparison.OrdinalIgnoreCase);
    });

    private static string ValueOf(string line)
    {
        int c = line.IndexOf(':');
        return c >= 0 ? line.Substring(c + 1).Trim() : "";
    }
}
