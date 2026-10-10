# -*- coding: utf-8 -*-
"""军团产出质量 + 组队覆盖修复回归判据（v4.248.0）

覆盖：
- 组队对话化「总是新建」，不覆盖选中的老团队（静态断言 _chat_team_build 无 _cur_project）
- 选品官 tools 移除 web_fetch（对齐研究员/竞品分析师 v4.147.9 A 方案）
- 配图师 self_check 含交付物核对（缺图即未交付）

跑法：QT_QPA_PLATFORM=offscreen python tests/test_legion_quality_248.py
退出码 0=全过，1=有失败。
"""
import sys
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import legion

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


def _role(name):
    lib = legion.default_role_library()
    for r in lib:
        if r.get("name") == name:
            return r
    return None


def test_team_build_always_new():
    """组队对话化必须新建团队，不能复用/覆盖当前选中的老团队。"""
    import inspect
    from legion_ui import LegionWindow
    src = inspect.getsource(LegionWindow._chat_team_build)
    check("组队·总是新建", "new_project" in src and "self._cur_project()" not in src)


def test_selector_no_web_fetch():
    sel = _role("选品官")
    tools = sel.get("tools", []) if sel else []
    check("选品官·无web_fetch", sel is not None and "web_fetch" not in tools)
    check("选品官·有browser_read", sel is not None and "browser_read" in tools)


def test_illustrator_delivery_check():
    ill = _role("配图师")
    sc = ill.get("self_check", "") if ill else ""
    check("配图师·self_check含交付物核对", ill is not None and ("交付" in sc or "生图" in sc))


if __name__ == "__main__":
    test_team_build_always_new()
    test_selector_no_web_fetch()
    test_illustrator_delivery_check()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
