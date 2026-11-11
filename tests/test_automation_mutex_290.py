# -*- coding: utf-8 -*-
"""v4.290.0 P2：自动化任务不再打断用户正在跑的任务。

背景（2026-11-11）：`_on_automation_tick` 只用 `self._busy` 当互斥闸，而 `_busy`
带 120 秒超时兜底（`_busy_timeout` 会把置回 False）——长任务跑到 2 分钟后，
自动化任务就能趁虚而入；`_agent_run` 起新任务前会 `request_stop()` 旧 worker，
于是**用户正在跑的任务被强杀**，表现为「任务没做完就停」。

纯标准库 + offscreen Qt。
"""
import os
import sys
import tempfile
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_290_mutex_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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


class _Probe:
    """记录「有没有走到建会话这一步」，并故意让后续崩掉以断开流程。"""

    def __init__(self):
        self.opened = 0

    def session(self, task):
        self.opened += 1
        raise RuntimeError("stop-here")


def fire(active, auto_task):
    w = UI.ChatWindow.__new__(UI.ChatWindow)
    w._agent_active = active
    w._auto_task_active = auto_task
    pr = _Probe()
    w._automation_session = pr.session
    try:
        ret = w._fire_automation_run({"id": "t1", "name": "任务", "message": "do"})
    except Exception:
        ret = "__raised__"
    return ret, pr.opened


print("=" * 66)
print("X 组：接线（源码钉子）")
print("=" * 66)

check("X1 tick 闸门补上了 _agent_active（不再只看会超时的 _busy）",
      "_busy or getattr(self, \"_agent_active\", False)" in _SRC)
check("X2 _fire_automation_run 内有双保险检查",
      "P2 双保险" in _SRC)
check("X3 延后不 mark_fired（返回 False 语义保留）",
      "自动化任务「%s」延后到下一 tick" in _SRC)

print()
print("=" * 66)
print("B 组：三种状态下的行为")
print("=" * 66)

# B1 用户任务在跑 → 直接返回 False，且完全不去建会话
ret1, opened1 = fire(True, None)
check("B1 用户任务在跑 → 返回 False 且不碰会话数据",
      ret1 is False and opened1 == 0, (ret1, opened1))

# B2 自动化任务自己在跑（_auto_task_active 非空）→ 新闸门不该拦它
ret2, opened2 = fire(True, {"id": "prev"})
check("B2 自动化任务在跑 → 新闸门不拦（继续走原流程）",
      opened2 == 1, (ret2, opened2))

# B3 空闲 → 不拦
ret3, opened3 = fire(False, None)
check("B3 空闲 → 不拦（原逻辑不受影响）", opened3 == 1, (ret3, opened3))

# B4 _agent_active 属性缺失（老对象/未初始化）→ 按空闲处理，不误拦
class _NoActive:
    pass

w4 = UI.ChatWindow.__new__(UI.ChatWindow)
pr4 = _Probe()
w4._automation_session = pr4.session
w4._auto_task_active = None
try:
    w4._fire_automation_run({"id": "t4", "name": "n", "message": "do"})
except Exception:
    pass
check("B4 _agent_active 未初始化时不误拦（getattr 兜底）",
      pr4.opened == 1, pr4.opened)

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
