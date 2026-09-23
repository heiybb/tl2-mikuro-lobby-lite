using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace TL2LobbyLauncher;

/// <summary>
/// Paints the system title bar in the window's own background color so it blends into the
/// page. Dark mode (DWMWA_USE_IMMERSIVE_DARK_MODE) gives the dark caption buttons on
/// Windows 10 1809+ and 11; the exact caption/text colors need Windows 11 (22000+) and are
/// silently ignored before that.
/// </summary>
public static class TitleBar
{
    private const int CaptionRgb = 0x16130F;   // App.xaml "Bg"
    private const int TextRgb = 0xECE5DC;      // App.xaml "Fg"

    private const int DWMWA_USE_IMMERSIVE_DARK_MODE = 20, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19;
    private const int DWMWA_BORDER_COLOR = 34, DWMWA_CAPTION_COLOR = 35, DWMWA_TEXT_COLOR = 36;

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(nint hwnd, int attr, ref int value, int size);

    /// <summary>Call from the window's constructor; applied once the native handle exists.</summary>
    public static void Attach(Window w) => w.SourceInitialized += (_, _) => Apply(w);

    private static void Apply(Window w)
    {
        nint h = new WindowInteropHelper(w).Handle;
        if (h == 0) return;
        int on = 1;
        if (DwmSetWindowAttribute(h, DWMWA_USE_IMMERSIVE_DARK_MODE, ref on, sizeof(int)) != 0)
            DwmSetWindowAttribute(h, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, ref on, sizeof(int));
        int cap = ColorRef(CaptionRgb), txt = ColorRef(TextRgb);
        DwmSetWindowAttribute(h, DWMWA_CAPTION_COLOR, ref cap, sizeof(int));
        DwmSetWindowAttribute(h, DWMWA_BORDER_COLOR, ref cap, sizeof(int));
        DwmSetWindowAttribute(h, DWMWA_TEXT_COLOR, ref txt, sizeof(int));
    }

    /// <summary>0xRRGGBB → Win32 COLORREF (0x00BBGGRR).</summary>
    private static int ColorRef(int rgb) => ((rgb & 0xFF) << 16) | (rgb & 0xFF00) | ((rgb >> 16) & 0xFF);
}
