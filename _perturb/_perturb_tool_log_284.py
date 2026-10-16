# -*- coding: utf-8 -*-
"""v4.284.0 P1b 扰动：工具日志补参/成败改坏必红。

三条分别打在两处修复点上，跑 tests/test_tool_log_284.py：
  P1 registry 分支入口 args 行打回裸「执行工具: X」（不记参数）
  P2 registry 分支出口完成行摘除（不记得败/结果）
  P3 入口 args 不过脱敏（凭据直进日志——v4.256.0 P1-1 回潮）

SKIP / MISS 都算哑弹。原串从真实文件取，且必须唯一。
IO 用纯 text mode（tools.py 磁盘为 CRLF，round-trip 原样）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_tool_log_284.py")
F_TOOLS = os.path.join(ROOT, "tools.py")

OLD_P1 = """            _args_log = sanitize_for_model(_log_brief(_trunc_args(args, 300)))
            get_logger().info(f"执行工具: {name} | args={_args_log}", module="tools",
                              extra={"tool": name, "args": _args_log})"""
NEW_P1 = """            get_logger().info(f"执行工具: {name}", module="tools",
                              extra={"tool": name})  # 扰动：入口 args 打回裸行"""

OLD_P2 = """            # v4.284.0（P1/A4）：出口记成败 + 结果摘要——与入口 args 行配对，
            # 「是否真执行成功/结果长什么样」从此可定论（排障材料补齐）。
            get_logger().info(
                f"工具完成: {name} | ok={bool(_tr.ok)} | {_log_brief(_tr.msg)}",
                module="tools",
                extra={"tool": name, "ok": bool(_tr.ok)})
"""
NEW_P2 = ""

OLD_P3 = """            _args_log = sanitize_for_model(_log_brief(_trunc_args(args, 300)))"""
NEW_P3 = """            _args_log = _log_brief(_trunc_args(args, 300))  # 扰动：入口脱敏摘除"""

CASES = [
    ("P1 入口 args 打回裸行", F_TOOLS, OLD_P1, NEW_P1,
     ["A1 入口行存在且带 args", "D5 旧「只记工具名」的裸行已清零"]),
    ("P2 出口完成行摘除", F_TOOLS, OLD_P2, NEW_P2,
     ["A5 出口行存在且带 ok 标记", "D3 出口完成行出现两次"]),
    ("P3 入口 args 脱敏摘除", F_TOOLS, OLD_P3, NEW_P3,
     ["A3 入口 args 已脱敏", "D4 入口 args 过 sanitize_for_model 脱敏"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_TOOLS])
    with open(F_TOOLS, "r") as fh:
        orig = fh.read()
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            if old not in orig:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            n = orig.count(old)
            if n != 1:
                print("SKIP %s: old 串出现 %d 次，非唯一" % (name, n))
                continue
            with open(target, "w") as fh:
                fh.write(orig.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and all(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(target, "w") as fh:
                fh.write(orig)
    finally:
        with open(F_TOOLS, "w") as fh:
            fh.write(orig)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
