# 贡献指南

感谢你想贡献！这个项目的核心资产分三层，**每层的准入规则不同**——先看你要改的是哪层。

## 三层的准入规则

| 层 | 位置 | 改动门槛 | PR 要求 |
|----|------|---------|---------|
| 静态框架层 | `frameworks/personality/` · `frameworks/career/` | **高**：科学定版内容 | 必须附文献出处（论文 + 年份）；维度定义类改动需在 issue 先讨论 |
| 证据映射层 | `frameworks/evidence_map/` | **中**：可迭代 | 每条新映射必须带 `basis`（依据）与 `confidence`（high/medium/low）标注 |
| 工具代码 | `scripts/am.py` 等 | **常规** | 复现步骤 + 受影响的命令行为说明 |

## 最欢迎的四类贡献

### 1. 自动注入模式（门槛最低、收益最大）

你的 agent 数据里发现了新的**定时任务/系统注入模式**（会污染人格推断的"假真人"消息）？
提 PR 往 `config.example.yaml → sources.automated_patterns` 追加默认正则：

```yaml
automated_patterns:
  - '^\[cron:'          # OpenClaw 定时任务注入
  - '^Read HEARTBEAT\.md'   # OpenClaw 心跳注入
  - '^【你的模式】'      # ← 你的贡献
```

一个模式，全体用户的数据质量受益。用 `am.py scan --inspect-hour H` 验证后再提。

### 2. 证据映射表新条目

发现某行为指标与某人格维度有文献支持的相关？往 `frameworks/evidence_map/behavioral.yaml`
加一条（字段结构见现有条目）：

```yaml
- indicator: your_indicator      # 指标名（需已实现或标注 v0.2）
  dimension: C1
  direction: +
  confidence: medium             # high=文献直接支持 / medium=类比推断 / low=本地假设
  basis: 一句话依据（含出处）
```

**禁止**：无依据的"感觉相关"；confidence 与依据强度不匹配的标注。

### 3. 新数据源连接器

不需要写代码——先试 CONFIG.md 的零代码协议（jsonl/json/jsonl_events 字段映射）。
真接不上（数据库/二进制格式）再写 Python 连接器，参考 `scripts/am.py` 内置三个连接器
的接口（`conn_<name>(src_cfg, on_msg, on_fail)`），并更新 CONFIG.md 的"不适用本协议"一节。

### 4. 报告与文档修正

文档里每篇方法论/架构文档结尾都有自测题——修改了实质内容请同步更新题目与答案。

## 硬性红线（违反直接 close）

1. **任何 PR 不得包含真实会话数据**：`profiles/`、`index/`、`reports/`、`config.yaml`
   已被 .gitignore 挡住——别用 `git add -f`。demo 数据必须是 `demo/gen_sample_data.py`
   生成或同等的显式合成数据
2. **不修改 frameworks/ 静态框架层而不附文献依据**（见上表）
3. **不做"你适合做 X 职业"式的断言措辞**——报告规范见 `frameworks/career/career_signals.yaml` 末尾
4. 上游用户的真实画像截图可以贴 issue（你自己的、自愿的），但默认建议脱敏

## 开发环境

- Python ≥ 3.8，零第三方依赖（PyYAML 可选）
- 无测试框架（v0）：改动请附「复现步骤 → 期望输出 → 实际输出」；涉及数据管线的改动
  用 `demo/gen_sample_data.py` 的合成数据自验证
- 自测回归：`scan` → `build` → `search` → `analyze` → `confirm --answers` → `analyze`
  在合成数据上全链路跑通即视为不回归
