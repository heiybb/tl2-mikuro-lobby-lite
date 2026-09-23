using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace TL2LobbyLauncher;

/// <summary>
/// Starts the game, optionally with two in-memory patches. The exe file on disk is never modified.
///
/// 1. TapTap auth (1.26 only): rewrites the two TapTap OAuth URL strings.
/// 2. Mod limit: the mod-activation routine does `mov eax,0Ah` and refuses any parent mod past
///    the 10th ("Unable to activate mod because there are already 10 parent mods active.").
///    Both of its checks compare against that one immediate, so changing 0x0A to 0x64 lifts the
///    limit to 100. The signature below is unique in 1.25.9.5, Steam 1.25.5.6 and 1.26.0.1:
///        B8 0A 00 00 00 39 ?? 1C 73 ?? 39 ?? 84 00 00 00 73 ?? 8B
///        mov eax,0Ah   cmp [+1Ch]  jae  cmp [+84h]        jae  mov
///    The limit is checked once, early in data loading.
///    - 1.25.x: .text is plaintext while suspended, so the byte is written before the game
///      runs a single instruction.
///    - 1.26: the Steam stub keeps .text encrypted until it runs, so after resuming we scan
///      until the decrypted signature shows up, suspend the whole process, write the byte and
///      resume. The stub decrypts at the very start; the data-loading thread starts only after
///      the renderer (d3d9.dll) is loaded, which is much later.
///
/// Timing: with CREATE_SUSPENDED the image is mapped but not one game instruction has run.
/// The strings live in .rdata, which the Steam stub does not encrypt, so the plaintext is
/// already there and our write survives the stub's .text decryption on resume.
///
/// Why overwrite the string bytes instead of pointing the code at a new buffer: the .data
/// slots that hold the pointers are relocation targets, and writing them would race the
/// loader's ASLR fix-ups. String bytes are never relocated, so an in-place overwrite needs
/// no allocation and has no race. The cost is a length limit: the new URL must fit in the
/// shortest original (26 bytes).
/// </summary>
public static class GameLauncher
{
    /// <summary>The built-in 1.26.0.1 TapTap hosts: the token endpoint and the authorize page.</summary>
    private static readonly string[] TapTapUrls =
    {
        "https://accounts.tapapis.com",
        "https://www.taptapauth.com",
    };

    private static readonly int?[] CapSig =
    {
        0xB8, 0x0A, 0x00, 0x00, 0x00, 0x39, null, 0x1C, 0x73, null,
        0x39, null, 0x84, 0x00, 0x00, 0x00, 0x73, null, 0x8B,
    };
    private const byte RaisedCap = 0x64;   // 100

    public enum CapResult { NotRequested, Raised, NotFound }

    /// <param name="CapMs">1.26 only: ms from resume until the limit was patched (diagnostics).</param>
    /// <param name="ProcessId">The game's process id, 0 if it did not start.</param>
    public readonly record struct Result(bool Started, string? Error, int UrlsReplaced, CapResult Cap, long CapMs = -1,
                                         int ProcessId = 0);

    /// <summary>Blocking (the 1.26 limit patch waits for the stub to decrypt, normally well under
    /// a second); call it off the UI thread.</summary>
    public static Result Start(string exe, string arguments, string? authUrl, bool raiseCap, bool packed)
    {
        string dir = Path.GetDirectoryName(exe)!;
        if (authUrl == null && !raiseCap)
        {
            try
            {
                using var p = Process.Start(new ProcessStartInfo { FileName = exe, Arguments = arguments,
                                                                   WorkingDirectory = dir, UseShellExecute = false });
                return new Result(true, null, 0, CapResult.NotRequested, ProcessId: p?.Id ?? 0);
            }
            catch (Exception ex) { return new Result(false, ex.Message, 0, CapResult.NotRequested); }
        }

        var si = new STARTUPINFO { cb = Marshal.SizeOf<STARTUPINFO>() };
        var cmd = new StringBuilder($"\"{exe}\" {arguments}".TrimEnd());
        if (!CreateProcessW(exe, cmd, IntPtr.Zero, IntPtr.Zero, false, CREATE_SUSPENDED,
                            IntPtr.Zero, dir, ref si, out var pi))
            return new Result(false, $"CreateProcess failed (win32 error {Marshal.GetLastWin32Error()})", 0,
                              CapResult.NotRequested);

        int replaced = 0;
        string? error = null;
        var cap = raiseCap ? CapResult.NotFound : CapResult.NotRequested;
        long capMs = -1;
        uint imageBase = 0;
        bool haveBase = false;
        try
        {
            haveBase = TryReadImageBase(pi.hProcess, out imageBase);
            if (!haveBase) error = "could not read the image base";
            else
            {
                if (authUrl != null) replaced = RepointTapTap(pi.hProcess, exe, imageBase, authUrl);
                if (raiseCap && !packed && PatchCapNow(pi.hProcess, exe, imageBase)) cap = CapResult.Raised;
            }
        }
        catch (Exception ex) { error = ex.Message; }
        finally
        {
            ResumeThread(pi.hThread);   // always let the game run, patched or not
            CloseHandle(pi.hThread);
        }
        try
        {
            if (raiseCap && packed && haveBase)
            {
                var sw = Stopwatch.StartNew();
                if (PatchCapAfterDecrypt(pi.hProcess, exe, imageBase)) { cap = CapResult.Raised; capMs = sw.ElapsedMilliseconds; }
            }
        }
        catch (Exception ex) { error ??= ex.Message; }
        finally { CloseHandle(pi.hProcess); }
        return new Result(true, error, replaced, cap, capMs, pi.dwProcessId);
    }

    // ---- mod limit ----

    private static bool PatchCapNow(IntPtr hProc, string exe, uint imageBase)
    {
        if (!TryGetSection(exe, ".text", out uint rva, out uint size)) return false;
        IntPtr start = (IntPtr)((long)imageBase + rva);
        var code = new byte[size];
        if (!ReadProcessMemory(hProc, start, code, (int)size, out IntPtr read)) return false;
        int idx = FindSig(code, (int)read);
        return idx >= 0 && WriteCapByte(hProc, start + idx + 1);
    }

    /// <summary>1.26: rescan .text until the stub has decrypted it, then patch with the process
    /// suspended so no game thread runs between finding the site and writing it.</summary>
    private static bool PatchCapAfterDecrypt(IntPtr hProc, string exe, uint imageBase)
    {
        if (!TryGetSection(exe, ".text", out uint rva, out uint size)) return false;
        IntPtr start = (IntPtr)((long)imageBase + rva);
        var code = new byte[size];
        var deadline = Stopwatch.StartNew();
        while (deadline.Elapsed < TimeSpan.FromSeconds(20))
        {
            if (WaitForSingleObject(hProc, 0) == 0) return false;          // game already exited
            if (ReadProcessMemory(hProc, start, code, (int)size, out IntPtr read))
            {
                int idx = FindSig(code, (int)read);
                if (idx >= 0)
                {
                    if (NtSuspendProcess(hProc) != 0) return WriteCapByte(hProc, start + idx + 1);
                    try { return WriteCapByte(hProc, start + idx + 1); }
                    finally { NtResumeProcess(hProc); }
                }
            }
            Thread.Sleep(1);
        }
        return false;
    }

    private static bool WriteCapByte(IntPtr hProc, IntPtr target)
    {
        var cur = new byte[1];
        if (!ReadProcessMemory(hProc, target, cur, 1, out _)) return false;
        if (cur[0] == RaisedCap) return true;
        if (cur[0] != 0x0A) return false;
        if (!VirtualProtectEx(hProc, target, (IntPtr)1, PAGE_EXECUTE_READWRITE, out uint old)) return false;
        bool ok = WriteProcessMemory(hProc, target, new[] { RaisedCap }, 1, out _);
        VirtualProtectEx(hProc, target, (IntPtr)1, old, out _);
        FlushInstructionCache(hProc, target, (IntPtr)1);
        return ok;
    }

    private static int FindSig(byte[] data, int length)
    {
        int last = length - CapSig.Length;
        for (int i = 0; i <= last; i++)
        {
            if (data[i] != 0xB8 || data[i + 1] != 0x0A) continue;   // fast prefilter
            bool ok = true;
            for (int j = 2; j < CapSig.Length && ok; j++)
                ok = CapSig[j] is not int b || data[i + j] == (byte)b;
            if (ok) return i;
        }
        return -1;
    }

    // ---- TapTap ----

    /// <summary>32-bit PEB ImageBaseAddress (offset 8): valid while suspended, correct under ASLR.</summary>
    private static bool TryReadImageBase(IntPtr hProc, out uint imageBase)
    {
        imageBase = 0;
        IntPtr peb32 = IntPtr.Zero;
        if (NtQueryInformationProcess(hProc, ProcessWow64Information, ref peb32, IntPtr.Size, out _) != 0
            || peb32 == IntPtr.Zero)
            return false;
        var buf = new byte[4];
        if (!ReadProcessMemory(hProc, peb32 + 8, buf, 4, out _)) return false;
        imageBase = BitConverter.ToUInt32(buf, 0);
        return true;
    }

    private static int RepointTapTap(IntPtr hProc, string exe, uint imageBase, string newUrl)
    {
        if (!TryGetSection(exe, ".rdata", out uint rva, out uint size)) return 0;
        IntPtr start = (IntPtr)((long)imageBase + rva);
        var data = new byte[size];
        if (!ReadProcessMemory(hProc, start, data, (int)size, out IntPtr read) || (long)read <= 0) return 0;

        byte[] newBytes = Encoding.ASCII.GetBytes(newUrl);
        int replaced = 0;
        foreach (string original in TapTapUrls)
        {
            byte[] pat = Encoding.ASCII.GetBytes(original);
            if (newBytes.Length > pat.Length) continue;
            int idx = FindString(data, (int)read, pat);
            if (idx < 0) continue;

            // new URL, then NUL through the original's terminator; never past its own footprint
            var payload = new byte[pat.Length + 1];
            Array.Copy(newBytes, payload, newBytes.Length);
            IntPtr target = start + idx;
            if (!VirtualProtectEx(hProc, target, (IntPtr)payload.Length, PAGE_READWRITE, out uint old)) continue;
            bool ok = WriteProcessMemory(hProc, target, payload, payload.Length, out _);
            VirtualProtectEx(hProc, target, (IntPtr)payload.Length, old, out _);
            if (ok) replaced++;
        }
        return replaced;
    }

    /// <summary>A whole NUL-terminated string starting on a NUL boundary, never a substring.</summary>
    private static int FindString(byte[] data, int length, byte[] pat)
    {
        int last = length - pat.Length - 1;
        for (int i = 0; i <= last; i++)
        {
            if (i > 0 && data[i - 1] != 0) continue;
            bool ok = true;
            for (int j = 0; j < pat.Length && ok; j++) ok = data[i + j] == pat[j];
            if (ok && data[i + pat.Length] == 0) return i;
        }
        return -1;
    }

    private static bool TryGetSection(string exe, string name, out uint rva, out uint size)
    {
        rva = size = 0;
        try
        {
            var h = new byte[0x1000];
            using (var fs = File.OpenRead(exe))
                if (fs.Read(h, 0, h.Length) < 0x200) return false;
            if (h[0] != 'M' || h[1] != 'Z') return false;
            int e = BitConverter.ToInt32(h, 0x3C);
            if (e <= 0 || e + 0xF8 > h.Length || h[e] != 'P' || h[e + 1] != 'E') return false;
            short nSec = BitConverter.ToInt16(h, e + 6);
            short optSize = BitConverter.ToInt16(h, e + 20);
            int tab = e + 24 + optSize;
            var want = new byte[8];
            Encoding.ASCII.GetBytes(name, 0, Math.Min(name.Length, 8), want, 0);
            for (int i = 0; i < nSec; i++)
            {
                int b = tab + i * 40;
                if (b + 40 > h.Length) break;
                if (!h.AsSpan(b, 8).SequenceEqual(want)) continue;
                size = BitConverter.ToUInt32(h, b + 8);
                rva = BitConverter.ToUInt32(h, b + 12);
                return size > 0 && rva > 0;
            }
        }
        catch { }
        return false;
    }

    // ---- Win32 ----
    private const uint CREATE_SUSPENDED = 0x4;
    private const uint PAGE_READWRITE = 0x04;
    private const uint PAGE_EXECUTE_READWRITE = 0x40;
    private const int ProcessWow64Information = 26;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public int cb;
        public IntPtr lpReserved, lpDesktop, lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION
    {
        public IntPtr hProcess, hThread;
        public int dwProcessId, dwThreadId;
    }

    [DllImport("kernel32", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CreateProcessW(string? app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit,
        uint flags, IntPtr env, string? cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);

    [DllImport("ntdll")]
    private static extern int NtQueryInformationProcess(IntPtr h, int cls, ref IntPtr info, int len, out int retLen);

    [DllImport("kernel32", SetLastError = true)]
    private static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, int size, out IntPtr read);

    [DllImport("kernel32", SetLastError = true)]
    private static extern bool WriteProcessMemory(IntPtr h, IntPtr addr, byte[] buf, int size, out IntPtr written);

    [DllImport("kernel32", SetLastError = true)]
    private static extern bool VirtualProtectEx(IntPtr h, IntPtr addr, IntPtr size, uint prot, out uint old);

    [DllImport("kernel32", SetLastError = true)]
    private static extern uint ResumeThread(IntPtr h);

    [DllImport("kernel32", SetLastError = true)]
    private static extern uint WaitForSingleObject(IntPtr h, uint ms);

    [DllImport("kernel32", SetLastError = true)]
    private static extern bool FlushInstructionCache(IntPtr h, IntPtr addr, IntPtr size);

    [DllImport("ntdll")]
    private static extern int NtSuspendProcess(IntPtr h);

    [DllImport("ntdll")]
    private static extern int NtResumeProcess(IntPtr h);

    [DllImport("kernel32", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr h);
}
