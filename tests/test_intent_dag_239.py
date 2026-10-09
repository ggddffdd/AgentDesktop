# -*- coding: utf-8 -*-
"""v4.240.0 判据：「多步指令 DAG 化」相A（decompose_intent 纯函数）

judge-first：本文件先于实现编写，预期红（intent_dag 尚未实现时整体 FAIL）。
实现落地后转绿（decompose_intent 把一句话拆成 ActionDAG）。

纪律（与 test_clarify_gate_238.py 同源）：
  · fail-open：异常 / 节点<2 → None（不分解、不阻断）。
  · 澄清优先：intent.needs_clarification 命中 → None（交澄清闸门，不分解）。
  · 零误触发：单动作无落盘 → None；歧义句 → None。
  · 依赖表可单测：生成/搜索 + 落盘动词 → 追加 write_file，deps=主节点。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import intent

try:
    from intent_dag import decompose_intent, ActionNode, ActionDAG
except Exception as e:  # noqa: BLE001
    print("  [FAIL] intent_dag 模块未实现（judge-first 预期红）：%s" % e)
    print("FAIL=1")
    sys.exit(1)


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

print("PASS=%d FAIL=%d" % (_P, _F))
sys.exit(1 if _F else 0)
