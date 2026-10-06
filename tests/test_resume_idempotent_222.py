# -*- coding: utf-8 -*-
"""v4.222 P2 断点幂等：执行 ledger 登记 + 恢复查重。

审查报告 P2 指出：崩溃/恢复后可能重复执行不可幂等工具（重复写文件/发请求/生成图）。
修复：每次工具执行登记 _exec_ledger；恢复时从 checkpoint 取历史 ledger 构建
_resume_done_hashes，_exec_tool_calls 对不可幂等工具已执行过的跳过。

判据：
  A 执行 ledger 登记成功（含 args_hash/status）
  B 恢复查重：不可幂等同参数命中 / 不同参数不命中 / 可幂等工具不查重
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent import AgentWorker, _tool_args_hash, _NON_IDEMPOTENT_TOOLS  # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


def main():
    print("-- P2 断点幂等（执行 ledger + 恢复查重） --")
    w = AgentWorker.__new__(AgentWorker)

    # A：登记执行 ledger
    w._exec_ledger = []
    h = _tool_args_hash("write_file", '{"path":"x"}')
    w._record_exec_ledger("write_file", '{"path":"x"}', True)
    _ok_a = (len(w._exec_ledger) == 1
              and w._exec_ledger[0]["name"] == "write_file"
              and w._exec_ledger[0]["args_hash"] == h
              and w._exec_ledger[0]["side_effect_status"] == "done")
    check("A 执行 ledger 登记成功（含 args_hash/status）", _ok_a)

    # B：恢复查重
    w._resume_done_hashes = {h}
    dup_same = w._is_resume_dup("write_file", '{"path":"x"}')
    dup_diff = w._is_resume_dup("write_file", '{"path":"y"}')
    dup_idem = w._is_resume_dup("read_file", '{"path":"x"}')
    check("B1 不可幂等工具已执行 → 恢复查重命中", dup_same is True, "got=%r" % dup_same)
    check("B2 不同参数 → 不命中", dup_diff is False, "got=%r" % dup_diff)
    check("B3 可幂等工具 → 不查重", dup_idem is False, "got=%r" % dup_idem)

    # 辅助：名单正确
    check("辅助 _NON_IDEMPOTENT_TOOLS 含 write_file", "write_file" in _NON_IDEMPOTENT_TOOLS)
    check("辅助 _NON_IDEMPOTENT_TOOLS 不含 read_file", "read_file" not in _NON_IDEMPOTENT_TOOLS)

    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
