# -*- coding: utf-8 -*-
"""v4.246.0 扰动脚本：军团「组队对话化」。

反向照妖镜：变异五处修复点，跑 tests/test_legion_team_chat.py，确认判据必红（改坏必红）。
五个 case 分别针对组队识别/排版/审批状态机，零哑弹。沿用「直接变异 + _perturb_guard 还原」模式。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
LEGION = os.path.join(ROOT, "legion.py")
CHAT = os.path.join(ROOT, "legion_chat.py")
JUDGE = os.path.join(ROOT, "tests", "test_legion_team_chat.py")

CASES = [
    # V1：组队识别失效 → 判据「组队·组个团队」等正向全红
    ("V1 组队识别失效", LEGION,
     "            if need.startswith(\"的\"):   # 「组队的事我再想想」→ 不是命令\n"
     "                return None\n"
     "            return need",
     "            if need.startswith(\"的\"):   # 「组队的事我再想想」→ 不是命令\n"
     "                return None\n"
     "            return None  # 扰动：组队识别失效", False),
    # V2：阵容标题被改 → 判据「排版·含阵容」翻红
    ("V2 阵容标题被改", LEGION,
     '    lines.append("② 阵容编排")',
     '    lines.append("② 阵容")  # 扰动：标题被改', False),
    # V3：空理解打回提示被删 → 判据「排版·空理解提示打回」翻红
    ("V3 空理解打回提示被删", LEGION,
     'lines.append("   " + (und or "（PM 没写理解 —— 建议打回重来）"))',
     'lines.append("   " + (und or "（PM 没写理解 —— 建议重来）"))  # 扰动：提示被删', False),
    # V4：组队审批分支失效 → 判据「审批·批准→pass / 打回→reject」翻红
    ("V4 组队审批分支失效", CHAT,
     "        if self._pending_team_plan is not None:",
     "        if False:  # 扰动：组队审批失效", False),
    # V5：组队命令识别失效 → 判据「组队命令→builder回调」翻红
    ("V5 组队命令识别失效", CHAT,
     "        _need = legion.parse_team_build_intent(text)\n        if _need:",
     "        _need = legion.parse_team_build_intent(text)\n        if False:  # 扰动：组队命令失效", False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [LEGION, CHAT]
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
