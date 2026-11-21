# -*- coding: utf-8 -*-
"""v4.321.0 run_python outcome 结构性信号的真实源码扰动验证。

把修复改坏，对应判据必须翻红：
  P1 handler 退回裸 3-tuple（不经 ToolResult）  → A5/B6 红
  P2 成功信号写死 True（忽略 returncode）        → A2/B3 红
  P3 _ret 忽略 with_ok（永远 2-tuple）           → A1/B2 红
  P4 handler 不传 with_ok=True                   → B5 红

tools.py 换行符是 CRLF。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _perturb_guard

SUITE = os.path.join(ROOT, "tests", "test_run_python_outcome_321.py")
CRLF = "\r\n"

CASES = [
    ("tools.py", "handler 退回裸 3tuple",
     ['    return ToolResult(ok=bool(_ok), msg=r, deliverables=list(d or []),',
      '                      verified=True, error_code=None if _ok else "RUN_FAILED")'],
     "    return (r, d, None)  # 扰动：退回裸 tuple，重进 _infer_ok", "A5", CRLF),
    ("tools.py", "成功信号写死 True",
     ['    _ok = (_rc == 0)'],
     "    _ok = True  # 扰动：忽略退出码", "A2", CRLF),
    ("tools.py", "_ret 忽略 with_ok",
     ['        return (msg, dels, ok) if with_ok else (msg, dels)'],
     "        return (msg, dels)  # 扰动：永远 2tuple", "A1", CRLF),
    ("tools.py", "handler 不传 with_ok",
     ['                                should_stop=should_stop, with_ok=True)'],
     "                                should_stop=should_stop)  # 扰动：不传 with_ok", "B5", CRLF),
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
