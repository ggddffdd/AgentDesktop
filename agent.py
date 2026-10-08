# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 — Agent 后台线程模块"""

import time
import json
import datetime
import os
import re
import threading
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal

import task_resume  # v4.101：断点续传检查点

from config import MAX_AGENT_STEPS, TOOL_RESULT_LIMIT, get_all_tools, WORKSPACE_DIR
import tools
import agent_text  # v4.216.0：纯文本判据层（从本类拆出）
import intent  # v4.225：统一意图对象（路由/是否动手/纯文本 三判据归一）
import task_state  # v4.225：任务状态机（子目标/产物落地检查）
import agent_loop  # v4.226：计划-执行-验证-总结 四阶段循环（判定层）
from agent_task_mixin import AgentTaskMixin  # v4.225：任务账本接线（抽出以保住拆分红线 agent.py<2400）
from agent_loop_mixin import AgentLoopMixin  # v4.226：四阶段循环接线（同上，抽出以保红线）
from agent_result_mixin import AgentResultMixin  # v4.226: 工具结果结算接线（从 agent.py 整体搬出，一并稳住红线）
import memory_store
import context_manager  # 审计修复 E1：context 工具按当前会话取管理器
from step_tracer import StepTracer  # v4.59 步级追踪
from task_graph import TaskGraph      # v4.60 任务图引擎
from agent_node import AgentNode      # v4.60 多Agent节点
from token_compressor import compress # v4.60 Token 压缩
from risk import command_danger_level  # ①-B 高危命令护栏：确认文案标记
from tool_contract import compute_impact_scope  # v4.220：确认弹窗影响范围（P1#5）

# v4.168.0：顶层 import 统一判据（与 UI / 模型调用层同源）。
# 显式声明而非只在函数内 import —— 这条判据漏打包会导致评价句误触生成工具。
try:
    import intent_guard            # noqa: F401
except Exception:                  # pragma: no cover
    intent_guard = None

# 自动记忆提取：对话结束后 LLM 自检是否产生了值得跨对话保留的信息
#
# v4.196 批⑫：新增 **来源申报** —— 这是记忆准入关的地基。
# 旧提示词只问「值不值得记」，不问「凭什么这么认为」，于是推断与用户原话
# 混在同一份介质里，越攒越脏。现在每条必须自报：
#   source     = user（用户亲口说） / tool（工具结果，必须绑 ⟦EV#n⟧）
#                / inference（你推断的） / ephemeral（本次任务临时状态）
#   confidence = 0~1，你自己有多大把握
# 机器据此验证，**不采信任何自称**（tool 来源要回验证据原文，inference 不入库）。
AUTO_REMEMBER_PROMPT = """
你是一个对话归档助手。请从以上对话中提取值得长期记忆的信息，以 JSON 数组格式输出。

每条是一个对象，包含以下字段：
- "topic"：该记忆的主题关键词（如"工作区路径""常用网址""偏好设置"），用于后续去重与覆盖；
- "category"：类别，取值为 "能力进化" / "用户偏好与约定" / "重要决策" 之一；
- "content"：1-2 句话的具体记忆内容，包含必要上下文（路径、数值、原因）；
- "source"：**信息来源，必须如实申报**，取值为：
    "user"      —— 用户在对话里亲口明确说过的事实（如"我姓张""我喜欢X"）；
    "tool"      —— 来自工具返回的结果（必须同时在 "ev" 字段给出证据编号，如 3 或 "EV#3"）；
    "inference" —— 你自己推断/总结的结论，用户没明说、工具也没直接给出；
    "ephemeral" —— 只对本次任务有效的临时状态（如"当前正在处理 Y 文件"），需要 "expires" 字段；
- "ev"：仅 source="tool" 时必填，本轮工具结果的证据编号（写在 ⟦EV#n⟧ 里的那个数字）；
- "confidence"：0~1，你对这条记忆真实性的把握程度（拿不准就写 0.5 以下）；
- "expires"：仅 source="ephemeral" 时给过期时间（如 "7天"、"30天"）。

铁律：
1. **不许把推断伪装成用户陈述**——用户没说过的，source 只能写 inference。
2. source="tool" 时 content 里的事实要素必须真能在该证据原文里找到，
   机器会逐字回验，对不上会被整条驳回。
3. 记不住就别记：没有相关信息输出空数组 []。

输出格式（纯 JSON 数组，不要 markdown 包裹）：
[{"topic":"工作区路径","category":"用户偏好与约定","source":"user","confidence":0.95,"content":"用户工作区路径为 ~/Documents 下对应项目目录"}]
"""

log = logging.getLogger("dsdesktop")

# v4.196 批⑬：decide().rule 里「**必须真的弹一次窗**」的集合。
# 这些规则被判成 needs_user 之后，还得绕得过 `_maybe_confirm` 的
# 「本次会话已全部信任」短路 —— 否则等于白判：用户早先点过一次信任，
# 装技能 / `rm -rf` / 高风险任务写入就再也不会被问了。
# 收成一个模块级常量：加规则不必改两处，探针也能按同一份清单核验。
FORCE_CONFIRM_RULES = (
    "always_confirm",       # v4.169.0 P0-3：装/建技能、删数据、删自动化
    "high_risk_exec",       # ①-B：高危命令/代码参数级（rm -rf / git push -f …）
    "task_risk_critical",   # v4.196 批⑬：高风险任务的写入与执行
)


_FILE_KINDS = ("file", "image", "video", "audio", "doc", "text",
               "pdf", "zip", "xlsx", "csv", "pptx", "md")


def _deliverable_satisfied(d, app_dir=""):
    """v4.222：产物级验收 —— 声明交付物是否真实落地。

    d = (path, kind, name)。返回 True=已满足。
    - 本地文件类：文件存在且大小 > 0；
    - web url / 无路径 / 非文件类：视为满足（无法本地验证，不误杀）。
    """
    try:
        p = str(d[0]) if d else ""
    except Exception:
        return True
    if not p:
        return True
    kind = str(d[1]).lower() if len(d) > 1 else ""
    if "://" in p:                       # 远程资源，本地无法验证
        return True
    if kind and kind not in _FILE_KINDS:
        return True                      # 非文件类交付物，不强制本地校验
    ap = p
    if not os.path.isabs(ap) and app_dir:
        ap = os.path.join(app_dir, p)
    if not os.path.isabs(ap):
        ap = os.path.abspath(ap)
    try:
        return os.path.isfile(ap) and os.path.getsize(ap) > 0
    except Exception:
        return False


# v4.222：断点幂等——不可幂等工具名单（恢复时需查重，避免副作用翻倍）。
_NON_IDEMPOTENT_TOOLS = frozenset({
    "write_file", "run_python", "exec_shell", "process_kill", "process_start",
    "app_kill", "app_close", "clean_recycle_bin", "send_email", "image_gen",
    "video_gen", "browser_open", "browser_navigate", "download_file",
})


def _tool_args_hash(name, args_sig):
    """工具调用的稳定指纹（name + 参数签名）。"""
    import hashlib
    return hashlib.sha256((name + ":" + (args_sig or "")).encode("utf-8")).hexdigest()[:16]


def _resolve_write_file_path(args_sig):
    """v4.232 断点 D：从 write_file 的参数 JSON 解析产物路径。

    按 tools.tool_write_file 同规则绝对化（相对路径落 WORKSPACE_DIR），
    供恢复查重时二次校验产物真实存在。解析失败/无 path → 返回 None（交由哈希回退）。
    """
    try:
        import json
        _a = json.loads(args_sig or "{}")
        _p = _a.get("path")
        if not _p:
            return None
        return os.path.abspath(os.path.join(WORKSPACE_DIR, _p))
    except Exception:
        return None


# v4.227（P2-1）：不可信内容边界整块外移到 `untrusted_boundary.py`。
# 这里只做 re-export —— `from agent import wrap_untrusted` 等既有调用点零改动可用，
# 判据/进包核验里按 agent 查的名字也照旧成立（单点真源在 untrusted_boundary）。
#
# 为什么外移而不是就地改：agent.py 2309 行，红线是 <2400（test_split_216 D2），
# 规则化清单 + 中和逻辑塞不进来。按既有纪律，外移不抬阈值。
#
# 本轮补的两个洞：
#   1. 清单从 8 项枚举改为规则驱动（prefix + exact），漏掉的 browser_*/legion_*/
#      webhook_events/clipboard_read/app_get_text/db_query 等一律纳入。
#   2. 包装前中和内容里的伪造闭合标签 —— 否则攻击者控制的正文写一个
#      `</untrusted_tool_output>` 就能把自己的边界提前关掉，等于自己解除边界。
from untrusted_boundary import (  # noqa: F401
    wrap_untrusted,
    _wrap_tool_content,
    wrap_tool_content,
    is_untrusted_tool,
    neutralize_forged_tags,
)


# v4.226（P1-1）：**已删除** `_AllowAllDecision`。
#
# 它是个 `allowed = True` 的兜底决策，在拿不到权限决策时替并发批次「按了放行」。
# 后果不是「少弹一次窗」，而是**整个最终闸门被短路**：exec_tool 的
# _permission_gate 认鸭子类型 `hasattr(perm_ctx, "allowed")`，拿到这个对象
# 就无条件放行 —— WRITE_LOCAL / EXEC / EXTERNAL 全部不拦。
#
# 为什么不改成 `allowed = False` 的拒绝对象，而是**什么都不传**（perm_ctx=None）：
# tools._permission_gate 对「无权限上下文」已有按风险分类的 fail-closed
# （READ 放行保兼容，其余一律拒，未登记工具落 EXTERNAL 天然被拒）。
# 那才是这套代码里既定的唯一口径。再叠一个 agent.py 私有的兜底对象等于
# 造第二套策略 —— 两套策略必然漂移，且这正是当初写下这个类的那一步。
#

class AgentWorker(AgentTaskMixin, AgentLoopMixin,
                   AgentResultMixin, QThread):
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

    # 类属性别名：探针与 UI 可按 `AgentWorker.FORCE_CONFIRM_RULES` 检索到同一份常量
    FORCE_CONFIRM_RULES = FORCE_CONFIRM_RULES

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

        return self._finalize_workflow(tg, output)

    def _finalize_workflow(self, tg, output):
        """v4.233 断点 E：按任务图节点终态如实上报，失败/跳过不得谎报「✅ 任务图完成」。

        `tg.run()` 只回节点**输出字典**，不含节点 status（failed/skipped 只活在
        `Task.status`）。所以必须从 `tg.task_list()` 查终态——有未成功节点就如实
        上报「⚠ 未完整达成」并标 `self._workflow_incomplete`（主账本 task_state 经
        `note_workflow_incomplete` 感知），全 completed 才报「✅ 任务图完成」。
        """
        _tasks = tg.task_list()
        _failed = [t["id"] for t in _tasks if t.get("status") == "failed"]
        _skipped = [t["id"] for t in _tasks if t.get("status") == "skipped"]
        if _failed or _skipped:
            _n = len(_failed) + len(_skipped)
            self._emit_status(
                "⚠ 任务图未完整达成：%d 个节点未成功（失败 %d / 跳过 %d）"
                % (_n, len(_failed), len(_skipped)))
            self._workflow_incomplete = {"failed": _failed, "skipped": _skipped}
            # 回写主账本（若有）—— fail-open：异常不影响主流程
            try:
                _ts = getattr(self, "_tstate", None)
                if _ts is not None and hasattr(_ts, "note_workflow_incomplete"):
                    _ts.note_workflow_incomplete(_failed, _skipped)
            except Exception:
                pass
            return output
        self._workflow_incomplete = None
        self._emit_status("✅ 任务图完成")
        return output

    # ---- 主循环 ----

    def __init__(self, mw, messages, tool_defs, mcp_clients=None, task_id=None,
                 resume=False, isolated=False, force_complex=False,
                 explicit_intent=True):
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
        # v4.169.0（审查 P0-3）：本轮用户消息是否表达了「执行意图」。
        # False（纯提问 / 讨论 / 评价句）时，权限引擎会拦住模型**自主发起**的
        # 非只读操作（写入 / 执行 / 外发），只读照旧放行。
        # 由 UI 启动 Agent 时按 intent_guard 判定后传入；默认 True（不改既有行为）。
        self.explicit_intent = bool(explicit_intent)
        # 本次确认是否"强制弹窗"（硬确认档用）；UI 侧据此跳过会话信任短路。
        self._confirm_force = False
        # v4.101：断点续传——每个 Agent 任务带唯一 task_id，停止时标记 paused、正常完成删除。
        self.task_id = task_id or task_resume.new_task_id()
        self.resume = bool(resume)
        self.stopped_by_user = False  # 结束时回填：是否因用户停止而终止（供 UI 决定是否显示「继续」）
        self._workflow_incomplete = None  # v4.233 断点 E：最近一次任务图未完整达成的节点集合（None=完整）
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
        # 审计修复 E1：GUI 线程（__init__ 所在线程）安全读取当前激活会话 sid，
        # run() 与并发工具池线程据此绑定默认上下文管理器，避免误用 _global。
        try:
            self._ctx_sid = (None if self._isolated
                             else getattr(mw.store.active(), "sid", None))
        except Exception:
            self._ctx_sid = None
        self._confirm_event = threading.Event()
        self._confirm_val = False
        self._stop_requested = False
        # v4.125 M-01：自动停止但"可续"（超时/预算熔断/步数耗尽）——这类停止
        # 检查点必须 mark_paused 保留（此前 mark_done 删档 + UI 只认
        # stopped_by_user → 提示"可点继续"却既无按钮也无存档）。
        self._resumable_stop = False
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

    def _maybe_confirm(self, title, detail, force=False):
        """危险操作：向主线程请求确认，子线程阻塞等待结果。

        v4.169.0（P0-3 配套）：`force=True` 时**跳过**"本次会话已全部信任"的短路。
        硬确认档（装/建技能、删自动化、删数据）必须真的弹一次 ——
        否则用户早先点过一回"全部信任"，这些不可逆操作就再也不会被问，
        decide() 里那道 always_confirm 等于白设。
        """
        engine = getattr(self.mw, "permission_engine", None)
        if not force:
            if engine is not None and engine.session_trusted:
                return True
            if getattr(self.mw, "session_trusted", False):
                return True
        self._confirm_force = bool(force)
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
        """从 tool_call 里稳妥解析 arguments JSON。

        P1（v4.186.0 审查 P1-1）：json.loads 对合法 JSON 数组/字符串/数字
        （如 arguments: "[1,2]"）会成功返回非 dict —— 原样返回会让下游
        (args or {}).get 崩 AttributeError 并逃出 run() 主循环。非 dict
        一律归一为空 dict（fail-closed：按缺参数走自然拒绝/确认路径）。
        """
        try:
            _parsed = json.loads(tc.get("function", {}).get("arguments", "{}") or "{}")
        except Exception:
            return {}
        return _parsed if isinstance(_parsed, dict) else {}

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
        "特别注意：Obsidian 是否可用以**刚才 sys_info 的真实返回**为准，绝不可声称"
        "『Obsidian 语义检索』等 sys_info 未确认的能力；Webhook 未开启也不可声称可用。"
        "若刚才调用了 web_search，请基于真实返回结果说明搜索功能是否正常（有无结果、是否报错），"
        "不要编造。无论何种情况，都【禁止生成任何 PPT、Word 文档、演示文稿、视频或图片等"
        "交付物】——用户只是想了解/验证工具状态，用简洁文字报告即可。"
        "不要编造任何不存在的问题或路径。"
    )
    # v4.189 批②：对账/核对意图纪律——防「没读文件凭记忆编引用」。
    # 背景（2026-09-30 小臭 CHANGELOG 编造事件）：对账请求下模型把记忆里的真素材
    # （其他版本的功能描述+日志日期+HTTP 4xx 错误码）拼装成「文件里的条目」
    # 煞有介事对账，两次拆穿才承认编造。纪律四条：先真读、先列结构清单、
    # 引用带行号原文、没有就说没有。与 _SELF_CHECK_INSTRUCTION（自检场景）互补，
    # 本条覆盖「核对/对账用户提供的文件」场景。
    _AUDIT_REF_INSTRUCTION = (
        "用户要求核对/对账文件内容。铁律："
        "①第一步必须先用 read_file 完整读取目标文件（找不到路径就先用 list_dir 定位，"
        "禁止凭记忆回答文件内容）；"
        "②回复开头先原样列出该文件的真实结构清单（全部版本节/配置项/条目名，"
        "一个不许漏、不许加）；"
        "③之后任何『文件里写了X』的引用必须带【行号+原文摘录】，缺一作废；"
        "④文件里没有的条目就直接说没有，禁止从你的记忆补全。"
    )
    # v4.189 批④：附件必读纪律——用户消息里带 [文件: …]/[非图片文件: …] 标记时注入。
    # 背景（2026-09-30 CHANGELOG 编造事件实锤）：附件投递只递「信封」（一行文本标记），
    # 内容不进上下文；模型不调 read_file 就「读完 CHANGELOG，给您交叉核验」直接开编。
    # 图片附件走视觉通道（image_url 直接进上下文），不在此列。
    _ATTACH_READ_INSTRUCTION = (
        "用户本轮发来了附件（见消息中的 [非图片文件: …] / [文件: …] 标记）。铁律："
        "在基于附件内容回答、总结、核对、引用之前，必须先用 read_file 真实读取该文件"
        "（大文件按提示用 offset 分段读完）；在真正调用 read_file 之前，"
        "禁止声称『已读/读完/看过了』，禁止概括或引用其内容——"
        "标记只是一行文字，文件内容不在你的上下文里，凭记忆或猜测描述附件内容属于编造。"
    )
    # 附件标记正则：[非图片文件: x] / [文件: x] / [file: x]（发送侧最终形态，
    # 图片标记已被 _extract_images_from_text 吃掉换成 image_url）
    _ATTACH_MARK_RE_STR = (r"\[(?:非图片文件|文件|file):\s*"
                           r"([^\]\n]{1,200}?\.(?:[A-Za-z0-9]{1,8}))\]")





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





    # v4.66：工具结果失败标志——用于「连续工具失败」死循环护栏。
    # 仅收录强错误特征，避免普通文本里出现「失败」二字被误判。
    _FAIL_MARKERS = (
        "文件不存在", "命令执行失败", "读取失败", "写入失败", "拒绝：",
        "命令执行超时", "Out-File", "FileOpenFailure", "NotSupportedException",
        "不是内部或外部命令", "未能找到路径", "找不到路径", "无法找到路径",
    )

    # v4.216.0：_REF_KW/_META_VERBS/_CLAUSE_SEP 的 intent_guard 运行时归一块
    # 已随词表迁至 agent_text.py（留在本类体会因裸名 NameError 被静默吞掉）。














    # ---------- v4.102 fix12：token 预算熔断 ----------
    def _collect_step_tools(self, resp):
        """累计本步调用过的工具名（去重），供任务轨迹写回使用。"""
        try:
            _tcs = (resp or {}).get("tool_calls") or []
            # v4.195 批⑪：记录本轮工具调用总数。
            # success 必须建立在「确实干了活」之上 —— 一轮下来一次工具都没调，
            # 只回了几句文字，哪怕没有任何报错也不能算「跑通」。
            if _tcs:
                self._note_exit("tool_calls", len(_tcs))
            for _tc in _tcs:
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
            # v4.195 批⑪ 结局推断：九态。
            #
            # 旧逻辑是四种之外一律 success —— 工具连续失败、模型原地复读、
            # 伪造工具调用、API 异常这四类全被记成成功；而 trace_log 又只召回
            # success 轨迹去当 few-shot 正面示范，于是错路径被反复教回去。
            # 现改为「目标完成 + 工具成功 + 校验通过三者齐备才算 success」。
            outcome = self._infer_outcome(
                steps=steps, duration_s=duration_s,
                max_steps=getattr(self, "_max_steps", MAX_AGENT_STEPS))
            # 每个非 success 结局都要留一句「下次怎么避开」，
            # 否则轨迹只记了失败、没沉淀教训，下次照样踩同一个坑。
            pitfall = self._PITFALL_HINT.get(
                outcome, "本轮以「%s」结束，复盘后再动手会更省。" % outcome)
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
        # 审计修复 E1：本 worker 线程绑定当前会话的上下文管理器（线程局部，
        # 不污染其他 worker/GUI 线程；tools.py 的 context 工具无 sid 参数走此默认值）
        context_manager.set_default_sid(self._ctx_sid)
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
        # v4.195 批⑪ 结局诚实层：退出原因旗标。
        #
        # 这些信号**本来就有**（上面每个护栏一直在计数），但 _persist_task_memory
        # 推断 outcome 时一个都没读，只认 token/stop/timeout/max_steps 四种，
        # 其余一律兜底 success —— 于是「工具连续失败」「模型原地复读」
        # 「伪造工具调用」「API 调用异常」全被记成了成功。
        #
        # 而它们还会被**放大**：trace_log 只召回 outcome=="success" 的轨迹去当
        # few-shot 正面示范（标题写死「历史成功轨迹参考（同类任务已跑通）」），
        # 于是错路径被当成范例教回模型 → 下次更容易重犯。
        # 这是全项目唯一一条「越用越差」的回路，必须从这里掐断。
        self._exit_flags = {
            "api_error": 0,             # API 调用异常次数
            "tool_fail": 0,             # 工具返回失败的次数
            "repeat_converged": False,  # 复读 / 原地打转被强制收敛
            "question_stopped": False,  # 连续追问被早停
            "fake_tool_stopped": False, # 文字伪造工具调用达上限而停
            "tool_calls": 0,            # 本轮工具调用总数
        }
        # v4.225（P3 统一意图）：先把用户意图归一成一个 Intent 对象存下来，
        # 下面三处分散判据（_needs_action / step1 强制路由 / 状态栏）都读它。
        # 注意 _needs_action 的**结论仍原样来自** _detect_action_intent ——
        # Intent 只是壳，换壳不改行为（改判据 = 重排行为 = 事故）。
        self._intent = intent.classify(self._last_user_text(), None)
        # v4.225（P2 任务状态机）：建账本（接线在 agent_task_mixin）
        self._tstate_init(task_state)
        # v4.226（四阶段循环）：建阶段状态 + 门槛满足时注入任务清单
        # （接线在 agent_loop_mixin；判定在 agent_loop）
        self._loop_start(agent_loop)
        self._needs_action = agent_text._detect_action_intent(self.messages)
        if not self._needs_action and self._intent.needs_action:
            # classify 走的是单条 user 消息口径，与 messages 口径可能不同；
            # 取【或】而不是覆盖 —— 只放宽不收紧，且仅在两口径不一致时生效。
            self._needs_action = True
        # v4.102 fix12：token 预算熔断状态。budget=0 即完全禁用（行为回退到 fix12 前）。
        (self._token_budget, self._token_warn_ratio,
         self._token_budget_ds) = config.get_agent_token_budget(getattr(mw, "cfg", None))
        self._used_tokens = 0        # 本轮累计 token
        self._used_tokens_ds = 0     # 付费（DeepSeek）通道单独累计
        self._token_warned = False   # 80% 告警只提示一次
        self._token_budget_hit = False
        self._tools_used = []        # 本轮实际调用过的工具名（写轨迹用）
        self._task_risk = None       # v4.196 批⑬：任务级风险（延迟按目标计算，见 _task_risk_ctx）
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
            # v4.222：断点幂等——从 checkpoint 恢复执行 ledger，构建不可幂等工具
            # 的「已执行」哈希集，供 _exec_tool_calls 跳过重复执行（防副作用翻倍）。
            try:
                _cp = task_resume.load_checkpoint(mw.cfg, self.task_id)
                self._exec_ledger = list(_cp.get("exec_ledger", [])) if _cp else []
            except Exception:
                self._exec_ledger = []
            self._resume_done_hashes = {
                e.get("args_hash") for e in (self._exec_ledger or [])
                if e.get("side_effect_status") == "done"
                and e.get("name") in _NON_IDEMPOTENT_TOOLS
            }
            _goal = self._goal_hint() or "（未知原始目标）"
            if len(_goal) > 400:
                _goal = _goal[:400] + "…"
            self.messages.append({
                "role": "system",
                "content": (
                    # v4.125 N-01：措辞对齐事实——历史消息由 _build_api_history
                    # 保真压缩而来，工具调用与结果确实可见（此前清洗丢光、
                    # 提示却声称完整，模型只能编造进度）。
                    f"【继续上次任务】你之前被暂停，历史消息中包含已完成的"
                    f"工作记录（含工具调用与结果）。请先通读历史确认做到哪一步，"
                    f"再从断点接着干原始目标（{_goal}）；已调用过的工具不要重复调用，"
                    f"禁止再说「我来/我开始」之类的空话，必须调用真实工具推进。"
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

        # v4.189 批②：对账/核对意图 → 注入「先真读+列清单+行号引用」纪律。
        # 只在本轮 run 开始时一次性注入（_internal 不回写 session，续跑重建
        # 后消失，天然无重复累积；此处查重仅为同一 worker 极端重入兜底）。
        try:
            if (agent_text._audit_ref_needed(_cur_user, _prev_user)
                    and not any(m.get("_internal")
                                and "核对/对账文件内容" in str(m.get("content", ""))
                                for m in self.messages)):
                self.messages.append({
                    "role": "user", "content": self._AUDIT_REF_INSTRUCTION,
                    "_internal": True,
                })
        except Exception:
            pass  # 纪律注入失败不阻断 Agent 主流程

        # v4.189 批④：本轮 user 消息带附件标记 → 注入「附件必读」纪律。
        # 覆盖通用场景（总结/看看/核对不限）：内容不在上下文，没 read_file
        # 之前一律不许声称已读。与批②独立触发（对账场景两条都注入，互补不冲突）。
        try:
            _attach_marks = []
            for _t in (_cur_user, _prev_user):
                _attach_marks.extend(
                    m.group(1) for m in
                    re.finditer(self._ATTACH_MARK_RE_STR, _t or ""))
            if (_attach_marks
                    and not any(m.get("_internal")
                                and "用户本轮发来了附件" in str(m.get("content", ""))
                                for m in self.messages)):
                self.messages.append({
                    "role": "user", "content": self._ATTACH_READ_INSTRUCTION,
                    "_internal": True,
                })
        except Exception:
            pass  # 纪律注入失败不阻断 Agent 主流程

        for step in range(1, self._max_steps + 1):
            self._tstate_step(step)  # v4.225：账本同步步号
            self._loop_step(step)    # v4.226：四阶段循环同步步号
            # v4.60：超时保护——超过 3 分钟自动停止，防止慢模型无响应
            elapsed = time.time() - _t0
            if elapsed > MAX_DURATION:
                self._emit_status(f"⏰ Agent 超时（{int(elapsed)}s），已自动停止。建议切回 DeepSeek 获得更快响应。")
                self.stream_commit.emit(f"\n\n⏰ 执行超时（{int(elapsed)}秒），已自动停止。Agnes 免费模型速度较慢，建议切回 DeepSeek。")
                self._resumable_stop = True  # v4.125 M-01：超时可续
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
                # v4.225：改读统一 Intent（force_tool 结论仍由 _route_force_tool 产出，
                # 这里只多走一层壳 + 顺带刷新 self._intent 供状态栏/日志用）。
                self._intent = intent.classify(_cur_user, _prev_user)
                _ft = self._intent.force_tool
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
                # v4.184.0：把 API 真实报文（若存在）一并写进结构化日志，
                # 否则 log_query 只能看到「HTTP Error 400」、复现不了根因。
                _api_b = getattr(e, "_api_body", "") or ""
                # v4.162：把调用失败写入结构化日志库（agent_log.db），否则 log_query 查不到、
                # 出事后无法复盘（此前只走了 Python logging，不落 agent_log.db）。
                try:
                    from structured_logger import get_logger
                    get_logger().error(f"Agent 调用失败: {e}", module="agent",
                                       extra={"error": str(e),
                                              "api_body": _api_b[:400]})
                except Exception:
                    pass
                self._note_exit("api_error")  # v4.195 批⑪：勿再兜底成 success
                self.tool_log.emit({"name": "错误", "args": "", "result": str(e)})
                # v4.108 H-04：失败要让用户在气泡里看得见，不再静默结束装"完成"。
                # v4.175.0：把 API 真实报文一并显示到气泡（与结构化日志同源）。
                # v4.228.0：异常不再直接 `{e}` 渲染 —— 此前界面显示成
                #   「模型：<urllib.error.URLError:10061> 由于目标计算机积极拒绝…」
                # 用户既不知道是哪个模型、也不知道能不能重试。改成人话并点名本轮模型。
                try:
                    from ui_msg import humanize_net_error as _hne
                    _friendly = _hne(e)
                except Exception:
                    _friendly = str(e)
                _notice = (f"\n\n⚠️ 模型调用失败"
                          f"（{getattr(self, '_last_model', '') or '当前模型'}）：{_friendly}")
                if _api_b:
                    _notice += f"\n\n接口原文：{_api_b[:400]}"
                self.stream_commit.emit(_notice)
                break

            # v4.102 fix12：累计工具名 + token 预算熔断（超预算即停，保留阶段性结果）
            self._collect_step_tools(resp)
            if not self._tick_token_budget(resp):
                self._resumable_stop = True  # v4.125 M-01：熔断可续
                break

            # v4.62：步内超时检查（单次模型调用最长 90s，避免下一轮开头才拦导致大幅超额）
            if time.time() - _t0 > MAX_DURATION:
                self._emit_status(f"⏰ Agent 超时（{int(time.time() - _t0)}s），已自动停止。")
                self.stream_commit.emit(
                    f"\n\n⏰ 执行超时（{int(time.time() - _t0)}秒），已自动停止。")
                self._resumable_stop = True  # v4.125 M-01：超时可续
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
                self._note_exit("repeat_converged")  # v4.195 批⑪
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
            if content and agent_text._looks_like_question(content):
                self._question_steps += 1
            else:
                self._question_steps = 0
            if self._question_steps >= MAX_QUESTION_STEPS:
                self._note_exit("question_stopped")  # v4.195 批⑪
                if content:
                    self.stream_commit.emit(content)
                self.stream_commit.emit(
                    "\n\n⚠️ 已连续多次只追问、未推进任务，已自动停止。"
                    "请补充关键信息（如平台 / 数据 / 目标 / 竞品）后重新发送，"
                    "或直接切普通模式获取建议。")
                self._emit_status("⚠️ 追问过多，已自动停止")
                break
            asst = {"role": "assistant", "content": content}
            # v4.168.2（BUG 修）：**把思考过程带上**。DeepSeek 思考模式下，
            # 下一轮请求里这条 assistant 消息必须含 `reasoning_content`，
            # 否则接口直接 400「The `reasoning_content` in the thinking mode must
            # be passed back to the API.」—— 表现就是"第一轮能跑、第二轮必挂"。
            # （出站门 ui._ensure_reasoning_content 会兜底补空串，但真实思考内容
            #   对模型连续性更有用，所以这里原样带上。）
            asst["reasoning_content"] = resp.get("reasoning_content") or ""
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
                    self._note_exit("tool_fail")  # v4.195 批⑪
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
                            "role": "user", "content": _instr, "_internal": True,
                        })
                # v4.60：去重护栏拦截的工具不算"有效执行"，不清空 _idle_steps
                if self._guard_blocked:
                    self._idle_steps = max(self._idle_steps, 1)
                # v4.59 checkpoint：每步工具执行完增量同步，崩了可从断点恢复
                self._sync_to_session(mw)
                # v4.108 H-05/M-15：同步后落盘检查点快照 + 心跳（原只有 run 开头一次）
                self._sync_agent_checkpoint(mw)
                # v4.226（四阶段循环）：工具批次执行完 → 进 VERIFY 固化核验记录。
                # 只标注阶段+存记录，**不注入指令**（补做仍由 v4.225 收尾闸门独管）。
                self._loop_verify(agent_loop)
            else:
                # v4.98 撒谎检测器：模型用文字"演"工具调用（伪造 [工具]/✅ 已保存/
                # run_python( 等）却不真发 tool_call，这种内容不能当最终结果展示，
                # 必须强制它真正调工具。
                if content and agent_text._looks_like_fake_tool_call(content):
                    self._fake_steps += 1
                    log.warning("Agent 检测到文字伪造工具调用（第 %d 次），强制真正执行",
                                self._fake_steps)
                    self._emit_status(
                        f"⚠ 检测到伪造工具调用（第 {self._fake_steps}/{MAX_FAKE_RETRIES} 次），"
                        "正在强制真正执行…")
                    self._force_next = True
                    if self._fake_steps >= MAX_FAKE_RETRIES:
                        self._note_exit("fake_tool_stopped")  # v4.195 批⑪
                        self.stream_commit.emit(
                            "\n\n⚠️ Agent 多次用文字伪造工具调用（声称『已保存/已运行』"
                            "实际却未执行任何工具），已停止。这类任务建议切到 DeepSeek 模型——"
                            "免费模型函数调用能力弱，容易口头编造结果。请明确下达具体操作"
                            "（如：运行 Python 代码 XX、把内容写入文件 XX）。")
                        self._sync_to_session(mw)
                        break
                    self.messages.append({"role": "user",
                                              "content": self._AGENT_FAKE_TOOL_INSTR,
                                              "_internal": True})
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
                        self.messages.append({"role": "user", "content": self._AGENT_NUDGE,
                                              "_internal": True})
                    continue
                # v4.186：承诺兜底——接上此前是死代码的 _looks_like_promise。
                # 即使意图判定为「无需执行」（_needs_action=False，典型是自检/诊断类请求
                # 动词漏词关掉了上面两个门），只要模型只回「我来/继续检查…」这类承诺却
                # 不调工具，也兜底强推一次。_nudged 一轮一次闸 + _nudge_count 上限防死循环。
                if (content and not self._nudged and not self._any_tool_executed
                        and self._nudge_count < MAX_FORCE_RETRIES
                        and agent_text._looks_like_promise(content)):
                    self._nudged = True
                    self._nudge_count += 1
                    self._force_next = True
                    self._emit_status("⚠ 检测到空头承诺未动手，强制真正执行…")
                    _tracer.trace(step, "nudge", reason="承诺未行动(兜底)", idle_steps=self._idle_steps)
                    self.messages.append({"role": "user", "content": self._AGENT_NUDGE,
                                          "_internal": True})
                    continue
                if self._nudge_count >= MAX_FORCE_RETRIES:
                    self.stream_commit.emit("⚠️ 已多次尝试但 Agent 始终未调用工具。请明确指示具体操作（如：搜索XX、读取文件XX、运行Python代码XX）。")
                # ── v4.225（P2 任务状态机）：收尾闸门 ──
                # 放在所有 nudge 分支**之后**、真正 break 之前：只有老逻辑
                # 都不打算再给一次机会时，这里才做最后一道「你确定做完了？」
                # 核查。四道 gate 全过才补一轮（接线在 agent_task_mixin，
                # 判定在 task_state.should_nudge）；任一不满足 → 旧路径 break。
                if self._tstate_nudge_now(task_state, step, self._max_steps):
                    self._tstate_trace_nudge(step, _tracer)
                    continue
                # ── v4.226（四阶段循环）：SUMMARIZE 收尾 ──
                # 位置在 tstate 闸门**之后**、break 之前：闸门放行是「再给一轮」，
                # 那时还没收尾，发小结就成了半截结论里的注解。真正走到这里才追加。
                # 小结内容全部来自账本事实（工具真调过没、产物落没落盘），
                # 与模型自述冲突时以小结为准。
                self._loop_summary(agent_loop)
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
                self._tstate_resume_reset_nudge()  # v4.225：续跑轮可再补一轮
                self._force_retries = 0
                resume_steps = range(1, self._max_resume_steps + 1)
                for rstep in resume_steps:
                    self._tstate_resume_step(self._max_steps, rstep)  # v4.225：账本跨轮延续
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
                        self._note_exit("api_error")          # v4.221：续跑失败也要记失败，否则断点被误删
                        self._resumable_stop = True           # 保留断点，可继续/可诊断
                        break
                    # v4.102 fix12：续跑是烧 token 重灾区，同样累加工具名并做预算熔断
                    self._collect_step_tools(resp)
                    if not self._tick_token_budget(resp):
                        break
                    content = resp.get("content") or ""
                    asst = {"role": "assistant", "content": content}
                    # v4.168.2：续跑路径同样带上思考过程（否则第二轮 400，同上）
                    asst["reasoning_content"] = resp.get("reasoning_content") or ""
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
                                self.messages.append({"role": "user", "content": self._AGENT_NUDGE,
                                              "_internal": True})
                            continue
                        # v4.186：续跑镜像——承诺兜底（同主分支）。接上 _looks_like_promise，
                        # 即使 needs_action=False 也把「承诺却没行动」兜底强推一次。
                        if (content and not self._nudged and not self._any_tool_executed
                                and self._nudge_count < MAX_FORCE_RETRIES
                                and agent_text._looks_like_promise(content)):
                            self._nudged = True
                            self._nudge_count += 1
                            self._force_next = True
                            self._emit_status("⚠ 续跑中检测到空头承诺未动手，强制真正执行…")
                            self.messages.append({"role": "user", "content": self._AGENT_NUDGE,
                                                  "_internal": True})
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
                self._resumable_stop = True  # v4.125 M-01：步数耗尽可续
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
                # v4.125 M-01：用户停止 **或** 可续的自动停止（超时/熔断/步数耗尽）
                # 都保留检查点——此前自动停止走 mark_done 删档，提示"可点继续"
                # 却既无按钮也无存档，任务卡死无法续。正常完成才删档。
                if self._stop_requested or getattr(self, "_resumable_stop", False):
                    task_resume.mark_paused(mw.cfg, self.task_id)
                else:
                    task_resume.mark_done(mw.cfg, self.task_id)
            except Exception as e:
                log.warning("任务完成标记失败 task_id=%s: %s", self.task_id, e)
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
            if not isinstance(args, dict):
                # P1（v4.186.0 审查 P1-1）：合法 JSON 非对象（"[1,2]"）解析成功
                # 但类型是 list/str，原样直通 decide() 会 AttributeError 逃出
                # run()（done 不发射、任务静默断）。归一为空 dict 走 fail-closed。
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
            _tr = None
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
            # v4.169.0：把"本轮是否有执行意图"传下去 —— 用户只是提问/讨论时，
            # 模型自主发起的非只读操作要确认（P0-3 来源闸）。
            dec = engine.decide(name, args,
                                explicit_intent=getattr(self, "explicit_intent", True),
                                task_risk=(getattr(self, "_task_risk_ctx", None) or (lambda: None))())
            # v4.169.0（审查 P1-5）：把这次工具决策写进旁路日志 ——
            # 事后能对上「模型想调什么 → 权限怎么判 → 实际调没调」。
            try:
                import route_log as _rl
                from permissions import args_fingerprint as _af
                _rl.log_tool_decision(
                    name=name, args_digest=_af(args)[:12],
                    decision=("deny" if not dec.allowed
                              else ("confirm" if dec.needs_user else "allow")),
                    rule=dec.rule, allowed=dec.allowed, need_confirm=dec.needs_user,
                    source=("explicit" if getattr(self, "explicit_intent", True)
                            else "implicit"))
            except Exception:
                pass
            if not dec.allowed:
                # 被引擎阻止（仅讨论/规划模式、路径越界等）
                result_str = dec.reason
                deliverables = []
                schedule = None
            elif dec.needs_user:
                # 危险/外部操作 → 弹确认框
                # v4.169.0：硬确认档（装/建技能、删数据）要 force —— 它必须真的问一次，
                # 不能被"本次会话已全部信任"短路掉。
                title, detail = self._build_confirm_detail(name, args)
                ok = self._maybe_confirm(title, detail,
                                         force=(dec.rule in FORCE_CONFIRM_RULES))
                if not ok:
                    result_str = "用户取消了该操作"
                    deliverables = []
                    schedule = None
                else:
                    # 用户确认：记一笔「该工具 + 该参数」的信任，避免同轮反复弹。
                    # v4.169.0（P0-5）：必须带上 args —— 只记工具名会让
                    # 确认过的安全参数给后续危险参数"背书"。
                    engine.trust_tool(name, args)
                    try:
                        _tr = tools.exec_tool(
                            mw.cfg, APP_DIR, name, args,
                            should_stop=lambda: self._stop_requested,
                            perm_ctx=dec)
                        result_str, deliverables, schedule = _tr
                    except Exception as _te:
                        result_str = f"工具执行崩溃：{_te}"
                        deliverables, schedule = [], None
                        _tr = None
            else:
                # 允许且无需确认（只读 / 半自主 / 自动模式 / 白名单 / 会话信任）
                try:
                    _tr = tools.exec_tool(
                        mw.cfg, APP_DIR, name, args,
                        should_stop=lambda: self._stop_requested,
                        perm_ctx=dec)
                    result_str, deliverables, schedule = _tr
                except Exception as _te:
                    result_str = f"工具执行崩溃：{_te}"
                    deliverables, schedule = [], None
                    _tr = None

            dt = int((time.time() - t0) * 1000)
            _okc = self._tool_outcome(
                _tr, name, result_str,
                self._tool_result_looks_failed)[0]
            # 发射工具完成信号
            self.tool_finished.emit({
                "name": name,
                "result_preview": str(result_str)[:200],
                "index": idx,
                # v4.125 P2：失败判定从「前50字含'失败'」收窄为「开头就是失败模式」——
                # 工具正文写"本次修复了失败原因"会被误标红叉（仅影响 UI 图标）。
                # v4.226: 改用 ToolResult.ok（不再看文案前缀）
                "success": _okc,
                "duration_ms": dt,
            })
            self._handle_tool_result(
                tc, name, fn, result_str, deliverables, schedule,
                tool_result=_tr)

    @staticmethod
    def _confirm_text(name, args):
        """按工具类型生成友好的确认文案（覆盖所有需确认工具）。"""
        if name == "write_file":
            return "确认写入文件", (
                f"路径：{args.get('path', '')}\n"
                f"内容长度：{len(args.get('content', ''))} 字符")
        if name == "run_python":
            code = args.get("code", "")
            if command_danger_level(code):
                return "⚠️ 高危操作：确认执行 Python 代码", (
                    "检测到高危操作（删除/卸载/账户权限/网络/注册表/下载即执行/杀进程等），"
                    "请确认代码来源可信后再执行：\n\n" + code[:500])
            return "确认执行 Python 代码", code[:500]
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
            cmd = args.get("command", "")
            if command_danger_level(cmd):
                return "⚠️ 高危操作：确认执行命令", (
                    "检测到高危操作（删除/卸载/Git不可逆/账户权限/服务网络/注册表写/"
                    "下载即执行/杀进程等），请确认命令来源可信后再执行：\n\n" + cmd)
            return "确认执行命令", cmd
        if (name.startswith("mouse_") or name.startswith("keyboard_")
                or name.startswith("app_")
                or name in ("window_focus", "process_kill", "process_start")):
            return "确认桌面/系统控制", (
                f"动作：{name}\n{json.dumps(args, ensure_ascii=False)[:300]}")
        return "确认执行操作", (
            f"工具：{name}\n{json.dumps(args, ensure_ascii=False)[:300]}")

    @staticmethod
    def _build_confirm_detail(name, args):
        """生成确认弹窗文案，并附上「影响范围」（若工具登记了预检，P1#5）。"""
        title, detail = AgentWorker._confirm_text(name, args)
        _scope = compute_impact_scope(name, args)
        if _scope:
            detail = detail + "\n\n影响范围：\n" + _scope
        return title, detail

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
                _tr = None
                if self._stop_requested:
                    self._emit_status("⏹ 已停止（用户请求）")
                    break
                # 发射工具开始信号
                self.tool_started.emit({
                    "name": name, "args": args, "index": idx, "total": total
                })
                t0 = time.time()
                # 审计修复 E1：线程池工作线程是全新线程，不继承 thread-local——
                # 在任务函数内先注入默认 sid，并发执行的 context 工具同样命中当前会话。
                _ctx_sid = self._ctx_sid
                # P1 #4（v4.219）：并发批次同样携带权限决策，杜绝绕过最终闸门。
                # v4.226（P1-1）：拿不到决策时**不再伪造一个 allowed=True 的兜底**，
                # 改为原样传 None —— 由 exec_tool 的 _permission_gate 按风险分类
                # fail-closed（READ 放行 / WRITE_LOCAL·EXEC·EXTERNAL 拒绝）。
                # decide() 抛异常同理：引擎自己出错了，绝不因此把权限放开。
                _engine = getattr(self.mw, "permission_engine", None)
                _dec = None
                if _engine is not None:
                    try:
                        _dec = _engine.decide(
                            name, args,
                            explicit_intent=getattr(self, "explicit_intent", True))
                    except Exception as _pe:
                        # 决策异常必须留痕：否则「权限闸失效」这件事只有代码知道。
                        log.warning("权限决策异常（并发批次 %s），按无授权处理: %s", name, _pe)
                        _dec = None
                def _exec_with_ctx(_name=name, _args=args, _sid=_ctx_sid,
                                  _stop=lambda: self._stop_requested, _dec_ctx=_dec):
                    context_manager.set_default_sid(_sid)
                    return tools.exec_tool(mw.cfg, APP_DIR, _name, _args,
                                           should_stop=_stop, perm_ctx=_dec_ctx)
                futures[pool.submit(_exec_with_ctx)] = (tc, t0, idx)

            for future in as_completed(futures):
                # v4.58：stop 后跳过剩余并发工具的结果渲染（工具已提交无法取消，但不渲染）
                if self._stop_requested:
                    continue
                tc, t0, idx = futures[future]
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    _tr = future.result()
                    result_str, deliverables, schedule = _tr
                except Exception as e:
                    result_str = f"工具执行异常：{e}"
                    deliverables = []
                    schedule = None
                    _tr = None

                dt = int((time.time() - t0) * 1000)
                _okc = self._tool_outcome(
                    _tr, name, result_str,
                    self._tool_result_looks_failed)[0]
                # 发射工具完成信号
                self.tool_finished.emit({
                    "name": name,
                    "result_preview": str(result_str)[:200],
                    "index": idx,
                    # v4.125 P2：失败判定从「前50字含'失败'」收窄为「开头就是失败模式」——
                # 工具正文写"本次修复了失败原因"会被误标红叉（仅影响 UI 图标）。
                # v4.226: 改用 ToolResult.ok（不再看文案前缀）
                "success": _okc,
                    "duration_ms": dt,
                })
                self._handle_tool_result(
                    tc, name, fn, result_str, deliverables, schedule,
                    tool_result=_tr)

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

    def _run_workflow_guarded(self, wf_type, task, mw, args):
        """带权限闸的 run_workflow（v4.169.0 审查 P0-4）。

        run_workflow 是「子代理并行」入口，**不走通用 exec_tool** ——
        它是直接调 `_run_workflow` 起任务图的。原实现因此整条绕过权限引擎：

          · discuss / plan 模式下也能启动（本该一个"不执行"、一个"只读"）；
          · 模型只要自主返回 run_workflow，就能拉起搜索 / 写作等后续动作；
          · 子节点是另起 AgentNode，继承不到主链"逐项确认"那套。

        修法：进任务图之前先过一次引擎（这一道用主链的 explicit_intent，
        所以"用户没表达执行意图而模型自主发起工作流"会被来源闸拦下）。

        至于子节点内部：工作流一旦被用户放行，其内部步骤属于**本次已授权动作的
        实施细节**，不再逐项弹确认（否则研究+写作三节点会弹一串，没人受得了）。
        子节点自身仍有 agent_node 里的工具白名单与 perm 检查兜底。
        """
        engine = getattr(mw, "permission_engine", None)
        # v4.226（P1-1）：缺引擎时**不再整条跳过闸门照样执行**。
        # 原实现是 `if engine is not None:` 包住全部判定，于是引擎为 None 时
        # 直接落到末尾的 `return self._run_workflow(...)` —— 一个字都没判就启动
        # 子代理任务图（内含搜索/写文件）。这里显式拒绝，与 decide() 异常同口径。
        if engine is None:
            log.warning("run_workflow 缺权限引擎，按无授权拒绝")
            return ("（子代理工作流未执行：权限引擎未启用，"
                    "无法完成授权判定，按保守策略拒绝）")
        try:
            dec = engine.decide(
                "run_workflow", args or {},
                explicit_intent=getattr(self, "explicit_intent", True),
                task_risk=(getattr(self, "_task_risk_ctx", None) or (lambda: None))())
        except Exception as e:
            return f"（子代理工作流未执行：权限判定异常 {e}）"
        if not dec.allowed:
            return f"（子代理工作流未执行：{dec.reason}）"
        if dec.needs_user:
            title, detail = self._build_confirm_detail("run_workflow", args or {})
            if not self._maybe_confirm(title, detail,
                                       force=(dec.rule in FORCE_CONFIRM_RULES)):
                return "（你取消了子代理工作流）"
            try:
                engine.trust_tool("run_workflow")
            except Exception:
                pass
        return self._run_workflow(wf_type, task)

    def _exec_tool_calls(self, tool_calls, mw, APP_DIR):
        """执行一批工具调用（串行/并发由权限引擎决策），供主循环与续跑共用。"""
        self._app_dir = APP_DIR  # v4.222：缓存，供 _handle_tool_result 绝对化交付物路径
        # v4.222：断点幂等——恢复模式下，已执行过的不可幂等操作不再重复触发
        if getattr(self, "_resume_done_hashes", None):
            _kept, _skipped = [], []
            for _tc in tool_calls:
                _fn = _tc.get("function", {})
                _nm = _fn.get("name", "")
                _as = _fn.get("arguments", "")
                if self._is_resume_dup(_nm, _as):
                    _skipped.append(_tc)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": _tc.get("id", ""),
                        "content": "（恢复检测：该不可幂等操作已在断点前执行过，已跳过重复执行以避免副作用翻倍）",
                    })
                else:
                    _kept.append(_tc)
            if _skipped and not _kept:
                self._guard_blocked = True
                return
            tool_calls = _kept
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
            _out = self._run_workflow_guarded(_wf_type, _task, mw, _args)
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
            self._safe_args(tc),
            explicit_intent=getattr(self, "explicit_intent", True),
            task_risk=(getattr(self, "_task_risk_ctx", None) or (lambda: None))()) for tc in tool_calls]
        # 任一被阻止 或 需用户确认 → 串行（逐条决策/确认）
        if any(not d.allowed for d in decs) or any(d.needs_user for d in decs):
            self._run_serial(tool_calls, mw, APP_DIR)
        else:
            # 全允许且无需确认 → 并发执行
            self._run_concurrent(tool_calls, mw, APP_DIR)

    def _task_risk_ctx(self):
        """v4.196 批⑬：本轮任务的**任务级**风险等级（给权限引擎的 fail-closed 闸）。

        与工具级风险（risk.classify）是两回事：同一个 write_file，
        「把小说草稿另存一份」和「填一份税务申报表」的可接受失败率完全不同。
        以前 decide 只看工具级，于是后者也能被会话信任一次放行。

        只在首次调用时算一次（目标不变，结果不变），并缓存。
        认不出任务等级 → normal，**绝不失手把日常任务判成 critical**（反之则可接受）。
        """
        try:
            if getattr(self, "_task_risk", None) is None:
                import risk as _rk
                _res = _rk.task_risk_level(self._goal_hint()[:500])
                self._task_risk = (_res[0], _res[1])
                if _res[0] != _rk.TASK_NORMAL:
                    log.info("任务风险等级=%s（%s）", _res[0], _res[1])
        except Exception as e:
            log.warning("任务风险分级失败（按普通任务处理）: %s", e)
            self._task_risk = ("normal", "")
        try:
            return (self._task_risk or ("normal", ""))[0]
        except Exception:
            return "normal"

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
                "exec_ledger": getattr(self, "_exec_ledger", []),
            })
        except Exception:
            pass

    # v4.195 批⑪：每种非成功结局对应的「下次怎么避开」。
    # 没有这句，轨迹就只是记录了一次失败，而不是沉淀了一条教训。
    _PITFALL_HINT = {
        "token_budget":
            ("任务 token 超预算被熔断；下次先拆分任务或限制工具轮次，"
             "避免大量升舱 DeepSeek 付费通道"),
        "max_steps":
            "步数耗尽仍未收敛，可能卡在工具循环；下次先明确产出物再开工",
        "stopped":
            "被用户中途停止，可能方向不符；下次先确认目标再执行",
        "aborted":
            "非任务原因被中断（环境/进程层面）；下次先确认运行环境再开工",
        "timeout":
            "单次超时（>180s）；下次拆成多个子任务分批执行",
        "model_error":
            ("API 调用异常，任务未真正执行完；下次先确认网络/额度/入参合法性，"
             "不要在没有工具结果的情况下继续给结论"),
        "tool_failed":
            ("工具连续失败导致中止；下次先确认路径/权限/命令是否合法，"
             "失败即如实报告，禁止凭记忆补全结果"),
        "hallucination_blocked":
            ("触发反幻觉护栏（伪造工具调用或原地复读）被停止；"
             "下次必须先拿到真实工具结果再下结论"),
        "validation_failed":
            "产出未通过校验；下次先看清产物要求再动手",
        "partial":
            ("未真正完成（缺工具执行或中途收敛）；"
             "下次先明确交付物是什么，做完再收尾"),
    }

    # v4.195 批⑨：工具结果的**失败标记**——用于把证据登记成 ok=False。
    # 失败证据不可作为任何结论的支撑（审查问题 8：工具失败禁止继续补全事实）。
    # 宁可判松（漏判为成功）也不判严（正常内容被误标失败会干扰模型），
    # 故只认工具**自己明确返回的**失败句式，不做语义猜测。
    _TOOL_FAIL_MARKS = (
        "[RESULT NOT FOUND]",
        "文件不存在：",
        "抓取失败",
        "请求失败",
        "读取失败",
        "写入失败",
        "timeout",
        "timed out",
        "Traceback (most recent call last)",
    )

    def _note_exit(self, key, n=1):
        """v4.195 批⑪：记录一条退出原因旗标。任何情况都不抛错。"""
        try:
            f = getattr(self, "_exit_flags", None)
            if not isinstance(f, dict):
                return
            if isinstance(f.get(key), bool):
                f[key] = True
            else:
                f[key] = int(f.get(key) or 0) + int(n)
        except Exception:
            pass



    # v4.222：断点幂等——执行 ledger 登记 + 恢复查重（实例方法）
    def _record_exec_ledger(self, name, args_sig, ok):
        """v4.222：登记一次工具执行，供断点恢复时幂等查询（防重复执行副作用）。"""
        if not hasattr(self, "_exec_ledger"):
            self._exec_ledger = []
        self._exec_ledger.append({
            "name": name,
            "args_hash": _tool_args_hash(name, args_sig),
            "side_effect_status": "done" if ok else "failed",
            "finished_at": time.time(),
        })

    def _is_resume_dup(self, name, args_sig):
        """v4.222：恢复时查询——该不可幂等工具是否已在前次执行过（同参数）。
        v4.232 断点 D：write_file 额外校验产物真实存在，避免「账本记 done 但文件被删」
        被误判为已完成、跳过导致下游拿到缺失产物（任务带着空洞继续跑）。
        """
        if name not in _NON_IDEMPOTENT_TOOLS:
            return False
        _done = getattr(self, "_resume_done_hashes", None)
        if not _done:
            return False
        if _tool_args_hash(name, args_sig) not in _done:
            return False
        # v4.232 断点 D：write_file 必须二次校验产物仍存在，缺失则视为未 dup、必须重做。
        if name == "write_file":
            _ap = _resolve_write_file_path(args_sig)
            if _ap is not None and not os.path.isfile(_ap):
                return False
        return True

    def _infer_outcome(self, steps=0, duration_s=0, max_steps=0):
        """v4.195 批⑪：**九态**结局推断 —— 取代原先「四种之外一律 success」。

        审查的问题 1 原文：「只有满足『目标完成 + 工具成功 + 校验通过』
        时才能写入 success」。这条原则本批照做——注意是**且**，三者缺一都不是成功。

        九态取值（trace_log.append_task_trajectory 的 docstring 早就预留了 error，
        其余是本次新增）：
            success          真成功：无异常 + 工具未失败 + 有实质执行
            partial          完成了一部分（产出不足 / 目标未达成）
            tool_failed      工具连续失败而停
            validation_failed 校验/闸门判定不通过
            hallucination_blocked 检测到伪造工具调用或复读而停
            aborted          不需要承担的终止（用户停止）
            timeout          超时
            model_error      API 调用异常
            token_budget     token 预算熔断
            max_steps        步数耗尽
            stopped          用户停止
        """
        f = getattr(self, "_exit_flags", None) or {}
        _api_err = int(f.get("api_error") or 0)
        _tool_fail = int(f.get("tool_fail") or 0)
        _repeat = bool(f.get("repeat_converged"))
        _question = bool(f.get("question_stopped"))
        _fake = bool(f.get("fake_tool_stopped"))
        _tool_calls = int(f.get("tool_calls") or 0)

        # ① 最高优先：不可归责于模型的硬中断（原本就有，顺序保持不变）
        if getattr(self, "_token_budget_hit", False):
            return "token_budget"
        if getattr(self, "_stop_requested", False):
            return "stopped"
        # ② API 异常：模型/通道侧出错，绝不能算完成
        if _api_err:
            return "model_error"
        # ③ 步数 / 时间耗尽
        if max_steps and steps and steps >= max_steps:
            return "max_steps"
        if duration_s and duration_s > 180:
            return "timeout"
        # ④ 【本批新增】真·非成功结局——这些都是原先被兜底成 success 的
        if _fake:
            return "hallucination_blocked"
        if _repeat:
            return "hallucination_blocked"
        if _tool_fail:
            return "tool_failed"
        if _question:
            return "partial"
        # ⑤ 严格 success：目标达成 + 工具成功 + 无异常，三者缺一不算
        #    —— `tool_calls == 0` 说明本轮没真正干活（可能只回复了文字），
        #      此时即便没有任何异常也不能算「跑通」，否则轨迹库会被灌满空成功。
        if _tool_calls == 0:
            return "partial"
        # v4.222：产物级验收 —— 「调过工具」不再等同「任务成功」。
        # 本轮若声明了交付物，必须真实落地（文件存在且非空）才算 success，
        # 否则降级为 partial，避免「模型说写完了但文件没生成」被记入成功轨迹。
        _dlv = getattr(self, "_deliverables", None) or []
        if _dlv:
            _unmet = [d for d in _dlv if not _deliverable_satisfied(d)]
            if _unmet:
                return "partial"
        return "success"

    @classmethod
    def _tool_result_looks_failed(cls, name, result_str):
        """本批⑨：判断工具调用是否实质失败。

        返回 True = 失败。三种信号：① 工具名本身就是「错误」；
        ② 返回体头部带明确的失败标记；③ 返回体是 Python 异常回溯。
        """
        try:
            if str(name or "").strip() in ("错误", "error", "Error"):
                return True
            s = str(result_str or "")
            if not s:
                return False
            head = s[:400]
            return any(m in head for m in cls._TOOL_FAIL_MARKS)
        except Exception:
            return False


    def _auto_remember(self, mw):
        """对话结束后自动提取值得长期记忆的信息，写入 MEMORY.md。
        
        仅在对话中实际执行过工具调用时才触发，避免纯闲聊污染记忆库。
        """
        # v4.107：隔离会话（导演台对话）不写长期记忆——导演闲聊（"这镜太暗了"）
        # 不该被提炼成用户画像事实污染全局记忆。
        if getattr(self, "_isolated", False):
            return
        # 快速判断：**本轮**对话是否有实质性操作（v4.169.0 审查：只看本轮）。
        #
        # 原来写的是 `any(msg.get("role") == "tool" for msg in self.messages)` ——
        # 扫的是**整个历史**（含 baseline 里带进来的旧工具消息）。
        # 于是只要历史上曾经调过工具，本轮**什么都没干**也会触发自动记忆提炼，
        # 属于"没有明确下令却产生持久副作用"：用户只是闲聊两句，
        # 记忆库里却多了一条推断出来的"事实"。
        #
        # "本轮"的界定直接复用既有的 _seq 机制：__init__ 给 baseline 打 _seq=0，
        # 运行内新生成的消息 _seq 单调递增（见 __init__ 注释），> 0 即本轮新增。
        has_tool_msg = any(
            isinstance(msg, dict) and msg.get("role") == "tool"
            and isinstance(msg.get("_seq"), int) and msg["_seq"] > 0
            for msg in self.messages
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

        # v4.196 批⑫：每条先过准入关（memory_gate），只有 admit 才落库，
        # pending 落待确认区，reject 当场驳回。
        results = []
        for item in facts:
            try:
                it = item if isinstance(item, dict) else {"content": str(item or "")}
                fact_txt = (it.get("content") or "").strip()
                if not fact_txt:
                    continue
                # 该主题已有的旧记忆（冲突判定的比对基线；缺失不算问题）
                old = ""
                try:
                    _topic = it.get("topic")
                    if _topic:
                        hits = memory_store.search_memory(str(_topic), limit=3)
                        old = "\n".join(str(h.get("text") or "") for h in (hits or []))
                except Exception:
                    old = ""
                try:
                    import memory_gate
                    verdict = memory_gate.admit(it, old_text=old)
                except Exception as e:
                    log.warning("记忆准入关异常（本条按待确认处理）: %s", e)
                    verdict = {"decision": "pending", "reason": f"准入关异常：{e}",
                               "fact": fact_txt, "topic": it.get("topic"),
                               "source": None, "confidence": None,
                               "expires_at": None, "evidence_id": None}
                verdict.setdefault("category", it.get("category"))
                verdict.setdefault("topic", it.get("topic"))
                results.append(verdict)
            except Exception as e:
                log.warning("自动记忆条目处理失败: %s", e)

        if not results:
            return

        count = 0
        for v in results:
            decision = v.get("decision")
            if decision == "admit":
                try:
                    result = memory_store.append_memory(
                        v.get("fact", ""), type=v.get("category"), topic=v.get("topic"),
                        source=v.get("source"), confidence=v.get("confidence"),
                        evidence_id=v.get("evidence_id"),
                        expires_at=v.get("expires_at"),
                        verified=bool(v.get("verified")))
                    if "已写入" in result or "已更新" in result:
                        count += 1
                except Exception as e:
                    log.warning("自动记忆写入失败: %s", e)
            else:
                # 拦下来的不能无声无息——落到待确认区，注明来源与原因，等人点头
                try:
                    memory_store.append_pending(v)
                except Exception as e:
                    log.warning("写入待确认区失败: %s", e)

        try:
            import memory_gate
            self._emit_status(memory_gate.summarize(results))
        except Exception:
            pass

    @staticmethod
    def _parse_remember_facts(raw):
        """从 LLM 回复中解析记忆条目列表，兼容对象/字符串/各种格式污染。

        v4.73：支持结构化对象 {"topic","category","content"}，topic 用于冲突合并。
        纯字符串条目 topic/category 记为 None（仅去重追加）。

        v4.196 批⑫：透传 source / ev / evidence / confidence / expires
        —— 这些是记忆准入关的判据字段，**丢一个整条就降级为推断**
        （工具来源没了证据编号，机器无从回验，只能按 inference 处理）。
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
                                # v4.196 批⑫：准入判据字段透传
                                "source": (f.get("source") or f.get("origin") or None),
                                "ev": (f.get("ev") or f.get("evidence")
                                       or f.get("evidence_id") or None),
                                "confidence": f.get("confidence"),
                                "expires": (f.get("expires") or f.get("expires_at") or None),
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
