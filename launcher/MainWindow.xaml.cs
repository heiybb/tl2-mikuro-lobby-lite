using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Win32;

namespace TL2LobbyLauncher;

public partial class MainWindow : Window
{
    /// <summary>One row of the server list; Server == null is the "official / don't change" row.</summary>
    private sealed record Row(ServerEntry? Server, string Title, string Detail);

    private readonly AppConfig _cfg = AppConfig.Load();
    private string? _exe;
    private GameBuild _build = GameBuild.Unknown;
    private readonly ObservableCollection<ModItem> _mods = new();

    public MainWindow()
    {
        InitializeComponent();
        TitleBar.Attach(this);
        Title = txtTitle.Text = Loc.T("Title");
        lblGame.Text = Loc.T("Game");
        lblServer.Text = Loc.T("Server");
        btnBrowse.Content = Loc.T("Browse");
        btnAdd.Content = Loc.T("Add");
        btnEdit.Content = Loc.T("Edit");
        btnRemove.Content = Loc.T("Remove");
        btnApply.Content = Loc.T("ApplyOnly");
        btnLaunch.Content = Loc.T("Launch");
        lblMods.Text = Loc.T("Mods");
        btnModsRefresh.Content = Loc.T("Mods_Refresh");
        btnModsFolder.Content = Loc.T("Mods_Folder");
        btnUp.ToolTip = Loc.T("Mods_Up");
        btnDown.ToolTip = Loc.T("Mods_Down");
        chkOver10.Content = Loc.T("Mods_Over10");
        chkOver10.ToolTip = Loc.T("Mods_Over10Tip");
        chkOver10.IsChecked = _cfg.AllowOver10;
        txtStatus.Text = Loc.T("Hint");

        PakPatch.RemoveLegacyState();
        SetGame(GameInfo.FindExe(_cfg.GameDir));
        RefreshList(_cfg.SelectedHost);
        lstMods.ItemsSource = _mods;
        LoadMods();
    }

    // ---- mods ----
    private void LoadMods()
    {
        _mods.Clear();
        foreach (var m in ModCatalog.Load(MaxEnabled)) _mods.Add(m);
        txtNoMods.Text = _mods.Count == 0 ? Loc.F("Mods_None", SchemeFile.ModsDir) : "";
        UpdateModUi();
    }

    private int EnabledCount => _mods.Count(m => m.IsEnabled);
    private int MaxEnabled => _cfg.AllowOver10 ? ModCatalog.RaisedMax : ModCatalog.StockMax;

    private void OnOver10Toggled(object sender, RoutedEventArgs e)
    {
        bool on = chkOver10.IsChecked == true;
        if (!on && EnabledCount > ModCatalog.StockMax)
        {
            chkOver10.IsChecked = true;
            MessageBox.Show(this, Loc.F("Mods_Over10Off", ModCatalog.StockMax), Title, MessageBoxButton.OK,
                MessageBoxImage.Information);
            return;
        }
        _cfg.AllowOver10 = on;
        _cfg.Save();
        UpdateModUi();
    }

    private void UpdateModUi()
    {
        txtModCount.Text = Loc.F("Mods_Count", EnabledCount, MaxEnabled);
        int i = lstMods.SelectedIndex;
        btnUp.IsEnabled = i > 0;
        btnDown.IsEnabled = i >= 0 && i < _mods.Count - 1;
    }

    private void OnModToggled(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement { DataContext: ModItem m } && m.IsEnabled && EnabledCount > MaxEnabled)
        {
            m.IsEnabled = false;
            MessageBox.Show(this, Loc.F(_cfg.AllowOver10 ? "Mods_CapRaised" : "Mods_Cap", MaxEnabled), Title,
                MessageBoxButton.OK, MessageBoxImage.Information);
        }
        UpdateModUi();
    }

    private void OnModSelectionChanged(object sender, SelectionChangedEventArgs e) => UpdateModUi();

    private void MoveMod(int delta)
    {
        int i = lstMods.SelectedIndex, j = i + delta;
        if (i < 0 || j < 0 || j >= _mods.Count) return;
        _mods.Move(i, j);
        lstMods.SelectedIndex = j;
        lstMods.ScrollIntoView(_mods[j]);
    }

    private void OnModUp(object sender, RoutedEventArgs e) => MoveMod(-1);
    private void OnModDown(object sender, RoutedEventArgs e) => MoveMod(+1);
    private void OnModsRefresh(object sender, RoutedEventArgs e) => LoadMods();

    private void OnModsFolder(object sender, RoutedEventArgs e)
    {
        try
        {
            Directory.CreateDirectory(SchemeFile.ModsDir);
            Process.Start(new ProcessStartInfo { FileName = SchemeFile.ModsDir, UseShellExecute = true })?.Dispose();
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, ex.Message, Title, MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    /// <summary>Enabled mods in list order = load order.</summary>
    private List<long> EnabledGuids() => _mods.Where(m => m.IsEnabled && m.Info.Valid).Select(m => m.Info.Guid).ToList();

    // ---- game ----
    private void SetGame(string? exe)
    {
        _exe = exe;
        // A launcher that died while the game ran leaves DATA.PAK patched; undo that first.
        if (exe != null) PakPatch.RestoreIfNeeded(Path.GetDirectoryName(exe)!);
        _build = exe != null ? GameInfo.Build(exe) : GameBuild.Unknown;
        txtGame.Text = exe ?? "";
        txtBuild.Text = exe == null ? Loc.T("NoGame") : _build switch
        {
            GameBuild.Steam126 => Loc.F("Build_Steam126", GameInfo.Version(exe)),
            GameBuild.Steam125 => Loc.F("Build_Steam125", GameInfo.Version(exe)),
            GameBuild.DrmFree => Loc.F("Build_DrmFree", GameInfo.Version(exe)),
            _ => Loc.T("Build_Unknown"),
        };
        btnLaunch.IsEnabled = exe != null;
    }

    private void OnBrowse(object sender, RoutedEventArgs e)
    {
        string? folder = PickFolder();
        if (folder == null) return;
        string exe = Path.Combine(folder, GameInfo.ExeName);
        if (!File.Exists(exe))
        {
            MessageBox.Show(this, Loc.T("NotAGameDir"), Title, MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        _cfg.GameDir = folder;
        _cfg.Save();
        SetGame(exe);
    }

    /// <summary>WPF's own folder picker exists from .NET 8 on; .NET Framework uses WinForms'.</summary>
    private string? PickFolder()
    {
#if NETFRAMEWORK
        using var dlg = new System.Windows.Forms.FolderBrowserDialog
        {
            Description = Loc.T("PickFolder"),
            ShowNewFolderButton = false,
        };
        var owner = new System.Windows.Interop.WindowInteropHelper(this).Handle;
        return dlg.ShowDialog(new Win32Owner(owner)) == System.Windows.Forms.DialogResult.OK ? dlg.SelectedPath : null;
#else
        var dlg = new OpenFolderDialog { Title = Loc.T("PickFolder") };
        return dlg.ShowDialog(this) == true ? dlg.FolderName : null;
#endif
    }

#if NETFRAMEWORK
    private sealed class Win32Owner(IntPtr handle) : System.Windows.Forms.IWin32Window
    {
        public IntPtr Handle { get; } = handle;
    }
#endif

    // ---- server list ----
    private void RefreshList(string? selectHost)
    {
        var rows = new List<Row> { new(null, Loc.T("Official"), Loc.T("OfficialDetail")) };
        foreach (var s in _cfg.Servers)
        {
            string detail = s.Port == LobbySettings.DefaultPort ? s.Host : $"{s.Host}:{s.Port}";
            if (!string.IsNullOrEmpty(s.AuthUrl)) detail += "   ·   " + s.AuthUrl;
            rows.Add(new Row(s, s.Name, detail));
        }
        lstServers.ItemsSource = rows;
        lstServers.SelectedItem = rows.FirstOrDefault(r =>
            r.Server != null && string.Equals(r.Server.Host, selectHost, StringComparison.OrdinalIgnoreCase)) ?? rows[0];
        UpdateButtons();
    }

    private ServerEntry? Selected => (lstServers.SelectedItem as Row)?.Server;

    private void OnSelectionChanged(object sender, SelectionChangedEventArgs e) => UpdateButtons();

    private void UpdateButtons() => btnEdit.IsEnabled = btnRemove.IsEnabled = Selected != null;

    private void OnAdd(object sender, RoutedEventArgs e)
    {
        var dlg = new ServerDialog(null, _cfg.Servers) { Owner = this };
        if (dlg.ShowDialog() != true) return;
        _cfg.Servers.Add(dlg.Result);
        _cfg.Save();
        RefreshList(dlg.Result.Host);
    }

    private void OnEdit(object sender, RoutedEventArgs e)
    {
        var s = Selected;
        if (s == null) return;
        var dlg = new ServerDialog(s, _cfg.Servers) { Owner = this };
        if (dlg.ShowDialog() != true) return;
        _cfg.Servers[_cfg.Servers.IndexOf(s)] = dlg.Result;
        _cfg.Save();
        RefreshList(dlg.Result.Host);
    }

    private void OnRemove(object sender, RoutedEventArgs e)
    {
        var s = Selected;
        if (s == null) return;
        if (MessageBox.Show(this, Loc.F("ConfirmRemove", s.Name), Title, MessageBoxButton.YesNo,
                MessageBoxImage.Question) != MessageBoxResult.Yes) return;
        _cfg.Servers.Remove(s);
        _cfg.Save();
        RefreshList(null);
    }

    // ---- apply / launch ----
    private bool Apply()
    {
        var s = Selected;
        try
        {
            // Hosts this launcher knows about: only those are removed when "official" is picked.
            // The last applied host is included in case that server was edited or removed since.
            var known = _cfg.Servers.Select(x => x.Host).ToList();
            if (!string.IsNullOrEmpty(_cfg.SelectedHost)) known.Add(_cfg.SelectedHost);
            LobbySettings.Apply(s, known);
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, Loc.F("ApplyFailed", LobbySettings.FilePath, ex.Message), Title,
                MessageBoxButton.OK, MessageBoxImage.Error);
            return false;
        }
        // Only touch modlauncher.sch when there are mods to manage: a player with an empty mods
        // folder keeps whatever file another launcher wrote.
        if (_mods.Count > 0)
        {
            try { SchemeFile.Write(EnabledGuids()); }
            catch (Exception ex)
            {
                MessageBox.Show(this, Loc.F("ApplyFailed", SchemeFile.DefaultPath, ex.Message), Title,
                    MessageBoxButton.OK, MessageBoxImage.Error);
                return false;
            }
        }
        _cfg.SelectedHost = s?.Host;
        _cfg.Save();
        txtStatus.Text = (s == null ? Loc.T("AppliedOfficial") : Loc.F("Applied", s.Display))
            + "  " + Loc.F("AppliedMods", EnabledGuids().Count);
        return true;
    }

    private void OnApply(object sender, RoutedEventArgs e) => Apply();

    private async void OnLaunch(object sender, RoutedEventArgs e)
    {
        if (_exe == null || !Apply()) return;
        var s = Selected;

        if (GameInfo.IsSteam(_build))
        {
            try { GameInfo.EnsureSteamAppId(Path.GetDirectoryName(_exe)!); }
            catch (Exception ex)
            {
                MessageBox.Show(this, Loc.F("AppIdFailed", ex.Message), Title, MessageBoxButton.OK, MessageBoxImage.Warning);
            }
            if (!GameInfo.IsSteamRunning() && !Ask("SteamNotRunning")) return;
        }

        // 1.26 only: send the TapTap login to the server's own auth Worker
        string? authUrl = null;
        if (_build == GameBuild.Steam126 && s != null)
        {
            if (!string.IsNullOrEmpty(s.AuthUrl)) authUrl = s.AuthUrl;
            else if (!Ask("NoAuthUrl")) return;
        }

        // With no mods enabled the game starts plain; otherwise it loads modlauncher.sch.
        int modCount = EnabledGuids().Count;
        string args = modCount > 0 ? SchemeFile.LaunchArg : "";
        // Patch the limit only when it matters: more than 10 mods ticked.
        bool raiseCap = _cfg.AllowOver10 && modCount > ModCatalog.StockMax;
        bool packed = _build == GameBuild.Steam126;
        string exe = _exe;

        btnLaunch.IsEnabled = btnApply.IsEnabled = false;
        txtStatus.Text = Loc.T("Launching");

        // 1.26 with our server: our login notice and a "Server: host" line, for this run only.
        // Any other launch (the official lobby, other builds) must see the stock DATA.PAK: a
        // patch left by an earlier run would show our notice over the real TapTap login.
        bool pakPatched = false;
        string gameDir = Path.GetDirectoryName(exe)!;
        if (_build == GameBuild.Steam126 && s != null)
        {
            string host = s.Port == LobbySettings.DefaultPort ? s.Host : $"{s.Host}:{s.Port}";
            var p = await Task.Run(() => PakPatch.Apply(gameDir, host));
            pakPatched = p.Patched > 0;
            if (p.Error != null)
                MessageBox.Show(this, Loc.F("PakPatchFailed", p.Error), Title, MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        else
        {
            string? err = await Task.Run(() => PakPatch.RestoreIfNeeded(gameDir));
            if ((err != null || PakPatch.IsPatched(gameDir))
                && MessageBox.Show(this, Loc.F("PakStillPatched", err ?? Loc.T("PakGameRunning")), Title,
                       MessageBoxButton.YesNo, MessageBoxImage.Warning, MessageBoxResult.No) != MessageBoxResult.Yes)
            {
                btnLaunch.IsEnabled = btnApply.IsEnabled = true;
                txtStatus.Text = Loc.T("Hint");
                return;
            }
        }

        var r = await Task.Run(() => GameLauncher.Start(exe, args, authUrl, raiseCap, packed));
        btnLaunch.IsEnabled = btnApply.IsEnabled = true;
        if (!r.Started)
        {
            if (pakPatched) PakPatch.RestoreIfNeeded(gameDir);
            MessageBox.Show(this, Loc.F("LaunchFailed", r.Error ?? ""), Title, MessageBoxButton.OK, MessageBoxImage.Error);
            return;
        }
        if (authUrl != null && r.UrlsReplaced == 0)
            MessageBox.Show(this, Loc.T("AuthNotPatched"), Title, MessageBoxButton.OK, MessageBoxImage.Warning);
        if (r.Cap == GameLauncher.CapResult.NotFound)
            MessageBox.Show(this, Loc.F("CapNotPatched", ModCatalog.StockMax), Title, MessageBoxButton.OK, MessageBoxImage.Warning);
        if (pakPatched)
        {
            // Stay alive, hidden, so the originals go back when the game exits: a later launch
            // straight from Steam must see the real TapTap notice.
            Hide();
            await WaitForGameExit(r.ProcessId, Path.GetDirectoryName(exe)!);
            await Task.Run(() => PakPatch.RestoreIfNeeded(Path.GetDirectoryName(exe)!));
        }
        Close();
    }

    private static async Task WaitForGameExit(int pid, string gameDir)
    {
        try
        {
            using var p = Process.GetProcessById(pid);
            await Task.Run(() => p.WaitForExit());
        }
        catch { /* already gone */ }
        // If the exe hands off to a second copy of itself (a Steam restart), wait for that too.
        while (PakPatch.GameRunning(gameDir)) await Task.Delay(2000);
    }

    private bool Ask(string key) =>
        MessageBox.Show(this, Loc.T(key), Title, MessageBoxButton.YesNo, MessageBoxImage.Warning,
            MessageBoxResult.No) == MessageBoxResult.Yes;
}
