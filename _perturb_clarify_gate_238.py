# -*- coding: utf-8 -*-
"""v4.239.0 扰动脚本：「歧义澄清」闸门（intent.py 检测块）。

反向照妖镜：变异 intent.py 的 needs_clarification 检测逻辑，跑
tests/test_clarify_gate_238.py，确认判据必红（改坏必红）。三个 case 全部针对
修复本体，零哑弹。沿用已验证的「直接变异 + _perturb_guard 还原」模式。

设计纪律（与 MEMORY.md 同源）：
  · 变异标记不用 `# 扰动`（本块是纯逻辑，由 _perturb_guard 直接字符串替换 + 还原）。
  · 三重还原：SIGTERM / SIGINT / atexit，被强杀也能还原，不留半截变异体。
  · 判据翻红的判据：退出码非 0 且 stdout/stderr 含 FAIL 字样。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
TARGET = os.path.join(ROOT, "intent.py")
JUDGE = os.path.join(ROOT, "tests", "test_clarify_gate_238.py")

# ---- 精确变异点（与源码 1:1 对齐，已用 Read 工具核对唯一性）----

# case 1：整段闸门失效（条件恒 False）→ CL1 正例不再澄清 → 判据翻红
OLD_V1 = "if kind == KIND_ACTION and not force and len(req) >= 2:"
NEW_V1 = "if False:"

# case 2：闸门恒开（条件恒 True）→ CL4 反例（疑问/否定/单工具…）被误澄清 → 判据翻红
OLD_V2 = "if kind == KIND_ACTION and not force and len(req) >= 2:"
NEW_V2 = "if True:"

# case 3：澄清选项被清空（clarify_options 恒空）→ CL2 选项非空断言翻红
OLD_V3 = "clarify_options = tuple(_opts)"
NEW_V3 = "clarify_options = ()"

CASES = [
    ("V1 检测条件恒 False（闸门整体失效）", OLD_V1, NEW_V1),
    ("V2 检测条件恒 True（误触发面全开）", OLD_V2, NEW_V2),
    ("V3 澄清选项恒空（CL2 字段脱节）", OLD_V3, NEW_V3),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
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
