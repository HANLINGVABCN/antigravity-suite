# Antigravity Suite

[![CI](https://github.com/HANLINGVABCN/antigravity-suite/actions/workflows/ci.yml/badge.svg)](https://github.com/HANLINGVABCN/antigravity-suite/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](requirements.txt)

Linux VPS 上的 Antigravity 账号配置管理、Web 反代与共享聊天记录套件。

**所有账号保留各自存档，并显示全部本地聊天记录。发现唯一可用的原配置后，桌面和网页使用同一份当前运行配置。** 支持管理员门禁、悬浮控制面板、账号检测、代理链接导入及本体安装／卸载。

这是独立社区项目，与 Google 无隶属关系。当前版本为 **v0.9.6**，上游兼容性仍可能变化。

## 功能

- 同一安装脚本支持 systemd 和 Debian/Ubuntu 容器，自动复用已有 Supervisor 或安装独立 Supervisor。终端向导可选端口、管理员用户名、自定义或随机密码；升级保留已有设置。网页可选开启验证器两步验证。
- HTTP 流式转发、gRPC-Web 和 WebSocket 支持；可搭配 HTTPS 反代或 SSH 转发。
- 已识别的本地状态、聊天列表等长期订阅使用鉴权 WebSocket 传输，避免 HTTP/1.1 六个连接被占满后普通请求一直排队；初始化等待状态、聊天列表与账号响应后才开放页面，未就绪时明确提示。
- 原生网页主脚本保留退出保护并使用 gzip 压缩，重复进入通过管理员鉴权后的私有 ETag 校验复用浏览器内容；转换和压缩在工作线程执行，减少传输体积和后台阻塞。
- 部署时读取已有反重力登录，提取为普通账号配置，名称为“默认配置-机器名”；已有相同账号时复用原卡片，未登录的新部署列表为空。运行目录只在后台管理，不特殊显示。
- 每次完成新的登录都保存一个独立配置，即使邮箱相同也生成新 ID；普通凭据续期、重启和载入旧配置不重复存档。后台每 3 秒检查一次稳定的登录变化，只更新当前会话，不覆盖其他存档。
- 当前选中的配置卡片及悬浮面板对应行提供“退出配置”：保存存档、关闭当前引擎、清理工作登录，再打开未登录界面。原生退出在处理按钮及认证方法前被阻止，提示“确定”只关闭提示，详见 [登录存档与退出](docs/login-sessions.md)。
- 发现唯一可用的原配置时，切换账号会保存上一账号并载入所选账号到原运行目录，桌面和网页读同一份当前登录；没有原配置时使用独立的工作环境，登录存档仍各自保存，所有聊天记录继续共享。
- 网页“更新项目”可检查 GitHub 正式版并自动更新，显示进度，更新前备份并保留账号和节点，新版启动失败时恢复原源码和运行环境。
- 导入可选择本工具、CPA 或 Sub2API；第三方账号 JSON 转换为本工具配置，不导入/绑定代理。导出也提供三种格式。
- 启动按钮分为 **切换并启动 → 启动中 → 进入**，启动完成后再次点击才进入 IDE。
- 顶部运行按钮随状态显示“停止反重力／启动反重力／启动中”，停止后再点可启动原配置；退出配置后的启动保持未登录界面。
- 手动停止状态会保存，后台重启不再自动打开空登录界面或默认配置；主动点击启动才重新运行。卡片状态刷新延迟到完整点击事件后，避免“进入”点击被按钮替换吞掉。
- 导入持续显示上传百分比、处理阶段与耗时，等待期间不自动消失，失败原因持续显示；预检查只读取元数据，减少配置包的重复写盘。
- 每张配置卡支持检测；授权保存及启动完成后自动检测。异常以红色突出显示，附上“可能是……”原因与时间。
- 支持 HTTP、HTTPS、SOCKS4/4a、SOCKS5/5h、IPv6、带认证的节点和逐行链接导入。节点可查看详情、修改字段或替换单个链接；配置和节点的 ID 创建后固定，改名和换地址不会解除绑定。
- 网页适配短窗口和手机：长弹窗的内容可以滚动，保存和关闭按钮始终可见；仅修改节点名称时自动同步链接备注，真实连接冲突仍拒绝保存。
- 默认限制 IDE 网页向外部站点发起请求，阻止外部跳转、预连接和 WebRTC，并移除客户端 IP 转发头。
- 导出本工具格式时，分别选择“导出本地全部聊天记录”和“导出对应代理”，默认不勾选；不导出聊天时同时过滤存档和工作目录内的对话正文、过程文件及索引。导入有代理文件时才显示选项，没有则提示“无代理文件”。
- 网页顶部和“服务设置”均提供“重启后台服务”。先保存监听端口，再重启后台使它生效；启动／重启反重力不改变后台端口。已保存的配置、密码、验证器和节点保留。

## 快速开始

支持 Linux x86_64 / ARM64、Python 3.9+，Debian/Ubuntu 的 systemd 主机及其容器：

```bash
git clone https://github.com/HANLINGVABCN/antigravity-suite.git
cd antigravity-suite
sudo bash install.sh
```

运行后先显示**终端安装向导**，依次选择：

1. 后台监听端口，默认 `18888`。
2. 管理员用户名，默认 `admin`。
3. 随机生成密码，或自己设置密码并再次确认。自定义密码至少 12 个字符，输入不显示。

安装前先检查正在运行的反重力，显示进程及可能的独占锁提示，询问是否关闭后继续。默认选择“不关闭”并取消安装；同意后先完成安装选项和依赖准备，再停止后台保存当前配置、关闭已确认的原生实例，并确认已释放占用。非交互安装不会默默关闭原生窗口，需明确追加 `--close-antigravity`，或先自行关闭。

选好后安装并启动后台，默认仅监听本机 `127.0.0.1`。**修改端口**：在网页“服务设置”修改监听端口，保存后点击“重启后台服务”。**修改监听地址**：先停止管理后台，在实际数据目录的 `config.json` 中修改 `listen_host`，再启动后台；例如改为 `0.0.0.0` 会监听所有 IPv4 网卡，适用于其他机器或另一个容器连接。同机反代和 SSH 转发可以继续使用 `127.0.0.1`。配置文件位置及操作命令见 [修改监听地址与端口](docs/deployment.md#修改监听地址与端口)。

选择随机密码时，安装结果会提示私有文件的位置：

```text
/var/lib/antigravity-suite-improved/initial-credentials.txt
```

可用 `sudo cat /var/lib/antigravity-suite-improved/initial-credentials.txt` 查看首次随机凭据。自定义密码使用刚才输入的内容，不保存明文；升级继续使用你当前的密码，旧的首次凭据文件不会更新。

已有配置时默认“保留设置升级”，也可选择仅修改端口；账号密码和验证器在网页“服务设置”中修改。自动安装可执行 `sudo bash install.sh --non-interactive`：首次使用默认端口、`admin` 和随机密码，升级保留原值。

从本机访问 VPS，可以运行：

```bash
ssh -N -L 127.0.0.1:19088:127.0.0.1:18888 user@your-vps.example
```

然后打开 `http://localhost:19088/?portal=1`。VPS 虚拟桌面内则打开 `http://localhost:18888/?portal=1`。若选择其他后台端口，请将转发目标和 VPS 内访问端口替换为实际值。

容器也直接运行同一个安装命令。自动识别已有 Supervisor 并复用现有目录；新容器默认使用 `/config/antigravity-suite-improved` 与其 `data/`（有 `/config` 时），否则使用普通默认目录。持久化和重启说明见 [部署说明](docs/deployment.md)。

## Google 账号使用

**默认复用本机已安装的兼容 Antigravity 官方客户端授权配置，无需每个账号申请 OAuth 应用。** 公开仓库不保存 client secret，程序仅在本机运行时读取匹配版本的应用信息。未安装本体或上游版本不兼容时，可以先安装本体、导入已有档案，或手动填写有权使用的授权配置，详见 [OAuth 配置](docs/oauth.md)。

Google 授权成功、存在 Token、引擎已启动都不等于账号一定有使用资格。检测会读取原生 `GetAuthStatus`；若返回地区不支持，卡片将显示异常及可能原因。

## 共享记录与隐私边界

共享库保存对话正文、过程文件及列表／搜索索引。切换或删除配置不会删除共享聊天记录。所有导出都包含登录凭据，只有勾选聊天记录时才附带本地全部共享记录，务必私有保存。

浏览器隐私保护针对本套件页面，并非整台电脑的 VPN 或网络防火墙。第三方预览、字体、反馈和部分集成功能可能被拦截。后端仍从 VPS 或所配置代理发出请求，子进程是否完全遵循代理需要网络层验证。本项目不保证免于账号风控、指纹关联或地区限制。

详见 [隐私与共享聊天记录](docs/privacy.md) 和 [安全说明](SECURITY.md)。

## 测试

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python tools/check_frontend.py
```

GitHub Actions 在 Linux / Python 3.9、3.11、3.14 上验证回归测试与源码发布检查，并单独运行 Chromium 界面测试。测试使用假后端和本地临时数据，不需要真实 Google 账号或代理。Windows 仅用于部分开发验证，Linux 符号链接及终端输入测试在 Linux 执行。

界面检查需要 Python 3.11+ 和测试浏览器，不属于运行服务的依赖：

```bash
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python -m pytest -q tests/test_portal_browser.py
```

## 项目文档

| 文档 | 内容 |
|---|---|
| [自动更新](docs/automatic-updates.md) | 网页升级、进度、备份和失败恢复 |
| [部署](docs/deployment.md) | systemd、Supervisor、SSH、反代与迁移 |
| [OAuth](docs/oauth.md) | 公开版 Google 授权配置和限制 |
| [格式兼容](docs/transfer-formats.md) | CPA/Sub2API 账号导入导出与代理规则 |
| [已有本体与配置](docs/existing-installations.md) | 一次性导入登录、原目录使用及进程接管 |
| [隐私](docs/privacy.md) | 浏览器请求边界、代理、账号环境及共享记录 |
| [更新记录](CHANGELOG.md) | 发布版本及行为变化 |
| [贡献指南](CONTRIBUTING.md) | 问题反馈、开发与 PR |
| [验证器](docs/authenticator.md) | 网页绑定、开启/关闭及恢复码 |
| [安全说明](SECURITY.md) | 私有漏洞反馈与部署边界 |

## 许可证

套件代码使用 [MIT](LICENSE)。Antigravity 本体单独下载，其许可和服务条款由 Google 提供，见 [第三方说明](THIRD_PARTY_NOTICES.md)。仓库不包含本体、真实凭据、SSH 密钥或用户聊天记录。
