# -*- coding: utf-8 -*-
"""军团数据抓取收尾回归判据（v4.250.0）

覆盖：
- browser_read 纳入抓取留痕（_FETCH_TRACE_TOOLS）
- 数据闸识别 browser_read 原文堆砌（「已读取网页文本（N 字）」贴进产出 = 未交付）

跑法：python tests/test_legion_data_verify_250.py（tools.py / legion.py 无 Qt 依赖，可直接跑）
退出码 0=全过，1=有失败。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import tools
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


def test_browser_read_traced():
    """browser_read 必须纳入抓取留痕（数据抓取最后一块留痕口子）。"""
    check("留痕·browser_read在追踪表", "browser_read" in tools._FETCH_TRACE_TOOLS)


def test_browser_read_dump_detected():
    """数据闸识别 browser_read 原文堆砌（成员把浏览器读取原文当交付物）。"""
    t = ("已读取网页文本（1500 字）：这里是网页正文内容，包含大量原始抓取的文字，"
         "未经任何加工整理就直接粘贴。")
    res = legion.audit_data_quality(t, role_name="研究员")
    check("数据闸·browser_read原文识别为堆砌",
          res.get("raw_dump") is True or any("浏览器" in e for e in (res.get("evidence") or [])))


if __name__ == "__main__":
    test_browser_read_traced()
    test_browser_read_dump_detected()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
