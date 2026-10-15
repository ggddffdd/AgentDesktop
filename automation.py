# -*- coding: utf-8 -*-
"""小臭玩AI — 自动化任务模块（定时提醒 / 定时执行 Agent 任务）。

职责：
1. 任务数据模型与 JSON 持久化（~/Documents/小臭玩AI/automation_tasks.json）；
2. 调度判断：一次性 / 每天 / 每周 / 间隔重复；
3. 供面板（automation_panel）与主窗口调度器（ui.py 的 QTimer tick）复用。

纯数据/逻辑模块，不依赖 Qt（便于单测与冒烟）。
"""

import os
import json
import time
import uuid
import logging
from datetime import datetime, timedelta

from config import USER_DATA_DIR

log = logging.getLogger("dsdesktop")

TASKS_PATH = os.path.join(USER_DATA_DIR, "automation_tasks.json")

# ---- 调度类型 ----
SCHED_ONCE = "once"          # 一次性：at_date + at_time
SCHED_DAILY = "daily"        # 每天：at_time
SCHED_WEEKLY = "weekly"      # 每周：weekday(0=周一..6=周日) + at_time
SCHED_INTERVAL = "interval"  # 间隔：interval_minutes

# ---- 动作类型 ----
ACT_REMIND = "remind"        # 到点弹窗提醒
ACT_RUN = "run"              # 到点把 message 作为指令交给 Agent 执行

SCHEDULE_TYPES = [SCHED_ONCE, SCHED_DAILY, SCHED_WEEKLY, SCHED_INTERVAL]
ACTIONS = [ACT_REMIND, ACT_RUN]

# v4.215.0：自动化任务专属会话的 sid 前缀（ui._automation_session 拼专用 sid：
# auto_{task_id}）。单源常量：进包核验据此钉「独立会话能力在包里」。
SESSION_SID_PREFIX = "auto_"

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

SCHEDULE_LABELS = {
    SCHED_ONCE: "一次性",
    SCHED_DAILY: "每天",
    SCHED_WEEKLY: "每周",
    SCHED_INTERVAL: "间隔重复",
}
ACTION_LABELS = {
    ACT_REMIND: "定时提醒",
    ACT_RUN: "执行任务",
}


def new_task(name, action, message, schedule_type, at_time="09:00",
             at_date="", weekday=0, interval_minutes=60, enabled=True,
             full_tools=False, prefer_offpeak=False):
    """构造一个任务字典（新建用）。

    full_tools（v4.215.0）：False = 默认受限工具集（查询/生成/本地写，无人值守
    安全：不含 EXEC/EXTERNAL、不含硬确认项）；True = 放开全部工具（含命令执行、
    浏览器点击等，遇到确认弹窗会等用户回来点）。
    """
    return {
        "id": uuid.uuid4().hex[:12],
        "name": (name or "").strip() or "未命名任务",
        "action": action if action in ACTIONS else ACT_REMIND,
        "message": message or "",
        "schedule_type": schedule_type if schedule_type in SCHEDULE_TYPES else SCHED_DAILY,
        "at_time": at_time or "09:00",
        "at_date": at_date or "",
        "weekday": int(weekday) % 7,
        "interval_minutes": max(1, int(interval_minutes or 60)),
        "enabled": bool(enabled),
        "full_tools": bool(full_tools),
        "prefer_offpeak": bool(prefer_offpeak),
        "last_run": 0.0,
        "last_result": "",
        "last_error": "",
        "last_attempt": 0.0,
        "fail_count": 0,
        "created": time.time(),
    }


def filter_tools_safe(tools, task=None):
    """v4.215.0：自动化任务的默认受限工具集。

    外部审核 P1「自动任务继承当前会话工具权限」的对策：定时任务多在无人值守时
    触发，默认不放行任何需要**人工确认**的工具（EXEC / EXTERNAL / 硬确认项），
    否则要么卡在确认弹窗上（任务挂起），要么在 session_trusted 状态下被静默
    放行（越权执行）。保留 READ（自主）与 WRITE_LOCAL 无硬确认项（半自主，
    本地生成/写入，无外发）。

    - 未登记工具（含全部 MCP 工具）按 risk.classify 兜底 EXTERNAL → 一律滤除
      （保守侧：新 MCP 工具不会静默获得无人值守执行权）。
    - **tier=manual 的本地写**（write_file / db_insert / db_update / schedule）
      也滤除 —— 交互模式下它们要人工确认（permissions.decide 第 7 步），
      无人值守触发只会卡在确认弹窗上。
    - 传入 task 且正文声明了落盘 / HTTP 时，只把 write_file、web_fetch 从原清单
      补回（见 _restore_declared_task_tools）。db_insert / schedule / 命令执行不补。
      范围内的 write_file 由权限引擎在定时任务线程上免确认，避免补回后又卡住。
    - 返回的是入参列表的**新列表**（浅拷贝过滤），不改入参。
    """
    try:
        from risk import (classify as _classify, tier_of as _tier_of,
                          ALWAYS_CONFIRM, RiskClass)
    except Exception:            # risk 不可用 → 最保守：全滤（退化为纯对话）
        return []
    kept = []
    for t in tools or []:
        name = ""
        try:
            name = (t or {}).get("function", {}).get("name", "")
        except Exception:
            continue
        if not name:
            continue
        if _classify(name) not in (RiskClass.READ, RiskClass.WRITE_LOCAL):
            continue
        if name in ALWAYS_CONFIRM:
            continue
        if _tier_of(name) == "manual":
            continue
        kept.append(t)
    return _strip_negated_browser_tools(
        _restore_declared_task_tools(kept, tools, task), task)


# 任务正文点名落盘 / HTTP 时才补回。不要放宽到 db_insert、schedule、跑命令。
_DECLARED_WRITE_KW = (
    "写文件", "写入", "保存到", "落盘", "存成", "生成文件", "导出", "建个文件",
    "write_file",
)
_DECLARED_HTTP_KW = (
    "https://", "http://", "requests", "web_fetch", "直连", "抓取",
    "纯 http", "纯http",
)


def _restore_declared_task_tools(kept, tools, task):
    """任务自己写了要落盘或要 HTTP 时，把被滤掉的对应工具从原清单补回。"""
    if not isinstance(task, dict):
        return kept
    text = str(task.get("message") or "")
    if not text:
        return kept
    low = text.lower()
    want = []
    if any(k.lower() in low for k in _DECLARED_WRITE_KW):
        want.append("write_file")
    if any(k.lower() in low for k in _DECLARED_HTTP_KW):
        want.append("web_fetch")
    if not want:
        return kept
    have = set()
    for item in kept or []:
        try:
            have.add((item or {}).get("function", {}).get("name", ""))
        except Exception:
            continue
    by_name = {}
    for item in tools or []:
        try:
            name = (item or {}).get("function", {}).get("name", "")
        except Exception:
            continue
        if name and name not in by_name:
            by_name[name] = item
    out = list(kept or [])
    for name in want:
        if name in have or name not in by_name:
            continue
        out.append(by_name[name])
        have.add(name)
    return out


# v4.280.0：任务正文显式否定浏览器时，物理摘掉 browser_* 工具。
# 背景：bd1c4be 已让「无需浏览器」不再被路由成打开网页、且定时任务不因多功能
# 澄清停住；但模型仍可能在无人值守时无视「无需浏览器」直接调 browser_open。
# 这里在工具集层面兜底——正文点名「不用浏览器 / 纯 HTTP / requests 直连」就把
# browser_open / browser_navigate / browser_click / browser_fill / browser_read
# 全摘掉，自动化根本没有开浏览器的手段。
# fail-open：task 非 dict / 无正文 / 未命中否定词 → 不动（保留浏览器能力，
# 不误伤真正要开网页的定时任务）。只认显式否定，不靠猜。
_NO_BROWSER_KW = (
    "无需浏览器", "不用浏览器", "别开浏览器", "别上浏览器", "不要浏览器",
    "不打开浏览器", "不开浏览器",
    "纯 http", "纯http", "requests 直连", "用 requests", "直连 http",
)


def _strip_negated_browser_tools(kept, task):
    """任务正文否定浏览器时，从无人值守工具集摘掉所有 browser_* 工具。"""
    if not isinstance(task, dict):
        return kept
    text = str(task.get("message") or "").lower()
    if not text:
        return kept
    if not any(k.lower() in text for k in _NO_BROWSER_KW):
        return kept
    out = []
    for item in kept or []:
        try:
            name = (item or {}).get("function", {}).get("name", "")
        except Exception:
            name = ""
        if name.startswith("browser_"):
            continue
        out.append(item)
    return out


class AutomationStore:
    """自动化任务持久化存储。"""

    def __init__(self, path=None):
        self.path = path or TASKS_PATH
        self.tasks = []
        self._load()

    def _load(self):
        self.tasks = []  # 先清空，避免重复 reload 时 append 导致任务翻倍
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("tasks", []) if isinstance(data, dict) else data
            for t in raw:
                if isinstance(t, dict) and t.get("id"):
                    t.setdefault("last_run", 0.0)
                    t.setdefault("last_result", "")
                    t.setdefault("last_error", "")
                    t.setdefault("last_attempt", 0.0)
                    t.setdefault("fail_count", 0)
                    t.setdefault("enabled", True)
                    t.setdefault("prefer_offpeak", False)
                    self.tasks.append(t)
        except Exception as e:
            log.error("加载 automation_tasks.json 失败: %s", e)

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"tasks": self.tasks}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            log.error("保存 automation_tasks.json 失败: %s", e)

    def add(self, task):
        self.tasks.append(task)
        self.save()

    def update(self, task):
        for i, t in enumerate(self.tasks):
            if t.get("id") == task.get("id"):
                self.tasks[i] = task
                break
        self.save()

    def delete(self, task_id):
        self.tasks = [t for t in self.tasks if t.get("id") != task_id]
        self.save()

    def set_enabled(self, task_id, enabled):
        for t in self.tasks:
            if t.get("id") == task_id:
                t["enabled"] = bool(enabled)
                break
        self.save()

    def get(self, task_id):
        for t in self.tasks:
            if t.get("id") == task_id:
                return t
        return None

    def list_all(self):
        return list(self.tasks)

    def list_enabled(self):
        return [t for t in self.tasks if t.get("enabled", True)]


# ---- 调度判断（纯函数） ----

def _parse_hm(s):
    try:
        h, m = str(s).split(":")[:2]
        return int(h), int(m)
    except Exception:
        return 0, 0


# 失败后允许补跑，但不要每秒重试把会话刷爆。
RETRY_COOLDOWN_SEC = 10 * 60
MAX_FAILS_PER_DAY = 3

# 助手只回了这类话，说明这一轮没真正做完。
REFUSAL_MARKERS = (
    "请开启 Agent 模式",
    "请开 Agent 模式",
    "请开启「助手做事」",
    "请切到「助手做事」",
    "请切到『助手做事』",
    "请切换到「助手做事」",
    "当前为「普通对话模式」",
    "无法直接调用任何工具",
    "我无法执行",
    "没法执行",
)
_MIN_SUBSTANTIVE = 80


def _contains_refusal(text):
    return any(m in (text or "") for m in REFUSAL_MARKERS)


def reply_is_real_success(reply, stopped=False, tools_ran=False):
    """接手了不算完成。停了、空回复、只让用户去开模式，都是失败。

    真正用过工具，或写出一段够长、且不是推脱的回复，才算成功。
    """
    if stopped:
        return False
    text = reply.strip() if isinstance(reply, str) else ""
    if not text or _contains_refusal(text):
        return False
    if tools_ran:
        return True
    return len(text) >= _MIN_SUBSTANTIVE


def failure_reason(reply, stopped=False):
    """给用户看的一句原因，不含内部词。"""
    if stopped:
        return "中途停了，这次不算完成"
    text = reply.strip() if isinstance(reply, str) else ""
    if not text:
        return "没有给出结果"
    if _contains_refusal(text):
        return "没有真正做完，还在等你切换干活方式"
    return "没有产出可用结果"


def _retry_blocked(task, now):
    """同一天失败后的冷却与次数上限。新的一天重新允许。"""
    if str(task.get("last_result") or "") != "failed":
        return False
    attempt = float(task.get("last_attempt") or 0)
    if attempt <= 0:
        return False
    try:
        if datetime.fromtimestamp(attempt).date() != now.date():
            return False
    except Exception:
        return False
    if int(task.get("fail_count") or 0) >= MAX_FAILS_PER_DAY:
        return True
    return now.timestamp() < attempt + RETRY_COOLDOWN_SEC


def _as_aware(now):
    """墙上时间。无时区的 datetime 按本机时区理解（调度器传入的就是 datetime.now()）。"""
    if now is None:
        return datetime.now().astimezone()
    if now.tzinfo is None:
        return now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return now


def _as_beijing(now):
    from zoneinfo import ZoneInfo
    return _as_aware(now).astimezone(ZoneInfo("Asia/Shanghai"))


def _peak_windows():
    from config import DEEPSEEK_PEAK_WINDOWS_BJ
    return DEEPSEEK_PEAK_WINDOWS_BJ


def _statutory_holidays():
    from config import DEEPSEEK_STATUTORY_HOLIDAYS
    return DEEPSEEK_STATUTORY_HOLIDAYS


def _price_ratio():
    from config import DEEPSEEK_OFFPEAK_PRICE_RATIO
    return float(DEEPSEEK_OFFPEAK_PRICE_RATIO)


def _max_delay_default():
    from config import DEEPSEEK_OFFPEAK_MAX_DELAY_SEC
    return int(DEEPSEEK_OFFPEAK_MAX_DELAY_SEC)


def is_deepseek_peak(now=None):
    """北京时间此刻是否按官方价目算高峰。空闲、周末、法定节假日返回 False。"""
    bj = _as_beijing(now)
    if bj.weekday() >= 5:
        return False
    if bj.date().isoformat() in _statutory_holidays():
        return False
    minute = bj.hour * 60 + bj.minute
    for start, end in _peak_windows():
        sh, sm = _parse_hm(start)
        eh, em = _parse_hm(end)
        if (sh * 60 + sm) <= minute < (eh * 60 + em):
            return True
    return False


def _next_offpeak_start(bj):
    """当前若在高峰，返回这段高峰结束的北京时间；否则就是此刻。"""
    if not is_deepseek_peak(bj):
        return bj
    minute = bj.hour * 60 + bj.minute
    for start, end in _peak_windows():
        sh, sm = _parse_hm(start)
        eh, em = _parse_hm(end)
        if (sh * 60 + sm) <= minute < (eh * 60 + em):
            return bj.replace(hour=eh, minute=em, second=0, microsecond=0)
    return bj


def _offpeak_wait_exceeded(waited, remain, max_delay_sec):
    """已经等够久，或者下一段空闲远到会超过上限。"""
    if waited >= max_delay_sec:
        return True
    return remain > (max_delay_sec - waited)


def _due_anchor(task, now_aware):
    """这次最早该跑的时刻，时区与 now_aware 相同。"""
    st = task.get("schedule_type", SCHED_DAILY)
    if st == SCHED_INTERVAL:
        interval = max(1, int(task.get("interval_minutes") or 60))
        last = float(task.get("last_run") or 0)
        base = last if last > 0 else float(task.get("created") or now_aware.timestamp())
        return datetime.fromtimestamp(base + interval * 60, tz=now_aware.tzinfo)
    if st == SCHED_ONCE:
        try:
            raw = datetime.fromisoformat(
                "%s %s" % ((task.get("at_date") or "").strip(),
                           (task.get("at_time") or "09:00").strip()))
        except Exception:
            return now_aware
        if raw.tzinfo is None:
            return raw.replace(tzinfo=now_aware.tzinfo)
        return raw.astimezone(now_aware.tzinfo)
    hh, mm = _parse_hm(task.get("at_time", "09:00"))
    anchor = now_aware.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if anchor > now_aware:
        return now_aware
    return anchor


def prefer_offpeak_for(action, checked):
    """只有执行任务能打开「等到空闲」。提醒到点就弹，不推迟。"""
    return action == ACT_RUN and bool(checked)


def should_defer_offpeak(task, now=None, max_delay_sec=None):
    """到期了但先别跑，等 DeepSeek 空闲时段。默认关，聊天不走这里。

    已经在空闲、没开选项、不是执行任务、没有折扣，都返回 False。
    等到下一段空闲会超过配置的最长等待时也返回 False，任务直接跑。
    """
    if not isinstance(task, dict) or not task.get("prefer_offpeak"):
        return False
    if task.get("action") != ACT_RUN:
        return False
    try:
        if not (_price_ratio() < 1.0):
            return False
    except Exception:
        return False
    now_aware = _as_aware(now)
    if not is_deepseek_peak(now_aware):
        return False
    if max_delay_sec is None:
        try:
            max_delay_sec = _max_delay_default()
        except Exception:
            return False
    try:
        max_delay_sec = float(max_delay_sec)
    except Exception:
        return False
    anchor = _due_anchor(task, now_aware)
    waited = (now_aware - anchor).total_seconds()
    if waited < 0:
        waited = 0.0
    bj_now = _as_beijing(now_aware)
    remain = (_next_offpeak_start(bj_now) - bj_now).total_seconds()
    if remain < 0:
        remain = 0.0
    if _offpeak_wait_exceeded(waited, remain, max_delay_sec):
        return False
    return True


def ready_to_fire(task, now=None):
    """调度器每秒问一次：到期，并且不用再等空闲时段。"""
    if not is_due(task, now):
        return False
    if should_defer_offpeak(task, now):
        return False
    return True


def is_due(task, now=None):
    """判断任务此刻是否到期。now 为 datetime，缺省用当前时间。"""
    now = now or datetime.now()
    if _retry_blocked(task, now):
        return False
    st = task.get("schedule_type", SCHED_DAILY)
    last = float(task.get("last_run", 0.0) or 0.0)
    now_ts = now.timestamp()
    hh, mm = _parse_hm(task.get("at_time", "09:00"))

    if st == SCHED_ONCE:
        if last > 0:
            return False
        try:
            iso = f"{task.get('at_date', '')} {task.get('at_time', '09:00')}".strip()
            dt = datetime.fromisoformat(iso)
        except Exception:
            return False
        return now_ts >= dt.timestamp()

    if st == SCHED_DAILY:
        if last > 0 and datetime.fromtimestamp(last).date() == now.date():
            return False
        return (now.hour, now.minute) >= (hh, mm)

    if st == SCHED_WEEKLY:
        if now.weekday() != int(task.get("weekday", 0)) % 7:
            return False
        if last > 0 and datetime.fromtimestamp(last).date() == now.date():
            return False
        return (now.hour, now.minute) >= (hh, mm)

    if st == SCHED_INTERVAL:
        interval = max(1, int(task.get("interval_minutes", 60)))
        if last <= 0:
            created = float(task.get("created", now_ts) or now_ts)
            return now_ts >= created + interval * 60
        return now_ts >= last + interval * 60

    return False


def mark_fired(task, now=None):
    """真正做完才回写 last_run；once 类型同时置 disabled（就地修改，无返回值）。"""
    now = now or datetime.now()
    task["last_run"] = now.timestamp()
    task["last_attempt"] = task["last_run"]
    task["last_result"] = "ok"
    task["last_error"] = ""
    task["fail_count"] = 0
    if task.get("schedule_type") == SCHED_ONCE:
        task["enabled"] = False


def mark_failed(task, now=None, reason=""):
    """记一次失败。不写 last_run，所以当天还能在冷却后补跑。"""
    now = now or datetime.now()
    prev = float(task.get("last_attempt") or 0)
    try:
        new_day = prev > 0 and datetime.fromtimestamp(prev).date() != now.date()
    except Exception:
        new_day = False
    if new_day:
        task["fail_count"] = 0
    task["fail_count"] = int(task.get("fail_count") or 0) + 1
    task["last_attempt"] = now.timestamp()
    task["last_result"] = "failed"
    task["last_error"] = (reason or "没做完")[:200]


def last_result_text(task):
    """自动化面板上的「上次结果」。没跑过返回空串。"""
    res = str(task.get("last_result") or "")
    if res == "ok":
        return "上次结果：成功"
    if res == "failed":
        err = (task.get("last_error") or "没做完").strip()
        return "上次结果：没做完 · " + err
    return ""


def bound_session(store, sid):
    """按启动时钉住的会话编号取会话。

    后启动的任务会改 store.active()，写入若跟着 active 走，两个任务的内容会串到同一个会话。
    """
    if store is None:
        return None
    if sid:
        sessions = getattr(store, "sessions", None)
        if isinstance(sessions, dict):
            sess = sessions.get(sid)
            if sess is not None:
                return sess
    try:
        return store.active()
    except Exception:
        return None


def schedule_summary(task):
    """调度方式的简短中文描述。"""
    st = task.get("schedule_type", SCHED_DAILY)
    at_time = task.get("at_time", "09:00")
    if st == SCHED_ONCE:
        text = f"一次性 · {task.get('at_date', '')} {at_time}"
    elif st == SCHED_DAILY:
        text = f"每天 {at_time}"
    elif st == SCHED_WEEKLY:
        text = f"每周{WEEKDAY_NAMES[int(task.get('weekday', 0)) % 7]} {at_time}"
    elif st == SCHED_INTERVAL:
        text = f"每 {int(task.get('interval_minutes', 60))} 分钟"
    else:
        text = at_time
    if task.get("prefer_offpeak") and task.get("action") == ACT_RUN:
        text += " · 错峰"
    return text


def next_run_text(task, now=None):
    """下次触发时间的人类可读描述。"""
    now = now or datetime.now()
    st = task.get("schedule_type", SCHED_DAILY)
    at_time = task.get("at_time", "09:00")
    hh, mm = _parse_hm(at_time)

    if st == SCHED_ONCE:
        return f"{task.get('at_date', '')} {at_time}"
    if st == SCHED_DAILY:
        if (now.hour, now.minute) < (hh, mm):
            return f"今天 {at_time}"
        return f"明天 {at_time}"
    if st == SCHED_WEEKLY:
        wd = int(task.get("weekday", 0)) % 7
        if now.weekday() == wd and (now.hour, now.minute) < (hh, mm):
            return f"今天 {at_time}"
        days_ahead = (wd - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        d = now.date() + timedelta(days=days_ahead)
        return f"{d.strftime('%m-%d')} {at_time}"
    if st == SCHED_INTERVAL:
        interval = max(1, int(task.get("interval_minutes", 60)))
        last = float(task.get("last_run", 0.0) or 0.0)
        base = last if last > 0 else float(task.get("created", now.timestamp()) or now.timestamp())
        nxt = datetime.fromtimestamp(base + interval * 60)
        return f"下次 {nxt.strftime('%m-%d %H:%M')}"
    return at_time
