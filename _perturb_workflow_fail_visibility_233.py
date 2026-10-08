# -*- coding: utf-8 -*-
"""断点 E 扰动脚本（v4.233）：任务图内部失败/跳过仍谎报「✅ 任务图完成」。

反向照妖镜：变异 agent.py 的 _finalize_workflow 修复点，跑
test_workflow_fail_visibility_233.py，确认判据必红（改坏必红）。两个 case 全部
针对修复本体，零哑弹。沿用 D 点已验证的「直接变异 + _perturb_guard 还原」模式。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
TARGET = os.path.join(ROOT, "agent.py")
JUDGE = os.path.join(ROOT, "tests", "test_workflow_fail_visibility_233.py")

# ---- 精确变异点（与源码 1:1 对齐，已用 Read 工具核对唯一性）----

# case 1：删掉整个「未完整」分支 → 退回「无论节点终态一律 ✅ 任务图完成」，
# 即断点 E 原 bug（内部 failed/skipped 被误报为完成，主账本无感知）。
OLD_V1 = """        if _failed or _skipped:
            _n = len(_failed) + len(_skipped)
            self._emit_status(
                "\u26a0 任务图未完整达成：%d 个节点未成功（失败 %d / 跳过 %d）"
                % (_n, len(_failed), len(_skipped)))
            self._workflow_incomplete = {"failed": _failed, "skipped": _skipped}
            # 回写主账本（若有）—— fail-open：异常不影响主流程
            try:
                _ts = getattr(self, "_tstate", None)
                if _ts is not None and hasattr(_ts, "note_workflow_incomplete"):
                    _ts.note_workflow_incomplete(_failed, _skipped)
            except Exception:
                pass
            return output"""
NEW_V1 = """"""

# case 2：保留未完整分支，但把发出的状态改回「✅ 任务图完成」——表面仍有
# _workflow_incomplete 标记，却对用户谎报完成，等效于 E bug 的「只记不报」变体。
OLD_V2 = """            self._emit_status(
                "\u26a0 任务图未完整达成：%d 个节点未成功（失败 %d / 跳过 %d）"
                % (_n, len(_failed), len(_skipped)))"""
NEW_V2 = """            self._emit_status("\u2705 任务图完成")"""

CASES = [
    ("V1 删整个未完整分支（退回一律✅完成）", OLD_V1, NEW_V1, ["E1", "E2"]),
    ("V2 未完整分支仍发「✅ 任务图完成」（只记不报）", OLD_V2, NEW_V2, ["E1", "E2"]),
]


def run_judge():
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([TARGET])  # 三重还原 + 残留预检
    src = open(TARGET, "r", encoding="utf-8").read()
    total = 0
    hits = 0
    try:
        for name, old, new, expect in CASES:
            total += 1
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            # 断言只变异一处
            assert src.count(old) == 1, "old 串出现 %d 次，非唯一" % src.count(old)
            mutated = src.replace(old, new, 1)
            open(TARGET, "w", encoding="utf-8").write(mutated)
            rc, out = run_judge()
            # 判据应翻红：退出码非 0 且含 FAIL 字样（覆盖 E1/E2 的 FAIL=）
            failed = (rc != 0) and ("FAIL" in out or any(t in out for t in expect))
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            # 还原（guard 也会在退出时再还原一次）
            open(TARGET, "w", encoding="utf-8").write(src)
    finally:
        open(TARGET, "w", encoding="utf-8").write(src)  # 最终兜底还原
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
