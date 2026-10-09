# -*- coding: utf-8 -*-
"""v4.241.0 扰动脚本：「多步指令 DAG 化」相B（agent_loop PLAN 接入 dag）。

反向照妖镜：变异 agent_loop.py 的 dag 分支（should_plan 门 / build_plan 依赖边 /
plan_instruction 不得跳步），跑 tests/test_intent_dag_239.py，确认 D6 必红
（改坏必红），且 D1-D5（相A，只测 intent_dag）不受影响仍绿。三个 case 全部针对
修复本体，零哑弹。

沿用已验证的「直接变异 + _perturb_guard 还原」模式（与 _perturb_intent_dag_239.py 同构）：
  · 变异由 _perturb_guard 三重还原（SIGTERM/SIGINT/atexit），被强杀也能还原，不留半截变异体。
  · 判据翻红的判据：退出码非 0 且 stdout/stderr 含 FAIL 字样。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
TARGET = os.path.join(ROOT, "agent_loop.py")
JUDGE = os.path.join(ROOT, "tests", "test_intent_dag_239.py")

# ---- 精确变异点（与源码 1:1 对齐，已用 Read 工具核对唯一性）----

# case 1：should_plan 的 dag 门失效（单工具 dag 不再注入计划）→ D6-8 翻红
OLD_V1 = ("        if dag is not None and getattr(dag, \"nodes\", None):\n"
          "            return True")
NEW_V1 = ("        if False:  # 变异：should_plan dag 门失效\n"
          "            return True")

# case 2：build_plan 的 dag 依赖边失效（退回扁平清单）→ D6-2/D6-3/D6-4 翻红
OLD_V2 = ("        if dag is not None and getattr(dag, \"nodes\", None):\n"
          "            g = str(goal or \"\").strip()")
NEW_V2 = ("        if False:  # 变异：build_plan 依赖边失效\n"
          "            g = str(goal or \"\").strip()")

# case 3：plan_instruction 的「不得跳步」约束失效 → D6-6 翻红
OLD_V3 = ("        if dag is not None and getattr(dag, \"nodes\", None):\n"
          "            _has_save = any(n.tool == \"write_file\" for n in dag.nodes)")
NEW_V3 = ("        if False:  # 变异：plan_instruction 不得跳步失效\n"
          "            _has_save = any(n.tool == \"write_file\" for n in dag.nodes)")

CASES = [
    ("V1 should_plan dag 门失效", OLD_V1, NEW_V1),
    ("V2 build_plan 依赖边失效", OLD_V2, NEW_V2),
    ("V3 plan_instruction 不得跳步失效", OLD_V3, NEW_V3),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([TARGET])  # 三重还原 + 残留预检
    src = open(TARGET, "r", encoding="utf-8").read()
    total = 0
    hits = 0
    try:
        for name, old, new in CASES:
            total += 1
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            # 断言只变异一处
            assert src.count(old) == 1, "old 串出现 %d 次，非唯一" % src.count(old)
            mutated = src.replace(old, new, 1)
            open(TARGET, "w", encoding="utf-8", newline="").write(mutated)
            rc, out = run_judge()
            # 判据应翻红：退出码非 0 且含 FAIL 字样
            failed = (rc != 0) and ("FAIL" in out)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            # 还原（guard 也会在退出时再还原一次）
            open(TARGET, "w", encoding="utf-8", newline="").write(src)
    finally:
        open(TARGET, "w", encoding="utf-8", newline="").write(src)  # 最终兜底还原
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
