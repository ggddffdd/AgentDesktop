# -*- coding: utf-8 -*-
"""v4.250.0 扰动脚本：军团数据抓取收尾 + UI 收尾。

反向照妖镜：变异四处修复点，跑两个判据，确认判据必红（改坏必红）。零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
TOOLS = os.path.join(ROOT, "tools.py")
LEGION = os.path.join(ROOT, "legion.py")
UI = os.path.join(ROOT, "legion_ui.py")
JUDGE_DATA = os.path.join(ROOT, "tests", "test_legion_data_verify_250.py")
JUDGE_UI = os.path.join(ROOT, "tests", "test_legion_ui_polish_250.py")

CASES = [
    # V1：browser_read 留痕失效 → 判据「留痕·browser_read在追踪表」翻红
    ("V1 browser_read留痕失效", TOOLS, "data", JUDGE_DATA,
     '_FETCH_TRACE_TOOLS = ("web_search", "web_fetch", "browser_read")',
     '_FETCH_TRACE_TOOLS = ("web_search", "web_fetch")  # 扰动：browser_read留痕失效', False),
    # V2：数据闸 browser_read 识别失效 → 判据「数据闸·browser_read原文识别」翻红
    ("V2 数据闸browser_read识别失效", LEGION, "data", JUDGE_DATA,
     '    for m in _BROWSER_READ_DUMP_RE.finditer(t):\n        evidence.append("浏览器读取原文：" + m.group(0))',
     '    pass  # 扰动：browser_read识别失效', False),
    # V3：技能 description 副行失效 → 判据「技能·item拼description副行」翻红
    ("V3 技能description副行失效", UI, "ui", JUDGE_UI,
     '                label += "\\n  " + desc[:50]',
     '                pass  # 扰动：desc副行删', False),
    # V4：团队库 CRUD 折叠失效 → 判据「团队·CRUD按钮默认隐藏」翻红
    ("V4 团队CRUD折叠失效", UI, "ui", JUDGE_UI,
     '            b.setVisible(False)',
     '            pass  # 扰动：CRUD折叠删', False),
]


def run_judge(judge):
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, judge], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [TOOLS, LEGION, UI]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    try:
        for name, fp, _kind, judge, old, new, replace_all in CASES:
            total += 1
            src = snaps[fp]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            cnt = src.count(old)
            if replace_all:
                assert cnt >= 1, "old 串在 %s 未命中" % fp
                mutated = src.replace(old, new)
            else:
                assert cnt == 1, "old 串在 %s 出现 %d 次，非唯一" % (fp, cnt)
                mutated = src.replace(old, new, 1)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(mutated)
            rc, out = run_judge(judge)
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
