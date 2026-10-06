# -*- coding: utf-8 -*-
"""v4.218.0 判据：拆分 app_window_state.close 为独立 app_close（审查报告 P1-1）。

根因：app_window_state 标 READ（auto 免确认）却含 close 动作会关窗口、丢未保存内容。
修复：close 拆成独立 tool_app_close，归 EXEC + manual 档（需确认；结构等价于强制确认、
但不进 ALWAYS_CONFIRM，从而不破坏「ALWAYS_CONFIRM 集合须与 v4.170.0 一致」的硬闸）。

契约：
  ① app_window_state schema 的 enum 不再含 'close'
  ② app_close 在 schema 声明（SOFTWARE_CONTROL_TOOL_DEFS）
  ③ app_close 在路由表（SOFTWARE_CONTROL_TOOL_TABLE）
  ④ app_close 风险归 EXEC 且落 manual 档（需确认，不进 ALWAYS_CONFIRM）
  ⑤ handler 可调用

零副作用：纯 import + 读 schema/表/风险表。
用法：python tests/test_app_close_218.py
"""
import os
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


import software_control_tools as sct  # noqa: E402
import risk  # noqa: E402
from risk import RiskClass, tier_of  # noqa: E402


def test_app_window_state_no_close():
    aw = [d for d in sct.SOFTWARE_CONTROL_TOOL_DEFS
          if d["function"]["name"] == "app_window_state"][0]
    enum = aw["function"]["parameters"]["properties"]["action"]["enum"]
    check("app_window_state 不再含 close 动作", "close" not in enum, "enum=%r" % enum)


def test_app_close_registered():
    names = [d["function"]["name"] for d in sct.SOFTWARE_CONTROL_TOOL_DEFS]
    check("schema 声明含 app_close", "app_close" in names)
    check("路由表含 app_close", "app_close" in sct.SOFTWARE_CONTROL_TOOL_TABLE)
    check("handler 可调用", callable(sct.SOFTWARE_CONTROL_TOOL_TABLE.get("app_close")))


def test_app_close_risk():
    check("app_close 风险归 EXEC",
          risk.classify("app_close") == RiskClass.EXEC,
          "classify=%r" % risk.classify("app_close"))
    check("app_close 强制确认（manual 档，需确认，不进 ALWAYS_CONFIRM）",
          risk.tier_of("app_close") == "manual",
          "tier_of=%r" % risk.tier_of("app_close"))


if __name__ == "__main__":
    test_app_window_state_no_close()
    test_app_close_registered()
    test_app_close_risk()
    print(f"\nPASS={_p} FAIL={_f}")
    sys.exit(1 if _f else 0)
