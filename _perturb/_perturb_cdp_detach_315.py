# -*- coding: utf-8 -*-
"""v4.315.0 _ensure_cdp 自动拉起脱离父进程（DETACHED_PROCESS）的真实源码扰动验证。

背景：2026-11-21 实测定性 —— `_ensure_cdp` 用 `CREATE_NEW_PROCESS_GROUP` 拉起 Edge
调试实例，但该标志不脱离父进程生命周期：父进程（一次性子进程如定时任务脚本）退出后
Edge 跟着死（9222 ready 掉回 False）。加固为 `| 0x00000008`（DETACHED_PROCESS）。

本文件验证：把加固改坏，对应判据必须翻红；改不红（假绿）即 FAIL。
browser_control_tools.py 是 LF 文件（无 BOM），锚点一律用 LF 拼接。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _perturb_guard

SUITE = os.path.join(ROOT, "tests", "test_cdp_detach_315.py")
LF = "\n"

# (相对路径, 标签, 原行列表, 替换文本, want判据, 换行)
CASES = [
    # DETACHED 标志被删 → A1 红（Popen 的 creationflags 不再含 0x8）
    ("browser_control_tools.py", "删掉 DETACHED 标志",
     ["subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008"],
     "subprocess.CREATE_NEW_PROCESS_GROUP  # 扰动：退化成不脱离父进程", "A1", LF),
    # 只剩 DETACHED（丢掉 CREATE_NEW_PROCESS_GROUP）→ A5 红（不再是合并）
    ("browser_control_tools.py", "只留 DETACHED 丢掉进程组",
     ["subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008"],
     "0x00000008  # 扰动：丢进程组标志", "A5", LF),
    # 合并符号改成逗号 → B2 红（源码不再是 `|` 合并形式）
    ("browser_control_tools.py", "合并符号改逗号",
     ["subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008"],
     "subprocess.CREATE_NEW_PROCESS_GROUP, 0x00000008  # 扰动：不是 OR 合并", "B2", LF),
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
