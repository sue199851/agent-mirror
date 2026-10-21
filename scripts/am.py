#!/usr/bin/env python3
"""Agent Mirror CLI —— 数据底座 + 三域入口

用法（Roadmap 见 README）:
    python3 scripts/am.py scan [--source <源名>] [--dry-run] [--report PATH] [--inspect-hour H]
    python3 scripts/am.py build [--source <源名>]        # 物化 + FTS5 索引
    python3 scripts/am.py search "<关键词>" [--limit N]  # 纯本地加权检索
    python3 scripts/am.py ask "<问题>"                   # 检索 + LLM 归纳（无 key 自动降级）
    python3 scripts/am.py analyze [--track personality|career|both] [--mode rules|llm|hybrid]
    python3 scripts/am.py confirm [--track <t>] [--answers "A C B"]   # 当事人校验（SJT）
    python3 scripts/am.py diff [personality|career]      # 快照漂移对比
    python3 scripts/am.py export [--track <t>]           # 报告数据 JSON
    （personality / career 是 analyze --track 的快捷别名；chat 多轮会话在 Roadmap）

数据永不上行；LLM 相关子命令受 config.yaml privacy 档位约束。
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = {
    "sources": {
        "claude_code": {"enabled": True, "paths": ["~/.claude/projects"], "include_globs": ["*.jsonl"]},
        "openclaw": {"enabled": True, "paths": ["~/.openclaw/agents"], "include_globs": ["*/sessions/*.jsonl"]},
        "hermes": {"enabled": True, "paths": ["~/.hermes/sessions"], "include_globs": ["*.jsonl", "*.json"]},
        # 自动消息过滤默认规则（config.yaml 可覆盖/追加）
        "automated_patterns": [r"^\[cron:", r"^Read HEARTBEAT\.md"],
    },
    "analysis": {"night_hours": [23, 24, 0, 1, 2, 3, 4, 5], "state_window_days": 7},
}

# 编译后的自动消息过滤规则（load_config 后刷新；实测发现的注入模式回流为默认值）
AUTOMATED_PATTERNS = []


def refresh_automated_patterns(pats):
    global AUTOMATED_PATTERNS
    AUTOMATED_PATTERNS = [re.compile(p) for p in (pats or [])]


# ──────────────────────────── 配置 ────────────────────────────
def load_config():
    try:
        import yaml
        with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        cfg = {}
    merged = {"sources": {}, "analysis": DEFAULTS["analysis"], "llm": cfg.get("llm", {}),
              "privacy": cfg.get("privacy", {}), "knowledge": cfg.get("knowledge", {})}
    src_cfg = cfg.get("sources") or {}
    for name, dflt in DEFAULTS["sources"].items():
        if name == "automated_patterns":
            merged["sources"]["automated_patterns"] = src_cfg.get("automated_patterns") \
                or DEFAULTS["sources"]["automated_patterns"]
            continue
        merged["sources"][name] = {**dflt, **(src_cfg.get(name) or {})}
    merged["sources"]["custom"] = src_cfg.get("custom") or []
    refresh_automated_patterns(merged["sources"]["automated_patterns"])
    return merged


# ──────────────────────── 统一消息模型 ────────────────────────
class Msg:
    __slots__ = ("source", "session", "ts", "role", "text", "project", "automated")

    def __init__(self, source, session, ts, role, text, project="", automated=False):
        self.source, self.session, self.ts = source, session, ts
        self.role, self.text, self.project = role, (text or "").strip(), project
        # automated=True: cron/定时任务注入的消息（[cron:...] 前缀），非真人行为
        self.automated = automated


def parse_ts(v):
    """iso8601 字符串 / epoch 秒 / epoch 毫秒 → datetime（本地时区），失败 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            v = v / 1000 if v > 1e12 else v
            return datetime.fromtimestamp(v)
        except Exception:
            return None
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        return None


def extract_text(content):
    """Claude/OpenAI 风格 content：str 或 [{type:text,text:...}, ...] → 纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") in ("text", None) and c.get("text"):
                parts.append(c["text"])
        return "\n".join(parts)
    return ""


USER_ROLES = {"user", "human", "me", "owner"}
ASST_ROLES = {"assistant", "ai", "model", "bot"}


def normalize_event(obj, source, session, project):
    """把一条'疑似消息'的 JSON 对象宽松映射为统一消息；不是消息返回 None。"""
    role_v = obj.get("role") or obj.get("speaker") or obj.get("from") or obj.get("author")
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else None
    if msg:  # claude_code / openclaw 事件式：{type:user, message:{role,content}}
        role_v = role_v or msg.get("role")
        content = msg.get("content", msg.get("text", ""))
    else:
        content = obj.get("text") or obj.get("content") or obj.get("body") or obj.get("message") or ""
    if not role_v:
        return None
    role = str(role_v).lower()
    if role in USER_ROLES:
        role = "user"
    elif role in ASST_ROLES:
        role = "assistant"
    else:
        return None
    text = extract_text(content) if not isinstance(content, str) else content
    if not text or not str(text).strip():
        return None
    ts = parse_ts(obj.get("timestamp") or obj.get("ts") or obj.get("created_at")
                  or obj.get("time") or obj.get("t") or (msg or {}).get("created_at"))
    text = str(text)
    # 自动消息判定：命中 config 配置的注入模式（cron/heartbeat/用户自定义）
    automated = role == "user" and any(p.search(text) for p in AUTOMATED_PATTERNS)
    if automated and role == "user":
        m = re.match(r"\[cron:[0-9a-f-]+\s+(.+?)\]", text)
        if m:                                    # 任务名信息量 > cwd/agent 名，无条件覆盖
            project = f"cron:{m.group(1)}"
        elif text.startswith("Read HEARTBEAT"):
            project = "heartbeat"
    return Msg(source, session, ts, role, text, project, automated)


# ──────────────────────── 三源连接器 ────────────────────────
def iter_jsonl(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                yield None


def find_msg_array(obj):
    """在 dict 里启发式找消息数组（hermes 旧 json 等）。"""
    for key in ("messages", "history", "conversation", "turns", "data", "chat"):
        v = obj.get(key)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return None


def source_paths(sc):
    """一个源可配多个目录（paths 列表，向后兼容单 path 字段）。"""
    p = sc.get("paths") or ([sc["path"]] if sc.get("path") else [])
    return [os.path.expanduser(x) for x in p]


def conn_claude_code(src_cfg, on_msg, on_fail):
    for root in source_paths(src_cfg):
        for d in sorted(glob.glob(os.path.join(root, "*"))):
            if not os.path.isdir(d):
                continue
            project = os.path.basename(d)
            for fp in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
                session = os.path.basename(fp).replace(".jsonl", "")
                for obj in iter_jsonl(fp):
                    if obj is None:
                        on_fail(fp)
                        continue
                    m = normalize_event(obj, "claude_code", session, project)
                    if m:
                        on_msg(m)


def conn_openclaw(src_cfg, on_msg, on_fail):
    for root in source_paths(src_cfg):
        for fp in sorted(glob.glob(os.path.join(root, "*", "sessions", "*.jsonl"))):
            base = os.path.basename(fp)
            if ".trajectory" in base or "-path" in base:   # 轨迹文件暂不收（Phase 1 行为域再收）
                continue
            agent = fp.split(os.sep)[-3]
            session = base.replace(".jsonl", "")
            cwd = None
            for obj in iter_jsonl(fp):
                if obj is None:
                    on_fail(fp)
                    continue
                if isinstance(obj.get("cwd"), str):        # session 行/工具行都可能带 cwd，滚动更新
                    cwd = obj["cwd"]
                m = normalize_event(obj, "openclaw", session, cwd or agent)
                if m:
                    on_msg(m)


def conn_hermes(src_cfg, on_msg, on_fail):
    for root in source_paths(src_cfg):
        for fp in sorted(glob.glob(os.path.join(root, "*.jsonl"))):
            session = os.path.basename(fp).replace(".jsonl", "")
            for obj in iter_jsonl(fp):
                if obj is None:
                    on_fail(fp)
                    continue
                m = normalize_event(obj, "hermes", session, "hermes")
                if m:
                    on_msg(m)
        for fp in sorted(glob.glob(os.path.join(root, "session_*.json"))):  # 旧版单文件会话
            session = os.path.basename(fp).replace(".json", "")
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
            except Exception:
                on_fail(fp)
                continue
            arr = find_msg_array(data) if isinstance(data, dict) else (data if isinstance(data, list) else None)
            for obj in arr or []:
                m = normalize_event(obj, "hermes", session, "hermes")
                if m:
                    on_msg(m)


CONNECTORS = {"claude_code": conn_claude_code, "openclaw": conn_openclaw, "hermes": conn_hermes}


# ──────────────────────── 自定义源连接器（CONFIG.md 零代码协议） ────────────────────────
def jp_walk(obj, path):
    """CONFIG.md 简化 JSONPath → 值列表（. 下钻，[*] 展开数组，a[0] 取下标）。
    "$.messages[*]" / "$[*]" / "message.role" / "content.parts[0]" 均可。"""
    if path in (None, ""):
        return [obj]
    p = str(path).strip().lstrip("$").lstrip(".")
    if not p:
        return [obj]
    cur = [obj]
    for seg in p.split("."):
        if not seg:
            continue
        m = re.match(r"^(\w+)\[(\d+)\]$", seg)
        if seg == "[*]":
            cur = [x for v in cur if isinstance(v, list) for x in v]
        elif seg.endswith("[*]"):
            key = seg[:-3]
            cur = [x for v in cur if isinstance(v, dict) and isinstance(v.get(key), list) for x in v[key]]
        elif m:
            key, idx = m.group(1), int(m.group(2))
            cur = [v[key][idx] for v in cur if isinstance(v, dict)
                   and isinstance(v.get(key), list) and len(v[key]) > idx]
        else:
            cur = [v[seg] for v in cur if isinstance(v, dict) and seg in v]
    return [v for v in cur if v is not None]


def _custom_files(sc, typ):
    roots = source_paths(sc)
    files = []
    for root in roots:
        if sc.get("include_globs"):
            for g in sc["include_globs"]:
                files += glob.glob(os.path.join(root, g), recursive=True)
        else:
            sg = sc.get("session_glob") or ("*.json" if typ == "json" else "*.jsonl")
            files += glob.glob(os.path.join(root, sg))
    return sorted(set(files))


def _custom_one(sc, fmt, obj, default_session, default_project):
    """一个消息对象 + 字段映射 → Msg（role 值不匹配返回 None，dry-run 可见）。"""
    ru, ra = str(fmt.get("role_user_value", "user")), str(fmt.get("role_assistant_value", "assistant"))
    role_vals = [str(v).strip() for v in jp_walk(obj, fmt.get("role_field") or "role")]
    if not role_vals:
        return None
    rv = role_vals[0]
    if rv == ru:
        role = "user"
    elif rv == ra:
        role = "assistant"
    elif rv.lower() == ru.lower():
        role = "user"
    elif rv.lower() == ra.lower():
        role = "assistant"
    else:
        return None
    tf = fmt.get("text_field") or "text"
    texts = jp_walk(obj, tf) or jp_walk(obj, "content")
    text = ""
    for t in texts:
        text = t if isinstance(t, str) else extract_text(t)
        if text:
            break
    if not str(text).strip():
        return None
    ts_raw = jp_walk(obj, fmt.get("timestamp_field")) if fmt.get("timestamp_field") else []
    ts = parse_ts(ts_raw[0] if ts_raw else None)
    sid = None
    if fmt.get("session_id_field"):
        sv = jp_walk(obj, fmt["session_id_field"])
        sid = str(sv[0]) if sv else None
    proj = None
    if fmt.get("project_field"):
        pv = jp_walk(obj, fmt["project_field"])
        proj = str(pv[0]) if pv else None
    automated = role == "user" and any(p.search(str(text)) for p in AUTOMATED_PATTERNS)
    return Msg(sc.get("name") or "custom", sid or default_session, ts, role, str(text),
               proj or default_project, automated)


def conn_custom(sc, on_msg, on_fail):
    """自定义源：jsonl（每行一个完整会话）/ json（一文件一会话）/ jsonl_events（每行一事件）。
    协议见 CONFIG.md；scan --source <name> --dry-run 可验证字段映射。"""
    typ = sc.get("type", "jsonl")
    fmt = sc.get("format") or {}
    name = sc.get("name") or "custom"
    default_project = None
    for root in source_paths(sc):
        default_project = default_project or os.path.basename(root.rstrip(os.sep))
    default_project = default_project or name
    files = _custom_files(sc, typ)
    if sc.get("max_sessions"):
        files = files[:int(sc["max_sessions"])]
    for fp in files:
        stem = os.path.basename(fp).rsplit(".", 1)[0]
        if typ == "json":
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
            except Exception:
                on_fail(fp)
                continue
            for obj in jp_walk(data, fmt.get("message_path") or "$"):
                if not isinstance(obj, dict):
                    continue
                m = _custom_one(sc, fmt, obj, stem,
                                _session_level_project(fmt, data) or default_project)
                if m:
                    on_msg(m)
        else:                                   # jsonl / jsonl_events：逐行
            for n, line in enumerate(iter_jsonl(fp), 1):
                if line is None:
                    on_fail(fp)
                    continue
                sess_default = stem if typ == "jsonl_events" else f"{stem}#{n}"
                root_proj = _session_level_project(fmt, line)
                events = jp_walk(line, fmt.get("message_path") or "$") if typ == "jsonl_events" else None
                if events is None:              # jsonl：message_path 直接给消息数组
                    events = jp_walk(line, fmt.get("message_path") or "$.messages[*]")
                for obj in events:
                    if not isinstance(obj, dict):
                        continue
                    if typ == "jsonl_events" and fmt.get("type_field"):
                        if str(obj.get(fmt["type_field"])) != str(fmt.get("message_type_value", "message")):
                            continue
                    m = _custom_one(sc, fmt, obj, sess_default, root_proj or default_project)
                    if m:
                        on_msg(m)


def _session_level_project(fmt, root_obj):
    """project_field 先看消息级，再看会话根级（整会话同一项目的常见形状）。"""
    if not fmt.get("project_field"):
        return None
    pv = jp_walk(root_obj, fmt["project_field"])
    return str(pv[0]) if pv else None


def each_source(cfg, only, on_msg, on_fail):
    """统一源遍历：三内置连接器 + custom 列表（--source <name> 过滤两者）。
    on_fail(文件路径, 源名)——解析失败可归因到源。"""
    for name, conn in CONNECTORS.items():
        sc = cfg["sources"].get(name, {})
        if not sc.get("enabled", True) or (only and only != name):
            continue
        conn(sc, on_msg, lambda fp, n=name: on_fail(fp, n))
    for sc in cfg["sources"].get("custom") or []:
        name = sc.get("name") or "custom"
        if not sc.get("enabled", True) or (only and only != name):
            continue
        conn_custom(sc, on_msg, lambda fp, n=name: on_fail(fp, n))


# ──────────────────────── 语言特征词典（种子版） ────────────────────────
# LIWC 体系的中文种子词典：每类 15~25 个高频词，覆盖 AI 对话语境。
# 仅为种子——扩展方式：config.yaml → lexicon.user_path 追加自定义词表（YAML: {类别: [词...]})
# v0 采用"子串匹配 + 每千字归一"，calibrate 后可换加权词典。
LEXICON = {
    "positive": ["开心", "高兴", "牛", "不错", "好棒", "喜欢", "满意", "顺利", "漂亮",
                 "厉害", "完美", "舒服", "赞", "妙", "感谢", "爽", "清晰", "优雅", "可靠", "稳"],
    "frustration": ["烦", "晕", "崩", "无语", "服了", "垃圾", "恼火", "疯了", "怎么又",
                    "还是不行", "搞不定", "卡住", "报错", "失败", "气人", "折腾", "白搞"],
    "anxiety": ["担心", "怕", "焦虑", "来不及", "紧张", "心慌", "别出问题", "会不会出",
                "慌", "压力", "熬夜", "熬不住", "悬"],
    "gratitude_politeness": ["谢谢", "请", "麻烦", "帮我", "感谢", "辛苦", "麻烦了",
                             "可以吗", "能不能", "拜托", "劳驾"],
    "imperative": ["直接", "改成", "就这样", "不要", "别问", "马上", "立刻", "必须",
                   "重新", "换掉", "删掉", "给我"],
    "achievement": ["搞定", "跑通", "完成", "通过", "解决", "修好", "验证", "落地",
                    "闭环", "收尾", "提交"],
    "why_questions": ["为什么", "凭什么", "原理", "是什么意思", "怎么理解", "机制",
                      "底层", "来龙去脉", "解释下"],
    "planning_first": ["先列", "计划", "分步骤", "第一步", "步骤", "顺序", "清单",
                       "拆解", "排期", "优先级", "方案对比"],
    "verification": ["检查", "验证", "再确认", "确认下", "核对", "复查", "检查下",
                     "验证下", "有没有问题"],
    "alternative_seeking": ["另一个方案", "别的方案", "还有没有", "对比一下", "多给几个",
                            "几种方案", "备选", "换个思路", "不同方案"],
    "format_demands": ["格式", "模板", "表格", "列表", "markdown", "结构化", "按这个格式"],
    "self_doubt": ["我不确定", "是不是我", "我搞错", "不太懂", "我菜", "没搞明白",
                   "不理解", "我的问题"],
    "hedge": ["可能", "也许", "大概", "说不定", "估计", "好像", "似乎"],
    # ── 职业域任务谱系 ──
    "task_debug": ["报错", "错误", "失败", "hang", "卡死", "崩溃", "异常", "定位",
                   "排查", "日志", "复现", "为什么挂", "fault", "dump"],
    "task_design": ["设计", "方案", "架构", "重构", "实现", "搭建", "写一个", "新建",
                    "做一个", "构建"],
    "task_explain": ["为什么", "原理", "机制", "解释", "讲讲", "是什么", "怎么理解"],
    "task_people": ["汇报", "措辞", "回复", "怎么跟", "沟通", "说服", "邮件", "周报",
                    "怎么说", "通知"],
    "task_plan": ["计划", "拆解", "优先级", "排期", "路线", "步骤", "规划", "目标"],
    "task_batch": ["批量", "全部", "所有", "统一", "一次性", "一键", "循环", "自动化"],
    "task_tooling": ["工具", "脚本", "自动化", "配置", "环境", "部署", "安装", "封装"],
    # ── 场景词典（工作/生活情境分类——"人×情境"变量，生活情境的社会表演少、信号更真）──
    "scene_life": ["股票", "基金", "行情", "涨了", "跌了", "收盘", "周末", "假期", "旅游",
                   "电影", "剧", "游戏", "家里", "爸妈", "孩子", "体检", "天气", "生日",
                   "礼物", "菜谱", "健身", "睡眠", "午休", "新闻", "热点"],
    "scene_work": ["编译", "部署", "版本", "bug", "上线", "服务器", "代码", "提交", "评审",
                   "需求", "会议", "排期", "客户", "报告", "pass", "精度", "模型", "工具链",
                   "测试", "发布", "分支", "仓库"],
}
WORK_PATH_RE = re.compile(r"/mnt/|/data/|toolchain|compiler|server|work")


def scene_of(msg_text, project):
    """消息级场景分类：内容词典优先，无命中按路径先验（/mnt 等工作盘 → work）。"""
    life = sum(msg_text.count(w) for w in LEXICON["scene_life"])
    work = sum(msg_text.count(w) for w in LEXICON["scene_work"])
    if life > work:
        return "life"
    if work > life:
        return "work"
    return "work" if WORK_PATH_RE.search(project or "") else "unknown"


def lex_count(text):
    """文本 → {类别: 命中数}。子串匹配，简单但对高频词足够（calibrate 后可升级）。"""
    hits = {}
    for cat, words in LEXICON.items():
        n = sum(text.count(w) for w in words)
        if n:
            hits[cat] = n
    return hits


# ──────────────────────── 统计引擎（scan） ────────────────────────
def run_scan(cfg, only=None, dry_run=False, inspect_hour=None):
    stats = {}      # source -> dict
    human_projects = Counter()     # 真人消息的投注分布（显示性偏好）
    auto_projects = Counter()      # cron/自动化任务的分布（自动化倾向，另作职业信号）
    hours = Counter()              # 直方图只用真人消息（cron 按固定节奏跑，会污染节律）
    inspect = Counter()            # --inspect-hour H：该小时真人消息的文本前缀分布
    night_hours = set(cfg["analysis"]["night_hours"])
    acc = {"night": 0, "weekend": 0, "human_ts": 0, "human": 0, "auto": 0}
    ts_min = ts_max = None
    sample = []

    # 预注册启用的源（0 条消息的源也在总览表里可见，避免"配了没显示"的困惑）
    custom_by_name = {sc.get("name") or "custom": sc for sc in cfg["sources"].get("custom") or []}
    for name in list(CONNECTORS) + list(custom_by_name):
        if name in custom_by_name:
            enabled = custom_by_name[name].get("enabled", True)
        else:
            enabled = (cfg["sources"].get(name, {}) or {}).get("enabled", True)
        if enabled and not (only and only != name):
            stats.setdefault(name, {"user": 0, "assistant": 0, "fail": 0, "human": 0, "auto": 0,
                                    "ts_min": None, "ts_max": None, "last_file": None})

    def on_msg(m, stats=stats):
        st = stats.setdefault(m.source, {"user": 0, "assistant": 0, "fail": 0, "human": 0, "auto": 0,
                                         "ts_min": None, "ts_max": None, "last_file": None})
        st[m.role] = st.get(m.role, 0) + 1
        if m.ts:
            st["ts_min"] = m.ts if not st["ts_min"] else min(st["ts_min"], m.ts)
            st["ts_max"] = m.ts if not st["ts_max"] else max(st["ts_max"], m.ts)
        if m.role != "user" or not m.text:
            return
        if m.automated:
            st["auto"] += 1
            acc["auto"] += 1
            if m.project:
                auto_projects[m.project] += 1
            return
        st["human"] += 1
        acc["human"] += 1
        if m.project:
            human_projects[f"{m.source}:{m.project}"] += 1
        if m.ts:
            hours[m.ts.hour] += 1
            acc["human_ts"] += 1
            if m.ts.hour in night_hours:
                acc["night"] += 1
            if m.ts.weekday() >= 5:
                acc["weekend"] += 1
            if inspect_hour is not None and m.ts.hour == inspect_hour:
                inspect[m.text[:60]] += 1
        if len(sample) < 3:
            sample.append(m)

    def on_fail(fp, src, stats=stats):
        st = stats.setdefault(src, {"user": 0, "assistant": 0, "fail": 0, "human": 0, "auto": 0,
                                    "ts_min": None, "ts_max": None, "last_file": None})
        st["fail"] += 1
        st["last_file"] = fp

    each_source(cfg, only, on_msg, on_fail)

    for st in stats.values():
        for k in ("ts_min", "ts_max"):
            if st[k]:
                ts_min = st[k] if not ts_min else min(ts_min, st[k])
                ts_max = st[k] if not ts_max else max(ts_max, st[k])
    return {"stats": stats, "human_projects": human_projects, "auto_projects": auto_projects,
            "hours": hours, "night": acc["night"], "weekend": acc["weekend"],
            "human_ts_total": acc["human_ts"], "human_total": acc["human"], "auto_total": acc["auto"],
            "ts_min": ts_min, "ts_max": ts_max, "sample": sample, "inspect": inspect}


def bar_chart(counter, width=28):
    mx = max(counter.values()) if counter else 1
    lines = []
    for h in range(24):
        v = counter.get(h, 0)
        lines.append(f"{h:02d}h │{'█' * max(1, int(v / mx * width)) if v else '·':<{width}}│ {v}")
    return "\n".join(lines)


def pretty_project(key):
    """claude_code 目录名美化（启发式：- 与 _ 有歧义，仅显示用）。
    -home-<user> → ~ ；-home-<user>-a-b → ~/a/b ；-mnt-data-x → /mnt/data/x"""
    if not key.startswith("-"):
        return key
    body = key.lstrip("-")
    if body.startswith("home-"):
        rest = body[len("home-"):]
        if "-" not in rest:            # -home-<user> 本身 → 家目录
            return "~"
        body = "~/" + rest.split("-", 1)[1]   # 剥掉用户名段
    else:
        body = "/" + body
    return body.replace("-", "/").rstrip("/") or "/"


def render_scan(res):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"# Agent Mirror · 数据体检\n", f"> 生成时间 {now} · 全程本地，无数据上行\n"]

    L.append("## 数据源总览\n")
    L.append("| 源 | 真人消息 | 自动消息(cron) | AI消息 | 最早 | 最晚 | 解析失败行 |")
    L.append("|----|---------|---------------|--------|------|------|-----------|")
    for name, st in res["stats"].items():
        f = lambda d: d.strftime("%Y-%m-%d") if d else "-"
        L.append(f"| {name} | {st.get('human',0):,} | {st.get('auto',0):,} | {st.get('assistant',0):,} "
                 f"| {f(st['ts_min'])} | {f(st['ts_max'])} | {st['fail']:,} |")
    span = (res["ts_max"] - res["ts_min"]).days if res["ts_min"] and res["ts_max"] else "-"
    L.append(f"\n**真人消息 {res['human_total']:,} 条** + 自动消息 {res['auto_total']:,} 条，"
             f"时间跨度 **{span}** 天\n")
    if res["auto_total"] > res["human_total"]:
        L.append("⚠️ 自动消息占比过半——画像分析只计真人消息，自动化任务单独作为'部署倾向'信号。\n")

    L.append("## 真人项目投注 Top 15（显示性偏好的原始证据）\n")
    # 按 pretty 名聚合（不同原始 key 可能美化后同名，如 ~ 与 ~/...）
    pretty = Counter()
    for (src, name), n in ((k.split(":", 1), v) for k, v in res["human_projects"].items()):
        pretty[f"{src}:{pretty_project(name)}"] += n
    L.append("| # | 源:项目 | 真人消息 | 占比 |")
    L.append("|---|---------|---------|------|")
    tot_p = sum(pretty.values()) or 1
    for i, (proj, n) in enumerate(pretty.most_common(15), 1):
        src, _, name = proj.partition(":")
        L.append(f"| {i} | {src}:`{name}` | {n:,} | {n / tot_p:.1%} |")

    if res["auto_projects"]:
        L.append("\n## 自动化任务分布 Top 10（你部署了什么 = 另一种职业信号）\n")
        L.append("| # | cron 任务 | 触发次数 |")
        L.append("|---|----------|---------|")
        # heartbeat 是系统注入非用户部署，不列入任务表（计数在总览表）
        jobs = [(p, n) for p, n in res["auto_projects"].most_common(12) if p != "heartbeat"][:10]
        for i, (proj, n) in enumerate(jobs, 1):
            L.append(f"| {i} | {proj} | {n:,} |")

    L.append("\n## 活跃时段分布（仅真人消息）\n\n```\n" + bar_chart(res["hours"]) + "\n```\n")

    if res.get("inspect"):
        L.append(f"## ╵ 时段诊断（该小时真人消息文本前缀 Top10）\n")
        for txt, n in res["inspect"].most_common(10):
            L.append(f"- `{n}×` {txt}")
        L.append("\n> 若出现大量同前缀消息 → 疑似无 [cron: 标记的周期注入，"
                 "把前缀报给维护者加入过滤规则\n")

    ut = res["human_ts_total"] or 1
    L.append("## 工作节律（state 轨道预览，仅真人消息）\n")
    L.append(f"- 深夜比（夜间时段真人消息占比）：**{res['night'] / ut:.1%}**")
    L.append(f"- 周末比：**{res['weekend'] / ut:.1%}**")
    L.append("- 持续 3 周深夜比 >40% 会触发过载预警（Phase 4 接入）\n")

    fails = sum(s["fail"] for s in res["stats"].values())
    if fails:
        L.append(f"## 解析质量\n\n共 {fails:,} 行解析失败（多为一行多 JSON/压缩格式，不影响统计）。"
                 f"失败最多文件示例：{max(res['stats'].values(), key=lambda s: s['fail'])['last_file']}\n")
    return "\n".join(L)


# ──────────────────────── analyze：消息收集与特征提取 ────────────────────────
def collect_human_msgs(cfg, only=None):
    """画像原料（真人 user 消息）。优先吃工件A缓存（build 产物），无缓存现场扫。"""
    a_path, _ = artifact_paths()
    if os.path.exists(a_path):
        msgs = []
        with open(a_path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("role") != "user" or d.get("automated") or not d.get("text"):
                    continue
                if only and d.get("source") != only:
                    continue
                if excluded(cfg, d.get("project", "")):
                    continue
                msgs.append(Msg(d["source"], d["session"], parse_ts(d.get("ts")),
                                "user", d["text"], d.get("project", ""), False))
        return msgs
    msgs = []

    def on_msg(m):
        if m.role == "user" and not m.automated and m.text:
            msgs.append(m)

    def on_fail(fp, src=None):
        pass

    each_source(cfg, only, on_msg, on_fail)
    return msgs


CORRECTION_RE = re.compile(r"不对|错了|不是这个|重来|改一下|又错|搞错")
DIMS = {  # BFAS 证据面板的内置映射（evidence_map YAML 加载失败时的兜底；YAML 可迭代覆盖）
    "O1 智性开放": [("why_questions", "+", "「为什么/原理」类提问密度"),
                  ("followup", "+", "追问率（会话平均轮数）"),
                  ("alternative_seeking", "+", "要求备选/对比方案")],
    "O2 经验开放": [("cross_domain", "?", "跨领域探索（v0 未实现，证据不足）")],
    "C1 勤勉": [("deep_session", "+", "深度会话占比（>3 轮）"),
               ("achievement", "+", "成就导向表达密度"),
               ("verification", "+", "主动要求验证/检查")],
    "C2 秩序": [("planning_first", "+", "计划先行表达密度"),
               ("format_demands", "+", "格式/结构化要求密度")],
    "E1 热情": [("positive", "+", "正性情绪词密度"),
               ("gratitude_politeness", "+", "感谢/礼貌表达密度")],
    "E2 果决": [("imperative", "+", "命令式指令密度（每千字）"),
               ("hedge", "-", "不确定措辞密度（每千字，低=果决）")],
    "A2 礼貌": [("gratitude_politeness", "+", "礼貌修饰密度"),
               ("imperative", "-", "命令式直接施压（低=礼貌）")],
    "N1 情绪波动": [("frustration", "+", "挫败/愤怒表达密度")],
    "N2 退缩": [("anxiety", "+", "焦虑表达密度"),
               ("self_doubt", "+", "自我怀疑措辞密度")],
}
RIASEC_EVID = {  # 职业域：任务谱系(p_* 为占比) → 六型证据
    "R 现实型": [("p_task_debug", "+", "排障/实操类任务占比")],
    "I 研究型": [("p_task_explain", "+", "原理探究类任务占比"),
                ("why_questions", "+", "「为什么/原理」提问密度（每千字）")],
    "A 艺术型": [("p_task_variant", "+", "多版本/变体生成占比")],
    "S 社会型": [("p_task_people", "+", "人际沟通类任务占比")],
    "E 企业型": [("p_task_plan", "+", "规划/拆解/优先级类占比"),
                ("achievement", "+", "成就导向表达密度（每千字）")],
    "C 常规型": [("p_task_batch", "+", "批量/规范化任务占比"),
                ("format_demands", "+", "结构化要求密度（每千字）")],
}
# 语言密度的粗标定满权值（每千字多少次算 1 分；v0 拍定，calibrate 后换回归系数）
LANG_SCALE = {"why_questions": 2.0, "achievement": 15.0, "format_demands": 3.0}


def extract_features(msgs, cfg):
    """真人消息流 → 特征字典（行为 + 语言 + 投注 + 任务谱系）。"""
    F = {}
    total_chars = sum(len(m.text) for m in msgs) or 1
    # ── 会话结构（行为）──
    by_session = {}
    for m in msgs:
        by_session.setdefault((m.source, m.session), []).append(m)
    turns = [len(v) for v in by_session.values()]
    F["sessions_total"] = len(by_session)
    F["avg_turns"] = sum(turns) / len(turns) if turns else 0
    F["deep_session"] = sum(1 for t in turns if t > 3) / len(turns) if turns else 0
    F["followup"] = sum(1 for t in turns if t > 1) / len(turns) if turns else 0
    F["correction_rate"] = sum(1 for m in msgs if CORRECTION_RE.search(m.text)) / len(msgs) if msgs else 0
    # ── 节律 ──
    tsd = [m.ts for m in msgs if m.ts]
    if tsd:
        night = set(cfg["analysis"]["night_hours"])
        F["night_ratio"] = sum(1 for t in tsd if t.hour in night) / len(tsd)
        F["weekend_ratio"] = sum(1 for t in tsd if t.weekday() >= 5) / len(tsd)
        hrs = [t.hour for t in tsd]
        mean_h = sum(hrs) / len(hrs)
        F["rhythm_stability"] = (sum((h - mean_h) ** 2 for h in hrs) / len(hrs)) ** 0.5  # 小时标准差，低=节律稳
    # ── 语言（每千字密度）──
    agg = Counter()
    for m in msgs:
        agg.update(lex_count(m.text))
    for cat in LEXICON:
        F[cat] = agg.get(cat, 0) * 1000 / total_chars
    # ── 场景分层（人×情境：生活情境社会表演少，语言信号更真）──
    scene_cnt = Counter()
    scene_lex = {}          # scene → {类别: 命中}
    scene_chars = Counter()
    for m in msgs:
        sc = scene_of(m.text, m.project)
        scene_cnt[sc] += 1
        scene_chars[sc] += len(m.text)
        agg2 = scene_lex.setdefault(sc, Counter())
        agg2.update(lex_count(m.text))
    tot_scene = sum(scene_cnt.values()) or 1
    F["scene_work_ratio"] = scene_cnt.get("work", 0) / tot_scene
    F["scene_life_ratio"] = scene_cnt.get("life", 0) / tot_scene
    F["scene_unknown_ratio"] = scene_cnt.get("unknown", 0) / tot_scene
    for sc in ("work", "life"):
        ch = scene_chars.get(sc, 0) or 1
        for cat in ("positive", "gratitude_politeness", "frustration", "imperative", "anxiety"):
            F[f"{sc}_{cat}"] = scene_lex.get(sc, Counter()).get(cat, 0) * 1000 / ch
    # ── 投注（按活跃天数归一，防止短时高强度试用盖过主战场）──
    by_proj = {}
    for m in msgs:
        key = f"{m.source}:{m.project}"
        p = by_proj.setdefault(key, {"msgs": 0, "days": set()})
        p["msgs"] += 1
        if m.ts:
            p["days"].add(m.ts.date())
    F["investment"] = sorted(
        ((k, v["msgs"], len(v["days"]), v["msgs"] / max(len(v["days"]), 1))
         for k, v in by_proj.items()), key=lambda x: -x[3])
    # ── 任务谱系（占比）──
    task_hits = {c: agg.get(c, 0) for c in
                 ("task_debug", "task_design", "task_explain", "task_people",
                  "task_plan", "task_batch", "task_tooling", "task_variant")}
    task_total = sum(task_hits.values()) or 1
    for c, v in task_hits.items():
        F[f"p_{c}"] = v / task_total
    return F


def fmt(v):
    if isinstance(v, float):
        return f"{v:.1%}" if v <= 1 else f"{v:.2f}"
    return str(v)


RATE_KEYS = {"night_ratio", "weekend_ratio", "followup", "deep_session", "correction_rate"}


def load_mapping(track):
    """证据映射：优先读 frameworks/evidence_map/*.yaml（总库可迭代的承诺——
    改 YAML 即改面板），解析失败回退内置。YAML 指标名未实现的显示 "-"（=已规划未实现）。"""
    fname = "behavioral.yaml" if track == "personality" else "career_signals.yaml"
    path = os.path.join(ROOT, "frameworks", "evidence_map", fname)
    builtin = DIMS if track == "personality" else RIASEC_EVID
    try:
        import yaml
        data = yaml.safe_load(open(path, encoding="utf-8")) or {}
        out = {}
        for m in data.get("mappings", []):
            dim = str(m.get("dimension", "")).split(".")[0].strip()
            ind = m.get("indicator")
            if not ind or not dim:
                continue
            note = f"[{m.get('confidence', '?')}] {m.get('basis', '')}"
            out.setdefault(dim, []).append((ind, str(m.get("direction", "+")), note))
        merged = {}
        for disp, rows in builtin.items():
            have = {r[0] for r in rows}       # 同名去重：内置已实现的优先，YAML 不重复追加
            merged[disp] = rows + [r for r in out.pop(disp.split()[0], []) if r[0] not in have]
        return merged                       # 未匹配 code 的维度(state.*/env.*/价值观)留给 v0.2 分数层
    except Exception:
        return builtin


def fmt_ind(ind, v):
    """单位感知显示：占比→%，语言密度→次/千字，均值→自带单位。"""
    if v is None:
        return "-"
    if ind.startswith("p_") or ind in RATE_KEYS:
        return f"{v:.1%}"
    if ind in LEXICON or ind.startswith(("work_", "life_")):
        return f"{v:.2f}次/千字"
    if ind == "avg_turns":
        return f"{v:.1f}轮"
    if ind == "rhythm_stability":
        return f"{v:.1f}h"
    return fmt(v)


def build_panel(F, mapping):
    """特征 → 证据面板 [{维度: [(指标, 值, 方向, 说明)] }]，缺指标的条目标注。"""
    panel = {}
    for dim, items in mapping.items():
        rows = []
        for ind, direction, note in items:
            val = fmt_ind(ind, F.get(ind)) if ind in F else "-"
            rows.append((ind, val, direction, note))
        panel[dim] = rows
    return panel


def is_evidence(val):
    """真证据 = 有值且数值 > 0（"-" 未实现 / "0.00次/千字" 零命中都不算；
    "0.02次/千字" 是低频真证据，字符串前缀判断会误杀，须解析数值）。"""
    if val == "-":
        return False
    m = re.search(r"[\d.]+", val)
    return bool(m) and float(m.group()) > 0


def evidence_strength(rows):
    """证据强度条：真命中（非零）指标的粗分档。"""
    hit = sum(1 for _, v, d, _ in rows if is_evidence(v) and d not in ("?",))
    return "▮" * min(hit, 5) + "▯" * max(5 - hit, 0) + f"  {hit} 项"


# ──────────────────────── LLM 层（OpenAI 兼容，BARS / 统计解读） ────────────────────────
def llm_key(cfg):
    """key 解析：config 指定的 env/文件优先 → AM_LLM_API_KEY / ~/.am-llm-key →
    旧名 CPD_LLM_API_KEY / ~/.cpd-llm-key（向后兼容，老配置不迁移也能用）。"""
    llmcfg = cfg.get("llm") or {}
    envs = [llmcfg.get("api_key_env"), "AM_LLM_API_KEY", "CPD_LLM_API_KEY"]
    for env in envs:
        if env and os.environ.get(env):
            return os.environ.get(env)
    files = [llmcfg.get("api_key_file"), "~/.am-llm-key", "~/.cpd-llm-key"]
    for kf in files:
        if not kf:
            continue
        try:
            with open(os.path.expanduser(kf)) as f:
                key = f.read().strip()
                if key:
                    return key
        except Exception:
            pass
    return None


def llm_chat(cfg, system, user, max_tokens=3000):
    import urllib.request
    key = llm_key(cfg)
    if not key:
        return None
    url = cfg["llm"].get("base_url", "").rstrip("/") + "/chat/completions"
    model = cfg["llm"].get("model") or ""
    body = {"messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0.2}
    if model:
        body["model"] = model
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def redact(text, patterns):
    for pat, rep in patterns or []:
        try:
            text = re.sub(pat, rep, text)
        except re.error:
            pass
    return text


def llm_analyze(cfg, track, F, msgs):
    """按 privacy 档位决定 LLM 能看到什么：
    stats_only → 只发统计指标（解读，不评分）；redacted → 发脱敏样本（BARS 评分）"""
    exposure = (cfg.get("privacy") or {}).get("llm_exposure", "stats_only")
    if exposure == "off":
        return "*(privacy=off，LLM 层已禁用)*"
    scope = ("BFAS 十维度（智性/求新·勤勉/秩序·热情/果决·共情/礼貌·波动/退缩）"
             if track == "personality" else "RIASEC 六型（现实/研究/艺术/社会/企业/常规）+ O*NET 工作价值观")
    stats_text = "\n".join(f"- {k}: {fmt(v)}" for k, v in F.items()
                           if isinstance(v, (int, float)) and not k.startswith("task"))
    if exposure == "redacted" and msgs:
        pats = (cfg.get("privacy") or {}).get("redact_patterns", [])
        samples = "\n---\n".join(redact(m.text[:500], pats) for m in msgs[:30])
        prompt = (f"你是受过心理测量训练的评估助手。基于以下用户与其 AI 助手的真实对话样本（已脱敏），"
                  f"按 {scope} 逐维度评分（1-5，行为锚定：1=几乎无此行为证据，5=证据非常充分），"
                  f"每个维度给出：分数、支持证据（引用原文片段）、不确定说明。\n\n"
                  f"对话样本：\n{samples}\n\n统计特征（辅助）：\n{stats_text}")
    else:
        prompt = (f"你是人格测量解读助手。以下是某用户 6 个月 AI 交互的统计特征（无原文，"
                  f"语言类为每千字密度）。请按 {scope} 逐维度解读：这些指标组合指向什么行为模式、"
                  f"哪些维度证据充分哪些不足、有什么需要警惕的过度解读。输出 markdown，"
                  f"禁止给出绝对分数，只做证据方向性解读。\n\n{stats_text}")
    try:
        return llm_chat(cfg, "你是严谨的心理测量助手，遵循证据、避免过度解读、用中文输出。", prompt)
    except Exception as e:
        return f"*(LLM 层调用失败: {e})*"


# ──────────────────────── analyze 报告 ────────────────────────
def render_analyze(track, F, panel, mapping, llm_text, n_msgs, days, verified=None, mode="rules", headline=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = "性格镜像" if track == "personality" else "职业罗盘"
    status = (f"✅ 状态：**已校准**（{verified} 条当事人回答）" if verified
              else "📝 状态：**初稿** —— 跑 `confirm` 校验后重跑 analyze 才是定稿")
    engine = {"rules": "规则层（无 LLM 参与）",
              "hybrid": "双层：规则层 + LLM 解读",
              "llm": "LLM 层"}[mode]
    L = [f"# {title} v0 · 证据面板\n",
         f"> 生成 {now} · {n_msgs} 条真人消息 / 跨度 {days} 天 · {engine}",
         f"> {status}",
         "> ⚠️ v0 方法说明：无人群常模对照，**不输出绝对分数**；面板列出每维度的行为证据与方向，"
         "用于纵向自我对照（与上一次快照比）。calibrate（BFI-2 自评对照）接入后才升格为分数。\n"]
    if headline:
        # 结论前置：1 秒看懂测出啥（原型/签名/组合身份/霍兰德），细节解读在下方证据面板
        L.append(headline)
        L.append("\n---\n")
    if track == "personality":
        L.append("## BFAS 十维 · 证据面板\n")
        for dim, rows in panel.items():
            L.append(f"### {dim} · 证据强度 {evidence_strength(rows)}\n")
            for ind, val, direction, note in rows:
                if val == "-":
                    arrow = "· 待采集（指标未实现）"
                elif is_evidence(val):
                    arrow = {"+": "→ 支持", "-": "→ 反向", "?": "→ 待证"}.get(direction, "")
                else:
                    arrow = "· 零命中（不计证据）"
                L.append(f"- `{ind}` = **{val}** {arrow} — {note}")
            L.append("")
        L.append("## 状态信号（state 轨道，不映射特质）\n")
        L.append(f"- 深夜比 `{fmt_ind('night_ratio', F.get('night_ratio'))}` · "
                 f"周末比 `{fmt_ind('weekend_ratio', F.get('weekend_ratio'))}` · "
                 f"工作时段标准差 `{fmt_ind('rhythm_stability', F.get('rhythm_stability'))}`（低=节律稳定）")
        L.append(f"- 会话均轮数 `{fmt_ind('avg_turns', F.get('avg_turns'))}` · "
                 f"深度会话占比 `{fmt_ind('deep_session', F.get('deep_session'))}` · "
                 f"纠错率 `{fmt_ind('correction_rate', F.get('correction_rate'))}`\n")
        # 场景分层（人×情境）
        if F.get("scene_work_ratio") is not None:
            L.append("### 场景分布（人 × 情境）\n")
            L.append(f"- 工作 `{F.get('scene_work_ratio', 0):.0%}` · "
                     f"生活 `{F.get('scene_life_ratio', 0):.0%}` · "
                     f"未分类 `{F.get('scene_unknown_ratio', 0):.0%}`")
            if F.get("scene_life_ratio", 0) > 0.05:
                L.append("\n| 语言信号 | 工作情境 | 生活情境 | 解读 |")
                L.append("|---------|---------|---------|------|")
                pairs = [("positive", "正性情绪"), ("gratitude_politeness", "礼貌/感谢"),
                         ("frustration", "挫败表达"), ("imperative", "命令式")]
                for cat, zh_ in pairs:
                    w, l = F.get(f"work_{cat}", 0), F.get(f"life_{cat}", 0)
                    if w == 0 and l == 0:
                        continue
                    diff = "生活更高" if l > w * 1.5 else ("工作更高" if w > l * 1.5 else "两情境相近")
                    L.append(f"| {zh_} | {w:.2f} | {l:.2f} | {diff} |")
                L.append("\n> 情境差异本身就是画像：工作里的表达带职业角色成分，"
                         "生活情境的信号更接近本真（社会表演少）。两情境差异大 = 情境特异性高。\n")
            else:
                L.append("\n> 生活情境样本 <5%，情境分层暂不出表（样本不足，避免过度解读）。\n")
    else:
        L.append("## RIASEC 六型 · 证据面板\n")
        scores = {}
        for dim, rows in panel.items():
            s = 0.0
            for ind, val, direction, note in rows:
                if ind not in F or direction != "+":
                    continue
                if ind.startswith("p_"):            # 占比类：0.5 占比 = 1 分
                    s += F[ind] * 2
                else:                                # 语言密度类：按标定满权值归一
                    s += min(F[ind] / LANG_SCALE.get(ind, 2.0), 1.0)
            scores[dim] = s
            L.append(f"### {dim} · 加权分 {s:.2f}\n")
            for ind, val, direction, note in rows:
                if val == "-":
                    arrow = "· 待采集（指标未实现）"
                elif is_evidence(val):
                    arrow = {"+": "→ 支持", "-": "→ 反向", "?": "→ 待证"}.get(direction, "")
                else:
                    arrow = "· 零命中（不计证据）"
                L.append(f"- `{ind}` = **{val}** {arrow} — {note}")
            L.append("")
        rank = riasec_rank(F, mapping)
        code = "".join(k[0] for k, _ in rank[:3])   # 霍兰德代码（前三高）
        L.append("## 兴趣代码（单用户内相对排序，方法学有效）\n")
        L.append(f"**{code}** — " + " > ".join(f"{k.split()[1]}({v:.2f})" for k, v in rank) + "\n")
        # 六型解读（前三高展开，来自 riasec.yaml 权威定义）
        types = load_riasec()
        if types:
            L.append("### 类型解读（前三高）\n")
            for k, v in rank[:3]:
                letter = k.split()[0]
                t = types.get(letter, {})
                if not t:
                    continue
                L.append(f"**{k.split()[1]}（{v:.2f}）**：{t.get('desc','')}")
                L.append(f"- 典型环境：{'、'.join(t.get('work_env', [])[:4])}")
                L.append(f"- AI 使用签名：{t.get('ai_usage_signature','')}\n")
        L.append("## 兴趣投注 Top 10（综合分 = 消息数 × √活跃天数，兼顾量与持续性）\n")
        L.append("| 项目 | 消息数 | 活跃天数 | 综合分 |")
        L.append("|------|-------|---------|--------|")
        inv = [(k, n, d, n * (d ** 0.5)) for k, n, d, _ in F["investment"]]
        for k, n, d, s in sorted(inv, key=lambda x: -x[3])[:10]:
            src, _, name = k.partition(":")
            L.append(f"| {src}:`{pretty_project(name)}` | {n} | {d} | {s:.0f} |")
        L.append("")
    if llm_text:
        L.append("## LLM 层解读\n\n" + llm_text + "\n")
    return "\n".join(L)


# ──────────────────────── 人读报告：markdown → 自包含 HTML（零依赖） ────────────────────────
def md_to_html(md):
    """受控 markdown 子集 → HTML。只处理 render_analyze 会产出的格式：
    标题 / 表格 / 无序列表 / 引用块 / 粗体 / 行内码 —— 不做通用解析器。"""
    import html as _h
    lines, out, i = md.split("\n"), [], 0

    def inline(s):
        s = _h.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s

    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif ln.startswith("#"):
            n = min(len(ln) - len(ln.lstrip("#")), 4)
            out.append(f"<h{n}>{inline(ln.lstrip('#').strip())}</h{n}>")
            i += 1
        elif ln.startswith("|"):                        # 表格（跳过分隔行 |---|）
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append(cells)
                i += 1
            if rows:
                out.append("<table>" + "".join(
                    "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
                    + "</table>")
        elif ln.lstrip().startswith("- "):              # 无序列表
            items = []
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                items.append(f"<li>{inline(lines[i].lstrip()[2:])}</li>")
                i += 1
            out.append(f"<ul>{''.join(items)}</ul>")
        elif ln.startswith(">"):                        # 引用块（状态/方法说明/自白）
            quotes = []
            while i < len(lines) and lines[i].startswith(">"):
                quotes.append(inline(lines[i].lstrip(">").strip()))
                i += 1
            out.append(f"<blockquote>{'<br>'.join(quotes)}</blockquote>")
        else:
            out.append(f"<p>{inline(ln)}</p>")
            i += 1
    return "\n".join(out)


_REPORT_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f6f7fb;color:#1f2430;
  font:15px/1.8 -apple-system,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:32px 20px 64px}
.mast{color:var(--accent);font-weight:700;font-size:13px;letter-spacing:1px}
h1{font-size:25px;margin:.25em 0 .4em}
h2{font-size:19px;margin-top:2.2em;padding-bottom:.3em;border-bottom:2px solid var(--accent)}
h3{font-size:16px;margin-top:1.5em}
h4{font-size:15px;margin-top:1.2em}
p{margin:.5em 0}
code{background:#eef0f6;border:1px solid #e4e7ef;border-radius:4px;padding:0 6px;
  font:13px/1.6 ui-monospace,Menlo,Consolas,monospace;white-space:nowrap}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:14px;
  background:#fff;border:1px solid #e4e7ef;border-radius:8px;overflow:hidden}
td{border-top:1px solid #edeff5;padding:8px 12px;text-align:left}
tr:first-child td{background:#f0f2f8;font-weight:600;border-top:none}
tr:nth-child(even) td{background:#fafbfd}
ul{padding-left:1.4em;margin:.6em 0}
li{margin:.3em 0}
blockquote{margin:1em 0;padding:12px 16px;border-left:4px solid #f0b429;
  background:#fff8e8;border-radius:6px;color:#6b5518}
blockquote strong{color:#8a6410}
footer{margin-top:48px;color:#8a93a3;font-size:12px;text-align:center}
@media print{body{background:#fff}.wrap{padding:0}}
"""


def report_html(track, md):
    """报告 markdown → 自包含 HTML（CSS 内联，浏览器直接打开 / 打印 PDF / 发分享，零依赖）。"""
    title = "性格镜像" if track == "personality" else "职业罗盘"
    accent = "#7c5cff" if track == "personality" else "#0d9488"
    return (f'<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>Agent Mirror · {title}</title>\n'
            f'<style>:root{{--accent:{accent}}}\n{_REPORT_CSS}</style></head>\n'
            f'<body><div class="wrap"><div class="mast">🪞 AGENT MIRROR</div>\n'
            f'{md_to_html(md)}\n'
            f'<footer>Agent Mirror v0 · 本地生成 · 原始会话数据不出本机</footer>\n'
            f'</div></body></html>\n')


# ──────────────────────── 知识库层：工件A/B + build / search / ask ────────────────────────
def collect_all_msgs(cfg, only=None):
    """全量收集（human + assistant + automated）—— 工件A 的原料，知识库要全面。"""
    msgs = []

    def on_msg(m):
        if m.text:
            msgs.append(m)

    def on_fail(fp, src=None):
        pass

    each_source(cfg, only, on_msg, on_fail)
    return msgs


def artifact_paths():
    idx = os.path.join(ROOT, "index")
    return os.path.join(idx, "messages.jsonl"), os.path.join(idx, "knowledge.sqlite")


def msg_to_line(m):
    return {"source": m.source, "session": m.session,
            "ts": m.ts.isoformat(sep=" ") if m.ts else None, "role": m.role,
            "text": m.text, "project": m.project, "automated": m.automated}


def excluded(cfg, project):
    ex = (cfg.get("sources") or {}).get("exclude_projects") or []
    return any(x in (project or "") for x in ex)


def build_knowledge(cfg, only=None):
    """全量重建 工件A(messages.jsonl) + 工件B(knowledge.sqlite FTS5)。幂等。"""
    import sqlite3
    msgs = [m for m in collect_all_msgs(cfg, only) if not excluded(cfg, m.project)]
    a_path, b_path = artifact_paths()
    os.makedirs(os.path.dirname(a_path), exist_ok=True)
    n_auto = n_asst = n_human = 0
    with open(a_path, "w", encoding="utf-8") as f:
        for m in msgs:
            f.write(json.dumps(msg_to_line(m), ensure_ascii=False) + "\n")
            if m.automated:
                n_auto += 1
            elif m.role == "assistant":
                n_asst += 1
            else:
                n_human += 1
    if os.path.exists(b_path):
        os.remove(b_path)
    db = sqlite3.connect(b_path)
    tok = ", tokenize='trigram'"          # 中文子串检索；老 SQLite 自动降级
    try:
        db.execute(f"CREATE VIRTUAL TABLE _probe USING fts5(t{tok})")
        db.execute("DROP TABLE _probe")
    except Exception:
        tok = ""
    db.execute(f"CREATE VIRTUAL TABLE entries USING fts5("
               f"text, source UNINDEXED, session UNINDEXED, project UNINDEXED, "
               f"role UNINDEXED, ts UNINDEXED{tok})")
    db.execute(f"CREATE VIRTUAL TABLE auto_events USING fts5("
               f"text, source UNINDEXED, project UNINDEXED, ts UNINDEXED{tok})")
    n_e = n_a = 0
    for m in msgs:
        ts = m.ts.isoformat(sep=" ") if m.ts else None
        if m.automated:                    # 部署历史：独立表，默认不搜（权重隔离，见 DATAFLOW.md）
            db.execute("INSERT INTO auto_events(text, source, project, ts) VALUES (?,?,?,?)",
                       (m.text[:400], m.source, m.project, ts))
            n_a += 1
        elif m.role == "assistant" and len(m.text.strip()) >= 40 and m.text.strip() != "NO_REPLY":
            db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?)",
                       (m.text, m.source, m.session, m.project, m.role, ts))
            n_e += 1
        elif m.role == "user" and not m.automated:
            db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?)",
                       (m.text, m.source, m.session, m.project, m.role, ts))
            n_e += 1
    db.commit()
    db.close()
    return {"total": len(msgs), "human": n_human, "assistant_kept": n_e - n_human if n_e > n_human else 0,
            "entries": n_e, "auto_events": n_a, "a_path": a_path, "b_path": b_path}


STOP_CHARS = set("的了在是我有过和就都不这那你他它吗吧呢啊哦呀与之其此为以被从对到给跟让向则很再又还")
STOP_WORDS = {"问题", "遇到", "之前", "哪些", "怎么", "什么", "现在", "可以", "应该",
              "一下", "看看", "帮我", "这个", "那个", "时候", "东西", "事情", "是不是",
              "有没有", "关于", "进行", "出现", "发生"}   # 高频疑问/泛词，检索时降权为噪音


def content_words(query):
    """自然语言 → 检索词：英文/数字词 + 中文2字滑窗(滤停用) + 4字精确块。
    滤字级停用 + 词级停用（实测教训：泛词会稀释真关键词，如「遇到/问题」）。"""
    words = re.findall(r"[A-Za-z0-9_\-]{2,}", query)
    for seg in re.findall(r"[一-鿿]+", query):
        words += [seg[i:i + 2] for i in range(len(seg) - 1)
                  if seg[i] not in STOP_CHARS and seg[i + 1] not in STOP_CHARS
                  and seg[i:i + 2] not in STOP_WORDS]
        for i in range(0, len(seg) - 2):
            words.append(seg[i:i + 4])          # 4字精确块，命中权重更高
    seen, out = set(), []
    for w in words:
        if w not in seen and len(w) >= 2:
            seen.add(w)
            out.append(w)
    return out[:8]                              # SQL 长度保护


def search_rows(query, limit=10, include_auto=False):
    """多词 LIKE OR + 命中加权排序。两条实测教训写进排序策略：
    ① 长 assistant 回复是排障记录的主要载体 → length DESC（信息量优先，短引用靠后）
    ② 同一会话霸榜（自指会话短而密）→ session 多样性：每会话最多 2 条"""
    import sqlite3
    _, b_path = artifact_paths()
    if not os.path.exists(b_path):
        return None                             # 未建库
    words = content_words(query)
    if not words:
        return []
    db = sqlite3.connect(b_path)
    conds = " OR ".join(["text LIKE ?"] * len(words))
    params = [f"%{w}%" for w in words]
    score = " + ".join(f"(CASE WHEN text LIKE ? THEN {2 if len(w) >= 3 else 1} ELSE 0 END)"
                       for w in words)
    sql = (f"SELECT text, source, project, ts, role, session, ({score}) AS s FROM entries "
           f"WHERE {conds} ORDER BY s DESC, length(text) DESC LIMIT ?")
    raw = db.execute(sql, params + params + [limit * 3]).fetchall()
    picked, per_sess = [], Counter()
    for r in raw:
        key = (r[1], r[5])
        if per_sess[key] >= 2:                  # session 多样性
            continue
        per_sess[key] += 1
        picked.append((r[0], r[1], r[2], r[3], r[4]))
        if len(picked) >= limit:
            break
    if include_auto:
        try:
            picked += [(r[0], r[1], r[2], r[3], r[4]) for r in db.execute(
                f"SELECT text, source, project, ts, role, ({score}) AS s FROM auto_events "
                f"WHERE {conds} ORDER BY s DESC LIMIT ?", params + params + [limit // 2])]
        except Exception:
            pass
    db.close()
    return picked


def load_cached_msgs():
    """读工件A → list[dict]（ask 拼上下文用）。"""
    a_path, _ = artifact_paths()
    out = []
    if os.path.exists(a_path):
        with open(a_path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def ask_llm(cfg, question, rows, cached):
    """检索命中 → 拼上下文（每条带前后1条）→ LLM 归纳。"""
    by_key = {}
    for i, d in enumerate(cached):
        by_key.setdefault((d["source"], d["session"]), []).append(i)
    idx_of = {}
    for key, idxs in by_key.items():
        for pos, i in enumerate(idxs):
            idx_of[i] = (key, pos)
    frags = []
    for text, source, project, ts, role in rows[:8]:
        head = f"[{source} · {project} · {ts} · {role}]"
        frags.append(f"{head}\n{text[:600]}")
    ctx = "\n---\n".join(frags)
    prompt = (f"以下是用户与其 AI 助手的历史对话片段（已检索）。请回答用户问题；"
              f"回答须引用片段内容（标注来源），片段不足以回答的部分明确说明不知道，禁止编造。\n\n"
              f"问题：{question}\n\n片段：\n{ctx}")
    return llm_chat(cfg, "你是严谨的知识库问答助手，忠于检索证据，用中文简洁回答。", prompt)


# ──────────────────────── confirm：当事人校验（informant validation） ────────────────────────
# Sphinx 式"最后一关考考你"：画像证据定稿前，让用户确认——防盲目接受 AI 判断，
# 也是 stated preference 与 revealed preference 的交叉验证（见 METHODOLOGY §5 前身）。

def load_calibration(track):
    p = os.path.join(ROOT, "profiles", track, "calibration.yaml")
    if not os.path.exists(p):
        return None
    try:
        import yaml
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


DIMNAME = {"O1": "智性开放", "O2": "经验开放", "C1": "勤勉", "C2": "秩序",
           "E1": "热情", "E2": "果决", "A2": "礼貌", "N1": "情绪波动", "N2": "退缩",
           # 霍兰德六型（career 域 SJT 投票用，与 BFAS 两字符码无冲突）
           "R": "现实型", "I": "研究型", "A": "艺术型", "S": "社会型", "E": "企业型", "C": "常规型"}


def load_sjt_bank():
    """读 SJT 情景题库（frameworks/personality/sjt_bank.yaml）。"""
    path = os.path.join(ROOT, "frameworks", "personality", "sjt_bank.yaml")
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f).get("scenarios", [])
    except Exception:
        return []


def supplement_questions(track, panel, cal, n_evidence):
    """确定性补充题（指标/数字全部来自真实面板，不经 LLM——杜绝幻觉数字）：
    ① 证据确认题：命中最多的未确认维度（按命中数降序取 n_evidence 个），
       答案直接产生 confirmed / discount 档位——"倾向具备"强档的关键来源；
    ② 盲区自评题：零证据维度取 1，答案产生 stated 高/中/低。
    结合已有校准：已确认 / 已自评 / 被否认的维度都不重出。"""
    disc_dims = {a.get("dimension") for a in (cal or {}).get("answers", [])
                 if str(a.get("effect", "")).startswith("discount")}
    answered_dims = {a.get("dimension") for a in (cal or {}).get("answers", [])
                     if str(a.get("effect", "")).startswith(("confirmed", "stated"))}
    qs, effects = [], []
    by_dim = {}
    for dim, rows in panel.items():
        if dim in disc_dims or dim in answered_dims:
            continue
        for ind, val, direction, note in rows:
            if is_evidence(val) and direction == "+":
                by_dim.setdefault(dim, []).append((ind, val, note))
    for dim in sorted(by_dim, key=lambda d: -len(by_dim[d]))[:n_evidence]:
        ind, val, note = by_dim[dim][0]
        qs.append({"type": "evidence", "dimension": dim, "indicator": ind,
                   "text": (f"数据显示：{note}（`{ind}` = {val}），被解读为支持「{dim}」。\n"
                            f"   哪个更符合你的实际？"),
                   "options": ["准确，这个解读成立",
                               "不准——那是情境要求，不代表我这个人（对 AI 如此，对人未必）",
                               "不准——数据没看到全貌（非工作时段我用别的工具/渠道）"]})
        effects.append({"A": "confirmed", "B": "discount_context", "C": "discount_blindspot"})
    blind_desc = {
        "O2 经验开放": "过去半年你自愿尝试新工具/新框架的程度",
        "N2 退缩": "你面对不确定时的焦虑/自我怀疑程度",
        "A1 共情": "你在协作中关注他人感受/处境的程度（AI 数据难以观测）",
        "O1 智性开放": "你在没人要求时主动深挖原理的程度",
        "C1 勤勉": "你投入长任务并坚持收尾的程度",
        "C2 秩序": "你做事先列计划/结构化的程度",
        "E1 热情": "你表达正性情绪（兴奋/赞赏）的频率",
        "E2 果决": "你快速拍板/改向的程度",
        "A2 礼貌": "你表达请求时的礼貌修饰习惯",
        "N1 情绪波动": "你遇到阻碍时的挫败反应强度",
    }
    blind_pool = [dim for dim, rows in panel.items()
                  if all(not is_evidence(v) for _, v, _, _ in rows)
                  and dim not in disc_dims and dim not in answered_dims]
    if blind_pool:
        dim = blind_pool[0]
        qs.append({"type": "blindspot", "dimension": dim,
                   "text": f"数据盲区：{blind_desc.get(dim, dim)}——行为数据测不到或证据不足，需要你自评。\n   更接近哪种？",
                   "options": ["明显高于一般人", "和一般人差不多", "明显低于一般人"]})
        effects.append({"A": "stated_high", "B": "stated_mid", "C": "stated_low"})
    return qs, effects


def generate_questions(track, F, panel, cal=None):
    """出题（v3 · 自适应，目标 = 凑齐结论）：题数不固定（4~6），缺什么补什么——
    性格域 = 2 道 SJT 情景题（随机抽；行为票参与档位判定，≥2 票 = 行为确认强档）
              + 证据确认题（强证据未确认维度 ×2）+ 盲区自评 ×1；
    职业域 = 证据确认 ×3 + 盲区自评 ×1（rules 题库只有 BFAS 编码题，职业域 SJT
              仅在 LLM 模式由 llm_questions 现场生成，votes 投霍兰德六型）。
    SJT 依据：情景判断测验效度优于自我报告（McDaniel et al. 2001）。"""
    import random
    qs, effects = [], []
    if track == "personality":
        bank = [s for s in load_sjt_bank() if s.get("options")]
        for sc in random.sample(bank, min(2, len(bank))):
            opts, votes_map = [], {}
            for j, o in enumerate(sc["options"][:4]):
                letter = "ABCD"[j]
                opts.append(str(o.get("text", "")))
                votes_map[letter] = o.get("votes", {})
            qs.append({"type": "sjt", "dimension": "情景判断",
                       "text": sc.get("scene", ""), "options": opts, "votes": votes_map})
            effects.append(None)                     # SJT 走 votes 分支，不用单一 effect
        n_ev = 2
    else:
        n_ev = 3
    sqs, seff = supplement_questions(track, panel, cal, n_evidence=n_ev)
    return qs + sqs, effects + seff


def robust_json_list(raw):
    """从 LLM 输出提取 JSON 数组（小模型友好）：贪婪匹配 → 字符修复重试。
    常见修复：中文标点→英文、全角引号、尾逗号、```json 围栏。"""
    if not raw:
        return None
    s = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    m = re.search(r"\[.*\]", s, re.S)
    if not m:
        return None
    s = m.group()
    for attempt in range(2):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            if attempt == 0:
                s = (s.replace("，", ",").replace("：", ":")
                      .replace("“", '"').replace("”", '"')
                      .replace("‘", "'").replace("’", "'"))
                s = re.sub(r",\s*([\]}])", r"\1", s)      # 尾逗号
    return None


def llm_questions(cfg, track, F, panel, cal):
    """LLM 现场出题（主路径，双域）：LLM 只负责它擅长的——把 analyze 的完整特征
    （项目投注/证据面板/语言行为特征/上次校准反馈）变成贴合用户本人的 SJT 情景题；
    性格域 votes 投 BFAS 维度，职业域 votes 投霍兰德六型（RIASEC）；
    证据确认题与盲区自评题由 supplement_questions 确定性补上（数字来自真实面板，
    不经 LLM——题型混合靠构造保证，目标 = 凑齐档位判定所需信息）。
    固定题库仅作 few-shot 示例定格式 + 无 key 兜底（题库只有 BFAS 编码题，
    rules 模式下职业域不出 SJT——LLM 模式才有）。"""
    is_career = (track == "career")
    invest = "；".join(f"{k.split(':', 1)[-1].split('/')[-1][:30]}({n}条/{d}天)"
                       for k, n, d, *_ in F.get("investment", [])[:5]) or "通用"
    # 证据面板摘要：各维度命中数
    panel_text = "; ".join(f"{dim}:{sum(1 for _, v, d, _ in rows if is_evidence(v) and d != '?')}项"
                           for dim, rows in panel.items())
    # 语言/行为特征精选（LLM 感知用户的表达风格）
    style = {k: round(F.get(k, 0), 2) for k in
             ("imperative", "gratitude_politeness", "planning_first", "why_questions",
              "frustration", "avg_turns", "deep_session", "night_ratio", "scene_work_ratio")}
    cal_text = (json.dumps(cal.get("answers", [])[:10], ensure_ascii=False)
                if cal and cal.get("answers") else "无")
    if is_career:
        VALID = {"R", "I", "A", "S", "E", "C"}
        sysmsg = ("你是严谨的职业心理学出题助手（Holland RIASEC 框架），"
                  "只输出纯 JSON，不要任何解释和代码围栏。")
        fewshot = json.dumps([{"scene": "周五晚十点，你终于有空碰自己的项目。三块屏幕摆着："
                                        "A 项目的渲染管线又崩了、B 项目有个没验证的算法思路、"
                                        "C 是一直想重构的配置面板。你先打开哪个？",
                               "options": [{"text": "修崩的管线——看到红叉就想按下去", "votes": {"R": "+"}},
                                           {"text": "把算法思路推一步，看看能不能证明", "votes": {"I": "+"}},
                                           {"text": "重构面板，顺便把配色调漂亮", "votes": {"A": "+"}},
                                           {"text": "先翻翻群消息，看看大家在忙什么", "votes": {"S": "+"}}]}],
                              ensure_ascii=False)
        header = ("你是职业心理学出题助手（Holland RIASEC）。基于下面这位用户的真实 AI 使用画像，"
                  "现场生成 2~3 道 SJT 情景判断题（RIASEC 版）。")
        rule_scene = ("1. 每题 = 具体的任务/项目/环境选择场景（贴合用户真实投注领域），测的是"
                      "兴趣与环境偏好（霍兰德框架），不是能力——问'你会先碰哪个/怎么选'，不问'你会不会'")
        rule_votes = ("2. 每个选项挂 votes：霍兰德六型字母（R现实/I研究/A艺术/S社会/E企业/C常规）"
                      "映射 \"+\"/\"-\"，可投 1~3 个型")
    else:
        VALID = {"O1", "O2", "C1", "C2", "E1", "E2", "A2", "N1", "N2"}
        sysmsg = "你是严谨的心理测量出题助手，只输出纯 JSON，不要任何解释和代码围栏。"
        bank = load_sjt_bank()
        fewshot = json.dumps(bank[0], ensure_ascii=False) if bank else "（无示例）"
        header = "你是心理测量出题助手。基于下面这位用户的真实 AI 使用画像，现场生成 2~3 道 SJT 情景判断题。"
        rule_scene = ("1. 每题 = 具体有画面感的场景（贴合用户真实领域和任务类型）+ 4 个行为选项"
                      "（第一人称会对 AI 说的话/会做的事）")
        rule_votes = ("2. 每个选项挂 votes：BFAS 维度代码（O1智性/O2求新/C1勤勉/C2秩序/E1热情/E2果决/"
                      "A2礼貌/N1波动/N2退缩）映射 \"+\"/\"-\"，可投 1~3 个维度")
    prompt = (
        f"{header}\n\n"
        f"## 用户画像（题目要贴合此人）\n"
        f"- 项目投注（领域）：{invest}\n"
        f"- 证据面板（维度命中数）：{panel_text}\n"
        f"- 表达与行为特征：{json.dumps(style, ensure_ascii=False)}\n"
        f"- 上次校准反馈（含历史投票，被否认的维度换角度出题）：{cal_text}\n\n"
        f"## 格式示例（仅定格式/风格，内容必须基于用户画像原创）\n{fewshot}\n\n"
        "## 出题规则\n"
        f"{rule_scene}\n"
        f"{rule_votes}\n"
        "3. 选项间要有真实区分度；禁止元问题（不问'准不准'，只问'你会怎么做'）\n"
        "4. 优先覆盖：证据强但未确认的维度（行为票可升强档）、证据弱/盲区的维度"
        "（从面板命中数看出来）、上次被否认的维度换角度出题\n"
        "5. 只输出 JSON 数组，无其他文字："
        '[{"scene":"...","options":[{"text":"...","votes":{"O1":"+"}}]}]')
    try:
        raw = llm_chat(cfg, sysmsg, prompt, max_tokens=3000)
        if not raw:
            print("⚠️ LLM 无返回（key/端点/额度？），回退固定题库", file=sys.stderr)
            return None
        items = robust_json_list(raw)
        if not items:
            print(f"⚠️ LLM 输出 JSON 解析失败（截断或格式错），回退固定题库。原始输出前 120 字：{raw[:120]!r}", file=sys.stderr)
            return None
        qs, effects = [], []
        # VALID 按域取（BFAS 九维 或 霍兰德六型），过滤 LLM 手滑代码
        for it in items[:3]:
            opts_raw = [o for o in it.get("options", []) if o.get("text")][:4]
            if len(opts_raw) < 3:
                continue
            opts = [str(o["text"]) for o in opts_raw]
            votes_map = {}
            for j, o in enumerate(opts_raw):
                votes_map["ABCD"[j]] = {str(k): str(v) for k, v in (o.get("votes") or {}).items()
                                        if str(k) in VALID and str(v) in ("+", "-")}
            qs.append({"type": "sjt", "dimension": "情景判断",
                       "text": str(it.get("scene", "")), "options": opts, "votes": votes_map})
            effects.append(None)
        if len(qs) >= 2:
            # 确定性补充：证据确认 + 盲区自评（数字来自真实面板，构造上保证题型齐全）
            sqs, seff = supplement_questions(track, panel, cal, n_evidence=2)
            return qs + sqs, effects + seff
        print("⚠️ LLM 题目结构不合格，回退固定题库", file=sys.stderr)
        return None
    except Exception as e:
        print(f"⚠️ LLM 出题异常: {type(e).__name__}: {e}，回退固定题库", file=sys.stderr)
        return None


def pending_path(track):
    return os.path.join(ROOT, "profiles", track, "pending.yaml")


def save_pending(track, qs, effects):
    os.makedirs(os.path.dirname(pending_path(track)), exist_ok=True)
    with open(pending_path(track), "w", encoding="utf-8") as f:
        json.dump({"questions": qs, "effects": effects,
                   "generated": datetime.now().isoformat(timespec="minutes")}, f,
                  ensure_ascii=False, indent=1)


def load_pending(track):
    p = pending_path(track)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("questions"), d.get("effects")
    except Exception:
        return None


def submit_answers(track, qs, effects, letters, dry_run=False):
    """交卷：字母序列 → 结果条目 → calibration.yaml（dry_run 不落盘）。"""
    if len(letters) != len(qs):
        print(f"❌ 答案数 {len(letters)} != 题数 {len(qs)}（按顺序 A/B/C 每题一个，空格分隔）")
        sys.exit(1)
    results = []
    print("\n✅ 交卷结果：")
    for i, (q, letter) in enumerate(zip(qs, letters), 1):
        if q.get("type") == "sjt":
            # SJT：选项 → 维度投票展开（一个选择投 1~3 个维度）
            votes = (q.get("votes") or {}).get(letter)
            if not votes:
                print(f"❌ Q{i} 答案 {letter} 无效（SJT 应为 A/B/C/D）")
                sys.exit(1)
            for code, d in votes.items():
                results.append({"q": i, "type": "sjt", "dimension": f"{code} {DIMNAME.get(code, code)}",
                                "choice": letter, "direction": d})
            print(f"Q{i} [SJT] 选 {letter} → 投票 {' '.join(f'{c}{v}' for c, v in votes.items())}")
            continue
        effect = effects[i - 1].get(letter)
        if effect is None:
            print(f"❌ Q{i} 答案 {letter} 无效（应为 {'A/B/C/D' if len(q.get('options', [])) > 3 else 'A/B/C'}）")
            sys.exit(1)
        results.append({"q": i, "type": q["type"], "dimension": q["dimension"],
                        "choice": letter, "effect": effect, "indicator": q.get("indicator")})
        print(f"Q{i} {q['dimension']}: {letter} → {effect}")
    if dry_run:
        print("\n[dry-run] 校验通过，未写入任何文件。")
        return
    # 合并语义（持续对话，不是一次性问卷——见 METHODOLOGY §5.1）：
    # SJT 行为票跨轮累计（多次独立行为选择 = 更强证据）；证据确认/盲区自评
    # 同维度以最新答案为准（覆盖旧档）。上一轮整体覆写会丢历史回答，已修。
    prev = load_calibration(track) or {}
    old = prev.get("answers") or []
    new_keys = {(a.get("type"), a.get("dimension")) for a in results
                if a.get("type") != "sjt"}
    kept = [a for a in old
            if a.get("type") == "sjt" or (a.get("type"), a.get("dimension")) not in new_keys]
    merged = kept + results
    out = os.path.join(ROOT, "profiles", track, "calibration.yaml")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        import yaml
        dump = yaml.safe_dump({"updated": datetime.now().isoformat(timespec="minutes"),
                               "track": track, "answers": merged},
                              allow_unicode=True, sort_keys=False)
    except Exception:
        dump = json.dumps({"updated": datetime.now().isoformat(timespec="minutes"),
                           "track": track, "answers": merged}, ensure_ascii=False, indent=2)
    with open(out, "w", encoding="utf-8") as f:
        f.write(dump)
    try:
        os.remove(pending_path(track))
    except OSError:
        pass
    print(f"\n📄 已写入 {out}（合并 {len(kept)} 条历史 + 本轮 {len(results)} 条）\n"
          f"   下次 analyze 的报告将标注校准状态（confirmed/discount 及原因）。SJT 行为票跨轮累计。\n"
          f"   注：权重真实回写在 v0.2 分数化后生效（当前 v0 证据面板无分数可加权）。")


def run_confirm(track, F, panel, answers=None, dry_run=False, mode="auto", cfg=None):
    """画像校验（SJT）。出题优先级：auto/llm → LLM 现场生成（吃 analyze 全量特征+上次反馈）；
    无 key 或失败 → 固定题库随机抽（兜底，保证无 LLM 完整可用）。
    交互优先：终端里一题一题答完即交卷；非终端打印题目并落盘 pending，--answers 交卷读 pending。"""
    cal = load_calibration(track)
    qs = effects = None
    letters = None
    if answers is not None:
        # 交卷模式：优先读 pending（与上次出题保持同题），免出题直接交
        got = load_pending(track)
        if got:
            qs, effects = got
    if qs is None:
        use_llm = mode in ("auto", "llm") and cfg and llm_key(cfg)
        if use_llm:
            print("🤖 LLM 现场出题中（基于你的画像 + 上次校准反馈）…", file=sys.stderr)
            got = llm_questions(cfg, track, F, panel, cal)
            if got:
                qs, effects = got
            else:
                print("↩️ 使用固定题库兜底", file=sys.stderr)
        if not qs:
            qs, effects = generate_questions(track, F, panel, cal)
    if not qs:
        print("（当前没有可出的题——先跑 analyze 生成证据面板）")
        return
    if answers is not None:
        letters = [x.strip().upper() for x in answers.split()] if isinstance(answers, str) else list(answers)
    elif sys.stdin.isatty():
        # 交互模式：一题一题答，q 放弃
        letters = []
        print(f"\n📝 画像校验 · {track}（共 {len(qs)} 题，输入 q 放弃）\n")
        for i, q in enumerate(qs, 1):
            print(f"Q{i} [{ {'sjt': '情景', 'evidence': '证据确认', 'blindspot': '盲区探测'}.get(q['type'], q['type']) }] {q['dimension']}")
            print(f"   {q['text']}")
            for L_, opt in zip("ABC", q["options"]):
                print(f"   {L_}. {opt}")
            while True:
                n_opts = len(q["options"])
                valid = "A/B/C/D"[: 2 * n_opts - 1] if n_opts > 3 else "A/B/C"
                a = input(f"\n   你的选择 ({valid}, q 放弃): ").strip().upper()
                if a == "Q":
                    print("已放弃，未写入任何内容。")
                    return
                if a in [c for c in "ABCD"[:n_opts]]:
                    letters.append(a)
                    print()
                    break
                print(f"   请输入 {valid} 或 q")
    else:
        # 非终端（脚本调用）：打印题目 + 落盘 pending，供 --answers 交卷
        save_pending(track, qs, effects)
        print(f"\n📝 画像校验 · {track}（共 {len(qs)} 题，题目已暂存，交卷：")
        print(f'   python3 scripts/am.py confirm --track {track} --answers "{" ".join(["A"] * len(qs))}"  # 按实际选择替换\n')
        for i, q in enumerate(qs, 1):
            print(f"Q{i} [{ {'sjt': '情景', 'evidence': '证据确认', 'blindspot': '盲区探测'}.get(q['type'], q['type']) }] {q['dimension']}")
            print(f"   {q['text']}")
            for L_, opt in zip("ABC", q["options"]):
                print(f"   {L_}. {opt}")
            print()
        return
    submit_answers(track, qs, effects, letters, dry_run=dry_run)


# ──────────────────────── diff：快照漂移对比 ────────────────────────
def run_diff(track):
    snap_root = os.path.join(ROOT, "profiles", track, "snapshots")
    snaps = sorted(d for d in glob.glob(os.path.join(snap_root, "*")) if os.path.isdir(d))
    feats = [d for d in snaps if os.path.exists(os.path.join(d, "features.json"))]
    if len(feats) < 2:
        print(f"需要 ≥2 个含 features.json 的快照（当前 {len(feats)}）。多跑几次 analyze 积累。")
        return
    import json as _j
    d1, d2 = feats[-2], feats[-1]
    f1 = _j.load(open(os.path.join(d1, "features.json")))
    f2 = _j.load(open(os.path.join(d2, "features.json")))
    print(f"# 画像漂移 · {track}\n\n> {os.path.basename(d1)} → {os.path.basename(d2)}\n")
    state_keys = {"night_ratio", "weekend_ratio", "correction_rate", "avg_turns", "rhythm_stability"}
    rows = []
    for k in sorted(set(f1) & set(f2)):
        v1, v2 = f1[k], f2[k]
        if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
            delta = v2 - v1
            if abs(delta) < 1e-9:
                continue
            arrow = "↑" if delta > 0 else "↓"
            track_type = "state（波动正常）" if k in state_keys else "trait（漂移需 ≥90 天窗口解读）"
            rows.append((k, v1, v2, delta, arrow, track_type))
    if not rows:
        print("数值指标无变化。")
        return
    print("| 指标 | 前值 | 后值 | 变化 | 轨道 |")
    print("|------|------|------|------|------|")
    for k, v1, v2, delta, arrow, t in rows:
        fv = lambda x: f"{x:.1%}" if isinstance(x, float) and x <= 1 else f"{x:.2f}"
        print(f"| `{k}` | {fv(v1)} | {fv(v2)} | {arrow}{fv(abs(delta))} | {t} |")
    print("\n> 解读规则：state 指标短期波动属正常；trait 指标的单次变化无意义，"
          "关注 90 天以上趋势（EMA 半衰期设计，见 METHODOLOGY §4）。")


# ──────────────────────── export：数据导出（二次分析 / 个人画图原料） ────────────────────────
def run_export(track):
    """报告全量数据 → reports/export-<track>-<日期>.json。纯规则确定性输出，零 LLM：
    画图环节由 agent 用 archify skill 渲染（不消耗 API key，路线 A 可用）。"""
    cfg = load_config()
    print("⏳ 收集真人消息并提取特征…", file=sys.stderr)
    msgs = collect_human_msgs(cfg)
    if not msgs:
        print("❌ 没有真人消息，先跑 scan 检查数据源", file=sys.stderr)
        sys.exit(1)
    tsd = [m.ts for m in msgs if m.ts]
    days = (max(tsd) - min(tsd)).days if tsd else 0
    F = extract_features(msgs, cfg)
    mapping = load_mapping(track)
    panel = build_panel(F, mapping)
    cal = load_calibration(track)
    n_ans = len(cal.get("answers", [])) if cal and cal.get("answers") else 0
    out = {"meta": {"tool": "agent-mirror", "track": track,
                    "generated": datetime.now().isoformat(timespec="minutes"),
                    "n_msgs": len(msgs), "days": days,
                    "calibrated": bool(n_ans), "n_answers": n_ans}}
    evidence = {dim: [{"indicator": i, "value": v, "direction": d, "note": nt}
                      for i, v, d, nt in rws] for dim, rws in panel.items()}
    if track == "personality":
        rows = synthesis_rows(panel, cal)
        rows_by_dim = {d: g for d, g, _ in rows}
        pkey = judge_prototype(rows_by_dim)
        pdoc = load_prototypes()
        p = pdoc["prototypes"].get(pkey) or {}
        rank = riasec_rank(F, RIASEC_EVID)
        riasec_first = rank[0][0].split()[0] if rank else None
        cross = {}
        if riasec_first:
            raw = (pdoc.get("cross_insights") or {}).get(f"{pkey}-{riasec_first}", "")
            parts = [x.strip() for x in raw.split("｜")] if raw else []
            cross = {"riasec_first": riasec_first,
                     "meme_line": parts[0] if parts else "",
                     "serious": parts[1] if len(parts) > 1 else raw}
        out.update({
            "synthesis": [{"dim": d, "grade": g, "basis": b} for d, g, b in rows],
            "signature": ocean_signature(rows_by_dim),
            "prototype": {"key": pkey, "name": p.get("name", ""), "emoji": p.get("emoji", ""),
                          "nickname": p.get("nickname", ""), "tagline": p.get("tagline", ""),
                          "population": p.get("population", ""),
                          "narrative": (p.get("narrative") or "").strip(),
                          "strengths": p.get("strengths", []), "blindspots": p.get("blindspots", []),
                          "ai_collab": p.get("ai_collab", ""), "growth": p.get("growth", "")},
            "cross": cross,
            "evidence": evidence,
            "state": {k: F.get(k) for k in ("night_ratio", "weekend_ratio", "rhythm_stability",
                                            "avg_turns", "deep_session", "correction_rate")},
            "scene": {k: F.get(k) for k in ("scene_work_ratio", "scene_life_ratio",
                                            "scene_unknown_ratio")},
        })
    else:                                     # career：RIASEC 加权 + 兴趣投注
        rank = riasec_rank(F, mapping)
        types = load_riasec()
        top3 = []
        for k, v in rank[:3]:
            letter = k.split()[0]
            t = types.get(letter, {})
            top3.append({"letter": letter, "name": k.split()[1], "score": round(v, 2),
                         "desc": t.get("desc", ""), "work_env": t.get("work_env", [])[:4],
                         "ai_usage_signature": t.get("ai_usage_signature", "")})
        inv = sorted([(k, n, d, n * (d ** 0.5)) for k, n, d, _ in F["investment"]],
                     key=lambda x: -x[3])[:10]
        out.update({
            "riasec": {"code": "".join(k[0] for k, _ in rank[:3]),
                       "rank": [{"type": k, "score": round(v, 2)} for k, v in rank],
                       "top3": top3},
            "evidence": evidence,
            "investment": [{"source": k.partition(":")[0],
                            "project": pretty_project(k.partition(":")[2]),
                            "msgs": n, "days": d, "score": round(s)} for k, n, d, s in inv],
        })
    path = os.path.join(ROOT, "reports", f"export-{track}-{datetime.now().strftime('%Y%m%d')}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"📄 数据导出: {path}")
    print("   机器格式（二次分析 / 个人画图原料）——人读报告看 reports/<track>-<日期>.md / .html")
    return out


def synthesis_rows(panel, cal):
    """面板 + 校准答案 → [(维度, 档位, 依据)]。render_synthesis 与 export 共用。
    档位判定优先级：盲区自评(stated) > 确认证据(confirmed) > 数据证据强度 > 证据不足。"""
    stated = {a["dimension"]: a["effect"].replace("stated_", "") for a in (cal or {}).get("answers", [])
              if str(a.get("effect", "")).startswith("stated")}
    confirmed = {a["dimension"] for a in (cal or {}).get("answers", [])
                 if a.get("effect") == "confirmed"}
    discount = {a["dimension"]: a["effect"].replace("discount_", "") for a in (cal or {}).get("answers", [])
                if str(a.get("effect", "")).startswith("discount")}
    # SJT 行为投票累计（每题选择投 1~3 个维度）
    sjt_votes = {}
    for a in (cal or {}).get("answers", []):
        if a.get("type") == "sjt":
            code = str(a.get("dimension", "")).split()[0]
            dim_disp = f"{code} {DIMNAME.get(code, '')}".strip()
            sjt_votes[dim_disp] = sjt_votes.get(dim_disp, 0) + (1 if a.get("direction") == "+" else -1)
    zh = {"high": "高", "mid": "中", "low": "低", "context": "已否认·情境性", "blindspot": "已否认·盲区"}
    rows = []
    for dim, rws in panel.items():
        hits = sum(1 for _, v, d, _ in rws if is_evidence(v) and d != "?")
        sjt = sjt_votes.get(dim, 0)
        sjt_note = f" · SJT行为票 {sjt:+d}" if sjt else ""
        if dim in stated:
            rows.append((dim, f"{zh[stated[dim]]}（自评）", "数据盲区，当事人自评" + sjt_note))
        elif dim in confirmed:
            rows.append((dim, "倾向具备", f"数据证据（{hits} 项）+ 本人确认{sjt_note}"))
        elif dim in discount:
            rows.append((dim, zh[discount[dim]], f"数据证据存在但当事人否认（{discount[dim]}）{sjt_note}"))
        elif hits >= 1 and sjt >= 2:
            # SJT 行为票强档：≥2 题情景选择一致投给该维度 = 当事人行为层面确认
            # （SJT 效度优于自我报告，McDaniel et al. 2001——票与本人确认同为 informant 证据）
            rows.append((dim, "倾向具备",
                         f"数据证据（{hits} 项）+ SJT 行为票 {sjt:+d}（当事人行为选择）"))
        elif hits >= 2:
            rows.append((dim, "证据倾向", f"{hits} 项数据证据（未经校验）{sjt_note}"))
        elif hits == 1:
            rows.append((dim, "初步证据", "仅 1 项证据（未经校验）" + sjt_note))
        else:
            rows.append((dim, "证据不足", "无有效证据，待数据积累或自评" + sjt_note))
    return rows


def render_synthesis(panel, cal, F=None, days=None):
    """画像速览：把证据面板 + 校准答案合成为每维度一行结论（表格 + 整体类型层）。
    措辞规范：不断言"你是X"，只说证据/自评指向什么（METHODOLOGY §7）。"""
    rows = synthesis_rows(panel, cal)
    L = [f"## 画像速览（{'已校准' if (cal and cal.get('answers')) else '初稿 · 待 confirm 校验'}）\n",
         "| 维度 | 档位 | 依据 |", "|------|------|------|"]
    for dim, grade, basis in rows:
        L.append(f"| {dim} | **{grade}** | {basis} |")
    L.append("\n> 档位含义：倾向具备=数据+当事人双重支持（本人确认 或 SJT 行为票≥2）· "
             "高/中/低（自评）=数据盲区由本人补充 · "
             "证据倾向=仅数据侧支持（建议跑 confirm 校验）· 证据不足=待积累。\n"
             "> 大五是维度制不是类型制：没有\"你是X型人\"的标签，类型制结论在职业域（霍兰德代码）。")
    # 整体类型层：三原型（Asendorpf 2001；Gerlach 2018 大样本仅部分复现三原型、另提四型）
    # + 原型×兴趣交叉身份
    riasec_first = None
    if F is not None:
        rank = riasec_rank(F, RIASEC_EVID)
        if rank:
            riasec_first = rank[0][0].split()[0]
    L.append("\n" + render_prototype([(dim, grade, basis) for dim, grade, basis in rows],
                                      riasec_first=riasec_first, days=days))
    return "\n".join(L)


def render_headline(track, F, panel, cal, n_msgs, days, mapping=None):
    """结论前置块：报告第一屏 1 秒看懂测出啥（钩子），证据与细节解读在下方（论据）。
    性格域 = 原型+签名+组合身份+强档统计；职业域 = 霍兰德代码+投注去向。
    与文末「一句话自白」同源同证据——先给记忆点，再给可证伪的细节。"""
    if track == "personality":
        rows = synthesis_rows(panel, cal or {})
        rows_by_dim = {dim: grade for dim, grade, _ in rows}   # 完整维度名（与 render_prototype 一致）
        pkey = judge_prototype(rows_by_dim)
        pdoc = load_prototypes()
        p = pdoc["prototypes"].get(pkey) or {}
        sig = ocean_signature(rows_by_dim)
        strong = [dim for dim, g, _ in rows if g == "倾向具备" or g.startswith("高")]
        pending = [dim.split()[0] for dim, g, _ in rows if dim not in strong and "否认" not in g]
        rank = riasec_rank(F, RIASEC_EVID) if F else []
        riasec_first = rank[0][0].split()[0] if rank else None
        meme = ""
        if riasec_first:
            raw = (pdoc.get("cross_insights") or {}).get(f"{pkey}-{riasec_first}", "")
            parts = [x.strip() for x in raw.split("｜")] if raw else []
            meme = parts[0] if parts else ""
        cal_n = f" · {len(cal['answers'])} 条校准" if cal and cal.get("answers") else ""
        L = ["## 🎯 一句话结论\n",
             f"**「{p.get('nickname', '未定型')}」{p.get('emoji', '🔬')} {p.get('name', pkey).split('（')[0]}**"
             f" · 签名 `{sig}`"
             + (f" · 组合身份「{meme.split('——')[0].strip()}」" if meme else "")]
        if p.get("tagline"):
            L.append(f"> 「{p['tagline']}」" + (f"\n> {meme}" if meme else ""))
        L.append(f"\n**{len(strong)}/{len(rows)} 维强档**"
                 + (f" · 待证：{'、'.join(pending)}" if pending else "")
                 + f" —— {n_msgs} 条真人消息 / {days} 天{cal_n}，证据与档位明细见下方。")
        return "\n".join(L)
    # 职业域
    rank = riasec_rank(F, mapping or RIASEC_EVID) or []
    if not rank:
        return None
    zh = {"R": "现实", "I": "研究", "A": "艺术", "S": "社会", "E": "企业", "C": "常规"}
    code = "".join(k[0] for k, _ in rank[:3])
    top, top_v = rank[0]
    inv = (F.get("investment") or [])
    # 按消息总量取投注第一（investment 列表按日均强度排序，一日爆发项目会霸榜首）
    top_inv = max(inv, key=lambda x: x[1]) if inv else None
    inv1 = f"`{str(top_inv[0]).split(':', 1)[-1]}`（{top_inv[1]} 条）" if top_inv else ""
    cal_n = f" · {len(cal['answers'])} 条校准" if cal and cal.get("answers") else ""
    L = ["## 🎯 一句话结论\n",
         f"**霍兰德代码 `{code}`** · {zh.get(top[0], '')}型主导（{top_v:.2f}"
         + (f" ≫ {zh.get(rank[1][0].split()[0], '')} {rank[1][1]:.2f}" if len(rank) > 1 else "")
         + "）"]
    if inv1:
        L.append(f"\n**精力投注第一**：{inv1}")
    L.append(f"\n> 措辞规范：报告只说「你的行为更接近{zh.get(top[0], '')}型环境」——环境匹配陈述，"
             f"不做\"适合做 X\"断言。{n_msgs} 条消息 / {days} 天{cal_n}，六型证据见下方。")
    return "\n".join(L)


def load_prototypes():
    """读大五原型定义全文档（prototypes 子树 + 顶层 cross_insights）。"""
    path = os.path.join(ROOT, "frameworks", "personality", "prototypes.yaml")
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        doc["prototypes"] = doc.get("prototypes") or {}
        doc["cross_insights"] = doc.get("cross_insights") or {}
        return doc
    except Exception:
        return {"prototypes": {}, "cross_insights": {}}


def judge_prototype(rows_by_dim):
    """档位表 → 原型判定（自参照：见 prototypes.yaml 判定规则）。
    rows_by_dim: {维度: 档位字符串}（render_synthesis 产出的档位列）。"""
    def strong(dims):
        return any(rows_by_dim.get(d, "") in ("倾向具备", "高", "高（自评）") for d in dims)
    def low(dims):
        return any(rows_by_dim.get(d, "") in ("低", "低（自评）") for d in dims)
    n_sensitive = strong(["N1 情绪波动", "N2 退缩"]) or "高" in rows_by_dim.get("N2 退缩", "")
    c_strong = strong(["C1 勤勉", "C2 秩序"])
    c_weak = low(["C1 勤勉", "C2 秩序"])
    e_active = strong(["E1 热情", "E2 果决"])
    if not n_sensitive and c_strong:
        return "resilient"
    if n_sensitive and c_strong:
        return "overcontrolled"
    if c_weak and e_active:
        return "undercontrolled"
    return "undetermined"


def ocean_signature(rows_by_dim):
    """十维档位 → 五域签名码（如 `C+ E+ A+ N− o`）。
    每域取两 aspect 中更强档位：高/倾向具备→`X+` · 证据倾向/中→`X` · 初步/不足→`x` · 低→`x−`。
    MBTI 式可传播标签，但每一位可回溯证据行。"""
    RANK = {"低": 0, "初步证据": 1, "证据不足": 1, "已否认·情境性": 1, "已否认·盲区": 1,
            "证据倾向": 2, "中": 2, "倾向具备": 3, "高": 3}
    domains = {"O": ["O1 智性开放", "O2 经验开放"], "C": ["C1 勤勉", "C2 秩序"],
               "E": ["E1 热情", "E2 果决"], "A": ["A1 共情", "A2 礼貌"],
               "N": ["N1 情绪波动", "N2 退缩"]}
    out = []
    for dom, dims in domains.items():
        grades = [rows_by_dim.get(d, "证据不足").replace("（自评）", "") for d in dims]
        g = max(grades, key=lambda x: RANK.get(x, 0))
        r = RANK.get(g, 0)
        if r == 3:
            out.append(f"{dom}+")
        elif r == 2:
            out.append(dom)
        elif r == 1:
            out.append(dom.lower())
        else:
            out.append(f"{dom.lower()}−")
    return " ".join(out)


def render_prototype(rows_by_dim, riasec_first=None, days=None):
    """整体类型层：原型 + 五维签名 + 原型×兴趣交叉身份（差异化引擎）。"""
    protos = load_prototypes()
    rows = {r[0]: r[1] for r in rows_by_dim} if isinstance(rows_by_dim, list) else dict(rows_by_dim)
    key = judge_prototype(rows)
    p = protos["prototypes"].get(key)
    if not p:
        return ""
    sig = ocean_signature(rows)
    L = ["## 整体人格画像 🎯\n",
         f"### {p.get('emoji','')} {p.get('name', key)} · 五维签名 `{sig}`",
         f"\n> {p.get('population','')} · 判定依据（自参照，非人群常模）："
         f"N 域稳定性 × C 域强度 × E 域活跃度的档位组合（强档 = 本人确认 / SJT 行为票≥2 / 自评高）\n",
         p.get("narrative", "").strip(),
         "\n**优势**"]
    L += [f"- {s}" for s in p.get("strengths", [])]
    L.append("\n**盲点**")
    L += [f"- {b}" for b in p.get("blindspots", [])]
    L.append(f"\n**与 AI 的协作风格**：{p.get('ai_collab', '-')}")
    L.append(f"\n**成长方向**：{p.get('growth', '-')}")
    # 交叉身份（差异化引擎）：原型 × 霍兰德兴趣首型
    meme_line = ""
    if riasec_first:
        zh = {"R": "现实", "I": "研究", "A": "艺术", "S": "社会", "E": "企业", "C": "常规"}
        raw = (protos.get("cross_insights") or {}).get(f"{key}-{riasec_first}", "")
        # 双层格式："梗名 —— 俏皮人设 ｜ 严肃洞察"
        parts = [x.strip() for x in raw.split("｜")] if raw else []
        meme_line = parts[0] if parts else ""
        serious = parts[1] if len(parts) > 1 else raw
        L.append(f"\n### 组合身份：{p.get('name', key).split('（')[0]} × {zh.get(riasec_first, riasec_first)}取向\n")
        if serious:
            L.append(serious)
        else:
            L.append(f"（{key} × {riasec_first} 组合暂无预置洞察——档位证据与兴趣代码见上下文）")
        L.append("\n> 差异化引擎：3 原型 × 6 兴趣首型 = 18 种组合身份，"
                 "外加每人唯一的五维签名——原型给底盘，组合给身份，签名给指纹，"
                 "三层都可回溯证据。")
        # 出圈自白（传播层：梗名 + 签名，可复制分享）
        L.append("\n**📋 一句话自白（复制即发）**\n")
        L.append(f"> 我的 AI 镜像报告：**「{p.get('nickname', key)}」**{p.get('emoji', '')}"
                 f" · 签名 `{sig}`"
                 + (f" · {meme_line.split('——')[0].strip()}" if meme_line else "")
                 + f"\n> 「{p.get('tagline', '')}」"
                 + (f"\n> {meme_line}" if meme_line else "")
                 + "\n> —— " + (f"{days} 天" if days else "长期") + " AI 交互数据全纪录分析 · [Agent Mirror]")
    L.append("\n> 原型给方向感和记忆点，维度面板给证据和可证伪性——两层互查，"
             "档位与原型矛盾时以证据为准并重新校验。出处见 frameworks/personality/prototypes.yaml。\n")
    return "\n".join(L)


def riasec_rank(F, mapping):
    """RIASEC 加权分排序（mapping 源是 3 元组 (ind, direction, note)，panel 才有 val）。"""
    scores = {}
    for dim, rows in mapping.items():
        s = 0.0
        for ind, direction, note in rows:
            if ind not in F or direction != "+":
                continue
            if ind.startswith("p_"):
                s += F[ind] * 2
            else:
                s += min(F[ind] / LANG_SCALE.get(ind, 2.0), 1.0)
        scores[dim] = s
    return sorted(scores.items(), key=lambda x: -x[1])


def load_riasec():
    path = os.path.join(ROOT, "frameworks", "career", "riasec.yaml")
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f).get("types", {})
    except Exception:
        return {}


# ──────────────────────── CLI ────────────────────────
def roadmap_stub(cmd, phase, desc):
    print(f"⏳ `{cmd}` 是 Roadmap {phase} 内容：{desc}\n"
          f"   当前可用：python3 scripts/am.py scan\n"
          f"   进度见 README.md Roadmap 节。")


def main():
    ap = argparse.ArgumentParser(description="Agent Mirror CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="数据体检：扫描数据源，输出统计画像")
    s.add_argument("--source", default=None, help="只扫指定源（claude_code/openclaw/hermes）")
    s.add_argument("--dry-run", action="store_true", help="只打印前 3 条统一消息样本")
    s.add_argument("--report", default=None, help="报告保存路径（如 reports/scan.md）")
    s.add_argument("--inspect-hour", type=int, default=None, metavar="H",
                   help="诊断：打印 H 点真人消息的文本前缀 Top10（找疑似周期注入）")

    a = sub.add_parser("analyze", help="画像分析：规则层证据面板 + 可选 LLM 层")
    a.add_argument("--track", choices=["personality", "career", "both"], default="both",
                   help="分析域（性格/职业，默认双域）")
    a.add_argument("--mode", choices=["rules", "llm", "hybrid"], default="rules",
                   help="rules=纯本地零依赖（默认） llm=仅LLM层 hybrid=规则+LLM")
    a.add_argument("--report", default=None, help="报告保存路径")
    a.add_argument("--source", default=None, help="只分析指定源")

    for alias in ("personality", "career"):          # 快捷别名 = analyze --track <alias>
        p = sub.add_parser(alias, help=f"= analyze --track {alias}")
        p.add_argument("--mode", choices=["rules", "llm", "hybrid"], default="rules")
        p.add_argument("--report", default=None)
        p.add_argument("--source", default=None)

    b = sub.add_parser("build", help="知识库：全量重建 工件A(messages.jsonl)+工件B(FTS5)，无LLM")
    b.add_argument("--source", default=None, help="只建指定源")

    sr = sub.add_parser("search", help="知识库：纯本地 BM25 检索历史会话，无LLM")
    sr.add_argument("query", nargs="+", help="检索词")
    sr.add_argument("--limit", type=int, default=10)
    sr.add_argument("--include-auto", action="store_true", help="附带查自动化任务表")

    ak = sub.add_parser("ask", help="知识库：检索+LLM 归纳回答（需 key，无 key 降级为 search）")
    ak.add_argument("query", nargs="+", help="问题")
    ak.add_argument("--limit", type=int, default=8)

    cf = sub.add_parser("confirm", help="画像校验：SJT 情景判断题，当事人确认（informant validation）")
    cf.add_argument("--track", choices=["personality", "career"], default="personality")
    cf.add_argument("--answers", default=None, help='非交互交卷："A C B C"（终端交互模式不需要）')
    cf.add_argument("--mode", choices=["auto", "llm", "rules"], default="auto",
                    help="出题：auto=有key用LLM现场生成(默认) llm=强制LLM rules=固定题库")
    cf.add_argument("--dry-run", action="store_true", help="交卷校验但不落盘（测试/演示用）")

    df = sub.add_parser("diff", help="快照漂移对比（state/trait 双轨解读）")
    df.add_argument("track", nargs="?", choices=["personality", "career"], default="personality",
                    help="分析域（缺省 personality）")

    ex = sub.add_parser("export", help="可视化导出：报告数据 JSON（二次分析原料），零 LLM")
    ex.add_argument("track", nargs="?", choices=["personality", "career"], default="personality",
                    help="分析域（缺省 personality）")

    args = ap.parse_args()
    cfg = load_config()

    if args.cmd == "scan":
        print(f"⏳ 扫描中（{'源: ' + args.source if args.source else '全部数据源'}，可能十几秒）…", file=sys.stderr)
        res = run_scan(cfg, only=args.source, dry_run=args.dry_run, inspect_hour=args.inspect_hour)
        if not res["stats"]:
            print("❌ 没有扫到任何数据源，检查 config.yaml sources 配置", file=sys.stderr)
            sys.exit(1)
        if args.dry_run:
            for m in res["sample"]:
                print(f"[{m.source}/{m.project}] {m.role} @ {m.ts}\n  {m.text[:120]}\n")
            return
        report = render_scan(res)
        print(report)
        if args.report:
            path = os.path.expanduser(args.report)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"\n📄 已保存: {path}", file=sys.stderr)
    elif args.cmd in ("analyze", "personality", "career"):
        track = getattr(args, "track", None) or args.cmd
        print("⏳ 收集真人消息并提取特征…", file=sys.stderr)
        msgs = collect_human_msgs(cfg, only=getattr(args, "source", None))
        if not msgs:
            print("❌ 没有扫到真人消息，先跑 scan 检查数据源", file=sys.stderr)
            sys.exit(1)
        tsd = [m.ts for m in msgs if m.ts]
        days = (max(tsd) - min(tsd)).days if tsd else 0
        F = extract_features(msgs, cfg)
        tracks = ["personality", "career"] if track == "both" else [track]
        for t in tracks:
            mapping = load_mapping(t)            # 总库 YAML 优先，内置为兜底
            panel = build_panel(F, mapping)
            llm_text = ""
            if args.mode in ("llm", "hybrid"):
                llm_text = llm_analyze(cfg, t, F, msgs)
            cal = load_calibration(t)
            verified = len(cal.get("answers", [])) if cal and cal.get("answers") else 0
            headline = render_headline(t, F, panel, cal, len(msgs), days, mapping)
            report = render_analyze(t, F, panel, mapping, llm_text, len(msgs), days,
                                    verified=verified, mode=args.mode, headline=headline)
            # 校准状态应用（confirm 交卷后生效）+ 画像速览（证据+校准综合结论）
            if cal and cal.get("answers"):
                n_sjt = sum(1 for a in cal["answers"] if a.get("type") == "sjt")
                n_conf = sum(1 for a in cal["answers"] if a.get("effect") == "confirmed")
                n_disc = sum(1 for a in cal["answers"] if str(a.get("effect", "")).startswith("discount"))
                n_state = sum(1 for a in cal["answers"] if str(a.get("effect", "")).startswith("stated"))
                report += (f"\n## 校准状态（informant validation）\n\n"
                           f"已应用 {len(cal['answers'])} 条当事人回答（{cal.get('updated','')}）："
                           f"SJT 行为票 {n_sjt} · ✓ 确认 {n_conf} · ✗ 否认 {n_disc}（含原因）"
                           f"· ◐ 盲区自评 {n_state}。\n"
                           f"> 证据行标注：confirmed=用户确认成立 · discount_context=情境性（对 AI 如此对人未必）· "
                           f"discount_blindspot=数据盲区 · stated_*=盲区自评补充 · SJT 行为票=情景选择投给维度的票\n")
                sjt_by_dim = {}
                for a in cal["answers"]:
                    if a.get("type") == "sjt":
                        sjt_by_dim[a["dimension"]] = sjt_by_dim.get(a["dimension"], 0) + \
                            (1 if a.get("direction") == "+" else -1)
                if sjt_by_dim:
                    report += ("SJT 行为票（跨轮累计）：" + " · ".join(
                        f"{k} {v:+d}" for k, v in sorted(sjt_by_dim.items())) + "\n")
                disc = {a["dimension"]: a["effect"] for a in cal["answers"]
                        if str(a.get("effect", "")).startswith("discount")}
                if disc:
                    report += ("\n被否认的证据维度：" + "、".join(
                        f"**{k}**（{v.replace('discount_', '')}）" for k, v in disc.items()) + "\n")
            if t == "personality":
                report += "\n" + render_synthesis(panel, cal, F, days=days) + "\n"
            print(report)
            day = datetime.now().strftime("%Y-%m-%d")
            snap = os.path.join(ROOT, "profiles", t, "snapshots", day)
            os.makedirs(snap, exist_ok=True)
            with open(os.path.join(snap, "evidence.md"), "w", encoding="utf-8") as f:
                f.write(report)
            # 人读报告（可见位置，md 源文件 + 浏览器直开的 HTML；同日重跑 = 初稿→定稿更新）
            rbase = os.path.join(ROOT, "reports", f"{t}-{day.replace('-', '')}")
            os.makedirs(os.path.dirname(rbase), exist_ok=True)
            with open(rbase + ".md", "w", encoding="utf-8") as f:
                f.write(report)
            with open(rbase + ".html", "w", encoding="utf-8") as f:
                f.write(report_html(t, report))
            # 结构化特征落盘（diff 的原料）
            fjson = {k: v for k, v in F.items() if not isinstance(v, list)}
            fjson["_investment"] = [list(x) for x in F.get("investment", [])]
            fjson["_meta"] = {"n_msgs": len(msgs), "days": days,
                               "generated": datetime.now().isoformat(timespec="minutes")}
            with open(os.path.join(snap, "features.json"), "w", encoding="utf-8") as f:
                json.dump(fjson, f, ensure_ascii=False, indent=1)
            print(f"📄 人读报告: {rbase}.md / .html（浏览器直接打开）", file=sys.stderr)
            print(f"📄 快照: {snap}（evidence.md 存档 + features.json 机器格式，diff 用）", file=sys.stderr)
            if not verified:
                print(f"⏭️  下一步（流程必经）: python3 scripts/am.py confirm --track {t}"
                     f" → 答题后重跑 analyze 出【定稿】", file=sys.stderr)
            if args.report:
                path = os.path.expanduser(args.report)
                if len(tracks) > 1:
                    path = f"{path}.{t}.md"
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(report)
                print(f"📄 报告: {path}", file=sys.stderr)

    elif args.cmd == "confirm":
        msgs = collect_human_msgs(cfg)
        if not msgs:
            print("❌ 没有真人消息，先跑 scan/build", file=sys.stderr)
            sys.exit(1)
        F = extract_features(msgs, cfg)
        run_confirm(args.track, F, build_panel(F, load_mapping(args.track)),
                    answers=getattr(args, "answers", None),
                    dry_run=args.dry_run, mode=getattr(args, "mode", "rules"), cfg=cfg)

    elif args.cmd == "diff":
        run_diff(args.track)

    elif args.cmd == "export":
        run_export(args.track)
    elif args.cmd == "build":
        print("⏳ 全量重建（采集→物化→建索引，约十几秒）…", file=sys.stderr)
        st = build_knowledge(cfg, only=args.source)
        print(f"✅ 工件A: {st['a_path']}（{st['total']:,} 条消息物化）")
        print(f"✅ 工件B: {st['b_path']}（entries {st['entries']:,} / auto_events {st['auto_events']:,}）")
        print("   下一步：am.py search \"关键词\"（纯本地）或 am.py ask \"问题\"（LLM）")

    elif args.cmd == "search":
        rows = search_rows(" ".join(args.query), args.limit, args.include_auto)
        if rows is None:
            print("❌ 索引不存在，先跑: python3 scripts/am.py build", file=sys.stderr)
            sys.exit(1)
        q = " ".join(args.query)
        print(f"🔎 「{q}」命中 {len(rows)} 条（LIKE 加权，长词×2，会话多样性）：\n")
        for i, (text, source, project, ts, role) in enumerate(rows, 1):
            print(f"{i}. [{source} · {pretty_project(project)} · {ts} · {role}]")
            print(f"   {text[:200]}{'…' if len(text) > 200 else ''}\n")
        if not rows:
            print("（无命中。换关键词试试；1-2 字短词自动走 LIKE 兜底，仍无果说明历史里没有）")

    elif args.cmd == "ask":
        q = " ".join(args.query)
        rows = search_rows(q, args.limit)
        if rows is None:
            print("❌ 索引不存在，先跑: python3 scripts/am.py build", file=sys.stderr)
            sys.exit(1)
        if not rows:
            print(f"检索无命中，无法回答「{q}」。换个说法或先 am.py search 确认关键词。")
            sys.exit(0)
        if not llm_key(cfg):
            print("⚠️ 未配置 LLM key（AM_LLM_API_KEY / ~/.am-llm-key，兼容旧名 CPD_LLM_API_KEY），降级为检索结果：\n")
            for text, source, project, ts, role in rows:
                print(f"- [{source} · {pretty_project(project)} · {ts}]\n  {text[:150]}…\n")
            sys.exit(0)
        print(f"🔎 检索命中 {len(rows)} 条，LLM 归纳中…\n", file=sys.stderr)
        ans = ask_llm(cfg, q, rows, load_cached_msgs())
        print(ans if ans else "❌ LLM 调用失败（检查 config.yaml llm 段与 key）")

    else:
        phases = {"chat": ("Phase 1+", "多轮会话"), "diff": ("Phase 4", "画像漂移对比")}
        ph, d = phases[args.cmd]
        roadmap_stub(args.cmd, ph, d)


if __name__ == "__main__":
    main()
