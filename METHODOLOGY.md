# Agent Mirror 方法论白皮书

> 本文档回答一个问题：**从你与 AI 的原始对话，到一份性格/职业画像，中间的每一步凭什么可信？**
> 每个设计选择都标注文献依据；文末有诚实的局限性声明——权威性来自出处，也来自承认边界。

## 0. 总览：四层管线

```
原始会话数据 (JSONL/JSON, 多源)
   │  L0 数据层：统一消息模型 + 真人/自动分轨
   ▼
可信消息流（真人行为样本）
   │  L1 特征层：语言特征 · 行为特征 · 投注特征
   ▼
特征向量（每用户 × 每时间窗）
   │  L2 映射层：证据映射表加权合成 + LLM 行为锚定评分 → 融合
   ▼
维度分数（BFAS 十维 / RIASEC 六型 / 价值观六项）
   │  L3 聚合层：state 短窗 / trait 长窗分离
   ▼
画像快照（当前 + 历史序列，可 diff）
```

## 1. L0 数据层：先保证数据是真的

**原则**：人格推断的垃圾进垃圾出效应极其严重——定时任务（cron）和系统注入（heartbeat）
的行为模式会被误认为用户人格。本项目实测发现三类污染（cron 前缀注入、heartbeat 注入、
未来未知模式），过滤规则在 `config.yaml → automated_patterns` 开放配置，
并内置 `--inspect-hour` 自证工具（按小时抽查真人消息前缀分布，任何用户可验证自己的数据质量）。

**依据**：数字足迹研究对数据来源真实性的基本要求；本项目实践的独特贡献是把"注入模式库"
作为可迭代的开源资产（用户发现新模式 → 提 PR 增加默认规则 → 全体用户受益）。

## 2. L1 特征层：处理哪些数据

### 2.1 语言特征（用户消息文本）
- 情绪词（正性/负性/愤怒/焦虑）、第一人称/第二人称使用、成就词、认知过程词、
  礼貌修饰、命令式句法、问句类型（开放 vs 封闭）、消息长度与结构化程度
- **依据**：LIWC（Linguistic Inquiry and Word Count）体系——Pennebaker & King (1999)
  确立了功能词与人格的稳定相关；Schwartz et al. (2013, PNAS) 用开放词汇法在 75K 样本上
  验证了语言→大五的预测力。中文适配：开源中文情感词典（如大连理工情感词汇本体库）+
  LLM 语义分类器双轨（词典可复现，LLM 捕捉词典外表达）。

### 2.2 行为特征（交互元数据，不看内容）
- 时段分布、深夜比、周末比、会话长度分布、追问率（得到答案后继续深挖）、
  纠错率（指出 AI 错误）、并行会话度、工具选择分化、重复提交相似度
- **依据**：**Stachl et al. (2020, PNAS)** 是本层的方法论基石——仅用手机使用行为
  （app 模式/通话/时间戳，完全不看内容）即可预测大五（r≈0.3）。
  AI 交互元数据是同构信号：怎么用，比说了什么更难伪装。

### 2.3 投注特征（自愿项目分布）
- 剔除工作安排的干扰后，用户"没人要求时自愿把 AI 用在哪"的时间投注分布
- **依据**：显示性偏好（revealed preference, Samuelson 1938）——行为选择揭示真实偏好，
  优于问卷的陈述性偏好；Baumeister et al. (2007) 系统论证了心理学中自我报告的局限。
  这是职业域（RIASEC）的第一信号源。

## 3. L2 映射层：权重从哪来

### 3.1 证据映射表（`frameworks/evidence_map/`）
每条映射：`指标 → 维度，方向，置信度，依据`。合成公式：

```
维度分数 = Σ(wᵢ × zᵢ) / Σwᵢ
wᵢ = 置信度权重(high=1.0 / medium=0.6 / low=0.3) × 特征类型先验
```

**特征类型先验**来自元分析：Azucar, Marengo & Settanni (2018) 对 35 项数字足迹研究的
元分析显示，数字足迹→大五的平均预测效度 r≈0.3–0.4，其中**行为特征的平均效度略高于
语言特征**——因此行为特征（2.2）的先验权重 ×1.2，语言特征 ×1.0（校准后由回归系数替换，见 §5）。

### 3.2 LLM 行为锚定评分（BARS）
对每个 BFAS 维度，LLM 收到：维度定义 + **行为锚定量表**（Behaviorally Anchored
Rating Scale，组织行为学标准方法：1-5 每级配具体行为描述）+ 脱敏抽样的会话片段。
LLM 输出分数 + 证据引用（会话 ID + 原文片段）。

**依据**：BARS 是工业组织心理学的成熟评估方法（Smith & Kendall 1963）。
近期预印本研究（2023-2024，该方向结论更新快、尚未定版）报告 LLM 从文本推断大五
与人类评分一致性大致在中上区间。本层在设计上**不依赖该区间成立**：定位是规则层
的补充信号，融合权重 α 由 calibrate 实测收敛决定（见 §5.2），届时以本地回归
结果为准。缓解 LLM 评分偏差（趋中/位置偏差）：多样本独立评分 + 锚点描述强制对齐。

### 3.3 双层融合（设计目标，v0 尚未落地）

```
维度分数 = α × 规则层 + (1-α) × LLM层      初始 α=0.5
```
规则层可复现、零成本、全本地（α=1 时系统完全无 LLM 依赖）；LLM 层语义敏感。
**当前 v0 状态**：规则层输出证据面板（无绝对分数），LLM 层输出统计解读文本，
两者在报告中并列呈现但未做数值融合；α 与分数化在 calibrate（§5.2）落地后一并实装。

## 4. L3 聚合层：state 与 trait 分离

- **state（状态）**：7 天滚动窗口统计（深夜比、强度、主题集中度）——捕捉工作压力/冲刺
- **trait（特质）**：指数移动平均（EMA，**半衰期 90 天**）——人格特质的时间常数以年计，
  90 天半衰期让季节性波动衰减、长期漂移保留
- **依据**：Epstein (1979) 跨情境聚合理论——单次行为与人格相关弱，聚合后信效度显著
  提升；state-trait 分离是人格测量学标准框架。**这也是纵向报告的方法学优势**：
  192 天的聚合数据比 15 分钟问卷抽样更接近"行为的稳定结构"。

## 5. 校准层：让用户自己验证（信任锚点）

### 5.1 v0.1 已实现：confirm 当事人校验（informant validation）

```
am.py confirm → 自适应出题（题数 4~6 不固定，缺什么补什么）：
  · SJT 情景判断 ×2 —— 行为选择给 BFAS 维度投票（每选项 1~3 维）
  · 证据确认题 ×2 —— 强证据维度按命中数降序；当事人确认 / 否认（情境性 / 盲区）
  · 盲区自评 ×1 —— 零证据维度，stated 高/中/低
  （LLM 路线：LLM 只写 SJT 情景题——性格域 votes 投 BFAS 维度、职业域 votes 投
   霍兰德六型（RIASEC），题面同样贴合本人投注领域；证据确认/盲区题由代码确定性生成，
   数字全部来自真实面板——题型混合靠构造保证，不经 LLM 手）
→ 档位合成（synthesis_rows）：
  倾向具备 = 数据证据 + 当事人双重支持（本人确认，或 SJT 行为票 ≥2——
  ≥2 题情景选择一致投给该维度 = 行为层面的确认）；否认权归当事人，
  discount 一票否决 SJT 强档
→ 原型判定只认强档（倾向具备 / 自评高）：证据倾向（纯数据未校验）
  不进入类型判定——类型层宁缺毋滥，判不上就输出「未定型」
```

**依据**：informant report 是人格测量的经典效度来源（旁证优于纯自陈）；
SJT 以行为选择绕开自我报告的社会期望偏差（McDaniel et al. 2001）。
已答过的维度不重出（confirmed/stated/discounted 均跳过），被否认的维度
换行为角度由 SJT 重新接近——校准是持续对话，不是一次性问卷。

### 5.2 规划中：calibrate（BFI-2 自评对照）

```
am.py calibrate → 引导完成 BFI-2 自评（60 题，公开量表，约 15 分钟）
              → 自评分 × 系统预测分 做相关/回归
              → 输出收敛度报告 + 把回归系数回写映射权重（个性化校准）
```

### 5.3 类型层：原型 × 签名 × 组合身份（差异化引擎）

维度档位是证据主体，类型层是它的"可传播摘要"——三层结构，层层可回溯：

**① 大五原型（3 + 未定型兜底）**：resilient 稳健 / overcontrolled 过控 / undercontrolled 冲动。
本项目沿用三原型体系，依据是 Asendorpf 系列研究（Asendorpf et al. 2001 三原型聚类）与
Block & Block (1980) 自我控制理论传统。争议如实交代：Gerlach et al. 2018（*Nature Human
Behaviour*，N≈1.5M 成年大样本）仅部分复现三原型，另提出 average / reserved /
self-centered / role-model 四类型——三原型在大样本下的稳健性存在争议，本项目
「判不上强档即输出未定型、宁缺毋滥」的设计正是对该争议的工程化回应。判定规则自参照
（prototypes.yaml）：N 域稳定性 × C 域强度 × E 域活跃度的**强档**组合；强档 = 本人确认 /
SJT 行为票 ≥2 / 自评高——判不上就输出「未定型」，宁缺毋滥（诚实结论优先于好看结论）。

**② 五维签名（4⁵ = 1024 种）**：每域取两 aspect 中更强档位，压缩为一位四态码——
`X+`（高）/ `X`（中）/ `x`（初步）/ `x−`（低），如 `O+ C+ E A N`。
MBTI 式的传播友好，但两点本质不同：每一位直接映射该域两个 aspect 的档位行（可证伪）；
四态保留中间态，没有 MBTI 的二分强制问题。

**③ 组合身份（3 原型 × 6 兴趣首型 = 18 种）**：人格原型（你怎么做事）× 霍兰德
首型（你愿意做什么）——两个**独立测量域**的交叉，差异化比任何单一类型高一个量级。
每条双层格式：「梗名 —— 俏皮人设 ｜ 严肃洞察」——左半负责传播（发朋友圈），
右半负责真实（写进自我认知），真实性和俏皮性不是折衷而是分层。

**差异化总量**：18 组合 × 1024 签名 = **18,432 种"身份 + 指纹"**，再叠加档位
依据列因人而异——"人多了测出来才有差异化"的数学保证。三层各自独立成立：
只信原型看底盘，只信签名看指纹，全信看组合身份。

**依据**：BFI-2（Soto & John, 2017）是大五测量的现代金标准量表且公开可用。
校准不是可选项而是信任机制：用户亲手验证"系统说的我 vs 我认知的我"的偏差，
收敛度报告本身诚实展示方法的能力边界。

## 6. 职业域的独立立论（为什么不用性格推职业）

性格→职业绩效只有弱预测力（Barrick & Mount 1991; Judge et al. 元分析：尽责性普适
r≈0.2-0.3，外向性仅预测销售/管理类），所以职业域立论于**显示性偏好**：
投注分布 + 任务类型谱系 → RIASEC 兴趣代码（Holland 1997）× O*NET 职业映射。
报告措辞强制为"你的行为更接近 X 型环境"（环境匹配陈述，可证伪），
禁止"你适合做 X"（人格断言，过度承诺）。

## 7. 局限性声明（权威性的另一半）

1. **效度上限**：间接行为推断的元分析效度 r≈0.3–0.4，低于专业量表自评。
   定位是**自我探索工具**，不是心理诊断。报告分数标注"探索性"。
2. **无常模对照**：单用户没有人群基准，绝对分数意义有限。报告主打**纵向自我对照**
   （你 vs 三个月前的你）——这在方法学上更干净，也是快照 diff 的设计动机。
3. **情境特异性**：对 AI 的行为 ≠ 对人的行为（Mischel 情境论）。画像标注
   "AI 协作情境下的人格表达"（domain-specific），不做全人格断言。
4. **中文语言特征**：LIWC 原版为英文，中文适配依赖开源词典 + LLM，效度需要
   calibrate 数据持续验证。
5. **LLM 评分偏差**：趋中/位置偏差存在，靠多样本 + BARS 锚定缓解，不假装消除。

## 参考文献

- Pennebaker, J. W., & King, L. A. (1999). Linguistic styles: language use as an individual difference. *Journal of Personality and Social Psychology*, 77(6).
- Kosinski, M., Stillwell, D., & Graepel, T. (2013). Private traits and attributes are predictable from digital records. *PNAS*, 110(15).
- Schwartz, H. A., et al. (2013). Personality, gender, and age in the language of social media: An open-vocabulary approach. *PNAS*, 110(15).
- Youyou, W., Kosinski, M., & Stillwell, D. (2015). Computer-based personality judgments are more accurate than those made by humans. *PNAS*, 112(4).
- Epstein, S. (1979). The stability of behavior: I. On predicting most of the people much of the time. *Journal of Personality and Social Psychology*, 37(7).
- Baumeister, R. F., et al. (2007). Psychology as the science of self-reports... *Perspectives on Psychological Science*, 2(2).
- Smith, P. C., & Kendall, L. M. (1963). Retranslation of expectations: An approach to the construction of unambiguous anchors for rating scales. *Journal of Applied Psychology*, 47(2).
- Barrick, M. R., & Mount, M. K. (1991). The Big Five personality dimensions and job performance. *Personnel Psychology*, 44(1).
- Holland, J. L. (1997). *Making Vocational Choices* (3rd ed.).
- Soto, C. J., & John, O. P. (2017). The next Big Five Inventory (BFI-2). *Journal of Personality and Social Psychology*, 113(1).
- Block, J., & Block, J. H. (1980). The role of ego-control and ego-resiliency in the organization of behavior. In W. A. Collins (Ed.), *Minnesota Symposia on Child Psychology: Development of cognition, affect, and social relations* (Vol. 13). Hillsdale, NJ: Erlbaum.（自我控制/自我弹性理论，三原型的理论源头）
- Asendorpf, J. B., Caspi, A., & Hofstee, W. K. B. (2001). The geography of adult personality types. *European Journal of Personality*, 15.（三原型聚类）
- Gerlach, T. M., Farb, N. V. S., & Crone, E. A. (2018). Digging into human nature: Revisiting the robustness of personality prototypes. *Nature Human Behaviour*, 2.（N≈1.5M 大样本：仅部分复现三原型，另提出四类型）
- Stachl, C., et al. (2020). Personality prediction from smartphone data. *PNAS*, 117(30).（行为特征层基石）
- Azucar, D., Marengo, D., & Settanni, M. (2018). The digital traces of personality in the digital age. *Personality and Individual Differences*, 124.（元分析，权重先验来源）

## 自测题（全部能答出来即通关）

1. 四层管线中，行为特征的先验权重为什么比语言特征高 1.2 倍？出处是哪篇元分析？
2. Stachl 2020 对本项目的意义是什么？它证明的结论恰好对应哪一类特征？
3. trait 聚合为什么用半衰期 90 天的 EMA 而不是简单平均？理论依据？
4. 职业域为什么立论于显示性偏好而不是性格→职业的映射？两篇元分析支持了"弱预测"判断？
5. α=1 时系统是什么形态？为什么这个形态必须保留？
6. 报告为什么主打纵向自我对照而不是与人群常模比？两个方法学理由。
7. BARS 是什么？为什么 LLM 评分要用它而不是直接问"这个人开放性多高"？
8. "倾向具备"强档的两个来源分别是什么？SJT 行为票要几票、为什么 discount 能一票否决它？
9. confirm 出题的"题型分工"：LLM 只写哪类题？另外两类题为什么必须由代码生成？
10. 组合身份 18 种怎么算出来的？五维签名有多少种？四态码和 MBTI 二分的本质区别是什么？

<details><summary>📖 参考答案（先自测再展开）</summary>

1. Azucar et al. 2018 元分析显示数字足迹→大五平均 r≈0.3–0.4，行为特征平均效度略高于语言特征（见 §3.1）。
2. 证明"完全不看内容、仅凭使用行为模式就能预测人格"（r≈0.3）——对应 2.2 行为特征层，是"怎么用比说了什么更难伪装"的实证基础（见 §2.2）。
3. 人格特质时间常数以年计，90 天半衰期让季节性/项目性波动指数衰减，同时保留真实的长期漂移；理论依据是 Epstein 1979 跨情境聚合理论（见 §4）。
4. 性格→职业绩效仅弱预测（Barrick & Mount 1991：尽责性 r≈0.2-0.3 且非职业特异），显示性偏好是兴趣的直接证据无需绕道；Judge 系列元分析同样支持弱预测（见 §6）。
5. α=1 = 纯规则层，完全无 LLM 依赖、全本地、可复现零成本——隐私敏感用户和审计场景必须保留这个形态（见 §3.3）。
6. ①单用户无人群常模，绝对分数不可辩护；②纵向对照利用了本项目独有优势——192 天聚合（Epstein 聚合效度增益），问卷工具做不到（见 §7.2）。
7. Behaviorally Anchored Rating Scale，行为锚定量表：1-5 每级配具体行为描述。直接裸问会让 LLM 评分无标准、趋中且不可比；锚定后评分跨样本一致且缓解位置偏差（见 §3.2）。
8. 本人确认（evidence 题答"准确"）或 SJT 行为票 ≥2（≥2 题情景选择一致投给该维度 = 行为层面确认）。discount 是当事人显式否认——显式否认 > 行为推断，所以一票否决（见 §5.1）。
9. LLM 只写 SJT 情景题（场景创作是它擅长的）；证据确认题和盲区自评题必须由代码生成——题面里的指标名/数值/维度全部来自真实面板，经 LLM 手就有幻觉数字风险（见 §5.1）。
10. 3 原型 × 6 霍兰德兴趣首型 = 18（两个独立测量域交叉）；签名 4⁵ = 1024 种；四态码每一位映射到该域两个 aspect 的真实档位行（可证伪），且保留中间态——MBTI 二分会把中间态强制推向两极，制造虚假确定性（见 §5.3）。

</details>
