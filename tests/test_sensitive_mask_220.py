# -*- coding: utf-8 -*-
"""输出脱敏（审查报告第二优先级，v4.220）验收判据。

验证点：
  MK1 本地绝对路径脱敏为 <路径已脱敏>。
  MK2 IPv4 脱敏为 <IP>。
  MK3 当前用户名脱敏为 <用户>。
  MK4 exec_tool 对工具返回的 msg 做脱敏（集成）。

MK4 是「改坏必红」核心：扰动脚本删掉 exec_tool 里的 `_tr.msg = _mask_sensitive(...)`
后，带路径的工具返回 msg 将不再脱敏，这条必翻红。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import getpass
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


def test_path_masked():
    print("== MK1 路径脱敏 ==")
    s = tc._mask_sensitive(r"已保存至 C:\Users\xyb\Desktop\a.png")
    check("绝对路径脱敏", "<路径已脱敏>" in s, "s=%r" % s)
    check("脱敏后不含原始用户名路径", r"C:\Users\xyb" not in s, "s=%r" % s)


def test_ip_masked():
    print("== MK2 IP 脱敏 ==")
    s = tc._mask_sensitive("连接 192.168.1.50:8080 成功")
    check("IPv4 脱敏", "<IP>" in s, "s=%r" % s)
    check("脱敏后不含原始IP", "192.168.1.50" not in s, "s=%r" % s)


def test_username_masked():
    print("== MK3 用户名脱敏 ==")
    u = getpass.getuser()
    if not u:
        check("用户名脱敏(无用户名,跳过)", True)
        return
    s = tc._mask_sensitive("操作用户 " + u + " 完成")
    check("当前用户名脱敏", "<用户>" in s, "s=%r" % s)
    check("脱敏后不含原始用户名", u not in s, "s=%r" % s)


def test_exec_tool_masks_msg():
    print("== MK4 exec_tool 对返回msg脱敏（集成） ==")

    def _fake(cfg, app_dir, args, **kw):
        return tc.ToolResult.ok_result(r"路径 C:\Users\xyb\secret.txt 已生成")

    prev = tools.TOOL_REGISTRY.get("__mask_test")
    tools.TOOL_REGISTRY["__mask_test"] = {"handler": _fake}
    try:
        r = tools.exec_tool(None, None, "__mask_test", {}, should_stop=lambda: False, perm_ctx=_ALLOW)
        check("exec_tool返回ToolResult", isinstance(r, tc.ToolResult), "type=%s" % type(r))
        check("exec_tool对msg脱敏", (isinstance(r, tc.ToolResult)
                                     and "<路径已脱敏>" in r.msg), "msg=%r" % getattr(r, "msg", ""))
        check("脱敏后msg不含原始路径", (isinstance(r, tc.ToolResult)
                                       and r"C:\Users\xyb" not in r.msg),
              "msg=%r" % getattr(r, "msg", ""))
    finally:
        if prev is None:
            tools.TOOL_REGISTRY.pop("__mask_test", None)
        else:
            tools.TOOL_REGISTRY["__mask_test"] = prev


def main():
    test_path_masked()
    test_ip_masked()
    test_username_masked()
    test_exec_tool_masks_msg()
    print("\n==== 输出脱敏结果：PASS=%d  FAIL=%d ====" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
