# -*- coding: utf-8 -*-
"""扰动验证：#2 工程卫生 ⑧（回归期日志改道）判据「改坏必红」。

逐个拆掉 `tests/test_log_redirect_hyg.py` 所守的写法，看对应判据是否真转红。

⚠️ 与 C 批不同：本判据直接读 `tests/run_all.py` / `tests/test_workspace_routing.py`
**真路径**（不像 C 批支持 *_PATH 覆盖），故走**就地改 + guard 还原**模式，
绝不能与全量回归并行跑（就改同一批源码）。

⚠️ PH1 / PH2 拆掉改道后，判据 D 组的探针会**真的写一行**到用户的
`~/Documents/小臭玩AI/debug.log` —— 那正是它要证明的事。脚本收尾把该文件
**按字节还原**（前提：无其它进程在写它；跑前会打印提示与原始大小）。

用法：python _perturb_log_redirect_hyg.py
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_log_redirect_hyg.py")
RA = os.path.join(ROOT, "tests", "run_all.py")
WR = os.path.join(ROOT, "tests", "test_workspace_routing.py")

sys.path.insert(0, HERE)
import _perturb_guard as _guard  # noqa: E402
_guard.arm([RA, WR])

REAL_LOG = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "debug.log")
_LOG0 = None
if os.path.exists(REAL_LOG):
    _LOG0 = open(REAL_LOG, "rb").read()

# 注入行的精确原文（PH1 删 / PH4 改）
INJECT_LINE = '    env.setdefault("XC_LOG_DIR", TEST_LOG_DIR)   # 套件日志→临时目录，不污染用户真实 debug.log\n'
TLOG_LINE = 'TEST_LOG_DIR = os.path.join(tempfile.gettempdir(), "dsb_test_logs")'
POP_LINE = 'os.environ.pop("XC_LOG_DIR", None)\n'

# (变异名, 必须红的判据项, 目标文件, 原串, 替换为)
CASES = [
    ("PH1", {"A2", "C1", "D2"}, RA, INJECT_LINE, ""),  # 整行删掉注入

    ("PH2", {"A5", "C3", "D2"}, RA, TLOG_LINE,
     'TEST_LOG_DIR = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI")  # 扰动：改道到真实目录'),

    ("PH3", {"A7", "A8"}, WR, POP_LINE, "pass  # 扰动：不 pop，测的成了「改道后」而非「默认」\n"),

    ("PH4", {"C6"}, RA, INJECT_LINE,
     '    env["XC_LOG_DIR"] = TEST_LOG_DIR   # 扰动：直接赋值，覆盖外部设定\n'),
]

PASS_N = 0
FAIL_N = 0


def run_judge():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([PY, JUDGE], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env, cwd=ROOT)
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    red = set(re.findall(r"\[FAIL\]\s+([A-Z]\d+)", out))
    return r.returncode, red, out


def mutate(fp, old, new):
    orig = open(fp, encoding="utf-8").read()
    if orig.count(old) != 1:
        return None, orig
    return orig.replace(old, new, 1), orig


def main():
    global PASS_N, FAIL_N
    if _LOG0 is not None:
        print("[提示] 真实 debug.log 原始 %d 字节；PH1/PH2 会写入探针标记，收尾按字节还原。" % len(_LOG0))
    print()

    for name, expect, fp, old, new in CASES:
        mutated, orig = mutate(fp, old, new)
        if mutated is None:
            FAIL_N += 1
            print("[FAIL] %s 锚点未命中（%s）：判据失效或源码已漂移" % (name, os.path.basename(fp)))
            continue
        try:
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(mutated)
            rc, red, out = run_judge()
        finally:
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(orig)

        ok = expect <= red
        if ok:
            PASS_N += 1
            print("[OK  ] %s 命中：期望红 %s ⊆ 实红 %s" % (name, sorted(expect), sorted(red)))
        else:
            FAIL_N += 1
            miss = sorted(expect - red)
            print("[FAIL] %s 哑弹：期望红 %s，实际只红 %s（缺 %s）" % (name, sorted(expect), sorted(red), miss))
            print("-" * 60)
            print(out[-1800:])
            print("-" * 60)

    # 反向基线：原文件跑判据应全绿
    rc, red, out = run_judge()
    if red or rc != 0:
        FAIL_N += 1
        print("[FAIL] 反向基线：原文件下判据应全绿，实际红 %s（rc=%d）" % (sorted(red), rc))
        print(out[-1500:])
    else:
        PASS_N += 1
        print("[OK  ] 反向基线：原文件下判据全绿（判据不过宽）")

    # 还原真实 debug.log
    if _LOG0 is not None:
        try:
            cur = open(REAL_LOG, "rb").read()
            if cur != _LOG0:
                with open(REAL_LOG, "wb") as f:
                    f.write(_LOG0)
                print("[还原] 真实 debug.log %d → %d 字节" % (len(cur), len(_LOG0)))
            else:
                print("[还原] 真实 debug.log 未被改动")
        except Exception as e:                                     # noqa: BLE001
            print("[还原] !! 失败：%s" % e)

    print()
    print("PERTURB PASS=%d FAIL=%d" % (PASS_N, FAIL_N))
    sys.exit(0 if FAIL_N == 0 else 1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
