using System.Globalization;

namespace TL2LobbyLauncher;

/// <summary>English, plus Simplified Chinese when the Windows UI language is Chinese.</summary>
public static class Loc
{
    private static readonly bool Zh = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "zh";

    public static string T(string key) =>
        (Zh ? ZhStrings : EnStrings).TryGetValue(key, out var s) ? s : EnStrings.GetValueOrDefault(key, key);

    public static string F(string key, params object[] args) => string.Format(T(key), args);

    private static readonly Dictionary<string, string> EnStrings = new()
    {
        ["Title"] = "TL2 Lobby Launcher",
        ["Game"] = "GAME",
        ["Browse"] = "Browse…",
        ["NoGame"] = "Torchlight2.exe not found. Click Browse… and pick the game folder.",
        ["Build_DrmFree"] = "Version {0} (DRM-free)",
        ["Build_Steam125"] = "Version {0} (Steam)",
        ["Build_Steam126"] = "Version {0} (Steam, TapTap login: needs an Auth URL)",
        ["Build_Unknown"] = "Unknown version",
        ["Server"] = "LOBBY SERVER",
        ["Official"] = "Official / don't change",
        ["OfficialDetail"] = "Removes this launcher's lobby lines; the game uses its built-in lobby",
        ["Add"] = "Add",
        ["Edit"] = "Edit",
        ["Remove"] = "Remove",
        ["ApplyOnly"] = "Apply only",
        ["Launch"] = "Launch game",
        ["Hint"] = "Launching from here also works around Steam Cloud restoring an old local_settings.txt.",
        ["Applied"] = "Saved. The game will connect to {0}.",
        ["AppliedOfficial"] = "Saved. Our lobby lines were removed from local_settings.txt.",
        ["ApplyFailed"] = "Could not write {0}:\n{1}",
        ["PickFolder"] = "Pick the folder that contains Torchlight2.exe",
        ["NotAGameDir"] = "That folder has no Torchlight2.exe.",
        ["SteamNotRunning"] = "Steam is not running. The Steam version of the game will not start without it.\n\nLaunch anyway?",
        ["AppIdFailed"] = "Could not write steam_appid.txt in the game folder ({0}). If the game refuses to start, run this launcher once as administrator.",
        ["NoAuthUrl"] = "This server has no Auth URL. Version 1.26 logs in through TapTap and will not reach this lobby without one.\n\nLaunch anyway?",
        ["AuthNotPatched"] = "The game started, but the TapTap login URLs were not found in memory (unsupported game version?). Online login will probably fail.",
        ["LaunchFailed"] = "Could not start the game:\n{0}",
        ["ConfirmRemove"] = "Remove \"{0}\"?",
        ["Dlg_Add"] = "Add server",
        ["Dlg_Edit"] = "Edit server",
        ["Dlg_Name"] = "Name",
        ["Dlg_Host"] = "Host (domain or IPv4)",
        ["Dlg_Port"] = "Port",
        ["Dlg_Auth"] = "Auth URL (only for version 1.26; optional)",
        ["Dlg_AuthHint"] = "The default shared one works with any server. Your own: https://<domain>, at most 26 characters",
        ["Dlg_Ok"] = "OK",
        ["Dlg_Cancel"] = "Cancel",
        ["Err_Name"] = "Enter a name.",
        ["Err_Host"] = "The host must be a domain name or an IPv4 address.",
        ["Err_Port"] = "The port must be 1-65535.",
        ["Err_AuthUrl"] = "The Auth URL must be https://<domain> with no path, at most 26 characters.",
        ["Err_Duplicate"] = "A server with this host already exists.",
    };

    private static readonly Dictionary<string, string> ZhStrings = new()
    {
        ["Title"] = "TL2 联机大厅启动器",
        ["Game"] = "游戏",
        ["Browse"] = "浏览…",
        ["NoGame"] = "没找到 Torchlight2.exe。点「浏览…」选择游戏目录。",
        ["Build_DrmFree"] = "版本 {0}(无 DRM 版)",
        ["Build_Steam125"] = "版本 {0}(Steam)",
        ["Build_Steam126"] = "版本 {0}(Steam,TapTap 登录:需要认证地址)",
        ["Build_Unknown"] = "未知版本",
        ["Server"] = "联机大厅服务器",
        ["Official"] = "官方 / 不修改",
        ["OfficialDetail"] = "移除本启动器写入的大厅设置,游戏使用内置大厅",
        ["Add"] = "添加",
        ["Edit"] = "编辑",
        ["Remove"] = "删除",
        ["ApplyOnly"] = "仅保存",
        ["Launch"] = "启动游戏",
        ["Hint"] = "从这里启动还能避开 Steam 云同步把 local_settings.txt 还原成旧版本。",
        ["Applied"] = "已保存。游戏将连接 {0}。",
        ["AppliedOfficial"] = "已保存。已从 local_settings.txt 移除本启动器写入的大厅设置。",
        ["ApplyFailed"] = "无法写入 {0}:\n{1}",
        ["PickFolder"] = "选择包含 Torchlight2.exe 的文件夹",
        ["NotAGameDir"] = "该文件夹里没有 Torchlight2.exe。",
        ["SteamNotRunning"] = "Steam 没有运行。Steam 版游戏没有 Steam 无法启动。\n\n仍然启动?",
        ["AppIdFailed"] = "无法在游戏目录写入 steam_appid.txt({0})。如果游戏启动不了,请以管理员身份运行一次本启动器。",
        ["NoAuthUrl"] = "这个服务器没有填认证地址。1.26 版通过 TapTap 登录,没有认证地址就连不上这个大厅。\n\n仍然启动?",
        ["AuthNotPatched"] = "游戏已启动,但内存里没找到 TapTap 登录地址(游戏版本不支持?)。联机登录大概率会失败。",
        ["LaunchFailed"] = "无法启动游戏:\n{0}",
        ["ConfirmRemove"] = "删除「{0}」?",
        ["Dlg_Add"] = "添加服务器",
        ["Dlg_Edit"] = "编辑服务器",
        ["Dlg_Name"] = "名称",
        ["Dlg_Host"] = "地址(域名或 IPv4)",
        ["Dlg_Port"] = "端口",
        ["Dlg_Auth"] = "认证地址(仅 1.26 版需要,可留空)",
        ["Dlg_AuthHint"] = "默认的公共认证地址适用于任何服务器;自建的格式为 https://<域名>,最多 26 个字符",
        ["Dlg_Ok"] = "确定",
        ["Dlg_Cancel"] = "取消",
        ["Err_Name"] = "请填写名称。",
        ["Err_Host"] = "地址必须是域名或 IPv4 地址。",
        ["Err_Port"] = "端口必须在 1-65535 之间。",
        ["Err_AuthUrl"] = "认证地址必须是 https://<域名>,不带路径,最多 26 个字符。",
        ["Err_Duplicate"] = "已有相同地址的服务器。",
    };
}
