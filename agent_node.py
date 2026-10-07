# -*- coding: utf-8 -*-
"""小臭玩AI — 多 Agent 节点 v4.60

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


class _AllowDecision:
    """军团/主链子节点**无权限适配器**时的兜底决策（放行）。

    v4.226（P1-1）复核：这一处**刻意不改**，与已删除的 agent._AllowAllDecision
    是两件不同的事，不是同类漏项 ——

      · agent._AllowAllDecision 所在的是**主链并发批次**：那里本就该有权限引擎
        （v4.219 P1#4 的全部意义就是「并发批次也要过闸」），拿不到决策说明闸门
        失效，放行等于把刚补上的闸门自己拆了。故已删除。
      · 本类所在的是 **AgentNode 子节点**：它是任务图里的一个工序，
        `tools=` 白名单已经限死了它能碰哪些工具，而任务图本身在
        agent._run_workflow_guarded 处**已整体过闸并取得用户放行**。
        agent.py 对此有明文设计：「工作流一旦被用户放行，其内部步骤属于
        本次已授权动作的实施细节，不再逐项弹确认」（否则研究+写作三节点
        会弹一串确认，没人受得了）。

    若把这里也改成 fail-closed，`write_file`（WRITE_LOCAL）在
    `research_write` 工作流的「撰写报告」节点会被直接拒掉 —— 工作流必然跑不通。
    即真要收紧，正确做法是给 AgentNode 接权限适配器（军团侧已有 `perm` 钩子），
    而不是把这个兜底翻成拒绝。

    下方判据 tests/test_permission_failclosed_226.py 的 D 组把这条设计决定
    钉住：改这个类会红，防止日后被当成「同类漏项」顺手改掉。
    """
    allowed = True
    needs_user = False
    reason = "legion-no-perm fallback"
    rule = "fallback"


class AgentExecutionError(RuntimeError):
    """成员执行的硬失败（模型调用异常等）—— 必须让上层看到失败，而不是空稿。

    v4.166.0：此前模型调用异常被 `break` 吞掉，run() 照常返回空 output，
    TaskGraph 见「正常返回」即标 completed ——「成员崩了」被记成「交了空稿」，
    PM 与最终报告都看不见，还会白烧后续波的 token。
    （工具执行异常那条线 v4.140 已改为 re-raise，本条是同级缺口。）
    """

    def __init__(self, role: str, stage: str, turn: int, root_error: BaseException):
        self.role = role
        self.stage = stage
        self.turn = turn
        self.root_error = root_error
        super().__init__(
            f"[{role}] {stage} 失败（第 {turn} 轮）："
            f"{type(root_error).__name__}: {root_error}"
        )


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
        # 军团调度三件套（v4.122）：项目经理做验收/调度时读产出与日志
        "legion_list_outputs": "列出军团各波次成员的产出清单（含字数与摘要）",
        "legion_get_output": "读取军团某位成员或某一波的完整产出原文",
        "legion_read_log": "读取本次军团执行的过程日志",
        "legion_board": "读取项目共享任务板（各节点状态与最近事件）",
        "legion_find_asset": "查资产库：找已产出的图/视频/剧本存货（三视图、关键帧、成片等），复用别重造",
        "legion_get_sources": "查成员抓取留痕：搜了哪些词、抓了哪些页面、抓回多少字（验收数据类产出必查）",
        "legion_report_issue": "上报问题：上游数据不可信/缺依赖/指令矛盾时上报，项目经理必须回应（别硬编数据）",
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
        # v4.128：Thinking 默认关，仅 PM 验收/复杂规划等重推理环节由调用方显式打开。
        self.thinking = bool((model_cfg or {}).get("thinking"))

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
        incomplete = False   # v4.166.0：跑满轮次而无正文时置位
        # v4.125 M-08：角色级模型——角色卡「模型」字段填了档位名则锁定该档位。
        _model_ov = (self.model_cfg or {}).get("profile", "")
        # v4.167.0：取消令牌（由 legion_worker._wrap 挂上；主对话链为 None → 行为不变）
        _token = getattr(self, "token", None)
        for turn in range(1, self.max_turns + 1):
            # v4.167.0（审查 §3）：轮次之间检查取消 —— 用户点了停止，
            # 已开始的成员**不再发起下一次模型调用**（原来只在波开始前查一次，
            # 波内成员进去之后没人再看 → "点了停止还继续扣费"）。
            if _token is not None:
                _token.raise_if_cancelled("model_call")

            try:
                resp = self.mw._agent_call(messages, my_tools, model_override=_model_ov,
                                           thinking=self.thinking)
            except Exception as e:
                log.error("AgentNode [%s] turn %d 调用失败: %s", self.name, turn, e)
                # v4.166.0：模型调用异常**必须显式失败**，不能 break ——
                # 原来 break 之后 run() 照常返回空 output，TaskGraph 见正常返回
                # 即标 completed（假成功）。现在抛出，由 TaskGraph 标 failed。
                raise AgentExecutionError(self.name, "model_call", turn, e) from e

            content = resp.get("content") or ""
            tool_calls = resp.get("tool_calls") or []

            if tool_calls:
                # 执行工具
                asst = {"role": "assistant", "content": content, "tool_calls": tool_calls}
                messages.append(asst)
                for tc in tool_calls:
                    # v4.167.0（审查 §3）：工具调用前再查一次令牌 ——
                    # 已开始的任务"不再进入下一次工具调用"，不再为停止后的动作付费。
                    if _token is not None:
                        _token.raise_if_cancelled("tool_call")

                    fn = tc.get("function", {})
                    t_name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except Exception:
                        args = {}
                    # 调工具（v4.125 M-04：执行端白名单二次校验——schema 过滤
                    # 防君子不防幻觉，幻觉出的白名单外工具在这里被硬拒。）
                    from tools import exec_tool
                    # v4.167.0（审查 #1，P0）：军团成员的工具调用必须过权限闸门。
                    #
                    # 主对话链有 UI 逐项确认；军团是**无人值守的批量执行**，
                    # 走的是另一套：默认只读 + 本波授权 + 工作区作用域 + 逐笔审计。
                    # 适配器只由 legion_worker._wrap 挂上（agent.perm）；
                    # 主对话链的 AgentNode 没有这个属性 → 行为与改动前完全一致。
                    #
                    # fail-closed：闸门自身出错时**拒绝**，绝不放行。
                    _perm = getattr(self, "perm", None)
                    _blocked = ""
                    if _perm is not None:
                        try:
                            _dec = _perm.check(t_name, args, role=self.name,
                                               wave=getattr(self, "wave_no", 0))
                            if not _dec.allowed:
                                _blocked = _dec.reason
                        except Exception as _pe:
                            _blocked = f"权限闸门检查异常，按保守策略拒绝（{_pe}）"
                    if _blocked:
                        result = (f"⛔ 权限闸门拒绝执行 {t_name}：{_blocked}"
                                  "（若这是本任务必需的能力，请让大哥在授权弹窗放行本波）")
                        try:
                            log.warning("AgentNode [%s] 工具被权限闸门拒绝: %s — %s",
                                        self.name, t_name, _blocked)
                        except Exception:
                            pass
                    else:
                        try:
                            result, _, _ = exec_tool(self.mw.cfg, APP_DIR, t_name, args,
                                                     allowed_tools=self.tool_names,
                                                     perm_ctx=(_dec if _perm is not None
                                                               else _AllowDecision()))
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
            # 达到最大轮次：审计修复 E10——for-else 触发时最后一条必是 role="tool"
            # 的截断回执，原实现把它当正文塞进 state，原始工具 JSON 残片会注入
            # 下游 agent 上下文。改为反向找最近一条非空 assistant 文本。
            output = next(
                (m.get("content", "") for m in reversed(messages)
                 if m.get("role") == "assistant" and m.get("content")),
                "",
            )
            if not output.strip():
                # v4.166.0：跑满轮次又没有任何正文，不能静默当「完成」。
                # 用可读说明兜底（避免空字符串进入下游上下文），并标记 incomplete，
                # 让上层能区分「真交了东西」与「只跑了工具 / 啥都没出」。
                _tool_n = sum(1 for m in messages if m.get("role") == "tool")
                output = (f"（{self.name} 在 {self.max_turns} 轮内未产出正文，"
                          f"仅完成 {_tool_n} 次工具调用）" if _tool_n else
                          f"（{self.name} 在 {self.max_turns} 轮内未产出任何内容）")
                incomplete = True
                log.warning("AgentNode [%s] 跑满 %d 轮无正文（工具调用 %d 次），标记 incomplete",
                            self.name, self.max_turns, _tool_n)

        # 更新 state
        state = dict(state)
        state[self.name + "_output"] = output
        if incomplete:
            # v4.166.0：显式标记，供 PM / 报告区分「真产出」与「只跑了工具」
            state[self.name + "_incomplete"] = True
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
