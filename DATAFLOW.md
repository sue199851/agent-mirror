# DATAFLOW.md · 三层输入输出契约

> 本文是采集层 / 知识库层 / 分析层的**接口规范**。三层通过两个磁盘工件衔接，
> 每层的输入来源、输出去向、LLM 依赖如下。改任何一层的格式，先改这里。

## 总览：三层两工件

```
┌─────────────────────────────────────────────────────────────┐
│ ① 采集层  connectors（三默认源 + custom，paths[] 多目录）      │
│    输入: config.yaml sources + 各源原始文件                    │
│    输出: ▶ 工件A index/messages.jsonl（统一消息物化缓存）       │
│          + scan 统计报告（人读）                               │
├─────────────────────────────────────────────────────────────┤
│ ② 知识库层  build / search / ask / chat                       │
│    输入: 工件A                                                 │
│    输出: ▶ 工件B index/knowledge.sqlite（FTS5 检索库）          │
│          + 检索结果（人读 / 给 ask 拼上下文）                    │
│    LLM:  build/search 纯脚本 ✅  ask/chat 用 LLM（可降级）       │
├─────────────────────────────────────────────────────────────┤
│ ③ 分析层  analyze（personality / career）                      │
│    输入: 工件A（只取真人消息做特征）                             │
│    输出: 证据面板报告 + profiles/*/snapshots/ 快照              │
│    LLM:  规则层纯脚本 ✅  llm/hybrid 模式用 LLM（可降级）         │
└─────────────────────────────────────────────────────────────┘
```

**为什么是两个工件而不是三层直连**：物化解耦——47k 消息重扫源文件要十几秒，
扫一次落盘后，知识库和分析层都吃缓存；换源/多机器数据合并只影响采集层。

## 工件A · `index/messages.jsonl`（统一消息流）

每行一条消息，字段契约（v1，向后兼容只加不改）：

```json
{
  "source":    "claude_code | openclaw | hermes | <custom名>",
  "session":   "会话ID（源文件名或源内ID）",
  "ts":        "ISO8601 本地时区，缺失为 null",
  "role":      "user | assistant",
  "text":      "正文纯文本（content数组已拼接，无JSON壳）",
  "project":   "项目/上下文标注（目录名、cwd、cron任务名）",
  "automated": false
}
```

写入时机：`am.py build` 全量重建（幂等，先清后写）。

## 工件B · `index/knowledge.sqlite`（FTS5 检索库）

| 表 | 收什么 | 为什么 | 检索权重 |
|----|--------|--------|---------|
| `entries` | 真人 user 消息 + **有实质内容的 assistant 回复**（≥40字，滤掉 NO_REPLY/空壳） | 问答对是知识主体：你问的问题和得到的解法都在这 | 主库，BM25 默认排序 |
| `auto_events` | automated 消息（cron/heartbeat） | 部署历史也是知识（"我当时配了什么监控"），但模板化内容会污染主检索 | 独立表，`search --include-auto` 才查 |

- **中文检索**：FTS5 `trigram` tokenizer（子串匹配，"耗时"能命中"函数耗时优化"）；
  SQLite<3.34 自动降级 unicode61（英文正常，中文退化为 LIKE 兜底，search 内置）
- 每条 entry 带原文定位（source+session+ts），检索结果可回溯到 messages.jsonl 上下文

## 每层命令与 LLM 依赖矩阵

| 命令 | 层 | 读 | 写 | LLM | 无 key 时 |
|------|----|----|----|-----|----------|
| `scan` | ① | 源文件 | 报告（stdout/文件） | ❌ | 完整可用 |
| `build` | ② | 源文件 | **工件A + 工件B** | ❌ | 完整可用 |
| `search "<kw>"` | ② | 工件B | stdout | ❌ | 完整可用 |
| `ask "<问题>"` | ② | 工件B(+A) | stdout | ✅ LLM | 降级：返回检索结果 + 提示 |
| `chat` | ② | 工件B(+A) | stdout | ✅ | Roadmap（当前每轮独立 ask） |
| `analyze --mode rules` | ③ | 工件A | stdout + **reports/<track>-<日期>.md/.html** + 快照 | ❌ | 完整可用（默认模式） |
| `analyze --mode llm/hybrid` | ③ | 工件A | 同上 | ✅ | 降级为 rules 并提示 |
| `confirm` | ③ | 工件A | calibration.yaml / pending.yaml | 出题可选 | 固定题库兜底 |
| `diff <track>` | ③ | 快照 features.json | stdout | ❌ | 完整可用 |
| `export <track>` | ③ | 工件A | reports/export-<track>-<日期>.json | ❌ | 完整可用 |

LLM 暴露始终受 `privacy.llm_exposure` 档位约束（stats_only / redacted / off），见 ARCHITECTURE.md §7。

### 产出物分层（人读 vs 机器）

| 产物 | 性质 | 消费者 |
|------|------|--------|
| `reports/<track>-<日期>.md` + `.html` | 人读报告（analyze 双写，html 自包含零依赖） | 人：浏览器直开 / 打印 / 分享 |
| `snapshots/<日期>/evidence.md` | 人读存档（同报告按日期归档） | 人：回看历史 |
| `snapshots/<日期>/features.json` | 机器 | `diff` 漂移对比 |
| `reports/export-<track>-<日期>.json` | 机器 | 二次分析 / 画图原料（个人玩法） |

## 标准用户旅程（怎么用）

```bash
# 首次：体检 → 建库 →（此后日常）
python3 scripts/am.py scan                    # 1. 我有多少数据？（10秒）
python3 scripts/am.py build                   # 2. 物化+建索引（15秒，无LLM）
python3 scripts/am.py search "OOM"            # 3a. 纯本地查历史（秒回，无LLM）
python3 scripts/am.py ask "我上次怎么调OOM的"  # 3b. LLM归纳（需key，可选）
python3 scripts/am.py analyze                 # 4. 画像证据面板（默认规则层）
python3 scripts/am.py analyze --mode hybrid   #    深度：规则+LLM双层
```

## 权重设计总表（同一消息在两层的不同命运）

| 消息类型 | 知识库层 | 分析层 |
|---------|---------|--------|
| 真人 user | `entries` 主库，全量 | 特征提取（画像唯一原料） |
| assistant 有内容 | `entries`（解法载体） | ❌ 不参与（不是用户行为） |
| assistant 噪音（NO_REPLY/空） | ❌ 不入库 | ❌ |
| automated（cron/heartbeat） | `auto_events`（部署历史，默认不搜） | ❌ 不进画像；部署倾向单独成表（scan 已出） |

> 设计原则：**知识库回答"发生过什么"（全面），分析层回答"说明了什么"（纯真人）**。
> 同一条消息可以两层都进，但语义完全不同。

## 已知边界（v1）

- **subagent 转录刻意不收**：Claude Code 把子代理会话存在
  `projects/<项目>/<主会话id>/subagents/agent-*.jsonl`（`isSidechain: true`）。里面的
  user 角色消息是 **agent 写给 agent 的任务指令**（非人手敲），收进人格分轨会污染
  真人样本——所以连接器只扫项目目录第一层的主会话文件。知识库收其 assistant 侧
  （解法内容有价值）是 Roadmap 可选项。若你在 include_globs 配了 `**/*.jsonl`
  想收，注意先把 subagent 前缀加入 automated_patterns。
- **主会话采集完整性自证**（实测命令）：
  `find ~/.claude/projects -maxdepth 2 -name '*.jsonl' | wc -l` vs 库内
  `claude_code` distinct session 数——个别差异应为建库后新出现的空壳会话
  （仅 mode/cost-state 系统事件，无消息，零遗漏）；周期性重跑 `build` 即收新会话。
- **history.jsonl 不收**：Claude Code 的手敲命令史与主会话内容大量重合，边际价值低。
- **自指索引**：用 Claude Code 开发 agent-mirror 本身的会话也会被索引（project 归属
  开发时的 cwd）。若在 `~/agent-mirror/` 目录下开发，`exclude_projects: ["agent-mirror"]`
  可排除；在 home 目录下开发的会话归 `-home-<你的用户名>`，需权衡是否整目录排除。
  已知二次效应：检索输出被粘贴回对话后会成为新的命中源（污染自增强）。
- **中文 BM25 不可用**：SQLite trigram 最小 3 字符，中文 2 字关键词走 LIKE 加权检索；
  纯英文场景未来可启用 BM25 分支。

## 变更记录

- v1 (2026-10-19)：初始契约。工件A/B 格式、LLM 依赖矩阵、权重总表、已知边界（自指/中文BM25）
- v1.1 (2026-10-19 同日迭代)：analyze 新增人读报告双格式自动落盘（reports/<track>-<日期>.md/.html，
  零依赖 md→html）；矩阵补 confirm/diff/export 三行；新增产出物分层表（人读 vs 机器）

## 自测题（全部能答出来即通关）

1. 两个工件分别是什么、谁写谁读？物化解耦换来什么（对比直接三层直连）？
2. 同一条 assistant 回复在两层的命运？automated 消息在两张表里分别去哪？
3. ask 的三级降级路径（无索引/无命中/无 key）分别是什么行为？
4. 自指索引是什么现象？为什么 `exclude_projects: ["agent-mirror"]` 可能匹配不到你开发 agent-mirror 的会话？
5. 中文检索为什么走 LIKE 加权而不是 BM25？（trigram 的限制是什么）
6. features.json 和 evidence.md 住在同一个快照目录里——各给谁看、分别被谁消费？export 的 JSON 又是给谁的？

<details><summary>📖 参考答案（先自测再展开）</summary>

1. 工件A=messages.jsonl（build 写，知识库 ask 拼上下文 + 分析层特征提取读）；工件B=knowledge.sqlite（build 写，search/ask 读）。换来：47k 消息只扫源一次（十几秒），后续各层秒级读缓存；换源/并机只影响采集层。
2. assistant 有内容回复进 entries（解法的载体）但**不进分析层**（不是用户行为）；automated 进 auto_events（默认不搜），两层都不进画像。
3. 无索引→提示先跑 build 退出；无命中→明说检索无果不硬答；无 key→降级打印检索结果清单（不加戏）。
4. 用 Claude Code 开发 agent-mirror 本身的会话被索引；检索输出粘贴回对话后成为新命中源（污染自增强）。因为 project 取自开发时的 cwd——在 home 目录下开发时 project 是 `-home-<用户名>`，不含 "agent-mirror" 字样。
5. SQLite FTS5 trigram tokenizer 最小匹配 3 字符，而中文关键词以 2 字为主（编译/报错/精度），BM25 排不上用场；LIKE 多词 OR + 命中加权（长词×2）+ session 多样性在几十万行内毫秒级。
6. features.json 给机器（`diff` 读它做漂移对比）；evidence.md 给人（同报告按日期归档供回看）；export 的 JSON 也给机器——二次分析/画图原料（个人玩法），人读报告是 analyze 自动落盘的 reports/<track>-<日期>.md/.html。

</details>
