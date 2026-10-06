# -*- coding: utf-8 -*-
"""影响范围展示（审查报告 P1 #5，v4.220）验收判据。

验证点：
  IS1 注册了预检的高危工具（process_kill）确认文案含「影响范围」与「预计影响」。
  IS2 未登记 scope 的工具（write_file）确认文案不含「影响范围」段。
  IS3 compute_impact_scope 对未登记工具返回 None。

IS1 是「改坏必红」核心：扰动脚本删掉 _build_confirm_detail 里的 scope 注入后，
process_kill 的确认文案将不再含「影响范围」，这条必翻红。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool_contract import compute_impact_scope  # noqa: E402
import agent  # noqa: E402
from agent import AgentWorker  # noqa: E402

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


def test_scope_in_confirm():
    print("== IS1 高危工具确认文案含影响范围 ==")
    title, detail = AgentWorker._build_confirm_detail("process_kill", {"name": "notepad"})
    check("process_kill确认文案含『影响范围』", "影响范围" in detail, "detail=%r" % detail[:200])
    check("process_kill确认文案含『预计影响』", "预计影响" in detail, "detail=%r" % detail[:200])


def test_no_scope_for_unregistered():
    print("== IS2 未登记工具不含影响范围段 ==")
    title, detail = AgentWorker._build_confirm_detail("write_file", {"path": "x", "content": "y"})
    check("write_file确认文案不含『影响范围』", "影响范围" not in detail, "detail=%r" % detail[:200])


def test_compute_scope_none_for_unregistered():
    print("== IS3 未登记工具compute_impact_scope返回None ==")
    check("compute_impact_scope('write_file')=None",
          compute_impact_scope("write_file", {}) is None,
          "got=%r" % compute_impact_scope("write_file", {}))


def main():
    test_scope_in_confirm()
    test_no_scope_for_unregistered()
    test_compute_scope_none_for_unregistered()
    print("\n==== 影响范围展示结果：PASS=%d  FAIL=%d ====" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
