# -*- coding: utf-8 -*-
"""v4.248.0 扰动脚本：军团产出质量 + 组队覆盖修复。

反向照妖镜：变异三处修复点，跑 tests/test_legion_quality_248.py，确认判据必红（改坏必红）。
三个 case 分别针对组队覆盖/选品官 web_fetch/配图师交付物，零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
LEGION = os.path.join(ROOT, "legion.py")
UI = os.path.join(ROOT, "legion_ui.py")
JUDGE = os.path.join(ROOT, "tests", "test_legion_quality_248.py")

CASES = [
    # V1：组队恢复覆盖选中团队 → 判据「组队·总是新建」翻红
    ("V1 组队覆盖复活", UI,
     "        # v4.248.0：组队 = 新建团队，绝不覆盖选中的老团队\n"
     "        p = legion.new_project(name=\"新军团\", emoji=\"🧙\")",
     "        # v4.248.0：组队 = 新建团队，绝不覆盖选中的老团队\n"
     "        p = self._cur_project()  # 扰动：组队覆盖复活", False),
    # V2：选品官 web_fetch 复活 → 判据「选品官·无web_fetch」翻红
    ("V2 选品官web_fetch复活", LEGION,
     "            # v4.248.0：移除 web_fetch —— 对齐研究员/竞品分析师（v4.147.9 A 方案）：\n"
     "            # 成员从不主动用 browser，靠提示倒逼已证无效，必须从工具白名单移除 web_fetch\n"
     "            # 逼它走 browser_read 抓实时页面。\n"
     "            tools=[\"web_search\", \"browser_open\", \"browser_read\",",
     "            # v4.248.0：移除 web_fetch —— 对齐研究员/竞品分析师（v4.147.9 A 方案）：\n"
     "            # 成员从不主动用 browser，靠提示倒逼已证无效，必须从工具白名单移除 web_fetch\n"
     "            # 逼它走 browser_read 抓实时页面。\n"
     "            tools=[\"web_search\", \"web_fetch\", \"browser_open\", \"browser_read\",  # 扰动：web_fetch复活", False),
    # V3：配图师交付物核对被删 → 判据「配图师·self_check含交付物核对」翻红
    ("V3 配图师交付物核对被删", LEGION,
     "                      \"少一张都判未交付，回去补生，不许只交提示词清单\",",
     "                      \"少一张都判未交全，回去补生，不许只交提示词清单\",  # 扰动：交付物核对删", False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [LEGION, UI]
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
            if replace_all:
                assert cnt >= 1, "old 串在 %s 未命中" % fp
                mutated = src.replace(old, new)
            else:
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
