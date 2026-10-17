# -*- coding: utf-8 -*-
"""v4.286.0 真后端探针扰动：探针语义退化改坏必红。

三条分别打在探针最要害的三处语义上，跑 tests/test_probe_real_backend_286.py：
  P1 fail-closed 总闸被拆（FAIL 集合永远为空 → 403 类事故再无拦截层）
  P2 SKIP 永远化（有 key 也 SKIP → 探针退化成「永远绿」的装饰品）
  P3 SKIP 横幅文案被删（静态判据 A3 + 行为判据 E2 联动）

SKIP / MISS 都算哑弹。原串从真实文件取，且必须唯一。
IO 用纯 text mode（probe_real_backend.py 磁盘为 LF，round-trip 原样）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_probe_real_backend_286.py")
F_PROBE = os.path.join(ROOT, "probe_real_backend.py")

OLD_P1 = "    fails = [r for r in results if r[1] == FAIL]"
NEW_P1 = "    fails = []  # 扰动：fail-closed 总闸拆除"

OLD_P2 = """    if not _has_key(cfg, "brave_api_key", "serper_api_key"):
        return (SKIP, "未配置 brave_api_key / serper_api_key")"""
NEW_P2 = """    if True:  # 扰动：永远 SKIP
        return (SKIP, "未配置 brave_api_key / serper_api_key")"""

OLD_P3 = '''        print("  [SKIP 横幅] %d 个探针因环境前提缺失跳过（无 key / 库未装）——" % len(skips))
        print("              SKIP 不冒充通过；配好 key 的环境才会真调用。")'''
NEW_P3 = '''        print("  (扰动) 有 %d 个跳过" % len(skips))'''

CASES = [
    ("P1 fail-closed 总闸拆除", F_PROBE, OLD_P1, NEW_P1,
     ["E3 任一 FAIL → rc=1", "E4 探针自身异常"]),
    ("P2 SKIP 永远化", F_PROBE, OLD_P2, NEW_P2,
     ["B2 web_search 有 key + 正常返回"]),
    ("P3 SKIP 横幅文案删除", F_PROBE, OLD_P3, NEW_P3,
     ["A3 SKIP 不冒充通过", "E2 全 SKIP → 打 SKIP 横幅"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_PROBE])
    with open(F_PROBE, "r") as fh:
        orig = fh.read()
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            if old not in orig:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            n = orig.count(old)
            if n != 1:
                print("SKIP %s: old 串出现 %d 次，非唯一" % (name, n))
                continue
            with open(target, "w") as fh:
                fh.write(orig.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and all(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(target, "w") as fh:
                fh.write(orig)
    finally:
        with open(target, "w") as fh:
            fh.write(orig)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
