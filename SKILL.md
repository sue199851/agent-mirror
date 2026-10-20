---
name: agent-mirror
description: |
  个人 AI 交互数据分析：把用户与 Claude Code / OpenClaw / Hermes 等 agent 的全部对话
  变成三类能力——①历史知识库检索（search/ask）②性格画像（BFAS 证据面板+当事人校验）
  ③职业罗盘（RIASEC 兴趣代码）。全程本地，数据不上传。
  当用户问"我之前怎么解决的 X"、"看看我的 AI 使用画像"、"分析我的性格/职业倾向"、
  "我的数据体检"时使用。
  TRIGGER: agent-mirror, 我的画像, AI使用分析, 检索历史会话, 性格画像, 职业罗盘
allowed-tools:
  - Bash(python3 *agent-mirror/scripts/am.py *)
---

# Agent Mirror

仓库本体在 `~/agent-mirror`（或用户 clone 的位置）。本 skill 是它的入口。

## 命令速查

```bash
M=python3 ~/agent-mirror/scripts/am.py   # 路径按实际 clone 位置调整

$M scan                                  # 数据体检（human/automated 分轨 + 时段分布）
$M build                                 # 物化 + FTS5 建库（隔段时间再来先跑这个）
$M search "<关键词>"                     # 纯本地检索历史会话
$M ask "<问题>"                          # 检索 + LLM 归纳（无 key 自动降级）
$M analyze [--track personality|career] [--mode rules|llm|hybrid]
$M confirm [--track personality|career] [--mode llm]   # 自适应校验：SJT+证据确认+盲区（题数 4~6 不固定）
$M diff [personality|career]             # 快照漂移对比（需 ≥2 次快照）
$M export [--track personality|career]   # 报告数据 JSON（画图原料，零 LLM）
$M scan --inspect-hour H                 # 诊断：某时段真人消息样本（查数据污染）
```

## 出图（个人玩法 · 非工具功能）

用户要"把结果画成图"时：跑 `export` 拿全量结构化 JSON，用 archify skill
（本机 `~/.claude/skills/archify`，需要 node）画 workflow 泳道图，validate 过
**9/9 showcase** 后 deliver 成自包含 HTML。渲染零 LLM，不消耗 API key。
铁律：图上每个数字/档位必须能回溯到 export JSON——不编造、不加戏。
（archify 是用户个人环境依赖，README 不宣传；工具本体的报告 = md + html 双格式，零依赖。）

## AI 使用指南

1. 用户要"查历史/上次怎么解决" → `search`/`ask`（ask 前确认 build 过）
2. 用户要"画像/分析我" → `analyze`，报告 stdout + 自动落盘 `reports/<track>-<日期>.md/.html`
   （人读，浏览器直开）+ snapshots/（evidence.md 存档 + features.json 机器格式，diff 用）
3. 报告解读规范（来自 METHODOLOGY，不可违反）：
   - 大五是**维度制**：只说档位与证据（"倾向具备/证据倾向/自评"），禁止"你是 X 型人"
   - 类型制结论只在职业域：霍兰德代码（如 REC）
   - 职业措辞："你的行为更接近 X 型环境"，禁止"你适合做 X"
   - v0 无人群常模：主打纵向自我对照（和上次快照比），不做横向人群比较
4. 用户答完 confirm 后 → 重跑 analyze 展示「校准状态」+「画像速览」节
5. 数据异常（某源 0 条 / 时段分布怪）→ `scan --inspect-hour` 自证，别猜
6. 隐私红线：原始会话内容永不上传 LLM（privacy 档位在 config.yaml）；profiles/ 永不入 git
7. 用户要"图/网页/可分享报告" → 上文「可视化导出」三步：export → archify 画 → deliver
