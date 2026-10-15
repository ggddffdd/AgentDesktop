# -*- coding: utf-8 -*-
"""v4.281.0 早停/超时「加明显 toast」验收判据（judge-first）。

根因（用户体感「小臭执行任务突然停止、没反馈」）
------------------------------------------------
agent 早停/超时路径原来只走 `stream_commit.emit`（写进 auto_{tid} 专属会话）
+ 托盘通知一闪而过。用户不在任务视图时，任务**静默停掉**无任何可见反馈。
v4.281.0 给所有早停/超时/澄清卡住补 `_stop_toast`，跨视图弹明显 toast。

本套件不依赖 Qt 渲染：用「把 AgentWorker._note_exit 绑到假对象」的方式，
验证「记录退出旗标 + 弹 toast」两件事都发生，且 **toast 抛错也不影响旗标记录**
（fail-closed：反馈失败绝不能让停止逻辑崩）。

静态护栏：agent.py 必须含 4 处 `_stop_toast` 调用点（3 处超时 + 1 处澄清卡住），
谁删了对应调用，静态判据立刻红。

judge-first：改源码前本文件应为 FAIL（_STOP_TOAST 未定义 / 调用点未插入）。
"""
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import agent as _agent

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s%s" % (name, ("  —— " + detail) if detail else ""))


# ===== 1. _STOP_TOAST 映射格式正确 =====
_stop = getattr(_agent.AgentWorker, "_STOP_TOAST", None)
_valid_kinds = {"error", "warn", "info", "success"}
_expected_keys = {"tool_fail", "repeat_converged", "question_stopped",
                  "fake_tool_stopped", "api_error"}

check("1.1 _STOP_TOAST 已定义", isinstance(_stop, dict), repr(type(_stop)))
if isinstance(_stop, dict):
    check("1.2 恰含 5 类早停原因", set(_stop.keys()) == _expected_keys,
          str(set(_stop.keys())))
    ok_fmt = all(
        isinstance(v, (tuple, list)) and len(v) == 2
        and isinstance(v[0], str) and v[1] in _valid_kinds
        for v in _stop.values()
    )
    check("1.3 每条 (文案str, kind有效) 格式正确", ok_fmt, repr(_stop))
    check("1.4 文案非空且为中文反馈", all(v[0].strip() for v in _stop.values()))


# ===== 2. _note_exit 命中映射时弹 toast（绑到假对象，不渲染 Qt）=====
class _Fake:
    """最小替身：只提供 _note_exit 需要的属性。

    ⚠️ 不继承 AgentWorker（避免触发 __init__），但 _note_exit 里用
    `self._STOP_TOAST` —— 必须显式把类属性挂上来，否则 AttributeError 被
    _note_exit 的 except 吞掉、toast 静默不弹（那正是真实代码不会有的坑）。
    """
    _exit_flags = {}
    _calls = []  # 记录 _stop_toast 被调用
    _STOP_TOAST = _agent.AgentWorker._STOP_TOAST

    def _stop_toast(self, text, kind="warn"):
        self._calls.append((text, kind))


_fake = _Fake()
_note = types.MethodType(_agent.AgentWorker._note_exit, _fake)

check("2.1 方法可绑定执行", callable(_note))
for key in _expected_keys:
    _fake._exit_flags = {}
    _fake._calls = []
    _note(key)
    exp_text, exp_kind = _stop[key]
    fired = any(c[0] == exp_text and c[1] == exp_kind for c in _fake._calls)
    check("2.2 _note_exit(%s) 弹对应 toast" % key, fired,
          "calls=%r" % _fake._calls)
    # 旗标也要照常记录（不因为弹 toast 丢）
    check("2.3 _note_exit(%s) 旗标仍记录" % key,
          _fake._exit_flags.get(key) in (True, 1), repr(_fake._exit_flags))

# 不在映射里的 key 不应弹 toast（如 tool_calls 是计数类、非早停）
_fake._exit_flags = {}
_fake._calls = []
_note("tool_calls", 1)
check("2.4 非早停 key 不弹 toast", _fake._calls == [],
      "calls=%r" % _fake._calls)


# ===== 3. fail-closed：toast 抛错不能破坏停止逻辑 =====
class _FakeRaise:
    _exit_flags = {}
    _calls = []
    _STOP_TOAST = _agent.AgentWorker._STOP_TOAST

    def _stop_toast(self, text, kind="warn"):
        self._calls.append((text, kind))
        raise RuntimeError("模拟 toast 渲染炸了")


_fr = _FakeRaise()
_note_r = types.MethodType(_agent.AgentWorker._note_exit, _fr)
try:
    _note_r("tool_fail")
    check("3.1 toast 抛错时 _note_exit 不传播异常", True)
except Exception as e:  # noqa
    check("3.1 toast 抛错时 _note_exit 不传播异常", False, "抛了 %s" % type(e).__name__)
check("3.2 异常下旗标仍记录", _fr._exit_flags.get("tool_fail") in (True, 1),
      repr(_fr._exit_flags))


# ===== 4. 静态护栏：agent.py 含 4 处 _stop_toast 调用点 =====
_src = open(os.path.join(ROOT, "agent.py"), encoding="utf-8", newline="").read()
# 超时 3 处（MAX_DURATION 分支）+ 澄清卡住 1 处，均带 v4.281.0 注释
_n_timeout = _src.count('self._stop_toast(f"任务执行超时')
_n_clarify = _src.count('self._stop_toast("任务需要你确认方向')
check("4.1 三处超时分支都补了 toast", _n_timeout == 3, "实际 %d 处" % _n_timeout)
check("4.2 澄清卡住分支补了 toast", _n_clarify == 1, "实际 %d 处" % _n_clarify)

# ui.py 自动化收口也补 toast
_ui_src = open(os.path.join(ROOT, "ui.py"), encoding="utf-8", newline="").read()
check("4.3 _finish_automation_run 补了 toast",
      "toast(message, kind=\"success\" if ok else \"error\"" in _ui_src)


print("\nPASS=%d  FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
