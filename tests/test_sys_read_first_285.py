# -*- coding: utf-8 -*-
"""v4.285.0 判据：AGENT_SYS_APPEND 必须同时含「汇报纪律」与「先读后说」。

背景（彻查「判断不准」双问题 P2）：
- v4.283.0 修了「没执行却说没执行」（汇报纪律）；
- 但「执行了却凭印象转述结果」这半边没人管——用户实证第三方转述/记错
  工具返回内容的事故多次出现。本判据钉住两条纪律并存，防后人精简时
  删掉任一条。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s  <%s>" % (name, detail))


print("=" * 64)
print("v4.285.0 系统提示：先读后说 + 汇报纪律")
print("=" * 64)

import config  # noqa: E402

APP = getattr(config, "AGENT_SYS_APPEND", "")

print("\n--- A. 「先读后说」条目在位（v4.285.0 新增）---")
check("A1 含 v4.285.0 先读后说标签", "【v4.285.0 先读后说】" in APP)
check("A2 要求照工具结果原文说", "原文" in APP and "工具结果" in APP)
check("A3 禁止凭印象转述/脑补", "凭印象" in APP and "脑补" in APP)
check("A4 记不准时要求回去重读", "重读" in APP)

print("\n--- B. v4.283.0 汇报纪律未被顶掉 ---")
check("B1 含 v4.283.0 汇报纪律标签", "【v4.283.0 汇报纪律】" in APP)
check("B2 禁止无据声称未执行", "没写文件" in APP or "没执行" in APP)

print("\n--- C. 基础形态 ---")
check("C1 AGENT_SYS_APPEND 是非空字符串", isinstance(APP, str) and len(APP) > 500,
      "len=%s" % (len(APP) if isinstance(APP, str) else type(APP)))

print()
print("=" * 64)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 64)
sys.exit(1 if FAIL else 0)
