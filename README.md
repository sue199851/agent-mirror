# Agent Mirror · 你的 AI 交互数据的三重价值

> 你和 Claude Code / OpenClaw 的每一次对话，都是一份比日记更私密的行为日志。
> 没人要求你的时候，你把 AI 用在哪个项目、几点提问、怎么追问、怎么发火——
> 这是比任何问卷都诚实的**显示性偏好**数据。Agent Mirror 把它变成三样东西：
> 一座可检索的知识库、一面性格镜像、一个职业罗盘。**全程本地，数据永不上行。**

**先看效果**：合成人设「夜航员」的端到端 demo（虚构数据 + 真实管线，可复现）→
[🖼️ 画像全链路可视化](demo/report/portrait.html) · [性格报告](demo/report/personality.html) ·
[职业报告](demo/report/career.html) · [LLM 出题样例（每轮不同）](demo/report/confirm-samples.md) · [demo 说明](demo/README.md)

| 功能域 | 回答的问题 | 方法 | 命令 |
|--------|-----------|------|------|
| 📚 **知识库** | 我经历过什么？上次怎么解决的？ | 全文检索 + LLM 问答 | `am.py search / ask` |
| 🪞 **性格镜像** | 我是谁？我的行为模式说明什么？ | 大五 + BFAS 10 维度 | `am.py personality` |
| 🧭 **职业罗盘** | 我自愿把精力投在哪？什么环境让我进入心流？ | RIASEC + 工作价值观 | `am.py career` |

> 性格与职业画像刻意分为两个独立功能：科学体系不同（特质心理学 vs 职业心理学），
> 可单独运行、单独迭代、单独分享。方法学白皮书见 [METHODOLOGY.md](METHODOLOGY.md)。

## 三条原则

1. **本地优先**：原始数据永不离开本机；LLM 只接收脱敏后的统计摘要与抽样片段（可完全关闭）
2. **科学框架**：不用 MBTI 玄学做主轴，用心理测量学金标准（大五/BFAS/RIASEC），每个分数附行为证据引用
3. **总库可迭代**：框架层（科学定版）+ 证据映射层（随数据校准）+ 个人画像层（时间序列快照）三层分离

## 环境要求

- **Python ≥ 3.8**，零第三方依赖（单文件 `scripts/am.py`，stdlib only）
- 可选 **PyYAML**：读 `config.yaml` 与 `frameworks/` 需要（缺失时内置默认配置照常可用）
- 纯检索/画像功能对 SQLite 版本无要求；FTS5 trigram 需要 SQLite ≥ 3.34（更老自动降级为 LIKE 兜底）

## 使用手册

**核心流程（严格顺序）**：`build → analyze（初稿）→ confirm（校验）→ analyze（定稿）`
报告头部标注状态：📝 初稿 / ✅ 已校准 —— confirm 是结论定稿的必经站。

### 路线 A · 不用 LLM（零配置，clone 即用）

```bash
python3 scripts/am.py scan               # ① 体检：三源扫到多少数据
python3 scripts/am.py build              # ② 建库：物化 + FTS5 索引
python3 scripts/am.py search "精度"      # ③ 随时检索历史会话（秒回）
python3 scripts/am.py analyze            # ④ 画像【初稿】：证据面板+原型+签名
python3 scripts/am.py confirm            # ⑤ 自适应校验：情景题+证据确认+盲区自评（无 key 用固定题库，一题一题答，q 放弃）
python3 scripts/am.py analyze            # ⑥ 【定稿】：校准状态+速览+组合身份+自白
```

**报告在哪**：每次 analyze 自动落盘 `reports/<track>-<日期>.md`（源文件）+ `.html`
（自包含网页——浏览器双击就开、可打印 PDF、可直接发人）。纯本地生成，零依赖零 LLM；
同日重跑 = 初稿自动更新为定稿。

### 路线 B · 用 LLM（先配一次 key，体验升级）

```bash
# 一次性配置：config.yaml 的 llm 段 + key（export AM_LLM_API_KEY=... 或 ~/.am-llm-key 文件）
python3 scripts/am.py ask "我上次怎么调 OOM 的"   # LLM 归纳回答（引用来源，不足处明说）
python3 scripts/am.py analyze --mode hybrid       # 双层证据：规则 + LLM 解读（默认只见统计）
python3 scripts/am.py confirm                     # 同⑤的命令：检测到 key 自动 LLM 现场出题（贴合你的真实场景，每轮不同）；--mode rules 可强制题库
# 解锁 BARS 评分（LLM 读脱敏样本打 1-5 分）：config.yaml → privacy.llm_exposure: redacted
```

### 隔了一段时间再来（周期性）

```bash
python3 scripts/am.py build && python3 scripts/am.py analyze    # 收新数据，出初稿
python3 scripts/am.py confirm                                    # 再校验一轮（数据变了认知也会变）
python3 scripts/am.py analyze && python3 scripts/am.py diff personality
```

> 一句话：**build 进数据 → analyze 出初稿 → confirm 校认知 → analyze 定稿 → diff 看漂移**

### 配置（只有两件事，都不必配）

| 配置 | 必要性 | 说明 |
|---|---|---|
| 数据源路径 | 默认即用 | 三默认源标准位置开箱即用；装在别处改 `config.yaml → sources.*.paths`（支持多目录，见 CONFIG.md）|
| LLM key | 完全可选 | 不配 = 路线 A 全功能；配了 = 路线 B 增强（`cp config.example.yaml config.yaml` 起步）|

### 命令速查

| 命令 | 作用 | LLM | 写入 |
|------|------|-----|------|
| `scan` | 数据体检 + 分轨 + 时段分布 | ❌ | 报告（stdout/--report） |
| `build` | 物化 messages.jsonl + FTS5 索引 | ❌ | index/ |
| `search "<kw>"` | 中文分词检索历史会话 | ❌ | — |
| `ask "<问题>"` | 检索 + 归纳回答 | 可选 | — |
| `analyze` | 双域证据面板 + 快照 | 可选 | reports/*.md+html · profiles/*/snapshots/ |
| `confirm` | 自适应校验：SJT 情景 + 证据确认 + 盲区自评（有 key 时 LLM 现场出题、贴合本人画像且每轮不同；无 key 题库兜底） | 可选 | profiles/*/calibration.yaml |
| `diff <track>` | 快照漂移对比 | ❌ | — |
| `export <track>` | 报告数据 JSON（机器格式：二次分析/画图原料） | ❌ | reports/export-*.json |
| `scan --inspect-hour H` | 诊断：某时段真人消息样本 | ❌ | — |

> `chat`（多轮会话）与 `calibrate`（BFI-2 自评对照）在 Roadmap。

### 产出物在哪（人读 vs 机器）

| 文件 | 给谁 | 说明 |
|------|------|------|
| `reports/<track>-<日期>.md` + `.html` | **人读** | analyze 自动落盘；html 浏览器直开/打印 PDF/发分享 |
| `profiles/<track>/snapshots/<日期>/evidence.md` | 人读存档 | 同报告按日期归档，供回看历史 |
| `profiles/<track>/snapshots/<日期>/features.json` | 机器 | 指标快照，`diff` 漂移对比的原料（不必人读） |
| `reports/export-<track>-<日期>.json` | 机器 | `export` 全量结构化数据（二次分析/画图原料） |

## 安装为 Claude Code skill（可选，一行命令）

仓库自带 `SKILL.md`——clone 之后软链一下，就能在任何 Claude Code 会话里用自然语言触发
（"看看我的 AI 使用画像"、"我之前怎么解决的 X"）：

```bash
git clone <本仓库> ~/agent-mirror
ln -s ~/agent-mirror ~/.claude/skills/agent-mirror
```

新开会话即生效。不想用了删掉软链即可，无残留。

## 支持的数据源

| 源 | 默认路径 | 状态 |
|----|---------|------|
| Claude Code | `~/.claude/projects/**/*.jsonl` | ✅ 已支持 |
| OpenClaw | `~/.openclaw/agents/*/sessions/*.jsonl` | ✅ 已支持 |
| Hermes | `~/.hermes/sessions/*` (jsonl + json) | ✅ 已支持 |
| 自定义 agent | 任意 | 📋 按 `CONFIG.md` 协议**零代码**接入 |

## 画像体系（总库）

> **方法学**：从原始数据到画像分数的每一步依据，见 **[METHODOLOGY.md](METHODOLOGY.md)**
> （四层管线：数据分轨 → 三类特征 → 证据加权+BARS → state/trait 聚合，每层带文献锚点 + 局限性声明）

- **性格域框架**：大五 5 域 → BFAS 10 维度 → **人格原型层**（坚韧/过控/失控三型，
  Asendorpf 2001；Gerlach 2018 在 N≈150 万复现）——维度给证据，原型给整体
- **职业域框架**：RIASEC 六型兴趣 + O*NET 六工作价值观 + 职业 RIASEC 映射表
- **组合身份**：3 原型 × 6 兴趣首型 = 18 种组合身份（如"坚韧型×现实取向"），
  外加每人唯一五维签名码（如 `O+ C+ E+ A+ N`）——千人千面，每层可回溯证据
- **场景分层**：工作/生活情境分类（人×情境变量）——生活情境社会表演少，
  语言信号权重更高；样本不足时诚实不出表
- **动态机制**：trait（特质，年尺度漂移）与 state（状态，周尺度波动）双轨记录，快照可 diff
- **证据映射**：行为指标 → 性格维度 / 职业信号的映射表，每条标注心理学依据与置信度，随数据迭代校准

详见 `frameworks/`（框架总库）与 `ARCHITECTURE.md`（架构定版）。

## 目录

```
agent-mirror/
├── README.md               # 本文件
├── ARCHITECTURE.md         # 架构定版（三域一底座 / 数据流 / 总库迭代机制）
├── METHODOLOGY.md          # 方法论白皮书（四层管线 + 文献锚点 + 局限性）
├── DATAFLOW.md             # 三层输入输出契约（工件A/B 格式、LLM 依赖矩阵）
├── AGENTS.md               # agent 入口契约（给 AI 消费本库用）
├── CONFIG.md               # 自定义数据源配置协议（零代码接入）
├── SKILL.md                # Claude Code skill 入口（软链到 ~/.claude/skills/ 即用）
├── config.example.yaml     # 配置模板（复制为 config.yaml；真实配置不入库）
├── frameworks/             # 【评分标准】科学框架定义，随仓库分发（进 git）
│   ├── personality/        #   BFAS 十维 · 大五 30 面
│   ├── career/             #   RIASEC · 工作价值观 · 职业映射
│   └── evidence_map/       #   行为指标→维度映射（改这里 = 改打分规则）
├── index/                  # 【知识库本体】build 的产物（不进 git）
│   ├── knowledge.sqlite    #   FTS5 检索库 ← search / ask 查这个
│   └── messages.jsonl      #   全部消息物化缓存 ← analyze 吃这个
├── profiles/               # 【最终结论】画像快照（不进 git，永不外传）
│   ├── personality/snapshots/日期/evidence.md   ← 性格结论（含画像速览）
│   ├── career/snapshots/日期/evidence.md        ← 职业结论（含霍兰德代码）
│   └── */calibration.yaml  ← confirm 答案存档
├── scripts/am.py           # CLI 入口（单文件，内置三源连接器）
└── reports/                # 【人读报告】scan.md 体检 · personality-portrait.html 画像图
```

## 隐私立场

AI 对话日志比日记更私密。因此：分析全程默认本地；任何 LLM 调用前先脱敏
（可配置：只发统计指标 / 发脱敏片段 / 完全禁用）；本仓库永远不接受用户数据上传。

## Roadmap

- [x] Phase 0 · 数据体检 scan（三源扫描 + human/automated 分轨 + 自证工具）
- [x] Phase 1 · 知识库域（物化缓存 + FTS5 索引 + search/ask）
- [x] Phase 2 · 行为指标引擎（语言词典 + 行为特征 + 投注归一 + 任务谱系）
- [x] Phase 3 · 证据面板 + LLM 层 + confirm 当事人校验（informant validation，LLM 出题每轮不同）
- [x] Phase 4 · 快照 diff + 漂移报告（state/trait 双轨解读）
- [x] 自定义源零代码接入（CONFIG.md 协议：jsonl / json / jsonl_events 字段映射）
- [x] 发布三件套：MIT License · 可复现 demo（[demo/](demo/README.md)）· 双语 README（[README.en.md](README.en.md)）
- [ ] Phase 5 · calibrate（BFI-2 自评对照，分数升格 + 效度闭环）
- [ ] chat 多轮会话（当前每轮独立 ask）
- [ ] 网页版导出连接器（ChatGPT / Claude 导出 JSON）

## 自测题（全部能答出来即通关）

1. 三域分别回答什么问题？性格域和职业域为什么必须拆开（两个理由）？
2. 标准旅程的命令顺序是什么？为什么 build 必须在 search 之前（两层的工件依赖）？
3. "本地优先"落到 LLM 调用上是哪三档 privacy 配置？默认档 LLM 能看到什么？
4. 显示性偏好比问卷诚实的依据是什么？哪个命令的输出是它的原始证据？
5. confirm 的题目分哪三类？哪类交给 LLM 写、为什么另外两类必须代码生成？答案回写后对下次 analyze 产生什么影响？
6. demo 里「夜航员」的画像和维护者本人的画像在哪两层拉开了差异（原型/签名 vs 霍兰德代码）？

<details><summary>📖 参考答案（先自测再展开）</summary>

1. 知识库=我经历过什么（FTS5 检索）；性格=我是谁（大五/BFAS）；职业=我自愿把精力投在哪（RIASEC/价值观）。拆开理由：①科学体系不同（特质心理学 vs 职业心理学），性格→职业仅弱预测力；②两份报告可独立分享。
2. scan → build → search/ask → analyze → confirm/diff。build 产出工件A(messages.jsonl)+工件B(knowledge.sqlite)，search 只读工件B——没有 B 检索无源。
3. stats_only（默认，只发统计指标）/ redacted（统计+脱敏片段）/ off（禁用 LLM）。默认档 LLM 只见数字与比例，永不见原文。
4. 问卷是 stated preference（有社会期望偏差），自愿投注是 revealed preference（Samuelson；Baumeister 2007 论自我报告局限）。原始证据 = scan 的"真人项目投注分布"表。
5. 三类：SJT 情景题 + 证据确认题 + 盲区自评题。SJT 交给 LLM 写（场景创作是它擅长的，且题面贴合当事人真实画像、每轮不同）；证据确认和盲区题必须代码生成——题面里的指标名/数值/维度全部来自真实面板，经 LLM 手有幻觉数字风险。回写 calibration.yaml → 下次 analyze 报告出现校准状态节：confirmed 标注确认、discount 标注否认原因（context / blindspot 两种修正方向不同）、stated_* 成为盲区维度的人工证据、SJT 行为票跨轮累计。
6. 原型/签名层：过控型 `O+ C+ E+ A N+` vs 坚韧型 `O+ C+ E A N`；霍兰德代码层：ICR（研究型主导）vs REI（现实型主导）——同一套管线、两套行为数据，三层结论全部区分开。

</details>
