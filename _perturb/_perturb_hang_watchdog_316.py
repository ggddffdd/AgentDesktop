# -*- coding: utf-8 -*-
"""v4.316.0 卡死自证 watchdog 的真实源码扰动验证。

验证：把四道防线各自改坏，对应判据必须翻红；改不红（假绿）即 FAIL。
  P1 is_hung 用 > 而非 >=       → A4 红（边界卡死被放过）
  P2 dump 用 all_threads=False  → A10 红（抓不到主线程栈）
  P3 删 main.py 心跳接线        → B2 红（watchdog 永远误判卡死）
  P4 watchdog 线程 daemon=False → B7 红（退出被 watchdog 阻塞）

换行符：hang_watchdog.py 是 LF；main.py 是 CRLF（逐文件按实际 nl 拼接）。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _perturb_guard

SUITE = os.path.join(ROOT, "tests", "test_hang_watchdog_316.py")
LF = "\n"
CRLF = "\r\n"

# (相对路径, 标签, 原行列表, 替换文本, want判据, 换行)
CASES = [
    ("hang_watchdog.py", "is_hung 边界改 >",
     ["    return (now - beat_ts) >= threshold"],
     "    return (now - beat_ts) > threshold  # 扰动：边界不判死", "A4", LF),
    ("hang_watchdog.py", "dump 不含主线程栈",
     ["        faulthandler.dump_traceback(file=_file, all_threads=True)"],
     "        faulthandler.dump_traceback(file=_file, all_threads=False)  # 扰动", "A10", LF),
    ("main.py", "删掉 QTimer 心跳接线",
     ["        _hb_timer.timeout.connect(hang_watchdog.beat)"],
     "        # 扰动：心跳接线被删，watchdog 将永远误判卡死", "B2", CRLF),
    ("hang_watchdog.py", "watchdog 线程非 daemon",
     ['    _thread = threading.Thread(target=_watchdog_loop, name="hang-watchdog", daemon=True)'],
     '    _thread = threading.Thread(target=_watchdog_loop, name="hang-watchdog", daemon=False)  # 扰动',
     "B7", LF),
    ("hang_watchdog.py", "faulthandler 不写独立日志",
     ["        faulthandler.enable(file=_file)"],
     "        faulthandler.enable()  # 扰动：输出丢到 stderr", "B4", LF),
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
