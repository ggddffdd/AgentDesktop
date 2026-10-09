# -*- coding: utf-8 -*-
"""v4.243.0 扰动脚本：审查报告第一批修复（I-1 / I-2 / T-1）。

反向照妖镜：变异三处修复点，跑 tests/test_review_fix_243.py，确认判据必红（改坏必红）。
三个 case 分别针对 I-1（委托失效）、I-2（退回裸词表）、T-1（失败前缀失效），零哑弹。
沿用「直接变异 + _perturb_guard 还原」模式；变异标记统一用 `# 扰动：`（guard 约定）。

设计纪律（与 MEMORY.md 同源）：
  · 变异由本脚本直接字符串替换，_perturb_guard.arm() 快照 + 三重还原兜底。
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
AGENT_TEXT = os.path.join(ROOT, "agent_text.py")
TOOL_CONTRACT = os.path.join(ROOT, "tool_contract.py")
JUDGE = os.path.join(ROOT, "tests", "test_review_fix_243.py")

# ---- 精确变异点（与三处修复 1:1 对齐，已用 grep 核对唯一性）----

# case 1（I-1）：_neg_hit 委托失效 → 短喊停「取消生成视频」不再被拦 → 判据翻红
OLD_V1 = "        return _ig.is_negation(text)"
NEW_V1 = "        return False  # 扰动：委托失效"

# case 2（I-2）：_route_force_tool 退回裸词表 → 「状态拉满/项目进度」重新被拦 → 判据翻红
OLD_V2 = "    if _is_status_query(text):"
NEW_V2 = "    if any(k in text for k in _STATUS_KW):  # 扰动：退回裸词表"

# case 3（T-1）：失败前缀失效 → 「未知工具/内容为空」等重新判成功 → 判据翻红
OLD_V3 = "    if s.startswith(_EXTRA_FAILURE_PREFIXES):"
NEW_V3 = "    if False:  # 扰动：失败前缀失效"

CASES = [
    ("V1 I-1 委托失效（_neg_hit 不再走 is_negation）", AGENT_TEXT, OLD_V1, NEW_V1),
    ("V2 I-2 退回裸词表（_is_status_query 不再生效）", AGENT_TEXT, OLD_V2, NEW_V2),
    ("V3 T-1 失败前缀失效（_EXTRA_FAILURE_PREFIXES 不再判失败）", TOOL_CONTRACT, OLD_V3, NEW_V3),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([AGENT_TEXT, TOOL_CONTRACT])
    snaps = {}
    for fp in (AGENT_TEXT, TOOL_CONTRACT):
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    try:
        for name, fp, old, new in CASES:
            total += 1
            src = snaps[fp]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            # 断言只变异一处
            assert src.count(old) == 1, "old 串在 %s 出现 %d 次，非唯一" % (fp, src.count(old))
            mutated = src.replace(old, new, 1)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(mutated)
            rc, out = run_judge()
            # 判据应翻红：退出码非 0 且含 FAIL 字样
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
