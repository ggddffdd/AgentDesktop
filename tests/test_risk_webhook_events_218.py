# -*- coding: utf-8 -*-
"""v4.218.0 判据：清理 webhook_events 风险表重复键（审查报告 P1-4 修正）。

根因：risk.py 中 webhook_events 被定义了两次——READ（:100）与 EXTERNAL（:145），
经 `git show v4.170.0:risk.py` 确认该重复键自 v4.170.0 起就在，
Python dict 后者覆盖→**历史有效值历来是 EXTERNAL**（审查报告误判为「新引入的拦截 bug」）。
v4.218 修复为：删除多余的 READ 重复键、保留唯一 EXTERNAL 条目，
既消除重复键隐患、又做到「与 v4.170.0 行为零变化」（满足 test_risk_policy_single_table 硬闸）。

契约：
  ① webhook_events 风险归 EXTERNAL（与 v4.170.0 一致，行为零变化）
  ② RISK_MAP 中 webhook_events 唯一（无重复键）—— 重复键会静默覆盖
  ③ webhook_start / webhook_stop 仍归 EXTERNAL（对外暴露端口，保持外部操作）

零副作用：纯读 risk.py 源码 + import risk 模块，不改动任何文件。
用法：python tests/test_risk_webhook_events_218.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}  {detail}")


import risk  # noqa: E402
from risk import RiskClass  # noqa: E402


def test_webhook_events_read():
    check("webhook_events 风险归 EXTERNAL（与 v4.170.0 一致）",
          risk.classify("webhook_events") == RiskClass.EXTERNAL,
          "classify=%r" % risk.classify("webhook_events"))
    # 重复键检测：源码里 "webhook_events": 作为键只应出现 1 次
    src = open(os.path.join(ROOT, "risk.py"), encoding="utf-8").read()
    keys = re.findall(r'"webhook_events"\s*:', src)
    check("webhook_events 在 RISK_MAP 中唯一（无重复键）",
          len(keys) == 1, "出现 %d 次" % len(keys))


def test_webhook_start_stop_external():
    check("webhook_start 仍归 EXTERNAL",
          risk.classify("webhook_start") == RiskClass.EXTERNAL)
    check("webhook_stop 仍归 EXTERNAL",
          risk.classify("webhook_stop") == RiskClass.EXTERNAL)


if __name__ == "__main__":
    test_webhook_events_read()
    test_webhook_start_stop_external()
    print(f"\nPASS={_p} FAIL={_f}")
    sys.exit(1 if _f else 0)
