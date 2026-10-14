# -*- coding: utf-8 -*-
"""v4.265.0 扰动：空 note 不再当通过、skipped 醒目、缺 key 横幅。

挖掉这三处，tests/test_vision_qc.py 必须翻红。零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
VQ = os.path.join(ROOT, "vision_qc.py")
DW = os.path.join(ROOT, "director_web.py")
JUDGE = os.path.join(ROOT, "tests", "test_vision_qc.py")

CASES = [
    ("V1 空 note 又当成通过", VQ,
     "    if not text:\n        return QC_SKIP_ERROR\n",
     "    if not text:\n        return QC_PASS  # 扰动：空 note 又当成通过\n",
     False),
    ("V2 跳过徽标退回灰字", DW,
     '    ".qc.skip{color:#9A6700;background:#FFF4D6;font-weight:700;"\n',
     '    ".qc.skip{color:#7a8290;"  # 扰动：跳过不再醒目\n',
     False),
    ("V3 缺 key 不提示", VQ,
     "    return QC_KEY_BANNER\n",
     "    return \"\"  # 扰动：缺 key 不提示\n",
     False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [VQ, DW]
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
