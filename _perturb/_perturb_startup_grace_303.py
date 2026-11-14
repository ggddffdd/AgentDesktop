# -*- coding: utf-8 -*-
"""v4.303.0 启动卡死修复的扰动：真实改坏源码，目标判据必须翻红。

九组扰动分别覆盖三个纯函数、渲染上限与五处 ui.py 接线；每组只改一行/一段，
改完立刻跑 `tests/test_startup_grace_303.py`，命中期望的 FAIL 才算通过。
automation.py / ui.py 均为 CRLF，跨行锚点必须写真实 \\r\\n。
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard  # noqa: E402

SUITE = os.path.join(ROOT, "tests", "test_startup_grace_303.py")
CR = "\r\n"
CASES = [
    ("automation.py", "启动宽限常量归零（一打开又立刻补跑）",
     "STARTUP_GRACE_SEC = 90.0",
     "STARTUP_GRACE_SEC = 0.0  # 扰动：宽限归零", "A2"),

    ("automation.py", "宽限纯函数失效（永远放行）",
     "    return elapsed < float(grace_sec)",
     "    return False  # 扰动：宽限判据失效", "A3"),

    ("automation.py", "active 归一化失效（又停在巨型自动化会话）",
     "    cand.sort(reverse=True)" + CR + "    return cand[0][1]",
     "    cand.sort(reverse=True)" + CR + "    return None  # 扰动：不切回普通会话",
     "B1"),

    ("automation.py", "自动化会话渲染上限失效（又渲染 300 条）",
     "        return min(int(normal_limit), MAX_RENDERED_AUTOMATION_MSGS)",
     "        return int(normal_limit)  # 扰动：上限失效", "C1"),

    ("ui.py", "tick 宽限接线断开（真实启动就会触发执行任务）",
     "            _grace = automation.startup_grace_blocks(" + CR
     + "                getattr(self, \"_automation_grace_started\", None)," + CR
     + "                time.monotonic())",
     "            _grace = False  # 扰动：宽限接线断开", "E1"),

    ("ui.py", "后台渲染跳过分支失效（又全量重建 DOM）",
     "        if (not follow_nav",
     "        if (False  # 扰动：后台跳过失效", "D9"),

    ("ui.py", "渲染上限接线断开（又用 300 条上限）",
     "            _limit = automation.render_limit_for_sid(" + CR
     + "                getattr(session, \"sid\", \"\"), MAX_RENDERED_MSGS)",
     "            _limit = MAX_RENDERED_MSGS  # 扰动：上限接线断开", "D10"),

    ("ui.py", "启动归一化接线断开（active 不切回）",
     "            self.store.switch(_fallback_sid)",
     "            pass  # 扰动：启动不归一化 active", "D4"),

    ("ui.py", "后台任务启动又重建 DOM（AppHang 直接来源）",
     "            # _render_messages 的自动化分支跳过，用户点进去时再按需渲染。",
     "            # _render_messages 的自动化分支跳过，用户点进去时再按需渲染。" + CR
     + "            self._render_messages(force_bottom=True)  # 扰动：又重建 DOM",
     "D12"),
]


def run_suite():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, SUITE], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
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
            io.open(path, "w", encoding="utf-8", newline="").write(
                src.replace(old, new, 1))
            out = run_suite()
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(
                originals[path])
        red = [line for line in out.splitlines() if "[FAIL]" in line]
        if "PASS=" in out and any(want in line for line in red):
            print("PASS %s → %s" % (label, want))
            npass += 1
        else:
            print("MISS %s：期望 %s，实际 %s"
                  % (label, want, red[:3] or out[-200:]))
            nfail += 1
    for path, original in originals.items():
        if io.open(path, encoding="utf-8", newline="").read() != original:
            print("FAIL 源码未恢复：%s" % os.path.basename(path))
            nfail += 1
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
