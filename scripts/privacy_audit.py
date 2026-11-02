#!/usr/bin/env python3
"""隐私上传审计（可重复回归闸）：捕获每个 LLM 出口的真实 payload，按档位断言"什么被上传了"。

跑法：python3 scripts/privacy_audit.py
原理：stub 掉 am.llm_chat 记录 (system, user) 真实载荷，向各出口注入金丝雀内容——
      假 VIN / 假 home 路径 / 标记原文片段——然后断言：

  stats_only（默认档）：任何原始会话文本、项目名、VIN 不得出现在 payload
                       （只允许统计数字、维度名、题库结构）；ask_llm 应拒绝归纳（返回 None）
  redacted           ：redact_patterns 覆盖的内容（VIN/路径/邮箱）必须已打码后才上行
  off                ：llm_chat 中心闸拒绝，真实网络函数被调用前即返回（零捕获）

任一断言失败 → 非零退出。改 LLM 相关代码后请重跑本脚本。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import am  # noqa: E402

# ── 金丝雀内容（全部虚构，仅用于检测泄露）──────────────────────────────
# 注意 VIN 金丝雀按配置的 redact_patterns 形状构造（\d{4}[A-Za-z0-9]{10,}）——
# 审计验证的是"规则被正确执行"，规则本身是否够宽（如字母开头的 LFV… VIN）见报告备注。
CANARY_VIN = "1234CANARYABCDEF99"        # 4 数字 + 14 位字母数字，匹配默认 VIN 规则
CANARY_HOME = "/home/canaryuser/secret"
CANARY_TEXT = "金丝雀原始会话文本CNRAWTEXT不得上行"
CANARY_PROJ = "CNRYPROJ"

CANNED_LLM_REPLY = ('[{"scene":"审计桩中性场景：三个方向你先碰哪个？",'
                    '"options":[{"text":"甲","votes":{"O1":"+"}},'
                    '{"text":"乙","votes":{"C1":"+"}},'
                    '{"text":"丙","votes":{"E1":"+"}}]}]')


def make_cfg(exposure):
    return {
        "llm": {"base_url": "http://stub", "model": "stub"},
        "privacy": {"llm_exposure": exposure,
                    "redact_patterns": [
                        [r"\d{4}[A-Za-z0-9]{10,}", "<VEH-ID>"],
                        [r"/home/[^/\s]+", "<HOME>"],
                        [r"[\w.+-]+@[\w-]+\.[\w.]+", "<EMAIL>"],
                    ]},
    }


def make_msgs():
    # 「为什么」保证词典命中（证据链采得到这条消息），金丝雀紧邻命中词确保落进片段窗口
    raw = (f"{CANARY_TEXT}，为什么要这样设计，车架号 {CANARY_VIN}，"
           f"日志在 {CANARY_HOME} 下面")
    return [am.Msg("claude_code", "sess-canary-0001", am.parse_ts("2026-10-28T22:00:00"),
                   "user", raw, CANARY_PROJ)]


def make_F():
    F = {"sessions_total": 3, "avg_turns": 5.0, "deep_session": 0.6, "followup": 0.7,
         "why_questions": 0.5, "achievement": 0.3, "verification": 0.2,
         "imperative": 0.4, "gratitude_politeness": 0.1, "frustration": 0.2,
         "anxiety": 0.1, "hedge": 0.3, "planning_first": 0.1, "format_demands": 0.2,
         "positive": 0.2, "night_ratio": 0.1, "weekend_ratio": 0.1,
         "investment": [(f"claude_code:/x/{CANARY_PROJ}", 100, 10, 10.0)]}
    return F


FAILS = []


def check(name, cond, detail=""):
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + ("" if cond else f"  ← {detail}"))
    if not cond:
        FAILS.append(name)


def run_with_capture(cfg, fn, *args, **kw):
    """stub am.llm_chat → 执行 fn → 返回 (fn结果, 捕获的payload列表)。"""
    captured = []

    def fake_llm_chat(c, system, user, max_tokens=3000):
        captured.append({"system": system, "user": user})
        return CANNED_LLM_REPLY

    orig = am.llm_chat
    am.llm_chat = fake_llm_chat
    try:
        result = fn(cfg, *args, **kw)
    finally:
        am.llm_chat = orig
    return result, captured


def audit_stats_only():
    print("\n[1] stats_only（默认档）：只允许统计指标上行")
    cfg = make_cfg("stats_only")
    msgs, F = make_msgs(), make_F()
    panel = am.build_panel(F, am.load_mapping("personality"))

    _, caps = run_with_capture(cfg, am.llm_analyze, "personality", F, msgs)
    blob = " ".join(c["system"] + c["user"] for c in caps)
    check("llm_analyze 不含原始会话文本", CANARY_TEXT not in blob)
    check("llm_analyze 不含 VIN", CANARY_VIN not in blob)
    check("llm_analyze 不含项目名", CANARY_PROJ not in blob)

    _, caps = run_with_capture(cfg, am.llm_questions, "personality", F, panel, None)
    blob = " ".join(c["system"] + c["user"] for c in caps)
    check("llm_questions 不含项目名（stats 严口径）", CANARY_PROJ not in blob)

    rows = [(CANARY_TEXT + f" {CANARY_VIN}", "claude_code", f"/home/x/{CANARY_PROJ}",
             "2026-10-28", "user")]
    res, caps = run_with_capture(cfg, am.ask_llm, "问题", rows, [])
    check("ask_llm 拒绝归纳（返回 None）", res is None)
    check("ask_llm 零上传", len(caps) == 0)


def audit_redacted():
    print("\n[2] redacted：片段须按 redact_patterns 打码后上行")
    cfg = make_cfg("redacted")
    msgs, F = make_msgs(), make_F()

    _, caps = run_with_capture(cfg, am.llm_analyze, "personality", F, msgs)
    blob = " ".join(c["system"] + c["user"] for c in caps)
    check("llm_analyze VIN 已打码", CANARY_VIN not in blob and "<VEH-ID>" in blob)
    check("llm_analyze home 路径已打码", CANARY_HOME not in blob and "<HOME>" in blob)

    rows = [(f"片段内容 {CANARY_VIN} 路径 {CANARY_HOME}", "claude_code",
             CANARY_HOME, "2026-10-28", "user")]
    res, caps = run_with_capture(cfg, am.ask_llm, "问题", rows, [])
    check("ask_llm 允许归纳（redacted 档）", isinstance(res, str) and len(caps) == 1)
    blob = caps[0]["user"] if caps else ""
    check("ask_llm VIN 已打码", CANARY_VIN not in blob and "<VEH-ID>" in blob)
    check("ask_llm home 路径已打码", CANARY_HOME not in blob and "<HOME>" in blob)


def audit_off():
    print("\n[3] off：中心闸拒绝，零上行")
    cfg = make_cfg("off")
    # off 档走真实 llm_chat（不 stub），验证闸在网络调用之前生效
    res = am.llm_chat(cfg, "s", "u")
    check("llm_chat 返回 None（未触网）", res is None)

    _, caps = run_with_capture(cfg, am.llm_analyze, "personality", make_F(), make_msgs())
    check("llm_analyze off 档自查短路（零捕获）",
          len(caps) == 0)


def audit_local_only():
    print("\n[4] 纯本地路径零 LLM 调用（证据链/报告只落本地）")
    cfg = make_cfg("stats_only")
    msgs, F = make_msgs(), make_F()
    panel = am.build_panel(F, am.load_mapping("personality"))
    ev = am.collect_evidence(msgs)

    def render(_cfg):
        return am.render_analyze("personality", F, panel, am.load_mapping("personality"),
                                 "", 1, 1, evidence=ev)

    report, caps = run_with_capture(cfg, render)
    import hashlib
    h = hashlib.sha1(msgs[0].text.encode("utf-8")).hexdigest()[:12]
    check("render_analyze 零 LLM 调用", len(caps) == 0)
    check("证据链进了本地报告（含消息指纹）",
          "证据链 · 条目级回溯" in report and f"#{h}" in report)


def main():
    print("隐私上传审计 · 金丝雀：假 VIN / 假路径 / 标记原文 / 假项目名（全虚构）")
    audit_stats_only()
    audit_redacted()
    audit_off()
    audit_local_only()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项断言失败：{FAILS}")
        sys.exit(1)
    print("✅ 全部通过：stats_only 只上行统计；redacted 打码后上行；off 零上行；本地路径零调用")


if __name__ == "__main__":
    main()
