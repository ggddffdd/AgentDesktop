# -*- coding: utf-8 -*-
"""v4.232 断点 D：续跑跳过缺失产物（write_file 账本记 done 但文件被删）。

审查 4 断点之 D：_is_resume_dup 只比对 _resume_done_hashes（已执行哈希集），
从不核验产物是否仍在磁盘。于是 write_file 被记入「done」后若文件被删/未落盘，
续跑会把它当成「已完成」跳过 → 任务带着缺失产物继续跑（下游静默出错）。

修复方向（judge-first，先红后绿）：在 hash 命中基础上，对 write_file 额外校验
产物真实存在（按 tool_write_file 同规则绝对化路径）；缺失则视为未 dup，必须重做。

判据：
  D1 write_file 产物存在 + 哈希命中 → 仍判 dup（回归：不能误放）
  D2 write_file 哈希命中但产物已删 → 判非 dup（D 断点核心，修复前应为 FAIL=红）
  D3 不同参数 → 不命中（哈希天然不命中）
  D4 非文件类工具（image_gen）哈希命中 → 仍 dup（路径不在参数，走哈希回退）
  D5 write_file 无 path 参数 → 哈希命中仍 dup（路径不可解析时回退哈希）
"""
import os
import sys
import json
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from config import WORKSPACE_DIR  # noqa: E402
from agent import AgentWorker, _tool_args_hash, _NON_IDEMPOTENT_TOOLS  # noqa: E402

_p = _f = 0
_tmp_dirs = []
_artifacts = []  # 真实落在 WORKSPACE_DIR 的文件，finally 清理


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


def _new_worker():
    w = AgentWorker.__new__(AgentWorker)
    w._exec_ledger = []
    w._resume_done_hashes = set()
    return w


def _write_file_args(relpath, content="hello"):
    return json.dumps({"path": relpath, "content": content})


def main():
    print("-- 断点 D：续跑跳过缺失产物（write_file 产物存在性校验） --")
    w = _new_worker()

    # 在 WORKSPACE_DIR 下建真实临时文件，路径按 tool_write_file 同规则绝对化。
    _tmp = tempfile.mkdtemp(prefix="d232_")
    _tmp_dirs.append(_tmp)
    _rel1 = "_d232_artifact_exists.txt"
    _rel2 = "_d232_artifact_deleted.txt"
    _abs1 = os.path.abspath(os.path.join(WORKSPACE_DIR, _rel1))
    _abs2 = os.path.abspath(os.path.join(WORKSPACE_DIR, _rel2))
    _artifacts.extend([_abs1, _abs2])
    with open(_abs1, "w", encoding="utf-8") as f:
        f.write("exists")
    with open(_abs2, "w", encoding="utf-8") as f:
        f.write("deleted")

    # D1：产物存在 + 哈希命中 → 仍判 dup
    a1 = _write_file_args(_rel1)
    h1 = _tool_args_hash("write_file", a1)
    w._resume_done_hashes = {h1}
    r_d1 = w._is_resume_dup("write_file", a1)
    check("D1 write_file 产物存在+哈希命中 → 仍判 dup", r_d1 is True, "got=%r" % r_d1)

    # D2：哈希命中但产物已删 → 判非 dup（核心 D 断点，修复前应为 FAIL=红）
    a2 = _write_file_args(_rel2)
    h2 = _tool_args_hash("write_file", a2)
    os.remove(_abs2)  # 模拟「账本记 done 但文件被删」
    w._resume_done_hashes = {h2}
    r_d2 = w._is_resume_dup("write_file", a2)
    check("D2 write_file 哈希命中但产物已删 → 判非 dup（重做）",
          r_d2 is False, "got=%r (修复前误判为 dup)" % r_d2)

    # D3：不同参数 → 哈希不命中 → 不 dup
    w._resume_done_hashes = {h1}
    r_d3 = w._is_resume_dup("write_file", _write_file_args("other.txt"))
    check("D3 不同参数 → 不命中", r_d3 is False, "got=%r" % r_d3)

    # D4：非文件类工具（image_gen）哈希命中 → 仍 dup（路径不在参数，哈希回退）
    ai = '{"prompt":"a cat"}'
    hi = _tool_args_hash("image_gen", ai)
    w._resume_done_hashes = {hi}
    # 注意：image_gen 不应因「无文件」而误判非 dup
    r_d4 = w._is_resume_dup("image_gen", ai)
    check("D4 非文件类工具哈希命中 → 仍 dup", r_d4 is True, "got=%r" % r_d4)

    # D5：write_file 无 path → 哈希命中仍 dup（路径不可解析回退哈希）
    a5 = json.dumps({"content": "no path here"})
    h5 = _tool_args_hash("write_file", a5)
    w._resume_done_hashes = {h5}
    r_d5 = w._is_resume_dup("write_file", a5)
    check("D5 write_file 无 path → 哈希命中仍 dup", r_d5 is True, "got=%r" % r_d5)

    # 辅助：名单正确
    check("辅助 _NON_IDEMPOTENT_TOOLS 含 write_file",
          "write_file" in _NON_IDEMPOTENT_TOOLS)
    check("辅助 _NON_IDEMPOTENT_TOOLS 含 image_gen",
          "image_gen" in _NON_IDEMPOTENT_TOOLS)

    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    try:
        _rc = main()
    finally:
        for d in _tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        # 清理落在 WORKSPACE_DIR 的真实文件
        for ap in _artifacts:
            try:
                if os.path.exists(ap):
                    os.remove(ap)
            except Exception:
                pass
    sys.exit(_rc)
