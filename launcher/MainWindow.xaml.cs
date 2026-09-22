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

    public MainWindow()
    {
        InitializeComponent();
        Title = txtTitle.Text = Loc.T("Title");
        lblGame.Text = Loc.T("Game");
        lblServer.Text = Loc.T("Server");
        btnBrowse.Content = Loc.T("Browse");
        btnAdd.Content = Loc.T("Add");
        btnEdit.Content = Loc.T("Edit");
        btnRemove.Content = Loc.T("Remove");
        btnApply.Content = Loc.T("ApplyOnly");
        btnLaunch.Content = Loc.T("Launch");
        txtStatus.Text = Loc.T("Hint");

        SetGame(GameInfo.FindExe(_cfg.GameDir));
        RefreshList(_cfg.SelectedHost);
    }

    // ---- game ----
    private void SetGame(string? exe)
    {
        _exe = exe;
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
        var dlg = new OpenFolderDialog { Title = Loc.T("PickFolder") };
        if (dlg.ShowDialog(this) != true) return;
        string exe = Path.Combine(dlg.FolderName, GameInfo.ExeName);
        if (!File.Exists(exe))
        {
            MessageBox.Show(this, Loc.T("NotAGameDir"), Title, MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        _cfg.GameDir = dlg.FolderName;
        _cfg.Save();
        SetGame(exe);
    }

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
        _cfg.SelectedHost = s?.Host;
        _cfg.Save();
        txtStatus.Text = s == null ? Loc.T("AppliedOfficial") : Loc.F("Applied", s.Display);
        return true;
    }

    private void OnApply(object sender, RoutedEventArgs e) => Apply();

    private void OnLaunch(object sender, RoutedEventArgs e)
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

        var r = GameLauncher.Start(_exe, authUrl);
        if (!r.Started)
        {
            MessageBox.Show(this, Loc.F("LaunchFailed", r.Error ?? ""), Title, MessageBoxButton.OK, MessageBoxImage.Error);
            return;
        }
        if (authUrl != null && r.UrlsReplaced == 0)
            MessageBox.Show(this, Loc.T("AuthNotPatched"), Title, MessageBoxButton.OK, MessageBoxImage.Warning);
        Close();
    }

    private bool Ask(string key) =>
        MessageBox.Show(this, Loc.T(key), Title, MessageBoxButton.YesNo, MessageBoxImage.Warning,
            MessageBoxResult.No) == MessageBoxResult.Yes;
}
