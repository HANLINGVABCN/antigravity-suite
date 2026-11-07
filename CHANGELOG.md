# Changelog

## 0.8.0 — 2026-11-08

- 网页增加项目版本、检查更新和自动更新入口，读取项目 GitHub 正式版并显示完整进度。
- 使用独立更新进程兼容 systemd 和 Supervisor，先准备依赖、保存并备份，再替换及重启；保留账号、验证器、ID、节点、端口和聊天记录，失败恢复原源码和环境。
- 校验发布来源、SHA256、附件摘要和包内版本，拒绝不安全路径及私有数据；更新期间暂停管理写操作。
- 修正网页和桌面各自读取不同配置的问题：发现唯一可用原配置时，两个入口使用同一份当前运行配置；账号切换保存独立存档和固定身份。
- 当前账号在原目录更新的登录凭据会被保存，重启不以旧快照覆盖；原图标保持不变，原目录按所属用户运行。
- 增加下载校验、失败回滚、数据保留、实际页面按钮和原运行目录跨账号切换测试。

## 0.7.0 — 2026-11-08

- 增加终端安装向导：首次选择后台端口、管理员用户名、自定义或随机密码；隐藏输入并确认自定义密码，随机凭据只写入私有文件。
- 升级默认保留设置，可仅调整端口；保留当前密码、验证器、档案与节点身份，配置损坏时拒绝重新初始化。支持明确的非交互安装选项和容器独立配置向导。
- 重排网页状态栏、操作区及手机列表，修正短窗口下长弹窗超出屏幕、隐藏弹窗和提示遮挡操作的问题，固定标题及操作区并支持内容滚动。
- 仅修改节点名称时自动同步原链接备注，保留连接认证和固定 ID；真正的链接/字段冲突仍在弹窗内提示并拒绝保存。
- 状态刷新保留卡片元素及键盘焦点，鼠标按下或弹窗打开时延后更新卡片，避免点击被刷新打断。
- 嵌套文件编辑返回原档案窗口并保留未保存输入，支持焦点返回、Tab 限制和 Escape 关闭。
- 增加安装配置、改名一致性和真实 Chromium 浏览器回归检查，覆盖短窗口、手机、长表单、刷新及嵌套编辑；后台通过启动检查后才报告安装成功。

## 0.6.0 — 2026-11-07

- 新增已有反重力本体、配置和运行实例发现，检查资源、运行架构及依赖，不再固定使用套件复制本体。
- 已有配置以固定 ID 和特殊标签显示，网页直接使用原目录；原生登录后的状态可以被后台发现。
- 本体按钮按实际发现结果仅显示“卸载反重力”或“安装反重力”。
- 通过 PID 与启动时间、用户、配置目录和所属端口接管实例，处理跨用户容器读取限制并清理旧套件孤儿进程。
- 特殊配置保持原凭据，保留已有普通配置与共享聊天记录；卸载本体以可恢复备份方式处理，不删除账号数据。
- 增加发现、固定身份、无覆盖恢复、实例复用、端口归属和 PID 重用的回归验证及使用影响说明。

## 0.5.0 — 2026-11-07

- 新增三种导入入口：本工具配置包、CPA Antigravity JSON、Sub2API 账号数据 JSON；支持选择文件或粘贴第三方 JSON。
- CPA/Sub2API 仅转换账号登录凭据，所有第三方代理数据均忽略，节点由用户自行配置。
- 新增 CPA 和 Sub2API 格式导出，保留账号凭据及已知项目字段，不导出代理、聊天记录或模型调度设置。
- 处理 CPA 的 expired/timestamp 和 Sub2API 的 expires_at，过滤其他服务及已停用账号，保留真实过期状态并安排导入后自动检测。
- 导入前完整验证并在写入失败时回滚，既有配置 ID、代理绑定和共享聊天库不变。
- 增加格式及往返回归测试、兼容范围说明和中文发布说明。

## 0.4.0 — 2026-11-07

- Added optional authenticator-app two-factor login, configured entirely in the web service settings.
- Password-verified setup uses a locally generated QR/secret and a real TOTP confirmation before enabling.
- Added ten hashed, single-use recovery codes and verified disable/regeneration flows.
- Reject replayed TOTP codes and concurrent reuse of recovery codes; commit consumption before granting a session.
- Enabling, disabling, changing credentials or regenerating recovery codes revoke prior sessions and existing IDE connections.
- Existing deployments keep their password and authenticator setting; new installs leave it off until explicitly enabled.

## 0.3.0 — 2026-11-07

- Added an export option to include the profile's associated proxy, including its credentials, as a separate proxy.json file.
- Added validated, session-bound import previews and an optional proxy import choice only when the package actually contains a proxy file.
- Packages without proxy data show “无代理文件” and import the profile directly without a proxy question.
- Imported proxies are bound automatically when selected; colliding node IDs never overwrite different existing nodes.
- Added transfer, rollback, preview expiry and compatibility regression tests.

## 0.2.1 — 2026-11-07

- Prefill the node editor with its current complete, importable URI, including encoded credentials and its name.
- Reject saves when both a URI and manual fields are supplied but conflict; report the differing fields without echoing passwords.
- Keep existing values, IDs and bindings on conflicts; blank passwords still retain the saved value during edits.
- Apply the same consistency check when creating a single node; batch links are imported without manual fields.

## 0.2.0 — 2026-11-07

- Added proxy-node details and editing, including a read-only ID, timestamps, bound profiles and explicit password viewing/clearing.
- Connection fields and single-link replacements update the existing node; proxy bindings and node/profile IDs stay unchanged.
- Reject ID/creation-time edits and roll back in-memory updates when persistence fails.
- Require stopping the active IDE before changing its node connection, while allowing label-only edits.
- Added regression coverage for stable IDs, persisted edits, binding preservation, password semantics and atomic failures.

## 0.1.0 — 2026-11-07

First public release.

- Administrator authentication with random initial credentials, password hashing and session validation.
- Streaming HTTP/gRPC-Web and WebSocket gateway with an IDE management overlay.
- Separate per-profile runtime directories and credentials, with a shared conversation library and indexes.
- Explicit switch/start/enter flow, manual diagnostics and automatic checks after OAuth/launch.
- Visible abnormal results with possible causes, including region and account eligibility failures.
- HTTP/HTTPS/SOCKS proxy support, authentication bridge and batch link import.
- Browser CSP privacy restrictions, external redirect blocking, IP-header removal and per-profile browser storage.
- Independent core installation/uninstallation, profile import/export and configuration editing with backups.
- Runtime OAuth registration lookup from an installed compatible official client, with private configuration/environment overrides; no bundled client secret.
- Linux CI across Python 3.9, 3.11 and 3.14, deployment examples and public-tree checks.

Known limits: shared history paths depend on Antigravity versions; profile directories are not OS tenant sandboxes; browser policy does not control other apps; server proxy enforcement is not a network-level firewall. Google authorization/region eligibility is outside this project's control.
