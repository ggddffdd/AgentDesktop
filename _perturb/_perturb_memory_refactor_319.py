# -*- coding: utf-8 -*-
"""v4.319.0 记忆层改造三项的真实源码扰动验证（改坏必红）。

  P1 rag.py 删 normalize_chunk 加载调用        → A10 红
  P2 rag.py 写入 chunk 去掉 entity_id 字段     → B2 红
  P3 memory_store.py changelog 上限 50→5       → A5 红
  P4 memory_store.py 归档失败仍合并（信息丢失） → B2 红(归档分支) / A9 红
  P5 memory_store.py _trim_oldest 退回硬裁     → B1 红

rag.py 是 LF，memory_store.py 是 CRLF。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _perturb_guard

SUITES = {
    "rag": os.path.join(ROOT, "tests", "test_rag_provenance_319.py"),
    "changelog": os.path.join(ROOT, "tests", "test_memory_changelog_319.py"),
    "merge": os.path.join(ROOT, "tests", "test_memory_merge_319.py"),
}
LF = "\n"
CRLF = "\r\n"

# (相对路径, 标签, 原行, 替换, 套件key, want判据, 换行)
CASES = [
    ("rag.py", "删 normalize_chunk 加载调用",
     "self._chunks = [normalize_chunk(c) for c in data.get(\"chunks\", [])]",
     "self._chunks = data.get(\"chunks\", [])  # 扰动：不归一", "rag", "A10", LF),
    ("rag.py", "写入去掉 entity_id",
     '                        "entity_id": fname,',
     '                        "entity_id": "",  # 扰动：溯源丢失', "rag", "B2", LF),
    ("memory_store.py", "changelog 上限改 5",
     "CHANGELOG_MAX = 50",
     "CHANGELOG_MAX = 5  # 扰动", "changelog", "A5", CRLF),
    ("memory_store.py", "归档失败仍合并",
     "    if not _archive_merged(oldest):",
     "    if False and not _archive_merged(oldest):  # 扰动：跳过归档", "merge", "A9", CRLF),
    ("memory_store.py", "_trim_oldest 退回硬裁",
     "    merged = build_merge_entry(oldest)",
     "    merged = \"\"  # 扰动：不生成合并摘要", "merge", "B1", CRLF),
]


def run_suite(key):
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, SUITES[key]], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def main():
    files = sorted({os.path.join(ROOT, rel) for rel, *_ in CASES})
    _perturb_guard.arm(files)
    originals = {p: io.open(p, encoding="utf-8", newline="").read() for p in files}
    # 三个套件基线都要绿
    for key in SUITES:
        base = run_suite(key)
        if "FAIL=0" not in base:
            print("BASELINE RED：套件 %s 未全绿" % key)
            print(base[-800:])
            print("PERTURB PASS=0 FAIL=%d" % len(CASES))
            return 1
    npass = nfail = 0
    for rel, label, old, new, key, want, nl in CASES:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("MISS %s：锚点数量=%d" % (label, src.count(old)))
            nfail += 1
            continue
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
            out = run_suite(key)
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
