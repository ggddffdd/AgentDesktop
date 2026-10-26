# -*- coding: utf-8 -*-
"""v4.289.2：失败可续 + 主工作流 incomplete/取消协议。
纯标准库，不访问网络；日志/数据改道临时目录。
"""
import ast
import os
from pathlib import Path
import sys
import tempfile

_SBX = tempfile.mkdtemp(prefix="xc_292_resume_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")
os.environ["XC_LEGION_DIR"] = os.path.join(_SBX, "legion")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import agent
import agent_node as an
import task_graph as tgmod

PASS = FAIL = 0
SRC = (ROOT / "agent.py").read_text(encoding="utf-8-sig")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def _run_inner_calls():
    tree = ast.parse(SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_run_inner")
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            out.append(n)
    return out


class MWText:
    cfg = {}

    def _agent_call(self, messages, tools, model_override=None, thinking=False):
        return {"content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "not_allowed_292", "arguments": "{}"}}]}


class FakeTaskGraph:
    last_token = None
    cancel_hook = None

    def __init__(self):
        self.nodes = []

    def create(self, tid, label, execfn, desc=""):
        self.nodes.append(execfn)
        return self

    def depend(self, *a):
        return self

    def task_list(self):
        return [{"id": "x", "subject": "x", "status": "completed"}]

    def run(self, state, token=None):
        FakeTaskGraph.last_token = token
        if FakeTaskGraph.cancel_hook is not None:
            FakeTaskGraph.cancel_hook()
        return dict(state)


def main():
    print("[A] 失败/耗尽结局必须可续")
    w = agent.AgentWorker.__new__(agent.AgentWorker)
    w._stop_requested = False
    w._resumable_stop = False
    w._resumable_reason = ""
    check("A1 _mark_resumable 存在", hasattr(w, "_mark_resumable"))
    if hasattr(w, "_mark_resumable"):
        w._mark_resumable("model_error")
        check("A2 标记可续且记录原因",
              w._resumable_stop is True and w._resumable_reason == "model_error")
        check("A3 检查点保留判决消费可续标记",
              w._checkpoint_should_pause() is True)
        w._resumable_stop = False
        check("A4 正常完成不保留检查点", w._checkpoint_should_pause() is False)

    calls = _run_inner_calls()
    reasons = set()
    for c in calls:
        if isinstance(c.func, ast.Attribute) and c.func.attr == "_mark_resumable":
            if c.args and isinstance(c.args[0], ast.Constant):
                reasons.add(c.args[0].value)
    need = {"model_error", "timeout", "tool_timeout", "token_budget",
            "resume_exhausted", "max_steps"}
    check("A5 六类非成功出口全部标可续", need <= reasons, reasons)
    check("A6 收尾改用统一保留判决",
          any(isinstance(c.func, ast.Attribute)
              and c.func.attr == "_checkpoint_should_pause" for c in calls))

    print("\n[B] AgentNode 无产出要同时报 TaskGraph")
    node = an.AgentNode("m", "角色", tools={"not_allowed_292"}, mw=MWText(),
                        max_turns=1)
    st = node.run({"messages": [], "context": ""})
    check("B1 保留角色级 _incomplete 标记", st.get("m_incomplete") is True)
    check("B2 同时写 TaskGraph.INCOMPLETE_FLAG",
          st.get(tgmod.INCOMPLETE_FLAG) is True, st)
    g = tgmod.TaskGraph()
    g.create("m", "M", lambda s: node.run(s))
    g.run({})
    check("B3 主工作流节点不再被误标 completed",
          g._tasks["m"].status == "incomplete", g._tasks["m"].status)

    print("\n[C] 工作流终态汇总")
    w2 = agent.AgentWorker.__new__(agent.AgentWorker)
    w2._tstate = None
    w2._workflow_incomplete = None
    msgs = []
    w2._emit_status = msgs.append
    class FT:
        def __init__(self, ss):
            self.ss = ss
        def task_list(self):
            return [{"id": i, "status": s} for i, s in self.ss]
    w2._finalize_workflow(FT([("a", "completed"), ("b", "incomplete"),
                              ("c", "cancelled")]), "half")
    inc = w2._workflow_incomplete or {}
    check("C1 incomplete/cancelled 计入未完整", inc.get("incomplete") == ["b"]
          and inc.get("cancelled") == ["c"], inc)
    check("C2 绝不谎报任务图完成", any("未完整" in m for m in msgs)
          and not any("✅ 任务图完成" in m for m in msgs), msgs)

    print("\n[D] 主工作流接入取消令牌")
    old_tg, old_node = agent.TaskGraph, agent.AgentNode
    try:
        agent.TaskGraph = FakeTaskGraph
        agent.AgentNode = lambda *a, **k: type("N", (), {"run": lambda s, s2=None: s2})()
        w3 = agent.AgentWorker.__new__(agent.AgentWorker)
        w3.messages = [{"role": "user", "content": "调研"}]
        w3.mw = type("MW", (), {"cfg": {}})()
        w3._workflow_token = None
        w3._resumable_stop = False
        w3._stop_requested = False
        w3._workflow_incomplete = None
        w3._emit_status = lambda s: None
        w3._finalize_workflow = lambda tg, out: out
        FakeTaskGraph.cancel_hook = w3.request_stop
        out = agent.AgentWorker._run_workflow(w3, "research_write", "调研")
        check("D1 TaskGraph.run 收到令牌", FakeTaskGraph.last_token is not None)
        check("D2 令牌随请求停止而取消（不继续烧 token）",
              FakeTaskGraph.last_token.is_cancelled is True)
        check("D3 取消中的工作流不返回伪产出", out == "")
    finally:
        agent.TaskGraph, agent.AgentNode = old_tg, old_node

    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
