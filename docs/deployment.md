# 部署、反代与升级

## 普通 Ubuntu/Debian VPS（systemd）

要求 Linux x86_64 或 ARM64、Python 3.9+、curl、Xvfb、xdpyinfo 和本体运行依赖。安装脚本针对使用 systemd 的主机：

```bash
git clone https://github.com/HANLINGVABCN/antigravity-suite.git
cd antigravity-suite
sudo bash install.sh
```

默认路径：

| 项目 | 路径 |
|---|---|
| 源码 | `/opt/antigravity-suite-improved` |
| 私有数据 | `/var/lib/antigravity-suite-improved` |
| 首次密码 | 数据目录内的 `initial-credentials.txt`，权限 600 |
| 服务 | `antigravity-suite-improved.service` |
| 默认监听 | `127.0.0.1:18888` |

首次启动生成随机密码，仅保存在私有文件。管理员密码修改会使旧会话失效，升级不重新生成密码。

本体未安装时仍可登录管理大厅、配置代理及导入档案。在顶部“安装 / 卸载反重力”操作本体；安装从官方 HTTPS 地址下载并独立解包，默认版本固定为 2.29.0。配置 `core_deb_sha256` 或 `core_deb_sha512` 可验证供应方校验值。`dpkg-deb` 不会安装缺失的系统运行依赖；缺库时先补齐依赖。

已有共享本体不会被套件卸载。要使用卸载功能，请先安装到套件自己的 `core_directory`；卸载仅移除这个本体目录，保留配置和聊天库。

## 手动或 Supervisor / s6 容器

安装依赖后建立独立环境：

```bash
cd /path/to/antigravity-suite
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
ANTIGRAVITY_SUITE_DATA=/path/to/private-data .venv/bin/python server.py
```

先验证前台启动，再参考 [Supervisor 示例](../examples/supervisor.conf) 加入你的监管配置。替换源码、数据和日志路径，运行 `supervisorctl reread`、`supervisorctl update`。没有 systemd 的容器不要执行 systemd 安装脚本。

服务用户必须能够读写数据目录、启动 Xvfb 并执行本体。默认 systemd 安装器使用 root；容器或手动部署可以选择专用普通用户。

## SSH 本地转发

在本地电脑运行：

```bash
ssh -N -L 127.0.0.1:19088:127.0.0.1:18888 -p 22 user@your-vps.example
```

然后访问 `http://localhost:19088/?portal=1`。在 VPS 虚拟桌面里的浏览器直接访问 `http://localhost:18888/?portal=1`。两处 localhost 分别指各自的机器。

## HTTPS 反向代理

使用独立域名。保留原始 Host、WebSocket 升级和长连接；关闭响应缓冲，设置足够长的读取超时。参考：

- [Nginx](../examples/nginx.conf)：需替换域名和证书路径。
- [Caddy](../examples/Caddyfile)：需替换域名，并满足自动证书签发条件。

本机 HTTPS 反代设置 `X-Forwarded-Proto` 时，可在私有 `config.json` 设置 `trusted_proxy: true`，用于 Secure Cookie 判断。该设置只信任 loopback 连接的转发协议头，不代表信任公网任意请求头。

不要直接公开原生动态端口或可选 TCP forwarder。管理员门禁由套件的 18888 网关提供。

## 升级与迁移

安装脚本会停止自己的服务，备份数据，再复制新源码和安装依赖。建议另行保存源码版本及完整数据备份。备份中包含凭据和聊天记录。

将早期单 HOME 数据迁移到独立 HOME / 共享历史库之前，使用 [迁移工具](../tools/migrate_shared_history.py)：

```bash
sudo systemctl stop antigravity-suite-improved
ANTIGRAVITY_SUITE_DATA=/var/lib/antigravity-suite-improved \
  /opt/antigravity-suite-improved/.venv/bin/python tools/migrate_shared_history.py
sudo systemctl start antigravity-suite-improved
```

迁移工具要求相关引擎都已停止，先备份，合并正文与索引，再链接各配置 HOME。不会尝试读取其他用户或其他应用的数据目录。

## 排错

```bash
systemctl status antigravity-suite-improved
journalctl -u antigravity-suite-improved -f
```

引擎日志位于 `profiles/<id>/home/engine.log`。诊断卡片分为尚未检测、检测中、正常和异常，原因以“可能是……”呈现。

- 登录回到门禁：检查 Cookie、HTTPS 及反代 Host。
- 启动任务不进入：设计上需等待按钮变为“进入”，再点一次。
- 代理检查失败：检查节点地址、协议、认证和网络。
- Google 鉴权失败：检查 OAuth 配置、凭据、账号资格及原生日志；有 Token 不等于有服务权限。
- 共享历史不可用：停止引擎后检查符号链接和索引版本，再从备份恢复。

卸载脚本只取消管理服务，保留源码、账号、历史和备份。请勿直接递归删除数据目录。


## 配置与节点的固定身份

每个配置和代理节点在创建时生成固定 ID。名称、邮箱、地址、端口、协议、认证信息及代理绑定属于可编辑字段；普通编辑不会生成新对象，不改变原 ID、创建时间或存储位置。

代理卡片的“详情与编辑”展示固定 ID、创建/更新时间及绑定配置，支持手动修改或用一个节点链接替换连接参数。编辑时密码留空保持原值，点击“查看已保存密码”可查看，勾选“清除已保存密码”才会清除；不要把真实密码粘贴到公开问题报告。

节点连接被当前 IDE 使用时，允许改备注名称；修改地址、协议或认证前需先停止 IDE，以免正在运行的引擎继续使用旧连接。保存后原配置仍绑定同一节点 ID，下次启动使用新连接。未运行的绑定配置也无需重新绑定。

节点批量导入以及配置导入副本会创建新对象，因此各自生成新 ID；这与修改现有对象不同。删除后重新创建也会得到新 ID。API 不提供重命名 ID 的操作。


### 节点链接与字段一致性

打开节点编辑时，链接框自动显示由当前已保存字段生成的完整链接，可复制或修改，链接可能包含节点密码，请勿公开分享。链接和下方字段都填写时，协议、地址、端口、用户名、密码及链接中的备注名称必须一致，否则显示冲突项并拒绝保存；不会覆盖原节点或改变 ID。只修改下方字段时可清空链接。编辑密码留空仍代表保留原密码，比较时使用已保存的密码；清除密码需要同时修改链接，或先清空链接。批量导入链接时请清空下方字段。


## 导出和导入对应代理

点击档案的“导出”后，如果档案绑定了有效节点，可选择“导出对应代理”。勾选时，包内添加独立的 `profile/proxy.json`，只包含对应的一个节点及其认证信息；不勾选则不添加代理文件。整个代理池不会被打包。没有绑定节点时显示“无对应代理”。

选择配置包后，服务先检查包结构。若存在有效代理文件，显示“导入对应代理”选项，可取消勾选后只导入配置；如果没有代理文件，显示“无代理文件”，直接导入配置，不询问是否导入代理。旧格式档案包也适用这一流程。选择导入代理时会自动绑定；不导入时不会按旧 proxy_id 误绑定本地节点。

代理 ID 在目标服务器未被使用时可保留；ID 与内容相同的已有节点直接复用，ID 相同但连接内容不同则新建节点并为新档案绑定，避免改写现有节点。配置导入仍创建副本，已有配置 ID 和绑定关系不变。导入预览与管理员会话绑定，10 分钟后失效，取消时删除临时上传包。

附带代理的包包含节点密码；所有配置包本身也包含账号凭据和聊天记录，应作为私有备份保管。
