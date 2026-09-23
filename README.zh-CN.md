# TL2 Mikuro Lobby Lite

**火炬之光 2(Torchlight II)** 的自建联机大厅。找一台有公网 IPv4 的机器跑起来,
把朋友的游戏指过去,就能像当年官方大厅那样建房、浏览、加入联机。不改游戏 exe。

[English](README.md)

这是精简的自部署版:没有账号系统,没有统计,不上报任何信息。服务器只在内存里记
「谁在线、有哪些房间」,别的都不存。

| 目录 | 内容 |
|---|---|
| [`server/`](server) | 大厅 + UDP 中继。Python 3.9+,只用标准库 |
| [`tap-auth/`](tap-auth) | Cloudflare Worker,**只有 Steam 1.26.0.1 版需要**(这一版登录改走 TapTap OAuth) |
| [`launcher/`](launcher) | Windows(WPF)小启动器:选服务器、选 MOD(可突破 10 个上限)、启动游戏 |

已测试版本:无 DRM 版 1.25.9.5、Steam 1.25.5.6、Steam 1.26.0.1。

## 下载

[Releases](../../releases) 页面有编译好的文件:

- `TL2LobbyLauncher.exe`:启动器,自带运行时,下载即用
- `TL2LobbyLauncher-net10-runtime.exe`:同一个启动器,体积小很多,需要先装
  [.NET 10 桌面运行时](https://dotnet.microsoft.com/download/dotnet/10.0)
- `tl2-lobby-lite-server.zip`:服务器文件

## 原理

游戏联机时先连一个中心大厅服务器:登录、拉房间列表、请大厅撮合。真正的对局数据在
玩家之间点对点传。大厅地址就是设置文件里的一项(`local_settings.txt` 的 `LOBBYHOST`),
客户端也不校验服务端身份,所以协议兼容的服务器就能接管。

多数家用网络下点对点直连打不通,所以本服务器顺带用 UDP 中继游戏流量。每一对玩家
独占一个 UDP 端口。TL2 联机是全网状的,每个玩家都要和其他所有玩家互连,所以 N 人局
要占 N×(N−1)/2 个端口,对局流量也全部经过你的服务器。

## 架服务器

需要一台有公网 IPv4 的机器,放行:

- TCP **4549**(大厅)
- UDP **4549**(中继主端口)
- UDP **4550–4999**(配对端口,默认段)

云服务器记得在厂商的安全组/防火墙里也放行。

```bash
git clone <本仓库> && cd tl2-mikuro-lobby-lite/server
cp .env.example .env
nano .env                   # RELAY_IP 填这台机器的公网 IP
```

然后三选一:

```bash
sudo ./deploy/install.sh               # 装成 systemd 服务,并在 ufw/firewalld/iptables 放行端口
docker compose up -d --build           # Docker,host 网络
python3 lobby_server.py                # 直接前台跑
```

在另一台电脑上验证:`python test_client.py --host <服务器>`,打印 `[OK]` 就对了。

给服务器配域名并用 Cloudflare DNS 的话,记录必须是**仅 DNS(灰云)**。代理只转 HTTP,
游戏的裸 TCP/UDP 会被吃掉。

### 配置项(`server/.env`)

| 键 | 默认 | 含义 |
|---|---|---|
| `RELAY_IP` | `auto` | 告诉玩家往哪发中继流量,填公网 IP。`public` = 启动时自动查(服务器唯一可能发出的外部请求) |
| `LOBBY_PORT` / `RELAY_PORT` | `4549` | 大厅 TCP 端口 / 中继 UDP 主端口 |
| `RELAY_PORT_RANGE` | `4550-4999` | 配对端口段;4 人局占 6 个,6 人局占 15 个 |
| `LOBBY_PASSWORD` | 空 | 可选的服务器共享密码,对 1.25.x 客户端生效(任意用户名 + 这个密码) |
| `ALLOW_TAPTAP` | `1` | 是否放行 1.26(TapTap 登录)客户端。它们带不了密码 |
| `LOG_IPS` | `0` | `0` = 日志里玩家 IP 打码成 `a.b.x.x` |
| `LOBBY_VERBOSE` | `0` | 打印每个大厅帧的 hexdump(调协议用) |

### 支持 Steam 1.26.0.1

1.26.0.1 更新把用户名密码登录换成了 TapTap OAuth,这一版的玩家需要在启动器里填「认证地址」。
认证端点只负责发一个玩家名,跟具体哪台大厅无关。启动器给每个新服务器默认填好公共的
`https://tl2-auth.chr.moe`,所以自建大厅不用额外配置就能让 1.26 玩家进来。

不想依赖它的话,把 [`tap-auth/`](tap-auth) 部署到你自己的 Cloudflare 账号,让玩家改用你的地址。
地址最多 26 个字符(`https://` 加最多 18 个字符的域名),原因见 tap-auth 的 README。

## 玩家怎么连

**用启动器**(`launcher/`):添加服务器地址,选中,勾选要用的 MOD,点「启动游戏」。1.26.0.1 必须经启动器启动。

启动器自带作者运营的两台公共服务器:**Mikuro Australia** 和 **Mikuro US**。它们跑的是完整版大厅,
会记录用于服务器管理的统计(在线列表、流量、登录日志),不是这个精简版。介意的话删掉它们,用你自己的服务器。

**手动改**(仅 1.25.x):完全退出游戏,用记事本打开
`文档\My Games\Runic Games\Torchlight 2\local_settings.txt`,改成:

```
LOBBYHOST :你的服务器地址
LOBBYPORT :4549
```

玩家这边不用开任何端口。

## 从源码编译

```bash
# 服务器:无需编译;跑测试
cd server && python run_tests.py

# 启动器(Windows,.NET 10 SDK)
dotnet publish launcher -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o publish
# -> publish/TL2LobbyLauncher.exe。换成 --self-contained false 得到依赖运行时的小体积版

# tap-auth:见 tap-auth/README.md(bun install && bun run deploy)
```

推送 `v*` 标签会触发 `.github/workflows/release.yml`:测试服务器、编译两种启动器、发布 GitHub Release。

## 隐私与安全

- **服务器**:用户名、IP、房间列表只在内存里,不写任何文件;日志只输出到 stdout /
  systemd journal,IP 默认打码;除非设了 `RELAY_IP=public`,不发起任何外部连接。
- **tap-auth**:本仓库里的版本没有数据库,不记日志(`wrangler.jsonc` 里关了 Workers observability),
  玩家名存在玩家自己浏览器的 cookie 里。公共实例 `tl2-auth.chr.moe` 由作者运营;想完全自己掌控就自己部署一份。
- **启动器**:只改 `local_settings.txt` 和 `modlauncher.sch`,Steam 版写一个 `steam_appid.txt`,
  自己的设置放在 `%APPDATA%\TL2LobbyLauncher`。
  1.26 连自建服务器时,还会在这一局游戏期间替换 `PAKS\DATA.PAK` 里的几项(TapTap 登录公告和它的
  中文翻译,以及游戏列表里的"Server: 主机名"一行)。游戏会用一个包含文件长度的抽样哈希校验
  `DATA.PAK`,所以新内容只在文件内部原地写入、从不追加。被覆盖的字节备份在
  `PAKS\TL2LobbyLauncher-restore\`,游戏退出后两个文件逐字节还原,这个文件夹也随之删除。
  游戏关着却还能看到它的话,打开一次启动器即可(或用 Steam 的"验证游戏文件的完整性")。
- 大厅协议是**明文 TCP**,这是游戏本身决定的。`LOBBY_PASSWORD` 走客户端自带的挑战应答,
  密码本身不上线路;但这条哈希链很弱(20 轮 SHA-256),能抓到流量的人可以离线爆破短密码。
  请用足够长的随机串,把它当成「挡陌生人」,别当强认证。
- tap-auth **不是**准入门槛:1.26 客户端报什么名字,大厅就收什么名字。在意的话用
  `ALLOW_TAPTAP=0` 关掉 1.26 客户端。
- 中继 key 是随机 32 位数;网状撮合只在同一局内的玩家之间进行。

## 局限

- 只支持 IPv4。
- 服务器是中继,不是权威主机:局内一切仍由房主的游戏裁决,和官方大厅时一样。
- 好友等社交消息收下但不处理。

以 [MIT 许可证](LICENSE) 发布。

非官方项目,与 Runic Games 及火炬之光 2 的发行方无关。不包含任何游戏文件。
