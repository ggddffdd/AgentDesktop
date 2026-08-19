# -*- coding: utf-8 -*-
"""AgentDesktop — 多 Agent 节点 v4.60

每个 AgentNode 是一个专用 LLM Agent，可被工作流引擎编排。
特性：
- 独立 system prompt（角色分工）
- 独立工具集（如研究员只有搜索工具、写手只有写文件工具）
- 独立模型配置（可选，默认用全局）
- 自动 tool 循环（调用工具 → 拿到结果 → 再思考 → 直到给出最终正文）

用法：
    researcher = AgentNode("researcher", "你是研究员，擅长搜索和提炼信息",
                           tools={"web_search", "web_fetch"}, mw=mw)
    writer = AgentNode("writer", "你是写手，把研究结果写成报告",
                       tools={"write_file"}, mw=mw)

    wf = WorkflowGraph()
    wf.add_node("research", researcher.run)
    wf.add_node("write", writer.run)
    wf.add_edge("research", "write")
    wf.add_edge("write", END)
    wf.run({"query": "AI趋势2026"})
"""

import time
import json
import logging
from typing import Set, Optional

log = logging.getLogger("dsdesktop")


class AgentNode:
    """专用 Agent 节点：包装 LLM 调用 + 工具循环。

    调用 .run(state) → 返回更新后的 state，其中 state["messages"] 包含该 agent
    产生的新消息，state["output"] 是最终正文。
    """

    # 工具名 → 兜底描述（防止 TOOL_DEFS 里找不到某个工具）
    _TOOL_DESC = {
        "web_search": "搜索互联网获取实时信息",
        "web_fetch": "抓取指定网页的完整内容",
        "write_file": "写入文件到本地",
        "read_file": "读取本地文件内容",
        "run_python": "执行 Python 代码并返回结果",
        "image_gen": "用 AI 生成图片",
        "search_memory": "搜索长期记忆库",
        "remember": "写入长期记忆",
    }

    def __init__(self, name: str, role_prompt: str,
                 tools: Set[str], mw,
                 max_turns: int = 6,
                 model_cfg: Optional[dict] = None):
        self.name = name
        self.role_prompt = role_prompt
        self.tool_names = tools
        self.mw = mw
        self.max_turns = max_turns
        self.model_cfg = model_cfg or {}

    def run(self, state: dict) -> dict:
        """执行本 Agent 的任务循环，返回更新后的 state。"""
        import config as _cfg
        from config import APP_DIR

        # 构建本 agent 的 messages
        messages = []
        # 系统提示 = 角色定义
        sys_msg = {"role": "system", "content": self.role_prompt}
        messages.append(sys_msg)

        # 从 state 中拿任务描述
        task = state.get("task", "") or state.get("query", "") or state.get("input", "")
        if task:
            messages.append({"role": "user", "content": str(task)})

        # 从 state 中拿上下文（前置 agent 的输出）
        ctx = state.get("context", "") or state.get(self.name + "_context", "")
        if ctx:
            messages.append({"role": "user", "content": f"【上下文】\n{str(ctx)[:3000]}"})

        # 构建工具集：只暴露本 agent 专属的工具
        all_tools = _cfg.get_all_tools(self.mw.cfg)
        my_tools = []
        for t in all_tools:
            fn = t.get("function", {})
            fn_name = fn.get("name", "")
            if fn_name in self.tool_names:
                my_tools.append(t)
        # 兜底：如果工具定义没找到，用轻量 schema
        for tn in self.tool_names:
            if not any(t.get("function", {}).get("name") == tn for t in my_tools):
                my_tools.append(self._fallback_tool_def(tn))

        # Agent 工具循环
        output = ""
        for turn in range(1, self.max_turns + 1):
            try:
                resp = self.mw._agent_call(messages, my_tools)
            except Exception as e:
                log.error("AgentNode [%s] turn %d 调用失败: %s", self.name, turn, e)
                break

            content = resp.get("content") or ""
            tool_calls = resp.get("tool_calls") or []

            if tool_calls:
                # 执行工具
                asst = {"role": "assistant", "content": content, "tool_calls": tool_calls}
                messages.append(asst)
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    t_name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except Exception:
                        args = {}
                    # 调工具
                    from tools import exec_tool
                    try:
                        result, _, _ = exec_tool(self.mw.cfg, APP_DIR, t_name, args)
                    except Exception as _te:
                        result = f"工具执行异常：{_te}"
                    # 截断过长结果
                    result = str(result)
                    if len(result) > 4000:
                        result = result[:4000] + "…(已截断)"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result,
                    })
            else:
                # 纯文本回复 = 本 agent 完成
                output = content
                messages.append({"role": "assistant", "content": content})
                break
        else:
            # 达到最大轮次：拿最后一轮的文本
            output = messages[-1].get("content", "") if messages else ""

        # 更新 state
        state = dict(state)
        state[self.name + "_output"] = output
        # 把本 agent 的消息追加到 context 供后续用
        history = json.dumps(
            [{"role": m["role"], "content": m.get("content", "")[:500]}
             for m in messages if m["role"] != "system"],
            ensure_ascii=False
        )
        state["context"] = (state.get("context", "") + f"\n\n【{self.name}】{output}").strip()
        state["messages"] = state.get("messages", []) + messages
        return state

    @staticmethod
    def _fallback_tool_def(name):
        """为未在 TOOL_DEFS 中找到的工具构建轻量 schema。"""
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": AgentNode._TOOL_DESC.get(name, f"工具 {name}"),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
