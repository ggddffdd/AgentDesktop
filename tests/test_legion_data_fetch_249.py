# -*- coding: utf-8 -*-
"""军团数据抓取系统性清 web_fetch 回归判据（v4.249.0）

覆盖：web_fetch 从 4 个口子彻底移除（角色卡 / 能力覆盖率映射 / 自动垫能力映射 / 工具候选列表），
统一走 browser_read（web_fetch 的超集，能抓 JS 渲染页 + 静态页）。

跑法：python tests/test_legion_data_fetch_249.py（legion.py 无 Qt 依赖，可直接跑）
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


def test_roles_no_web_fetch():
    """① 4 个残留角色卡移除 web_fetch，补 browser_read。"""
    lib = legion.default_role_library()
    for name in ("知乎策略师", "跨境电商操盘", "商业策略师", "AI引用策略师"):
        r = next((x for x in lib if x.get("name") == name), None)
        check(f"角色·{name}·无web_fetch",
              r is not None and "web_fetch" not in r.get("tools", []))
        check(f"角色·{name}·有browser_read",
              r is not None and "browser_read" in r.get("tools", []))


def test_capability_requires_no_web_fetch():
    """② 能力覆盖率映射表清 web_fetch。"""
    for cap, req in legion._CAPABILITY_REQUIRES.items():
        check(f"能力·{cap}·无web_fetch", "web_fetch" not in req.get("tools", []))


def test_tag_map_no_web_fetch():
    """③ 自动垫能力映射表清 web_fetch。"""
    for tag, req in legion.CAPABILITY_TAG_MAP.items():
        check(f"标签·{tag}·无web_fetch", "web_fetch" not in req.get("tools", []))


def test_candidates_no_web_fetch():
    """④ 工具候选列表清 web_fetch。"""
    check("候选列表·无web_fetch", "web_fetch" not in legion.TOOL_CANDIDATES)


if __name__ == "__main__":
    test_roles_no_web_fetch()
    test_capability_requires_no_web_fetch()
    test_tag_map_no_web_fetch()
    test_candidates_no_web_fetch()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
