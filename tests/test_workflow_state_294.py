# -*- coding: utf-8 -*-
"""v4.289.4：工作流节点失败必须进入主账本与机器小结。

背景（2026-10-27 核实）：`_finalize_workflow` 只写 `self._workflow_incomplete` 与
`TaskState.workflow_incomplete`，而 `pending()/is_complete()/verify_report()/
build_summary()` 全都不读它 —— 该字段是纯装饰；`run_workflow` 分支也不走
`_handle_tool_result`，账本连「这轮跑过工作流」都不知道；失败且输出为空时
tool message 还写「（工作流已完成，无文本输出）」。

纯标准库，不联网；日志/数据改道临时目录。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_294_wf_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")
os.environ["XC_LEGION_DIR"] = os.path.join(_SBX, "legion")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import agent
import agent_loop as loop
import task_state as tsmod

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


class Sig:
    def __init__(self):
        self.ev = []

    def emit(self, *a, **k):
        self.ev.append(a)


class FakeTG:
    def __init__(self, statuses):
        self._s = statuses

    def task_list(self):
        return [{"id": k, "subject": k, "status": v} for k, v in self._s.items()]


def make_worker(tstate=None):
    w = agent.AgentWorker.__new__(agent.AgentWorker)
    w.messages = []
    for n in ("tool_started", "tool_finished", "tool_log", "status", "render",
              "deliverable_added", "stream_begin", "stream_chunk", "stream_commit",
              "done", "confirm_action", "schedule_reminder"):
        setattr(w, n, Sig())
    w._ctx_sid = "s1"
    w._stop_requested = False
    w._app_dir = _SBX
    w._tstate = tstate
    w._guard_blocked = False
    w._resume_done_hashes = set()
    w._recent_tool_sigs = []
    w._remember_calls = 0
    w._deliverables = []
    w._exec_ledger = []
    w._workflow_incomplete = None
    w.task_id = "t294wf"
    w._emit_status = lambda s: None
    w._next_tool_index = lambda: 1
    w._record_exec_ledger = lambda *a, **k: None
    w._handle_tool_result = agent.AgentWorker._handle_tool_result.__get__(
        w, agent.AgentWorker)
    return w


def tc(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class Dec:
    allowed = True
    needs_user = False
    reason = ""


class Engine:
    def decide(self, *a, **k):
        return Dec()


class MW:
    cfg = {}
    permission_engine = Engine()


import evidence  # noqa: E402
evidence.register = lambda *a, **k: None  # 不落证据库

# ---------------------------------------------------------------- W1-W4
print("=" * 66)
print("W 组：_finalize_workflow 如实上报")
print("=" * 66)

w = make_worker()
_out = w._finalize_workflow(FakeTG({"a": "failed", "b": "done"}), "部分正文")
check("W1 失败节点时返回文本带「未完整」标记，正文保留",
      "未完整" in _out and "部分正文" in _out, _out[:120])
check("W2 失败时不再出现「✅ 任务图完成」", "任务图完成" not in _out, _out[:120])

_out2 = w._finalize_workflow(FakeTG({"a": "failed", "b": "cancelled"}), "")
check("W3 空输出 + 未成功节点 → 返回非空且说明未完成",
      bool(_out2.strip()) and "未完整" in _out2, repr(_out2[:120]))
check("W4 空输出不再被写成「已完成，无文本输出」", "已完成" not in _out2, _out2[:120])

_out3 = w._finalize_workflow(FakeTG({"a": "done"}), "正文")
check("W5 全部成功时正文原样返回、不误报未完整",
      _out3 == "正文", repr(_out3))

# ---------------------------------------------------------------- W6-W11
print()
print("=" * 66)
print("W 组：主账本消费 workflow_incomplete")
print("=" * 66)

ts = tsmod.TaskState()
ts.required_tools = ["web_search"]
ts.record_tool("web_search", {"query": "x"}, "结果", ok=True)
ts.note_workflow_incomplete(["writer"], [], ["analyst"], ["researcher"])
_p = ts.pending()
check("W6 pending() 反映工作流未成功节点", bool(_p) and any("工作流" in x for x in _p), _p)
check("W7 is_complete() 不再误判为完成", ts.is_complete() is False, ts.is_complete())
_sum = loop.build_summary(ts) or ""
check("W8 build_summary 的「未完成」段写入工作流节点",
      "未完成：无" not in _sum and "工作流" in _sum, _sum.replace("\n", " | ")[:200])
_rep = loop.verify_report(ts)
check("W9 verify_report 记录工作流未满足",
      any("工作流" in x for x in _rep), _rep)

# 成功时清空，避免残留污染后续轮次
ts.ok_workflow() if hasattr(ts, "ok_workflow") else ts.note_workflow_incomplete(
    [], [], [], [])
check("W10 工作流成功后未达标清单清空（不留残留）",
      not ts.pending() and ts.is_complete() is True, ts.pending())

# ---------------------------------------------------------------- W11-W14
print()
print("=" * 66)
print("W 组：run_workflow 进主账本")
print("=" * 66)

ts2 = tsmod.TaskState()
w2 = make_worker(ts2)
w2._run_workflow_guarded = (
    lambda t, task, mw, a: agent.AgentWorker._finalize_workflow(
        w2, FakeTG({"writer": "failed"}), "部分正文"))
_wf = tc("wf1", "run_workflow", {"type": "research_write", "task": "写报告"})
w2._exec_tool_calls([_wf], MW(), str(ROOT))

check("W11 账本记录 run_workflow 被调用（不再整条绕过）",
      "run_workflow" in list(ts2.used_tools), ts2.used_tools)
check("W12 节点失败时 run_workflow 记为失败",
      "run_workflow" in list(ts2.failed_tools), ts2.failed_tools)
_cont = "".join(str(m.get("content")) for m in w2.messages if m.get("role") == "tool")
check("W13 喂回模型的 tool 回执带未完整标记", "未完整" in _cont, _cont[:150])
check("W14 喂回模型的回执不再说「工作流已完成」", "工作流已完成" not in _cont,
      _cont[:150])

# 成功路径不该被误标失败
ts3 = tsmod.TaskState()
w3 = make_worker(ts3)
w3._run_workflow_guarded = (
    lambda t, task, mw, a: agent.AgentWorker._finalize_workflow(
        w3, FakeTG({"writer": "done"}), "完整正文"))
w3._exec_tool_calls([tc("wf2", "run_workflow", {"type": "research_write", "task": "写报告"})],
                    MW(), str(ROOT))
check("W15 工作流成功时不误标失败、账本为成功",
      ("run_workflow" in list(ts3.used_tools))
      and ("run_workflow" not in list(ts3.failed_tools)),
      (ts3.used_tools, ts3.failed_tools))
check("W16 工作流成功后不残留未达标项", ts3.pending() == [], ts3.pending())

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
