# -*- coding: utf-8 -*-
"""v4.277.0 工具 schema 预算扰动。

改坏六处，跑 tests/test_tool_schema_budget_277.py，确认判据必红。
SKIP / MISS 都算哑弹。原串从真实文件取，且必须唯一。

  P1 核心顺序不再按名字
  P2 按需工具插到核心前面
  P3 忽略 full_tool_schema
  P4 忽略 request_tools 点名
  P5 工具结果里的名单也拿来补 schema
  P6 执行前不再剥掉索要工具
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_tool_schema_budget_277.py")
F_BUDGET = os.path.join(ROOT, "tool_schema_budget.py")
F_AGENT = os.path.join(ROOT, "agent.py")

OLD_P1 = (
    '    "browser_open",\n'
    '    "browser_read",\n'
)
NEW_P1 = (
    '    "browser_read",\n'
    '    "browser_open",  # 扰动：核心不再按名字排\n'
)

OLD_P2 = (
    "    for name in extras:\n"
    "        out.append(by_name[name])\n"
)
NEW_P2 = (
    "    for name in extras:\n"
    "        out.insert(0, by_name[name])  # 扰动：按需工具插到核心前面\n"
)

OLD_P3 = (
    "    if _wants_full_schema(cfg) or not _looks_like_full_catalog(indexed):\n"
)
NEW_P3 = (
    "    if not _looks_like_full_catalog(indexed):  # 扰动：忽略 full_tool_schema\n"
)

OLD_P4 = (
    "    for name in requested or ():\n"
    "        found.add(name)\n"
)
NEW_P4 = (
    "    for name in ():  # 扰动：忽略索要的工具\n"
    "        found.add(name)\n"
)

OLD_P5 = (
    '        if role == "user":\n'
    "            text = _content_text(content)\n"
)
NEW_P5 = (
    '        if role == "user" or role == "tool":  # 扰动：连工具结果一起扫\n'
    "            text = _content_text(content)\n"
)

OLD_P6 = (
    "        tool_calls = self._peel_schema_requests(tool_calls)\n"
    "        if not tool_calls:\n"
    "            return\n"
)
NEW_P6 = (
    "        if not tool_calls:  # 扰动：不再处理索要工具\n"
    "            return\n"
)

CASES = [
    ("P1 核心不再按名字排", F_BUDGET, OLD_P1, NEW_P1,
     ["[FAIL] 核心正好 25 个且按名字排死"]),
    ("P2 按需工具插到核心前面", F_BUDGET, OLD_P2, NEW_P2,
     ["[FAIL] 按需工具排在核心和索要入口后面", "[FAIL] 核心前缀字节不变"]),
    ("P3 忽略 full_tool_schema", F_BUDGET, OLD_P3, NEW_P3,
     ["[FAIL] full_tool_schema 恢复全量且不含索要入口"]),
    ("P4 忽略索要的工具", F_BUDGET, OLD_P4, NEW_P4,
     ["[FAIL] request_tools 会补上点名的工具"]),
    ("P5 连工具结果一起扫", F_BUDGET, OLD_P5, NEW_P5,
     ["[FAIL] 工具结果里的名单不会把 schema 又撑满"]),
    ("P6 不再剥掉索要工具", F_AGENT, OLD_P6, NEW_P6,
     ["[FAIL] 执行前先剥掉索要工具"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_BUDGET, F_AGENT])
    orig = {}
    for path in (F_BUDGET, F_AGENT):
        with open(path, "r", encoding="utf-8", newline="") as fh:
            orig[path] = fh.read()
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            src = orig[target]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            n = src.count(old)
            if n != 1:
                print("SKIP %s: old 串出现 %d 次，非唯一" % (name, n))
                continue
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(src.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and all(token in out for token in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
    finally:
        for path, src in orig.items():
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
