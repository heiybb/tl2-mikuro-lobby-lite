using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.CompilerServices;
using System.Text;

namespace TL2LobbyLauncher;

/// <summary>
/// One packed *.MOD file, read from its header (little-endian):
///     u16 ver (== 4), u16 modver, u64 gamever, u32 offData, u32 offMan,
///     sstr title, sstr author, sstr descr, sstr website, sstr download,
///     i64 modid   (the GUID the scheme file refers to)
/// sstr = u16 UTF-16 code-unit count, then that many UTF-16LE units.
/// </summary>
public sealed class ModInfo
{
    public required string FilePath { get; init; }
    public long Guid { get; init; }
    public string Name { get; init; } = "";
    public string Author { get; init; } = "";
    public bool Valid { get; init; }

    public static ModInfo Read(string path)
    {
        try
        {
            using var fs = File.OpenRead(path);
            using var br = new BinaryReader(fs, Encoding.Unicode);
            if (br.ReadUInt16() != 4) return Invalid(path);
            br.ReadUInt16(); br.ReadUInt64(); br.ReadUInt32(); br.ReadUInt32();
            string title = SStr(br), author = SStr(br);
            SStr(br); SStr(br); SStr(br);            // description, website, download
            long guid = br.ReadInt64();
            return new ModInfo
            {
                FilePath = path,
                Guid = guid,
                Name = string.IsNullOrWhiteSpace(title) ? Path.GetFileNameWithoutExtension(path) : title.Trim(),
                Author = author.Trim(),
                Valid = guid != 0,
            };
        }
        catch { return Invalid(path); }
    }

    private static ModInfo Invalid(string path) => new() { FilePath = path, Name = Path.GetFileName(path) };

    private static string SStr(BinaryReader br)
    {
        ushort n = br.ReadUInt16();
        if (n > 8192) throw new InvalidDataException("string too long");
        byte[] b = br.ReadBytes(n * 2);
        if (b.Length != n * 2) throw new EndOfStreamException();
        return Encoding.Unicode.GetString(b);
    }
}

/// <summary>
/// modlauncher.sch in the settings folder: the active-mod list the game reads when started
/// with MODSCHEME=MODLAUNCHER.SCH. The same file the official ModLauncher writes, so the
/// launchers can be used interchangeably. Format (UTF-16LE with BOM, CRLF):
///     [MODS]
///     \t&lt;INTEGER64&gt;MODGUID:&lt;decimal id&gt;     (one per mod, in load order)
///     [/MODS]
/// </summary>
public static class SchemeFile
{
    public const string LaunchArg = "MODSCHEME=MODLAUNCHER.SCH";
    private const string GuidTag = "MODGUID:";

    public static string DefaultPath => Path.Combine(LobbySettings.SettingsDir, "modlauncher.sch");
    public static string ModsDir => Path.Combine(LobbySettings.SettingsDir, "mods");

    public static List<long> Read(string? path = null)
    {
        path ??= DefaultPath;
        var guids = new List<long>();
        if (!File.Exists(path)) return guids;
        foreach (string line in File.ReadAllLines(path))
        {
            int i = line.IndexOf(GuidTag, StringComparison.OrdinalIgnoreCase);
            if (i >= 0 && long.TryParse(line[(i + GuidTag.Length)..].Trim(), NumberStyles.Integer,
                    CultureInfo.InvariantCulture, out long g) && !guids.Contains(g))
                guids.Add(g);
        }
        return guids;
    }

    public static void Write(IEnumerable<long> guidsInLoadOrder, string? path = null)
    {
        path ??= DefaultPath;
        var sb = new StringBuilder("[MODS]\r\n");
        foreach (long g in guidsInLoadOrder.Distinct())
            sb.Append("\t<INTEGER64>").Append(GuidTag).Append(g.ToString(CultureInfo.InvariantCulture)).Append("\r\n");
        sb.Append("[/MODS]\r\n");
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, sb.ToString(), new UnicodeEncoding(bigEndian: false, byteOrderMark: true));
    }
}

/// <summary>List row for one mod.</summary>
public sealed class ModItem : INotifyPropertyChanged
{
    public ModInfo Info { get; }
    public ModItem(ModInfo info) => Info = info;

    public string Title => Info.Valid ? Info.Name : "⚠ " + Info.Name;
    public string Detail => Info.Valid
        ? (Info.Author.Length > 0 ? $"{Info.Author}  ·  " : "") + Path.GetFileName(Info.FilePath)
        : Loc.T("Mod_Invalid");
    public bool CanToggle => Info.Valid;

    private bool _enabled;
    public bool IsEnabled
    {
        get => _enabled;
        set { if (_enabled != value) { _enabled = value; Changed(); } }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed([CallerMemberName] string? n = null) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
}

public static class ModCatalog
{
    /// <summary>The engine activates at most 10 parent mods; more are refused. The launcher can
    /// patch that limit to 100 at launch (see GameLauncher).</summary>
    public const int StockMax = 10;
    public const int RaisedMax = 100;

    /// <summary>All mods on disk: enabled ones first in scheme order, then the rest by name.
    /// Several files with the same GUID: the first one found wins (the game can only load one).</summary>
    public static List<ModItem> Load(int maxEnabled)
    {
        var infos = new List<ModInfo>();
        try
        {
            if (Directory.Exists(SchemeFile.ModsDir))
                infos = Directory.GetFiles(SchemeFile.ModsDir, "*.mod", SearchOption.AllDirectories)
                    .OrderBy(p => p, StringComparer.OrdinalIgnoreCase)
                    .Select(ModInfo.Read).ToList();
        }
        catch { /* unreadable folder: show what we have */ }

        var byGuid = new Dictionary<long, ModInfo>();
        var rows = new List<ModItem>();
        foreach (var m in infos)
        {
            if (!m.Valid) { rows.Add(new ModItem(m)); continue; }
            byGuid.TryAdd(m.Guid, m);
        }

        var enabled = new List<ModItem>();
        List<long> scheme;
        try { scheme = SchemeFile.Read(); } catch { scheme = new(); }
        foreach (long g in scheme)
            if (byGuid.Remove(g, out var m) && enabled.Count < maxEnabled)
                enabled.Add(new ModItem(m) { IsEnabled = true });
            else if (m != null)
                byGuid[g] = m;                       // over the cap: keep it listed, disabled

        var rest = byGuid.Values.OrderBy(m => m.Name, StringComparer.CurrentCultureIgnoreCase).Select(m => new ModItem(m));
        return enabled.Concat(rest).Concat(rows).ToList();
    }
}
