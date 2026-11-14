# -*- coding: utf-8 -*-
"""v4.302.0 重复工具调用修复的扰动：真实改坏源码，目标判据必须翻红。

七组扰动分别对应四个根因与三处源码接线；每组都只改一行/一段，
改完立刻跑 `tests/test_no_progress_302.py`，命中期望的 FAIL 才算通过。
源码文件均为 CRLF，跨行锚点必须写真实 \\r\\n。
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard  # noqa: E402

SUITE = os.path.join(ROOT, "tests", "test_no_progress_302.py")
CASES = [
    ("ui_routing.py", "本轮工具回执计数失效（required 又每步都带）",
     "            n += 1",
     "            n += 0  # 扰动：永远当作没调过工具", "A3"),
    ("ui.py", "required 分支退回旧变量（模型又没文本出口）",
     "elif (force_required or redo or _tool_intent_required) and not self._is_reasoning_model(_model, _base_url):",
     "elif (force_required or redo or _tool_intent) and not self._is_reasoning_model(_model, _base_url):",
     "B3"),
    ("system_control_tools.py", "回收站计数又全算（清空后仍报还有项）",
     '                if f.lower() != "desktop.ini" and not f.upper().startswith("$I")',
     "                if True  # 扰动：又全算", "C5"),
    ("agent_text.py", "结果无进展纯判据失效",
     "    return len(items) >= window and len(set(items)) == 1",
     "    return False  # 扰动：无进展判据失效", "D5"),
    ("agent.py", "护栏接线断开（判据还在但不被调用）",
     "        if self._guard_no_progress(tool_calls, _IDEMPOTENT_READ):",
     "        if False:  # 扰动：护栏接线断开", "E6"),
    ("agent.py", "核验通过空转签名接线断开",
     '                _np_sig = ("" if _v_sig else agent_text.verified_no_progress_sig(\r\n'
     '                    self.messages, bool(getattr(_lp_stall, "verified", False))))',
     '                _np_sig = ""  # 扰动：核验通过空转签名丢失\r\n'
     '                _unused = (self.messages, bool(getattr(_lp_stall, "verified", False)))',
     "F8"),
    ("agent.py", "核验通过空转又用旧阈值早停",
     "                _stall_limit = (agent_text.MAX_NO_PROGRESS_STEPS if _np_sig",
     "                _stall_limit = (MAX_VERIFY_STALL_STEPS if _np_sig", "F6"),
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
        print(baseline[-1200:])
        print("PERTURB PASS=0 FAIL=%d" % len(CASES))
        return 1
    npass = nfail = 0
    for rel, label, old, new, want in CASES:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
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
        if io.open(path, encoding="utf-8", newline="").read() != original:
            print("FAIL 源码未恢复：%s" % os.path.basename(path))
            nfail += 1
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
