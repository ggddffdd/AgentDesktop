# -*- coding: utf-8 -*-
"""参数校验补全（审查报告 P2，v4.223）验收判据。

v4.220 只覆盖 type/required/enum/min/max 五个维度，且只接了 3 处工具；
报告点名缺失的四项本轮补齐：

  PV1 路径标准化（type='path'）：展开 ~ / 环境变量、折叠 . 与 ..；
      相对路径保持相对（不篡改各工具不同的落点基准）。
  PV2 路径越界围栏（base_dir）：越界即拒。
  PV3 长度上限（max_len）：超限即拒。
  PV4 未知字段策略（unknown=ignore/strip/reject）。
  PV5 超时限制（normalize_timeout + schema max）：非法回落默认、超限封顶。
  PV6 工具级 schema 注册表：登记工具被校验，未登记工具原样放行（零破坏）。
  PV7 exec_tool 统一接入：非法参数在副作用之前被拦下。

每条都是「改坏必红」：扰动脚本删掉对应维度后，对应判据必须翻红。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tool_contract as tc  # noqa: E402
import tools  # noqa: E402
from permissions import Decision  # noqa: E402

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


def test_path_normalize_and_fence():
    print("== PV1/PV2 路径标准化 + 越界围栏 ==")
    ok, err = tc._validate_args({"p": "~/x/../y.txt"},
                                [{"key": "p", "type": "path"}])
    check("PV1 路径被标准化（~ 展开且 .. 折叠）",
          ok is not None and ok["p"] == os.path.join(os.path.expanduser("~"), "y.txt"),
          "got=%s err=%s" % (ok, err))
    ok2, _ = tc._validate_args({"p": "a/./b"}, [{"key": "p", "type": "path"}])
    check("PV1 相对路径保持相对（不被 abspath 篡改落点基准）",
          ok2 is not None and ok2["p"] == os.path.normpath("a/b"), "got=%s" % ok2)

    ok3, err3 = tc._validate_args(
        {"p": "D:/outside/x.txt"},
        [{"key": "p", "type": "path", "base_dir": "C:/inside"}])
    check("PV2 越界路径被拒", ok3 is None and "越界" in (err3 or ""),
          "ok=%s err=%s" % (ok3, err3))
    ok4, _ = tc._validate_args(
        {"p": "C:/inside/sub/x.txt"},
        [{"key": "p", "type": "path", "base_dir": "C:/inside"}])
    check("PV2 界内路径放行", ok4 is not None, "ok=%s" % ok4)


def test_max_len():
    print("== PV3 长度上限 ==")
    ok, err = tc._validate_args({"s": "x" * 10},
                                [{"key": "s", "type": "str", "max_len": 5}])
    check("PV3 超长参数被拒", ok is None and "超过上限" in (err or ""),
          "ok=%s err=%s" % (ok, err))
    ok2, _ = tc._validate_args({"s": "x" * 5},
                               [{"key": "s", "type": "str", "max_len": 5}])
    check("PV3 等长参数放行", ok2 is not None, "ok=%s" % ok2)


def test_unknown_policy():
    print("== PV4 未知字段策略（ignore / strip / reject）==")
    schema = [{"key": "a", "type": "int"}]
    ok_ig, _ = tc._validate_args({"a": 1, "zz": 2}, schema, unknown="ignore")
    check("PV4 ignore 保留未知字段（默认零破坏）",
          ok_ig is not None and "zz" in ok_ig, "got=%s" % ok_ig)
    ok_st, _ = tc._validate_args({"a": 1, "zz": 2}, schema, unknown="strip")
    check("PV4 strip 丢弃未知字段", ok_st is not None and "zz" not in ok_st,
          "got=%s" % ok_st)
    ok_rj, err_rj = tc._validate_args({"a": 1, "zz": 2}, schema, unknown="reject")
    check("PV4 reject 拒绝未知字段",
          ok_rj is None and "未知参数" in (err_rj or ""),
          "ok=%s err=%s" % (ok_rj, err_rj))


def test_timeout_cap():
    print("== PV5 超时限制 ==")
    check("PV5 非法值回落默认", tc.normalize_timeout("abc") == tc.TOOL_TIMEOUT_DEFAULT,
          "got=%s" % tc.normalize_timeout("abc"))
    check("PV5 非正值回落默认", tc.normalize_timeout(-5) == tc.TOOL_TIMEOUT_DEFAULT,
          "got=%s" % tc.normalize_timeout(-5))
    check("PV5 超限封顶到 cap", tc.normalize_timeout(99999) == tc.TOOL_TIMEOUT_CAP,
          "got=%s" % tc.normalize_timeout(99999))
    check("PV5 合法值原样保留", tc.normalize_timeout(45) == 45,
          "got=%s" % tc.normalize_timeout(45))


def test_schema_registry():
    print("== PV6 工具级 schema 注册表 ==")
    names = tc.registered_tool_schemas()
    check("PV6 高危工具已登记 schema（至少 7 个）", len(names) >= 7,
          "got=%s" % sorted(names))
    ok, err = tc.validate_for_tool("run_command", {"command": "echo", "timeout": 99999})
    check("PV6 登记工具按 schema 校验（超时超限被拒）",
          ok is None and "timeout" in (err or ""), "ok=%s err=%s" % (ok, err))
    ok2, err2 = tc.validate_for_tool("__no_such_tool__", {"zz": 1})
    check("PV6 未登记工具原样放行（零破坏）",
          err2 is None and isinstance(ok2, dict) and ok2.get("zz") == 1,
          "ok=%s err=%s" % (ok2, err2))


def test_exec_tool_wired():
    print("== PV7 exec_tool 统一接入（副作用前拦截）==")

    def _h(cfg, app_dir, args, **kw):
        return ("不该被执行到", [], None)

    prev = tools.TOOL_REGISTRY.get("run_command")
    try:
        tools.TOOL_REGISTRY["run_command"] = {"handler": _h}
        r = tools.exec_tool(None, None, "run_command",
                            {"command": "echo", "timeout": 99999},
                            perm_ctx=_ALLOW)
        check("PV7 非法参数被拦（handler 未执行）",
              isinstance(r, tc.ToolResult) and r.ok is False
              and "参数校验未通过" in (r.msg or ""), "msg=%s" % getattr(r, "msg", "?"))
        r2 = tools.exec_tool(None, None, "run_command", {"command": "echo"},
                             perm_ctx=_ALLOW)
        check("PV7 合法参数放行（handler 正常执行）",
              isinstance(r2, tc.ToolResult) and (r2.msg or "") == "不该被执行到",
              "msg=%s" % getattr(r2, "msg", "?"))
    finally:
        if prev is None:
            tools.TOOL_REGISTRY.pop("run_command", None)
        else:
            tools.TOOL_REGISTRY["run_command"] = prev


def main():
    test_path_normalize_and_fence()
    test_max_len()
    test_unknown_policy()
    test_timeout_cap()
    test_schema_registry()
    test_exec_tool_wired()
    print("\nPARAM_VALIDATION_223 PASS=%d FAIL=%d" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
