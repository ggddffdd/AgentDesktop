# -*- coding: utf-8 -*-
"""v4.265.0 扰动：沙箱仅冻结版、导航白名单、critical 抽样入口。

挖掉这三处，tests/test_webengine_policy_265.py 必须翻红。零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
POL = os.path.join(ROOT, "webengine_policy.py")
RUN = os.path.join(ROOT, "tests", "run_all.py")
JUDGE = os.path.join(ROOT, "tests", "test_webengine_policy_265.py")

CASES = [
    ("V1 开发版也关沙箱", POL,
     "    if frozen:\n",
     "    if True:  # 扰动：开发版也关沙箱\n",
     False),
    ("V2 外站一律放行", POL,
     "        return bool(host) and host in allow\n",
     "        return True  # 扰动：外站一律放行\n",
     False),
    ("V3 去掉 critical 抽样入口", RUN,
     '    ap.add_argument("--perturb-critical", action="store_true",\n',
     '    ap.add_argument("--perturb-off", action="store_true",  # 扰动：关掉 critical 抽样\n',
     False),
    ("V4 多斜杠盘符不还原", POL,
     "    drive = path.lstrip(\"/\")\n",
     "    drive = path  # 扰动：不剥盘符前的斜杠\n",
     False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [POL, RUN]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    try:
        for name, fp, old, new, replace_all in CASES:
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
