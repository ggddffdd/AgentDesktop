# -*- coding: utf-8 -*-
"""v4.265.0 扰动：军团权限复用 risk.py 的再确认规则。

挖掉 ALWAYS_CONFIRM / command_danger_level / task critical / worker 接线，
tests/test_legion_permissions.py 必须翻红。零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
LP = os.path.join(ROOT, "legion_permissions.py")
WORKER = os.path.join(ROOT, "legion_worker.py")
JUDGE = os.path.join(ROOT, "tests", "test_legion_permissions.py")

CASES = [
    ("V1 硬确认失效", LP,
     "        if tool in ALWAYS_CONFIRM:\n",
     "        if False and tool in ALWAYS_CONFIRM:  # 扰动：硬确认失效\n",
     False),
    ("V2 不探测高危命令", LP,
     "            label = command_danger_level(_exec_blob(args))\n",
     "            label = \"\"  # 扰动：不探测高危\n",
     False),
    ("V3 critical 不收紧", LP,
     "            if lab:\n",
     "            if False and lab:  # 扰动：critical 不收紧\n",
     False),
    ("V4 worker 不接二次确认", WORKER,
     "                p.reconfirm_cb = self._perm_reconfirm\n",
     "                p.reconfirm_cb = None  # 扰动：不接二次确认\n",
     False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    cases = list(CASES)
    files = [LP, WORKER]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    try:
        for name, fp, old, new, replace_all in cases:
            total += 1
            src = snaps[fp]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            cnt = src.count(old)
            assert cnt == 1, "old 串在 %s 出现 %d 次，非唯一" % (os.path.basename(fp), cnt)
            mutated = src.replace(old, new, 1)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(mutated)
            rc, out = run_judge()
            failed = (rc != 0) and ("FAIL" in out)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    finally:
        for fp, src in snaps.items():
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
