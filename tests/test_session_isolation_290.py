# -*- coding: utf-8 -*-
"""v4.290.0 P0：自动化任务收尾把活动会话还给用户（修串台）。

背景（2026-11-11 实测）：`_fire_automation_run` 为满足 `_agent_run` 的
`store.active()` 取值，会 switch 到 `auto_{task_id}`，但**从不切回** ——
任务结束后 active 仍停在自动化会话，用户下一条消息直接写进那里，
表现就是「自动化任务和普通对话串在一起」。

纯标准库 + offscreen Qt；数据目录改道临时目录。
"""
import os
import sys
import tempfile
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_290_isolation_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import automation
import ui as UI

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


_SRC = (ROOT / "ui.py").read_text(encoding="utf-8-sig")


class _Sess:
    def __init__(self, sid, title=""):
        self.sid = sid
        self.title = title
        self.messages = []


class _Store:
    def __init__(self, sessions, active_sid):
        self.sessions = sessions
        self._active = active_sid
        self.switched = []
        self.saved = 0

    def active(self):
        return self.sessions.get(self._active)

    def switch(self, sid):
        self._active = sid
        self.switched.append(sid)

    def save(self):
        self.saved += 1


def make_window(store, prev_sid):
    w = UI.ChatWindow.__new__(UI.ChatWindow)
    w.store = store
    w._automation_prev_sid = prev_sid
    w._render_messages = lambda **k: None
    return w


print("=" * 66)
print("I 组：接线（源码钉子）")
print("=" * 66)

check("I1 启动时记录原会话（切到 auto_ 之前）",
      "_automation_prev_sid" in _SRC and
      _SRC.count("_automation_prev_sid") >= 4,
      _SRC.count("_automation_prev_sid"))
check("I2 收尾调用归还逻辑",
      "self._restore_session_after_automation()" in _SRC)
check("I3 归还逻辑存在且有「用户已切走就不打扰」的早退",
      "def _restore_session_after_automation" in _SRC
      and "用户已自己切走" in _SRC)
check("I4 启动失败路径清掉归还标记（防用过期 sid 切会话）",
      _SRC.count("self._automation_prev_sid = None") >= 3,
      _SRC.count("self._automation_prev_sid = None"))

print()
print("=" * 66)
print("R 组：归还行为")
print("=" * 66)

# R1 当前停在自动化会话 → 切回用户原会话
auto_sid = automation.SESSION_SID_PREFIX + "t1"
store = _Store({"chatA": _Sess("chatA"),
                auto_sid: _Sess(auto_sid, "自动化")}, auto_sid)
w = make_window(store, "chatA")
w._restore_session_after_automation()
check("R1 停在自动化会话 → 切回原会话",
      store._active == "chatA" and store.switched == ["chatA"], store.switched)
check("R2 切回后落了盘", store.saved == 1, store.saved)
check("R3 归还标记已清空（不残留到下一轮）",
      w._automation_prev_sid is None, w._automation_prev_sid)

# R4 用户中途自己切到别的会话 → 不打扰
store2 = _Store({"chatA": _Sess("chatA"), "chatB": _Sess("chatB"),
                 auto_sid: _Sess(auto_sid)}, "chatB")
w2 = make_window(store2, "chatA")
w2._restore_session_after_automation()
check("R4 用户已切到别的会话 → 不硬拽回来",
      store2._active == "chatB" and store2.switched == [], store2.switched)

# R5 原会话已被删 → 不硬切
store3 = _Store({auto_sid: _Sess(auto_sid)}, auto_sid)
w3 = make_window(store3, "chatA")
w3._restore_session_after_automation()
check("R5 原会话已被删除 → 停在原地，不报错",
      store3._active == auto_sid and store3.switched == [], store3.switched)

# R6 非自动化任务收尾（无 prev）→ 完全不动
store4 = _Store({"chatA": _Sess("chatA")}, "chatA")
w4 = make_window(store4, None)
w4._restore_session_after_automation()
check("R6 普通对话收尾（无 prev）→ 不动视图",
      store4._active == "chatA" and store4.switched == [] and store4.saved == 0)

# R7 当前已在原会话（无需切）→ 不重复 switch
store5 = _Store({"chatA": _Sess("chatA")}, "chatA")
w5 = make_window(store5, "chatA")
w5._restore_session_after_automation()
check("R7 当前已就是原会话 → 不重复切换",
      store5.switched == [] and store5.saved == 0, store5.switched)

# R8 store 抛异常 → 吞掉，不炸主流程
class _BoomStore(_Store):
    def switch(self, sid):
        raise RuntimeError("boom-290")

store6 = _BoomStore({"chatA": _Sess("chatA"), auto_sid: _Sess(auto_sid)}, auto_sid)
w6 = make_window(store6, "chatA")
_err = None
try:
    w6._restore_session_after_automation()
except Exception as e:
    _err = e
check("R8 切会话异常被吞（不影响 Agent 收尾）", _err is None, repr(_err))

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
