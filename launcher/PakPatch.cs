using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace TL2LobbyLauncher;

/// <summary>
/// 1.26 only: while the game runs, replaces a few entries of PAKS/DATA.PAK with our versions
/// (the TapTap login notice and its Chinese translations, and a "Server: host" line in the
/// online game list), and puts the originals back when the game exits.
///
/// Format. DATA.PAK is back-to-back blocks <c>[u32 size][u32 zsize][zlib]</c> with no gaps;
/// DATA.PAK.MAN lists every file as <c>{u32 crc32, u8 type, name, u32 off, u32 size, u64 ftime}</c>
/// plus one sampled hash (mhash) over the manifest.
///
/// The constraint. At startup the game checks a rolling hash of DATA.PAK: its length, the last
/// byte and one byte every length/(25..75) bytes (29 bytes for the 1.26 file). Any mismatch and
/// the archive is not mounted; the game then dies looking up its first font. So DATA.PAK must
/// keep its exact length and those sampled bytes, which rules out appending.
///
/// So everything happens inside the file. A new block overwrites its own original block, which
/// works when it compresses to no more than the old one; the two translations shrink by about
/// 3 KB each, and that leftover tail becomes free space. A block that grows (SERVERLIST gains a
/// widget) takes over the block right after it, after that neighbour has been copied verbatim
/// into free space and its record repointed. No write may touch a sampled byte.
///
/// Restoring writes every overwritten byte range back from a backup and puts the original
/// manifest back, so both files end up byte-identical to what Steam installed.
///
/// assets/pakpatch.json (built by pakpatch/build.py) holds only our changes, never game data:
/// splice ops for the two BINLAYOUTs, string pairs for the two BINDATs, and our own login
/// button image. Every entry is checked against its original size and crc first; a mismatch
/// skips that entry.
///
/// Crash safety: the backups and a state file are written before anything in the game folder
/// changes, so a launcher that dies mid-game is cleaned up on its next start.
/// </summary>
public static class PakPatch
{
    public readonly record struct Result(int Patched, string? Error);

    private sealed class Doc
    {
        public int PlaceholderLength { get; set; }
        public List<FilePatch> Files { get; set; } = new();
    }

    private sealed class FilePatch
    {
        public string Path { get; set; } = "";
        public string Kind { get; set; } = "";
        public uint OrigSize { get; set; }
        public uint OrigCrc { get; set; }
        public List<SpliceOp>? Ops { get; set; }
        public string? Placeholder { get; set; }
        public List<string[]>? Replace { get; set; }
        /// <summary>kind "replace": the whole new file, zlib-compressed, base64.</summary>
        public string? Data { get; set; }
    }

    private sealed class SpliceOp
    {
        public int At { get; set; }
        public int Del { get; set; }
        public string Ins { get; set; } = "";
    }

    private sealed class State
    {
        public long PakLength { get; set; }
        public string OrigManSha { get; set; } = "";
        public string PatchedManSha { get; set; } = "";
        /// <summary>Overwritten DATA.PAK ranges; their original bytes are concatenated, in this
        /// order, in <see cref="Paths.RangesBackup"/>.</summary>
        public List<long[]> Ranges { get; set; } = new();
    }

    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        WriteIndented = true,
    };

    /// <summary>The game files and the restore folder next to them. The folder exists only while
    /// a patch is in place, and lives with the game so any copy of the launcher, under any
    /// Windows account, finds it; Steam removes it with the game on uninstall.</summary>
    private sealed record Paths(string GameDir)
    {
        public string Paks => Path.Combine(GameDir, "PAKS");
        public string Pak => Path.Combine(Paks, "DATA.PAK");
        public string Man => Path.Combine(Paks, "DATA.PAK.MAN");
        public string StateDir => Path.Combine(Paks, "TL2LobbyLauncher-restore");
        public string State => Path.Combine(StateDir, "state.json");
        public string ManBackup => Path.Combine(StateDir, "DATA.PAK.MAN.orig");
        public string RangesBackup => Path.Combine(StateDir, "DATA.PAK.ranges.orig");
        public string Readme => Path.Combine(StateDir, "README.txt");
    }

    private const string ReadmeText =
        "This folder means the TL2 Lobby Launcher patch to DATA.PAK is still in place\r\n" +
        "(usually the game is still running, or the launcher was closed before the game exited).\r\n" +
        "To undo it: start TL2LobbyLauncher.exe once; it restores the files and deletes this folder.\r\n" +
        "Or in Steam: Torchlight II > Properties > Installed Files > Verify integrity of game files.\r\n" +
        "Please do not edit the files in here.\r\n" +
        "\r\n" +
        "这个文件夹在,说明 TL2 Lobby Launcher 对 DATA.PAK 的补丁还没还原\r\n" +
        "(通常是游戏还在运行,或者启动器在游戏退出前被关掉了)。\r\n" +
        "还原方法:打开一次 TL2LobbyLauncher.exe,它会自动还原并删掉这个文件夹;\r\n" +
        "或者在 Steam 里:Torchlight II > 属性 > 已安装文件 > 验证游戏文件的完整性。\r\n" +
        "请不要手动修改这里的文件。\r\n";

    /// <summary>Earlier builds kept the restore state in %APPDATA%; that location is no longer
    /// used, so remove whatever is left there.</summary>
    public static void RemoveLegacyState()
    {
        try
        {
            string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                                      "TL2LobbyLauncher", "pakpatch");
            if (Directory.Exists(dir)) Directory.Delete(dir, recursive: true);
        }
        catch { /* non-fatal */ }
    }

    /// <summary>Serializes patch/restore across launcher instances.</summary>
    private static T Locked<T>(Func<T> body)
    {
        using var m = new Mutex(false, @"Local\TL2LobbyLauncher.PakPatch");
        try { m.WaitOne(); }
        catch (AbandonedMutexException) { /* the other instance died; we own it now */ }
        try { return body(); }
        finally { m.ReleaseMutex(); }
    }

    /// <summary>Is a Torchlight2.exe from this game folder running? Another install (say a
    /// DRM-free copy elsewhere) does not count. A process whose path cannot be read counts as
    /// running, to be safe.</summary>
    public static bool GameRunning(string gameDir)
    {
        string want = Path.GetFullPath(Path.Combine(gameDir, GameInfo.ExeName));
        var procs = Process.GetProcessesByName(Path.GetFileNameWithoutExtension(GameInfo.ExeName));
        try
        {
            foreach (var p in procs)
            {
                string? path;
                try { path = p.MainModule?.FileName; }
                catch { return true; }
                if (path == null || string.Equals(Path.GetFullPath(path), want, StringComparison.OrdinalIgnoreCase))
                    return true;
            }
            return false;
        }
        finally { foreach (var p in procs) p.Dispose(); }
    }

    /// <summary>The server line text: gold "Server:" and the host, centered by padding both
    /// sides with spaces to the placeholder's exact length.</summary>
    public static string ServerLine(string host, int length)
    {
        const string prefix = "|cFFEAAA15Server:|u ";
        int room = length - prefix.Length;
        if (host.Length > room) host = host[..(room - 1)] + "…";
        string text = prefix + host;
        int pad = length - text.Length;
        return new string(' ', pad / 2) + text + new string(' ', pad - pad / 2);
    }

    /// <summary>Puts back any patch left by an earlier run. No-op while the game is running.</summary>
    public static string? RestoreIfNeeded(string gameDir)
    {
        try { return Locked(() => RestoreLocked(new Paths(gameDir))); }
        catch (Exception ex) { return ex.Message; }
    }

    private static string? RestoreLocked(Paths p)
    {
        if (!Directory.Exists(p.StateDir)) return null;
        if (GameRunning(p.GameDir)) return null;
        var st = File.Exists(p.State) ? JsonSerializer.Deserialize<State>(File.ReadAllText(p.State), Json) : null;

        if (st != null && File.Exists(p.Man) && File.Exists(p.Pak) && new FileInfo(p.Pak).Length == st.PakLength)
        {
            string cur = Sha(File.ReadAllBytes(p.Man));
            // Patched manifest: undo both. Original manifest: a run that died between writing
            // the blocks and the manifest; the ranges may be written, so put them back too.
            // Anything else means Steam replaced the files; leave them alone.
            if (cur == st.PatchedManSha || cur == st.OrigManSha)
            {
                if (File.Exists(p.RangesBackup)) WriteRanges(p.Pak, st.Ranges, File.ReadAllBytes(p.RangesBackup));
                if (cur == st.PatchedManSha)
                {
                    byte[] orig = File.ReadAllBytes(p.ManBackup);
                    if (Sha(orig) != st.OrigManSha) return "the DATA.PAK.MAN backup is damaged";
                    WriteReplace(p.Man, orig);
                }
            }
        }
        Directory.Delete(p.StateDir, recursive: true);
        return null;
    }

    /// <summary>Patches DATA.PAK for this launch. Patched == 0 with no error means nothing in
    /// this game version matched (a different build, or the files changed).</summary>
    public static Result Apply(string gameDir, string host)
    {
        try
        {
            return Locked(() =>
            {
                var p = new Paths(gameDir);
                string? err = RestoreLocked(p);
                if (err != null) return new Result(0, err);
                if (Directory.Exists(p.StateDir)) return new Result(0, "an earlier patch is still in place");
                if (GameRunning(gameDir)) return new Result(0, "the game is already running");
                return ApplyLocked(p, host);
            });
        }
        catch (Exception ex) { return new Result(0, ex.Message); }
    }

    /// <summary>A planned write into DATA.PAK.</summary>
    private sealed record Write(long At, byte[] Bytes);

    private static Result ApplyLocked(Paths p, string host)
    {
        string manPath = p.Man, pakPath = p.Pak;
        if (!File.Exists(manPath) || !File.Exists(pakPath)) return new Result(0, null);

        var doc = LoadDoc();
        byte[] man = File.ReadAllBytes(manPath);
        var recs = ParseManifest(man);
        byte[] patched = (byte[])man.Clone();
        var writes = new List<Write>();
        long pakLength;
        int count = 0;

        using (var pak = new FileStream(pakPath, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            pakLength = pak.Length;
            var sampled = SampledOffsets(pakLength);
            // records sharing one block, by block offset
            var byOff = recs.Values.GroupBy(r => r.Off).ToDictionary(g => (long)g.Key, g => g.ToList());
            var offs = byOff.Keys.OrderBy(o => o).ToArray();
            long NextBlock(long after)
            {
                int i = Array.BinarySearch(offs, after);
                return i >= 0 ? offs[i] : -1;
            }

            // New payloads, smallest growth first, so blocks that shrink free space before a
            // growing one needs it.
            var items = new List<(FilePatch f, Record rec, long slotLen, byte[] data, byte[] block)>();
            foreach (var f in doc.Files)
            {
                if (!recs.TryGetValue(f.Path, out var rec) || rec.Size != f.OrigSize || rec.Crc != f.OrigCrc) continue;
                if (byOff[rec.Off].Count != 1) continue;          // block shared with another record
                byte[] orig = ReadBlock(pak, rec.Off, out long slotLen);
                if (orig.Length != f.OrigSize || Crc32(orig) != f.OrigCrc) continue;
                byte[]? data = f.Kind switch
                {
                    "splice" => Splice(orig, f, doc.PlaceholderLength, host),
                    "strings" => ReplaceStrings(orig, f.Replace!),
                    "replace" => Inflate(Convert.FromBase64String(f.Data!)),
                    _ => null,
                };
                if (data != null) items.Add((f, rec, slotLen, data, Block(data)));
            }
            items.Sort((a, b) => (a.block.Length - a.slotLen).CompareTo(b.block.Length - b.slotLen));

            var taken = items.Select(i => (long)i.rec.Off).ToHashSet();   // blocks we replace
            var free = new List<(long at, long len)>();                     // leftover tails
            bool Clean(long at, long len) => !sampled.Any(s => s >= at && s < at + len);

            foreach (var it in items)
            {
                long start = it.rec.Off, end = start + it.slotLen;
                var moves = new List<(long from, long len, long to)>();
                var freeBefore = free.ToList();
                // Grow into following blocks until the new block fits, moving each one away.
                while (end - start < it.block.Length && moves.Count < 4)
                {
                    long nb = NextBlock(end);
                    if (nb < 0 || taken.Contains(nb)) break;
                    long len = BlockLength(pak, nb);
                    int slot = free.FindIndex(fr => fr.len >= len && Clean(fr.at, len));
                    if (slot < 0) break;
                    var fr = free[slot];
                    free[slot] = (fr.at + len, fr.len - len);
                    moves.Add((nb, len, fr.at));
                    end = nb + len;
                }
                if (end - start < it.block.Length || !Clean(start, it.block.Length))
                {
                    free = freeBefore;                                     // skip this entry
                    continue;
                }
                foreach (var (from, len, to) in moves)
                {
                    var bytes = new byte[len];
                    pak.Seek(from, SeekOrigin.Begin);
                    pak.ReadExactly(bytes);
                    writes.Add(new Write(to, bytes));
                    foreach (var r in byOff[from]) BitConverter.TryWriteBytes(patched.AsSpan(r.OffAt), (uint)to);
                    taken.Add(from);
                }
                writes.Add(new Write(start, it.block));
                BitConverter.TryWriteBytes(patched.AsSpan(it.rec.CrcAt), Crc32(it.data));
                BitConverter.TryWriteBytes(patched.AsSpan(it.rec.OffAt + 4), (uint)it.data.Length);
                if (end - start > it.block.Length) free.Add((start + it.block.Length, end - start - it.block.Length));
                count++;
            }
            if (count == 0) return new Result(0, null);

            // Back up every byte we are about to overwrite, and the manifest, before any write.
            Directory.CreateDirectory(p.StateDir);
            File.WriteAllText(p.Readme, ReadmeText, new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
            var st = new State { PakLength = pakLength, OrigManSha = Sha(man) };
            using (var bk = new FileStream(p.RangesBackup, FileMode.Create, FileAccess.Write))
            {
                foreach (var w in writes)
                {
                    var old = new byte[w.Bytes.Length];
                    pak.Seek(w.At, SeekOrigin.Begin);
                    pak.ReadExactly(old);
                    bk.Write(old);
                    st.Ranges.Add(new[] { w.At, w.Bytes.Length });
                }
                bk.Flush(true);
            }
            File.WriteAllBytes(p.ManBackup, man);
            BitConverter.TryWriteBytes(patched.AsSpan(2), ManifestHash(patched));
            st.PatchedManSha = Sha(patched);
            File.WriteAllText(p.State, JsonSerializer.Serialize(st, Json));
        }

        try
        {
            using (var pak = new FileStream(pakPath, FileMode.Open, FileAccess.Write, FileShare.Read))
            {
                foreach (var w in writes)
                {
                    pak.Seek(w.At, SeekOrigin.Begin);
                    pak.Write(w.Bytes);
                }
                pak.Flush(true);
                if (pak.Length != pakLength) throw new IOException("DATA.PAK changed length");
            }
            WriteReplace(manPath, patched);
        }
        catch
        {
            RestoreLocked(p);
            throw;
        }
        return new Result(count, null);
    }

    // ---- payload edits ----

    private static byte[] Splice(byte[] orig, FilePatch f, int placeholderLength, string host)
    {
        var ms = new MemoryStream(orig.Length + 1024);
        int pos = 0;
        foreach (var op in f.Ops!)
        {
            ms.Write(orig, pos, op.At - pos);
            ms.Write(Convert.FromBase64String(op.Ins));
            pos = op.At + op.Del;
        }
        ms.Write(orig, pos, orig.Length - pos);
        byte[] data = ms.ToArray();

        if (f.Placeholder != null)
        {
            byte[] ph = Encoding.Unicode.GetBytes(f.Placeholder);
            int at = data.AsSpan().IndexOf(ph);
            if (at < 0) return data;
            byte[] line = Encoding.Unicode.GetBytes(ServerLine(host, placeholderLength));
            if (line.Length != ph.Length) throw new InvalidOperationException("server line has the wrong length");
            line.CopyTo(data, at);
        }
        return data;
    }

    /// <summary>BINDAT: <c>u32 ver, u32 count, u32 firstId</c>, then the string table (entry 0
    /// <c>u16 len, wchar[]</c>, the rest <c>u32 id, u16 len, wchar[]</c>), then the node tree,
    /// which refers to strings by id only. Swapping a string's text keeps its id, so nothing
    /// else in the file moves.</summary>
    private static byte[]? ReplaceStrings(byte[] orig, List<string[]> pairs)
    {
        var map = pairs.ToDictionary(p => p[0], p => p[1], StringComparer.Ordinal);
        if (BitConverter.ToUInt32(orig, 0) != 2) return null;
        int count = BitConverter.ToInt32(orig, 4);
        var ms = new MemoryStream(orig.Length + 4096);
        ms.Write(orig, 0, 12);
        int o = 12, hits = 0;
        for (int k = 0; k < count; k++)
        {
            if (k > 0) { ms.Write(orig, o, 4); o += 4; }
            int n = BitConverter.ToUInt16(orig, o);
            string s = Encoding.Unicode.GetString(orig, o + 2, 2 * n);
            o += 2 + 2 * n;
            if (map.TryGetValue(s, out var repl)) { s = repl; hits++; }
            ms.Write(BitConverter.GetBytes((ushort)s.Length));
            ms.Write(Encoding.Unicode.GetBytes(s));
        }
        if (hits != map.Count) return null;   // not the file we built the pairs from
        ms.Write(orig, o, orig.Length - o);
        return ms.ToArray();
    }

    // ---- DATA.PAK / DATA.PAK.MAN ----

    private sealed record Record(int CrcAt, int OffAt, uint Crc, uint Off, uint Size);

    private static Dictionary<string, Record> ParseManifest(byte[] b)
    {
        int o = 2 + 4;
        SkipString(b, ref o);
        o += 4;                                   // file count
        uint dirs = BitConverter.ToUInt32(b, o); o += 4;
        var recs = new Dictionary<string, Record>(StringComparer.Ordinal);
        for (uint d = 0; d < dirs; d++)
        {
            string dir = ReadString(b, ref o);
            uint n = BitConverter.ToUInt32(b, o); o += 4;
            for (uint i = 0; i < n; i++)
            {
                int crcAt = o;
                uint crc = BitConverter.ToUInt32(b, o);
                o += 5;                           // crc + type
                string name = ReadString(b, ref o);
                int offAt = o;
                uint off = BitConverter.ToUInt32(b, o);
                uint size = BitConverter.ToUInt32(b, o + 4);
                o += 16;                          // off, size, ftime
                if (name.Length > 0) recs[dir + name] = new Record(crcAt, offAt, crc, off, size);
            }
        }
        return recs;
    }

    private static string ReadString(byte[] b, ref int o)
    {
        int n = BitConverter.ToUInt16(b, o);
        string s = Encoding.Unicode.GetString(b, o + 2, 2 * n);
        o += 2 + 2 * n;
        return s;
    }

    private static void SkipString(byte[] b, ref int o) => o += 2 + 2 * BitConverter.ToUInt16(b, o);

    /// <summary>mhash: seed 8234, h = (sbyte)b + 33h over every stride-th byte from 0, with
    /// stride = 15 + (695696193 * len mod 2^32) mod 11. The stride never lands on the hash
    /// field itself (bytes 2..5).</summary>
    private static uint ManifestHash(byte[] b)
    {
        uint n = (uint)b.Length;
        int stride = (int)(15 + unchecked(695696193u * n) % 11);
        uint h = 8234;
        for (int k = 0; k < b.Length; k += stride) h = unchecked((uint)(sbyte)b[k] + h * 33);
        return h;
    }

    /// <summary>The bytes the game's DATA.PAK rolling hash reads: every stride-th from offset 8,
    /// with stride = len / (25 + (695696193 * len mod 2^32) mod 51), plus the last byte.</summary>
    private static List<long> SampledOffsets(long n)
    {
        uint divisor = 25 + unchecked(695696193u * (uint)n) % 51;
        long stride = Math.Max(n / divisor, 2);
        var s = new List<long>();
        for (long k = 8; k < n; k += stride) s.Add(k);
        s.Add(n - 1);
        return s;
    }

    private static long BlockLength(FileStream pak, long off)
    {
        var hdr = new byte[8];
        pak.Seek(off, SeekOrigin.Begin);
        pak.ReadExactly(hdr);
        uint size = BitConverter.ToUInt32(hdr, 0), zsize = BitConverter.ToUInt32(hdr, 4);
        return 8L + (zsize == 0 ? size : zsize);
    }

    private static byte[] ReadBlock(FileStream pak, long off, out long blockLength)
    {
        var hdr = new byte[8];
        pak.Seek(off, SeekOrigin.Begin);
        pak.ReadExactly(hdr);
        uint size = BitConverter.ToUInt32(hdr, 0), zsize = BitConverter.ToUInt32(hdr, 4);
        var raw = new byte[zsize == 0 ? size : zsize];
        pak.ReadExactly(raw);
        blockLength = 8L + raw.Length;
        if (zsize == 0) return raw;
        var data = new byte[size];
        using var z = new ZLibStream(new MemoryStream(raw), CompressionMode.Decompress);
        z.ReadExactly(data);
        return data;
    }

    private static byte[] Inflate(byte[] z)
    {
        var ms = new MemoryStream();
        using (var s = new ZLibStream(new MemoryStream(z), CompressionMode.Decompress)) s.CopyTo(ms);
        return ms.ToArray();
    }

    /// <summary><c>[u32 size][u32 zsize][zlib]</c>, compressed as small as zlib goes.</summary>
    private static byte[] Block(byte[] data)
    {
        var ms = new MemoryStream();
        ms.Write(BitConverter.GetBytes((uint)data.Length));
        ms.Write(new byte[4]);
        using (var z = new ZLibStream(ms, CompressionLevel.SmallestSize, leaveOpen: true)) z.Write(data);
        byte[] b = ms.ToArray();
        BitConverter.TryWriteBytes(b.AsSpan(4), (uint)(b.Length - 8));
        return b;
    }

    private static void WriteRanges(string pakPath, List<long[]> ranges, byte[] backup)
    {
        using var pak = new FileStream(pakPath, FileMode.Open, FileAccess.Write, FileShare.Read);
        int pos = 0;
        foreach (var r in ranges)
        {
            pak.Seek(r[0], SeekOrigin.Begin);
            pak.Write(backup, pos, (int)r[1]);
            pos += (int)r[1];
        }
        if (pos != backup.Length) throw new IOException("the DATA.PAK range backup is damaged");
        pak.Flush(true);
    }

    /// <summary>Write next to the target, then swap it in, so a failure never leaves a half file.</summary>
    private static void WriteReplace(string path, byte[] data)
    {
        string tmp = path + ".tl2ll.tmp";
        File.WriteAllBytes(tmp, data);
        File.Move(tmp, path, overwrite: true);
    }

    private static Doc LoadDoc()
    {
        using var s = typeof(PakPatch).Assembly.GetManifestResourceStream("pakpatch.json")
                      ?? throw new InvalidOperationException("pakpatch.json is not embedded");
        return JsonSerializer.Deserialize<Doc>(s, Json) ?? new Doc();
    }

    private static string Sha(byte[] b) => Convert.ToHexString(SHA256.HashData(b));

    private static readonly uint[] CrcTable = Enumerable.Range(0, 256).Select(i =>
    {
        uint c = (uint)i;
        for (int k = 0; k < 8; k++) c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1;
        return c;
    }).ToArray();

    private static uint Crc32(byte[] b)
    {
        uint c = 0xFFFFFFFF;
        foreach (byte x in b) c = CrcTable[(c ^ x) & 0xFF] ^ (c >> 8);
        return ~c;
    }
}
