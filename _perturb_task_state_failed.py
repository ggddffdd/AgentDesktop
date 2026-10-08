# -*- coding: utf-8 -*-
"""A 断点扰动脚本（v4.231）：账本把"调过"当"做成"。

反向照妖镜：变异 task_state.py 的修复点，跑 test_task_state_failed_pending.py，
确认判据必红（改坏必红）。三个 case 全部针对修复本体，绝不引入死循环。

沿用 B 点已验证的「直接变异 + _perturb_guard 还原」模式。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
TARGET = os.path.join(ROOT, "task_state.py")
JUDGE = os.path.join(ROOT, "tests", "test_task_state_failed_pending.py")

# ---- 精确变异点（与源码 1:1 对齐，已用 Edit 工具验证唯一性）----

OLD_V1 = """        # v4.231 A 断点修复：失败的"点名工具"也算未达标，否则账本会
        # 把"调过但失败"误判成"已完成"（静默收尾、不 nudge、不重试）。
        # 限定 required_tools 且排除已 succeeded（先败后成归正）的，避免对非点名
        # 的自主失败误 nudge；防死循环由 should_nudge 的 injected+max_steps 闸覆盖。
        for t in self.failed_tools:
            if t in self.required_tools and t not in self.succeeded_tools:
                out.append("工具 %s 上次调用失败，需要重试或换方案" % t)
        return out"""
NEW_V1 = """        return out"""

OLD_V2 = """        if ok:
            if name not in self.succeeded_tools:
                self.succeeded_tools.append(name)
            if name in self.failed_tools:  # 先败后成：状态归正，避免遗留失败标记
                self.failed_tools.remove(name)"""
NEW_V2 = """        pass"""

OLD_V3 = """            if t in self.required_tools and t not in self.succeeded_tools:"""
NEW_V3 = """            if t not in self.succeeded_tools:"""

CASES = [
    ("V1 删 pending 的 failed_tools 维度（核心回归）", OLD_V1, NEW_V1,
     ["pending_includes_failed", "should_nudge_triggers", "resume_can_re_nudge", "pending_names_failure"]),
    ("V2 删 record_tool 的 ok 分支（先败后成不归正）", OLD_V2, NEW_V2,
     ["success_after_failure_clears"]),
    ("V3 删 required_tools 限定（非点名失败污染账本）", OLD_V3, NEW_V3,
     ["non_required_failure_not_pollutes"]),
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
            # 判据应翻红：退出码非 0 且含 FAIL 字样（覆盖 FAILPENDING_PASS/FAIL=）
            failed = (rc != 0) and ("FAIL" in out or any(t in out for t in expect))
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                # 把判据输出带出来方便排查
                print("---- judge output ----\n" + out[:1500])
            # 还原（guard 也会在退出时再还原一次）
            open(TARGET, "w", encoding="utf-8").write(src)
    finally:
        open(TARGET, "w", encoding="utf-8").write(src)  # 最终兜底还原
    # 注：_perturb_guard 无 disarm——arm 时注册的 atexit/signal 会在进程退出时
    # 自动还原，finally 此处也已手动还原，故无需也不能调用不存在的 disarm。
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
