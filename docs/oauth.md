# Google 授权配置

公开源码不包含任何内置 OAuth client secret。默认从本机已安装的兼容 Antigravity 官方客户端读取授权应用信息，复用它的桌面授权注册；不需要每个 Google 账号申请应用。管理员登录和档案导入也不需要申请应用。

本机读取通过已验证的注册信息指纹匹配，支持当前验证过的 2.24.0 / 2.29.0 发行版。应用信息只用于当前服务的授权交换，不打印或复制到公开源码。上游更改注册信息或二进制布局后，可能无法识别。此时先安装兼容本体、导入已有账号档案，或使用下面的手动配置方式。

## 手动配置（可选覆盖）

先停止管理服务，再编辑私有数据目录中的 `config.json`，**保留原字段**并添加：

```json
{
  "oauth_client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
  "oauth_client_secret": "YOUR_CLIENT_SECRET_IF_REQUIRED",
  "oauth_redirect_uri": "http://localhost:18888/__suite/api/auth/google/callback"
}
```

上面仅展示附加字段，不是完整配置文件。完成后重启服务。client secret 只在对应应用确实要求时填写；无需 secret 的公共客户端可以省略。

也可以通过服务环境变量 `ANTIGRAVITY_OAUTH_CLIENT_ID` 和 `ANTIGRAVITY_OAUTH_CLIENT_SECRET` 提供，配置文件字段优先。程序不会自动加载 `.env`；配置 systemd/Supervisor 的私有环境文件，并设置权限 600。

redirect URI 必须符合 OAuth 应用注册的回调地址，不能任意替换成反代域名。未配置时使用 `http://localhost:<套件端口>/__suite/api/auth/google/callback`。浏览器在远程电脑上时，localhost 指浏览器所在电脑；即使该页面打不开，也可以复制地址栏中的完整回调 URL，返回配置大厅粘贴。

## 流程

1. 安装兼容的官方本体；程序自动读取授权注册。需要覆盖时再配置 client ID、必要时的 secret 和合法回调地址。
2. 在配置大厅获取授权链接，在浏览器中完成 Google 授权。
3. 粘贴完整回调 URL，选择名称和代理并保存。
4. 后台完成 PKCE 与 state 验证，取得凭据后自动检测一次。
5. 检测结果正常时再点击“切换并启动”；启动完成后点击“进入”。

OAuth state 与当前管理员会话绑定，10 分钟后失效。授权网页使用浏览器网络；凭据交换使用所选 VPS 代理。公共代码不会通过其他待授权会话兜底匹配 state。

## 可用性限制

本项目请求 Antigravity 需要的权限，部分 scope、OAuth 应用或账号可能受 Google 的审批、应用许可或服务政策限制。自行新建普通 OAuth client 不保证能取得 Antigravity 所需权限。若 Google 拒绝授权，请检查你有权使用的应用配置，或导入通过支持方式取得的现有档案。

已有凭据也不保证账号具备服务资格。地区、套餐、组织策略及 Google 后续判断可能影响使用；检测只报告本次原生响应。

不得将真实应用 secret、授权码、Token 或档案导出包提交到 GitHub。
