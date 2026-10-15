# -*- coding: utf-8 -*-
"""v4.280.0 扰动：定时任务否定浏览器 → 必须物理摘掉 browser_* 工具。

改坏两处修复（摘浏览器调用、否定词命中），tests/test_automation_no_browser_280.py
必须翻红。哑弹即失败。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_automation_no_browser_280.py")
F_AUTO = os.path.join(ROOT, "automation.py")

CASES = [
    ("V1 摘浏览器调用被摘掉 → browser_* 泄漏",
     F_AUTO,
     "    return _strip_negated_browser_tools(\n"
     "        _restore_declared_task_tools(kept, tools, task), task)\n",
     "    return _restore_declared_task_tools(kept, tools, task)\n"),
    ("V2 否定词永不命中 → 不摘浏览器",
     F_AUTO,
     "    if not any(k.lower() in text for k in _NO_BROWSER_KW):\n",
     "    if not any([]):  # 扰动：否定词永不命中\n"),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True,
                       cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([F_AUTO])
    original = open(F_AUTO, "r", encoding="utf-8").read()
    total = 0
    hits = 0
    try:
        for name, path, old, new in CASES:
            total += 1
            src = original
            if old not in src:
                print("SKIP %s: old 串未命中" % name)
                continue
            if src.count(old) != 1:
                print("SKIP %s: old 串出现 %d 次" % (name, src.count(old)))
                continue
            mutated = src.replace(old, new, 1)
            open(path, "w", encoding="utf-8", newline="\n").write(mutated)
            try:
                rc, out = run_judge()
            finally:
                open(path, "w", encoding="utf-8", newline="\n").write(src)
            failed = (rc != 0) and ("FAIL" in out)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹）" % name)
                print(out[:1500])
    finally:
        open(F_AUTO, "w", encoding="utf-8", newline="\n").write(original)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
