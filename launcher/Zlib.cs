using System.IO;
using System.IO.Compression;
#if NETFRAMEWORK
using System.Runtime.InteropServices;
#endif

namespace TL2LobbyLauncher;

/// <summary>
/// zlib streams (2-byte header, deflate data, Adler-32), the format DATA.PAK blocks use.
///
/// Compression must be as small as zlib gets (level 9): a patched block has to fit in the
/// space of the original, and the translations only free room at level 9 (at level 6 they
/// shrink by about 20 bytes, at level 9 by about 3 KB).
///   .NET 10:   ZLibStream with CompressionLevel.SmallestSize.
///   .NET 4.8:  DeflateStream stops at level 6, so call the zlib that .NET Framework itself
///              ships and uses (clrcompression.dll in the runtime folder) with level 9. If that
///              ever fails to load, fall back to DeflateStream; a block that then does not fit
///              is skipped by PakPatch, never written.
/// </summary>
internal static class Zlib
{
    public static byte[] Compress(byte[] data)
    {
#if NETFRAMEWORK
        try { return Native.Compress(data); }
        catch (Exception ex) when (ex is DllNotFoundException or EntryPointNotFoundException or BadImageFormatException
                                       or InvalidOperationException) { }
        return CompressManaged(data);
#else
        var ms = new MemoryStream();
        using (var z = new ZLibStream(ms, CompressionLevel.SmallestSize, leaveOpen: true)) z.Write(data, 0, data.Length);
        return ms.ToArray();
#endif
    }

    /// <summary>Inflates a zlib stream; <paramref name="size"/> is the expected length, or -1.</summary>
    public static byte[] Decompress(byte[] z, int size = -1)
    {
        if (z.Length < 6 || (z[0] & 0x0F) != 8 || ((z[0] << 8) | z[1]) % 31 != 0)
            throw new InvalidDataException("not a zlib stream");
        if ((z[1] & 0x20) != 0) throw new InvalidDataException("zlib preset dictionary not supported");
        var ms = size >= 0 ? new MemoryStream(size) : new MemoryStream();
        using (var d = new DeflateStream(new MemoryStream(z, 2, z.Length - 2), CompressionMode.Decompress))
            d.CopyTo(ms);
        byte[] data = ms.ToArray();
        uint want = (uint)(z[z.Length - 4] << 24 | z[z.Length - 3] << 16 | z[z.Length - 2] << 8 | z[z.Length - 1]);
        if (Adler32(data) != want) throw new InvalidDataException("zlib checksum mismatch");
        return data;
    }

    private static uint Adler32(byte[] data)
    {
        const uint Mod = 65521;
        uint a = 1, b = 0;
        int i = 0;
        while (i < data.Length)
        {
            int n = Math.Min(5552, data.Length - i);        // largest run that cannot overflow
            for (int end = i + n; i < end; i++) { a += data[i]; b += a; }
            a %= Mod; b %= Mod;
        }
        return (b << 16) | a;
    }

#if NETFRAMEWORK
    /// <summary>Level-6 fallback: raw deflate from DeflateStream, wrapped as zlib by hand.</summary>
    private static byte[] CompressManaged(byte[] data)
    {
        var ms = new MemoryStream();
        ms.WriteByte(0x78);
        ms.WriteByte(0x9C);                                 // deflate, 32K window, default level
        using (var d = new DeflateStream(ms, CompressionLevel.Optimal, leaveOpen: true)) d.Write(data, 0, data.Length);
        uint adler = Adler32(data);
        ms.WriteByte((byte)(adler >> 24)); ms.WriteByte((byte)(adler >> 16));
        ms.WriteByte((byte)(adler >> 8)); ms.WriteByte((byte)adler);
        return ms.ToArray();
    }

    /// <summary>zlib's own deflate, from the copy .NET Framework ships. The declarations mirror
    /// the ones System.IO.Compression uses for the same DLL (ZLibNative).</summary>
    private static class Native
    {
        private const int Z_OK = 0, Z_STREAM_END = 1, Z_FINISH = 4, Z_DEFLATED = 8;
        private const int BestCompression = 9, WindowBits = 15 /* zlib header */, MemLevel = 8, DefaultStrategy = 0;

        [StructLayout(LayoutKind.Sequential)]
        private struct ZStream
        {
            public IntPtr nextIn; public uint availIn; public uint totalIn;
            public IntPtr nextOut; public uint availOut; public uint totalOut;
            public IntPtr msg; public IntPtr state;
            public IntPtr zalloc; public IntPtr zfree; public IntPtr opaque;
            public int dataType; public uint adler; public uint reserved;
        }

        [DllImport("clrcompression.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int deflateInit2_(ref ZStream s, int level, int method, int windowBits, int memLevel,
                                                int strategy, [MarshalAs(UnmanagedType.LPStr)] string version, int streamSize);

        [DllImport("clrcompression.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int deflate(ref ZStream s, int flush);

        [DllImport("clrcompression.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int deflateEnd(ref ZStream s);

        [DllImport("kernel32", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr LoadLibraryW(string path);

        private static readonly Lazy<bool> Loaded = new(() =>
            LoadLibraryW(Path.Combine(RuntimeEnvironment.GetRuntimeDirectory(), "clrcompression.dll")) != IntPtr.Zero);

        public static byte[] Compress(byte[] data)
        {
            // Load it by full path from the runtime folder, so the DllImport above binds to
            // that module and never to a same-named file elsewhere on the search path.
            if (!Loaded.Value) throw new DllNotFoundException("clrcompression.dll");
            var output = new byte[data.Length + data.Length / 8 + 1024];     // > deflateBound
            var hin = GCHandle.Alloc(data, GCHandleType.Pinned);
            var hout = GCHandle.Alloc(output, GCHandleType.Pinned);
            // zlib keeps a pointer back to the z_stream, so it must not move between calls:
            // it is a local, and a blittable struct passed by ref goes by its stack address.
            var s = new ZStream();
            try
            {
                int rc = deflateInit2_(ref s, BestCompression, Z_DEFLATED, WindowBits, MemLevel, DefaultStrategy,
                                       "1.2.3", Marshal.SizeOf<ZStream>());
                if (rc != Z_OK) throw new InvalidOperationException($"deflateInit2_ returned {rc}");
                try
                {
                    s.nextIn = hin.AddrOfPinnedObject(); s.availIn = (uint)data.Length;
                    s.nextOut = hout.AddrOfPinnedObject(); s.availOut = (uint)output.Length;
                    rc = deflate(ref s, Z_FINISH);
                    if (rc != Z_STREAM_END) throw new InvalidOperationException($"deflate returned {rc}");
                }
                finally { deflateEnd(ref s); }
                var result = new byte[s.totalOut];
                Buffer.BlockCopy(output, 0, result, 0, result.Length);
                return result;
            }
            finally { hin.Free(); hout.Free(); }
        }
    }
#endif
}
