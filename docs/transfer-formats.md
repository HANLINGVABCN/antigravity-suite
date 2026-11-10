# 本工具、CPA 和 Sub2API 导入导出

点击配置列表的 **导入**，选择以下来源：

| 入口 | 接受的文件 | 导入内容 |
|---|---|---|
| 从本工具导入 | 本工具 `.tar.gz` 配置包 | 账号凭据；可选导入附带聊天及代理 |
| 从 CPA 导入 | CLIProxyAPI 的 Antigravity 账号 JSON | 转换账号登录凭据，不导入、不绑定代理 |
| 从 Sub2API 导入 | Sub2API 的账号数据 JSON | 只转换 Antigravity OAuth 账号，不导入、不绑定代理 |

第三方入口支持选择 JSON 文件或粘贴 JSON。每个文件最大 4MB、最多 100 个账号；本工具压缩包最大上传 512MB，解包最多 1GiB、100000 项。输入 JSON 在服务端验证后写成**本工具自己的配置**，不是另开一个 CPA 或 Sub2API 服务。

**CPA / Sub2API 的文件即使含有代理数据也会忽略。** 导入后的配置默认没有绑定节点，代理由你在本工具里自己添加和绑定。本工具的原生配置包仍按已有设计，有 `proxy.json` 才询问是否导入对应代理；没有则显示“无代理文件”，直接导入配置。

## CPA 文件

支持单个账号对象或账号对象数组。仅导入 `type: "antigravity"` 的启用账号。

典型字段：

```json
{
  "type": "antigravity",
  "name": "示例账号",
  "email": "example@example.com",
  "access_token": "EXAMPLE_ACCESS_TOKEN",
  "refresh_token": "EXAMPLE_REFRESH_TOKEN",
  "expired": "2030-01-01T18:00:00+08:00",
  "expires_in": 3599,
  "timestamp": 1893488401000,
  "project_id": "example-project",
  "disabled": false
}
```

`expired` 会转换为 UTC 过期时间；兼容 `expiry`、秒/毫秒时间戳和数字字符串。保留提供的 `project_id`，但模型能力列表、上游 endpoint、代理链接和调度信息不会改动本工具。

`refresh_token` 必须存在。仅有过期/缺失的 `access_token` 时不伪造“刚登录成功”的状态，导入后安排原生检测。没有刷新凭据时需要重新授权，不能只凭一段 API 密钥登录反重力。

## Sub2API 文件

支持 `sub2api-data` 或旧 `sub2api-bundle` 数据类型，版本 0/1，以及省略类型/版本的账号数据对象。文件需要 `accounts` 数组，例如：

```json
{
  "type": "sub2api-data",
  "version": 1,
  "proxies": [],
  "accounts": [
    {
      "name": "示例账号",
      "platform": "antigravity",
      "type": "oauth",
      "credentials": {
        "access_token": "EXAMPLE_ACCESS_TOKEN",
        "refresh_token": "EXAMPLE_REFRESH_TOKEN",
        "expires_at": "1893492000",
        "email": "example@example.com",
        "project_id": "example-project"
      }
    }
  ]
}
```

只接受 `platform: "antigravity"`、`type: "oauth"` 的启用账号。Claude、Codex/OpenAI、API Key 和已停用账号跳过；若没有可导入账号，会给出提示。`proxies` 和账号的 `proxy_key` 完全忽略，不读取其中节点密码，不建立绑定。

## 聊天文件的导入选项

有聊天附件时显示“导入附带聊天记录”，默认不勾选。勾选后选择：

- **合并导入**：保留本机记录，加入包里的聊天；相同 ID 的不同版本沿用合并规则，保存冲突版本，索引一起合并。
- **覆盖导入**：明确二次确认后，将本机所有配置共用的聊天库替换为包里的聊天。旧记录不会因切换账号恢复，账号凭据、软件设置和配置卡目录不受覆盖影响。

未勾选时只导入账号，旧包夹带在 HOME 或 store 中的聊天也不会被偷偷导入。没有聊天附件时显示“无聊天文件”，不显示聊天勾选及合并／覆盖选项；代理附件独立选择，无代理时显示“无代理文件”。

导入聊天前关闭对应桌面和 CLI，完成后恢复原先运行的桌面。旧聊天仅临时移动用于失败回滚，完成后清除临时目录；进程中断后在下次启动恢复，不额外堆积备份。导入期间不要另行打开相同目录的客户端。

## 导出

点击某配置的 **导出**，选择：

1. **导出为本工具格式**：固定账号存档中的登录凭据，可独立勾选附带本机全部聊天和对应代理，默认不附带；不包含整份软件设置、工作目录或旧快照。
2. **导出为 CPA 格式**：平铺的 Antigravity 账号 JSON，不含代理及聊天记录。
3. **导出为 Sub2API 格式**：单账号的 `sub2api-data` JSON，`proxies` 为 `[]`，不包含代理引用和聊天记录。

当前配置先将有效续期凭据保存到原配置卡，再导出；非当前配置只读取自己的存档。导出不停止客户端、不迁移聊天，勾选聊天时通过 SQLite 读取已提交索引。没有 Google 凭据的空配置不能导出为第三方账号格式，企业授权类型暂不支持第三方导出。

## ID、检测和兼容边界

- 导入第三方账号会创建本工具的新配置和新 ID，既有配置、节点 ID 及绑定不变。导入不会切换当前主账号，但会安排临时进程进行账号检测。
- 批量文件会先完整验证所选 Antigravity 账号；字段缺失时不写入部分账号。保存失败时回滚新建配置。
- 对话库不从第三方 JSON 导入，因此不会删除、覆盖或另建你的共享聊天记录。
- 检测异常显示可能原因。有凭据、格式正确和 Google 账号实际有权限是三件事；凭据所属授权应用/范围、地区、账号资格或过期情况都可能影响使用。
- 格式适配按 CLIProxyAPI/Sub2API 上游结构和 CPA 样例验证，并做了往返测试。不同分支、未来格式或 Google 授权行为可能变化；未测试的格式不能保证通用。
- 所有 JSON 导出均包含账号登录凭据，应私有保存；仓库示例和测试只用虚构数据，不能将真实文件提交到 GitHub。
