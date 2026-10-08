# -*- coding: utf-8 -*-
"""断点 E 判据（v4.233）：任务图内部有 failed/skipped 节点时，_run_workflow 不得谎报「✅ 任务图完成」。

根因：tg.run() 只回节点输出字典、不含节点 status。_run_workflow 收尾永远
self._emit_status("✅ 任务图完成") 并返回 output，从不看节点终态 →
工作流内部失败被误报为「完成」，主账本 task_state 无感知、不 nudge。

改法（轻档）：收尾逻辑抽成 _finalize_workflow(tg, output)——
  读 tg.task_list() 的节点 status；
  含 failed/skipped → 发「⚠ 任务图未完整达成：N 节点未成功」且设
    self._workflow_incomplete（主账本经 TaskState.note_workflow_incomplete 感知）；
  全 completed → 才发「✅ 任务图完成」。

judge-first：本文件在改源码前应为 FAIL（_finalize_workflow 不存在 / 行为未修）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent import AgentWorker  # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  ✅ %s" % name)
    else:
        _f += 1
        print("  ❌ %s  %s" % (name, detail))


class _FakeTg:
    """只提供 task_list()，STATUS 由构造参数给定，免去真实节点执行。"""
    def __init__(self, statuses):
        # statuses: list of (id, status)
        self._list = [{"id": i, "subject": i, "status": s, "blockedBy": []}
                      for i, s in statuses]

    def task_list(self):
        return list(self._list)


def _make_worker():
    w = AgentWorker.__new__(AgentWorker)
    w.messages = [{"role": "user", "content": "写一份报告"}]
    w.mw = None
    w._tstate = None
    w._tstate_nudged = False
    w._workflow_incomplete = None
    _emitted = []
    w._emit_status = lambda s: _emitted.append(s)
    w._emitted = _emitted
    return w


def main():
    # E1（核心）：含 failed 节点 → 如实上报未完整、不得谎报✅完成
    w = _make_worker()
    tg = _FakeTg([("search", "completed"), ("analyze", "completed"), ("write", "failed")])
    w._finalize_workflow(tg, "部分产出")
    _ok1 = (w._workflow_incomplete is not None
            and "write" in w._workflow_incomplete.get("failed", [])
            and any("未完整" in s for s in w._emitted)
            and not any("✅ 任务图完成" in s for s in w._emitted))
    check("E1 含 failed 节点：如实上报未完整、不谎报✅完成", _ok1,
          "inc=%r emitted=%r" % (w._workflow_incomplete, w._emitted))

    # E2：含 skipped 节点 → 同样不得谎报（B 点修复后 failed 下游会标 skipped）
    w = _make_worker()
    tg = _FakeTg([("search", "failed"), ("analyze", "skipped"), ("write", "skipped")])
    w._finalize_workflow(tg, "")
    _ok2 = (w._workflow_incomplete is not None
            and "analyze" in w._workflow_incomplete.get("skipped", [])
            and any("未完整" in s for s in w._emitted)
            and not any("✅ 任务图完成" in s for s in w._emitted))
    check("E2 含 skipped 节点：如实上报未完整、不谎报✅完成", _ok2,
          "inc=%r emitted=%r" % (w._workflow_incomplete, w._emitted))

    # E3：全 completed → 正常报完成，且不标 incomplete
    w = _make_worker()
    tg = _FakeTg([("search", "completed"), ("analyze", "completed"), ("write", "completed")])
    w._finalize_workflow(tg, "完整报告")
    _ok3 = (w._workflow_incomplete is None
            and any("✅ 任务图完成" in s for s in w._emitted))
    check("E3 全 completed：正常报✅完成且未标 incomplete", _ok3,
          "inc=%r emitted=%r" % (w._workflow_incomplete, w._emitted))

    # E4：failed+skipped 计数正确
    w = _make_worker()
    tg = _FakeTg([("search", "failed"), ("analyze", "skipped"), ("write", "completed")])
    w._finalize_workflow(tg, "")
    inc = w._workflow_incomplete or {}
    _ok4 = (len(inc.get("failed", [])) == 1 and len(inc.get("skipped", [])) == 1
            and inc.get("failed") == ["search"] and inc.get("skipped") == ["analyze"])
    check("E4 failed+skipped 节点计数正确", _ok4, "inc=%r" % inc)

    # E5：主账本感知 —— _tstate 经 note_workflow_incomplete 记录
    import task_state as ts  # noqa: E402
    w = _make_worker()
    w._tstate = ts.TaskState()
    tg = _FakeTg([("search", "completed"), ("analyze", "failed"), ("write", "skipped")])
    w._finalize_workflow(tg, "")
    _ts_inc = getattr(w._tstate, "workflow_incomplete", None)
    _ok5 = (_ts_inc is not None
            and _ts_inc.get("failed") == ["analyze"]
            and _ts_inc.get("skipped") == ["write"])
    check("E5 主账本感知：_tstate 记录 workflow_incomplete", _ok5,
          "tstate.inc=%r" % _ts_inc)

    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
