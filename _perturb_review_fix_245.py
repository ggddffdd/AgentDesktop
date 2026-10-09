# -*- coding: utf-8 -*-
"""v4.245.0 扰动脚本：审查报告第三批修复（T-3/T-6/T-8/T-4/T-5/I-7）。

反向照妖镜：变异六处修复点，跑 tests/test_review_fix_245.py，确认判据必红（改坏必红）。
六个 case 分别针对六处修复，零哑弹。沿用「直接变异 + _perturb_guard 还原」模式。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
AGENT = os.path.join(ROOT, "agent.py")
TOOLS = os.path.join(ROOT, "tools.py")
AGENT_TEXT = os.path.join(ROOT, "agent_text.py")
JUDGE = os.path.join(ROOT, "tests", "test_review_fix_245.py")

CASES = [
    # T-3：删 _run_concurrent 的空批次保护 → 判据「_run_concurrent 有空批次保护」翻红
    ("V1 T-3 空批次保护被删", AGENT,
     "        if not tool_calls:\n            return\n        max_workers = min(len(tool_calls), 5)",
     "        max_workers = min(len(tool_calls), 5)  # 扰动：空批次保护删", False),
    # T-6：验证异常不留痕（两处都变异）→ 判据「后验验证异常有 log.warning 留痕」翻红
    ("V2 T-6 验证异常不留痕", TOOLS,
     'log.warning("后验验证异常 %s: %s", name, _ve)',
     "pass  # 扰动：验证异常不留痕", True),
    # T-8：缺时间不反问 → 判据「daily 缺时间返回反问」翻红
    ("V3 T-8 缺时间不反问", TOOLS,
     "    if not _has_time and st in (auto.SCHED_DAILY, auto.SCHED_WEEKLY):",
     "    if False:  # 扰动：缺时间不反问", False),
    # T-4：幂等豁免失效 → 判据「幂等读类豁免集合存在」翻红
    ("V4 T-4 幂等豁免失效", AGENT,
     "            if _nm in _IDEMPOTENT_READ:",
     "            if False:  # 扰动：幂等豁免失效", False),
    # T-5：第二道锁失效 → 判据「needs_user 未确认 → 拒绝」翻红
    ("V5 T-5 第二道锁失效", TOOLS,
     '        if getattr(perm_ctx, "needs_user", False) and not getattr(perm_ctx, "confirmed", False):',
     "        if False:  # 扰动：第二道锁失效", False),
    # I-7：单字复活 → 判据「快看这个 False」翻红
    ("V6 I-7 单字复活", AGENT_TEXT,
     '    "行动", "加速",',
     '    "行动", "加速", "快", "动",  # 扰动：单字复活', False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [AGENT, TOOLS, AGENT_TEXT]
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
