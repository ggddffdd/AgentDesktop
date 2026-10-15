# -*- coding: utf-8 -*-
"""v4.279.0 扰动：定时任务不得停在多功能澄清上。

改坏四条修复（否定短语、澄清豁免、落盘工具补回、范围内写文件免确认），
tests/test_automation_clarify_279.py 必须翻红。哑弹即失败。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_automation_clarify_279.py")
F_INTENT = os.path.join(ROOT, "intent.py")
F_AGENT = os.path.join(ROOT, "agent.py")
F_AUTO = os.path.join(ROOT, "automation.py")
F_PERM = os.path.join(ROOT, "permissions.py")
F_UI = os.path.join(ROOT, "ui.py")

CASES = [
    ("V1 「无需浏览器」仍算打开网页",
     F_INTENT,
     "    out = text or \"\"\n"
     "    for phrase in _BROWSER_NEGATION:\n",
     "    return text or \"\"  # 扰动：否定短语仍算打开浏览器\n"
     "    out = text or \"\"\n"
     "    for phrase in _BROWSER_NEGATION:\n"),
    ("V2 定时任务也走多功能澄清",
     F_AGENT,
     "                and self._is_unattended_automation()):\n",
     "                and False):  # 扰动：自动化也澄清\n"),
    ("V3 第一步重新分类后不再豁免",
     F_AGENT,
     "                self._waive_automation_clarify()  # 定时任务不因多功能澄清停住\n",
     ""),
    ("V4 声明了落盘也不补 write_file",
     F_AUTO,
     "    if not isinstance(task, dict):\n"
     "        return kept\n",
     "    if True:  # 扰动：声明了落盘也不补工具\n"
     "        return kept\n"),
    ("V5 定时任务写文件仍要确认",
     F_PERM,
     "        if unattended_active() and name == \"write_file\":\n",
     "        if False and name == \"write_file\":  # 扰动：定时任务仍要确认写文件\n"),
    ("V6 工具过滤不再带上任务正文",
     F_UI,
     "automation.filter_tools_safe(all_tools, _auto_t)",
     "automation.filter_tools_safe(all_tools)"),
    ("V7 自动化线程不再标记无人值守",
     F_AGENT,
     "            self._arm_unattended()\n",
     "            pass  # 扰动：不标记无人值守\n"),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True,
                       cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([F_INTENT, F_AGENT, F_AUTO, F_PERM, F_UI])
    originals = {}
    for path in (F_INTENT, F_AGENT, F_AUTO, F_PERM, F_UI):
        originals[path] = open(path, "r", encoding="utf-8").read()
    total = 0
    hits = 0
    try:
        for name, path, old, new in CASES:
            total += 1
            src = originals[path]
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
        for path, src in originals.items():
            open(path, "w", encoding="utf-8", newline="\n").write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
