# -*- coding: utf-8 -*-
"""v4.238.1 扰动脚本：run_all.py 父进程 UTF-8 兜底（CI cp1252 首跑红）。

反向照妖镜：变异 tests/run_all.py 入口的 reconfigure 兜底，跑
test_runall_utf8_238.py，确认判据必红（改坏必红）。两个 case 全部针对
修复本体，零哑弹。沿用已验证的「直接变异 + _perturb_guard 还原」模式。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
TARGET = os.path.join(ROOT, "tests", "run_all.py")
JUDGE = os.path.join(ROOT, "tests", "test_runall_utf8_238.py")

# ---- 精确变异点（与源码 1:1 对齐，已用 Read 工具核对唯一性）----

# case 1：删掉父进程 reconfigure 兜底 → 回到 CI 首跑的原始死法
# （cp1252 控制台下第一个中文 print 就 UnicodeEncodeError）。
OLD_V1 = """    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())"""
NEW_V1 = """    sys.exit(main())"""

# case 2：把 reconfigure 的编码改成 ascii/strict → 兜底形同虚设，
# 中文 print 照样炸（验证判据盯的是「编码选择本身」，不只是「有没有这行」）。
OLD_V2 = '_stream.reconfigure(encoding="utf-8", errors="replace")'
NEW_V2 = '_stream.reconfigure(encoding="ascii", errors="strict")'

CASES = [
    ("V1 删父进程 reconfigure 兜底块（核心回归）", OLD_V1, NEW_V1),
    ("V2 reconfigure 编码退化为 ascii/strict", OLD_V2, NEW_V2),
]


def run_judge():
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([TARGET])  # 三重还原 + 残留预检
    src = open(TARGET, "r", encoding="utf-8").read()
    total = 0
    hits = 0
    try:
        for name, old, new in CASES:
            total += 1
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            # 断言只变异一处
            assert src.count(old) == 1, "old 串出现 %d 次，非唯一" % src.count(old)
            mutated = src.replace(old, new, 1)
            open(TARGET, "w", encoding="utf-8", newline="").write(mutated)
            rc, out = run_judge()
            # 判据应翻红：退出码非 0 且含 FAIL 字样
            failed = (rc != 0) and ("FAIL" in out)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            # 还原（guard 也会在退出时再还原一次）
            open(TARGET, "w", encoding="utf-8", newline="").write(src)
    finally:
        open(TARGET, "w", encoding="utf-8", newline="").write(src)  # 最终兜底还原
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
