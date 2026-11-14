# -*- coding: utf-8 -*-
"""v4.303.0：启动即补跑与自动化任务卡死的回归判据。

背景（2026-11-14 16:47 实测）：
  16:47:51 程序启动 → 16:47:52 QTimer 首个 tick 立刻把「今天没成功跑过」的
  daily 任务判为到期 → 16:47:57 已开始 web_fetch（每日 AI 新闻抓取）；
  16:48:19 第一个任务收尾后，第二个失败任务（GitHub 热榜）接着接管 UI；
  16:49:56 Windows Application Hang (AppHangB1) 判定「停止与 Windows 交互」。
  现场数据：两个 auto_{id} 会话分别 590/625 条、593249/310060 字符，
  sessions.json 的 active 卡死在 auto_75d5b6a48a85。

三处修复：
  A 启动宽限期（ACT_RUN 专属，提醒类不受影响）；
  B 后台自动化渲染不重建巨型 DOM + 自动化会话渲染上限 40；
  C 启动时把落在自动化专属会话的 active 归一化回普通会话。

纯标准库 + offscreen Qt；不碰真实用户数据目录。
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

_SBX = tempfile.mkdtemp(prefix="xc_303_grace_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import automation
import ui as UIMOD

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


AUTO = (ROOT / "automation.py").read_text(encoding="utf-8-sig")
UI = (ROOT / "ui.py").read_text(encoding="utf-8-sig")


def fn_seg(src, name):
    """取某个 def 的函数体（到下一个顶层 def / class 为止），失败返回空串。"""
    key = "def %s(" % name
    i = src.find(key)
    if i < 0:
        return ""
    j = src.find("\n    def ", i + len(key))
    k = src.find("\nclass ", i + len(key))
    ends = [x for x in (j, k) if x > 0]
    return src[i:(min(ends) if ends else len(src))]


def call_if(name, *args, **kwargs):
    """探测式调用：旧代码没有这些纯函数时返回全新的哨兵对象。

    不能返回 None —— 那会让「is None」「两个探测值相等」这类判据在旧代码
    上假绿。每次新建一个 object() 既不崩、也不等于任何期望值，红因干净。
    """
    fn = getattr(automation, name, None)
    if fn is None:
        return object()
    try:
        return fn(*args, **kwargs)
    except Exception:
        return object()


def api_ok():
    return all(hasattr(automation, n) for n in (
        "startup_grace_blocks", "pick_user_active_sid",
        "render_limit_for_sid"))


# ============================================================
# A 组：启动宽限期纯函数
# ============================================================
check("A1 宽限常量 90 秒、自动化渲染上限 40 条",
      getattr(automation, "STARTUP_GRACE_SEC", None) == 90.0
      and getattr(automation, "MAX_RENDERED_AUTOMATION_MSGS", None) == 40)
check("A2 启动 10 秒 → 拦住",
      call_if("startup_grace_blocks", 0.0, 10.0) is True)
check("A3 启动 89.9 秒 → 仍拦住",
      call_if("startup_grace_blocks", 0.0, 89.9) is True)
check("A4 恰好 90 秒 → 放行（边界不拖堂）",
      call_if("startup_grace_blocks", 0.0, 90.0) is False)
check("A5 启动 91 秒 → 放行",
      call_if("startup_grace_blocks", 0.0, 91.0) is False)
check("A6 时钟倒退 → 保守拦住",
      call_if("startup_grace_blocks", 20.0, 10.0) is True)
check("A7 坏值 None / 非数字 → 保守拦住，不抛异常",
      call_if("startup_grace_blocks", None, 10.0) is True
      and call_if("startup_grace_blocks", "bad", 10.0) is True
      and call_if("startup_grace_blocks", 0.0, None) is True)
check("A8 自定义 grace_sec 生效",
      call_if("startup_grace_blocks", 0.0, 4.9, grace_sec=5.0) is True
      and call_if("startup_grace_blocks", 0.0, 5.1, grace_sec=5.0) is False)
check("A9 纯函数不依赖真实时钟（同一入参结果稳定）",
      call_if("startup_grace_blocks", 100.0, 150.0)
      == call_if("startup_grace_blocks", 100.0, 150.0))


# ============================================================
# B 组：active 归一化纯函数
# ============================================================
class FakeSession:
    def __init__(self, sid, created=""):
        self.sid = sid
        self.created = created


def mk_sessions(*items):
    return {sid: FakeSession(sid, created) for sid, created in items}


_s = mk_sessions(("auto_news", "2026-11-14 16:48:19"),
                 ("user_old", "2026-11-10 09:00:00"),
                 ("user_new", "2026-11-14 10:29:34"),
                 ("auto_git", "2026-11-14 16:48:20"))
check("B1 active 落在自动化会话 → 切回最近的普通会话",
      call_if("pick_user_active_sid", _s, "auto_news") == "user_new")
check("B2 active 本来就是普通会话 → 不动 store",
      call_if("pick_user_active_sid", _s, "user_old") is None)
check("B3 只有自动化会话 → 返回 None（不硬造会话）",
      call_if("pick_user_active_sid",
              mk_sessions(("auto_news", "2026-11-14 16:48:19")),
              "auto_news") is None)
check("B4 空会话表 → 返回 None",
      call_if("pick_user_active_sid", {}, "auto_news") is None
      and call_if("pick_user_active_sid", None, "auto_news") is None)
check("B5 自定义前缀生效",
      call_if("pick_user_active_sid", _s, "auto_news", prefix="user_") is None
      and call_if("pick_user_active_sid", _s, "user_old", prefix="user_")
      in ("auto_news", "auto_git"))
check("B6 纯函数不修改传入的会话表",
      api_ok() and set(_s.keys()) == {"auto_news", "user_old", "user_new", "auto_git"})
check("B7 created 缺失也不抛（空串排序稳定）",
      call_if("pick_user_active_sid",
              mk_sessions(("auto_x", "2026-11-14"), ("u1", "")),
              "auto_x") == "u1")


# ============================================================
# C 组：渲染上限纯函数
# ============================================================
check("C1 自动化专属会话 → 上限 40",
      call_if("render_limit_for_sid", "auto_news", 300) == 40)
check("C2 普通会话 → 沿用 300",
      call_if("render_limit_for_sid", "user_new", 300) == 300)
check("C3 原上限更小时取更小值（不倒扩）",
      call_if("render_limit_for_sid", "auto_news", 20) == 20)
check("C4 返回 int，空 sid 按普通会话处理",
      isinstance(call_if("render_limit_for_sid", "", 300), int)
      and call_if("render_limit_for_sid", None, 300) == 300)
check("C5 自定义前缀生效",
      call_if("render_limit_for_sid", "user_new", 300, prefix="user_") == 40
      and call_if("render_limit_for_sid", "auto_news", 300, prefix="user_") == 300)


# ============================================================
# D 组：源码接线（三处都得真的接上）
# ============================================================
_init = fn_seg(UI, "__init__")
_tick = fn_seg(UI, "_on_automation_tick")
_render = fn_seg(UI, "_render_messages")
_fire = fn_seg(UI, "_fire_automation_run")
_restore = fn_seg(UI, "_restore_session_after_automation")

check("D1 三个纯函数都在 automation.py 里定义",
      all(("def %s(" % n) in AUTO for n in (
          "startup_grace_blocks", "pick_user_active_sid",
          "render_limit_for_sid")))
check("D2 常量在 automation.py 单源定义",
      "STARTUP_GRACE_SEC = 90.0" in AUTO
      and "MAX_RENDERED_AUTOMATION_MSGS = 40" in AUTO)
check("D3 构造期记录宽限起点",
      "self._automation_grace_started = time.monotonic()" in _init)
check("D4 构造期调用 active 归一化并真的切回去",
      "automation.pick_user_active_sid(" in _init
      and "self.store.switch(_fallback_sid)" in _init)
check("D5 tick 里真的调用宽限纯函数",
      "automation.startup_grace_blocks(" in _tick)
check("D6 宽限只拦 ACT_RUN，提醒类用 ACT_REMIND 默认放行",
      "== automation.ACT_RUN" in _tick
      and 't.get("action", automation.ACT_REMIND)' in _tick)
check("D7 宽限判断在 ready_to_fire 之前",
      _tick.find("startup_grace_blocks(") < _tick.find("ready_to_fire(")
      and "startup_grace_blocks(" in _tick)
check("D8 _render_messages 有后台自动化跳过分支",
      "not follow_nav" in _render
      and 'getattr(self, "_auto_task_active", None) is not None' in _render
      and "automation.SESSION_SID_PREFIX" in _render
      and "return" in _render[:900])
check("D9 跳过分支用 follow_nav 放行用户主动渲染",
      "if (not follow_nav" in _render)
check("D10 全量重建改走按会话类型的渲染上限",
      "automation.render_limit_for_sid(" in _render
      and "msgs[-_limit:]" in _render
      and "len(msgs) > _limit" in _render)
check("D11 普通会话的 MAX_RENDERED_MSGS 常量仍被保留",
      "get_app_icon, APP_VERSION, MAX_RENDERED_MSGS" in UI)
check("D12 后台任务启动不再调用 _render_messages（AppHang 直接来源）",
      "self._render_messages(force_bottom=True)" not in _fire)
check("D13 收尾切回用户会话仍照常渲染（旧体验不丢）",
      "self._render_messages(force_bottom=True)" in _restore
      and "follow_nav=True" not in _restore)
check("D14 v4.297 薄投喂视图没被改坏",
      "def history_for_agent(" in AUTO
      and "automation.history_for_agent(" in UI)
check("D15 tick 仍保留原有互斥与锁（新宽限没绕过保护）",
      "self._busy" in _tick
      and "_automation_lock" in _tick
      and "_agent_active" in _tick)


# ============================================================
# E 组：真实调用 _on_automation_tick（不是只看源码字符串）
# ============================================================
class FakeStore:
    def __init__(self, tasks):
        self.tasks = tasks

    def list_enabled(self):
        return [t for t in self.tasks if t.get("enabled", True)]

    def save(self):
        return None


def mk_task(action, name):
    return {"id": name, "name": name, "action": action,
            "message": "测试指令", "schedule_type": "daily",
            "at_time": "00:00", "last_run": 0.0, "last_attempt": 0.0,
            "fail_count": 0, "enabled": True, "prefer_offpeak": False}


class FakeWindow:
    """只挂 _on_automation_tick 需要的属性，避开整个 Qt 窗口初始化。"""
    _on_automation_tick = UIMOD.ChatWindow._on_automation_tick

    def __init__(self, tasks, grace_started=0.0):
        self.automation_store = FakeStore(tasks)
        self._automation_grace_started = grace_started
        self._busy = False
        self._agent_active = False
        self._automation_lock = None
        self.fired = []
        self.reminded = []

    def _reload_automation_if_changed(self):
        return None

    def _fire_reminder(self, msg):
        self.reminded.append(msg)

    def _fire_automation_run(self, task):
        self.fired.append(task["id"])
        return True


def tick_now(w, monotonic_value=10.0):
    with mock.patch.object(UIMOD.time, "monotonic",
                           return_value=monotonic_value):
        w._on_automation_tick()


def run_tick(tasks, monotonic_value):
    w = FakeWindow(tasks)
    tick_now(w, monotonic_value)
    return w


_w1 = run_tick([mk_task(automation.ACT_RUN, "run_job"),
                mk_task(automation.ACT_REMIND, "remind_job")], 10.0)
check("E1 启动 10 秒：执行任务不触发、提醒任务正常触发",
      _w1.fired == [] and _w1.reminded == ["测试指令"],
      "fired=%r reminded=%r" % (_w1.fired, _w1.reminded))
check("E2 启动 89 秒：执行任务仍不触发",
      run_tick([mk_task(automation.ACT_RUN, "r")], 89.0).fired == [])
check("E3 过宽限（91 秒）：执行任务恢复触发",
      run_tick([mk_task(automation.ACT_RUN, "r")], 91.0).fired == ["r"])
_w4 = FakeWindow([mk_task(automation.ACT_RUN, "r")])
_w4._automation_grace_started = None
tick_now(_w4, 10.0)
check("E4 坏值宽限起点（None）→ 保守不触发，不抛异常",
      _w4.fired == [], _w4.fired)
check("E5 宽限没把原有 due 逻辑改坏",
      automation.ready_to_fire(mk_task(automation.ACT_RUN, "r"),
                               datetime.now()) is True)
check("E6 只跑一个：过宽限后两个执行任务也只提交第一个（原互斥保留）",
      run_tick([mk_task(automation.ACT_RUN, "a"),
                mk_task(automation.ACT_RUN, "b")], 91.0).fired == ["a"])


print("\n==== v4.303.0 启动宽限与自动化渲染：PASS=%d  FAIL=%d ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
