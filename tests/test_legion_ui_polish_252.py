# -*- coding: utf-8 -*-
"""军团 UI 体验优化判据（v4.252.0 · A 档 1+2）

覆盖：
- #1 RoleEditor 技能列表：搜索框存在 + 按 category 分组 + 搜索过滤生效
- #2 _move_member_to：成员跨波直接移动（不再只靠 ↑↓ 一格一格挪）

跑法：QT_QPA_PLATFORM=offscreen python tests/test_legion_ui_polish_252.py
退出码 0=全过，1=有失败。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# —— 必须在 import legion 之前改道，否则会写进真实军团目录 ——
_SBX = tempfile.mkdtemp(prefix="xc_sbx_polish252_")
os.environ["XC_LEGION_DIR"] = _SBX
os.environ.setdefault("XC_USER_DATA_DIR", os.path.join(_SBX, "userdata"))

import legion  # noqa: E402

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


def test_skill_filter_group():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from legion_ui import RoleEditor
    e = RoleEditor({})
    # 搜索框存在
    check("技能·有搜索框", hasattr(e, "e_skill_filter"))
    # 造 3 个技能、2 个分类（可控数据，不依赖真实技能目录）
    e._all_skills = [
        {"slug": "a", "name": "文本处理A", "emoji": "📝", "description": "去AI味", "category": "文本处理"},
        {"slug": "b", "name": "生图B", "emoji": "🎨", "description": "画图", "category": "生图"},
        {"slug": "c", "name": "文本处理C", "emoji": "📄", "description": "文档", "category": "文本处理"},
    ]
    e._selected_slugs = set()
    e._build_skill_items("")
    # 精确检查「📂 分类 header」而非 name 子串（name 本身可能含分类名，会误判）
    cats = set()
    for i in range(e.e_skills.count()):
        t = e.e_skills.item(i).text()
        if t.startswith("📂 "):
            cats.add(t.split(" ", 1)[1].strip())
    check("技能·按分类分组", cats == {"文本处理", "生图"})
    # 搜索过滤：只留含「文本」的项
    e._build_skill_items("文本")
    texts2 = [e.e_skills.item(i).text() for i in range(e.e_skills.count())]
    has_a = any("文本处理A" in t for t in texts2)
    has_c = any("文本处理C" in t for t in texts2)
    has_b = any("生图B" in t for t in texts2)
    check("技能·搜索过滤生效", has_a and has_c and not has_b)


def test_move_member_to():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from legion_ui import LegionWindow
    w = LegionWindow()
    try:
        p = legion.new_project(name="t", emoji="🧪")
        r1 = legion.new_role(name="成员A", emoji="A")
        r2 = legion.new_role(name="成员B", emoji="B")
        p["waves"] = [{"members": [r1]}, {"members": [r2]}]
        w.data.setdefault("projects", []).append(p)
        w.cur_project_id = p["id"]
        w._move_member_to(0, 0, 1)          # 成员A 从第1波移到第2波
        check("移动·第1波清空", (p["waves"][0].get("members") or []) == [])
        check("移动·成员到第2波末尾",
              p["waves"][1]["members"][-1]["name"] == "成员A")
        # 越界目标波：不崩
        ok = True
        try:
            w._move_member_to(0, 0, 99)
        except Exception:
            ok = False
        check("移动·越界目标不崩", ok)
    finally:
        w.close()


if __name__ == "__main__":
    test_skill_filter_group()
    test_move_member_to()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
