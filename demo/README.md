# Demo · 合成人设「夜航员」的完整报告

这个目录是一份**可复现的端到端 demo**：虚构人设 → 生成数据 → 真实管线 → 完整报告。
所有数据 100% 合成（项目名、消息、时间戳均为编造，与任何真人无关），
报告由 `scripts/am.py` 真实产出，**不是手写的假报告**。

## 看什么

| 文件 | 内容 |
|------|------|
| `report/portrait.html` | **画像全链路可视化**（workflow 泳道图：数据 → 建库 → 证据面板 → LLM 出题 → 校准 → 定稿，图上每个数字可回溯 export JSON） |
| `report/personality.html` | 性格镜像定稿（浏览器直接打开）：证据面板 → 校准状态 → 画像速览 → 原型 → 一句话自白 |
| `report/career.html` | 职业罗盘：RIASEC 六型证据 + 霍兰德代码（**ICR**）+ 价值观方向解读 |
| `report/scan.md` | 数据体检：投注分布（显示性偏好原始证据）+ 活跃时段 |
| `report/confirm-samples.md` | confirm LLM 出题样例：同一人设连续两轮，题目零重复 |
| `sample-data/nightowl.jsonl` | 96 会话 / 371 真人消息 / 141 天的合成数据 |
| `gen_sample_data.py` | 数据生成器（固定种子，自验证 14 项指标落点） |

> `portrait.html` 由维护者的 archify skill（个人环境，非本工具依赖）从 `export` JSON
> 渲染的附加可视化视图——工具本体的报告是 md/html 双格式、零依赖，不依赖 archify；
> 图上每个数字/档位均可回溯到 export JSON，不编造、不加戏。

## 人设档案：夜航员（nightowl）

独立开发者，昼伏夜出，凌晨是灵感高峰期。设计上刻意做成
「深夜型 / 高挫败 / 低礼貌 / 高探索 / 低计划」，与维护者本人的画像相反——
**用于演示不同行为数据测出不同画像**（区分性）：

| 指标 | 夜航员（demo） | 维护者本人（对照） |
|------|--------------|------------------|
| 深夜比 | 62% | 0% |
| 周末比 | 53% | 3.3% |
| 挫败密度 | 2.51 次/千字 | 0.12 次/千字 |
| 礼貌密度 | 0.31 次/千字 | 0.09 次/千字 |
| 备选寻求 | 1.78 次/千字 | 0.01 次/千字 |
| 原型 | 🛡️ 过控型 | 🛡️ 过控型 |
| 组合身份 | 「赛博福尔摩斯」（过控×研究 ICR） | 「精密工匠」（过控×现实 REI） |

> 两套数据的行为统计**全面相反**，但原型层收敛到同一档——三原型聚类本就是粗粒度底盘；
> 区分性落在**霍兰德代码（ICR vs REI）、组合身份、精力投注去向**（音画/游戏/自托管 vs
> 编译器/工具链/工作盘）。这正是分层设计的演示：原型给底盘（3+1 种），组合给身份
> （18 种），签名给指纹（4⁵ = 1024 种）——越往下越千人千面。

## 怎么复现

```bash
python3 demo/gen_sample_data.py     # 重新生成合成数据（固定种子，输出确定性一致）

# 在临时目录跑完整旅程（不污染你自己的 profiles/）：
mkdir /tmp/am-demo && cd /tmp/am-demo
mkdir scripts frameworks && cp <仓库>/scripts/am.py scripts/
cp -r <仓库>/frameworks/* frameworks/
cp <仓库>/demo/sample-data/nightowl.jsonl .
cat > config.yaml <<'EOF'
sources:
  claude_code: {enabled: false}
  openclaw: {enabled: false}
  hermes: {enabled: false}
  custom:
    - name: synth-nightowl
      type: jsonl
      path: /tmp/am-demo
      session_glob: "nightowl.jsonl"
      format:
        message_path: "$.messages[*]"
        role_field: role
        role_user_value: user
        role_assistant_value: assistant
        text_field: text
        timestamp_field: created_at
        project_field: project
EOF
python3 scripts/am.py scan                       # 体检：371 条真人消息 / 141 天
python3 scripts/am.py analyze --track personality  # 初稿（纯规则，零 LLM）
python3 scripts/am.py confirm --track personality  # 校验出题（有 key 时 LLM 现场出题，每轮不同）
python3 scripts/am.py analyze --track personality  # 定稿（校准状态 + 原型 + 自白）
```

> 规则层完全确定性：同样的合成数据 + 同样的 confirm 答案 → 同样的报告。
> LLM 出题文本每轮不同（这是特性不是缺陷），但 SJT 投票与证据确认的落盘结构一致。

## 诚实声明

- 报告头部照实标注「v0 方法说明：无人群常模对照，不输出绝对分数」
- 生成器内置 14 项指标自验证（深夜比/挫败/祈使/礼貌…目标区间），越界即退出非零
- 「夜航员」的 LLM confirm 答案由维护者按人设代答（合成人设没有真人当事人）；
  demo README 不掩饰这一点——你自己的报告只有你自己能校准
