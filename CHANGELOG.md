# Changelog

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
