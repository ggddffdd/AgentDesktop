# -*- coding: utf-8 -*-
"""v4.252.0 扰动脚本：军团 UI 体验优化（技能搜索分组 + 成员跨波移动）。

反向照妖镜：变异三处，跑 tests/test_legion_ui_polish_252.py，确认判据必红（改坏必红）。
V1 搜索过滤失效；V2 分类分组失效；V3 跨波移动失效。零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
UI = os.path.join(ROOT, "legion_ui.py")
JUDGE = os.path.join(ROOT, "tests", "test_legion_ui_polish_252.py")

CASES = [
    # V1：搜索过滤失效 → 判据「技能·搜索过滤生效」翻红
    ("V1 搜索过滤失效", UI,
     "                    if q not in hay:\n"
     "                        continue",
     "                    if False:  # 扰动：搜索过滤失效\n"
     "                        continue", False),
    # V2：分类分组失效 → 判据「技能·按分类分组」翻红
    ("V2 分类分组失效", UI,
     "                by_cat.setdefault(cat, []).append((slug, name, emoji, desc))",
     "                by_cat.setdefault(\"全部\", []).append((slug, name, emoji, desc))  # 扰动：不分组",
     False),
    # V3：跨波移动失效 → 判据「移动·成员到第2波末尾」翻红
    ("V3 跨波移动失效", UI,
     "        member = members.pop(mi)\n"
     "        waves[target_wi].setdefault(\"members\", []).append(member)",
     "        members.pop(mi)  # 扰动：移出去不追加（成员丢失）",
     False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [UI]
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
            assert cnt == 1, "old 串在 %s 出现 %d 次，非唯一" % (fp, cnt)
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
