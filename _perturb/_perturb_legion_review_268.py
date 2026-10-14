# -*- coding: utf-8 -*-
"""v4.268.0 扰动：军团审阅 P0/P1。

挖掉派发前授权、多技能全文、精确匹配，tests/test_legion_review_268.py 必须翻红。
零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _perturb_guard as G  # noqa: E402

PY = sys.executable
LP = os.path.join(ROOT, "legion_permissions.py")
WORKER = os.path.join(ROOT, "legion_worker.py")
LEGION = os.path.join(ROOT, "legion.py")
JUDGE = os.path.join(ROOT, "tests", "test_legion_review_268.py")

CASES = [
    ("V1 派发前不授权", LP,
     '    if adapter is None or gate_mode != "human":\n        return False\n',
     '    if True:  # 扰动：派发前不授权\n        return False\n',
     False),
    ("V2 派发前不调用授予", WORKER,
     "            self._grant_wave_before_dispatch(wi + 1)\n",
     "            pass  # 扰动：派发前不调用授予\n",
     False),
    ("V3 多技能不下发全文", LEGION,
     "        if req or _skill_hits_task(slug, sk, task):\n",
     "        if False and (req or _skill_hits_task(slug, sk, task)):  # 扰动：多技能不下发全文\n",
     False),
    ("V4 恢复子串匹配", LEGION,
     "            if c and _norm_role_key(c) == nk:\n",
     "            if c and nk and (nk in _norm_role_key(c) or _norm_role_key(c) in nk):  # 扰动：恢复子串匹配\n",
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
    files = [LP, WORKER, LEGION]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    try:
        for name, fp, old, new, _replace_all in cases:
            total += 1
            src = snaps[fp]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            cnt = src.count(old)
            if cnt != 1:
                print("SKIP %s: old 串出现 %d 次" % (name, cnt))
                continue
            mutated = src.replace(old, new, 1)
            with open(fp, "w", encoding="utf-8", newline="\n") as f:
                f.write(mutated)
            rc, out = run_judge()
            failed = (rc != 0) and ("FAIL" in out)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(fp, "w", encoding="utf-8", newline="\n") as f:
                f.write(src)
    finally:
        for fp, src in snaps.items():
            with open(fp, "w", encoding="utf-8", newline="\n") as f:
                f.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
