# 自定义数据源配置协议（零代码接入）

任何 agent 工具的会话数据，只要是「JSON/JSONL + 消息数组 + role/text/timestamp 三件套」，
就能通过在 `config.yaml → sources.custom` 追加一段配置接入，无需写代码。
本文是配置项的完整说明。

## 配置项总表

| 配置项 | 必填 | 类型 | 说明 |
|--------|------|------|------|
| `name` | ✅ | string | 源名称（唯一，用于报告标注与过滤） |
| `type` | ✅ | string | `jsonl`（每行一个 JSON，含完整会话）<br>`json`（一个文件一个完整 JSON）<br>`jsonl_events`（每行一个事件，消息是其中一类事件——Claude Code/OpenClaw 式） |
| `path` | ✅ | string | 数据根目录（支持 `~`） |
| `session_glob` | ❌ | string | 会话文件匹配模式，默认 `*.jsonl` / `*.json`（按 type） |
| `include_globs` | ❌ | list | 递归匹配（相对 path），优先级高于 session_glob |
| `format` | ✅ | dict | 字段映射（见下） |
| `language` | ❌ | string | `zh` / `en` / `mixed`，提示语言分析器，默认 `mixed` |
| `enabled` | ❌ | bool | 默认 true |
| `max_sessions` | ❌ | int | 采样上限（调试用），默认不限 |

## format 字段映射（核心）

统一消息模型需要 4 个字段：`role`、`text`、`timestamp`、`session_id`。
配置就是告诉连接器这些字段在源数据里的**路径和取值**：

| 配置项 | 必填 | 说明 | 示例值 |
|--------|------|------|--------|
| `message_path` | ✅ | 消息数组的 JSONPath（`$` 是文件/行根） | `$.messages[*]`、`$.data.conversation[*]` |
| `role_field` | ✅ | 消息对象内 role 字段名 | `role`、`speaker` |
| `role_user_value` | ✅ | 用户角色的取值 | `user`、`human`、`me` |
| `role_assistant_value` | ✅ | AI 角色的取值 | `assistant`、`ai`、`model` |
| `text_field` | ❌ | 正文内容字段名；支持 `content` 或嵌套 `content[0].text` | `text`、`content` |
| `timestamp_field` | ❌ | 消息级时间戳字段名 | `ts`、`created_at` |
| `timestamp_format` | ❌ | `iso8601`（默认）/ `epoch_s` / `epoch_ms` | `epoch_ms` |
| `session_id_field` | ❌ | 会话 ID 字段（缺省用文件名） | `conversationId` |
| `project_field` | ❌ | 项目/上下文标注字段（缺省用 path 尾段） | `cwd`、`channel` |

**路径语法**：`$` 开头的简化 JSONPath，支持 `a.b.c` 逐层下钻与 `[*]` 数组展开。
遇到消息正文是 `[{type:text,text:...}, {type:image,...}]` 结构时，
`text_field: content` 会自动提取所有 text 部分拼接。

## 完整示例

### 示例 1：自建 agent，JSONL 每行一个完整会话

```yaml
custom:
  - name: my-agent
    type: jsonl
    path: ~/.myagent/logs/
    session_glob: "*.jsonl"
    format:
      message_path: "$.messages[*]"
      role_field: role
      role_user_value: human
      role_assistant_value: assistant
      text_field: text
      timestamp_field: created_at
      timestamp_format: iso8601
```

对应数据形状（每行）：
```json
{"messages": [{"role": "human", "text": "帮我看看这个报错", "created_at": "2026-10-01T09:30:00"},
              {"role": "assistant", "text": "……", "created_at": "2026-10-01T09:30:05"}]}
```

### 示例 2：每行一个事件流（消息是事件之一）

```yaml
custom:
  - name: my-event-agent
    type: jsonl_events
    path: ~/logs/agent/
    include_globs: ["**/*.jsonl"]
    format:
      message_path: "$[*]"              # 每行本身是数组，或用 type_field 过滤
      type_field: type                  # 事件类型字段
      message_type_value: message       # 哪个 type 是消息
      role_field: message.role
      role_user_value: user
      role_assistant_value: assistant
      text_field: message.content
      timestamp_field: timestamp
      timestamp_format: iso8601
```

### 示例 3：单 JSON 文件装一个会话（Hermes 旧版风格）

```yaml
custom:
  - name: legacy-hermes
    type: json
    path: ~/.hermes/sessions/
    session_glob: "session_*.json"
    format:
      message_path: "$.history[*]"
      role_field: from
      role_user_value: user
      role_assistant_value: assistant
      text_field: "content.parts[0]"
      timestamp_field: t
      timestamp_format: epoch_s
```

## 验证你的配置

```bash
# dry-run：只解析并打印前 3 条统一消息，不写任何东西
python3 scripts/am.py scan --source my-agent --dry-run

# 正式扫描
python3 scripts/am.py scan --source my-agent
```

## 不适用本协议的情况

- 数据在数据库里（SQLite/PostgreSQL）：`type: sqlite` + SQL 模板在 Roadmap（OpenClaw memory 优先支持）
- 二进制/加密格式：需要写 Python 连接器，参考 `scripts/am.py` 内置连接器的接口
- 纯截图/语音记录：超出文本分析范围，不在目标内

## 自测题（全部能答出来即通关）

1. 自定义源必填哪三个顶层配置项？`type` 的三个取值各对应什么数据形状？
2. `role_user_value` 为什么必须配？如果源数据里用户角色叫 `human` 而你配了 `user` 会发生什么？
3. 验证一个新数据源的两步命令？dry-run 时会输出什么？
4. `exclude_projects` 是子串匹配——写出它可能误伤的场景和一个规避写法。

<details><summary>📖 参考答案（先自测再展开）</summary>

1. `name`（唯一标识，用于报告标注）/ `type` / `path`。`jsonl`=每行一个完整会话 JSON；`json`=一个文件一个完整会话；`jsonl_events`=每行一个事件、消息是其中一类（Claude Code/OpenClaw 式，需要 type_field 过滤）。
2. 连接器靠 role 字段值区分人和 AI；配错则所有消息 role 无法归入 user/assistant，normalize 返回 None——表现为源被"扫到 0 条消息"，dry-run 一眼可见。
3. `am.py scan --source <name> --dry-run`（打印前 3 条统一消息样本）→ 确认字段对了再正式 `scan --source <name>`。dry-run 输出的是映射后的统一消息（source/project/role/ts + 文本前 120 字），不是原始 JSON。
4. 子串匹配会误伤包含同串的项目名，如 exclude `["ccu"]` 会同时排除 `ccu-170` 和 `l4-ccu-test`。规避：写完整项目目录名（如 `["-mnt-data-ccu-170"]`）或足够长的特异子串。

</details>
