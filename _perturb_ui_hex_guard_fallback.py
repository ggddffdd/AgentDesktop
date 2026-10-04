# -*- coding: utf-8 -*-
"""扰动验证：`tests/test_ui_hex_guard_fallback.py` 的判据「改坏必红」。

逐个拆掉「THEME 兜底豁免」所守的写法，看对应判据是否真转红。
变异副本写临时文件 + `UIHG_PATH` 指向它（判据支持该覆盖点），**不在原文件动刀**。

护栏（`_perturb_guard.arm`）照接 —— 虽然本脚本只改副本，但它仍往 **ROOT** 落
`_uihg_mut_*.py`（命中护栏的 `*_mut_*.py` scratch 约定），所以三件事都真需要：
  ① 起手清掉上次被强杀残留的副本（残留副本里带 `# 扰动：` 注释，还在 ROOT 顶层，
     会被 `find_leftovers()` 当成「源码残留变异」——那正是让整轮扰动中止的形态）；
  ② atexit 兜底清本次新建的副本（Windows TerminateProcess 不可捕获，finally 不执行）；
  ③ 残留标记预检，顺带证明「原文件一字未动」这个前提成立。

三个方向：
  PU1 拆掉豁免动作（`if i in fallback` → `if False`）—— 兜底又变回 SOFT 警告
  PU2 让豁免**过宽**（把整个文件都算兜底）—— 普通裸 hex 也会被吞（判据 C 组该红）
  PU3 让识别器变哑（恒返回空集）—— 等价于没做豁免

用法：python _perturb_ui_hex_guard_fallback.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_ui_hex_guard_fallback.py")
SRC = os.path.join(ROOT, "ui_hex_guard.py")

sys.path.insert(0, HERE)
import _perturb_guard as _guard  # noqa: E402
_guard.arm([SRC])   # 快照原文件 + 三重还原 + 起手清 scratch / 查残留标记

BASE = open(SRC, encoding="utf-8").read()

# (变异名, 必须红的判据项, 原串, 替换为)
CASES = [
    ("PU1", {"B2", "B4", "B6", "C1"},
     "            if i in fallback:\n"
     "                fallback_hits += len(HEX.findall(line))\n"
     "                continue\n",
     "            if False:  # 扰动：不豁免兜底\n"
     "                fallback_hits += len(HEX.findall(line))\n"
     "                continue\n"),

    ("PU2", {"C2", "C3"},
     "        fallback = _theme_fallback_spans(src)",
     "        fallback = set(range(1, 10 ** 6))  # 扰动：豁免过宽（整个文件都算兜底）"),

    ("PU3", {"B2", "B4", "C1"},
     "    spans = set()\n"
     "    try:\n"
     "        tree = ast.parse(src)\n"
     "    except Exception:\n"
     "        return spans\n",
     "    spans = set()\n"
     "    if True:  # 扰动：识别器恒返回空\n"
     "        return spans\n"
     "    try:\n"
     "        tree = ast.parse(src)\n"
     "    except Exception:\n"
     "        return spans\n"),
]

PASS_N = 0
FAIL_N = 0


def run_judge(src_text):
    fd, p = tempfile.mkstemp(prefix="_uihg_mut_", suffix=".py", dir=ROOT)
    os.close(fd)
    try:
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(src_text)
        env = dict(os.environ, UIHG_PATH=p, QT_QPA_PLATFORM="offscreen",
                   PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        r = subprocess.run([PY, JUDGE], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600, env=env, cwd=ROOT)
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        return set(re.findall(r"\[FAIL\]\s+([A-Z]\d+)", out)), out
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


def main():
    global PASS_N, FAIL_N
    for name, expect, old, new in CASES:
        if BASE.count(old) != 1:
            FAIL_N += 1
            print("[FAIL] %s 锚点命中 %d 次（要求 1）：判据失效或源码漂移" % (name, BASE.count(old)))
            continue
        red, out = run_judge(BASE.replace(old, new, 1))
        if expect <= red:
            PASS_N += 1
            print("[OK  ] %s 命中：期望红 %s ⊆ 实红 %s" % (name, sorted(expect), sorted(red)))
        else:
            FAIL_N += 1
            print("[FAIL] %s 哑弹：期望红 %s，实际只红 %s（缺 %s）"
                  % (name, sorted(expect), sorted(red), sorted(expect - red)))
            print("-" * 60)
            print(out[-1600:])
            print("-" * 60)

    # 反向基线：原文件跑判据应全绿
    red, out = run_judge(BASE)
    if red:
        FAIL_N += 1
        print("[FAIL] 反向基线：原文件下应全绿，实际红 %s" % sorted(red))
        print(out[-1200:])
    else:
        PASS_N += 1
        print("[OK  ] 反向基线：原文件下判据全绿（判据不过宽）")

    print()
    print("PERTURB PASS=%d FAIL=%d" % (PASS_N, FAIL_N))
    sys.exit(0 if FAIL_N == 0 else 1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
