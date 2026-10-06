# -*- coding: utf-8 -*-
"""v4.222 P1 任务完成验收：产物级校验。

审查报告 P1 指出：_infer_outcome 把「调过工具」近似当成「任务成功」
（if _tool_calls==0: partial; else: success），缺产物级验证，导致
「模型说写完了但文件没生成」被记入成功轨迹。

修复：_infer_outcome 末段增加产物级验收——本轮声明了交付物(deliverables)
就必须真实落地（文件存在且非空），否则降级 partial。

判据：
  A 产物真实落地 → success
  B 声明产物但未落地 → partial（不再 success）
  C 无交付物（老行为）→ success（兜底不被破坏）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent import AgentWorker, _deliverable_satisfied  # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


def _mk(tool_calls, deliverables):
    w = AgentWorker.__new__(AgentWorker)
    w._exit_flags = {"tool_calls": tool_calls}
    w._token_budget_hit = False
    w._stop_requested = False
    w._deliverables = deliverables
    return w


def main():
    print("-- P1 任务完成验收（产物级） --")
    _real = os.path.abspath(__file__)  # 真实存在且非空的产物

    # A：产物真实落地 → success
    out_a = _mk(2, [(_real, "file", "x")])._infer_outcome(steps=5, max_steps=10, duration_s=1)
    check("A 声明产物真实落地 → success", out_a == "success", "got=%r" % out_a)

    # B：声明产物但未落地 → partial（不再 success）
    out_b = _mk(2, [("/nonexistent_path_xyz/no_such_file.txt", "file", "x")])._infer_outcome(
        steps=5, max_steps=10, duration_s=1)
    check("B 声明产物未落地 → partial（不再 success）", out_b == "partial", "got=%r" % out_b)

    # C：无交付物（老行为）→ success（兜底不被破坏）
    out_c = _mk(2, [])._infer_outcome(steps=5, max_steps=10, duration_s=1)
    check("C 无交付物 → success（老行为兜底不变）", out_c == "success", "got=%r" % out_c)

    # 辅助函数单测
    check("辅助 _deliverable_satisfied：真实文件=True", _deliverable_satisfied((_real, "file", "x")))
    check("辅助 _deliverable_satisfied：不存在=False",
          not _deliverable_satisfied(("/nope_xyz.txt", "file", "x")))
    check("辅助 _deliverable_satisfied：url 类=True（不误杀）",
          _deliverable_satisfied(("https://example.com/x.png", "image", "x")))

    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
