# -*- coding: utf-8 -*-
"""v4.319.0 P3-1 判据：记忆变更日志由代码维护（Agent 只读）。

工单 P3-1：每次 add/update/merge/delete 由代码追加 {ts, op, target_id, 摘要}，
保留最近 50 条；Agent 无写 API；memory_stats 展示 changelog 计数。

判据两层：
  A 层 —— 行为（追加/只读/上限50/字段齐全/stats兼容不炸）。
  B 层 —— 源码契约（变更点确实挂钩、无公开写接口、stats 2元组兼容）。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print("  [PASS] %s" % name)
        _p += 1
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        _f += 1


def read_src(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


MS_SRC = read_src(os.path.join(ROOT, "memory_store.py"))
try:
    import memory_store as ms
except Exception as e:
    ms = None
    print("import memory_store ERR", e)

print("=== A 层：changelog 行为 ===")

# 用隔离目录，绝不碰真实记忆库
if ms is not None and hasattr(ms, "get_changelog"):
    with tempfile.TemporaryDirectory() as td:
        try:
            ms._configure(td)
        except Exception as e:
            print("configure note", e)
        changelog_path = os.path.join(td, "memory_changelog.jsonl")

        # 初始为空
        check("A1  初始 changelog 为空", ms.get_changelog() == [])

        # 内部写入函数存在且能写
        append = getattr(ms, "_changelog_append", None)
        if append is not None:
            append("add", "target-a", "新增事实A", ts="2026-11-21 10:00")
            entries = ms.get_changelog()
            check("A2  追加后可读回", len(entries) == 1, "entries=%r" % entries)
            if entries:
                e = entries[0]
                check("A3  字段齐全(ts/op/target_id/摘要)",
                      set(("ts", "op", "target_id", "summary")).issubset(e.keys()),
                      "e=%r" % e)
                check("A4  字段值正确",
                      e.get("op") == "add" and e.get("target_id") == "target-a")
            # 上限 50
            for i in range(60):
                append("add", "t%d" % i, "s%d" % i, ts="2026-11-21 10:%02d" % (i % 60))
            entries = ms.get_changelog()
            check("A5  保留最近 50 条", len(entries) == 50, "len=%d" % len(entries))
            check("A6  保留的是最新的（t59 在）",
                  entries and entries[-1].get("target_id") == "t59",
                  "last=%r" % (entries[-1] if entries else None))
            # 落盘文件存在
            check("A7  changelog 独立落盘(jsonl)", os.path.exists(changelog_path))
        else:
            for n in ["A2 追加可读", "A3 字段齐全", "A4 字段值", "A5 上限50",
                      "A6 最新保留", "A7 独立落盘"]:
                check(n, False, "_changelog_append 不存在")

        # count 函数
        cnt = getattr(ms, "changelog_count", None)
        if cnt is not None:
            check("A8  changelog_count 与条目数一致", cnt() == len(ms.get_changelog()),
                  "cnt=%r" % cnt())
        else:
            check("A8  changelog_count 存在", False)

        # memory_stats 保持 2 元组（调用方兼容）
        try:
            r = ms.memory_stats()
            check("A9  memory_stats 仍返回 2 元组（不破坏调用方）",
                  isinstance(r, tuple) and len(r) == 2, "r=%r" % (r,))
        except Exception as e:
            check("A9  memory_stats 不炸", False, str(e))
else:
    for n in ["A1 初始为空", "A2 追加可读", "A3 字段齐全", "A4 字段值", "A5 上限50",
              "A6 最新保留", "A7 独立落盘", "A8 count", "A9 stats兼容"]:
        check(n, False, "memory_store.get_changelog 不存在")

print("=== B 层：源码契约（语义锚定）===")

# 写入点挂钩：append_memory 内要有 changelog 调用（真实调用行）
import re as _re
def _func_body(src, name):
    m = _re.search(r"^def %s\b(?:(?!\ndef )[\s\S])*" % _re.escape(name), src, _re.M)
    return m.group(0) if m else ""

app = _func_body(MS_SRC, "append_memory")
check("B1  append_memory 挂钩 changelog",
      "_changelog_append(" in app, "len=%d" % len(app))
trim = _func_body(MS_SRC, "_trim_oldest")
check("B2  _trim_oldest(merge) 挂钩 changelog",
      "_changelog_append(" in trim, "len=%d" % len(trim))
# Agent 只读：不能有公开的 write_changelog / set_changelog 等
check("B3  无公开 changelog 写接口",
      not _re.search(r"^def (write|set|update|delete)_changelog\b", MS_SRC, _re.M))
# __all__ 若有，只读函数应列入，写函数不应列入（若有 __all__）
m_all = _re.search(r"__all__\s*=\s*\[(?:(?!\])[\s\S])*\]", MS_SRC)
if m_all:
    check("B4  __all__ 含只读接口不含写接口",
          "get_changelog" in m_all.group(0) and "write_changelog" not in m_all.group(0))
else:
    check("B4  __all__ 检查（本模块无 __all__，跳过计通过）", True)
# 上限常量 50
check("B5  changelog 上限常量为 50",
      _re.search(r"CHANGELOG_MAX\s*=\s*50\b", MS_SRC) is not None)

print("汇总：PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
