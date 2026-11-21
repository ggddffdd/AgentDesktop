# -*- coding: utf-8 -*-
"""v4.319.0 P2-2 判据：合并代替硬裁（超上限时归档+合并，不物理删除）。

工单 P2-2：_trim_oldest 超限时把最旧 N 条合并成带日期区间的摘要条目，
原条目归档不删；合并条目保留 source/entity_id 溯源；召回可命中合并条目。

判据两层：
  A 层 —— 纯函数行为（build_merge_entry 摘要/日期区间/溯源）+ 归档不删 + 召回命中。
  B 层 —— 源码契约（_trim_oldest 真调合并、真归档、真挂 changelog）。
"""
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

print("=== A 层：build_merge_entry 纯函数 ===")

bme = getattr(ms, "build_merge_entry", None) if ms else None
OLD = [
    ("2026-09-01 10:00", "[元数据] 来源:研究 | 实体:项目A\n项目A 立项，目标做 X"),
    ("2026-09-15 11:30", "[元数据] 来源:会议 | 实体:项目B\n项目B 完成初版，待评审"),
    ("2026-10-02 09:00", "无元数据的普通条目：买了咖啡机"),
]

if bme is not None:
    merged = bme(OLD)
    check("A1  合并条目非空字符串", isinstance(merged, str) and len(merged) > 0)
    check("A2  含日期区间（2026-09 ~ 2026-10）",
          "2026-09" in merged and "2026-10" in merged, "merged=%r" % merged[:120])
    check("A3  标明这是合并摘要", "摘要" in merged or "合并" in merged)
    check("A4  保留来源溯源（研究/会议）",
          "研究" in merged and "会议" in merged, "merged=%r" % merged[:160])
    check("A5  保留 entity_id 溯源（项目A/项目B）",
          "项目A" in merged and "项目B" in merged)
    check("A6  含原条目事实关键字（可从归档找回+召回命中）",
          "立项" in merged and "咖啡机" in merged, "merged=%r" % merged[:200])
    check("A7  空输入不炸", isinstance(bme([]), str))
else:
    for n in ["A1 合并非空", "A2 日期区间", "A3 摘要标记", "A4 来源溯源",
              "A5 实体溯源", "A6 事实关键字", "A7 空输入"]:
        check(n, False, "build_merge_entry 不存在")

# 端到端：隔离目录跑 _trim_oldest，验归档不删 + 条目回落 + recall 命中
if ms is not None and hasattr(ms, "build_merge_entry"):
    with tempfile.TemporaryDirectory() as td:
        try:
            ms._configure(td)
        except Exception as e:
            print("configure note", e)
        # 造 5 条旧记忆
        lines = []
        for i in range(5):
            lines.append("## 2026-09-%02d 10:00\n[元数据] 来源:测试%d | 实体:实体%d\n事实内容%d 关键词%d" % (i + 1, i, i, i, i))
        txt = "\n".join(lines) + "\n"
        mempath = os.path.join(td, "memory.md")
        with open(mempath, "w", encoding="utf-8") as f:
            f.write(txt)
        before = ms.memory_stats()[0]
        # 裁到 3 条
        ms._trim_oldest(3)
        after = ms.memory_stats()[0]
        check("A8  条目数回落到上限内", after <= 3, "before=%d after=%d" % (before, after))
        # 归档存在（不物理删除）
        archived = False
        for root, dirs, files in os.walk(td):
            for fn in files:
                if "merge" in fn.lower() or "归档" in fn or "archive" in fn.lower():
                    archived = True
        check("A9  原条目归档落盘（不物理删除）", archived, "td=%s" % td)
        # 合并条目能被召回命中
        rec = ms.recall_memory("事实内容1", limit=8, max_chars=4000)
        check("A10 合并条目可被召回命中", "事实内容1" in rec or "摘要" in rec,
              "rec=%r" % rec[:200])
else:
    for n in ["A8 条目回落", "A9 归档不删", "A10 召回命中"]:
        check(n, False, "memory_store 未就绪")

print("=== B 层：源码契约（语义锚定）===")
import re as _re


def _func_body(src, name):
    m = _re.search(r"^def %s\b(?:(?!\ndef )[\s\S])*" % _re.escape(name), src, _re.M)
    return m.group(0) if m else ""


trim = _func_body(MS_SRC, "_trim_oldest")
check("B1  _trim_oldest 调用 build_merge_entry（真合并非硬删）",
      "build_merge_entry(" in trim, "len=%d" % len(trim))
check("B2  _trim_oldest 归档落盘（不物理删除）",
      "归档" in trim or "archive" in trim.lower() or "_merge_archive" in trim)
check("B3  _trim_oldest 挂 changelog（merge 记一笔）",
      "_changelog_append(" in trim)
# 硬删标志应消失：不再用裸 keep=parts[-max:] 直接覆盖（改造后应有 merge 分支）
check("B4  _trim_oldest 不再纯硬裁（删掉裸 keep=parts[-max:]）",
      "parts[-max_entries:]" not in trim)

print("汇总：PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
