# -*- coding: utf-8 -*-
"""v4.284.0 P1a 扰动：第三方转述族判据改坏必红。

三条分别打在三处修复点上，跑 tests/test_third_party_284.py：
  P1 转述标记匹配被摘除（_THIRD_PARTY_SAY_RE 分支恒不命中）
  P2 has_imperative 反否决摘除（转述+真指令被误拦）
  P3 _reasons 接线摘除（判据算得出但不生效——层 1/2/3 全断）

SKIP / MISS 都算哑弹。原串从真实文件取，且必须唯一。
IO 用纯 text mode（intent_guard.py 磁盘为 CRLF，round-trip 原样）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_third_party_284.py")
F_GUARD = os.path.join(ROOT, "intent_guard.py")

OLD_P1 = """    t = _norm(text)
    if _THIRD_PARTY_SAY_RE.search(t):
        return True
    return any(k in t for k in _THIRD_PARTY_HEARD)"""
NEW_P1 = """    t = _norm(text)
    if False:  # 扰动：转述标记匹配摘除
        return True
    return False"""

OLD_P2 = """    if not text:
        return False
    if has_imperative(text):
        return False           # 主句里有用户自己的真指令 → 放行
    t = _norm(text)
    if _THIRD_PARTY_SAY_RE.search(t):"""
NEW_P2 = """    if not text:
        return False
    if False:  # 扰动：has_imperative 反否决摘除
        return False
    t = _norm(text)
    if _THIRD_PARTY_SAY_RE.search(t):"""

OLD_P3 = """    if is_third_party_report(t):
        out.append("third_party")"""
NEW_P3 = """    if False and is_third_party_report(t):
        out.append("third_party")"""

CASES = [
    ("P1 转述标记匹配摘除", F_GUARD, OLD_P1, NEW_P1,
     ["A1 转述被拦（我朋友说你能写稿）", "A2 拦因是 third_party"]),
    ("P2 has_imperative 反否决摘除", F_GUARD, OLD_P2, NEW_P2,
     ["B1 转述+真指令放行（我朋友说你能写稿，帮我写一个）"]),
    ("P3 _reasons 接线摘除", F_GUARD, OLD_P3, NEW_P3,
     ["A1 转述被拦（我朋友说你能写稿）", "D1 转述句不再 needs_action"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_GUARD])
    with open(F_GUARD, "r") as fh:
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
        with open(F_GUARD, "w") as fh:
            fh.write(orig)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
