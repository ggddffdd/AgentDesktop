# -*- coding: utf-8 -*-
"""结构化返回（审查报告第二优先级，v4.220 全面重构）验收判据。

验证点：
  SR1 exec_tool 始终返回 ToolResult（统一契约），不再裸三元组散落各处。
  SR2 仍返回 tuple 的工具（未迁原生 ToolResult）经 exec_tool 归一为 ToolResult。
  SR3 原生 ToolResult 工具（process_kill）正确填充 data 字段。

SR1/SR2 是「改坏必红」核心：扰动脚本让 exec_tool 跳过 ToolResult 归一
（直接 return 原始 _r）后，exec_tool 将返回 tuple 而非 ToolResult，这两条必翻红。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tool_contract as tc  # noqa: E402
import tools  # noqa: E402
from permissions import Decision  # noqa: E402
from system_control_tools import tool_process_kill  # noqa: E402

_ALLOW = Decision(allowed=True, needs_user=False, reason="test", rule="test")

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, detail))


def test_exec_tool_returns_toolresult():
    print("== SR1 exec_tool 始终返回 ToolResult ==")

    def _tup(cfg, app_dir, args, **kw):
        return ("临时结果", [], None)

    prev = tools.TOOL_REGISTRY.get("__sr_test")
    tools.TOOL_REGISTRY["__sr_test"] = {"handler": _tup}
    try:
        r = tools.exec_tool(None, None, "__sr_test", {}, should_stop=lambda: False, perm_ctx=_ALLOW)
        check("exec_tool返回ToolResult", isinstance(r, tc.ToolResult), "type=%s" % type(r))
        check("ToolResult.ok为布尔", isinstance(r, tc.ToolResult) and isinstance(r.ok, bool),
              "ok=%s" % getattr(r, "ok", "?"))
        check("ToolResult.data为dict", isinstance(r, tc.ToolResult) and isinstance(r.data, dict),
              "data=%s" % getattr(r, "data", "?"))
    finally:
        if prev is None:
            tools.TOOL_REGISTRY.pop("__sr_test", None)
        else:
            tools.TOOL_REGISTRY["__sr_test"] = prev


def test_tuple_normalized():
    print("== SR2 仍返回tuple的工具经exec_tool归一 ==")

    def _tup(cfg, app_dir, args, **kw):
        return ("原始三元组", [("a.txt", "file", "x")], None)

    prev = tools.TOOL_REGISTRY.get("__sr_test2")
    tools.TOOL_REGISTRY["__sr_test2"] = {"handler": _tup}
    try:
        r = tools.exec_tool(None, None, "__sr_test2", {}, should_stop=lambda: False, perm_ctx=_ALLOW)
        check("tuple工具归一为ToolResult", isinstance(r, tc.ToolResult), "type=%s" % type(r))
        check("归一后deliverables保留", (isinstance(r, tc.ToolResult)
                                         and r.deliverables == [("a.txt", "file", "x")]),
              "deliverables=%r" % getattr(r, "deliverables", None))
    finally:
        if prev is None:
            tools.TOOL_REGISTRY.pop("__sr_test2", None)
        else:
            tools.TOOL_REGISTRY["__sr_test2"] = prev


def test_native_tool_populates_data():
    print("== SR3 原生ToolResult工具填充data ==")
    # notepad 可能没在跑→ok=False，但 data 必含 name（结构性不依赖真实杀进程）
    r = tool_process_kill(None, None, {"name": "notepad", "force": True})
    check("process_kill返回ToolResult", isinstance(r, tc.ToolResult), "type=%s" % type(r))
    check("process_kill填充data.name", (isinstance(r, tc.ToolResult)
                                        and r.data.get("name") == "notepad"),
          "data=%r" % getattr(r, "data", None))


def main():
    test_exec_tool_returns_toolresult()
    test_tuple_normalized()
    test_native_tool_populates_data()
    print("\n==== 结构化返回结果：PASS=%d  FAIL=%d ====" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
