# AGENTS.md · Agent Mirror 入口契约

> 本文件给 AI agent（Claude Code / OpenClaw / Hermes）消费。
> 人也可以读，但优先级：本文 < `ARCHITECTURE.md`（架构争议时以它为准）。

## 这个仓库是什么

本地人格/职业画像 + 个人 AI 历史知识库。数据底座解析用户与各 agent 的全部会话，
三个功能域（知识库 / 性格 / 职业）独立消费。

## Agent 允许的操作

```bash
# 数据体检（只读，安全）
python3 scripts/am.py scan
python3 scripts/am.py scan --source claude_code --dry-run

# 知识库检索（只读）
python3 scripts/am.py search "<关键词>"        # Roadmap Phase 1
python3 scripts/am.py ask "<问题>"             # 需 LLM key，Roadmap Phase 1

# 画像（写入 profiles/，先读 config.yaml privacy 档位）
python3 scripts/am.py analyze --track personality   # 规则层证据面板（无 LLM）
python3 scripts/am.py analyze --track career
python3 scripts/am.py confirm --track personality   # 当事人校验（SJT 出题）
python3 scripts/am.py diff personality              # 快照漂移对比
```

## Agent 禁止的操作

1. **不上传任何原始会话内容到任何远端**——LLM 调用只允许经 config.yaml 的脱敏管线
2. **不修改 frameworks/ 静态框架层**（personality/ career/ 下的 YAML）——那是科学定版内容，
   修改需人工确认文献依据；evidence_map/ 允许追加（需附 basis 与 confidence 标注）
3. **不删除 profiles/snapshots/ 的历史快照**——只能追加
4. 不在报告中输出"你适合做 X 职业"这类断言，规范措辞见 career_signals.yaml 末尾

## 常用引用（agent 回答画像类问题时）

- BFAS 十维度定义：`frameworks/personality/bfas.yaml`
- RIASEC 六型与 AI 使用签名：`frameworks/career/riasec.yaml`
- 指标→维度映射（含置信度）：`frameworks/evidence_map/*.yaml`
- 架构与设计决策：`ARCHITECTURE.md`
