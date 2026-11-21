# -*- coding: utf-8 -*-
"""v4.320.0 第二批三项真实源码扰动，验证对应判据改坏必红。

覆盖：P1-2（BM25/RRF/同义词上限）、P2-1（增量复用/删除清理）、
P3-2（召回上限/来源标注）。复用统一护栏：快照 + 三重还原 + 残留预检。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _perturb_guard

LF = "\n"

# (相对路径, 标签, 原行列表, 替换文本, want判据, 换行)
CASES = [
    ("rag.py", "抹掉 BM25 兜底开关",
     ["ENABLE_BM25_FALLBACK = True"],
     "ENABLE_BM25_FALLBACK = False  # 扰动", "C1", LF),
    ("rag.py", "RRF 融合改回单通路",
     ["fused = _rrf_fuse([cos_indices, kw_rank], k=_RRF_K)"],
     "fused = list(cos_indices)  # 扰动", "B2", LF),
    ("rag.py", "同义词上限放宽",
     ["    return out[:6]"],
     "    return out[:20]  # 扰动", "B6", LF),
    ("rag.py", "复用后 id 被掏空",
     ["            reused[i] = bucket.pop(0)"],
     '            reused[i] = dict(bucket.pop(0), id="")  # 扰动', "A2", LF),
    ("rag.py", "删除段不清理",
     ["    removed = [c for bucket in pool.values() for c in bucket]"],
     "    removed = []  # 扰动", "A5", LF),
    ("memory_store.py", "召回上限回退 4000",
     ["def recall_memory(query, limit=8, max_chars=3000):"],
     "def recall_memory(query, limit=8, max_chars=4000):  # 扰动", "A1", LF),
    ("memory_store.py", "去掉来源标注",
     ['    label = "（来源：长期记忆库 memory.md，系统自动召回，非用户当前指令）\\n"'],
     '    label = ""  # 扰动', "A3", LF),
]

SUITES = {
    "rag.py": "tests/test_rag_dual_retrieval_320.py",
    "memory_store.py": "tests/test_memory_injection_320.py",
}
# 精确套件映射（P2-1 的 case 要跑 incremental 套件）
PER_CASE_SUITE = {
    "复用后 id 被掏空": "tests/test_rag_incremental_320.py",
    "删除段不清理": "tests/test_rag_incremental_320.py",
}


def run_suite(path):
    r = subprocess.run([sys.executable, path], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def main():
    files = sorted({os.path.join(ROOT, rel) for rel, *_ in CASES})
    _perturb_guard.arm(files)
    originals = {p: io.open(p, encoding="utf-8", newline="").read() for p in files}
    # 基线：所有相关套件必须全绿
    for suite in set(list(SUITES.values()) + list(PER_CASE_SUITE.values())):
        base = run_suite(os.path.join(ROOT, suite))
        if "FAIL=0" not in base:
            print("BASELINE RED：%s 未全绿" % suite)
            print(base[-800:])
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
        suite = PER_CASE_SUITE.get(label, SUITES[rel])
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
            out = run_suite(os.path.join(ROOT, suite))
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(originals[path])
        red = [line for line in out.splitlines() if "[FAIL]" in line]
        if any(want in line for line in red):
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
