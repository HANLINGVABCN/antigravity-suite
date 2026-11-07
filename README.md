# Antigravity Suite

[![CI](https://github.com/HANLINGVABCN/antigravity-suite/actions/workflows/ci.yml/badge.svg)](https://github.com/HANLINGVABCN/antigravity-suite/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](requirements.txt)

Linux VPS 上的 Antigravity 账号配置管理、Web 反代与共享聊天记录套件。

**所有配置共用本地聊天记录，登录凭据和运行目录分别保存。** 支持管理员门禁、悬浮控制面板、账号检测、代理链接导入及本体安装／卸载。

这是独立社区项目，与 Google 无隶属关系。当前版本为 **v0.5.0**，上游兼容性仍可能变化。

## 功能

- 随机初始管理员密码、密码哈希、登录限速与会话验证；网页可选开启验证器 App 两步验证，支持本机生成的绑定二维码和一次性恢复码。
- HTTP 流式转发、gRPC-Web 和 WebSocket 支持；可搭配 HTTPS 反代或 SSH 转发。
- 每个账号拥有独立 HOME、Cookie、缓存和默认项目目录；所有账号读取同一个本地对话库。
- 导入可选择本工具、CPA 或 Sub2API；第三方账号 JSON 转换为本工具配置，不导入/绑定代理。导出也提供三种格式。
- 启动按钮分为 **切换并启动 → 启动中 → 进入**，启动完成后再次点击才进入 IDE。
- 每张配置卡支持检测；授权保存及启动完成后自动检测。异常以红色突出显示，附上“可能是……”原因与时间。
- 支持 HTTP、HTTPS、SOCKS4/4a、SOCKS5/5h、IPv6、带认证的节点和逐行链接导入。节点可查看详情、修改字段或替换单个链接；配置和节点的 ID 创建后固定，改名和换地址不会解除绑定。
- 默认限制 IDE 网页向外部站点发起请求，阻止外部跳转、预连接和 WebRTC，并移除客户端 IP 转发头。
- 独立本体下载、安装和卸载；档案导入导出与文本配置编辑。导出可选附带绑定代理，导入有代理文件时才显示导入选项，没有则提示“无代理文件”并直接导入配置。卸载本体保留账号及聊天库。

## 快速开始

支持 Linux x86_64 / ARM64、Python 3.9+。Ubuntu/Debian 的 systemd 主机：

```bash
git clone https://github.com/HANLINGVABCN/antigravity-suite.git
cd antigravity-suite
sudo bash install.sh
```

默认监听 `127.0.0.1:18888`。初始密码保存在：

```text
/var/lib/antigravity-suite-improved/initial-credentials.txt
```

从本机访问 VPS，可以运行：

```bash
ssh -N -L 127.0.0.1:19088:127.0.0.1:18888 user@your-vps.example
```

然后打开 `http://localhost:19088/?portal=1`。VPS 虚拟桌面内则打开 `http://localhost:18888/?portal=1`。

容器 / Supervisor 部署、反代、升级和数据迁移见 [部署说明](docs/deployment.md)。不使用 systemd 的容器请按手动部署步骤安装。

## Google 账号使用

**默认复用本机已安装的兼容 Antigravity 官方客户端授权配置，无需每个账号申请 OAuth 应用。** 公开仓库不保存 client secret，程序仅在本机运行时读取匹配版本的应用信息。未安装本体或上游版本不兼容时，可以先安装本体、导入已有档案，或手动填写有权使用的授权配置，详见 [OAuth 配置](docs/oauth.md)。

Google 授权成功、存在 Token、引擎已启动都不等于账号一定有使用资格。检测会读取原生 `GetAuthStatus`；若返回地区不支持，卡片将显示异常及可能原因。

## 共享记录与隐私边界

共享库保存对话正文、过程文件及列表／搜索索引。切换或删除配置不会删除共享聊天记录。导出的档案包含凭据和共享记录，务必私有保存。

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

GitHub Actions 在 Linux / Python 3.9、3.11、3.14 上验证回归测试与源码发布检查。测试使用假后端和本地临时数据，不需要真实 Google 账号或代理。Windows 仅用于部分开发验证，Linux 符号链接测试在 Linux 执行。

## 项目文档

| 文档 | 内容 |
|---|---|
| [部署](docs/deployment.md) | systemd、Supervisor、SSH、反代与迁移 |
| [OAuth](docs/oauth.md) | 公开版 Google 授权配置和限制 |
| [格式兼容](docs/transfer-formats.md) | CPA/Sub2API 账号导入导出与代理规则 |
| [隐私](docs/privacy.md) | 浏览器请求边界、代理、账号环境及共享记录 |
| [更新记录](CHANGELOG.md) | 发布版本及行为变化 |
| [贡献指南](CONTRIBUTING.md) | 问题反馈、开发与 PR |
| [验证器](docs/authenticator.md) | 网页绑定、开启/关闭及恢复码 |
| [安全说明](SECURITY.md) | 私有漏洞反馈与部署边界 |

## 许可证

套件代码使用 [MIT](LICENSE)。Antigravity 本体单独下载，其许可和服务条款由 Google 提供，见 [第三方说明](THIRD_PARTY_NOTICES.md)。仓库不包含本体、真实凭据、SSH 密钥或用户聊天记录。
