# -*- coding: utf-8 -*-
"""v4.283.0 防「模型无指令自己加戏」扰动。

改坏三处修复点，跑 tests/test_no_self_action_283.py，确认判据必红。
SKIP / MISS 都算哑弹。原串从真实文件取，且必须唯一。

  P1 寒暄判据接线摘除（is_presence_check 不再进 _reasons）
  P2 否认授义词表清空（_NEG_DENIAL_OF_AUTH 扫描循环改空）
  P3 汇报纪律从句从 AGENT_SYS_APPEND 删除
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_no_self_action_283.py")

F_GUARD = os.path.join(ROOT, "intent_guard.py")
F_CONFIG = os.path.join(ROOT, "config.py")

OLD_P1 = """    if is_presence_check(t):
        out.append("presence")
"""
NEW_P1 = """    if False and is_presence_check(t):
        out.append("presence")
"""

OLD_P2 = """    for k in _NEG_DENIAL_OF_AUTH:
        i = text.find(k)
        if i >= 0:
            out.append((i, k))
"""
NEW_P2 = """    for k in ():
        i = text.find(k)
        if i >= 0:
            out.append((i, k))
"""

OLD_P3 = (
    '    "- 【v4.283.0 汇报纪律】汇报执行结果必须引用**本会话**的工具记录（证据编号）；"\n'
    '    "禁止声称「没写文件 / 没执行 / 已跳过 / 按新规矩没落盘」——除非本会话工具记录里"\n'
    '    "确有对应的取消或失败证据。看不到其他会话（含定时任务会话）的执行事实时，"\n'
    '    "明说「我看不到那个会话的记录」，可建议查日志核实，**禁止凭印象编造**。"\n'
    '    "落盘路径以用户或任务正文指定为准，不要擅自另写副本到产物目录或其他路径。\\n"\n'
)
NEW_P3 = ""

CASES = [
    ("P1 寒暄判据接线摘除", F_GUARD, OLD_P1, NEW_P1,
     ["A1 寒暄被拦（H吗.）", "C1 原案 msg138「H吗.」禁工具"]),
    ("P2 否认授义词表清空", F_GUARD, OLD_P2, NEW_P2,
     ["B1 否认授意判否定（我没有叫你写口播啊）",
      "C2 原案 msg146 禁工具且 negated"]),
    ("P3 汇报纪律从句删除", F_CONFIG, OLD_P3, NEW_P3,
     ["D1 汇报纪律已写入 AGENT_SYS_APPEND"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_GUARD, F_CONFIG])
    origs = {}
    for path in (F_GUARD, F_CONFIG):
        with open(path, "r") as fh:
            origs[path] = fh.read()
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            orig = origs[target]
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
        for path, orig in origs.items():
            with open(path, "w") as fh:
                fh.write(orig)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
