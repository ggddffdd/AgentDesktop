# -*- coding: utf-8 -*-
"""intent_dag.py —— v4.242.0 多步指令 DAG 化（相A：纯函数 decompose_intent）

设计稿：DESIGN_intent_dag.md §3-§4。本模块是**纯函数层**：无 IO、可单测、不接线、
零风险（相A 不触 agent.py / agent_loop.py / task_state.py，相B/相C 才接入）。

judge-first 伴生判据：tests/test_intent_dag_239.py（D1-D5）
零哑弹扰动：_perturb_intent_dag_239.py（P1-P3，PERTURB PASS=3 FAIL=0）

设计纪律（与 intent.py 同源）：
  · fail-open：任何异常 / 节点数<2 → 返回 None（退回当前行为，不分解、不阻断）。
  · 澄清优先：intent.needs_clarification 命中 → 直接 None（交澄清闸门，不分解）。
  · 只基于字面动词/连接词/落盘动词，不引入画像（与「画像上下文」第1项彻底解耦）。
  · 严禁凭空编造依赖：write_file 节点只在文本确有落盘动词/路径词时才追加。
"""
from dataclasses import dataclass

VERSION = "v4.251.0"

# 落盘动词（命中即认为用户要"产出文件"）
_SAVE_VERBS = ("存", "保存", "导出", "写到", "写入", "下载", "落盘", "存档",
               "存成", "存到", "存起来", "导出到")
# 路径词（含盘符 / 常见落盘位置）——命中同样视为落盘信号
# v4.244.0（审查报告 I-6）：删「下载」（已在 _SAVE_VERBS 重复登记）与「电脑」（位置词
# 非落盘信号，见词就造 write_file 节点会凭空编造依赖）。
_PATH_WORDS = ("D盘", "桌面", "本地", "文件夹", "目录", "硬盘", "u盘", "U盘")
# 生成/搜索类动词 → 候选工具（用于从文本补主节点；force_tool 优先）
# v4.244.0（审查报告 I-6）：删裸字键「画」——「动画片」里的「画」被误判成生图意图。
# 「画一张图」类真指令由 force_tool/_phrase_hit 覆盖，不依赖此裸字键。
_GEN_KEYWORDS = (
    ("视频", "video_gen"), ("生视频", "video_gen"), ("做视频", "video_gen"),
    ("图片", "image_gen"), ("生图", "image_gen"), ("做图", "image_gen"),
    ("搜", "web_search"), ("搜索", "web_search"),
    ("查一下", "web_search"), ("查查", "web_search"),
)
# 依赖推断规则表（小、可单测）：这些主节点遇到落盘信号 → 追加 write_file 依赖节点
_DEP_RULES = ("video_gen", "image_gen", "web_search")


@dataclass
class ActionNode:
    """DAG 中的单个动作节点（纯数据，无 IO，可 json 序列化便于判据断言）。"""
    id: str                      # "n1"
    tool: str                    # 候选工具名（video_gen / write_file / web_search ...）
    verb: str = ""               # 原句动词（生成 / 存 / 搜 ...）
    object: str = ""             # 宾语（视频 / D盘 ...）
    deps: tuple = ()             # 依赖的 node id 列表
    precond_tool: str = ""       # 前置必须已调用的工具（如 write_file 依赖 video_gen）
    done: bool = False


@dataclass
class ActionDAG:
    """多步指令的结构化分解（纯数据）。"""
    nodes: tuple                 # (ActionNode, ...)
    ordered: tuple               # 拓扑序的 node id 列表


def _has_save_signal(text):
    """落盘信号：落盘动词或路径词命中。"""
    return any(v in text for v in _SAVE_VERBS) or any(p in text for p in _PATH_WORDS)


def _primary_tools(text, intent):
    """主动作工具名列表（按出现顺序、去重）。force_tool 优先，其次文本动词补全。"""
    tools = []
    ft = getattr(intent, "force_tool", None)
    if ft:
        tools.append(ft)
    for kw, tool in _GEN_KEYWORDS:
        if kw in text and tool not in tools:
            tools.append(tool)
    return tools


def _should_decompose(text, intent):
    """触发门槛：未触发澄清 且 kind==action 且 存在主节点。

    与澄清闸门（v4.239.0）正交：澄清要求 force=None 多候选，分解要求
    force≠None 且有动词结构；澄清优先，命中即不分解。
    """
    if getattr(intent, "needs_clarification", False):
        return False
    if getattr(intent, "kind", None) != "action":
        return False
    return len(_primary_tools(text, intent)) >= 1


def decompose_intent(text, intent):
    """一句话抽 N 动作 + 依赖 → ActionDAG | None（fail-open）。

    相A 范围：仅生成/搜索类主节点 + 落盘动词 → 追加 write_file 依赖节点。
    注入 PLAN / 前置校验接入在相B / 相C，本函数不负责执行。

    返回 None 的语义 = 「不分解，退回当前行为」，绝不阻断主循环。
    """
    try:
        if not isinstance(text, str):
            return None
        if not hasattr(intent, "kind"):
            return None
        if not _should_decompose(text, intent):
            return None
        primaries = _primary_tools(text, intent)
        if len(primaries) < 1:
            return None
        save_signal = _has_save_signal(text)
        nodes = []
        nid = 1
        main_ids = []
        for tool in primaries:
            nid_s = "n%d" % nid
            nodes.append(ActionNode(id=nid_s, tool=tool))
            main_ids.append(nid_s)
            nid += 1
        appended = False
        if save_signal:  # 有落盘信号 → 追加 write_file 依赖节点
            for idx, tool in enumerate(primaries):
                if tool in _DEP_RULES:
                    wid = "n%d" % nid
                    nodes.append(ActionNode(
                        id=wid, tool="write_file",
                        deps=(main_ids[idx],), precond_tool=tool))
                    nid += 1
                    appended = True
                    break  # 一次一句只补一个落盘节点，避免凭空编造
        # 未追加且主节点不足 2 → 单动作，不分解
        if not appended and len(nodes) < 2:
            return None
        if len(nodes) < 2:
            return None
        ordered = tuple(n.id for n in nodes)
        return ActionDAG(nodes=tuple(nodes), ordered=ordered)
    except Exception:
        return None


__all__ = ["VERSION", "ActionNode", "ActionDAG", "decompose_intent"]
