# -*- coding: utf-8 -*-
"""军团体验反馈（v4.269.0）。

纯函数给界面用：一句话进度、阵容摘要、哪些日志该折叠。
成功/提示走 toast.notice，不在这里弹模态框。
"""
import logging
import re

VERSION = "v4.279.0"

_log = logging.getLogger("legion")

# 这些是执行器给自己看的技术行。主时间线不刷，收进「详情」。
_FOLD_MARKERS = (
    "🔑",
    "🧰",
    "📖",
    "⚙️",
    "⚙",
    "能力注册表",
    "技能缺口",
    "技能披露",
    "本波工具集",
    "本波能力",
    "派发前授予",
)

_FAIL_MARKERS = ("⚠️", "🔴", "❌", "失败", "未通过", "异常", "ERROR", "⛔")

_WAVE_MARK = re.compile(r"第\s*(\d+)\s*波")


def notice(msg, kind="info", detail=None):
    """成功/提示。主窗口注册过 toast 就浮一层，没有宿主就只记日志。"""
    try:
        from toast import toast
        toast(str(msg), kind=kind, detail=detail)
    except Exception as e:
        _log.info("notice %s %s (%s)", msg, detail or "", e)


def is_failure_log(text):
    t = str(text or "")
    return any(m in t for m in _FAIL_MARKERS)


def fold_log_line(text):
    """True = 技术日志，主时间线不展示。失败、项目经理、波次标记不折叠。"""
    t = str(text or "").strip()
    if not t:
        return True
    if is_failure_log(t):
        return False
    if "项目经理" in t or t.startswith("💬"):
        return False
    if "──" in t and "波" in t:
        return False
    return any(m in t for m in _FOLD_MARKERS)


def failure_oneline(text, limit=72):
    """失败日志收成一句，后面的技术尾巴留给详情。"""
    t = " ".join(str(text or "").split())
    for sep in ("——", "。"):
        if sep in t:
            head = t.split(sep, 1)[0].strip()
            if len(head) >= 8:
                t = head
                break
    if len(t) <= limit:
        return t
    return t[:limit - 1] + "…"


def wave_marker(text):
    """从 `── 第 N 波` 里取出波次号。不是波次标记则 None。"""
    if not text or "──" not in str(text):
        return None
    m = _WAVE_MARK.search(str(text))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _names(group):
    out = []
    for m in group:
        if not isinstance(m, dict):
            continue
        nm = str(m.get("role") or "").strip() or "成员"
        if nm not in out:
            out.append(nm)
    return "、".join(out[:3]) or "成员"


def progress_sentence(nodes, cur_wave, total_waves):
    """置顶一句：第几波、谁、进行中 / 已通过 / 未通过 / 等你验收。"""
    nodes = nodes if isinstance(nodes, dict) else {}
    try:
        cur_wave = int(cur_wave or 0)
    except (TypeError, ValueError):
        cur_wave = 0
    try:
        total_waves = int(total_waves or 0)
    except (TypeError, ValueError):
        total_waves = 0
    members = []
    for k, n in nodes.items():
        if not isinstance(n, dict):
            continue
        key = str(k)
        if key.startswith("gate_") or key in ("run", "pm_plan"):
            continue
        wave = n.get("wave") or 0
        try:
            wave = int(wave)
        except (TypeError, ValueError):
            wave = 0
        if cur_wave and wave and wave != cur_wave:
            continue
        members.append(n)
    running = [m for m in members if m.get("status") == "running"]
    errors = [m for m in members if m.get("status") == "error"]
    incomplete = [m for m in members if m.get("status") == "incomplete"]
    done = [m for m in members if m.get("status") in ("done", "approved")]
    pending = warned = approved = False
    for k, n in nodes.items():
        if not str(k).startswith("gate_") or not isinstance(n, dict):
            continue
        wave = n.get("wave") or 0
        try:
            wave = int(wave)
        except (TypeError, ValueError):
            wave = 0
        if cur_wave and wave and wave != cur_wave:
            continue
        s = n.get("status")
        if s in ("running", "await_approval"):
            pending = True
        elif s == "accepted_with_warning":
            warned = True
        elif s in ("approved", "done", "timeout"):
            approved = True
    if running:
        body = "%s 进行中" % _names(running)
    elif errors:
        body = "%s 未通过" % _names(errors)
    elif incomplete:
        body = "%s 没交上稿" % _names(incomplete)
    elif pending:
        body = "等你验收"
    elif warned:
        body = "带风险通过"
    elif done or approved:
        body = ("%s 已通过" % _names(done)) if done else "已通过"
    elif total_waves or cur_wave:
        body = "准备中"
    else:
        return "还没开工"
    if total_waves:
        wave_txt = "第 %s/%s 波" % (cur_wave or 0, total_waves)
    elif cur_wave:
        wave_txt = "第 %s 波" % cur_wave
    else:
        wave_txt = ""
    if wave_txt:
        return "%s · %s" % (wave_txt, body)
    return body


def team_lineup_brief(plan):
    """组队方案收成两行：阵容一句 + 缺角/缺技能（没有就写明阵容齐）。"""
    plan = plan if isinstance(plan, dict) else {}
    parts = []
    for i, w in enumerate(plan.get("waves") or [], 1):
        if not isinstance(w, dict):
            continue
        names = []
        for m in (w.get("members") or []):
            if isinstance(m, dict) and str(m.get("name") or "").strip():
                names.append(str(m.get("name")).strip())
            elif isinstance(m, str) and m.strip():
                names.append(m.strip())
        parts.append("第%d波 %s" % (i, "、".join(names) if names else "（空）"))
    und = str(plan.get("understanding") or "").strip()
    headline = " · ".join(parts) if parts else "（方案里没有波次）"
    if und:
        short = und if len(und) <= 36 else (und[:35] + "…")
        headline = short + "｜" + headline
    mr = []
    for g in (plan.get("missing_roles") or []):
        if isinstance(g, dict) and str(g.get("name") or "").strip():
            mr.append(str(g.get("name")).strip())
        elif isinstance(g, str) and g.strip():
            mr.append(g.strip())
    ms = []
    labels = []
    for s in (plan.get("missing_skills") or []):
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        who = str(s.get("for_role") or "").strip()
        ms.append(name)
        labels.append("%s→%s" % (name, who) if who else name)
    if mr or labels:
        bits = []
        if mr:
            bits.append("缺角：" + "、".join(mr))
        if labels:
            bits.append("缺技能：" + "、".join(labels))
        gap = "；".join(bits)
    else:
        gap = "阵容齐：没有缺角，也没有缺技能"
    return {
        "headline": headline,
        "gap_line": gap,
        "missing_roles": mr,
        "missing_skills": ms,
    }
