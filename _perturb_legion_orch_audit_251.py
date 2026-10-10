# -*- coding: utf-8 -*-
"""v4.251.0 扰动脚本：编排页两处硬伤修复。

反向照妖镜：变异两处修复点，跑 tests/test_legion_orch_audit_251.py，确认判据必红（改坏必红）。
V1 授权切页改回角色库；V2 越界守卫失效。零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
UI = os.path.join(ROOT, "legion_ui.py")
JUDGE = os.path.join(ROOT, "tests", "test_legion_orch_audit_251.py")

CASES = [
    # V1：授权切页改回角色库(idx1) → 判据「授权·自动切到聊天页(idx0)」翻红
    ("V1 授权切页回角色库", UI,
     "导致授权到来时把人带到角色库而非能看到「放行/打回/终止」的对话流。\n"
     "                self._switch_page(0)",
     "导致授权到来时把人带到角色库而非能看到「放行/打回/终止」的对话流。\n"
     "                self._switch_page(1)  # 扰动：切回角色库", False),
    # V2：越界守卫失效 → 判据「添加成员·越界波次不崩」翻红
    ("V2 越界守卫失效", UI,
     "        if not (0 <= wi < len(p.get(\"waves\") or [])):\n"
     "            return",
     "        if False:  # 扰动：越界守卫失效\n"
     "            return", False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [UI]
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
