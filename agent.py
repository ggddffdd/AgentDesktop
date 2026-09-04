# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 — Agent 后台线程模块"""

import time
import json
import datetime
import os
import threading
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal

import task_resume  # v4.101：断点续传检查点

from config import MAX_AGENT_STEPS, TOOL_RESULT_LIMIT, get_all_tools
import tools
import memory_store
from step_tracer import StepTracer  # v4.59 步级追踪
from task_graph import TaskGraph      # v4.60 任务图引擎
from agent_node import AgentNode      # v4.60 多Agent节点
from token_compressor import compress # v4.60 Token 压缩

# 自动记忆提取：对话结束后 LLM 自检是否产生了值得跨对话保留的信息
AUTO_REMEMBER_PROMPT = """
你是一个对话归档助手。请从以上对话中提取值得长期记忆的信息，以 JSON 数组格式输出。

每条是一个对象，包含三个字段：
- "topic"：该记忆的主题关键词（如"工作区路径""常用网址""偏好设置"），用于后续去重与覆盖——同一主题的新信息会替换旧信息，避免新旧并存；
- "category"：类别，取值为 "能力进化" / "用户偏好与约定" / "重要决策" 之一；
- "content"：1-2 句话的具体记忆内容，包含必要上下文（路径、数值、原因）。

只提取这三类，且只提取对话中明确的、可验证的事实与决定。如果对话中没有值得长期记忆的新信息，输出空数组 []。

输出格式（纯 JSON 数组，不要 markdown 包裹）：
[{"topic":"工作区路径","category":"用户偏好与约定","content":"用户工作区路径为 ~/Documents 下对应项目目录"}]
"""

log = logging.getLogger("dsdesktop")


class AgentWorker(QThread):
    """后台线程跑 Agent 循环，所有 UI 更新通过信号抛回主线程，彻底避免卡死。
    
    支持多工具并发执行（ThreadPoolExecutor）+ MCP 工具。
    """
    status = Signal(str)
    render = Signal()
    tool_log = Signal(dict)            # 持久化工具调用记录 {name, args, result}
    tool_started = Signal(dict)        # 工具开始时：{name, args, index, total}
    tool_finished = Signal(dict)       # 工具完成时：{name, result_preview, index, success, duration_ms}
    confirm_action = Signal(str, str)  # (标题, 详情) -> 主线程弹确认框，结果经事件回传
    stream_begin = Signal()
    stream_chunk = Signal(str)
    stream_commit = Signal(str)        # 最终回答全文 -> 主线程提交进历史
    schedule_reminder = Signal(int, str, int)  # (延时毫秒, 提醒内容, 重复秒数) -> 主线程定时/循环弹窗
    deliverable_added = Signal(str, str, str)  # (相对路径, 类型, 文件名) -> 主线程写入交付物区
    done = Signal()

    # ---- v4.60 工作流模式 ----

    def _run_workflow(self, workflow_type="research_write", task=""):
        """v4.60：运行预设任务图，返回最终结果文本。workflow_type 可选：
        - "research_write": 搜索→分析→写作（3 节点串行流水线）
        - "multi_search": 多角度并行搜索（3 个研究员并行跑，结果归并）
        """
        if not task:
            for msg in reversed(self.messages):
                if msg.get("role") == "user":
                    c = msg.get("content", "")
                    if isinstance(c, str):
                        task = c
                    break
        if not task:
            task = "未指定任务"

        tg = TaskGraph()
        angles = None  # multi_search 用的角度表，归并阶段复用

        if workflow_type == "research_write":
            researcher = AgentNode("researcher",
                "你是研究员。搜索互联网获取信息，整理成结构化摘要。必须调 web_search。",
                tools={"web_search", "web_fetch"}, mw=self.mw)
            analyst = AgentNode("analyst",
                "你是分析师。基于研究结果提炼关键洞察和建议。不调用工具，直接出分析文本。",
                tools=set(), mw=self.mw)
            writer = AgentNode("writer",
                "你是写手。将分析结果写成完整报告/文章。必要时调 write_file 保存。",
                tools={"write_file"}, mw=self.mw)

            tg.create("search", "🔍 搜索资料", researcher.run, "使用 web_search 获取信息")
            tg.create("analyze", "📊 分析提炼", analyst.run, "基于搜索结果提炼洞察")
            tg.create("write", "📝 撰写报告", writer.run, "写成完整报告")
            tg.depend("analyze", "search")
            tg.depend("write", "analyze")
            out_key, out_field = "write", "writer_output"

        elif workflow_type == "multi_search":
            # 多角度并行搜索：3 个研究员各自独立搜索，TaskGraph 并行执行，最后归并
            angles = [
                ("latest", "最新动态", "你是研究员。搜索「{task}」的最新动态、近期新闻与进展，返回结构化中文摘要。必须调 web_search。"),
                ("tech", "技术原理", "你是研究员。搜索「{task}」的技术原理、实现方式与关键概念，返回结构化中文摘要。必须调 web_search。"),
                ("market", "应用与影响", "你是研究员。搜索「{task}」的应用场景、行业影响与真实案例，返回结构化中文摘要。必须调 web_search。"),
            ]
            for key, label, role in angles:
                n = AgentNode(f"searcher_{key}", role.format(task=task),
                              tools={"web_search", "web_fetch"}, mw=self.mw)
                tg.create(f"search_{key}", f"搜索·{label}", n.run)
            out_key = out_field = None

        else:
            self._emit_status(f"未知工作流: {workflow_type}")
            return ""

        self._emit_status(f"🔄 任务图启动：{workflow_type}")
        # 打印任务清单
        for t in tg.task_list():
            self._emit_status(f"  └ {t['subject']} [{t['status']}]")

        try:
            state = tg.run({"task": task, "query": task})
        except Exception as e:
            self._emit_status(f"任务图执行失败: {e}")
            return f"任务图执行失败：{e}"

        # 归并结果
        if workflow_type == "multi_search":
            parts = []
            for key, label, _ in angles:
                node_out = state.get(f"search_{key}", {})
                txt = node_out.get(f"searcher_{key}_output", "") if isinstance(node_out, dict) else ""
                if txt:
                    parts.append(f"【{label}】\n{txt}")
            output = "\n\n".join(parts) if parts else "（搜索未返回结果）"
        else:
            node_out = state.get(out_key, {})
            output = node_out.get(out_field, "") if isinstance(node_out, dict) else ""

        self._emit_status("✅ 任务图完成")
        return output

    # ---- 主循环 ----

    def __init__(self, mw, messages, tool_defs, mcp_clients=None, task_id=None,
                 resume=False, isolated=False, force_complex=False):
        super().__init__()
        self.mw = mw
        self.messages = messages
        self.tool_defs = tool_defs
        # v4.107：isolated=隔离会话（导演台底部独立对话条）。与主对话模块零交集：
        # 不回写主会话历史、不写长期记忆、不从主会话取 _seq 种子（自己从 0 起算）。
        # 仅保留 deliverable_added（成片登记到交付物区）。
        self._isolated = bool(isolated)
        # force_complex=True：绕过工具意图判定，直接升舱 complex_model（DeepSeek）。
        # 导演台独立会话用——工具集虽小但都是写操作，弱模型容易退化成文字演工具。
        self._force_complex = bool(force_complex) or bool(isolated)
        # v4.101：断点续传——每个 Agent 任务带唯一 task_id，停止时标记 paused、正常完成删除。
        self.task_id = task_id or task_resume.new_task_id()
        self.resume = bool(resume)
        self.stopped_by_user = False  # 结束时回填：是否因用户停止而终止（供 UI 决定是否显示「继续」）
        # ④ 防御性：给 baseline（sys_msg + 清洗后的历史）打 _seq=0 哨兵，标记「已存在于
        # session、禁止回写」；运行内新生成的消息单调递增 _seq（种子取 session 已有最大
        # _seq 之上，避免跨运行 _seq 撞号）。_sync_to_session 据此按序号对齐回写，
        # 替代原先脆弱的「内容指纹」匹配（重复 user 指令会命中错误首次出现 → 漏写/重复写）。
        self._seq_ctr = 0
        for _m in self.messages:
            if isinstance(_m, dict):
                _m["_seq"] = 0
        try:
            if not self._isolated:  # 隔离会话：不读主会话，_seq 从 0 起算
                _sess = mw.store.active()
                if _sess is not None:
                    for _em in _sess.messages:
                        if isinstance(_em, dict) and isinstance(_em.get("_seq"), int) and _em["_seq"] > 0:
                            self._seq_ctr = max(self._seq_ctr, _em["_seq"])
        except Exception:
            pass
        self.mcp_clients = mcp_clients or []
        self._confirm_event = threading.Event()
        self._confirm_val = False
        self._stop_requested = False
        self._last_status_ts = 0  # v4.102 fix7：最近具体状态时间戳，供心跳判断是否抢话
        self._last_status_text = ""  # v4.102 fix7：最近一条具体状态文字，心跳兜底时带上
        # v4.102 fix9：_guard_blocked 必须 __init__ 初始化——纯文本分支（模型首步就输出
        # 回答、无 tool_calls）会在 750 行读取它。此前只在工具执行路径赋值，导致纯咨询
        # 问题（fix8 nudge 门槛后纯文本分支命中）直接 AttributeError 崩溃 → run() 抛异常
        # → done.emit() 不触发 → UI 卡死。用户实证：回答已渲染但状态栏常驻「工作中」。
        self._guard_blocked = False
        # v4.108 M-25：工具序号全局单调——UI 用它做 live-tool-{index} 卡片 id。
        # 旧实现每批 enumerate 从 0 起，跨轮重复 id → replace_live 替换错卡/状态串。
        self._tool_seq = 0
        # 技能清单由 ui.py 的 _build_system_prompt() 统一通过 config.load_dynamic_skills() 注入，
        # 此处不再重复加载，避免双份/不一致。

    def request_stop(self):
        """主线程调用，请求停止 Agent 循环（下一轮/下一工具前生效）。
        v4.108 M-20：同时唤醒阻塞中的危险操作确认等待（否则关窗/停止时
        子线程永久卡在 _confirm_event.wait()，QThread 销毁报 crash）。"""
        self._stop_requested = True
        try:
            self._confirm_event.set()
        except Exception:
            pass

    def _next_tool_index(self):
        """v4.108 M-25：工具卡片 id 用全局单调序号（跨批不重复）。"""
        self._tool_seq += 1
        return self._tool_seq

    def _emit_status(self, text):
        """v4.102 fix7：统一状态出口——记录最近一次具体状态的时间戳与文字，
        供心跳线程判断「是否已有更具体的进度展示，避免被笼统的『工作中』覆盖」；
        心跳兜底时也带上最近一条具体状态，长工具调用期间用户仍能看到在干嘛。"""
        self._last_status_ts = time.time()
        self._last_status_text = str(text)
        self.status.emit(text)

    def _start_heartbeat(self):
        """v4.58：后台心跳线程，每 2 秒 emit 一次 status，防长时间工具调用期间 UI 假死。
        v4.102 fix7：仅当最近 4 秒内没有更具体的状态（思考中第 N 步/执行工具 XX）时，
        才发笼统的『Agent 工作中…』——否则会覆盖掉具体进度，用户不知道模型在干嘛。
        兜底时带上最近一条具体状态，例如「⏳ Agent 工作中…（执行工具：web_search）」。"""
        self._heartbeat_alive = True
        def _beat():
            while getattr(self, "_heartbeat_alive", False):
                time.sleep(2)
                if not getattr(self, "_heartbeat_alive", False):
                    return
                # 最近 4 秒内有具体状态（思考中/执行工具/错误提示）→ 心跳不抢话
                if time.time() - getattr(self, "_last_status_ts", 0) < 4:
                    continue
                _hint = getattr(self, "_last_status_text", "") or ""
                self.status.emit(f"⏳ Agent 工作中…（{_hint}）" if _hint else "⏳ Agent 工作中…")
        threading.Thread(target=_beat, daemon=True).start()

    def _stop_heartbeat(self):
        self._heartbeat_alive = False

    def _maybe_confirm(self, title, detail):
        """危险操作：向主线程请求确认，子线程阻塞等待结果。"""
        engine = getattr(self.mw, "permission_engine", None)
        if engine is not None and engine.session_trusted:
            return True
        if getattr(self.mw, "session_trusted", False):
            return True
        self._confirm_val = False
        self._confirm_event.clear()
        self.confirm_action.emit(title, detail)
        # v4.108 M-20：等待加超时——关窗/停止（request_stop 已 set 本事件）时
        # 不再永久阻塞；超时按「拒绝」处理（危险操作默认不执行，安全优先）。
        self._confirm_event.wait(timeout=180)
        return self._confirm_val

    def _stream_text(self, text):
        """逐段 emit，主线程负责把 _streaming_text 滚动渲染（打字机效果）。"""
        self.stream_begin.emit()
        n = max(1, len(text) // 200)   # 约 200 段，总时长可控
        acc = ""
        for i in range(0, len(text), n):
            acc = text[:i + n]
            self.stream_chunk.emit(acc)
            time.sleep(0.02)
        self.stream_commit.emit(text)


    @staticmethod
    def _safe_args(tc):
        """从 tool_call 里稳妥解析 arguments JSON。"""
        try:
            return json.loads(tc.get("function", {}).get("arguments", "{}") or "{}")
        except Exception:
            return {}

    # 硬化提示：模型只描述计划却不调工具时，强制它真正执行
    _AGENT_NUDGE = (
        "你刚才只描述了计划，却没有调用任何工具——这等于什么都没做，任务失败。"
        "现在必须立即调用真实工具来完成任务：检索资料用 web_search、写文件用 write_file、"
        "做数据分析用 run_python。禁止再输出「我来/下一步/马上/让我看看」之类的承诺性文字。"
        "若任务确实已全部完成，请直接给出最终成果与产物路径，不要再空头承诺。"
    )

    # v4.98 撒谎检测器配套：模型用文字"演"工具调用（伪造 [工具]/✅ 已保存/run_python(）
    # 却不真发 tool_call 时，强制它真正调用工具，禁止口头编造结果。
    _AGENT_FAKE_TOOL_INSTR = (
        "你刚才并没有真正调用任何工具，只是用文字假装『已运行/已保存/已调用』——这是撒谎，"
        "任务并没有完成。现在必须立即发出真实的 tool_call 让系统真正执行：写文件用 write_file、"
        "跑代码用 run_python、搜索用 web_search、生图用 image_gen。"
        "禁止再用『✅ 已保存』『[工具]』『run_python(...)』『已调用工具』这类文字伪造工具调用，"
        "直接发出真实的 function call，让系统执行并返回真实结果。"
    )

    # v4.60o：用户要求"记住/保存"真实能力时的内部指令——拿到 sys_info 真实数据后，
    # 用 remember 工具把能力清单写入长期记忆，下次启动自动回填系统提示，不再凭空编。
    _CAP_PERSIST_INSTRUCTION = (
        "用户明确要求你记住真实能力，不要下次又忘记。请立即调用 remember 工具，"
        "把刚才 sys_info 返回的真实能力清单写入长期记忆（要点：可用技能、工具、"
        "模型、MCP 服务器、数据目录）。写入后用真实数据向用户简洁确认已记住，"
        "不要编造任何不存在的问题或路径。"
    )
    # v4.60p：普通自检（非"记住"）路径——sys_info 返回后只准逐条复述，禁止编造
    _SELF_CHECK_INSTRUCTION = (
        "以上是系统的真实能力清单（sys_info 返回）或刚刚真实执行的工具结果（web_search）。"
        "请只准基于以上真实信息回复，禁止从你自己的知识添加任何新功能。"
        "特别注意：Obsidian Vault 未配置，RAG 用的是本地 rag_data 目录，绝不可声称"
        "『Obsidian 语义检索』；Webhook 未开启也不可声称可用。"
        "若刚才调用了 web_search，请基于真实返回结果说明搜索功能是否正常（有无结果、是否报错），"
        "不要编造。无论何种情况，都【禁止生成任何 PPT、Word 文档、演示文稿、视频或图片等"
        "交付物】——用户只是想了解/验证工具状态，用简洁文字报告即可。"
        "不要编造任何不存在的问题或路径。"
    )

    def _looks_like_promise(self, text):
        """判断模型返回是否像『承诺执行却不行动』（有承诺词且有动作词）。
        v4.58 扩展：补上「让我/帮你/查查/看看/检查」等常见空承诺模式。
        """
        t = (text or "").lower()
        promise = ("我来", "现在开始", "下一步", "马上", "即将", "准备去", "打算",
                   "这就去", "去搜索", "去查", "去写", "去生成", "去执行", "先帮你",
                   "让我", "帮你", "我先", "这就查", "这就看", "让我查", "让我看",
                   "我查查", "我看看", "查查", "看看",
                   "用Python", "用", "改用", "换成")
        action = ("搜索", "查", "写", "做", "执行", "生成", "分析", "打开", "运行",
                  "抓取", "下载", "创建", "整理", "发", "导出", "检查", "查看",
                  "看看", "查查", "检索", "读取", "调用", "跑")
        return any(p in t for p in promise) and any(a in t for a in action)

    def _looks_like_question(self, text):
        """v4.61：判断模型输出是否像「向用户追问」而非推进任务。
        用于循环护栏：连续追问则早停防刷屏。
        """
        t = text or ""
        if "？" not in t and "?" not in t:
            return False
        markers = ("需要你", "请告诉", "请给", "请提供", "你的", "提供", "几个",
                   "多少", "什么", "如何", "怎么", "告诉", "补充", "了解", "信息")
        return any(m in t for m in markers)

    def _looks_like_fake_tool_call(self, text):
        """v4.98 撒谎检测器：模型不真发 tool_call，却用文字"演"工具调用
        （伪造 [工具] run_python / ✅ 已保存:路径 / run_python( / 伪 tool_call JSON 等）。
        在无 tool_calls 的纯文本响应里命中这些标记，即视为撒谎，不当最终结果展示。"""
        t = text or ""
        if not t:
            return False
        markers = (
            "[工具]", "[工具调用]", "run_python(", "run_python (",
            "已保存:", "✅ 已保存", "🔄 重新生成", "✏️ 改写问题",
            "工具调用：", "工具调用:", "调用了工具", "已调用工具",
            "已写入文件", "代码已保存", "文件已生成", "文件已创建",
            '"name": "run_python"', '"name": "write_file"', '"name": "web_search"',
            '"name": "image_gen"', '"name": "run_command"',
        )
        if any(m in t for m in markers):
            return True
        # 伪 tool_call JSON 结构：含 "function" 且像工具调用
        low = t.lower()
        if '"function"' in low and ("run_python" in low or "write_file" in low
                                    or "web_search" in low or '"name"' in low):
            return True
        return False

    def _step_tools_all_failed(self):
        """v4.66：检查本轮工具结果是否全部是失败（用于死循环护栏）。
        找到最后一条带 tool_calls 的 assistant 消息，其后的 tool 消息即本轮结果；
        若全部含失败标志则返回 True，否则 False。"""
        last_assistant = -1
        for i in range(len(self.messages) - 1, -1, -1):
            m = self.messages[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                last_assistant = i
                break
        if last_assistant < 0:
            return False
        tool_msgs = [m for m in self.messages[last_assistant + 1:]
                     if m.get("role") == "tool"]
        if not tool_msgs:
            return False
        for tm in tool_msgs:
            c = tm.get("content", "") or ""
            if not any(k in c for k in self._FAIL_MARKERS):
                return False
        return True

    # v4.102 fix10：纯内容创作豁免——区分「写文字内容」（不需工具）vs「写文件/产物」（需工具）。
    # _CREATE_VERB_KW：内容创作动词（产出是文字/文本内容）
    _CREATE_VERB_KW = (
        "写文案", "写一篇", "写一段", "写个", "写点", "写一下", "写文章", "写小说",
        "写故事", "写个故事", "写剧本", "写脚本", "写大纲", "写总结", "写回答",
        "写内容", "写文字", "写标题", "写简介", "写介绍", "写个介绍", "帮我写",
        "给我写", "写嘛", "写个嘛", "写个什么", "写点什么", "写一篇什么",
        "创作", "编个", "编个故事", "编文案", "生成文案", "生成文章", "生成内容",
        "生成一段", "生成一篇", "生成大纲", "生成脚本", "生成故事", "生成标题",
        "生成回答", "生成介绍", "生成摘要", "生成总结", "写作文", "写作业",
        "拟个", "拟一个", "拟定", "起草", "改写", "润色", "扩写", "起个标题", "起标题",
    )
    # _ARTIFACT_KW：需要真实落盘/产物的词——命中即不豁免（应走工具）。
    # 注意：不放宽泛的「图片/视频」（会误伤「口播视频的文案」这类内容创作场景词），
    # 只放真正指「文件/表格/文档类产物」的明确词。
    _ARTIFACT_KW = (
        "写文件", "写入文件", "保存", "保存到", "导出", "导出文件", "导出到",
        "存到", "存文件", "存盘", "落盘", "生成文件", "创建文件", "新建文件",
        "建个文件", "生成excel", "生成表格", "生成csv", "生成ppt", "生成word",
        "生成pdf", "生成报告文件", "生成一个文件", "制作表格", "表格文件",
        "做成表格", "整理成表格", "统计成表", "做成word", "做成pdf", "做成ppt",
        "生成报表", "输出文件", "输出为", "保存为",
    )
    # _REAL_TOOL_KW：明确工具操作/数据动作——命中即不豁免（应走工具/数据动作）。
    _REAL_TOOL_KW = (
        "搜索", "搜一下", "查一下", "上网查", "爬取", "爬虫", "数据分析", "数据处理",
        "运行", "python", "代码", "脚本", "执行", "计算", "统计分析", "报表",
        "打开文件", "读取文件", "读文件", "提取", "解析", "爬", "抓取",
        "做张表", "整理", "批量", "翻译", "总结文档", "分析文档",
        # 明确的多媒体/产物生成（走 image_gen / video_gen 工具）：
        "生成图片", "生成一张图片", "生图", "画一张", "画图片", "配个图", "做张图",
        "出图", "生成视频", "生成一段视频", "生成个视频", "做视频", "做一段视频",
        "做个视频", "剪辑", "配音", "生成一张", "做一段视频", "生成一个小视频",
    )

    # 任务意图关键词：命中即认为本次需要执行操作（搜索/写文件/分析等），
    # 用于循环护栏——还没真正动手时强制模型调用工具，避免「说要搜却不动」。
    _ACTION_KEYWORDS = (
        "搜索", "搜", "查", "查一下", "写", "生成", "做", "执行", "分析", "数据",
        "文件", "定时", "截图", "打开", "运行", "创建", "整理", "发", "导出",
        "翻译", "总结", "爬", "抓", "下载", "安装", "监控", "提醒", "找",
        # v4.60：催促类——用户催着动但没指定具体动作
        "继续", "动起来", "动", "干活", "动手", "开始", "快点", "麻利", "赶紧",
        "行动", "快", "加速",
    )

    # v4.80：意图→工具路由关键词（能明确推断要调哪个工具时直接指定 tool_choice，
    # 避免模型把「生成视频」这类明确动作退化成反复调 sys_info 查能力清单）
    _VIDEO_GEN_KW = (
        "视频", "短视频", "离谱视频", "搞笑视频", "mv", "文生视频", "图生视频",
        "视频生成", "视频创作", "生成视频", "做视频", "来个视频", "做条视频",
        "ai视频", "ai生成视频", "一条视频", "生成个视频",
    )
    _IMAGE_GEN_KW = (
        "生成图片", "生图", "画图", "画一张", "生成一张", "配图", "ai绘画",
        "出图", "做张图", "画个图", "画幅图", "ai生图", "画张",
    )
    _STATUS_KW = (
        "进度", "如何", "怎么样", "咋样", "好了吗", "完成了吗", "完成没",
        "到哪", "到哪了", "状态", "做了吗", "生成了没", "出来没", "出来了吗",
        "还好吗", "还在吗", "啥情况", "怎么样了", "进行到", "现在怎样",
    )
    _BARE_VERB_KW = (
        "生成", "做", "出", "画", "用agnes", "做啊", "生成啊", "重做", "重新", "再来",
    )
    # v4.103 五次：浏览器路由——「打开xx网页/网址/链接」强制 browser_open。
    # 背景：用户说「打开知乎网页」，模型不选 browser_open 反而用 window_list/process_start
    # 自行拉 Edge（拉起后无窗口、还触发重复调用护栏卡死）。路由词表：
    # ① URL 特征直接命中；② 站点/网页词 与 打开类动词 共现才命中（防「打开文件」误伤）。
    _BROWSER_URL_KW = ("http://", "https://", "www.", ".com/", ".cn/")
    _BROWSER_SITE_KW = (
        "网页", "网址", "网站", "链接", "浏览器", "页面",
        "知乎", "微博", "b站", "bilibili", "淘宝", "京东", "百度", "哔哩哔哩",
        "谷歌", "google", "github", "csdn", "掘金", "搜狐", "网易", "腾讯网",
    )
    _BROWSER_OPEN_VERB_KW = ("打开", "访问", "浏览", "逛逛", "逛一下", "看一下", "看看", "截", "抓取")

    # v4.66：工具结果失败标志——用于「连续工具失败」死循环护栏。
    # 仅收录强错误特征，避免普通文本里出现「失败」二字被误判。
    _FAIL_MARKERS = (
        "文件不存在", "命令执行失败", "读取失败", "写入失败", "拒绝：",
        "命令执行超时", "Out-File", "FileOpenFailure", "NotSupportedException",
        "不是内部或外部命令", "未能找到路径", "找不到路径", "无法找到路径",
    )

    # 选题/盘点/列方向 类关键词（v4.56）：命中时不算"需要执行"——AI 应直接出文本，
    # 不要被 force_required 强行推到调搜索。
    _TOPIC_KEYWORDS = (
        "列方向", "列选题", "想几个", "盘点", "选题", "爆款方向", "做什么内容",
        "给我想", "给我建议", "推荐方向", "哪些方向", "哪些选题", "哪些赛道",
        "有什么选题", "给我几个", "列几个", "出主意", "给我列", "写什么",
    )
    # 隐式匹配：内容平台 + 方向词（v4.56 补）——避免「小红书爆款」被推到搜狗
    _TOPIC_PLATFORMS = ("小红书", "抖音", "视频号", "公众号", "知乎", "微博", "b站", "bilibili", "快手")
    _TOPIC_DIR_KEYWORDS = (
        "爆款", "趋势", "风向", "方向", "赛道", "品类", "选题", "增长", "画像",
        "做什么", "写什么", "发什么", "内容", "玩法", "风格", "推荐", "建议",
        "榜单", "最新", "热门", "爆火", "火",
    )

    def _route_force_tool(self, text, prev_text=None):
        """v4.80：依据用户【当前】原话推断最该调用的工具，返回工具名或 None。
        仅用于 step1 强制指定 tool_choice：视频优先于图片（『图生视频』含『图』但属视频）；
        用户点名 Agnes 生成时默认路由 video_gen（本项目 Agnes 主力做视频）。

        关键修复（v4.80b）：
        - 状态类追问（『进度如何』『好了吗』『状态』等）一律返回 None，不强制任何工具，
          避免对已生成的任务反复重触（上一版用『最近 2 条拼接』导致追问被拼上上文的『视频』而强重触）。
        - 仅当【当前句只是裸续接动词】（如『用Agnes生成』『生成』）且自身无对象时，
          才用上一句上下文兜底路由（覆盖『用Agnes生成啊』这类短续接）。"""
        if not text:
            return None
        t = text.lower()
        # 1) 状态追问优先拦截：进度/如何/好了吗/状态 → 不强制工具，让模型正常汇报
        if any(k in text for k in self._STATUS_KW):
            return None
        # 2) 当前消息已含明确对象词 → 直接路由
        if any(k in text for k in self._VIDEO_GEN_KW):
            return "video_gen"
        if any(k in text for k in self._IMAGE_GEN_KW):
            return "image_gen"
        # v4.103 五次：浏览器路由（先于搜索——「打开xx网页搜一下」应进浏览器而非纯搜索）
        if any(k in t for k in self._BROWSER_URL_KW):
            return "browser_open"
        if (any(k in text for k in self._BROWSER_SITE_KW)
                and any(v in text for v in self._BROWSER_OPEN_VERB_KW)):
            return "browser_open"
        if "agnes" in t:
            if any(k in text for k in self._IMAGE_GEN_KW):
                return "image_gen"
            return "video_gen"
        if "搜索" in text or "搜" in text:
            return "web_search"
        # 3) 当前句仅含裸续接动词、无对象 → 用上一句上下文兜底（仅限此场景）
        if prev_text and any(v in t for v in self._BARE_VERB_KW):
            pt = prev_text.lower()
            if any(k in pt for k in self._VIDEO_GEN_KW):
                return "video_gen"
            if any(k in pt for k in self._IMAGE_GEN_KW):
                return "image_gen"
        return None

    def _content_creation_only(self, text):
        """v4.102 fix10：判断是否为「纯内容创作」——用户要的是文本产出（文案/文章/内容/
        大纲/脚本/总结/回答等），不需要调用任何工具。这类任务强制 tool_choice=required 只会
        让模型（尤其 Agnes）「必须调工具但不知道该调啥」→ 返回 0 内容 + 0 工具（用户实证：
        『帮我写一段口播文案』→ content 空、界面『Agent 完成』但无文案输出）。
        区分依据：命中内容创作动词（写/生成/做/创建/编/改），且产出对象是文本级内容，
        且【不含】需要真实落盘/产物的工具词（文件/保存/导出/写入/打开/表格/SQL/图片/视频/
        截图/爬取/运行/代码/数据分析等）。
        """
        if not text:
            return False
        t = text.lower()
        # 纯内容创作动词（写/创作/编/拟/起草/润色等）——命中即大概率是文字产出
        has_create = any(k in text for k in self._CREATE_VERB_KW)
        if not has_create:
            return False
        # 产物生成意图：同时命中「产物对象」（视频/图片/文件/表格/PDF/PPT/Word/Excel/截图）
        # 和「产物动词」（做/生成/制作/创建/出/画/剪辑/配音）→ 明确要产物，不豁免。
        # 注意：匹配对象词时不看它前面是否紧贴动词，只做「同句共现」判断——
        # 「写一段口播视频的文案」：视频是场景词，但句中无产物动词 → 豁免；
        # 「生成一段口播视频」：句中有「生成」+「视频」→ 不豁免。
        _obj = ("视频", "图片", "图片", "文件", "表格", "ppt", "pdf", "word", "excel", "截图",
                "csv", "海报", "插画", "配音")
        _verb = ("做", "生成", "制作", "创建", "出", "画", "剪辑", "配音")
        if any(o in text for o in _obj) and any(v in t for v in _verb):
            return False
        # 命中「真实落盘/产物」工具词（文件/保存/导出/搜索/运行等）→ 不是纯内容创作
        if any(k in text for k in self._ARTIFACT_KW):
            return False
        if any(k in text for k in self._REAL_TOOL_KW):
            return False
        # 未被以上排除，且命中文本产出对象提示 → 视为纯内容创作
        return True

    def _detect_action_intent(self, messages):
        """从最后一条用户消息判断本次是否需要执行操作。"""
        text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                c = msg.get("content", "")
                if isinstance(c, str):
                    text = c
                break
        if not text:
            return False
        t = text.lower()
        is_topic = any(k in text for k in self._TOPIC_KEYWORDS)
        if not is_topic:
            # 隐式：平台 + 方向词 且无"搜/查"字 → 视作选题
            has_platform = any(p in text for p in self._TOPIC_PLATFORMS)
            has_dir = any(k in text for k in self._TOPIC_DIR_KEYWORDS)
            if has_platform and has_dir and "搜" not in t and "查" not in t:
                is_topic = True
        if is_topic and not any(k in text for k in self._ACTION_KEYWORDS):
            return False
        # v4.102 fix10：纯内容创作（写文案/写文章/生成一段内容）优先豁免——即便文中
        # 提到「视频/图片」作为场景（如「口播视频的文案」），只要产出是文字内容就不强制
        # 工具，否则弱模型「必须调工具但不知道该调啥」→ 空 content，无回复气泡。
        if self._content_creation_only(text):
            return False
        # v4.102 fix10：明确要求生成图片/视频（产物是多媒体文件）→ 必须走工具
        if any(k in text for k in self._IMAGE_GEN_KW) or any(k in text for k in self._VIDEO_GEN_KW):
            return True
        return any(k in text for k in self._ACTION_KEYWORDS)

    # ---------- v4.102 fix12：token 预算熔断 ----------
    def _collect_step_tools(self, resp):
        """累计本步调用过的工具名（去重），供任务轨迹写回使用。"""
        try:
            for _tc in (resp or {}).get("tool_calls") or []:
                _n = (_tc.get("function") or {}).get("name", "")
                if _n and _n not in self._tools_used:
                    self._tools_used.append(_n)
        except Exception:
            pass

    def _tick_token_budget(self, resp):
        """累加本步 token 用量并按预算告警/熔断。返回 False 表示已熔断，应停止循环。

        设计要点：
        - 预算为 0（config 里禁用）时直接放行，行为**完全回退**到 fix12 之前，不伤现有体验；
        - 达告警比例（默认 80%）提前提示一次，让用户有机会主动收手；
        - 达硬上限则熔断：输出已完成的阶段性结果 + 明确告知如何调高预算；
        - 付费通道（DeepSeek）可单独设更紧的预算，防烧钱。
        """
        if not getattr(self, "_token_budget", 0):
            return True
        try:
            usage = (resp or {}).get("usage") or {}
            step_tokens = int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            step_tokens = 0
        self._used_tokens += step_tokens
        if (resp or {}).get("model"):
            self._last_model = str(resp.get("model"))
        if str((resp or {}).get("channel") or "") == "deepseek":
            self._used_tokens_ds += step_tokens
        # 付费通道单独预算（0 = 跟随总预算），两者取更紧的那个
        _limit = self._token_budget
        if self._token_budget_ds and self._used_tokens_ds:
            _limit = min(self._token_budget, self._token_budget_ds)
        if (not self._token_warned
                and self._used_tokens >= self._token_budget * self._token_warn_ratio):
            self._token_warned = True
            self._emit_status(
                f"⚠️ 已用 {self._used_tokens} tokens（预算 {self._token_budget} 的 "
                f"{int(self._token_warn_ratio * 100)}%），接近上限将自动停止")
        if self._used_tokens >= _limit:
            self._token_budget_hit = True
            self.stream_commit.emit(
                f"\n\n🛑 已触达 token 预算上限（{self._used_tokens}/{_limit}），已自动停止，"
                f"以上为已完成的阶段性结果。\n如需继续：把设置里的 `agent_token_budget`"
                f"（当前 {self._token_budget}）调高，或把任务拆小后重发。")
            self._emit_status(
                f"🛑 触达 token 预算（{self._used_tokens}/{_limit}），已停止")
            return False
        return True

    # ---------- v4.102 fix12：任务退出统一写回轨迹 ----------
    def _persist_task_memory(self, mw, duration_s=0, steps=0):
        """长任务退出时写回一条「任务级」轨迹，实现「踩过的坑下次不再踩」。

        只在确属长任务（执行过工具或步数 > 2）时写，避免普通问答把轨迹库灌水。
        与 harness refine 互补：refine 沉淀技能级经验（待审核），本函数沉淀任务级
        经验（直接落盘可检索）。全程 try 包裹，写失败绝不影响任务正常收尾。
        """
        try:
            if not (getattr(self, "_tools_used", None) or steps > 2):
                return
            import trace_log
            # 结局推断：熔断 > 用户停止 > 超时 > 步数耗尽 > 成功
            if self._token_budget_hit:
                outcome = "token_budget"
            elif self._stop_requested:
                outcome = "stopped"
            elif duration_s and duration_s > 180:
                outcome = "timeout"
            elif steps and steps >= getattr(self, "_max_steps", MAX_AGENT_STEPS):
                outcome = "max_steps"
            else:
                outcome = "success"
            pitfall = None
            if outcome == "token_budget":
                pitfall = ("任务 token 超预算被熔断；下次先拆分任务或限制工具轮次，"
                           "避免大量升舱 DeepSeek 付费通道")
            elif outcome == "max_steps":
                pitfall = "步数耗尽仍未收敛，可能卡在工具循环；下次先明确产出物再开工"
            elif outcome == "stopped":
                pitfall = "被用户中途停止，可能方向不符；下次先确认目标再执行"
            trace_log.append_task_trajectory(
                getattr(mw, "cfg", None) or {},
                task=self._goal_hint()[:200],
                outcome=outcome,
                pattern=("调用过的工具：" + "、".join(self._tools_used[:10])
                         if self._tools_used else None),
                pitfall=pitfall,
                tools=self._tools_used,
                tokens=getattr(self, "_used_tokens", 0),
                steps=steps,
                duration_s=duration_s,
                model=getattr(self, "_last_model", ""),
            )
        except Exception as e:
            log.error("Agent 任务轨迹写回失败（已忽略）: %s", e)

    def run(self):
        mw = self.mw
        import config
        from config import APP_DIR
        mw.app_dir = APP_DIR  # 缓存供工具调用使用
        # v4.59 步级追踪器：记录每一步的输入/决策/工具/耗时
        _t0 = time.time()
        # v4.107：隔离会话不落盘轨迹（enabled=False），也不借用主会话 sid。
        _tracer = StepTracer(
            None if self._isolated else getattr(mw.store.active(), "sid", None),
            enabled=not self._isolated,
        )
        _tracer.start()
        _tracer.trace(0, "thinking", summary="Agent 启动", user_goal=self._goal_hint()[:200])
        _total_tools = 0
        # 技能清单已由 ui.py 构造 system 消息时通过 config.load_dynamic_skills() 注入，无需在此重复。
        self._nudged = False  # 防"光说不做"：仅允许触发一次硬化提示
        self._idle_steps = 0   # v4.60：连续空步计数（模型不调工具但回文本）
        self._question_steps = 0  # v4.61：连续追问步计数（防刷屏）
        self._fail_steps = 0     # v4.66：连续工具全失败步计数（防死循环刷屏）
        self._nudge_count = 0  # v4.60：nudge 触发次数，独立于 _force_retries
        MAX_DURATION = 180     # v4.60：单次 agent 最长 3 分钟，超时自动停止
        MAX_FAIL_STEPS = 3     # v4.66：连续工具全失败步上限，超出则早停防死循环
        self._fake_steps = 0   # v4.98：文字伪造工具调用计数（撒谎检测）
        MAX_FAKE_RETRIES = 3   # v4.98：伪造工具调用最多容忍次数，超出诚实提示并停止
        self._last_step_sig = None  # v4.102 fix8：上一步 (content, tool_calls) 签名
        self._repeat_steps = 0      # v4.102 fix8：连续重复步计数（防弱模型复读打转）
        self._needs_action = self._detect_action_intent(self.messages)
        # v4.102 fix12：token 预算熔断状态。budget=0 即完全禁用（行为回退到 fix12 前）。
        (self._token_budget, self._token_warn_ratio,
         self._token_budget_ds) = config.get_agent_token_budget(getattr(mw, "cfg", None))
        self._used_tokens = 0        # 本轮累计 token
        self._used_tokens_ds = 0     # 付费（DeepSeek）通道单独累计
        self._token_warned = False   # 80% 告警只提示一次
        self._token_budget_hit = False
        self._tools_used = []        # 本轮实际调用过的工具名（写轨迹用）
        self._last_model = ""
        self._any_tool_executed = False  # 本轮是否已真正执行过工具
        self._force_next = False  # 下一步强制模型调工具
        self._force_retries = 0
        self._cap_instructed = False  # v4.60o：能力已指示写记忆，避免重复注入
        MAX_FORCE_RETRIES = 3
        MAX_QUESTION_STEPS = 3  # v4.61：连续追问上限，超过则早停防刷屏
        # v4.104.1：步数预算改为 config.json 可覆盖（改参数不必再重打包）
        # 返回 (单轮步数, 续跑单轮步数, 续跑轮数)，总预算 = 单轮 × (1 + 续跑轮数)
        (self._max_steps, self._max_resume_steps,
         self._resume_budget) = config.get_agent_step_budget(getattr(mw, "cfg", None))
        self._start_heartbeat()  # v4.58：防长时间工具调用期间 UI 假死

        # v4.101：断点续传——任务开始即写「运行中」检查点（崩溃/强杀遗留可据此恢复）；
        # 用户主动停止时改写为 paused 保留，正常完成/自动停止则删除。
        # v4.108 M-13：isolated 隔离会话（导演台对话）跳过 checkpoint——
        # 否则会以主会话 sid 写检查点，主聊天界面冒出导演任务的「继续上次任务」按钮。
        if not getattr(self, "_isolated", False):
            try:
                _sid = getattr(mw.store.active(), "sid", None)
            except Exception:
                _sid = None
            try:
                task_resume.save_checkpoint(mw.cfg, {
                    "task_id": self.task_id,
                    "task_type": "agent",
                    "status": "running",
                    "sid": _sid,
                    "task": self._goal_hint()[:200],
                    "created": datetime.datetime.now().isoformat(timespec="seconds"),
                })
            except Exception:
                pass
        # 续传模式：注入「继续上次任务」提示，强制模型从断点接着干。
        # 注意 _seq=-1 哨兵：_sync_to_session 不会把它回写进会话（system 角色本就不该持久化）。
        if self.resume:
            _goal = self._goal_hint() or "（未知原始目标）"
            if len(_goal) > 400:
                _goal = _goal[:400] + "…"
            self.messages.append({
                "role": "system",
                "content": (
                    f"【继续上次任务】你之前被用户暂停，已完成的工作都在历史消息中"
                    f"（含工具调用与结果）。请直接阅读历史，从断点接着干原始目标"
                    f"（{_goal}），禁止再说「我来/我开始」之类的空话，必须调用真实工具推进。"
                ),
                "_seq": -1,
            })
            self._force_next = True

        # v4.60：自检请求检测——强制先调 sys_info 拿真实数据，防模型瞎编
        _SELF_CHECK_KW = ("自检", "能力盘点", "能力报告", "你能做什么", "你的功能",
                          "你的能力", "检查一下", "系统状态", "你有什么", "功能清单",
                          "全貌", "全景报告", "检查问题", "需要修复",
                          "检测一下", "测试一下", "工具能用吗", "工具正常吗",
                          "检查工具", "检测工具", "测试工具", "搜索功能",
                          "检查搜索", "搜索能用吗", "搜索正常吗", "工具自检")
        # v4.60o：用户要求"记住/保存"真实能力 → 强制 sys_info + 指示写长期记忆
        _PERSIST_CAP_PHRASE = ("记住你的能力", "记住能力", "把能力记", "记下你的能力",
                               "保存你的能力", "记住你能", "记住你都", "记下你的",
                               "记一下你的能力", "记住自己的能", "记住你的真实能力")
        _REMEMBER_VERBS = ("记住", "记下", "记一下", "记下来", "保存", "存一下", "记牢")
        _CAP_NOUNS = ("能力", "能做什么", "功能", "你会", "你能", "本事", "技能",
                      "工具", "模型", "会干")
        _force_sys_info = False
        _persist_cap = False
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                last_text = msg.get("content", "")
                if isinstance(last_text, str):
                    if any(kw in last_text for kw in _SELF_CHECK_KW):
                        _force_sys_info = True
                    if any(kw in last_text for kw in _PERSIST_CAP_PHRASE):
                        _persist_cap = True
                    elif (any(v in last_text for v in _REMEMBER_VERBS)
                          and any(n in last_text for n in _CAP_NOUNS)):
                        _persist_cap = True
                break
        _force_sys_info = _force_sys_info or _persist_cap
        # v4.80b：分别取当前句与上一句用户原话；当前句用于路由判断，上一句仅作续接指令兜底
        _user_msgs = [m.get("content", "") for m in self.messages
                      if m.get("role") == "user" and isinstance(m.get("content"), str)]
        _cur_user = _user_msgs[-1] if _user_msgs else ""
        _prev_user = _user_msgs[-2] if len(_user_msgs) >= 2 else ""

        for step in range(1, self._max_steps + 1):
            # v4.60：超时保护——超过 3 分钟自动停止，防止慢模型无响应
            elapsed = time.time() - _t0
            if elapsed > MAX_DURATION:
                self._emit_status(f"⏰ Agent 超时（{int(elapsed)}s），已自动停止。建议切回 DeepSeek 获得更快响应。")
                self.stream_commit.emit(f"\n\n⏰ 执行超时（{int(elapsed)}秒），已自动停止。Agnes 免费模型速度较慢，建议切回 DeepSeek。")
                break
            if self._stop_requested:
                self._emit_status("⏹ 已停止（用户请求）")
                break
            self._emit_status(f"Agent 思考中（第 {step} 步）…")
            self.stream_begin.emit()
            # 强制调用工具：①本次需要执行且尚未动手时，首步强制；②中途模型空谈时重试强制
            force_required = (self._force_next
                              or (step == 1 and self._needs_action
                                  and not self._any_tool_executed))
            # v4.60/4.72：自检请求第一步强制调工具。
            # 提到搜索/搜 → 强制跑一次真实 web_search 验证搜索功能（而非只列清单）；
            # 否则强制 sys_info 拿能力清单。
            if _force_sys_info and step == 1:
                if "搜索" in last_text or "搜" in last_text:
                    _ft = "web_search"
                else:
                    _ft = "sys_info"
            elif step == 1:
                # v4.80b：意图路由——以当前句为主，能明确推断目标工具时直接指定，杜绝 sys_info 死循环
                _ft = self._route_force_tool(_cur_user, _prev_user)
            else:
                _ft = None
            try:
                resp = mw._agent_call(
                    self.messages, self.tool_defs,
                    on_delta=lambda d: self.stream_chunk.emit(d),
                    force_required=force_required,
                    force_tool=_ft,
                    force_complex=self._force_complex,
                )
            except Exception as e:
                log.error("Agent 调用失败: %s", e)
                self.tool_log.emit({"name": "错误", "args": "", "result": str(e)})
                # v4.108 H-04：失败要让用户在气泡里看得见，不再静默结束装"完成"。
                self.stream_commit.emit(f"\n\n⚠️ 模型调用失败：{e}")
                break

            # v4.102 fix12：累计工具名 + token 预算熔断（超预算即停，保留阶段性结果）
            self._collect_step_tools(resp)
            if not self._tick_token_budget(resp):
                break

            # v4.62：步内超时检查（单次模型调用最长 90s，避免下一轮开头才拦导致大幅超额）
            if time.time() - _t0 > MAX_DURATION:
                self._emit_status(f"⏰ Agent 超时（{int(time.time() - _t0)}s），已自动停止。")
                self.stream_commit.emit(
                    f"\n\n⏰ 执行超时（{int(time.time() - _t0)}秒），已自动停止。")
                break
            content = resp.get("content") or ""
            # v4.102 fix8：复读/原地打转护栏——模型连续 N 步输出完全相同的内容（或调用
            # 完全相同工具），说明弱模型在工具循环里原地复读、没有推进（用户实证：
            # 同一句话和最终结论反复出现两遍，还「聊完莫名其妙继续调工具」）。
            # 连续 2 次相同 → 判定打转，强制收敛：注入收敛指令并停止本轮。
            _sig = (content, tuple(
                (tc.get("function", {}).get("name", ""),
                 str(tc.get("function", {}).get("arguments", ""))[:200])
                for tc in (resp.get("tool_calls") or [])))
            if _sig == getattr(self, "_last_step_sig", None):
                self._repeat_steps = getattr(self, "_repeat_steps", 0) + 1
            else:
                self._repeat_steps = 0
            self._last_step_sig = _sig
            if self._repeat_steps >= 2:
                _emit_status = self._emit_status
                _emit_status("⚠️ 检测到重复输出（模型原地打转），已强制收敛…")
                self.stream_commit.emit(
                    "\n\n⚠️ 检测到刚才的内容和工具调用重复出现，已停止循环。"
                    "请基于已有信息直接给出最终回答。")
                self._force_next = False
                self._force_retries = 0
                break
            # v4.61：追问护栏——模型连续 K 步都在向用户追问而非推进任务（即便夹杂轻量工具调用），
            # 直接早停并提示补充信息，避免 content-gap-analysis 等技能带偏刷十几个问问题气泡。
            if content and self._looks_like_question(content):
                self._question_steps += 1
            else:
                self._question_steps = 0
            if self._question_steps >= MAX_QUESTION_STEPS:
                if content:
                    self.stream_commit.emit(content)
                self.stream_commit.emit(
                    "\n\n⚠️ 已连续多次只追问、未推进任务，已自动停止。"
                    "请补充关键信息（如平台 / 数据 / 目标 / 竞品）后重新发送，"
                    "或直接切普通模式获取建议。")
                self._emit_status("⚠️ 追问过多，已自动停止")
                break
            asst = {"role": "assistant", "content": content}
            # v4.59 追踪：模型决策
            _tracer.trace(step, "thinking",
                          model_summary=content[:200],
                          has_tool_calls=bool(resp.get("tool_calls")),
                          tool_count=len(resp.get("tool_calls") or []))
            if resp.get("tool_calls"):
                asst["tool_calls"] = resp["tool_calls"]
            self.messages.append(asst)

            if resp.get("tool_calls"):
                self._any_tool_executed = True  # 已真正动手，后续允许出最终文本
                self._idle_steps = 0            # v4.60：调了工具 → 清空空步计数
                # v4.102 fix8：强制调工具的目的一旦达成（本轮真调了工具），立即复位
                # _force_next——否则 tool_choice=required 永久卡死，模型永远无法输出
                # 纯文本最终结论（用户实证：给出结论后还被迫继续调工具）。
                self._force_next = False
                self._force_retries = 0
                _total_tools += len(resp["tool_calls"])
                _tracer.trace(step, "tool_call",
                              tools=[tc.get("function", {}).get("name", "") for tc in resp["tool_calls"]])
                if content:
                    self.stream_commit.emit(content)
                self._guard_blocked = False
                self._exec_tool_calls(resp["tool_calls"], mw, APP_DIR)
                # v4.62：工具执行（如 web_search）可能很久，执行后再次检查超时，避免久等
                if time.time() - _t0 > MAX_DURATION:
                    self._emit_status(f"⏰ Agent 超时（{int(time.time() - _t0)}s），已自动停止。")
                    self.stream_commit.emit(
                        f"\n\n⏰ 执行超时（{int(time.time() - _t0)}秒），已自动停止。")
                    break
                # v4.66：连续工具失败护栏——模型反复调工具但每步都报错（文件找不到、
                # PowerShell 语法错）却不换思路，会死循环刷屏。连续 3 步全失败则早停。
                if self._step_tools_all_failed():
                    self._fail_steps += 1
                else:
                    self._fail_steps = 0
                if self._fail_steps >= MAX_FAIL_STEPS:
                    self._emit_status("⏹ 工具连续失败，已自动停止。请检查路径/命令。")
                    self.stream_commit.emit(
                        "\n\n⏹ 检测到工具连续多次执行失败（如文件找不到或命令报错），"
                        "已自动停止以避免空转。请确认：聊天附件是否发送成功、命令是否用了 "
                        "PowerShell 语法（Get-ChildItem / 2>$null / Select-String）；或换一种"
                        "更直接的问法（例如直接说『总结这份报告』）。")
                    break
                # v4.60o/4.60p：sys_info 跑完后注入指令
                #  - "记住能力" → 用 remember 把真实清单写长期记忆
                #  - 普通自检   → 只准逐条复述清单，禁止编造（尤其 Obsidian/Webhook）
                if not self._cap_instructed:
                    _names = [tc.get("function", {}).get("name", "")
                              for tc in resp.get("tool_calls", [])]
                    if "sys_info" in _names or ("web_search" in _names and _force_sys_info):
                        self._cap_instructed = True
                        _instr = (self._CAP_PERSIST_INSTRUCTION if _persist_cap
                                  else self._SELF_CHECK_INSTRUCTION)
                        self.messages.append({
                            "role": "user", "content": _instr,
                            "_internal": True,
                        })
                # v4.60：去重护栏拦截的工具不算"有效执行"，不清空 _idle_steps
                if self._guard_blocked:
                    self._idle_steps = max(self._idle_steps, 1)
                # v4.59 checkpoint：每步工具执行完增量同步，崩了可从断点恢复
                self._sync_to_session(mw)
                # v4.108 H-05/M-15：同步后落盘检查点快照 + 心跳（原只有 run 开头一次）
                self._sync_agent_checkpoint(mw)
            else:
                # v4.98 撒谎检测器：模型用文字"演"工具调用（伪造 [工具]/✅ 已保存/
                # run_python( 等）却不真发 tool_call，这种内容不能当最终结果展示，
                # 必须强制它真正调工具。
                if content and self._looks_like_fake_tool_call(content):
                    self._fake_steps += 1
                    log.warning("Agent 检测到文字伪造工具调用（第 %d 次），强制真正执行",
                                self._fake_steps)
                    self._emit_status(
                        f"⚠ 检测到伪造工具调用（第 {self._fake_steps}/{MAX_FAKE_RETRIES} 次），"
                        "正在强制真正执行…")
                    self._force_next = True
                    if self._fake_steps >= MAX_FAKE_RETRIES:
                        self.stream_commit.emit(
                            "\n\n⚠️ Agent 多次用文字伪造工具调用（声称『已保存/已运行』"
                            "实际却未执行任何工具），已停止。这类任务建议切到 DeepSeek 模型——"
                            "免费模型函数调用能力弱，容易口头编造结果。请明确下达具体操作"
                            "（如：运行 Python 代码 XX、把内容写入文件 XX）。")
                        self._sync_to_session(mw)
                        break
                    self.messages.append({"role": "user", "content": self._AGENT_FAKE_TOOL_INSTR})
                    self._idle_steps = 0
                    continue
                # 正常纯文本分支：模型确实无工具可调用，给出最终回答
                if content:
                    self.stream_commit.emit(content)
                # v4.100：若上一轮工具调用被护栏拦截（_guard_blocked 仍为 True，
                # 纯文本轮不会重置它），说明模型已尝试调工具只是被拦，不应再因
                # "空回"触发 nudge 死循环，直接结束本轮，让已拦截的提示作为结果。
                if self._guard_blocked:
                    break
                # v4.60 重构：nudge 不再依赖 _any_tool_executed。
                # 模型连续 2 步空回（不调工具）→ 警告并强制；最多 3 次。
                # v4.102 fix8：仅当任务确实需要执行（_needs_action）时才 nudge 强制调工具。
                # 纯咨询/纯问答（如「QWen 能不能离线用」）模型直接回答就够，不该被
                # 强制调工具——否则会出现「给出结论后还莫名其妙继续调工具」的体验。
                self._idle_steps += 1
                if (self._needs_action and self._idle_steps >= 2
                        and self._nudge_count < MAX_FORCE_RETRIES):
                    self._nudge_count += 1
                    self._force_next = True
                    self._emit_status(f"⚠ 模型连续 {self._idle_steps} 步未调工具，第 {self._nudge_count}/{MAX_FORCE_RETRIES} 次强制…")
                    if not self._nudged:
                        self._nudged = True
                        _tracer.trace(step, "nudge", reason="模型空回不调工具", idle_steps=self._idle_steps)
                        self.messages.append({"role": "user", "content": self._AGENT_NUDGE})
                    continue
                if self._nudge_count >= MAX_FORCE_RETRIES:
                    self.stream_commit.emit("⚠️ 已多次尝试但 Agent 始终未调用工具。请明确指示具体操作（如：搜索XX、读取文件XX、运行Python代码XX）。")
                break
        else:
            # 单轮步数耗尽：不硬停，把当前进度回写会话，并自动续跑（最多 AGENT_RESUME_ROUNDS 轮）
            self._sync_to_session(mw)
            if self._resume_budget > 0:
                self._resume_budget -= 1
                self._emit_status("⏳ 单轮步数已用尽，正在从断点自动续跑…")
                # 续跑截断日志：每次触发记一条，便于跑几天观察频率以决定调参
                log.warning("Agent 续跑截断触发：单轮步数耗尽，自动续跑（resume_budget 剩余 %d，续跑上限步数 %d）",
                            self._resume_budget, self._max_resume_steps)
                # goal 单独抽取并温和截断，避免原始目标过长撑爆上下文（原 f-string 内联易被静默截断）
                _goal_hint = self._goal_hint() or "（未知原始目标）"
                if len(_goal_hint) > 400:
                    _goal_hint = _goal_hint[:400] + "…"
                self.messages.append({
                    "role": "system",
                    "content": (
                        f"【自动续跑提示】上一轮 Agent 已在 {self._max_steps} 步内执行了部分操作"
                        f"（请先阅读历史中的工具调用与结果）。任务尚未完成，请继续推进原始目标"
                        f"（{_goal_hint}），直接从断点接着干，禁止再说「我来搜索/我开始」之类"
                        f"的空话，必须调用真实工具推进。"
                    ),
                })
                # 续跑：复用同一 worker 实例，重置步数计数器但保留 messages 上下文
                self._force_next = True
                self._force_retries = 0
                resume_steps = range(1, self._max_resume_steps + 1)
                for rstep in resume_steps:
                    if self._stop_requested:
                        self._emit_status("⏹ 已停止（用户请求）")
                        break
                    self._emit_status(f"Agent 续跑（第 {rstep} 步）…")
                    force_required = self._force_next or (rstep == 1 and self._needs_action
                                                          and not self._any_tool_executed)
                    try:
                        resp = mw._agent_call(
                            self.messages, self.tool_defs,
                            on_delta=lambda d: self.stream_chunk.emit(d),
                            force_required=force_required,
                            # v4.108 M-12：续跑同样透传复杂模型开关——导演隔离会话恒复杂，
                            # 漏传会让纯文字续跑轮回落弱模型，退化成「文字演工具」。
                            force_complex=self._force_complex,
                        )
                    except Exception as e:
                        log.error("Agent 续跑调用失败: %s", e)
                        self.tool_log.emit({"name": "错误", "args": "", "result": str(e)})
                        break
                    # v4.102 fix12：续跑是烧 token 重灾区，同样累加工具名并做预算熔断
                    self._collect_step_tools(resp)
                    if not self._tick_token_budget(resp):
                        break
                    content = resp.get("content") or ""
                    asst = {"role": "assistant", "content": content}
                    if resp.get("tool_calls"):
                        asst["tool_calls"] = resp["tool_calls"]
                    self.messages.append(asst)
                    if resp.get("tool_calls"):
                        self._any_tool_executed = True
                        self._idle_steps = 0  # v4.60：调了工具 → 清空空步计数
                        if content:
                            self.stream_commit.emit(content)
                        self._guard_blocked = False
                        self._exec_tool_calls(resp["tool_calls"], mw, APP_DIR)
                        if self._guard_blocked:
                            self._idle_steps = max(self._idle_steps, 1)
                        # v4.59 checkpoint：续跑中每步工具执行完也同步
                        self._sync_to_session(mw)
                        # v4.108 H-05/M-15：续跑每步同样落盘快照 + 心跳
                        self._sync_agent_checkpoint(mw)
                    else:
                        if content:
                            self.stream_commit.emit(content)
                        # v4.60：续跑中也用空步计数，解耦 _any_tool_executed
                        self._idle_steps += 1
                        if self._idle_steps >= 2 and self._nudge_count < MAX_FORCE_RETRIES:
                            self._nudge_count += 1
                            self._force_next = True
                            self._emit_status(f"⚠ 续跑中模型连续 {self._idle_steps} 步未调工具，第 {self._nudge_count}/{MAX_FORCE_RETRIES} 次强制…")
                            if not self._nudged:
                                self._nudged = True
                                self.messages.append({"role": "user", "content": self._AGENT_NUDGE})
                            continue
                        break
                else:
                    # 续跑轮也耗尽：递归再续一轮（受 _resume_budget 限制）
                    log.warning("Agent 续跑截断：续跑轮也达到最大步数 %d，已暂停", self._max_resume_steps)
                    self.tool_log.emit({
                        "name": "提示",
                        "args": "",
                        "result": f"续跑轮也达到最大步数 {self._max_resume_steps}，已暂停（可点继续）",
                    })
            else:
                log.warning("Agent 步数截断：已达最大步数 %d，已停止（可点继续）", self._max_steps)
                self.tool_log.emit({
                    "name": "提示",
                    "args": "",
                    "result": f"已达到最大步数 {self._max_steps}，已停止（可点继续）",
                })

        # --- 自动记忆提取（对话结束后的归档步骤）---
        # v4.102 fix9：收尾段必须保证 done.emit() 必定触发（否则 UI 侧 _on_agent_done →
        # _reset_busy() 永不执行 → _busy 卡 True → 输入框永久锁死、状态栏一直「工作中」）。
        # 用户实证：内容已出（stream_commit 执行过）但 _sync_to_session / _auto_remember /
        # render 任一抛异常都会跳过 done.emit()，造成「回答完还在显示工作中、不能继续聊」。
        # 故将 done.emit() 与 _stop_heartbeat() 放入 finally 兜底。
        try:
            self._sync_to_session(mw)
            # v4.101：断点续传收尾——用户主动停止 → 标记 paused（保留检查点供「继续」）；
            # 正常完成/自动停止（超时·追问过多·工具连败）→ 删除检查点，不留「继续」入口。
            self.stopped_by_user = self._stop_requested
            try:
                if self._stop_requested:
                    task_resume.mark_paused(mw.cfg, self.task_id)
                else:
                    task_resume.mark_done(mw.cfg, self.task_id)
            except Exception:
                pass
            if not self._stop_requested:
                self._auto_remember(mw)
            # v4.102 fix12：所有退出路径（正常完成/熔断/停止/超时/步数耗尽）都在此汇流，
            # 统一写回一条任务级轨迹，实现「踩过的坑下次不再踩」。
            # v4.107：隔离会话跳过——导演会话的经验不该进全局经验库。
            if not self._isolated:
                self._persist_task_memory(mw, duration_s=round(time.time() - _t0, 1),
                                          steps=step)
            self.render.emit()
        except Exception as e:
            # 收尾归档失败不影响本轮收尾，绝不吞掉 done.emit()
            log.error("Agent 收尾归档异常（已忽略，仍会正常结束）: %s", e)
        finally:
            self._stop_heartbeat()  # v4.58：心跳线程随 agent 结束关闭
            _duration = round(time.time() - _t0, 1)
            try:
                _tracer.done(total_steps=step, total_duration_s=_duration)
            except Exception:
                pass
            self.done.emit()

    def _run_serial(self, tool_calls, mw, APP_DIR):
        """串行执行工具调用（含危险操作确认），统一走权限引擎决策。"""
        engine = mw.permission_engine
        total = len(tool_calls)
        # v4.120：批内完全重复调用（同名+同参数）去重——小模型偶发把同一 tool_call
        # 发两遍（实测 use_skill/image_gen 毫秒级双发），重复执行既浪费又产生双倍
        # tool_log/交付物。跳过的调用必须补占位 tool 回执，否则下轮 API 400（会话带毒）。
        _seen_sigs = set()
        for _seq_idx, tc in enumerate(tool_calls):
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}") or "{}")
            except Exception:
                args = {}
            _sig = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
            if _sig in _seen_sigs:
                log.info("批内重复调用已去重: %s", name)
                self._handle_tool_result(tc, name, fn,
                                         "（与本次批内上一调用完全重复，已自动去重）",
                                         [], None)
                continue
            _seen_sigs.add(_sig)
            # v4.108 M-25：卡片 id 用全局单调序号；started/finished 同值配对
            idx = self._next_tool_index()
            if self._stop_requested:
                self._emit_status("⏹ 已停止（用户请求）")
                result_str = "⏹ 已停止（用户请求）"
                deliverables = []
                schedule = None
                self._handle_tool_result(tc, name, fn, result_str, deliverables, schedule)
                continue
            self._emit_status(f"执行工具：{name}")

            # 发射工具开始信号
            self.tool_started.emit({
                "name": name, "args": args, "index": idx, "total": total
            })
            t0 = time.time()

            # 权限引擎统一决策（allowed / needs_user / reason）
            dec = engine.decide(name, args)
            if not dec.allowed:
                # 被引擎阻止（仅讨论/规划模式、路径越界等）
                result_str = dec.reason
                deliverables = []
                schedule = None
            elif dec.needs_user:
                # 危险/外部操作 → 弹确认框
                title, detail = self._confirm_text(name, args)
                ok = self._maybe_confirm(title, detail)
                if not ok:
                    result_str = "用户取消了该操作"
                    deliverables = []
                    schedule = None
                else:
                    # 用户确认：本会话记一笔信任该工具，避免同轮反复弹
                    engine.trust_tool(name)
                    try:
                        result_str, deliverables, schedule = tools.exec_tool(mw.cfg, APP_DIR, name, args)
                    except Exception as _te:
                        result_str = f"工具执行崩溃：{_te}"
                        deliverables, schedule = [], None
            else:
                # 允许且无需确认（只读 / 半自主 / 自动模式 / 白名单 / 会话信任）
                try:
                    result_str, deliverables, schedule = tools.exec_tool(mw.cfg, APP_DIR, name, args)
                except Exception as _te:
                    result_str = f"工具执行崩溃：{_te}"
                    deliverables, schedule = [], None

            dt = int((time.time() - t0) * 1000)
            # 发射工具完成信号
            self.tool_finished.emit({
                "name": name,
                "result_preview": str(result_str)[:200],
                "index": idx,
                "success": "失败" not in str(result_str)[:50] and "取消" not in str(result_str)[:50],
                "duration_ms": dt,
            })

            self._handle_tool_result(tc, name, fn, result_str, deliverables, schedule)

    @staticmethod
    def _confirm_text(name, args):
        """按工具类型生成友好的确认文案（覆盖所有需确认工具）。"""
        if name == "write_file":
            return "确认写入文件", (
                f"路径：{args.get('path', '')}\n"
                f"内容长度：{len(args.get('content', ''))} 字符")
        if name == "run_python":
            return "确认执行 Python 代码", args.get("code", "")[:500]
        if name in ("browser_open", "browser_click", "browser_fill", "browser_read"):
            bdetail = f"动作：{name}\n网址：{args.get('url', '')}\n"
            if args.get("selector"):
                bdetail += f"元素：{args.get('selector', '')}\n"
            if args.get("text"):
                bdetail += f"填入文本：{args.get('text', '')[:200]}\n"
            return "确认浏览器操作", bdetail
        if name == "skill_install":
            return "确认安装外部技能", (
                f"来源：{args.get('url', '')}\n\n"
                f"将从该链接拉取 SKILL.md，自动做安全审计后写入用户目录。\n"
                f"含有危险指令会被拒绝安装。")
        if name == "send_email":
            return "确认发送邮件", (
                f"收件：{args.get('to', '')}\n"
                f"主题：{args.get('subject', '')}\n"
                f"正文长度：{len(args.get('body', ''))} 字符")
        if name == "schedule":
            return "确认新增定时提醒", (
                f"内容：{args.get('message', '')}\n"
                f"延迟(秒)：{args.get('delay', '')}  重复(秒)：{args.get('repeat', 0)}")
        if name.startswith("webhook_"):
            return "确认 Webhook 操作", f"动作：{name}"
        if name.startswith("db_"):
            return "确认数据库操作", (
                f"动作：{name}\n{json.dumps(args, ensure_ascii=False)[:300]}")
        if name in ("run_command",):
            return "确认执行命令", args.get("command", "")
        if (name.startswith("mouse_") or name.startswith("keyboard_")
                or name.startswith("app_")
                or name in ("window_focus", "process_kill", "process_start")):
            return "确认桌面/系统控制", (
                f"动作：{name}\n{json.dumps(args, ensure_ascii=False)[:300]}")
        return "确认执行操作", (
            f"工具：{name}\n{json.dumps(args, ensure_ascii=False)[:300]}")

    def _run_concurrent(self, tool_calls, mw, APP_DIR):
        """并发执行工具调用（所有 tools 无数据依赖）"""
        max_workers = min(len(tool_calls), 5)
        total = len(tool_calls)
        self._emit_status(f"并发执行 {total} 个工具（{max_workers} 线程）…")

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            _seen_sigs = set()  # v4.120：批内完全重复调用去重（同 _run_serial）
            for _seq_idx, tc in enumerate(tool_calls):
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except Exception:
                    args = {}
                _sig = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                if _sig in _seen_sigs:
                    log.info("批内重复调用已去重: %s", name)
                    self._handle_tool_result(
                        tc, name, fn,
                        "（与本次批内上一调用完全重复，已自动去重）", [], None)
                    continue
                _seen_sigs.add(_sig)
                # v4.108 M-25：卡片 id 用全局单调序号（并发批内各工具唯一）
                idx = self._next_tool_index()
                if self._stop_requested:
                    self._emit_status("⏹ 已停止（用户请求）")
                    break
                # 发射工具开始信号
                self.tool_started.emit({
                    "name": name, "args": args, "index": idx, "total": total
                })
                t0 = time.time()
                futures[pool.submit(tools.exec_tool, mw.cfg, APP_DIR, name, args)] = (tc, t0, idx)

            for future in as_completed(futures):
                # v4.58：stop 后跳过剩余并发工具的结果渲染（工具已提交无法取消，但不渲染）
                if self._stop_requested:
                    continue
                tc, t0, idx = futures[future]
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    result_str, deliverables, schedule = future.result()
                except Exception as e:
                    result_str = f"工具执行异常：{e}"
                    deliverables = []
                    schedule = None

                dt = int((time.time() - t0) * 1000)
                # 发射工具完成信号
                self.tool_finished.emit({
                    "name": name,
                    "result_preview": str(result_str)[:200],
                    "index": idx,
                    "success": "失败" not in str(result_str)[:50] and "取消" not in str(result_str)[:50],
                    "duration_ms": dt,
                })

                self._handle_tool_result(tc, name, fn, result_str, deliverables, schedule)

        # v4.108 H-03 修复：停止后为所有未拿到回执的 tool_calls 补占位 tool 消息——
        # assistant.tool_calls 已落库，缺回执会让下一轮 API 400（会话带毒）。
        if self._stop_requested:
            _done_ids = set()
            for m in self.messages:
                if m.get("role") == "tool":
                    _done_ids.add(m.get("tool_call_id"))
            for tc in tool_calls:
                if tc.get("id") not in _done_ids:
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": "（系统提示：用户已停止任务，该工具调用未完成执行。）",
                    })

    def _exec_tool_calls(self, tool_calls, mw, APP_DIR):
        """执行一批工具调用（串行/并发由权限引擎决策），供主循环与续跑共用。"""
        # v4.93：run_workflow 是「子代理并行」入口——不走通用 exec_tool，直接触发任务图，
        # 把工作流最终结果作为 tool 结果喂回，让模型基于子代理产出整合最终回答。
        _wf_tc = next((tc for tc in tool_calls
                       if tc.get("function", {}).get("name") == "run_workflow"), None)
        if _wf_tc is not None:
            _args = self._safe_args(_wf_tc)
            _wf_type = _args.get("type", "research_write")
            _task = _args.get("task", "")
            self._emit_status(f"🔄 触发子代理工作流：{_wf_type}")
            # v4.108 M-25：workflow 也走全局工具序号 + started/finished 配对，
            # 让 UI 能显示一张「子代理工作流」卡片并在完成时原位替换。
            _wf_idx = self._next_tool_index()
            self.tool_started.emit({
                "name": "run_workflow", "args": {"type": _wf_type},
                "index": _wf_idx, "total": 1,
            })
            _t0 = time.time()
            _out = self._run_workflow(_wf_type, _task)
            self.messages.append({
                "role": "tool",
                "tool_call_id": _wf_tc.get("id", ""),
                "content": _out or "（工作流已完成，无文本输出）",
            })
            self.tool_finished.emit({
                "name": "run_workflow",
                "result_preview": str(_out)[:200],
                "index": _wf_idx,
                "success": bool(_out),
                "duration_ms": int((time.time() - _t0) * 1000),
            })
            # v4.108 H-02 修复：同批其余 tool_calls 必须补占位回执——
            # assistant 消息已带完整 tool_calls 落库，缺 tool 回执会让下一轮 API 400。
            for tc in tool_calls:
                if tc is _wf_tc:
                    continue
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": "（系统提示：该调用已被同批的 run_workflow 子代理工作流取代，无需单独执行。）",
                })
            return
        # v4.100：remember 会话级节流——闲聊或误操作下模型可能反复写记忆刷屏，
        # 限制单次 Agent 运行内 remember 调用累计不超过 2 次，超出部分直接拦截并提示，
        # 其余工具正常执行。
        if not hasattr(self, "_remember_calls"):
            self._remember_calls = 0
        _rem_in_batch = sum(1 for tc in tool_calls
                            if tc.get("function", {}).get("name") == "remember")
        if _rem_in_batch and self._remember_calls >= 2:
            _kept, _blocked_rem = [], []
            for tc in tool_calls:
                if tc.get("function", {}).get("name") == "remember":
                    _blocked_rem.append(tc)
                else:
                    _kept.append(tc)
            for tc in _blocked_rem:
                fn = tc.get("function", {})
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": "（系统提示：本次会话已写入足够记忆，为避免刷屏不再重复写入。如需记录请用更精简的事实。）",
                })
            self._guard_blocked = True  # 通知主循环：本次工具调用已被拦截
            tool_calls = _kept
        elif _rem_in_batch:
            self._remember_calls += _rem_in_batch
        # 防"同参数疯狂调工具"护栏（v4.57）：同一批 (name, args) 签名与最近已执行的
        # 完全相同 → 判定为死循环空转，整批拦截并塞一条 tool 结果，逼模型出正文而非继续空转。
        if not hasattr(self, "_recent_tool_sigs"):
            self._recent_tool_sigs = []
        new_sigs = []
        dup_all = True
        for tc in tool_calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}") or "{}")
            except Exception:
                args = fn.get("arguments", "")
            sig = (fn.get("name", ""), json.dumps(args, ensure_ascii=False))
            new_sigs.append(sig)
            if sig not in self._recent_tool_sigs:
                dup_all = False
        if dup_all and self._recent_tool_sigs:
            self.tool_log.emit({
                "name": "护栏", "args": "",
                "result": "检测到重复工具调用，已拦截，请直接基于已有信息给出正文回答，不要再次调用相同工具。",
            })
            for tc in tool_calls:
                fn = tc.get("function", {})
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": "（系统提示：该工具调用已重复执行且被拦截，请直接给出正文回答，不要再调用相同工具。）",
                })
            self._guard_blocked = True  # v4.60：通知主循环，这次工具调用被拦截了
            return
        self._recent_tool_sigs.extend(new_sigs)
        self._recent_tool_sigs = self._recent_tool_sigs[-12:]
        engine = mw.permission_engine
        decs = [engine.decide(
            tc.get("function", {}).get("name", ""),
            self._safe_args(tc)) for tc in tool_calls]
        # 任一被阻止 或 需用户确认 → 串行（逐条决策/确认）
        if any(not d.allowed for d in decs) or any(d.needs_user for d in decs):
            self._run_serial(tool_calls, mw, APP_DIR)
        else:
            # 全允许且无需确认 → 并发执行
            self._run_concurrent(tool_calls, mw, APP_DIR)

    def _goal_hint(self):
        """从对话历史里提取原始用户目标，用于续跑提示（避免模型忘了要干啥）。"""
        goal = ""
        for msg in self.messages:
            if msg.get("role") == "user":
                c = msg.get("content", "")
                if isinstance(c, str):
                    goal = c
        # 优先用 session.goal（首条 user 原话），否则用最后一条 user
        try:
            sess_goal = getattr(self.mw.store.active(), "goal", "") or ""
            if sess_goal:
                return sess_goal
        except Exception:
            pass
        return goal

    def _is_sync_writable(self, m):
        """v4.108 M-14：判定消息可否回写进可见会话历史。

        仅真实对话内容（assistant 正文/工具调用、tool 结果）可回写；system 提示、
        _internal 自检指令、nudge/伪造工具指令等一律排除——否则它们会以 user 角色
        气泡渲染进聊天记录，后续轮次还被当作用户发言发给模型，污染上下文与界面。
        """
        if not isinstance(m, dict):
            return False
        if m.get("role") == "system":
            return False
        if m.get("_internal"):
            return False
        c = str(m.get("content") or "")
        if c in (self._AGENT_NUDGE, self._AGENT_FAKE_TOOL_INSTR):
            return False
        return bool(m.get("tool_calls")) or m.get("role") in ("assistant", "tool")

    def _sync_to_session(self, mw):
        """把 agent 本轮产生的 assistant(tool_calls) + tool 结果回写到 session.messages，
        使『继续』时模型能看到真实断点（已搜了什么、结果是什么），而不是从零开始。

        对齐策略（④ 防御性优化）：
        - 主路径按 `_seq` 单调序号对齐：self.messages 中 `_seq` 大于 session 已记录最大
          `_seq` 的消息即为本次新增，直接写回；baseline 历史被打 `_seq=0` 哨兵，永不回写。
        - 兜底：若 session 尚无任何 `_seq`（首批运行），用内容指纹反向扫描定位历史边界
          （反向扫描避免重复消息命中错误首次出现），再写回其后的增量。
        """
        # v4.107：隔离会话（导演台独立对话条）一律不回写主会话历史。
        if getattr(self, "_isolated", False):
            return
        try:
            session = mw.store.active()
        except Exception:
            return
        if session is None:
            return
        existing = session.messages
        if not existing:
            return

        # 给 self.messages 中尚未编号的消息补单调序号（运行内递增，种子已在 __init__ 取
        # session 已有最大 _seq 之上，故跨运行不会撞号；baseline 已是 _seq=0 哨兵，跳过）。
        for m in self.messages:
            if isinstance(m, dict) and not isinstance(m.get("_seq"), int):
                self._seq_ctr += 1
                m["_seq"] = self._seq_ctr

        # 主路径：session 已记录过 _seq → 直接按序号对齐写回新增部分
        seqs = [m.get("_seq") for m in existing
                if isinstance(m, dict) and isinstance(m.get("_seq"), int) and m["_seq"] > 0]
        if seqs:
            max_seq = max(seqs)
            # v4.102 fix8：按 (role, content, tool_call_id) 指纹去重——stream_commit 已在
            # ui 侧把同一 assistant 文本追加进 session（无 _seq），此处若再原样追加会
            # 导致每条回复/工具结果渲染两遍（用户实证：结论重复两次、工具卡片重复）。
            _exist_sigs = set()
            for _em in existing:
                if isinstance(_em, dict):
                    _exist_sigs.add((_em.get("role"), str(_em.get("content")),
                                     str(_em.get("tool_call_id", ""))))
            for m in self.messages:
                if not (isinstance(m, dict) and isinstance(m.get("_seq"), int)
                        and m["_seq"] > max_seq):
                    continue
                if not self._is_sync_writable(m):  # v4.108 M-14：内部消息不回写
                    continue
                _sig = (m.get("role"), str(m.get("content")), str(m.get("tool_call_id", "")))
                if _sig in _exist_sigs:
                    continue
                _exist_sigs.add(_sig)
                existing.append(dict(m))
            return

        # 兜底：session 尚无 _seq（首批/旧会话），内容指纹反向扫描定位历史边界
        last_exist = existing[-1]
        start_idx = None
        # 反向扫描：取最后一条消息在 self.messages 中的「最后一次出现」，规避重复内容命中错误位置
        for i in range(len(self.messages) - 1, -1, -1):
            m = self.messages[i]
            if (m.get("role") == last_exist.get("role")
                    and m.get("content") == last_exist.get("content")
                    and m.get("role") != "system"):
                start_idx = i + 1
                break
        # v4.102 fix8：兜底追加也按指纹去重——stream_commit 已写入的同 content
        # assistant 不再重复追加（否则每条回复渲染两遍）
        _exist_sigs = set()
        for _em in existing:
            if isinstance(_em, dict):
                _exist_sigs.add((_em.get("role"), str(_em.get("content")),
                                 str(_em.get("tool_call_id", ""))))
        if start_idx is None:
            # 无法对齐（例如结构变化），保守追加尾部增量
            for m in self.messages[-1:]:
                if self._is_sync_writable(m):  # v4.108 M-14
                    _sig = (m.get("role"), str(m.get("content")), str(m.get("tool_call_id", "")))
                    if _sig in _exist_sigs:
                        continue
                    _exist_sigs.add(_sig)
                    existing.append(dict(m))
            return
        for m in self.messages[start_idx:]:
            if self._is_sync_writable(m):  # v4.108 M-14（原白名单 role in assistant/tool）
                _sig = (m.get("role"), str(m.get("content")), str(m.get("tool_call_id", "")))
                if _sig in _exist_sigs:
                    continue
                _exist_sigs.add(_sig)
                existing.append(dict(m))

    def _sync_agent_checkpoint(self, mw):
        """v4.108 H-05/M-15：每步工具执行后增量更新断点检查点。

        原实现只在 run() 开头写一次 checkpoint（仅任务描述）——崩溃后「继续上次任务」
        拿不到任何工具调用/结果快照，恢复上下文全丢（断点续跑空壳）。此处把最近
        可回写消息快照 + 心跳时间戳一起落盘（原子写），文件有界（最近 40 条）。
        """
        if getattr(self, "_isolated", False):
            return
        try:
            _sid = getattr(mw.store.active(), "sid", None)
        except Exception:
            _sid = None
        try:
            recent = [dict(m) for m in self.messages[-40:]
                      if isinstance(m, dict) and self._is_sync_writable(m)]
            task_resume.save_checkpoint(mw.cfg, {
                "task_id": self.task_id,
                "task_type": "agent",
                "status": "running",
                "sid": _sid,
                "task": self._goal_hint()[:200],
                "messages": recent,
            })
        except Exception:
            pass

    def _handle_tool_result(self, tc, name, fn, result_str, deliverables, schedule):
        """统一处理工具执行结果：发射信号、追加 tool message。

        v4.103 修复：交付物必须标准化为 (rel, kind, name) 三元组再 emit。历史 bug——
        browser_open 等工具返回裸路径字符串，emit(*d) 把路径字符串拆成数十个字符当
        多个参数 → TypeError；该异常发生在 Agent 主循环内且未被捕获，直接冲出 run()
        绕过末尾 finally 的 done.emit()，导致 _busy 永久 True、输入框锁死、「Agent 工作中」
        永远转圈。现对交付物做防御性标准化 + 异常吞掉，任何工具返回异常格式都不再卡死。
        """
        # 交付物：统一标准化为 (rel, kind, name) 三元组，杜绝 emit(*d) 把字符串拆成多参数
        for d in (deliverables or []):
            try:
                if isinstance(d, (tuple, list)) and len(d) >= 3:
                    self.deliverable_added.emit(str(d[0]), str(d[1]), str(d[2]))
                elif isinstance(d, (tuple, list)) and len(d) == 2:
                    self.deliverable_added.emit(str(d[0]), str(d[1]),
                                                os.path.basename(str(d[0])))
                elif isinstance(d, str):
                    _ext = d.lower()
                    _kind = ("image" if _ext.endswith((".png", ".jpg", ".jpeg", ".gif",
                                                       ".bmp", ".webp"))
                             else "video" if _ext.endswith((".mp4", ".avi", ".mov",
                                                            ".mkv", ".webm"))
                             else "file")
                    self.deliverable_added.emit(d, _kind, os.path.basename(d))
                else:
                    log.warning("跳过格式异常的交付物（非字符串/元组）: %r", d)
            except Exception as e:
                log.warning("交付物发射失败（已忽略，避免卡死 Agent）: %r -> %s", d, e)
        # 定时提醒
        if schedule:
            msg, delay_secs = schedule[0], schedule[1]
            repeat_secs = schedule[2] if len(schedule) > 2 else 0
            self.schedule_reminder.emit(int(delay_secs * 1000), msg, repeat_secs)

        # 持久化工具记录
        self.tool_log.emit({
            "name": name,
            "args": fn.get("arguments", ""),
            "result": result_str,
        })
        self.render.emit()
        self.messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "name": name,
            # v4.60：先 Token 压缩（去HTML/去重/智能截断），再取上限
            "content": compress(str(result_str), TOOL_RESULT_LIMIT),
        })

    def _auto_remember(self, mw):
        """对话结束后自动提取值得长期记忆的信息，写入 MEMORY.md。
        
        仅在对话中实际执行过工具调用时才触发，避免纯闲聊污染记忆库。
        """
        # v4.107：隔离会话（导演台对话）不写长期记忆——导演闲聊（"这镜太暗了"）
        # 不该被提炼成用户画像事实污染全局记忆。
        if getattr(self, "_isolated", False):
            return
        # 快速判断：本轮对话是否有实质性操作
        has_tool_msg = any(
            msg.get("role") == "tool" for msg in self.messages
        )
        if not has_tool_msg:
            return

        self._emit_status("正在提取长期记忆…")

        # 构造提取请求：复用已有对话历史 + 追加归档指令
        extraction_msgs = list(self.messages)
        extraction_msgs.append({"role": "user", "content": AUTO_REMEMBER_PROMPT})

        try:
            resp = mw._agent_call(
                extraction_msgs,
                [],  # 无须工具，纯文本回答
                on_delta=lambda d: None,
            )
            content = (resp.get("content") or "").strip()
        except Exception as e:
            log.warning("自动记忆 LLM 调用失败: %s", e)
            return

        facts = self._parse_remember_facts(content)
        if not facts:
            return

        count = 0
        for item in facts:
            try:
                if isinstance(item, dict):
                    fact = (item.get("content") or "").strip()
                    topic = item.get("topic")
                    ctype = item.get("category")
                else:
                    fact = (item or "").strip()
                    topic = None
                    ctype = None
                if not fact:
                    continue
                # v4.73：传 topic/type 触发冲突合并（同主题旧条目覆盖，防新旧并存）
                result = memory_store.append_memory(fact, type=ctype, topic=topic)
                if "已写入" in result or "已更新" in result:
                    count += 1
            except Exception as e:
                log.warning("自动记忆写入失败: %s", e)

        if count:
            self._emit_status(f"已自动记录 {count} 条进化记忆到 MEMORY.md")

    @staticmethod
    def _parse_remember_facts(raw):
        """从 LLM 回复中解析记忆条目列表，兼容对象/字符串/各种格式污染。

        v4.73：支持结构化对象 {"topic","category","content"}，topic 用于冲突合并。
        纯字符串条目 topic/category 记为 None（仅去重追加）。
        """
        raw = (raw or "").strip()
        if not raw:
            return []
        # 移除可能的 markdown 代码块包裹
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        # 尝试解析 JSON
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                out = []
                for f in parsed:
                    if isinstance(f, str):
                        out.append({"topic": None, "category": None, "content": f})
                    elif isinstance(f, dict):
                        content = (f.get("content") or f.get("fact") or "").strip()
                        if content:
                            out.append({
                                "topic": (f.get("topic") or f.get("subject") or None),
                                "category": (f.get("category") or f.get("type") or None),
                                "content": content,
                            })
                return out
            if isinstance(parsed, str):
                return [{"topic": None, "category": None, "content": parsed}]
        except json.JSONDecodeError:
            pass
        # 兜底：按行分割，清理编号前缀
        lines = []
        for line in raw.split("\n"):
            line = line.strip().lstrip("-*•123456789. ").strip()
            if line and not line.startswith("```") and not line.startswith("["):
                lines.append({"topic": None, "category": None, "content": line})
        return lines
