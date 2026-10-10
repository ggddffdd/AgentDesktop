# -*- coding: utf-8 -*-
"""编排页硬伤修复回归判据（v4.251.0）

覆盖：
- #1 授权请求到来时自动切到「聊天页」（idx 0），而非角色库页（idx 1）
      —— 根因：v4.148.1 页面重排（聊天从旧索引变 0、新增编排 tab）时漏改
        _on_auth_request 里的 _switch_page(1)。
- #2 _add_member 波次越界不崩（与 _edit_member/_del_member 对齐，补边界守卫）

跑法：QT_QPA_PLATFORM=offscreen python tests/test_legion_orch_audit_251.py
退出码 0=全过，1=有失败。
"""
import sys
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


def _make_window():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from legion_ui import LegionWindow
    return LegionWindow()


def test_auth_switch_to_chat():
    """#1：授权请求必须自动切到聊天页（idx 0），不是角色库页（idx 1）。"""
    w = _make_window()
    try:
        w._switch_page(3)                 # 先跳到编排页，模拟授权到来时不在聊天页
        w._on_auth_request("第 1 波验收", "建议打回")
        check("授权·自动切到聊天页(idx0)", w._stack.currentIndex() == 0)
    finally:
        w.close()


def test_add_member_bounds():
    """#2：_add_member 波次越界应提前 return，不崩、不弹 RolePicker。"""
    import legion_ui
    w = _make_window()
    try:
        p = legion.new_project(name="测试团", emoji="\U0001f9ea")
        p["waves"] = [legion.new_wave()]        # 只有 1 个波
        w.data.setdefault("projects", []).append(p)
        w.cur_project_id = p["id"]

        # 哨兵：若守卫失效、流程走到 RolePicker，构造时立刻 raise
        class _Sentinel:
            def __init__(self, *a, **k):
                raise AssertionError("越界波次不应走到 RolePicker")

        orig = legion_ui.RolePicker
        legion_ui.RolePicker = _Sentinel
        try:
            w._add_member(99)                   # 越界，应提前 return
            ok = True
        except Exception:
            ok = False
        finally:
            legion_ui.RolePicker = orig
        check("添加成员·越界波次不崩", ok)
    finally:
        w.close()


if __name__ == "__main__":
    test_auth_switch_to_chat()
    test_add_member_bounds()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
