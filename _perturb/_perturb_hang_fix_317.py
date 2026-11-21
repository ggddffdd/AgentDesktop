# -*- coding: utf-8 -*-
"""v4.317.0 自动化双止损的真实源码扰动验证。

把三处防线各自改坏，对应判据必须翻红：
  P1 verify_fail_streak 不归零（verified 达标也累加） → A3 红
  P2 agent_loop_mixin 早停不用新计数器（删新判据）   → B2 红
  P3 should_speak_on_finish 自动化仍播报              → A7 红
  P4 ui.py 收尾删掉守门调用                           → B4/B5 红
  P5 trim_auto_session 连普通会话也裁                 → A17 红
  P6 session.py save() 删掉裁剪调用                   → B6/B7 红

换行符：session.py 是 LF，其余（agent_text/agent/automation/ui）是 CRLF。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _perturb_guard

SUITE = os.path.join(ROOT, "tests", "test_hang_fix_317.py")
LF = "\n"
CRLF = "\r\n"

# (相对路径, 标签, 原行列表, 替换文本, want判据, 换行)
CASES = [
    ("agent_text.py", "verify_fail_streak 达标不归零",
     ["    if verified or not has_report:",
      "        return 0"],
     "    if not has_report:  # 扰动：verified 达标也不归零\n        return 0", "A3", CRLF),
    ("agent_loop_mixin.py", "早停不用新计数器",
     ["        if stall_steps < stall_limit and not _fail_exceeded:"],
     "        if stall_steps < stall_limit:  # 扰动：删新判据", "B2", CRLF),
    ("automation.py", "自动化收尾仍播报",
     ["    if is_automation:",
      "        return False",
      "    return bool(speech_enabled) and not bool(degraded)"],
     "    if False:  # 扰动：自动化也播报\n        return False\n    return bool(speech_enabled) and not bool(degraded)", "A7", CRLF),
    ("ui.py", "收尾删掉播报守门",
     ["        if not automation.should_speak_on_finish("],
     "        if False and not automation.should_speak_on_finish(  # 扰动：守门失效", "B4", CRLF),
    ("session.py", "裁普通会话也裁",
     ["    if not str(sid or \"\").startswith(prefix):",
      "        return messages, 0"],
     "    if False:  # 扰动：普通会话也裁\n        return messages, 0", "A17", LF),
    ("session.py", "save 删掉裁剪调用",
     ["            _msgs, _removed = trim_auto_session(_sid, _s.messages)"],
     "            _msgs, _removed = _s.messages, 0  # 扰动：不裁剪", "B6", LF),
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
