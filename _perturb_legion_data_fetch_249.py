# -*- coding: utf-8 -*-
"""v4.249.0 扰动脚本：军团数据抓取系统性清 web_fetch。

反向照妖镜：变异四个口子，跑 tests/test_legion_data_fetch_249.py，确认判据必红（改坏必红）。
四个 case 分别针对角色卡/能力映射/标签映射/候选列表，零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
LEGION = os.path.join(ROOT, "legion.py")
JUDGE = os.path.join(ROOT, "tests", "test_legion_data_fetch_249.py")

CASES = [
    # V1：4 个角色卡 web_fetch 复活 → 判据「角色·无web_fetch」翻红
    ("V1 角色卡web_fetch复活", LEGION,
     '            tools=["web_search", "browser_open", "browser_read", "write_file", "read_file"],',
     '            tools=["web_search", "web_fetch", "browser_open", "browser_read", "write_file", "read_file"],  # 扰动：web_fetch复活',
     True),
    # V2：能力映射 web_fetch 复活 → 判据「能力·实时搜索·无web_fetch」翻红
    ("V2 能力映射web_fetch复活", LEGION,
     '    "实时搜索": {"tools": ["web_search", "browser_read"], "skills": []},',
     '    "实时搜索": {"tools": ["web_search", "web_fetch"], "skills": []},  # 扰动：web_fetch复活',
     False),
    # V3：标签映射 web_fetch 复活 → 判据「标签·web-research·无web_fetch」翻红
    ("V3 标签映射web_fetch复活", LEGION,
     '        "tools": ["web_search", "browser_read"],',
     '        "tools": ["web_search", "web_fetch"],  # 扰动：web_fetch复活',
     False),
    # V4：候选列表 web_fetch 复活 → 判据「候选列表·无web_fetch」翻红
    ("V4 候选列表web_fetch复活", LEGION,
     '    "web_search", "browser_open", "browser_read", "write_file", "read_file",',
     '    "web_search", "web_fetch", "browser_open", "browser_read", "write_file", "read_file",  # 扰动：web_fetch复活',
     False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [LEGION]
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
