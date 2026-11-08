# Changelog

## 0.9.3 — 2026-11-08

- 安装向导前检测运行中的原生反重力，显示进程、配置目录及可能的独占锁原因，询问是否关闭后继续。默认不关闭并取消安装，非交互需明确添加 `--close-antigravity`。
- 同意关闭后先准备依赖，再停止管理后台保存配置、关闭确认范围内的原生进程并等待锁释放；安装前再次检查，不删除锁文件、不执行退出登录。新配置目录的实例或自动重启导致的占用会明确报错。
- 安装失败统一显示具体阶段及退出码，保留上方错误和恢复流程，避免启动失败却无明显提示。
- 增加拒绝关闭、显式非交互授权、进程身份、未授权新环境、重新启动占用及真实终端／文件锁释放测试；完善中文使用说明。

## 0.9.2 — 2026-11-08

- 安装脚本支持 Debian/Ubuntu 容器，自动识别 systemd、已有 Supervisor 或独立 Supervisor；同一向导选择端口与账号密码，复用已运行项目路径，避免创建重复后台。无管理器时配置私有控制 socket 和启动脚本，兼容 LinuxServer s6 启动钩子。
- 安装先准备依赖，再停止并备份现有数据及源码，通过实际 HTTP 启动检查后报告成功；失败恢复原源码和服务。卸载识别对应管理器，只移除本项目并恢复原生保护，账号和聊天库保留。
- 导出本工具格式增加“导出本地全部聊天记录”与“导出对应代理”两个独立选项，默认不勾选，界面说明本次内容；不选聊天时过滤共享库、存档和 HOME 中的正文、过程文件及索引。旧 API 未指定选项仍兼容含聊天导出。
- 服务设置增加“重启后台服务”，保存端口后可直接应用；独立工作进程停止、启动及检查后台，失败尝试恢复原端口，保留管理员认证和配置节点。页面说明后台重启与反重力重启的区别及反代目标调整。
- 修复未登录状态与旧登录存档标记不一致时打开登录界面返回“档案不存在”：入口保持可见，先通过安全本地退出备份清理工作登录，再打开未登录界面，存档保留。
- 完善容器持久化、无启动钩子处理、导出内容和端口生效说明，新增管理器识别、独立导出选项及后台重启回归。

## 0.9.1 — 2026-11-08

- 登录保护识别原生脚本的局部变量重命名，兼容同一上游版本的不同压缩产物；仍核对认证方法与有效授权字段，未知结构不误报已验证。
- 导入持续显示上传进度、检查和保存阶段及耗时，处理中不再 7 秒后消失；失败提示保留，阻止重复提交。
- 配置包预检查只读取元数据并验证路径和大小，不再完整写盘后删除；保存时正常解压并合并聊天记录，减少小内存 VPS 的重复磁盘处理。
- 账号检测显示当前阶段和耗时，临时接口超时／端口变化自动重试；等待权限响应默认 30 秒，保留已经确认的 Token 状态及具体失败阶段，原因仍使用“可能是”表述，不将检测未完成等同于节点或账号失效。
- 顶部启动／停止按钮按运行状态切换，停止后可以启动同一配置；启动中禁用，退出配置后的启动继续打开未登录界面。“退出配置”继续保留在顶部与悬浮面板。
- 已停止的配置执行退出时，未登录环境保留该配置的代理绑定，避免服务重启后丢失当前进程代理信息而使用直连。
- 增加慢导入反馈、持久错误、预检查不落盘、超时重试、HTTP 异常阶段及启动停止状态的回归测试；已有旧部署补丁不替代完整版本更新，升级保留配置和节点 ID、账号密码及验证器。

## 0.9.0 — 2026-11-08

- 成功重新登录时生成独立登录存档，即使邮箱相同也生成新 ID；取消或失败不生成成功存档。登录操作编号与凭据哈希分开记录，正常续期、重启、切换旧配置不重复建卡，也不覆盖其他会话。
- 每 3 秒检查稳定的当前登录变化，续期只更新当前存档，登录成功回调立即尝试保存并自动检测；部署时已有登录仍只提取一次，普通卡片和已有 ID 保留。
- 网页顶部和桌面／网页悬浮窗增加“退出配置”，先停止并存档，仅清理工作登录，再打开未登录界面；不调用 Google／原生撤销授权，未能确认清理时异常提示并尝试恢复，保留备份和全部聊天记录。
- 兼容 Linux Electron 本体增加可恢复的保护，原生退出在改动认证状态之前提示并拦截；网页和原生请求层同时拒绝 AuthLogout。使用内联沙箱预加载与客户端弹窗，兼容受限桌面容器；首次接管保护更新需要重启被管理实例。
- 桌面控制使用本机能力令牌与精简接口，不分享管理员密码／验证器／会话，不允许导出凭据、任意文件或命令；网页明确显示保护等待验证或已验证状态。
- 增加登录代次、同账号重登、续期、恢复、退出回滚、权限边界、请求拦截和 ASAR 原样恢复测试；真实 Electron 副本配合模拟授权服务验证沙箱、前置拦截、警告弹窗和原文件不变。
- 完善登录存档、卸载恢复、兼容性及授权可能共同失效的说明；上游本体变化仍需检查保护兼容性，不保证登录存档长期独立有效。

## 0.8.1 — 2026-11-08

- 原生退出只解除当前会话，保留原账号存档；重新登录不同账号时新增或复用该账号的独立卡片，保存前核对身份，避免新账号覆盖旧账号。

- 移除账号列表的特殊标签、紫色边框、原目录说明及重复运行目录卡片，所有账号统一显示为普通配置。
- 首次部署/接管时读取一次已有反重力登录，提取账号和配置快照；复用相同账号的原 ID 与绑定，未登录不生成占位卡片，后续扫描不持续导入。
- 运行目录登记与账号列表分开，原桌面和网页继续使用同一份当前运行配置；旧重复卡片身份和文件留在私有数据中。
- 旧独立登录卡片转换为普通账号时保留原 ID，快照包含已提交 SQLite/WAL 数据；删除普通账号后不在重启时重新生成。
- 增加已登录导入、未登录无卡片、去重、稳定身份、SQLite 快照、删除重启及实际界面回归检查。

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
