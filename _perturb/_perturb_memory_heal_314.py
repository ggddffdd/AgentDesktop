# -*- coding: utf-8 -*-
"""v4.314.0 memory.db 缺表自愈的真实源码扰动验证（改坏必红）。

背景：2026-11-20 实测事故 —— memory.db 变 0 字节空库后，`search_memory` 静默失效
（`_sync_md_to_db` 崩 no such table，异常被 catch 成 warning，用户毫无察觉）。

修法把表存在性自愈下沉到 `_with_db` 单点。本文件验证：把自愈逻辑改坏，
对应判据必须翻红；改不红（假绿）即 FAIL。

memory_store.py 是 CRLF 文件，锚点一律用 CR 拼接。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _perturb_guard

SUITE = os.path.join(ROOT, "tests", "test_memory_db_heal_314.py")
CR = "\r\n"

# (相对路径, 标签, 原行列表, 替换文本, want判据, 换行)
CASES = [
    # 自愈检查整块被删 → A3 红（索引不再从 memory.md 重建）
    ("memory_store.py", "删掉表存在性自愈",
     ["        if not conn.execute(",
      "                \"SELECT 1 FROM sqlite_master WHERE name='memories' LIMIT 1\"",
      "        ).fetchone():",
      "            _create_tables(conn)"],
     "        pass  # 扰动：缺表不自愈", "A3", CR),
    # 自愈分支变成空操作 → A5 红（FTS 虚拟表不再重建）
    ("memory_store.py", "自愈分支不建表",
     ["            _create_tables(conn)"],
     "            pass  # 扰动：检查到了但不建表", "A5", CR),
    # 加密模式被排除在自愈之外 → A17 红（加密库 0 字节时搜不到）
    ("memory_store.py", "加密模式绕过自愈",
     ["        if not conn.execute("],
     "        if _cipher is None and not conn.execute(  # 扰动：加密模式不自愈",
     "A17", CR),
    # 建表挪到 return 之后（不可达）→ A4 红（memory.db 仍是 0 字节）
    ("memory_store.py", "建表挪到 return 之后",
     ["            _create_tables(conn)", "        return fn(conn)"],
     "            pass  # 扰动：建表挪到 return 之后，不可达" + CR +
     "        return fn(conn)" + CR +
     "        if True:" + CR +
     "            _create_tables(conn)  # 扰动：不可达", "A4", CR),
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