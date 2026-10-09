# -*- coding: utf-8 -*-
"""v4.242.0 判据：「多步指令 DAG 化」相A（decompose_intent 纯函数）+ 相B（PLAN 接入 dag）+ 相C（task_state 前置校验前置化）

judge-first：本文件先于实现编写，预期红（intent_dag / 相C 接口尚未实现时整体 FAIL）。
实现落地后转绿（decompose_intent 把一句话拆成 ActionDAG；task_state.precond_check 把漏落盘从收尾救回提前到事中 nudge）。

纪律（与 test_clarify_gate_238.py 同源）：
  · fail-open：异常 / 节点<2 → None（不分解、不阻断）。
  · 澄清优先：intent.needs_clarification 命中 → None（交澄清闸门，不分解）。
  · 零误触发：单动作无落盘 → None；歧义句 → None。
  · 依赖表可单测：生成/搜索 + 落盘动词 → 追加 write_file，deps=主节点。
  · 相C：precond_check 基于 task_state 既有事实（succeeded_tools / artifacts）映射 dag 节点，
    漏落盘（生成了但 write_file 没调）被检出、补齐后无未满足、dag=None 不阻断。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import intent
import agent_loop  # 相B 接入点（should_plan/build_plan/plan_instruction）

try:
    from intent_dag import decompose_intent, ActionNode, ActionDAG
except Exception as e:  # noqa: BLE001
    print("  [FAIL] intent_dag 模块未实现（judge-first 预期红）：%s" % e)
    print("FAIL=1")
    sys.exit(1)

from task_state import TaskState  # 相C：precond_check / dag_nudge_instruction 载体

try:
    from agent_task_mixin import AgentTaskMixin
except Exception as _e_am:  # noqa: BLE001
    AgentTaskMixin = None
    print("  [WARN] agent_task_mixin 未导入（D8 跳过）: %s" % _e_am)


def _mk(kind=intent.KIND_ACTION, force=None, req=(), clarify=False):
    return intent.Intent(kind=kind, force_tool=force,
                         requested_tools=req, needs_clarification=clarify)


def _nodes(dag, tool):
    return [n for n in dag.nodes if n.tool == tool]


_P = 0
_F = 0


def check(name, cond, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("  [OK] %s" % name)
    else:
        _F += 1
        print("  [FAIL] %s %s" % (name, detail))


# ---- D1 正例：生成视频 + 落盘 → 2 节点，write_file 依赖 video_gen ----
it = _mk(force="video_gen", req=("video_gen",))
dag = decompose_intent("生成视频并存到 D 盘", it)
check("D1 返回 DAG（非 None）", dag is not None, "got %r" % dag)
if dag:
    wf = _nodes(dag, "write_file")
    check("D1 含 write_file 节点", len(wf) == 1, "wf=%d" % len(wf))
    if wf:
        check("D1 write_file.deps 含 video_gen 节点",
              len(wf[0].deps) >= 1 and wf[0].deps[0].startswith("n1"),
              "deps=%r" % (wf[0].deps,))
        check("D1 write_file.precond_tool 非空",
              bool(wf[0].precond_tool), "precond=%r" % wf[0].precond_tool)
    check("D1 节点数恰好 2", len(dag.nodes) == 2, "nodes=%d" % len(dag.nodes))

# D1b 路径词（桌面）同样触发落盘
it = _mk(force="image_gen", req=("image_gen",))
dag = decompose_intent("生成图片保存到桌面", it)
check("D1b 图片+桌面→含 write_file",
      dag is not None and any(n.tool == "write_file" for n in dag.nodes))

# ---- D2 单动作无落盘 → None ----
it = _mk(force="video_gen", req=("video_gen",))
dag = decompose_intent("做个昆明旅游的视频", it)
check("D2 单动作无落盘→None", dag is None, "got %r" % dag)

# ---- D3 歧义优先 → None（交澄清闸门）----
it = _mk(force=None, req=("image_gen", "video_gen"), clarify=True)
dag = decompose_intent("视频和图片都处理一下", it)
check("D3 歧义句→None（澄清优先）", dag is None, "got %r" % dag)

# ---- D4 fail-open ----
check("D4 空串→None", decompose_intent("", _mk()) is None)
check("D4 None 文本→None", decompose_intent(None, _mk()) is None)
check("D4 非 Intent→None", decompose_intent("生成视频并存盘", "not-intent") is None)

# ---- D5 搜索 + 整理存盘 → web_search + write_file（deps=search）----
it = _mk(force="web_search", req=("web_search",))
dag = decompose_intent("搜一下并整理成文档存起来", it)
check("D5 返回 DAG", dag is not None, "got %r" % dag)
if dag:
    ws = _nodes(dag, "web_search")
    wf = _nodes(dag, "write_file")
    check("D5 含 web_search 主节点", len(ws) == 1, "ws=%d" % len(ws))
    check("D5 含 write_file 节点", len(wf) == 1, "wf=%d" % len(wf))
    if wf and ws:
        check("D5 write_file 依赖 web_search 节点",
              wf[0].precond_tool == "web_search" or wf[0].deps == (ws[0].id,),
              "deps=%r precond=%r" % (wf[0].deps, wf[0].precond_tool))
    check("D5 节点数恰好 2", len(dag.nodes) == 2, "nodes=%d" % len(dag.nodes))

# ---- ordered 拓扑合理（生成在前，落盘在后）----
if dag and len(dag.nodes) == 2:
    check("D5 ordered 长度=2 且生成节点在前",
          len(dag.ordered) == 2 and dag.ordered[0].startswith("n1"))

# ---- ActionDAG 纯数据结构可断言 ----
if dag:
    check("DAG 是 ActionDAG 实例", isinstance(dag, ActionDAG))
    check("节点是 ActionNode 实例", all(isinstance(n, ActionNode) for n in dag.nodes))

# ---- D6 (相B) 接入 PLAN：build_plan/plan_instruction/should_plan 带依赖边 ----
# judge-first：相B 未实现时，build_plan/plan_instruction/should_plan 还不接受 dag
# 形参，调用会抛 TypeError → 防御性转成 FAIL（而非让整个套件崩成无 FAIL 字样）。
it = _mk(force="video_gen", req=("video_gen",))
_d6dag = decompose_intent("生成视频并存到 D 盘", it)
check("D6-0 dag 非空（相A 已落地）", _d6dag is not None, "got %r" % _d6dag)
if _d6dag:
    try:
        _p6 = agent_loop.build_plan("生成视频并存到 D 盘", ["video_gen"], dag=_d6dag)
    except TypeError:
        _p6 = []
        check("D6-1 build_plan 已接入 dag 形参", False, "尚未接入 dag")
    else:
        check("D6-1 build_plan 已接入 dag 形参", True)
        check("D6-2 build_plan(dag) 含依赖边标记（↓）",
              any("↓" in x for x in _p6), str(_p6))
        check("D6-3 build_plan(dag) 含 write_file 节点",
              any("write_file" in x for x in _p6), str(_p6))
        check("D6-4 build_plan(dag) 含 video_gen 节点",
              any("video_gen" in x for x in _p6), str(_p6))
        try:
            _in6 = agent_loop.plan_instruction(_p6, dag=_d6dag)
        except TypeError:
            _in6 = ""
            check("D6-5 plan_instruction 已接入 dag 形参", False, "尚未接入 dag")
        else:
            check("D6-5 plan_instruction 已接入 dag 形参", True)
            check("D6-6 plan_instruction(dag) 含「不得跳步」硬约束",
                  "不得跳步" in _in6, _in6[:160])
    try:
        _sp6 = agent_loop.should_plan(["video_gen"], dag=_d6dag)
    except TypeError:
        check("D6-7 should_plan 已接入 dag 形参", False, "尚未接入 dag")
    else:
        check("D6-7 should_plan 已接入 dag 形参", True)
        check("D6-8 should_plan(dag) 单工具也注入计划", _sp6 is True, "got %r" % _sp6)

# ---- D7 (相C) 前置校验前置化：task_state.precond_check(dag) ----
# judge-first：precond_check / dag_nudge_instruction 尚未实现时整体 FAIL（预期红）。
# 实现落地后转绿：把「生成了但没存盘」的漏落盘从收尾救回提前到事中 nudge。
_d7dag = decompose_intent("生成视频并存到 D 盘", _mk(force="video_gen", req=("video_gen",)))
check("D7-0 dag 非空（相A 已落地）", _d7dag is not None, "got %r" % _d7dag)

if not (hasattr(TaskState, "precond_check") and hasattr(TaskState, "dag_nudge_instruction")):
    check("D7-1 task_state.precond_check 已实现", False, "尚未实现（judge-first 预期红）")
    check("D7-2 漏落盘被检出", False, "precond_check 未实现")
    check("D7-3 补齐后无未满足", False, "precond_check 未实现")
    check("D7-4 dag=None 返回空（不阻断）", False, "precond_check 未实现")
    check("D7-5 依赖前置未满足被检出", False, "precond_check 未实现")
    check("D7-6 nudge 文案含 write_file 指令", False, "dag_nudge_instruction 未实现")
else:
    wf_node = [n for n in _d7dag.nodes if n.tool == "write_file"]
    check("D7-1 dag 含 write_file 节点", len(wf_node) == 1, "wf=%d" % len(wf_node))
    # 漏落盘：video_gen 已调且产物落盘，但 write_file 未调
    _ts_a = TaskState()
    _ts_a.record_tool("video_gen", args={"output_path": __file__}, ok=True)
    _miss_a = _ts_a.precond_check(_d7dag)
    check("D7-2 漏落盘被检出（write_file 未调）",
          any((m[1] == "write_file") for m in _miss_a), "miss=%r" % (_miss_a,))
    # 补齐：write_file 也调了
    _ts_a.record_tool("write_file", args={"path": __file__}, ok=True)
    _miss_a2 = _ts_a.precond_check(_d7dag)
    check("D7-3 补齐后无未满足", _miss_a2 == [], "miss=%r" % (_miss_a2,))
    # dag=None 不阻断
    _miss_none = _ts_a.precond_check(None)
    check("D7-4 dag=None 返回空（不阻断）", _miss_none == [], "got %r" % (_miss_none,))
    # 依赖前置检查：video_gen 压根没调
    _ts_b = TaskState()
    _miss_b = _ts_b.precond_check(_d7dag)
    check("D7-5 依赖前置未满足被检出（video_gen 未调）",
          any((m[2] == "video_gen" and m[3] == "precond_not_called") for m in _miss_b),
          "miss=%r" % (_miss_b,))
    # nudge 文案含 write_file 指令
    _nudge = _ts_a.dag_nudge_instruction(_miss_a)
    check("D7-6 nudge 文案含 write_file", "write_file" in _nudge, _nudge[:120])

    # 异常 fail-open：dag.nodes 迭代抛异常 → 返回 [] 不崩（被 except 兜住）
    class _BoomNodes(object):
        def __iter__(self):
            raise RuntimeError("boom")

    class _BoomDag(object):
        @property
        def nodes(self):
            return _BoomNodes()

    _ts_c = TaskState()
    try:
        _miss_c = _ts_c.precond_check(_BoomDag())
    except Exception:
        _miss_c = "RAISED"
    check("D7-7 异常 fail-open 返回空（不崩）", _miss_c == [], "got %r" % (_miss_c,))

# ---- D8 (相C) 接线：agent_task_mixin._dag_precond_miss_now ----
# 用最小 FakeAgent 验证「漏落盘 → 注入一轮 nudge / 一轮闸 / 补齐后不介入 / dag=None 不阻断」。
if AgentTaskMixin is None:
    for _n in ("D8-1", "D8-2", "D8-3", "D8-4", "D8-5"):
        check(_n, False, "agent_task_mixin 未导入")
else:
    class _FakeAgent(AgentTaskMixin):
        def __init__(self):
            self._intent_dag = None
            self._tstate = None
            self._tstate_nudged = False
            self._force_next = False
            self._idle_steps = 0
            self.messages = []
            self._emitted = []

        def _emit_status(self, msg):
            self._emitted.append(msg)

    _d8dag = decompose_intent("生成视频并存到 D 盘", _mk(force="video_gen", req=("video_gen",)))
    # D8-1 dag=None → 不介入
    _fa1 = _FakeAgent()
    _fa1._tstate = TaskState()
    _fa1._intent_dag = None
    check("D8-1 dag=None 不介入", _fa1._dag_precond_miss_now(_fa1._tstate, 1, 10) is False)
    check("D8-1b dag=None 未注入消息", _fa1.messages == [])
    # D8-2 漏落盘 → 介入并注入 nudge
    _fa2 = _FakeAgent()
    _fa2._tstate = TaskState()
    _fa2._tstate.record_tool("video_gen", args={"output_path": __file__}, ok=True)
    _fa2._intent_dag = _d8dag
    _r2 = _fa2._dag_precond_miss_now(_fa2._tstate, 1, 10)
    check("D8-2 漏落盘触发注入", _r2 is True, "r=%r" % _r2)
    check("D8-2b 注入了 internal nudge", any(m.get("_internal") for m in _fa2.messages), str(_fa2.messages))
    check("D8-2c 置 _tstate_nudged", _fa2._tstate_nudged is True)
    check("D8-2d 置 _force_next", _fa2._force_next is True)
    # D8-3 一轮闸：已注入则本轮不再介入
    _r3 = _fa2._dag_precond_miss_now(_fa2._tstate, 2, 10)
    check("D8-3 一轮闸：已注入不再介入", _r3 is False, "r=%r" % _r3)
    # D8-4 steps 无余量 → 不介入
    _fa4 = _FakeAgent()
    _fa4._tstate = TaskState()
    _fa4._tstate.record_tool("video_gen", args={"output_path": __file__}, ok=True)
    _fa4._intent_dag = _d8dag
    check("D8-4 步数耗尽不介入", _fa4._dag_precond_miss_now(_fa4._tstate, 10, 10) is False)
    # D8-5 补齐后无未满足 → 不介入
    _fa5 = _FakeAgent()
    _fa5._tstate = TaskState()
    _fa5._tstate.record_tool("video_gen", args={"output_path": __file__}, ok=True)
    _fa5._tstate.record_tool("write_file", args={"path": __file__}, ok=True)
    _fa5._intent_dag = _d8dag
    check("D8-5 补齐后不介入", _fa5._dag_precond_miss_now(_fa5._tstate, 1, 10) is False)

print("PASS=%d FAIL=%d" % (_P, _F))
sys.exit(1 if _F else 0)
