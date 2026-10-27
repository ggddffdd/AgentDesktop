# -*- coding: utf-8 -*-
"""v4.289.5：产物级硬要求（用户显式要求产出文件）。

背景（2026-10-27 GPT 审查第 2 条）：任务完成条件此前只覆盖「字面点名工具」，
「把结论存成 md」「导出到桌面」这类**只要产物、不点名工具**的请求完全不进
硬验收 —— 模型说一句「已保存」就能收尾，账本无感。

口径：落盘动词 **且**（文件扩展名 或 明确落盘位置），见
`task_state.SAVE_REQUIREMENT_POLICY`。工具名字面策略保持原样不动
（`HARD_REQUIREMENT_POLICY == "literal_tool_name_only"`）。

纯标准库，不联网；数据改道临时目录。
"""
import os
import sys
import tempfile
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_295_accept_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import agent_loop as loop
import task_state as tsmod

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


print("=" * 66)
print("A 组：落盘诉求识别（宁可漏，不可错）")
print("=" * 66)

_YES = ["把结论存成 md", "导出成 pdf", "存成 docx", "把结果写入 result.json",
        "存到 D盘", "下载到本地文件夹", "另存为 csv 文件"]
_NO = ["帮我写一段口播文案", "这个想法存在吗", "保存一下", "把这条写入记忆",
       "生成一份周报", "这个 cmd 命令怎么用", "xlsx 文件怎么打开",
       "帮我看看 docstring", "搜索一下最近的新闻", ""]

_miss = [s for s in _YES if not tsmod.expect_save_from_text(s)]
_false = [s for s in _NO if tsmod.expect_save_from_text(s)]
check("A1 显式落盘诉求全部识别（7 例）", not _miss, _miss)
check("A2 非落盘请求零误判（9 例）", not _false, _false)
check("A3 工具名字面策略未被改动（刻意设计，勿回退）",
      tsmod.HARD_REQUIREMENT_POLICY == "literal_tool_name_only",
      tsmod.HARD_REQUIREMENT_POLICY)
check("A4 落盘策略已登记为可检出常量",
      getattr(tsmod, "SAVE_REQUIREMENT_POLICY", "") == "save_verb_and(ext_or_location)",
      getattr(tsmod, "SAVE_REQUIREMENT_POLICY", "缺失"))

print()
print("=" * 66)
print("B 组：账本消费产物级硬要求")
print("=" * 66)

ts = tsmod.TaskState("存成 md", [], expect_save=True)
check("B1 没点名工具也算有硬要求（否则 nudge/小结根本不介入）",
      ts.has_requirements() is True, ts.has_requirements())
check("B2 无产物时给出明确未达标项",
      any("产出文件" in x for x in ts.pending()), ts.pending())
check("B3 is_complete 不再误判为完成", ts.is_complete() is False)
_sum = loop.build_summary(ts) or ""
check("B4 小结的「未完成」段反映产物缺口（有工具记录时才出小结）",
      _sum == "" or "产出文件" in _sum, _sum.replace("\n", " | ")[:160])

# 真正落盘后应清零
_p = os.path.join(_SBX, "out.md")
with open(_p, "w", encoding="utf-8") as f:
    f.write("# ok\n")
ts2 = tsmod.TaskState("存成 md", [], expect_save=True)
ts2.record_tool("write_file", {"path": _p}, "已写入 out.md", ok=True)
check("B5 真落盘后未达标清单清零", ts2.pending() == [], ts2.pending())
check("B6 真落盘后 is_complete 为真", ts2.is_complete() is True)
# 声称写入但文件不存在 → 双缺口（产物未落地 + 落盘诉求未兑现）
_ghost = os.path.join(_SBX, "ghost.md")
ts5 = tsmod.TaskState("存成 md", [], expect_save=True)
ts5.record_tool("write_file", {"path": _ghost}, "已写入", ok=True)
check("B7 声称写入但文件不存在 → 判未达标（不信文案）",
      ts5.is_complete() is False and len(ts5.pending()) >= 1, ts5.pending())
_sum2 = loop.build_summary(ts2) or ""
check("B8 真落盘后小结写「未完成：无」", "未完成：无" in _sum2,
      _sum2.replace("\n", " | ")[:160])
_rep2 = loop.verify_report(ts2)
check("B9 真落盘后核验记录报「已全部落实」",
      any("已全部落实" in x for x in _rep2), _rep2)

# 未要求落盘的请求不该被逼着产出文件
ts3 = tsmod.TaskState("帮我写一段口播文案", [])
ts3.record_tool("write_file", {"path": _p}, "已写入", ok=True)
check("B10 未要求落盘时 artifact_gap 恒为 None",
      ts3.artifact_gap() is None and ts3.expect_save is False)

# 核验记录要反映缺口
ts4 = tsmod.TaskState("导出到桌面", [], expect_save=True)
ts4.record_tool("web_search", {"query": "x"}, "结果", ok=True)
_rep4 = loop.verify_report(ts4)
check("B11 verify_report 记录产物缺口",
      any("产出文件" in x for x in _rep4), _rep4)

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
