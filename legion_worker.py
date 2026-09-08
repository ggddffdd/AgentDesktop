# -*- coding: utf-8 -*-
"""Agent 军团执行器 v4.123

把 legion.py 定义的项目（波次 + 角色成员）编译成 task_graph.TaskGraph 并执行：

- **宪法第二章（v4.123）**：重要节点的授权权**永远在用户手里**。
  项目经理📋 只有**建议权**：它出 PASS/FAIL 建议，弹窗交给用户批 —— 它不能替你批。
  三档模式（项目级 gate_mode）：
      off      无调度验收，一路跑完
      advisory 顾问模式：PM 出建议并按建议执行（老行为兼容，不是授权）
      human    **默认（新项目）**：每波暂停等你批准，超时=不授权=停止推进
  自动放行是可选开关（auto_pass_after=0 默认关闭），开启后仍要过三道围栏：
      ① 参数指纹（波次+成员+任务）对得上才算"同类"，变了重新批
      ② 自动放行次数上限（auto_pass_max，用尽即收回人手）
      ③ 每笔都进审计日志 legion_auth.jsonl，可一键收回信任（legion.revoke_trust）

- **共享任务板（v4.123）**：状态写在**项目**的任务板上（legion_board/<项目ID>.json），
  不挂在 PM 身上 —— 换成员、换 PM、重启程序都不丢，项目才连续。
  PM 通过 legion_board 工具读状态，知道谁干到哪了才能调度。

- **项目经理不进波次**：它是跨波调度台，被编进波次时自动剔除（日志会提示）。

- **wave 内并行**：同一波次成员无依赖，TaskGraph 依赖就绪即并行（内部 ThreadPoolExecutor，
  上限 min(len(ready), 5)，与既有 multi_search 同款）。
- **wave 间串行 + 验收闸门**：v4.122 起改为**逐波执行**（不再一次建全图），
  每波跑完由「项目经理」验收：
      PASS  → 放行下一波
      FAIL  → 带【打回指令】重跑本波（上限 gate_max_retry，v4.123 起默认 2 次）
              重跑仍 FAIL → 标红放行，不卡死流水线
  闸门由项目级 gate_enabled 控制（新项目默认开，老项目默认关，行为不变）。

- **开工计划节点**：开启闸门时，第 1 波前先让项目经理出一份《执行计划》
  （拆任务 / 排波次 / 每波验收标准），作为后续验收的基准。纯文本建议，不改执行图。

- **修复：并行波 context 互相覆盖**（v4.122）
  旧实现依赖 AgentNode 往 state["context"] 追加，而 TaskGraph.run 合并并行结果时
  `state["context"] = r["context"]` 后完成者覆盖先完成者 —— 同波其他成员产出会丢。
  现在由本执行器自己维护 self._ctx，逐波把**本波全部成员**产出合并进去。

- **项目经理的「眼睛」**：每波产出与过程日志实时写进 legion 运行时状态仓，
  PM 通过 legion_list_outputs / legion_get_output / legion_read_log 读取。
  （此前日志只往 UI 日志框 emit，外部完全读不到 —— PM 加进来也是瞎子。）

LLM 通道沿用 OrchestrateWorker 的既有模式：mw._agent_call（见 ui.py:579），
不另起一套网络栈，避免与主程序的模型/超时/重试策略分叉。

已知限制：TaskGraph.run 不支持中途取消（无取消钩子），MVP 阶段不提供停止按钮；
后续如需取消，可在 _wrap 里查 self._stop_requested 抛异常中断。
"""
import logging
import os
import threading
import glob
import json

from PySide6.QtCore import QThread, Signal

# 顶层导入（不放在 run() 内）：保证 PyInstaller 静态分析能扫到这三个模块，
# 否则打包后运行会 ModuleNotFoundError。三者之间无循环依赖，可安全顶层导入。
import legion
from task_graph import TaskGraph
from agent_node import AgentNode

log = logging.getLogger("legion")

# PM 输出的《执行计划》最多带多少字进后续验收指令（防止长计划挤爆上下文）
_PLAN_CTX_LIMIT = 800

# v4.124.4：手工补录数据——「PM 拿不到数据就明说，经验判断必须标注，禁止编造」
# 落地机制。扫描 LEGION_DIR/legion_runs/manual_*.json 自动注入到 PM 提示词。
# 兄长/大哥可手动录入真实竞品数据（亚马逊现查现录），PM 第 2 波就能带数据放行，
# 而不必再撞一次"工具拿不到"的墙。JSON 字段自由，PM 看到文件名 + 头 800 字。
_MANUAL_CTX_FILE_GLOB = "manual_*.json"
_MANUAL_CTX_PER_FILE_LIMIT = 800  # 单文件最多带进 context 的字符数（防超出）
_MANUAL_CTX_TOTAL_LIMIT = 2400    # 所有 manual 文件合计上限字符数


class LegionWorker(QThread):
    """按项目波次跑一个军团任务，结果以纯文本归并后经 done 信号抛出。"""

    log_line = Signal(str)              # 进度/日志文本（追加到日志框）
    node_status = Signal(str, str)      # (task_id, status) status ∈ running / done / error
    done = Signal(str)                  # 归并后的完整结果文本
    # 授权请求（宪法第二章）：(标题, 详情) -> 主线程弹框，结果经 set_auth_result 回传
    auth_request = Signal(str, str)

    def __init__(self, mw, project, task="", legion_data=None, parent=None,
                 resume_from=None, ckpt_run_id=None):
        super().__init__(parent)
        self.mw = mw
        self.project = project or {}
        self.task = task or ""
        # 军团全量数据（含角色库）：用于取用户在角色库里定制过的「项目经理」。
        # 不传则回落内置默认定义 —— 功能不残，只是用不到用户的定制。
        self.legion_data = legion_data
        self._outputs = []              # [(wave_idx, role_name, text)] 兼容旧字段
        # v4.124.5：断点续传
        # resume_from 是从 checkpoint 恢复出的「下一个要跑的 wave_idx」
        # （即 last_completed_wave + 1），传 None 表示从头跑
        self._resume_from = resume_from
        # ckpt_run_id 用于续跑时识别原 run_id，避免历史记录混淆
        self._ckpt_run_id = ckpt_run_id
        self._ctx = ""                  # 自己维护的累积上下文（见模块注释：修并行覆盖）
        self.run_id = None
        self.pid = (self.project or {}).get("id") or ""
        # 授权等待（人手闸门）
        self._auth_event = threading.Event()
        self._auth_val = None

    # ---- 授权：向主线程请求人手批准（PM 无放行权，只有建议权）----
    def _request_auth(self, title, detail, timeout=600):
        """阻塞等待用户授权。返回 "pass" / "reject" / "abort" / None（超时=不授权）。"""
        self._auth_val = None
        self._auth_event.clear()
        try:
            self.auth_request.emit(title, detail)
        except Exception:
            return None
        self._auth_event.wait(timeout=timeout)
        return self._auth_val

    def set_auth_result(self, val):
        """主线程弹框后回调（子线程 Event 解阻塞）。"""
        self._auth_val = val
        self._auth_event.set()

    # ---- 任务板：状态写项目共享板（换人换 PM 都不丢）----
    def _board(self, key, **fields):
        if not self.pid:
            return
        try:
            legion.board_update(self.pid, key,
                                project_name=self.project.get("name", ""), **fields)
        except Exception:
            pass

    # ---- 内部：日志（同时写 UI 与状态仓，供 PM 工具查询）----
    def _log(self, text):
        try:
            self.log_line.emit(text if text.endswith("\n") else text + "\n")
        except Exception:
            pass
        try:
            legion.record_log(self.run_id, text)
        except Exception:
            pass

    # ---- 内部：包装 executor 让 UI 能看到每个成员的起止（状态同步写任务板）----
    def _wrap(self, agent, tid, board_key=None, role_name="", wave_no=0):
        def _exec(state):
            self.node_status.emit(tid, "running")
            self._board(board_key, role=role_name, wave=wave_no, status="running")
            try:
                r = agent.run(state)
                self.node_status.emit(tid, "done")
                try:
                    out_txt = ""
                    if isinstance(r, dict):
                        out_txt = (r.get(tid + "_output") or "").strip()
                    self._board(board_key, role=role_name, wave=wave_no, status="done",
                                chars=len(out_txt),
                                summary=out_txt.replace("\n", " ")[:80])
                except Exception:
                    self._board(board_key, status="done")
                return r
            except Exception as e:
                self.node_status.emit(tid, "error")
                self.log_line.emit(f"  [{tid}] 成员执行异常：{e}\n")
                self._board(board_key, role=role_name, wave=wave_no, status="error",
                            summary=str(e)[:80])
                # 返回原 state 快照：让 TaskGraph 继续推进，单个成员失败不拖垮整个军团
                return dict(state)
        return _exec

    # ---- 内部：跑一波（波内并行）----
    def _run_wave(self, wi, members, state):
        """用 TaskGraph 并行跑一波，返回合并后的 state。

        派发前先看 self._aborted：用户已经终止，**绝不**发起任何成员调用。
        这是「成员零调用」的最后一道闸（外层 for 顶已挡过，这里是兜底）。
        """
        if getattr(self, "_aborted", False):
            return dict(state)
        tg = TaskGraph()
        wave_no = wi + 1
        for mi, role in enumerate(members):
            tid = f"w{wi}_m{mi}"
            role_name = f"{role.get('emoji', '')}{role.get('name', '角色')}".strip()
            prompt = legion.build_role_prompt(role)
            agent = AgentNode(tid, prompt, tools=set(role.get("tools") or []), mw=self.mw)
            tg.create(tid, role_name,
                      self._wrap(agent, tid, board_key=tid,
                                 role_name=role_name, wave_no=wave_no),
                      role.get("mission", ""))
        return tg.run(state)

    # ---- 内部：跑一次项目经理节点（计划 / 验收）----
    def _run_pm(self, pm_role, tid, instruction, ctx=""):
        """跑一个项目经理节点，返回其输出文本。失败返回空串（不拖垮军团）。

        派发前先看 self._aborted：用户已终止就不再烧 PM 的 token（v4.124.3）。
        之前只在外层 for 顶检查 → PM 自己的规划/验收调用没有任何闸门，
        一旦 PM 调用耗时长（例如长上下文、模型慢）就会"成员停了 PM 空转"。
        """
        if getattr(self, "_aborted", False):
            return ""
        try:
            prompt = legion.build_role_prompt(pm_role)
            agent = AgentNode(tid, prompt,
                              tools=set(pm_role.get("tools") or []), mw=self.mw)
            st = {"task": instruction, "query": instruction}
            if ctx:
                st["context"] = ctx
            st = self._wrap(agent, tid)(st)
            return (st.get(tid + "_output") or "").strip()
        except Exception as e:
            self._log(f"  ⚠️ 项目经理节点执行异常：{e}")
            return ""

    @staticmethod
    def _role_name(role):
        return f"{role.get('emoji', '')}{role.get('name', '角色')}".strip()

    # ---- v4.124.4：手工补录数据自动注入 ----
    @staticmethod
    def _load_manual_context(glob_pattern="manual_*.json"):
        """扫 `legion_runs/<pattern>` 把每份 JSON 的头 _MANUAL_CTX_PER_FILE_LIMIT 字拼起，
        注入到 PM 提示词（开工计划 + 验收指令），让 PM 第一秒就知道有手工补录数据可读。

        文件名约束 `manual_*.json` —— 用户习惯性命名（"manual_competitor_research_*.json"）。
        返回 (text, file_list)：
          text 是提示词嵌入用的字符串（可能为空）
          file_list 是补录文件名清单（用于 UI 日志）
        """
        runs_dir = os.path.join(legion.LEGION_DIR, "legion_runs")
        if not os.path.isdir(runs_dir):
            return "", []
        try:
            files = sorted(glob.glob(os.path.join(runs_dir, glob_pattern)))
        except Exception:
            return "", []
        if not files:
            return "", []

        chunks = []
        total = 0
        file_list = []
        for fp in files:
            if total >= _MANUAL_CTX_TOTAL_LIMIT:
                break
            name = os.path.basename(fp)
            file_list.append(name)
            # 单文件最多读 64k，避免超长文件撑爆 context（截断按 _MANUAL_CTX_PER_FILE_LIMIT）
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    raw = f.read(65536)
            except Exception as e:
                chunks.append(f"### {name}（读取失败：{e}）")
                continue
            # 优先用 JSON pretty-print：解析失败的整文退回原文截断
            try:
                obj = json.loads(raw)
                pretty = json.dumps(obj, ensure_ascii=False, indent=2)
                if len(pretty) > _MANUAL_CTX_PER_FILE_LIMIT:
                    pretty = pretty[:_MANUAL_CTX_PER_FILE_LIMIT] + "\n…(截断)"
            except Exception:
                pretty = (raw[:_MANUAL_CTX_PER_FILE_LIMIT]
                          + "\n…(非 JSON 原文截断)")
            chunk = f"### {name}\n{pretty}"
            chunks.append(chunk)
            total += len(chunk) + 2

        if not chunks:
            return "", []
        head = (
            "【手工补录数据 · 已读入，PM 可直接引用】\n"
            f"来源：{os.path.join(legion.LEGION_DIR, 'legion_runs')}\n"
            f"共 {len(file_list)} 份（按文件名时间序）：{' / '.join(file_list)}\n"
            "规则：① 视为真实数据可直接引用 ② 引用时标注『基于人工补录（{filename}）』\n"
            "③ 若引用某条数据后结论强依赖它，请在判定/写作时声明依据。\n"
            "以下是文件内容（已截断，需读全文请用 read_file 工具）：\n\n"
        )
        text = head + "\n\n".join(chunks)
        if len(text) > _MANUAL_CTX_TOTAL_LIMIT:
            text = text[:_MANUAL_CTX_TOTAL_LIMIT] + "\n…(总长截断)"
        return text, file_list

    def run(self):
        proj = self.project
        pname = f"{proj.get('emoji', '')}{proj.get('name', '军团')}".strip()
        waves = legion.wave_members(proj)

        if not waves:
            self.log_line.emit("该项目还没有配置团队成员 —— 先点「+ 添加团队」把人加进来。\n")
            self.done.emit("")
            return

        task = self.task
        # v4.124.5：续跑复用 ckpt 里的 run_id —— 审计账本不出现"复活节岛"，
        # 同一项目所有授权 / 产出 / checkpoint 串在同一条 run_id 链上。
        if self._resume_from is not None:
            _ckpt = legion.load_checkpoint(self.pid)
            if _ckpt and _ckpt.get("run_id"):
                self.run_id = _ckpt["run_id"]
            else:
                self.run_id = legion.start_run(pname, task)
        else:
            self.run_id = legion.start_run(pname, task)

        # ---- 闸门准备（v4.123：授权权归用户，PM 只有建议权）----
        gate_mode = (proj.get("gate_mode") or "").strip()
        if not gate_mode:
            # 老数据兼容：没有 mode 就按旧的 gate_enabled 推导，行为不变
            gate_mode = "advisory" if proj.get("gate_enabled") else "off"
        gate_enabled = gate_mode != "off"

        max_retry = 0
        try:
            max_retry = max(0, int(proj.get("gate_max_retry") or 0))
        except (TypeError, ValueError):
            max_retry = 0
        try:
            auto_after = max(0, int(proj.get("auto_pass_after") or 0))
        except (TypeError, ValueError):
            auto_after = 0
        try:
            auto_max = max(0, int(proj.get("auto_pass_max") or 0))
        except (TypeError, ValueError):
            auto_max = 3

        pm = legion.pm_role(self.legion_data) if gate_enabled else None
        if gate_enabled and not pm:
            self._log("⚠️ 已开启波次验收，但角色库里找不到「项目经理」—— 本次跳过验收，按原流程跑完。")
            gate_enabled = False
            gate_mode = "off"

        # 任务板：本次执行开始
        self._board("run", status="running", run_id=self.run_id, task=task[:80])

        # ---- v4.124.5：断点续传装载 ----
        # 如果 __init__ 传了 resume_from → 跳过计划调用 + 直接装入 parts_by_wave
        # _ckpt 已经在 run() 头部加载过一次（用于复用 run_id），这里复用不重读盘
        ckpt_data = _ckpt if (self._resume_from is not None and _ckpt) else None
        if ckpt_data:
            self._log(f"⏵ 续跑：跳过前 {self._resume_from} 波（从第 {self._resume_from + 1} 波启动）")
            # 阵容比对（成员签名逐项）—— 仅警告不阻断
            cur_fp = legion._waves_fingerprint(waves)
            if cur_fp != ckpt_data.get("waves_fingerprint"):
                self._log("⚠️ 阵容与存档不一致（成员 / 工具 / 模型 有改动）—— 仍按现阵容续跑")
            # 任务文本变更 —— 仅警告不阻断
            if (ckpt_data.get("task") or "").strip() != task.strip():
                self._log("⚠️ 任务文本与存档不一致 —— 仍按现任务续跑")
            self._log("")

        # 宪法第二章预检：项目经理是全局调度台，不进任何波次
        pm_in_wave = 0
        for w in (proj.get("waves") or []):
            pm_in_wave += sum(
                1 for m in (w.get("members") or [])
                if isinstance(m, dict) and legion.is_pm_role(m))
        if pm_in_wave:
            self._log(f"ℹ️ 检测到 {pm_in_wave} 处把「项目经理」编进了波次 —— 已自动剔除："
                      f"它跨波调度，不占执行位，也不当自己那一波的裁判。")

        total = sum(len(w) for w in waves)
        self._log(f"▶ 军团启动：{pname} · {len(waves)} 个波次 · {total} 位成员")
        self._log(f"任务：{task}")
        for wi, members in enumerate(waves):
            names = "、".join(self._role_name(r) for r in members)
            self._log(f"  第 {wi + 1} 波（并行）：{names}")
        mode_txt = {
            "off": "关闭（无调度验收，一路跑完）",
            "advisory": f"顾问模式 —— 项目经理出建议并按建议执行（重跑上限 {max_retry} 次）",
            "human": "人手把关 —— 每波暂停等你批准，项目经理**无放行权**"
                     + (f"（同类批准满 {auto_after} 次后可围栏内自动放行，上限 {auto_max} 次）"
                        if auto_after > 0 else "（未开启自动放行）"),
        }.get(gate_mode, gate_mode)
        self._log(f"调度与验收：{mode_txt}")
        self._log("")

        # ---- 开工计划：PM 拆任务 / 排波次 / 定验收标准（纯建议，不改执行图）----
        plan_text = ""
        if gate_enabled and self._resume_from is None:
            # v4.124.4：注入手工补录数据 —— "工具拿不到就用人工" 的接地路径
            manual_ctx, manual_files = self._load_manual_context()
            if manual_files:
                self._log(f"📥 已读入手工补录：{' / '.join(manual_files)}（{len(manual_ctx)} 字带进 PM 提示）")
            plan_instr = (
                f"【任务】{task}\n\n"
                f"【军团编制】共 {len(waves)} 波：\n" +
                "\n".join(f"  第 {wi+1} 波：" + "、".join(self._role_name(r) for r in w)
                          for wi, w in enumerate(waves)) + "\n\n" +
                ("\n" + manual_ctx + "\n" if manual_ctx else "") +
                "【你要做的】以调度器身份出一份《执行计划》，包含：\n"
                "1. 任务拆解：这个目标要拆成哪几件事，谁做\n"
                "2. 波次编排：先跑哪波、哪波可并行、哪波必须等上波验收\n"
                "3. 每波验收标准：可判定的硬指标（有/没有、对/错），不要写「质量好」这种虚的\n"
                "注意：你只出计划，不执行。不需要调用工具读产出（还没开始跑）。"
            )
            self._log("📋 项目经理：正在编制执行计划…")
            plan_text = self._run_pm(pm, "pm_plan", plan_instr)
            if plan_text:
                self._log(f"📋 执行计划已出（{len(plan_text)} 字）\n")
            else:
                self._log("⚠️ 执行计划未产出（不影响执行，继续跑）\n")

        # ---- 逐波执行 ----
        state = {"task": task, "query": task}
        parts_by_wave = {}      # wave_idx -> 归并文本（重跑时整体替换，避免残留旧稿）
        gate_reports = []       # 每波的验收结论文本，附在最终结果末尾
        self._ctx = ""
        manual_ctx = ""          # v4.124.4：人工补录文本，可能为空
        manual_files = []        # 文件名清单（仅日志）
        # aborted 已升格为 self._aborted（v4.124.3）：在 try 内初始化，
        # 让 _run_pm / _run_wave 入口能查 → 派发前识别已终止
        user_rejects = 0        # 用户亲手打回次数（防无限重跑）

        # v4.124.5：续跑注入（skip 计划 → 直接装入 parts_by_wave / plan_text / gate_reports）
        if self._resume_from is not None and ckpt_data:
            parts_by_wave = dict(ckpt_data.get("parts_by_wave") or {})
            plan_text = ckpt_data.get("plan_text") or ""
            gate_reports = list(ckpt_data.get("gate_reports") or [])
            self._ctx = "\n\n".join(parts_by_wave[k] for k in sorted(parts_by_wave))
            self._log(f"📦 续跑装入：plan_text {len(plan_text)} 字 / "
                      f"{len(parts_by_wave)} 波产出 / {len(gate_reports)} 条验收记录\n")

        try:
            # 升格为实例属性（v4.124.3）：让 _run_pm / _run_wave 入口能查，
            # 在任何派发之前识别已终止 → PM 不再空转，token 不再烧
            self._aborted = False
            for wi, members in enumerate(waves):
                if self._aborted:
                    # 用户已在上一波点了「终止」——立即停，不再启动后续波次
                    break
                # v4.124.5：续跑跳过已完成波次（直接从 resume_from 起的波次执行）
                if self._resume_from is not None and wi < self._resume_from:
                    continue
                attempt = 0
                advice = ""
                while True:
                    # 重跑循环顶部也要查（防 reject 后再 abort，attempt+1 仍触发 _run_wave）
                    if self._aborted:
                        break
                    wave_no = wi + 1
                    if attempt:
                        self._log(f"\n── 第 {wave_no} 波 · 重跑第 {attempt} 次（按打回指令修正）")
                    else:
                        self._log(f"\n── 第 {wave_no} 波启动")

                    # 上下文：累积历史 + 打回指令（不让并行成员互相覆盖）
                    wave_state_in = dict(state)
                    wave_state_in["context"] = self._ctx
                    if advice:
                        wave_state_in["context"] = (
                            self._ctx + f"\n\n【项目经理打回指令 · 必须照做】\n{advice}")

                    wave_state = self._run_wave(wi, members, wave_state_in)

                    # 收集本波产出（从各自节点取，避免并行 context 覆盖丢稿）
                    texts = []
                    for mi, role in enumerate(members):
                        tid = f"w{wi}_m{mi}"
                        node_out = wave_state.get(tid, {}) or {}
                        txt = ""
                        if isinstance(node_out, dict):
                            txt = (node_out.get(f"{tid}_output") or "").strip()
                        role_name = self._role_name(role)
                        legion.record_output(self.run_id, wave_no, attempt, role_name, txt)
                        if txt:
                            texts.append((role_name, txt))
                            self._outputs.append((wi, role_name, txt))

                    if texts:
                        parts_by_wave[wi] = "\n\n".join(
                            f"## 第 {wave_no} 波 · {rn}\n\n{t}" for rn, t in texts)
                        # 累积上下文供下一波使用（本波全部成员，一个不漏）
                        self._ctx = (self._ctx + "\n\n" + "\n\n".join(
                            f"【{rn}】{t}" for rn, t in texts)).strip()
                    else:
                        self._log(f"  ⚠️ 第 {wave_no} 波没有任何产出")

                    # v4.124.5：每波完成时落 checkpoint → 崩了可续跑
                    legion.save_checkpoint(
                        self.pid, self.run_id, task, plan_text,
                        parts_by_wave, gate_reports, last_completed_wave=wi,
                        waves=waves)

                    if not gate_enabled:
                        break

                    # ---- 波次验收：PM 只出**建议**，放行与否由用户授权（宪法第二章）----
                    self._log(f"📋 项目经理：验收第 {wave_no} 波…")
                    gate_instr = self._build_gate_instruction(
                        task, wave_no, attempt, members, texts, plan_text,
                        manual_ctx=manual_ctx)
                    verdict_text = self._run_pm(pm, f"pm_gate_w{wave_no}_{attempt}", gate_instr)
                    v = legion.parse_verdict(verdict_text)
                    suggest = "PASS（建议放行）" if v["pass"] else "FAIL（建议打回）"
                    gate_reports.append(
                        f"## 第 {wave_no} 波验收（第 {attempt} 次）· {suggest}\n\n{verdict_text}")

                    if not v.get("parsed"):
                        self._log("  ⚠️ 未解析到标准判定行 —— 按『建议放行』处理（宁可漏检，不可卡死）")
                    self._log(f"  📋 项目经理建议：{'PASS ✅（放行）' if v['pass'] else 'FAIL ❌（打回）'}")

                    fp = legion.fingerprint(wave_no, members, task)
                    decision = None

                    if gate_mode == "human":
                        # 先过围栏：指纹对得上 + 人工批准够数 + 自动次数没超
                        allowed, reason = legion.auto_pass_allowed(
                            self.pid, fp, auto_after, auto_max)
                        if allowed:
                            decision = "pass" if v["pass"] else "reject"
                            legion.mark_auto_used(self.pid, fp)
                            legion.record_auth(
                                self.pid, pname, wave_no, fp, suggest,
                                "放行" if decision == "pass" else "打回",
                                by="auto", reason=reason,
                                run_id=self.run_id)
                            self._log(f"  🔓 围栏内自动放行（{reason}）—— 已记审计，可抽查")
                        else:
                            self._board(f"gate_w{wave_no}", wave=wave_no,
                                        status="await_approval", pm_verdict=suggest)
                            self._log("  ⏸ 等待你授权（弹窗）：放行 / 打回重做 / 终止")
                            decision = self._request_auth(
                                f"第 {wave_no} 波 · 等待你授权",
                                self._auth_detail(wave_no, members, texts, v,
                                                  verdict_text, attempt))
                            if decision is None:
                                # 超时 = 不授权（与 agent.py 危险操作确认同一原则）
                                legion.record_auth(self.pid, pname, wave_no, fp, suggest,
                                                   "未授权", by="timeout",
                                                   reason="等待超时，按不授权处理",
                                                   run_id=self.run_id)
                                self._board(f"gate_w{wave_no}", wave=wave_no, status="timeout")
                                self._log("  ⏱ 等待授权超时 —— 按**不授权**处理，停止推进（已产出保留）")
                                self._aborted = True
                                break
                            legion.record_auth(
                                self.pid, pname, wave_no, fp, suggest,
                                {"pass": "放行", "reject": "打回", "abort": "终止"}.get(decision, decision),
                                by="user", reason="用户亲手批准",
                                run_id=self.run_id)
                            if decision == "pass":
                                legion.grant_trust(self.pid, fp, wave_no)
                    else:
                        # 顾问模式：按 PM 建议执行（用户已在此项目里选择信任建议）
                        decision = "pass" if v["pass"] else "reject"
                        legion.record_auth(
                            self.pid, pname, wave_no, fp, suggest,
                            "放行" if decision == "pass" else "打回",
                            by="advisory",
                            reason="顾问模式：按 PM 建议执行（PM 无授权权，此处不是授权）",
                            run_id=self.run_id)
                        if decision == "pass":
                            legion.grant_trust(self.pid, fp, wave_no)

                    if decision == "pass":
                        self._board(f"gate_w{wave_no}", wave=wave_no,
                                    status="approved", pm_verdict=suggest)
                        self._log("  ✅ 已授权放行，进入下一波")
                        break
                    if decision == "abort":
                        self._board(f"gate_w{wave_no}", wave=wave_no,
                                    status="aborted", pm_verdict=suggest)
                        self._log("  ⛔ 你终止了本次军团执行")
                        self._aborted = True
                        break

                    # ---- decision == "reject"：打回重跑 ----
                    self._board(f"gate_w{wave_no}", wave=wave_no,
                                status="rejected", pm_verdict=suggest)
                    if gate_mode == "advisory" and attempt >= max_retry:
                        self._log(f"  ⛔ 已达重跑上限（{max_retry} 次）—— 标红放行，不卡流水线")
                        break
                    if user_rejects >= max(3, max_retry + 2):
                        self._log(
                            f"  ⛔ 已连续打回 {user_rejects} 次（硬上限）—— "
                            f"停下来等你决定，不再自动重跑")
                        self._aborted = True
                        break
                    user_rejects += 1
                    attempt += 1
                    advice = v.get("advice") or "（项目经理未给出具体指令，请按质量标准自行复查重做）"
                    self._log(f"  ↩︎ 打回重跑：{advice[:120]}")

                    # 重跑前清掉本波旧稿，避免残留
                    legion.record_output(self.run_id, wave_no, attempt,
                                         "（本波打回重做，旧产出已作废）", "")

        except Exception as e:
            self._log(f"\n✗ 军团执行失败：{e}")
            # v4.124.5：异常落盘 —— 已完成的波次保留，下次可续跑
            try:
                legion.save_checkpoint(
                    self.pid, self.run_id, task, plan_text,
                    parts_by_wave, gate_reports, last_completed_wave=-1, waves=waves)
            except Exception:
                pass
            legion.end_run(self.run_id, "failed")
            out = "\n\n---\n\n".join(parts_by_wave[k] for k in sorted(parts_by_wave))
            self.done.emit(out or "")
            return

        # ---- 归并：按波次顺序汇总（重跑过的波只保留最后一次）----
        parts = [parts_by_wave[k] for k in sorted(parts_by_wave)]
        if gate_reports:
            parts.append("## 项目经理验收记录\n\n" + "\n\n---\n\n".join(gate_reports))
        output = "\n\n---\n\n".join(parts)

        n_out = len(parts_by_wave)
        if self._aborted:
            self._log(f"\n⏹ 军团已停下（{n_out}/{len(waves)} 个波次有产出）—— "
                      f"后续波次未执行，产出一律保留。")
            legion.end_run(self.run_id, "aborted")
            self._board("run", status="aborted", run_id=self.run_id)
            # v4.124.5：终止落盘 —— 让用户下次点续跑
            legion.save_checkpoint(
                self.pid, self.run_id, task, plan_text,
                parts_by_wave, gate_reports, last_completed_wave=n_out - 1,
                waves=waves)
            self._log(f"💾 已存 checkpoint：下次点项目「⏵ 续跑」可从第 {n_out + 1} 波继续")
        else:
            self._log(f"\n✓ 军团执行完毕（{n_out}/{len(waves)} 个波次有产出）。")
            # v4.124.5：执行完毕也清 checkpoint（不占磁盘 / 不让用户以为能续跑已完成项目）
            legion.clear_checkpoint(self.pid)
            legion.end_run(self.run_id, "done")
            self._board("run", status="done", run_id=self.run_id)
        self.done.emit(output or "（军团未产出内容）")

    # ---- 内部：授权弹窗的详情文本 ----
    def _auth_detail(self, wave_no, members, texts, v, verdict_text, attempt):
        """给主线程弹框用的授权详情：说清批的是什么、PM 为什么这么建议。"""
        names = "、".join(self._role_name(r) for r in members)
        produced = "、".join(f"{rn}（{len(t)} 字）" for rn, t in texts) or "（无人产出）"
        lines = [
            f"第 {wave_no} 波" + (f" · 第 {attempt} 次重跑" if attempt else ""),
            f"成员：{names}",
            f"产出：{produced}",
            "",
            f"📋 项目经理建议：{'PASS（放行下一波）' if v['pass'] else 'FAIL（打回重做）'}",
        ]
        if not v.get("parsed"):
            lines.append("⚠️ 未解析到标准判定行，已按『建议放行』处理，请你把关。")
        adv = (v.get("advice") or "").strip()
        if adv:
            lines.append("")
            lines.append(f"PM 理由 / 打回指令：\n{adv[:600]}")
        lines.append("")
        lines.append("【宪法第二章】重要节点必须你亲手授权 —— 项目经理只有建议权。")
        lines.append("· 放行 → 进入下一波　· 打回 → 本波重做　· 终止 → 停下并保留已产出")
        return "\n".join(lines)

    # ---- 内部：构造验收指令 ----
    def _build_gate_instruction(self, task, wave_no, attempt, members, texts,
                                plan_text, manual_ctx=""):
        """给项目经理的验收指令。

        刻意**不把产出正文塞进上下文**：AgentNode 会截断 context 到 3000 字，
        长稿会被砍得没法验收。改为让 PM 用 legion_get_output 自己读全文。

        v4.124.4 起 manual_ctx 注入：开工时读入的「手工补录」数据会再带一次，
        让 PM 验收第 N 波时还能想起「有真实数据可对照」而不是只看工具产出。
        """
        names = "、".join(self._role_name(r) for r in members)
        produced = "、".join(rn for rn, _ in texts) or "（无人产出）"
        lines = [
            f"【任务】{task}",
            f"【当前进度】第 {wave_no} 波刚跑完"
            + (f"（这是打回后的第 {attempt} 次重跑）" if attempt else ""),
            f"【本波成员】{names}",
            f"【已登记产出】{produced}",
        ]
        if not texts:
            lines.append("【异常】本波没有任何产出，请直接判定 FAIL 并说明如何补救。")
        if plan_text:
            lines.append(f"\n【开工时你定的执行计划】\n{plan_text[:_PLAN_CTX_LIMIT]}")
        if manual_ctx:
            # 截断以防 token 爆炸（plan_text 已 800 字，加起来超过 1.6k）
            lines.append(f"\n{manual_ctx[:_MANUAL_CTX_TOTAL_LIMIT]}")
        lines.append(
            "\n【你的动作】\n"
            "1. 用 legion_list_outputs 看产出清单，再用 legion_get_output 读全文"
            "（必要时 legion_read_log 查执行过程）；\n"
            "2. 逐条对照验收标准判断有没有硬伤（事实错误 / 跑题 / 缺交付物 / 违反约束）；\n"
            "3. 按输出格式给出结论，最后一行固定写：判定：PASS  或  判定：FAIL。\n"
            "🔴 你的判定只是**建议**：放不放行由用户授权，禁止写「准予放行」「已批准」。\n"
            "注意：没有硬伤就建议放行，打回要重跑、很贵。"
        )
        return "\n".join(lines)
