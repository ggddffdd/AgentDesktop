# -*- coding: utf-8 -*-
"""v4.285.0 判据：工具描述「选不错」——重叠族必须互相点名分工。

背景（彻查「工具调用准确性」P2）：db_delete 描述仅 5 字符「删除记录。」，
mouse/keyboard/screenshot/app_focus 等族普遍一句话——模型选错工具的
直接原因是描述没给足判别信息。本判据钉住两件事：
  1. 目标工具描述长度下限（不许再缩回一句话）；
  2. 每个工具的描述里必须出现其「分工邻居」的工具名（机检「说清了
     什么时候用我、什么时候用邻居」——这是重叠族选型的核心信息）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0

# 目标工具 → 描述中必须点名的分工邻居（写描述时逐一落实）
NEIGHBORS = {
    "db_update": ("db_insert", "db_delete"),
    "db_delete": ("db_update", "db_query"),
    "mouse_move": ("mouse_click",),
    "mouse_click": ("mouse_move",),
    "mouse_scroll": ("mouse_move",),
    "keyboard_type": ("keyboard_press", "clipboard_write"),
    "keyboard_press": ("keyboard_type",),
    "clipboard_read": ("clipboard_write",),
    "clipboard_write": ("keyboard_type", "app_type"),
    "window_list": ("window_focus", "process_list"),
    "window_focus": ("app_focus", "window_list"),
    "app_focus": ("window_focus", "app_launch"),
    "app_launch": ("process_start", "app_focus"),
    "process_start": ("app_launch",),
    "process_list": ("process_kill", "window_list"),
    "screenshot": ("app_screenshot",),
    "app_screenshot": ("screenshot",),
    "app_type": ("keyboard_type", "clipboard_write"),
    "app_get_text": ("app_screenshot",),
    "list_automation": ("create_automation", "update_automation", "delete_automation"),
}

MIN_LEN = 60


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s  <%s>" % (name, detail))


print("=" * 64)
print("v4.285.0 工具描述：重叠族分工点名")
print("=" * 64)

import tool_defs  # noqa: E402

_DEFS = tool_defs.TOOL_DEFS
_BY_NAME = {}
for _d in _DEFS:
    _fn = _d.get("function", _d)
    _BY_NAME[_fn.get("name", "")] = _fn.get("description", "") or ""

print("\n--- A. 全量底线：84 个工具描述都非空且不短得离谱 ---")
check("A1 工具总数仍是 84", len(_DEFS) == 84, "n=%d" % len(_DEFS))
_empty = [n for n, d in _BY_NAME.items() if not d or len(d) < 5]
check("A2 无空/极短描述", not _empty, repr(_empty))

print("\n--- B. 目标工具长度下限（%d 字符）---" % MIN_LEN)
_short = [(n, len(_BY_NAME.get(n, ""))) for n in NEIGHBORS
          if len(_BY_NAME.get(n, "")) < MIN_LEN]
check("B1 目标工具描述全部达到长度下限", not _short, repr(_short))

print("\n--- C. 分工邻居点名（机检「说清了何时用邻居」）---")
for _n, _nbs in sorted(NEIGHBORS.items()):
    _d = _BY_NAME.get(_n, "")
    _miss = [x for x in _nbs if x not in _d]
    check("C %s 描述点名邻居 %s" % (_n, "/".join(_nbs)), not _miss,
          "len=%d miss=%s" % (len(_d), _miss))

print()
print("=" * 64)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 64)
sys.exit(1 if FAIL else 0)
