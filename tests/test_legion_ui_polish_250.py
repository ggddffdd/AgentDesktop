# -*- coding: utf-8 -*-
"""军团 UI 收尾回归判据（v4.250.0）

覆盖：
- 技能列表 item 显示 description 副行（治「记不得技能是干嘛的」）
- 团队库页 克隆/编辑/删除 按钮选中后才浮现（治「按钮多」）

跑法：QT_QPA_PLATFORM=offscreen python tests/test_legion_ui_polish_250.py
退出码 0=全过，1=有失败。
"""
import sys
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

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


def test_skill_list_desc():
    """技能列表 item 必须显示 description 副行（不再只靠悬停 tooltip）。

    v4.252.0：desc 副行逻辑从 __init__ 移进 _build_skill_items（搜索+分组重构），
    判据改为 inspect 该方法，避免重构后判据跟着失效。
    """
    import inspect
    from legion_ui import RoleEditor
    src = inspect.getsource(RoleEditor._build_skill_items)
    check("技能·item拼description副行", 'label += "\\n"' in src or "desc[:50]" in src)


def test_teams_crud_hidden():
    """团队库 克隆/编辑/删除 是选中后才需要的低频操作，默认隐藏。"""
    import inspect
    from legion_ui import LegionWindow
    src = inspect.getsource(LegionWindow._build_teams_page)
    check("团队·CRUD按钮默认隐藏",
          "self._team_edit_btns" in src and "b.setVisible(False)" in src)


if __name__ == "__main__":
    test_skill_list_desc()
    test_teams_crud_hidden()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
