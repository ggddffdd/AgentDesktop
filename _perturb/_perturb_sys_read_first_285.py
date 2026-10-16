# -*- coding: utf-8 -*-
"""v4.285.0 P2a 扰动：「先读后说」系统提示改坏必红。

P1 删掉 v4.285.0 先读后说条目（打回 v4.284.0 状态）
P2 连同 v4.283.0 汇报纪律一起删（双杀，防「精简时整段被清」）

跑 tests/test_sys_read_first_285.py。原串从真实文件取且唯一。
IO 纯 text mode（config.py 磁盘为 CRLF，round-trip 原样）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_sys_read_first_285.py")
F_CFG = os.path.join(ROOT, "config.py")

OLD_P1 = """    "- 【v4.285.0 先读后说】汇报「工具返回了什么」必须照本会话工具结果的**原文**说："
    "引用其中的关键字段、数字、路径、报错信息时，逐个字核对后再写进回复；"
    "工具结果里没有的内容（状态、进度、结论、数字）一律不得凭印象转述或脑补。"
    "概括可以简化措辞，但不得改变事实与数量；记不准原文时回去重读该条工具结果，"
    "不要「差不多就行」——转述走样比不汇报更糟。\\n"
"""
NEW_P1 = ""

OLD_P2 = """    "- 【v4.283.0 汇报纪律】汇报执行结果必须引用**本会话**的工具记录（证据编号）；"
    "禁止声称「没写文件 / 没执行 / 已跳过 / 按新规矩没落盘」——除非本会话工具记录里"
    "确有对应的取消或失败证据。看不到其他会话（含定时任务会话）的执行事实时，"
    "明说「我看不到那个会话的记录」，可建议查日志核实，**禁止凭印象编造**。"
    "落盘路径以用户或任务正文指定为准，不要擅自另写副本到产物目录或其他路径。\\n"
"""
NEW_P2 = ""

CASES = [
    ("P1 先读后说条目删除", F_CFG, OLD_P1, NEW_P1,
     ["A1 含 v4.285.0 先读后说标签"]),
    ("P2 汇报纪律条目删除", F_CFG, OLD_P2, NEW_P2,
     ["B1 含 v4.283.0 汇报纪律标签"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_CFG])
    with open(F_CFG, "r") as fh:
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
        with open(F_CFG, "w") as fh:
            fh.write(orig)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
