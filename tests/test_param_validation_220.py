# -*- coding: utf-8 -*-
"""参数校验（审查报告第二优先级，v4.220）验收判据。

验证点：
  PV1 缺必填参数时，工具返回结构化失败（ToolResult.ok=False），绝不抛 KeyError 崩。
  PV2 非法类型（如 force 传非布尔）被拒。
  PV3 合法参数通过校验（结果不再含「缺少必填参数」/「必须是」）。
  PV4 app_kill 缺 target 同样返回结构化失败（原代码直接 args["target"] 缺参会崩）。

PV1/PV4 是「改坏必红」核心：扰动脚本把 _validate_args 校验去掉后，缺参将不再
被优雅拒绝（抛异常或 KeyError），这几条必须翻红。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from system_control_tools import tool_process_kill  # noqa: E402
from software_control_tools import tool_app_kill  # noqa: E402
import tool_contract as tc  # noqa: E402

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


def test_missing_param_graceful():
    print("== PV1 缺必填参数返回结构化失败（不崩） ==")
    try:
        r = tool_process_kill(None, None, {})
    except Exception as e:
        check("缺name时process_kill优雅失败(不抛异常)", False, "抛异常: %r" % e)
        return
    check("缺name时process_kill返回ToolResult", isinstance(r, tc.ToolResult), "type=%s" % type(r))
    check("缺name时process_kill.ok=False", (isinstance(r, tc.ToolResult) and r.ok is False),
          "ok=%s" % getattr(r, "ok", "?"))
    check("缺name时报『缺少必填参数』", (isinstance(r, tc.ToolResult) and "缺少必填参数" in r.msg),
          "msg=%r" % getattr(r, "msg", ""))


def test_invalid_type_rejected():
    print("== PV2 非法类型被拒 ==")
    r = tool_process_kill(None, None, {"name": "notepad", "force": "不是布尔"})
    check("非法force类型被拒(ok=False)", isinstance(r, tc.ToolResult) and r.ok is False, "r=%r" % r)


def test_valid_passes_validation():
    print("== PV3 合法参数通过校验 ==")
    r = tool_process_kill(None, None, {"name": "notepad", "force": True})
    check("合法参数返回ToolResult", isinstance(r, tc.ToolResult), "type=%s" % type(r))
    if isinstance(r, tc.ToolResult):
        check("合法参数不再报『缺少必填参数』", "缺少必填参数" not in r.msg, "msg=%r" % r.msg)
        check("合法参数不再报『必须是』", "必须是" not in r.msg, "msg=%r" % r.msg)
        check("合法参数填充data.name", r.data.get("name") == "notepad", "data=%r" % r.data)


def test_app_kill_missing_target():
    print("== PV4 app_kill 缺 target 优雅失败（原 args['target'] 会 KeyError） ==")
    try:
        r = tool_app_kill(None, None, {})
    except Exception as e:
        check("缺target时app_kill优雅失败(不抛异常)", False, "抛异常(KeyError?): %r" % e)
        return
    check("缺target时app_kill返回ToolResult", isinstance(r, tc.ToolResult), "type=%s" % type(r))
    check("缺target时app_kill.ok=False且报缺参",
          (isinstance(r, tc.ToolResult) and r.ok is False and "缺少必填参数" in r.msg),
          "msg=%r" % getattr(r, "msg", ""))


def main():
    test_missing_param_graceful()
    test_invalid_type_rejected()
    test_valid_passes_validation()
    test_app_kill_missing_target()
    print("\n==== 参数校验结果：PASS=%d  FAIL=%d ====" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
