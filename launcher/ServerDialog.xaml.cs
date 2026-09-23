using System.Windows;

namespace TL2LobbyLauncher;

public partial class ServerDialog : Window
{
    private readonly ServerEntry? _original;
    private readonly IReadOnlyList<ServerEntry> _all;

    public ServerEntry Result { get; private set; } = new();

    public ServerDialog(ServerEntry? original, IReadOnlyList<ServerEntry> all)
    {
        InitializeComponent();
        _original = original;
        _all = all;
        Title = Loc.T(original == null ? "Dlg_Add" : "Dlg_Edit");
        lblName.Text = Loc.T("Dlg_Name");
        lblHost.Text = Loc.T("Dlg_Host");
        lblPort.Text = Loc.T("Dlg_Port");
        lblAuth.Text = Loc.T("Dlg_Auth");
        txtAuthHint.Text = Loc.T("Dlg_AuthHint");
        btnOk.Content = Loc.T("Dlg_Ok");
        btnCancel.Content = Loc.T("Dlg_Cancel");

        txtName.Text = original?.Name ?? "";
        txtHost.Text = original?.Host ?? "";
        txtPort.Text = (original?.Port ?? LobbySettings.DefaultPort).ToString();
        txtAuth.Text = original?.AuthUrl ?? AppConfig.DefaultAuthUrl;
        Loaded += (_, _) => txtName.Focus();
    }

    private void OnOk(object sender, RoutedEventArgs e)
    {
        var s = new ServerEntry
        {
            Name = txtName.Text.Trim(),
            Host = txtHost.Text.Trim(),
            Port = int.TryParse(txtPort.Text.Trim(), out int p) ? p : 0,
            AuthUrl = txtAuth.Text.Trim().TrimEnd('/'),
        };
        string? err = ServerValidation.Validate(s);
        if (err == null && _all.Any(x => !ReferenceEquals(x, _original)
                && string.Equals(x.Host, s.Host, StringComparison.OrdinalIgnoreCase)))
            err = "Err_Duplicate";
        if (err != null)
        {
            txtError.Text = Loc.T(err);
            return;
        }
        Result = s;
        DialogResult = true;
    }
}
