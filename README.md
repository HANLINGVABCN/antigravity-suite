# Antigravity Suite

[![CI](https://github.com/HANLINGVABCN/antigravity-suite/actions/workflows/ci.yml/badge.svg)](https://github.com/HANLINGVABCN/antigravity-suite/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](requirements.txt)

**在任意 Linux 服务器上无头（Headless）部署 Antigravity，反向代理原生前端实现远程网页访问——本地无需登录 Google 账号、无需开梯子，随时随地打开浏览器即用。**

> 本项目是独立社区工具，与 Google 无隶属关系。当前版本为 **v0.10.1**，上游客户端协议仍可能随版本更新而变化。由于需要运行无头 Antigravity 本体，**对服务器内存和 CPU 性能有一定要求**。

## 这个项目是干嘛的？

官方 Antigravity 需要在本机桌面运行、登录 Google 账号并保持代理环境。**Antigravity Suite** 的核心思路是把无头 Antigravity 部署在你的 Linux 服务器上，只反向代理它的原生 Web 前端页面：

- **本地免登 Google 账号、免开梯子**：部署并反代好域名后，手机或任意电脑打开网页就能直接使用，本机不需要翻墙，也不需要在本地浏览器登录 Google 账号。
- **只反代原生前端，降低封号风控概率**：底层跑的是官方原版客户端进程，而不是逆向模拟接口的纯 API 反代；配合前端严格 CSP 阻断外链、移除客户端真实 IP、抹平多端设备指纹与静音遥测，尽可能降低触发风控的概率。
- **既能当 Gemini 网页端，也能管自己的服务器**：平时在手机/电脑上可直接当成免梯子的私人 Gemini 网页端聊天使用；同时因为引擎直接运行在你的 Linux 服务器内，也能直接让 AI 帮你开发、排查和管理服务器。
- **多账号凭证自由切换与迁移（兼容 CPA / Sub2API）**：支持多个登录凭证随意切换、备份、转移与导入导出，可为每个账号单独配置代理；支持与 **CPA** 和 **Sub2API** 格式互转（CPA 已做基础实测，Sub2API 尚未充分实测，欢迎反馈）。
- **自带安全门禁**：内置管理员密码登录与 **2FA 两步验证器**（TOTP），适合配合 HTTPS 反向代理公网随时访问。

---

## 核心功能

### 🌐 远程 Web 访问与高可用
- **完整代理原生前端**：支持 HTTP 流式转发、gRPC-Web、WebSocket 以及 Antigravity `2.35+` 的原生 `/connect-websocket` 通道，可搭配 Nginx / OpenResty / Caddy HTTPS 反代或 SSH 端口转发使用。
- **防连接耗尽与卡死自愈**：旧版长订阅自动走鉴权 WebSocket 隧道，避免 HTTP/1.1 连接槽被占满；上游引擎重启换端口或假死无响应时，网关自动刷新端口重连并在超时后回退到配置中心（`/?portal=1`），减少 `504` 锁死。
- **便捷交互**：支持 HTTPS 页面原生语音输入（按需向当前域名请求麦克风权限）；提供可拖动、自动贴边收纳的悬浮控制面板，快速切换账号或返回配置中心。

### 🔐 多账号管理、独立代理与格式互通
- **多凭证隔离存档，聊天记录共享**：每张配置卡拥有固定目录与 ID，仅保存账号凭据、名称、邮箱与节点绑定；所有账号共享本机全部聊天记录与软件设置，切换、停止或退出配置不重复迁移历史库。
- **已有安装自动接管**：部署时自动识别本机已有的 Antigravity 登录并纳入管理（命名为“默认配置-机器名”）；发现唯一可用原配置时，桌面端与网页端共用同一份当前登录。
- **独立代理配置**：支持 HTTP / HTTPS / SOCKS5 代理与代理链接导入，SOCKS5 默认远程解析 DNS；切换账号时自动应用对应节点。
- **多格式导入导出**：支持本工具格式、**CPA**、**Sub2API** 三种格式导入导出。导出本工具格式时可按需独立勾选是否附带本地共享聊天记录和代理配置（默认仅导出凭据），导入聊天记录支持合并或二次确认覆盖，导出过程无需停止客户端。
- **防误退出双保险**：在前端脚本、HTTP 网关与 `/connect-websocket` 通道同时拦截原生退出动作，防止误点导致已保存凭据失效；需要切换或登出时使用套件提供的“退出配置”，详见 [登录存档与退出](docs/login-sessions.md)。

### 🛡️ 隐私隔离与风控防护
- **前端零外泄边界**：严格 CSP 限制外部子资源、字体、预连接与外部跳转；禁用 `RTCPeerConnection`（含 `webkit`/`moz` 前缀）防止 WebRTC 泄露本地地址。
- **客户端信息脱敏与指纹归一化**：向上游转发时剥离各类客户端 IP 转发头（`X-Forwarded-For`、`X-Real-IP`、`CF-Connecting-IP` 等）与 `Sec-CH-UA*` 特征头，统一归一化 `User-Agent` 与 `Accept-Language`，避免手机与电脑交替远程访问产生设备指纹漂移。
- **前端遥测静音**：在网关层直接拦截 `RecordAnalyticsEvent` 与 `RecordObservabilityData` 等客户端遥测上报。
- **浏览器多账号隔离**：对同一域名下的 `Cookie`、`localStorage`、`sessionStorage`、`IndexedDB`、`Cache` 及跨标签 `BroadcastChannel` 全部按账号 ID 加命名空间前缀隔离。

### 💻 桌面端与官方 CLI（`agy`）联动
- **共用登录与代理桥**：同一 Linux 用户下通过 SSH 运行官方 `agy` CLI 时，自动共用当前配置凭据和本地代理桥；绑定了代理节点时，若代理不可用会直接阻止启动，绝不回退直连。
- **独立可用**：手动停止桌面窗口后 CLI 仍可继续使用；切换或退出账号时后台自动安全收尾对应 CLI 再更换登录，详见 [桌面端与 CLI 共用登录](docs/login-sessions.md#桌面端与-cli-共用登录)。

### ⚙️ 一键部署、门禁与在线更新
- **统一安装与保活**：同一安装脚本同时支持 systemd 主机与 Debian/Ubuntu 容器，自动复用已有 Supervisor 或安装独立 Supervisor；支持手动停止状态记忆（不会在后台重启后擅自拉起），并支持在网页中“重启后台服务”。
- **2FA 两步验证**：支持在网页“服务设置”中修改账号密码并绑定验证器（TOTP）与恢复码。
- **网页一键更新**：内置“更新项目”功能，可检查 GitHub 正式版并展示更新进度，更新前自动备份账号和节点，新版启动失败自动回滚。

---

## 快速开始

支持 Linux `x86_64` / `ARM64`、Python 3.9+，Debian/Ubuntu 的 systemd 主机及其容器：

```bash
git clone https://github.com/HANLINGVABCN/antigravity-suite.git
cd antigravity-suite
sudo bash install.sh
```

运行后进入**终端安装向导**，依次配置：

1. 后台监听端口（默认 `18888`）。
2. 管理员用户名（默认 `admin`）。
3. 随机生成密码，或自定义密码（至少 12 个字符，输入不回显）。

> **安装前检查**：脚本会先检测正在运行的 Antigravity 实例并提示是否关闭后继续（默认取消）。非交互部署可使用 `sudo bash install.sh --non-interactive`（首次默认端口 `18888`、`admin` 与随机密码，升级保留原配置；如需自动关闭已有实例请显式追加 `--close-antigravity`）。

### 查看首次凭据与监听设置

- 选择随机密码时，凭据保存在：
  ```text
  /var/lib/antigravity-suite-improved/initial-credentials.txt
  ```
  可用 `sudo cat /var/lib/antigravity-suite-improved/initial-credentials.txt` 查看（自定义密码不落明文，升级保留原有密码）。
- 后台默认仅监听 `127.0.0.1`：
  - **修改端口**：在网页“服务设置”中保存新端口后点击“重启后台服务”。
  - **修改监听地址**：停止后台后在数据目录的 `config.json` 中将 `listen_host` 改为 `0.0.0.0`（适用于跨容器或独立反代机访问），详见 [修改监听地址与端口](docs/deployment.md#修改监听地址与端口)。

### 访问方式

1. **HTTPS 反向代理（推荐，手机/电脑免梯子随时直连）**：使用 Nginx / OpenResty / Caddy 将域名反代至后台端口，并开启 WebSocket 转发支持（详见 [部署说明](docs/deployment.md)），然后直接访问 `https://your-domain.example/?portal=1` 并建议开启 2FA 验证器。
2. **SSH 本地端口转发**：
   ```bash
   ssh -N -L 127.0.0.1:19088:127.0.0.1:18888 user@your-vps.example
   ```
   然后浏览器打开 `http://localhost:19088/?portal=1`。
3. **VPS 虚拟桌面本地访问**：直接打开 `http://127.0.0.1:18888/?portal=1`。

> **Docker / 容器部署**：直接运行同一个 `install.sh`，会自动识别并复用已有 Supervisor；存在 `/config` 时默认安装在 `/config/antigravity-suite-improved` 与其 `data/` 目录，详见 [部署说明](docs/deployment.md)。

---

## Google 账号使用

- **默认复用官方客户端 OAuth 配置**：无需自己为每个账号申请 Google OAuth 应用。公开仓库不包含任何 client secret，程序仅在本机运行时读取兼容版本客户端的授权信息；如尚未安装本体或版本不兼容，可先通过面板安装本体、导入已有凭证（含 CPA/Sub2API）或手动配置授权参数，详见 [OAuth 配置](docs/oauth.md)。
- **账号资格检测**：授权成功或存在 Token 并不等同于账号有使用资格，面板检测会调用原生 `GetAuthStatus`，若遇地区不支持等情况会在卡片上直接提示原因。

## 共享记录与隐私边界

- 共享库保存对话正文、过程文件与索引，切换或删除账号配置不会删除共享聊天记录。
- 所有导出文件均包含登录凭据，勾选聊天记录时会附带本机全部共享记录，**请务必妥善私有保管，切勿泄露**。
- 浏览器隐私保护仅作用于本套件页面，不能替代整机 VPN 或防火墙；第三方预览、外链字体与反馈等集成功能可能被拦截。后端流量从服务器或所配置代理发出，本项目不保证绝对免于账号风控、指纹关联或地区限制，详见 [隐私与共享聊天记录](docs/privacy.md) 和 [安全说明](SECURITY.md)。

## 测试

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python tools/check_frontend.py
```

GitHub Actions 在 Linux（Python 3.9、3.11、3.14）上运行回归测试与发布检查，并单独执行 Chromium 浏览器界面测试（使用假后端与本地临时目录，不依赖真实 Google 账号或代理）。运行界面测试（需要 Python 3.11+）：

```bash
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python -m pytest -q tests/test_portal_browser.py
```

## 项目文档

| 文档 | 内容 |
|---|---|
| [部署说明](docs/deployment.md) | systemd、Supervisor、容器、SSH、反代与迁移 |
| [登录存档与退出](docs/login-sessions.md) | 多账号切换、防误退与桌面端 / CLI 共用登录 |
| [格式兼容](docs/transfer-formats.md) | CPA / Sub2API 账号导入导出与代理规则 |
| [验证器（2FA）](docs/authenticator.md) | 网页绑定、开启/关闭两步验证及恢复码 |
| [隐私与共享记录](docs/privacy.md) | 浏览器请求边界、代理、账号隔离及共享记录 |
| [OAuth 配置](docs/oauth.md) | 公开版 Google 授权配置和限制 |
| [已有本体与配置](docs/existing-installations.md) | 一次性导入登录、原目录使用及进程接管 |
| [自动更新](docs/automatic-updates.md) | 网页升级、进度、备份和失败恢复 |
| [更新记录](CHANGELOG.md) | 发布版本及行为变化 |
| [安全说明](SECURITY.md) | 私有漏洞反馈与部署边界 |
| [贡献指南](CONTRIBUTING.md) | 问题反馈、开发与 PR |

## 许可证

套件代码使用 [MIT](LICENSE) 许可。Antigravity 本体单独下载，其许可和服务条款由 Google 提供，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。仓库不包含本体二进制、真实凭据、SSH 密钥或用户聊天记录。
