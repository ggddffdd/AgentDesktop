# -*- coding: utf-8 -*-
"""v4.318.0 纯查询 outcome 契约的真实源码扰动验证。

把修复改坏，对应判据必须翻红：
  P1 删 sys_info 登记              → A1/A5 红
  P2 删 log_query 登记             → A2/A6 红
  P3 契约复活中文子串匹配          → B3/A1 红（含失败字样的正文被误判）
  P4 契约不验非空 msg（空串也 True）→ A3/B4 红

tool_contract.py 换行符是 LF。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _perturb_guard

SUITE = os.path.join(ROOT, "tests", "test_query_outcome_318.py")
LF = "\n"

# (相对路径, 标签, 原行列表, 替换文本, want判据, 换行)
CASES = [
    ("tool_contract.py", "删 sys_info 登记",
     ['register_outcome("sys_info", _outcome_readonly_query)'],
     "# 扰动：删 sys_info 登记", "A1", LF),
    ("tool_contract.py", "删 log_query 登记",
     ['register_outcome("log_query", _outcome_readonly_query)'],
     "# 扰动：删 log_query 登记", "A2", LF),
    ("tool_contract.py", "契约复活中文子串匹配",
     ["    return True if isinstance(msg, str) and msg.strip() else None"],
     '    return ("失败" not in msg) if isinstance(msg, str) else None  # 扰动：反模式复活', "B3", LF),
    ("tool_contract.py", "契约不验非空 msg",
     ["    return True if isinstance(msg, str) and msg.strip() else None"],
     "    return True if isinstance(msg, str) else None  # 扰动：空串也 True", "A3", LF),
]


def run_suite():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, SUITE], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def main():
    files = sorted({os.path.join(ROOT, rel) for rel, *_ in CASES})
    _perturb_guard.arm(files)
    originals = {p: io.open(p, encoding="utf-8", newline="").read() for p in files}
    baseline = run_suite()
    if "FAIL=0" not in baseline:
        print("BASELINE RED：套件本身未全绿")
        print(baseline[-1500:])
        print("PERTURB PASS=0 FAIL=%d" % len(CASES))
        return 1
    npass = nfail = 0
    for rel, label, old_lines, new, want, nl in CASES:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
        old = nl.join(old_lines)
        if src.count(old) != 1:
            print("MISS %s：锚点数量=%d" % (label, src.count(old)))
            nfail += 1
            continue
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
            out = run_suite()
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(originals[path])
        red = [line for line in out.splitlines() if "[FAIL]" in line]
        if "PASS=" in out and any(want in line for line in red):
            print("PASS %s → %s" % (label, want))
            npass += 1
        else:
            print("MISS %s：期望 %s，实际 %s" % (label, want, red[:3] or out[-200:]))
            nfail += 1
    for path, original in originals.items():
        if io.open(path, "r", encoding="utf-8", newline="").read() != original:
            print("FAIL 源码未恢复：%s" % os.path.basename(path))
            nfail += 1
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
