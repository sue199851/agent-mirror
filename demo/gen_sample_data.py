#!/usr/bin/env python3
"""demo 合成人设数据生成器 ——「夜航员」（nightowl）

用途：为 GitHub demo 提供一份**虚构人格**的真实管线输入。
- 数据 100% 合成：项目名/消息/时间戳均为编造，与任何真人无关
- 人设刻意与维护者本人相反（深夜型 / 高挫败 / 低礼貌 / 高探索），
  用于演示"不同行为数据 → 不同画像"的区分性
- 确定性：固定随机种子，任何人重跑得到相同数据
- 自验证：生成后用 am.py 的真实特征提取器检查关键指标落点

跑法：python3 demo/gen_sample_data.py
输出：demo/sample-data/nightowl.jsonl（每行一个会话，CONFIG.md jsonl 协议格式）
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "sample-data", "nightowl.jsonl")
R = random.Random(20261020)          # 固定种子：可复现

# ── 人设档案（想捏别的人设，改这里） ──────────────────────────────
# 夜航员：独立开发者，昼伏夜出，凌晨是灵感高峰。对新范式来者不拒，
# 挫败外露但指向"事"不指向"己"，对机器不客气，想到就干、收尾靠外部结构。
PERSONA = {
    "projects": [
        ("nightlamp", 0.24, "音画可视化，WebGL shader + 音频分析"),
        ("pixelpet", 0.18, "像素风电子宠物游戏，副业项目"),
        ("homelab", 0.16, "自托管全家桶，NAS + 容器 + 监控"),
        ("synthwave", 0.14, "AI 生成音乐实验，音色合成"),
        ("autoblog", 0.12, "自动写作流水线，抓取→改写→发布"),
        ("langplay", 0.10, "用 LLM 练外语，对话式学习"),
        ("junkbox", 0.06, "一次性脚本杂物箱"),
    ],
    "span": ("2026-05-15", "2026-10-10"),          # 约 150 天
    "n_sessions": 96,
    "hours": {23: 16, 0: 12, 1: 11, 2: 9, 3: 6, 4: 4, 5: 2, 6: 1,
              20: 7, 21: 7, 22: 6,
              10: 2, 11: 2, 12: 1, 13: 2, 14: 2, 15: 2, 16: 2, 17: 2, 18: 1, 19: 2},
    "weekend_ratio": 0.45,                           # 周末会话占比
}

# ── 消息模板（关键词句 + 无词典噪音的技术 filler 稀释密度） ──────────
# 模板权重 ≈ 期望词频：总字符约 38k，360 条真人消息
TPL = {
    "frust": 0.10,   # 挫败（对事不对己）
    "imper": 0.10,   # 祈使（对机器不客气）
    "why": 0.06,     # 原理追问
    "alt": 0.08,     # 备选寻求
    "win": 0.06,     # 成就时刻
    "life": 0.06,    # 生活场景
    "soft": 0.05,    # 低频礼貌/对冲/自评
    "neutral": 0.46, # 无关键词的技术推进
}
FRUST = ["又崩了", "烦死，卡住了", "第三次了这个错", "这个 hang 住半小时了",
         "服了，折腾一晚", "白搞了，换方向", "这个 dump 看不出任何线索",
         "卡住半小时了，日志全是空的", "崩了，dump 给你"]
IMPER = ["给我能跑的版本", "给我 diff", "重新生成，这次不走老路",
         "这段删掉", "立刻跑一遍", "就这样，别再动", "改成异步的",
         "马上来一版能跑的", "重新来，参数全重置"]
WHY = ["为什么这里用单例？讲讲", "底层是怎么实现的", "凭什么这里不能并行",
       "这套机制的来龙去脉讲讲", "这个宏展开后是什么意思"]
ALT = ["多给几个方案", "换个思路", "还有没有别的解法", "备选全摆出来",
       "几种方案一起给", "另一个方案是什么"]
WIN = ["跑通了，提交", "搞定，收尾，舒服", "闭环了", "修好了，漂亮，全部用例过了",
       "落地了，今晚到这"]
LIFE = ["周末看什么电影，列几部", "游戏存档怎么弄到 NAS 上",
        "凌晨 4 点才困，作息全乱了", "这剧第五季崩了",
        "假期把家里网络重新折腾一遍", "睡眠太差了"]
SOFT = ["谢谢，这个思路可以", "可能是我参数没配对", "是不是我理解错了",
        "好像不是这里的问题", "这个我不太懂，解释下"]
NEUTRAL = ["接着上次的继续", "新分支上跑同样的输入", "看这两份产物的差异在哪",
           "输出贴在下面了", "把上一版的结论带上", "换个 30 秒的窗口再看一次",
           "今天从第 3 步那个岔路走", "把两台机器的结果放一起看",
           "接着昨晚那个结论往下推", "这轮先不动数据，只看指标"]
FILLER = [
    "版本钉在 1.14.2，没动过 lockfile。昨晚 4090 那台还正常，今天换到笔记本就出问题，驱动 550 对比 560 差了一个大版本",
    "输入是 44.1kHz 的 float32 PCM，窗口 2048，hop 512。图谱上 8kHz 之后全是毛刺，信噪比掉到 -12dB",
    "容器是 alpine 3.20 的底子，跑在 x86 上。zfs arc 只给了 2G，swap 没开",
    "shader 入口在 frag.glsl 217 行附近，uniform block 绑定点 3。错误码 1282 但 draw call 本身没死",
    "主循环 60fps 顶得住，但 GC 每 40 秒抖一次，火焰图上 sys_time 的部分占了 18%",
    "存档是 protobuf v3 的，向后兼容到 v1。断电恢复那支路径从来没走过真机测试",
    "阈值扫描了 0.68 到 0.95，步进 0.03。precision 在 0.84 之后掉崖，recall 反着涨",
    "token 预算每轮 4k，上下文留 12 轮。temperature 0.7，top_p 0.9，惩罚系数没动",
    "面板是 grafana 10，数据源 prometheus，retention 15 天。凌晨那几个小时全是空的，断点在 03:17",
    "episode 的 state 序列长度不一，最短 3 帧，最长 91 帧。reward 的 discount 取 0.97",
    "走索引 28ms，全表扫 4.2 秒。explain 里 type 是 ALL，rows 估了 1.7 亿，联合索引加上就下去了",
    "重试了 5 次，第 3 次和第 5 次成功。间隔按指数退避到 32 秒，之后连跑一小时没再出",
    "grpc 的 keepalive 设了 30 秒，idle 连接 5 分钟回收。凌晨 3 点那批调用全部卡在 header 阶段",
    "画面在第二关的雪地场景必掉帧，draw call 从 200 涨到 1400，批量合批之后只降了 300",
    "音色那一轨用了 FM 算子叠加，失谐系数 1.007 和 2.01。release 拉到 2.4 秒后尾音才对",
    "抓回来的正文有三分之一是登录墙，跳转链路绕了 4 层，最后一层带一次性 token",
]
ASSISTANT = [
    "从日志看，崩溃点在顶点着色器的 uniform 更新与音频回调的竞态。把音频分析移到独立线程，"
    "用环形缓冲传递频谱数据，渲染侧只读快照。先改这里，跑一遍复现脚本看时序。",
    "这个错在序列化协议里 int32 溢出。三条修法：升级协议版本号走 int64、拆分大数组分片传输、"
    "或者压缩后走旁路。先把最小改动的第一种实现出来，量级上去再换。",
    "并行不行的原因在全局状态表没有分锁。要并行得先做两级锁拆分，或者干脆改成 actor 模型。"
    "给你两个版本的实现对比，跑完基准再定。",
    "定位到了：定时器和主循环共享同一个 fd，epoll 触发时序竞争。复现步骤是连续触发两次心跳。"
    "把 fd 的所有权移到 timer 线程，主循环轮询结果而不是直接读。",
    "这是工作目录权限问题。容器里的 uid 映射没对上，卷挂载后属主变了。两种做法：在 compose 里"
    "显式声明 user 字段，或者 entrypoint 里改一次属主。后者侵入小。",
    "音色合成这块，用 FM 加几个失谐算子就能出那种 80 年代质感。参数按经典 DX7 的算法先预设了一版，"
    "包络的 release 你自己调，试几组听感差异很大。",
    "改写流水线的去重用 simhash 滑窗就够，不需要上 embedding。阈值 0.82 附近精度最好，"
    "超过 0.9 会漏近义改写。先跑一周语料看召回再定。",
    "对话练习的话，纠错时机放在句尾而不是句中，中断会显著降流利度，工作记忆占用的机制问题。"
    "改成回合制反馈，再测一轮对比数据。",
]
CORRECTIONS = ["不对，", "又错了，", "重来，", "不是这个，", "改一下，"]


def weighted_hours():
    pool = [h for h, w in PERSONA["hours"].items() for _ in range(w)]
    return R.choice(pool)


def pick_date(start, end):
    if R.random() < PERSONA["weekend_ratio"]:
        weekend = [start + timedelta(days=i) for i in range((end - start).days + 1)
                   if (start + timedelta(days=i)).weekday() >= 5]
        return R.choice(weekend)
    return start + timedelta(days=R.randint(0, (end - start).days))


def session_flow():
    """会话深度分布：1 轮 28% / 2-3 轮 34% / 4-7 轮 27% / 8-14 轮 11%。"""
    r = R.random()
    if r < 0.28:
        return 1
    if r < 0.62:
        return R.randint(2, 3)
    if r < 0.89:
        return R.randint(4, 7)
    return R.randint(8, 14)


def pick_template(kinds, weights):
    return R.choices(kinds, weights=weights, k=1)[0]


def user_text(first):
    kinds = list(TPL)
    weights = list(TPL.values())
    if first:                          # 开场更挫败/更祈使
        weights = [w * (3 if k == "frust" else 1) for k, w in zip(kinds, weights)]
    kind = R.choices(kinds, weights=weights, k=1)[0]
    core = {
        "frust": R.choice(FRUST), "imper": R.choice(IMPER), "why": R.choice(WHY),
        "alt": R.choice(ALT), "win": R.choice(WIN), "life": R.choice(LIFE),
        "soft": R.choice(SOFT), "neutral": R.choice(NEUTRAL),
    }[kind]
    filler = R.choice(FILLER) if R.random() < 0.72 else ""
    sep = "。" if filler else ""
    return core + sep + filler


def main():
    start = datetime.fromisoformat(PERSONA["span"][0])
    end = datetime.fromisoformat(PERSONA["span"][1])
    projects, weights = zip(*[(p, w) for p, w, _ in PERSONA["projects"]])
    lines, sessions = [], []
    for i in range(PERSONA["n_sessions"]):
        proj = R.choices(projects, weights=weights)[0]
        d = pick_date(start, end)
        ts = d.replace(hour=weighted_hours(), minute=R.randint(0, 59),
                       second=R.randint(0, 59), microsecond=0)
        n_turns = session_flow()
        msgs = []
        t = ts
        for j in range(n_turns):
            text = user_text(first=(j == 0))
            if j > 0 and R.random() < 0.12:
                text = R.choice(CORRECTIONS) + text
            msgs.append({"role": "user", "text": text, "created_at": t.isoformat()})
            t = t + timedelta(minutes=R.randint(1, 25))
            if R.random() < 0.85:
                msgs.append({"role": "assistant", "text": R.choice(ASSISTANT),
                             "created_at": t.isoformat()})
                t = t + timedelta(seconds=R.randint(20, 300))
        lines.append(json.dumps({"project": proj, "messages": msgs}, ensure_ascii=False))
        sessions.append(proj)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # ── 自验证：用 am.py 真实特征提取器检查人设落点（session 规则对齐 conn_custom） ──
    sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "am", os.path.join(HERE, "..", "scripts", "am.py"))
    am = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(am)
    am.refresh_automated_patterns([r"^\[cron:", r"^Read HEARTBEAT\.md"])
    users = []
    for n, line in enumerate(lines, 1):
        o = json.loads(line)
        for x in o["messages"]:
            if x["role"] == "user":
                users.append(am.Msg("synth-nightowl", f"nightowl#{n}",
                                    am.parse_ts(x["created_at"]), "user", x["text"], o["project"]))
    F = am.extract_features(users, {"analysis": am.DEFAULTS["analysis"]})
    checks = [
        ("night_ratio", F["night_ratio"], 0.50, 0.75, "深夜比"),
        ("weekend_ratio", F["weekend_ratio"], 0.30, 0.60, "周末比"),
        ("deep_session", F["deep_session"], 0.25, 0.50, "深度会话"),
        ("followup", F["followup"], 0.45, 0.80, "追问率"),
        ("correction_rate", F["correction_rate"], 0.08, 0.25, "纠错率"),
        ("frustration", F["frustration"], 1.0, 3.0, "挫败密度"),
        ("imperative", F["imperative"], 1.5, 4.0, "祈使密度"),
        ("why_questions", F["why_questions"], 0.4, 2.0, "原理提问"),
        ("alternative_seeking", F["alternative_seeking"], 0.3, 2.0, "备选寻求"),
        ("gratitude_politeness", F["gratitude_politeness"], 0.0, 0.6, "礼貌密度"),
        ("planning_first", F["planning_first"], 0.0, 0.5, "计划先行"),
        ("verification", F["verification"], 0.0, 0.5, "验证要求"),
        ("positive", F["positive"], 0.0, 0.8, "正性情绪"),
        ("anxiety", F["anxiety"], 0.0, 0.5, "焦虑"),
    ]
    print(f"✅ 生成 {PERSONA['n_sessions']} 会话 / {len(users)} 真人消息 → {OUT}\n")
    ok = True
    for k, v, lo, hi, label in checks:
        hit = lo <= v <= hi
        ok = ok and hit
        print(f"  {'✓' if hit else '✗ 越界'} {label:　<6} {k} = {v:.2f}  (目标 {lo}–{hi})")
    if not ok:
        print("\n⚠️ 有指标越界——调整模板配比后重跑")
        sys.exit(1)
    print("\n人设落点命中：深夜型 / 高挫败 / 高祈使低礼貌 / 高探索 / 低计划——与维护者"
          "「工作日白天 · 低挫败 · 高礼貌」形成对照，用于演示区分性。")


if __name__ == "__main__":
    main()
