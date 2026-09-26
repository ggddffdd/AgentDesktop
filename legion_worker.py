# -*- coding: utf-8 -*-
"""Agent 军团执行器 v4.124.12

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
import re
import threading
import glob
import json

from PySide6.QtCore import QThread, Signal

# 顶层导入（不放在 run() 内）：保证 PyInstaller 静态分析能扫到这三个模块，
# 否则打包后运行会 ModuleNotFoundError。三者之间无循环依赖，可安全顶层导入。
import legion
from task_graph import TaskGraph, INCOMPLETE_FLAG
from agent_node import AgentNode

# v4.167.0：统一取消令牌（纯标准库）
from cancel_token import CancelledError

log = logging.getLogger("legion")


class _DenyAllAdapter:
    """权限适配器创建失败时的兜底：拒绝一切非只读（fail-closed）。

    存在的意义：闸门失效绝不能被静默忽略。宁可军团工具报「闸门不可用」，
    也不要在无人值守下放行 run_command / write_file。
    """

    def check(self, tool, args=None, role="", wave=0, **_kw):
        try:
            from legion_permissions import LegionDecision
            from risk import RiskClass, classify
            if classify(tool) == RiskClass.READ:
                return LegionDecision(True, "只读放行（闸门降级中）", "read_degraded")
            return LegionDecision(False, "权限闸门不可用，按保守策略拒绝", "adapter_unavailable")
        except Exception:
            class _D:
                allowed = False
                reason = "权限闸门不可用，按保守策略拒绝"
                rule = "adapter_unavailable"
                needs_auth = False
            return _D()

    def grant_wave(self, *a, **k):
        return None

    def grant_all(self, *a, **k):
        return None

    def revoke(self, *a, **k):
        return None

# PM 输出的《执行计划》最多带多少字进后续验收指令（防止长计划挤爆上下文）
_PLAN_CTX_LIMIT = 800

# v4.124.4：手工补录数据——「PM 拿不到数据就明说，经验判断必须标注，禁止编造」
# 落地机制。扫描 LEGION_DIR/legion_runs/manual_*.json 自动注入到 PM 提示词。
# 兄长/大哥可手动录入真实竞品数据（亚马逊现查现录），PM 第 2 波就能带数据放行，
# 而不必再撞一次"工具拿不到"的墙。JSON 字段自由，PM 看到文件名 + 头 800 字。
_MANUAL_CTX_FILE_GLOB = "manual_*.json"
_MANUAL_CTX_PER_FILE_LIMIT = 800  # 单文件最多带进 context 的字符数（防超出）
_MANUAL_CTX_TOTAL_LIMIT = 2400    # 所有 manual 文件合计上限字符数
# v4.124.16：结项总结给 PM 的输入上限（控制收尾这一刀的成本，别把整篇稿再烧一遍）
_FINAL_WAVE_BRIEF = 600           # 每波产出摘要字数
_FINAL_GATE_BRIEF = 300           # 每条验收记录摘要字数
_FINAL_CTX_MAX = 6000             # 结项输入总上限


class LegionWorker(QThread):
    """按项目波次跑一个军团任务，结果以纯文本归并后经 done 信号抛出。"""

    log_line = Signal(str)              # 进度/日志文本（追加到日志框）
    # 审计修复 G1：删除 node_status = Signal(str, str) —— 全工程零 .connect，
    # 且三处 emit 均与 _board()（同步写任务板的真实状态通道）逐点重复，属纯死代码。
    done = Signal(str)                  # 归并后的完整结果文本
    # 授权请求（宪法第二章）：(标题, 详情) -> 主线程弹框，结果经 set_auth_result 回传
    auth_request = Signal(str, str)
    # v4.124.8：联系项目经理 —— PM 判定「需重走流程」时 emit PM 回复，
    # 主线程弹窗让用户拍板：继续 / 停下重编，结果经 set_replan_result 回传
    replan_request = Signal(str)

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
        # v4.125 ②：本队 briefing（开工计划解析出的三段规矩，成员派发用）
        self._briefing = {}
        # v4.125 ③：子任务拆分 —— wi → [{idx,title,text}]；单项打回标记 (wi, idx)
        self._wave_subtasks = {}
        self._subtask_rerun = None
        # v4.126.1 ②：定点改稿的底稿 —— wi → {mi: (role_name, text)}
        # 打回只点名某个成员时，其他成员靠它原样保留，不必重烧 token。
        self._wave_member_texts = {}
        # v4.124.5：断点续传
        # resume_from 是从 checkpoint 恢复出的「下一个要跑的 wave_idx」
        # （即 last_completed_wave + 1），传 None 表示从头跑
        self._resume_from = resume_from
        # ckpt_run_id 用于续跑时识别原 run_id，避免历史记录混淆
        self._ckpt_run_id = ckpt_run_id
        self._ctx = ""                  # 自己维护的累积上下文（见模块注释：修并行覆盖）
        # v4.140 P2-1：必须在 __init__ 就把 _aborted 初始化为 False。
        # 原实现只在 abort() 里设 True、在 run() 顶部（1497 行）重置为 False ——
        # 若 abort() 在 run() 启动循环前被调用，True 会被 1497 行的重置覆盖，
        # 造成「点了停止却没停」的竞态。现统一在 __init__ 初始化，run() 不再重置。
        self._aborted = False
        # v4.140 P0-1：wi -> [role_name,...] 记录本波「执行失败（异常）」的成员，
        # 用于杜绝「异常被伪装成 completed」的假成功，并在报告里显式标注。
        self._wave_failed_members = {}
        # v4.168.0（审查 #2 残余）：wi -> [role_name,...] 记录「跑满轮次但没写出正文」
        # 的成员。与 failed 分开登记 —— 一个是被打崩了，一个是跑了但没产出，
        # 复盘时要能分清。结项报告显式补录，绝不冒充「成员已完成」。
        self._wave_incomplete_members = {}
        # v4.140 P0-2：wi -> [(role_name, err_str),...] 记录提交自检闸（数据/形态）
        # 自身崩溃的成员，fail-closed 不静默放行，但也不无限回炉成员。
        self._gate_errors = {}
        # v4.140 P1-1：(_wi, role_name) -> text 的「当前最新稿」映射，
        # 取代旧的 append 历史式 _outputs，避免回炉后首稿/回炉稿不一致。
        self._outputs_map = {}
        self.run_id = None
        self.pid = (self.project or {}).get("id") or ""
        # 授权等待（人手闸门）
        self._auth_event = threading.Event()
        self._auth_val = None
        # v4.124.13：授权弹窗里用户顺手填的一笔（打回=打死方向 / 放行=锁定标的）
        self._auth_note = ""
        self._auth_remember = False
        # v4.124.8：联系项目经理的消息队列 + 重走流程拍板
        #   _pm_inbox 由 UI 线程 send_message 入队，波循环检查点消费；
        #   _replan_event / _replan_val 是「PM 说不 → 用户拍板」的阻塞握手。
        self._pm_inbox = []
        self._inbox_lock = threading.Lock()
        self._replan_event = threading.Event()
        self._replan_val = None
        self._pname = ""        # run() 里赋项目显示名，供 record_message 审计用
        # v4.167.0（审查 #1）：军团权限适配器（run() 里按 gate_mode 创建并预授）
        self._perm = None
        # v4.167.0（审查 §3）：本 run 取消令牌（abort / 授权中止时广播）
        self._cancel = None

    def _ensure_perm(self):
        """取军团权限适配器（懒创建，保证 _wrap / 授权注入都能拿到）。

        v4.167.0（审查 #1）：成员默认只读，危险能力需本波显式授权；
        适配器内部自建权限引擎（范围比主对话链窄），**不复用**主链那个
        （它可能带 session trust，会把信任外溢到无人值守的军团）。
        """
        if getattr(self, "_perm", None) is not None:
            return self._perm
        try:
            from legion_permissions import LegionPermissionAdapter
            self._perm = LegionPermissionAdapter(
                run_id=getattr(self, "run_id", "") or "",
                project_id=getattr(self, "pid", "") or "",
                project_name=getattr(self, "_pname", "") or "",
            )
        except Exception as e:
            # fail-closed：闸门建不起来时，用「拒绝一切非只读」的哨兵兜底，
            # 宁可军团工具报"闸门不可用"，也不静默放行。
            try:
                log.error("军团权限适配器创建失败，降级为保守拒绝: %s", e)
            except Exception:
                pass
            self._perm = _DenyAllAdapter()
        return self._perm


    # ---- v4.167.0：本 run 统一取消令牌（审查 §3）----
    def _ensure_cancel(self):
        """取本 run 的取消令牌（懒创建）。

        为什么要它：原来只在**波开始前**看 `self._aborted`，波内成员进去之后
        没人再查 —— 一次波内并行最多 5 个成员、每个还要跑多轮模型调用，
        所以"点了停止"最坏要等一整波跑完，期间继续烧 token。
        令牌把"停止意图"变成成员每个阶段都能看到的东西。
        """
        if getattr(self, "_cancel", None) is None:
            try:
                from cancel_token import CancellationToken
                self._cancel = CancellationToken(
                    name=f"legion/{getattr(self, 'run_id', '') or '-'}")
            except Exception as e:
                try:
                    log.warning("取消令牌创建失败: %s", e)
                except Exception:
                    pass
                return None
        return self._cancel

    def _cancel_run(self, reason="", stage=""):
        """广播取消（幂等：重复调只有第一次生效）。返回是否本次真正完成取消。"""
        tk = self._ensure_cancel()
        if tk is None:
            return False
        try:
            return tk.cancel(reason, stage=stage)
        except Exception:
            return False


    # ---- 授权：向主线程请求人手批准（PM 无放行权，只有建议权）----
    def _request_auth(self, title, detail, timeout=600):
        """阻塞等待用户授权。返回 "pass" / "reject" / "abort" / None（超时=不授权）。"""
        self._auth_val = None
        self._auth_note = ""     # v4.124.13：每次授权重新收一笔
        self._auth_revision = ""  # v4.131-E：每次授权重新收一条手写改稿要求
        self._auth_event.clear()
        try:
            self.auth_request.emit(title, detail)
        except Exception as _e:
            # v4.145 修复⑥：信号发射失败不再静默当成超时——记日志后仍返回 None
            # （调用方按「未授权」处理），便于排查「授权入口失联」类问题。
            try:
                log.error("auth_request 信号发射失败: %s", _e)
            except Exception:
                pass
            return None
        self._auth_event.wait(timeout=timeout)
        return self._auth_val

    def set_auth_revision(self, text):
        """v4.131-E：大哥在授权弹窗里手写的改稿要求（选填）。

        只在**打回**时生效，拼在项目经理指令的最前面 —— 你亲手写的优先级最高，
        PM 那套通用套话压不过你的一句话。放行/终止时忽略。
        """
        self._auth_revision = (text or "").strip()

    def set_auth_note(self, note, remember=False):
        """v4.124.13：主线程在授权弹窗里顺手填的一笔（须在 set_auth_result 之前调）。

        note     —— 打回=打死这个方向（进否决黑名单）；放行=锁定这个标的
        remember —— 勾了「跨项目记住」→ 连同当事角色写进跨项目教训（长期记忆）
        """
        self._auth_note = (note or "").strip()
        self._auth_remember = bool(remember)

    def _apply_auth_note(self, decision, members):
        """把弹窗里的一笔写进记忆层，返回审计用的补充说明（无则空串）。"""
        note = (getattr(self, "_auth_note", "") or "").strip()
        if not note or decision not in ("pass", "reject"):
            return ""
        if decision == "reject":
            if legion.kill_direction(self.pid, note):
                dd = self.project.get("dead_directions")
                if not isinstance(dd, list):
                    dd = self.project["dead_directions"] = []
                for n in [x.strip() for x in note.replace("；", ";").replace("，", ";")
                          .replace("、", ";").split(";") if x.strip()]:
                    if n not in dd:
                        dd.append(n)
            if getattr(self, "_auth_remember", False):
                tags = [str(r.get("name", "")).strip() for r in (members or [])
                        if str(r.get("name", "")).strip()]
                legion.add_global_lesson(note, tags=tags)
                self._log(f"  🧠 已写入跨项目教训（长期记忆）：{note[:60]}")
            self._log(f"  ☠ 已记入否决黑名单（永久，所有波次/续跑都不再提）：{note[:60]}")
            return f"打死方向：{note}"
        # v4.124.14：标的绑定当时任务 —— 换任务重跑时旧标的自动挂起
        if legion.lock_target(self.pid, note, task=getattr(self, "task", "")):
            self.project["locked_target"] = {"text": note,
                                             "task": getattr(self, "task", "")}
        self._log(f"  🔒 已锁定标的（永久，后续波次只围绕它）：{note[:60]}")
        return f"锁定标的：{note}"

    def set_auth_result(self, val):
        """主线程弹框后回调（子线程 Event 解阻塞）。"""
        self._auth_val = val
        self._auth_event.set()

    def abort(self):
        """主线程调用：请求停止当前执行（保留已产出，可续跑）。

        v4.124.8：用于「联系项目经理 → PM 说不 → 用户选停下重编」的收尾，
        也兼容任何"立刻停"的场景。若正阻塞在授权/重走流程弹窗，一并解除阻塞。
        """
        self._aborted = True
        # v4.167.0（审查 §3）：立即广播取消令牌 —— 波内成员在下一轮
        # 模型调用 / 工具调用前就会看到并停手，不再继续扣费。
        self._cancel_run("用户点了停止", stage="user_stop")
        try:
            self._auth_val = "abort"
            self._auth_event.set()
        except Exception:
            pass
        # v4.124.17 M-06：abort 必须同时解除授权弹窗与重编弹窗的阻塞——
        # 正阻塞在 _request_replan 时调 abort，这里塞 "abort" 哨兵并 set() 立即解阻塞。
        # v4.153 P1-01：_request_replan 收尾用 `is True` 严格判定，
        # "abort" 字符串不会被判成 truthy 的"继续"，而是正确判为"停下重编"。
        try:
            self._replan_val = "abort"
            self._replan_event.set()
        except Exception:
            pass

    # ---- v4.124.8：联系项目经理（消息队列 + 重走流程拍板）----
    def send_message(self, text, urgent=False):
        """UI 线程调用：把用户的一句话投进 PM 收件箱。

        不阻塞、不直接打断正在跑的波 —— 原子波设计：一波内成员并行执行不可分割，
        中途杀成员会出半成品。消息只入队，由波循环的检查点消费。
        返回 True 表示已入队。
        """
        text = (text or "").strip()
        if not text:
            return False
        with self._inbox_lock:
            self._pm_inbox.append({"text": text, "urgent": bool(urgent)})
        return True

    def _drain_messages(self, urgent_only=False):
        """取出队列消息并清空已取部分。

        urgent_only=True 只取急件（留在队列里的备忘下一波界再处理）。
        返回 [(text, urgent), ...]。
        """
        with self._inbox_lock:
            if urgent_only:
                out = [m for m in self._pm_inbox if m["urgent"]]
                self._pm_inbox = [m for m in self._pm_inbox if not m["urgent"]]
            else:
                out = list(self._pm_inbox)
                self._pm_inbox = []
        return out

    def _request_replan(self, reply):
        """阻塞等待用户拍板：继续 / 停下重编。返回 True=继续，False=停下。

        与 _request_auth 同一机制：子线程 emit → 主线程弹窗 → set_replan_result 解阻塞。
        v4.153 P1-01：宪法第二章 fail-closed —— 任何异常、超时、或 abort 中断，
        一律判「停下重编」，不继续（未明确授权不继续）。旧实现异常/超时都返回 True，
        等于"没弹窗、没人拍板也默认继续"，违反授权权归用户的铁律。
        """
        self._replan_val = None
        self._replan_event.clear()
        try:
            self.replan_request.emit(reply)
        except Exception:
            self._log("⚠️ 重编弹窗派发失败 —— 安全起见判为「停下重编」")
            return False
        self._replan_event.wait(timeout=600)
        # 收尾：严格布尔判定 —— set_replan_result 写入的是真布尔；
        # abort() 会塞字符串 "abort" 解除阻塞，必须判成「停下」而非 truthy 误判继续
        # （旧实现用 truthy：bool("abort") 为 True，会把"用户已停"误判成"继续"，
        #  正阻塞在重编弹窗时点了停止会白等满 600s 超时还继续跑）。
        if self._replan_val is True:
            return True
        return False

    def set_replan_result(self, val):
        """主线程弹框后回调：True=继续跑，False=停下重编。"""
        self._replan_val = bool(val)
        self._replan_event.set()

    def _respond_to_pm(self, pm_role, msg_text, urgent, wave_no):
        """把用户一句话交给 PM 就地处里，返回 PM 的回复文本。"""
        if getattr(self, "_aborted", False):
            return ""
        kind = "急件" if urgent else "备忘"
        instr = (
            f"【用户的即时要求 · {kind} · 第 {wave_no} 波】\n"
            f"{msg_text}\n\n"
            "【你的判断】这条要求是否会影响**后续波次的执行面**"
            "（换品类 / 换目标 / 换验收标准 / 换阵容 / 换交付物形态）？\n"
            "· 会影响 → 明确回复「这需要重新编计划、重新走审批」，说明影响哪几波、为什么；\n"
            "· 只是小调整（措辞 / 语气 / 补一条参考）→ 就地吸收，说明你接下来怎么调整调度。\n"
            "最后一行固定输出：判定：REPLAN  或  判定：ADJUST"
        )
        out = self._run_pm(pm_role, "pm_message", instr)
        return (out or "").strip()

    def _check_pm_inbox(self, pm, wave_no, urgent_only=False):
        """波循环检查点：把队列里的用户消息交给 PM 就地处里。

        urgent_only=True 只处理急件（波刚跑完、验收前）；False 处理全部（波界）。
        返回 True 表示用户决定「停下重编」（self._aborted 已置位），外层应立即停。
        """
        msgs = self._drain_messages(urgent_only=urgent_only)
        if not msgs:
            return False
        for m in msgs:
            # 每句话都进审计账本（与授权同 run_id 谱系）——"因为用户 17:10 说了什么"可查
            legion.record_message(self.pid, self._pname, self.run_id, wave_no,
                                  m["text"], urgent=m["urgent"])
            kind = "急件" if m["urgent"] else "备忘"
            self._log(f"💬 联系项目经理（{kind} · 第 {wave_no} 波）：{m['text'][:80]}")
            reply = self._respond_to_pm(pm, m["text"], m["urgent"], wave_no)
            if reply and ("REPLAN" in reply or "需要重新编计划" in reply
                          or "重新走审批" in reply):
                self._log("📋 项目经理：这条要求会影响后续波次 → 需要重新编计划、重新走审批")
                cont = self._request_replan(reply)
                if not cont:
                    self._log("⏹ 你已决定停下重编计划 —— 保留已产出，可续跑")
                    self._aborted = True
                    return True
                self._log("▶ 你决定继续按原计划跑（这条要求已留痕，本次不改变执行面）")
            else:
                self._log(f"📋 项目经理已就地吸收：{reply[:120]}")
        return False


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
            # v4.167.0（审查 #1）：把权限闸门挂到成员上 —— 成员的工具调用
            # 在 agent_node.py 里会先过 perm.check()，默认只读、写入限工作区。
            try:
                _p = self._ensure_perm()
                if _p is not None:
                    agent.perm = _p
                # v4.167.0（审查 §3）：给成员挂本 run 取消令牌的子令牌 ——
                # 成员在模型轮次/工具调用之间查它，父取消即全部子取消。
                _tk = self._ensure_cancel()
                if _tk is not None:
                    agent.token = _tk.child(name=f"{role_name or tid}/w{wave_no}")
                agent.wave_no = wave_no
            except Exception:
                pass

            self._board(board_key, role=role_name, wave=wave_no, status="running")
            # v4.131：抓取留痕归属 —— 本线程内的 web_search / web_fetch 全记到这位
            # 成员名下。必须线程局部（波内并行），全局会串到别的成员头上。
            try:
                legion.set_source_ctx(getattr(self, "run_id", None), wave_no, role_name)
            except Exception:
                pass
            try:
                r = agent.run(state)
                # v4.168.0（审查 #2 残余）：成员跑满轮次却没写出正文 ——
                # 这不是成功（没有可验收的东西），也不该当崩溃。
                # 标记出来交给 TaskGraph 记 incomplete，并让任务板/结项报告都看得见。
                _inc = bool(isinstance(r, dict)
                            and r.get(getattr(agent, "name", "") + "_incomplete"))
                if _inc and isinstance(r, dict):
                    r = dict(r)
                    r[INCOMPLETE_FLAG] = True
                try:
                    out_txt = ""
                    if isinstance(r, dict):
                        out_txt = (r.get(tid + "_output") or "").strip()
                    if _inc:
                        self._board(board_key, role=role_name, wave=wave_no,
                                    status="incomplete", chars=len(out_txt),
                                    summary=(out_txt.replace("\n", " ")[:80]
                                             or "跑满轮次无正文产出"))
                        self.log_line.emit(
                            f"  [{tid}] ⚠️ 成员跑满轮次但未产出正文（incomplete）"
                            f"—— 不计为完成，已显式标注\n")
                    else:
                        self._board(board_key, role=role_name, wave=wave_no, status="done",
                                    chars=len(out_txt),
                                    summary=out_txt.replace("\n", " ")[:80])
                except Exception:
                    self._board(board_key, status="incomplete" if _inc else "done")
                return r
            except Exception as e:
                # v4.167.0（审查 §3）：**取消不是失败** —— 任务板必须能区分
                # "被大哥叫停"与"成员崩了"，否则事后分不清谁真干了、谁没跑。
                if isinstance(e, CancelledError):
                    self.log_line.emit(
                        f"  [{tid}] ⏹ 已取消"
                        f"（{getattr(e, 'stage', '') or '停止时'}）\n")
                    self._board(board_key, role=role_name, wave=wave_no,
                                status="cancelled",
                                summary=f"已取消：{getattr(e, 'reason', '')}"[:80])
                    raise
                self.log_line.emit(f"  [{tid}] 成员执行异常：{e}\n")
                self._board(board_key, role=role_name, wave=wave_no, status="error",
                            summary=str(e)[:80])
                # v4.140 P0-1：成员执行异常**必须显式失败**，绝不能再伪装成 completed。
                # 原实现 return dict(state) 让 TaskGraph 见正常 return 即标记 completed，
                # 于是「成员崩溃」被当成「成员交了空稿」—— 假成功，PM/报告都看不到。
                # 现在 re-raise：TaskGraph 的 f.result() 捕获后把节点标为 failed
                # （见 task_graph.py:144），单个成员失败**不**拖垮整波（其他成员仍正常完成），
                # 但失败本身不再被掩盖。下游 _run_wave 会二次确认并显式标注本波失败。
                raise
            finally:
                try:
                    legion.clear_source_ctx()
                except Exception:
                    pass
        return _exec

    # ---- 内部：跑一波（波内并行）----
    def _run_wave(self, wi, members, state):
        """用 TaskGraph 并行跑一波，返回合并后的 state。

        派发前先看 self._aborted：用户已经终止，**绝不**发起任何成员调用。
        这是「成员零调用」的最后一道闸（外层 for 顶已挡过，这里是兜底）。
        """
        if getattr(self, "_aborted", False):
            # v4.167.0（审查 §3）：停止意图必须同步到令牌 —— 否则已经进了
            # 线程池的成员仍会被逐个取出执行（"点了停止还继续扣费"）。
            self._cancel_run("用户已终止本次军团运行", stage="wave_dispatch")
            return dict(state)
        tg = TaskGraph()
        wave_no = wi + 1
        # v4.124.12 改动①：及格线下放 —— 交付纪律直接注进成员 prompt，
        # 成员第一遍就知道验收标准（形态闸/标的闸/引用纪律），不用等打回才学乖。
        discipline = legion.member_discipline_block(wave_no)
        # v4.125 ②：本队 briefing（开工计划里 PM 定的三段）也注进成员 prompt ——
        # 纪律不给成员看就是纸面规矩。只注纪律+交接物（形态已在 discipline 里）。
        _brief = getattr(self, "_briefing", None) or {}
        _brief_blk = ""
        if _brief.get("工作纪律"):
            _brief_blk += "\n【本队工作纪律（项目经理定的，验收按此打回）】" + _brief["工作纪律"]
        if _brief.get("交接物") and wave_no >= 2:
            _brief_blk += "\n【上一波留下的交接物（直接拿来用，别重问）】" + _brief["交接物"]
        # v4.126.1 ①：交付形态钉在 prompt **第一行**。
        # 此前交付形态只写在 PM 的执行计划里，成员 prompt 拿不到（纪律块只有通用
        # 形态闸，没有本任务约定的具体交付物）→ 实战里写手连打回两次都交错东西：
        # 第一次写成方案说明、第二次还是不成稿。把「这次交什么」摆在成员睁眼
        # 第一眼的位置，比打回两次便宜得多。
        _form_head = ""
        if _brief.get("交付形态"):
            _form_head = (
                "🔴【本波交付形态 · 交错直接打回】\n"
                + _brief["交付形态"].strip()
                + "\n\n⬆️ 上面这条决定你这次要交出什么。先确认形态，再动笔；"
                  "形态不对，内容再好也是废稿。\n\n")
        # v4.124.13 三层记忆：标的记忆（长期）+ 项目教训本（中期）+ 跨项目教训（长期）
        # 全部拼进成员 prompt —— 军团不该每次都是新兵营，打过的败仗要变成教材。
        tmem = legion.target_memory_block(self.project, task=getattr(self, "task", ""))
        plessons = legion.project_lessons_block(self.pid)
        # v4.134：PM 的《能力配置》在这里落地 —— 工具白名单、追加技能、本波口径。
        # 角色卡是「默认能力」，能力配置是「本波该有什么」，后者优先。
        _caps = getattr(self, "_capability", None) or {}
        _pool = legion.tool_pool() if _caps else None
        for mi, role in enumerate(members):
            tid = f"w{wi}_m{mi}"
            _cap = _caps.get(legion.cap_norm_name(role.get("name") or ""))
            role, _cnotes, _miss = legion.apply_capability(role, _cap, tool_pool=_pool)
            if _cap and _cnotes:
                self._log("  ⚙️ %s 本波能力：%s"
                          % (self._role_name(role), "；".join(_cnotes)))
                try:
                    self._cap_notes.append((wave_no, self._role_name(role), list(_cnotes)))
                except Exception:
                    pass
            # v4.147.9：打印成员**最终工具集** —— 排查「成员为什么不用某工具」时，
            # 第一步就是确认它手里到底有没有这个工具（PM 能力限定 / 存档复用都可能收走）。
            self._log("  🧰 %s 本波工具集：%s"
                      % (self._role_name(role), "、".join(sorted(role.get("tools") or []))))
            # v4.135：PM 在《能力配置》里「追加技能」写了库里不存在的 slug（幽灵技能），
            # 过去被 apply_capability 静默丢、只记一条日志，缺口永远进不了「去 GitHub 找」闭环。
            # 现在把 miss 回灌到差技能缺口清单，UI 才能自动找候选 + 一键装挂。
            if _miss:
                self._add_missing(_miss, role.get("name"))
            role_name = f"{role.get('emoji', '')}{role.get('name', '角色')}".strip()
            gmem = legion.global_lessons_block(role)
            # v4.138 P2/P1：把**本任务**的交付契约（从任务描述抠出的表列/条数/必需
            # 小节 —— 提交时机器按它校验）和官方站打不开时的替代源清单钉进 prompt。
            # 此前任务级形态只活在 PM 的 briefing 里，PM 没写就彻底丢失。
            _task0 = getattr(self, "task", "")
            prompt = (_form_head + legion.build_role_prompt(role) + discipline
                      + legion.deliverable_contract_block(
                          legion.extract_deliverable_contract(_task0, role_name))
                      + legion.fallback_source_block(_task0, role_name)
                      + legion.capability_prompt_block(_cap) + _brief_blk)
            for blk in (tmem, plessons, gmem):
                if blk:
                    prompt += "\n\n" + blk
            agent = AgentNode(tid, prompt, tools=set(role.get("tools") or []),
                              mw=self.mw, model_cfg={"profile": role.get("model", "")})
            tg.create(tid, role_name,
                      self._wrap(agent, tid, board_key=tid,
                                 role_name=role_name, wave_no=wave_no),
                      role.get("mission", ""))
        _state = tg.run(state, token=self._ensure_cancel())
        # v4.140 P0-1：二次确认「成员执行异常」已被显式标记失败，杜绝假成功。
        # _wrap 现对异常 re-raise → TaskGraph 把该节点标为 failed（task_graph.py:144）。
        # 这里扫描各节点状态：任何 failed 成员都意味着本波交付不完整，记入
        # _wave_failed_members，供 parts_by_wave / 结项报告显式标注（不再静默缺失）。
        for mi, role in enumerate(members):
            _tid = f"w{wi}_m{mi}"
            _task = getattr(tg, "_tasks", {}) or {}
            _node = _task.get(_tid)
            _st = getattr(_node, "status", None) if _node is not None else None
            if _st == "failed":
                _rn = self._role_name(role)
                self._wave_failed_members.setdefault(wi, []).append(_rn)
                self._log(f"  ⚠️ 成员 {_rn} 执行失败（节点 failed）—— 本波交付不完整，"
                          f"已显式标记，不再伪装成成功")
            elif _st == "incomplete":
                # v4.168.0（审查 #2 残余）：跑满轮次没写正文 —— 同样不算交活。
                # 与 failed 分开登记：一个是"崩了"，一个是"跑了但没产出"，
                # 复盘时要能分清是模型挂了还是任务本身就写不出东西。
                _rn = self._role_name(role)
                self._wave_incomplete_members.setdefault(wi, []).append(_rn)
                self._log(f"  ⚠️ 成员 {_rn} 跑满轮次未产出正文（incomplete）"
                          f"—— 不计为完成，已显式标注")
        return _state

    # ---- v4.138 P0：交付闸门前移（成员产出 → PM 评审之间的机器闸）----
    def _collect_wave_texts(self, wi, members, wave_state, wave_no, attempt,
                            record=True):
        """从各成员节点收集本波产出（从各自节点取，避免并行 context 覆盖丢稿）。

        record=True 才落库（legion 运行期账本，保留每次 attempt 历史用于审计）。
        _wave_member_texts（底稿）无论 record 与否都更新 —— 回炉要基于最新稿改。
        v4.140 P1-1：_outputs 改为「当前最新稿」去重覆盖（按 (wi, role)），
        不再 append 历史首稿 —— 回炉后首稿/回炉稿不一致（正是 P1-2 悬空引用
        拿旧稿的根因）。现在 _outputs 与 _wave_member_texts 语义一致：都是最新稿。
        """
        texts = []
        for mi, role in enumerate(members):
            tid = f"w{wi}_m{mi}"
            node_out = wave_state.get(tid, {}) or {}
            txt = ""
            if isinstance(node_out, dict):
                txt = (node_out.get(f"{tid}_output") or "").strip()
            role_name = self._role_name(role)
            if record:
                legion.record_output(self.run_id, wave_no, attempt, role_name, txt)
            if txt:
                texts.append((role_name, txt))
                # v4.140 P1-1：最新稿去重覆盖（回炉后旧首稿被新稿顶替）
                self._outputs_map[(wi, role_name)] = txt
                self._outputs = sorted(
                    ((_w, _rn, _t) for (_w, _rn), _t in self._outputs_map.items()),
                    key=lambda x: (x[0], x[1]))
                self._wave_member_texts.setdefault(wi, {})[mi] = (role_name, txt)
        return texts

    def _submit_gate(self, wi, members, wave_state, texts, wave_no, attempt, state):
        """v4.138 P0：**成员产出后、PM 评审前**的机器闸。

        对每份产出跑「数据可信度（audit_data_quality）+ 交付形态（按任务契约
        audit_deliverable_form）」双校验；不合格的成员**当场回炉**（带机器给出的
        具体改法），最多 SUBMIT_GATE_ROUNDS 轮，通过才交项目经理。

        为什么前移：原 v4.131 硬检发生在 PM 判完之后，脏产出已经烧掉
        「成员执行 + PM 评审」两轮才被拦、还要大哥人工打回。放在这里，
        废稿在零成本点被退回 —— PM 与大哥都看不到它。

        返回 (texts, note)；note 给日志/报告看（可为空串）。
        """
        _rounds = int(getattr(legion, "SUBMIT_GATE_ROUNDS", 0) or 0)
        if _rounds <= 0:
            return texts, ""
        _task = getattr(self, "task", "")
        # v4.147.8：形态契约必须并入 **PM 的交付形态 / 交接物 / 开工计划**。
        # 大哥的任务往往只有一句（实测「TK马来区选品运营售后全链路」才 15 字），
        # 而「列固定 7 列 / 行数 ≥10 行」这类**可机器校验**的要求全写在 PM 的计划里。
        # 原实现只拿 self.task 解析 → 契约恒为空 → audit_deliverable_form 的
        # `if cols or min_rows:` 为假 → **形态闸整体空转**：裸 URL 清单、原始字段 dump
        # 都能过闸交到 PM（TK 马来团第 1 波实测，直接导致连续 FAIL）。
        _brief0 = getattr(self, "_briefing", None) or {}
        _contract_src = "\n\n".join(_x for _x in (
            _task,
            str(_brief0.get("交付形态") or ""),
            str(_brief0.get("交接物") or ""),
            str(getattr(self, "_plan_text", "") or ""),
        ) if _x)
        _name2mi = {}
        for mi, role in enumerate(members):
            _name2mi[self._role_name(role)] = mi
        note = ""
        for _rd in range(_rounds):
            if getattr(self, "_aborted", False):
                break
            _bad = []
            for rn, tx in texts:
                mi = _name2mi.get(rn)
                if mi is None:
                    continue
                # v4.140 P0-2：提交自检闸（数据可信度 + 交付形态）自身崩溃时，
                # 绝不能把「没能验证」当成「通过」。原实现 except→None 后，
                # 下面的判坏条件对 None 不成立 → 静默放行 → 假成功。
                # 现改为三态：verdict="bad" 真坏（回炉）；verdict="error" 闸故障
                # （fail-closed：记系统异常、标 unverified，但**不对成员反复回炉**
                # —— 回炉修不了闸，会白烧 token 还死循环）。error 成员仍交 PM，
                # 但带「未验证」标记，结项报告也显式补录，杜绝静默。
                try:
                    _dq = legion.audit_data_quality(tx, role_name=rn, wave=wave_no)
                except Exception as _e:
                    _dq = {"verdict": "error", "error": str(_e)}
                    self._log(f"  ⚠️ 数据可信度闸异常（{rn}）：{_e} —— 该产出未经验证")
                try:
                    _fm = legion.audit_deliverable_form(
                        tx, legion.extract_deliverable_contract(_contract_src, rn), rn)
                except Exception as _e:
                    _fm = {"bad": False, "verdict": "error", "error": str(_e)}
                    self._log(f"  ⚠️ 交付形态闸异常（{rn}）：{_e} —— 该产出未经验证")
                _is_bad = (isinstance(_dq, dict) and _dq.get("verdict") == "bad") \
                          or (isinstance(_fm, dict) and _fm.get("bad"))
                _is_err = (isinstance(_dq, dict) and _dq.get("verdict") == "error") \
                          or (isinstance(_fm, dict)
                              and (_fm.get("verdict") == "error" or _fm.get("error")))
                if _is_bad:
                    _bad.append((mi, rn, _dq, _fm))
                elif _is_err:
                    # 闸故障：记系统异常，标记该成员产出「未验证」，交 PM 时一并提示；
                    # 既不放行（fail-closed），也不对成员死循环回炉。
                    _err_msg = str((_dq or {}).get("error")
                                   or (_fm or {}).get("error") or "未知闸故障")
                    self._gate_errors.setdefault(wi, []).append((rn, _err_msg))
                    self._log(f"  🔧 提交自检闸故障（{rn}）：产出标记为未验证，"
                              f"已记录系统异常（不静默放行、不无限回炉）")
            if not _bad:
                break
            _who = "、".join(rn for (_mi, rn, _d, _f) in _bad)
            self._log(f"  🚦 提交自检未过（{len(_bad)} 位：{_who}）—— 当场回炉、"
                      f"不进项目经理（第 {_rd + 1}/{_rounds} 轮）")
            _blk = legion.submit_gate_block(
                [(rn, _d, _f) for (_mi, rn, _d, _f) in _bad])
            _ws2 = self._run_member_rerun(
                wi, members, [mi for (mi, _rn, _d, _f) in _bad], _blk, state)
            if _ws2 is None:
                self._log("  ⚠️ 回炉未产出新稿 —— 交项目经理按常规复核")
                break
            wave_state.update(_ws2)
            # v4.147.6 修「回炉改好了、工具却读到旧稿」死循环（TK 马来团第 1 波实测）：
            # 原为 record=False —— 回炉后的新稿只进 texts 内存、**不落库**；而 PM 是用
            # legion_get_output / legion_list_outputs **工具**去读产出的（读的正是落库那份），
            # 于是永远读到首版报错文本（如「抓取失败：<urlopen error timed out>」30 字），
            # 判 FAIL → 打回重跑 → 成员又回炉改好 → 又不落库 → 又读到旧稿 → **无限循环**。
            # 实测：研究员回炉稿 2363 字，工具里却只有 30 字；第 0 次同样发生过。
            # 此处 texts 就是要交 PM 的最终稿，必须落库（覆盖语义见 legion.record_output）。
            texts = self._collect_wave_texts(wi, members, wave_state, wave_no,
                                             attempt, record=True)
            note = f"（本波经 {_rd + 1} 轮提交自检回炉）"
        return texts, note

    # ---- 内部：子任务单项改稿（v4.125 ③）----
    def _run_subtask_rerun(self, wi, members, sub_idx, advice, state):
        """只重生成第 sub_idx 个子项（改子不动父与兄弟）。

        返回仿 _run_wave 的 state（成员 0 的 tid 下挂新文本，收集逻辑复用）；
        子项越界 / 无子任务 / 改稿失败返回 None（上层退回整波重跑）。
        首版主力成员取 members[0]（写手/配图师通常是波里第一个产出角色）。
        """
        subs = self._wave_subtasks.get(wi) or []
        if not (0 <= sub_idx < len(subs)) or len(subs) < 2:
            return None
        if not members:
            return None
        sub = subs[sub_idx]
        wave_no = wi + 1
        role = members[0]
        role_name = self._role_name(role)
        tid = f"w{wi}_m0"
        # v4.134：子项改稿同样套 PM 的能力配置（工具/技能/口径），
        # 否则「只重做这一个」会退回角色卡默认能力。
        _caps = getattr(self, "_capability", None) or {}
        _cap = _caps.get(legion.cap_norm_name(role.get("name") or ""))
        role, _, _miss = legion.apply_capability(
            role, _cap, tool_pool=(legion.tool_pool() if _caps else None))
        if _miss:
            self._add_missing(_miss, role.get("name"))
        prompt = (legion.build_role_prompt(role)
                  + legion.member_discipline_block(wave_no)
                  + legion.capability_prompt_block(_cap))
        _brief = getattr(self, "_briefing", None) or {}
        if _brief.get("工作纪律"):
            prompt += "\n【本队工作纪律】" + _brief["工作纪律"]
        instr = (
            f"【子任务改稿 · 只重做这一个】\n"
            f"本波产出被拆成 {len(subs)} 个子项，项目经理只打回了第 {sub['idx']} 个：\n\n"
            f"── 子项标题：{sub['title']}\n"
            f"── 原文 ──\n{sub['text']}\n── 原文止 ──\n\n"
            f"【打回意见（必须逐条落实）】\n{advice or '（按质量标准复查）'}\n\n"
            f"要求：\n"
            f"1. 只重新产出**这一个子项**的新内容，标题格式与原文保持一致；\n"
            f"2. 打回意见指出的每一条都要改到位；没被点名的部分尽量保留；\n"
            f"3. 🔴 禁止输出其他子项、禁止解释、禁止客套——直接给新内容。"
        )
        try:
            agent = AgentNode(tid, prompt, tools=set(role.get("tools") or []),
                              mw=self.mw, model_cfg={"profile": role.get("model", "")})
            st = {"task": instr, "query": instr,
                  "context": (self._ctx or "")[-2000:]}   # 只带尾部关键前置，不烧全量
            st = self._wrap(agent, tid, board_key=tid,
                            role_name=role_name, wave_no=wave_no)(st)
            new_text = (st.get(tid + "_output") or "").strip()
        except Exception as e:
            self._log(f"  ⚠️ 子项改稿异常：{e}")
            return None
        if not new_text:
            self._log("  ⚠️ 子项改稿无产出 —— 退回整波重跑")
            return None
        sub["text"] = new_text
        self._log(f"  🧩 子项 {sub['idx']} 改稿完成（{len(new_text)} 字）")
        # 仿 _run_wave 的 state 结构，让 while 循环里的收集逻辑直接复用
        return {"w{0}_m{1}".format(wi, 0): {tid + "_output": new_text}}

    # ---- 内部：定点改稿（v4.126.1 ②）----
    def _parse_named_members(self, advice, members):
        """从打回指令里解析「被点名要重做」的成员下标。

        PM 的打回指令常见形态：
            1. **✍️写手重做**：在产出正文中直接给出《淘宝详情页文案》……
        此前无论点名几个成员，一律整波重跑 —— 没犯错的成员也跟着重烧一遍 token
        （实战：第 2 波打回两次，审校被无辜重跑两次）。
        命中条件：该行出现成员名 且 同行出现重做/重写/改稿等动词。
        """
        import re
        hits = []
        if not advice or not members:
            return hits
        verb = re.compile(r"重做|重写|重跑|改稿|返工|重交|修改|重出")
        for mi, role in enumerate(members):
            nm = (role.get("name") or "").strip()
            if not nm:
                continue
            for line in advice.splitlines():
                if nm in line and verb.search(line):
                    hits.append(mi)
                    break
        return hits

    def _run_member_rerun(self, wi, members, mi_list, advice, state):
        """只重做被点名的成员，其余成员产出原样保留。

        返回 state（只含被重跑的 tid，{tid: {tid+'_output': text}}）；
        任何一位失败/无产出 → 返回 None，上层退回整波重跑（保守，不丢稿）。
        """
        if not members or not mi_list:
            return None
        wave_no = wi + 1
        _brief = getattr(self, "_briefing", None) or {}
        # v4.134：打回重跑**必须**带上 PM 配的能力 —— 打回指令十有八九就是
        # 「别再用那个工具」，重跑不带禁用等于让它照犯（此前正是如此）。
        _caps = getattr(self, "_capability", None) or {}
        _pool = legion.tool_pool() if _caps else None
        out = {}
        for mi in mi_list:
            if not (0 <= mi < len(members)):
                continue
            role = members[mi]
            _cap = _caps.get(legion.cap_norm_name(role.get("name") or ""))
            role, _, _miss = legion.apply_capability(role, _cap, tool_pool=_pool)
            if _miss:
                self._add_missing(_miss, role.get("name"))
            role_name = self._role_name(role)
            tid = f"w{wi}_m{mi}"
            # v4.138：回炉/打回重跑**同样**带上任务交付契约 + 替代源清单 ——
            # 被退回十有八九就是这两样没落到产出里，重跑不带等于让它照犯。
            _task1 = getattr(self, "task", "")
            prompt = (legion.build_role_prompt(role)
                      + legion.member_discipline_block(wave_no)
                      + legion.deliverable_contract_block(
                          legion.extract_deliverable_contract(_task1, role_name))
                      + legion.fallback_source_block(_task1, role_name)
                      + legion.capability_prompt_block(_cap))
            if _brief.get("交付形态"):
                prompt = ("🔴【本波交付形态 · 交错直接打回】\n"
                          + _brief["交付形态"].strip() + "\n\n") + prompt
            if _brief.get("工作纪律"):
                prompt += "\n【本队工作纪律】" + _brief["工作纪律"]
            _old = (self._wave_member_texts.get(wi) or {}).get(mi, ("", ""))[1]
            instr = (
                f"【按打回指令改稿 · 只重做你自己这一份】\n"
                f"项目经理点名要你（{role_name}）重做，本波其他成员的产出不动。\n\n"
                f"── 你上一版产出 ──\n{(_old or '')[:6000]}\n── 上一版止 ──\n\n"
                f"【打回指令（逐条落实）】\n{advice}\n\n"
                f"要求：\n"
                f"1. 在上面那版基础上改，别从零重写；\n"
                f"2. 打回指出的每一条都要落实，没被点名的部分保留；\n"
                f"3. 🔴 只输出你的成稿正文，禁止解释、禁止客套、禁止替别人写。")
            try:
                agent = AgentNode(tid, prompt, tools=set(role.get("tools") or []),
                                  mw=self.mw, model_cfg={"profile": role.get("model", "")})
                st = {"task": instr, "query": instr,
                      "context": (self._ctx or "")[-2000:]}
                st = self._wrap(agent, tid, board_key=tid,
                                role_name=role_name, wave_no=wave_no)(st)
                nt = (st.get(tid + "_output") or "").strip()
            except Exception as e:
                self._log(f"  ⚠️ {role_name} 改稿异常：{e}")
                return None
            if not nt:
                self._log(f"  ⚠️ {role_name} 改稿无产出 —— 退回整波重跑")
                return None
            out[tid] = {tid + "_output": nt}
            self._log(f"  🎯 {role_name} 单独改稿完成（{len(nt)} 字）")
        return out or None

    # ---- 内部：跑一次项目经理节点（计划 / 验收）----
    def _run_pm(self, pm_role, tid, instruction, ctx="", force=False):
        """跑一个项目经理节点，返回其输出文本。失败返回空串（不拖垮军团）。

        派发前先看 self._aborted：用户已终止就不再烧 PM 的 token（v4.124.3）。
        之前只在外层 for 顶检查 → PM 自己的规划/验收调用没有任何闸门，
        一旦 PM 调用耗时长（例如长上下文、模型慢）就会"成员停了 PM 空转"。

        force=True（v4.124.16）：跳过中止闸门，专供**收尾结项**用 ——
        中止/异常时恰恰最需要 PM 交代"做到哪、缺什么、怎么续"，不能跟着一起停。
        """
        if getattr(self, "_aborted", False) and not force:
            return ""
        try:
            prompt = legion.build_role_prompt(pm_role)
            # v4.128：Thinking 只给「验收 / 规划 / 经停预检」这类重推理环节开，
            # 且只在文本模型是 3.0 时才发 chat_template_kwargs（2.5 不认这个参数，
            # 误发会 400）。默认 2.5 + 默认关闭 → 请求体与升级前完全一致。
            _thinking = self._pm_thinking(tid)
            agent = AgentNode(tid, prompt,
                              tools=set(pm_role.get("tools") or []), mw=self.mw,
                              model_cfg={"profile": pm_role.get("model", ""),
                                         "thinking": _thinking})
            st = {"task": instruction, "query": instruction}
            if ctx:
                st["context"] = ctx
            # v4.125 M-09：PM 节点也上任务板（此前 board_key 缺省 None →
            # 任务板出现 "null" 键，PM 状态对 legion_board 工具不可见）。
            st = self._wrap(agent, tid, board_key=tid,
                            role_name="项目经理", wave_no=0)(st)
            return (st.get(tid + "_output") or "").strip()
        except Exception as e:
            self._log(f"  ⚠️ 项目经理节点执行异常：{e}")
            return ""

    @staticmethod
    def _role_name(role):
        return f"{role.get('emoji', '')}{role.get('name', '角色')}".strip()

    def _pm_thinking(self, tid):
        """v4.128：PM 节点要不要开 Thinking？

        三个条件同时满足才开：① 界面开关 agnes_thinking_enabled 打开 ② 文本模型是
        agnes-3.0-flash（2.5 不认 chat_template_kwargs，误发会 400）③ 环节属于
        验收 / 规划 / 经停预检（重推理）。任一不满足 → 不开。
        """
        try:
            import agnes_text as _at
            cfg = getattr(self.mw, "cfg", {}) or {}
            if not bool(cfg.get("agnes_thinking_enabled", False)):
                return False
            if _at.resolve_text_model(cfg) != _at.AGNES_TEXT_30:
                return False
            _t = str(tid or "")
            return _t.startswith(("pm_gate", "pm_plan", "pm_preflight"))
        except Exception:
            return False

    # ---- v4.124.12 改动④：经停点预检 ----
    # Omnify 的核心机制：高成本波（生图/生视频/写手）启动前先停下来核对前置产出，
    # 避免烧完 token 才发现前置就是空的。预检只提醒不拦停（不新增卡死模式）。
    _HIGH_COST_TOOL_HINTS = ("image_gen", "video_gen")

    @classmethod
    def _is_high_cost_wave(cls, members):
        """本波任一成员挂了生图/生视频类工具 → 高成本波，值得花一次预检。"""
        for r in members or []:
            for t in (r.get("tools") or []):
                if any(h in str(t) for h in cls._HIGH_COST_TOOL_HINTS):
                    return True
        return False

    def _accept_with_warning(self, wave_no, wi, attempt, max_retry, fp, suggest,
                             task, plan_text, parts_by_wave, gate_reports, waves):
        """v4.168.0（审查 #4）：顾问模式重跑耗尽 → **带风险接受**本波。

        原来这里只有「任务板写 rejected + 日志一句『标红放行』+ break」，
        结果出现语义冲突：

            任务板：第 N 波 rejected
            实际流程：后续波已执行
            最终状态：done
            checkpoint：可能没记录这波已被接受

        现在四条线一次对齐：
          ① 任务板写 `accepted_with_warning`（不是 rejected）
          ② 审计账本记一笔 `带风险接受`（by=advisory，写明凭什么）
          ③ checkpoint 用 last_completed_wave=本波 落盘（与实际继续位置一致）
          ④ `_warned_waves` 留给结项报告强制标注
        """
        note = (f"第 {wave_no} 波未通过验收"
                f"（项目经理 {attempt} 次判定均为 FAIL，已达顾问模式重跑上限 {max_retry}）"
                f" —— 按顾问模式**带风险继续**")
        try:
            self._board(f"gate_w{wave_no}", wave=wave_no,
                        status="accepted_with_warning", pm_verdict=suggest)
        except Exception as e:
            log.warning("任务板写入 accepted_with_warning 失败: %s", e)
        try:
            legion.record_auth(
                self.pid, self._pname, wave_no, fp, suggest, "带风险接受",
                by="advisory",
                reason="顾问模式重跑耗尽：PM 无授权权，此处不是授权，是带风险继续",
                run_id=self.run_id)
        except Exception as e:
            log.warning("带风险接受审计写入失败: %s", e)
        try:
            if not isinstance(getattr(self, "_warned_waves", None), dict):
                self._warned_waves = {}
            self._warned_waves[int(wave_no)] = note
        except Exception:
            pass
        # 提交自检闸/成员失败等既有缺口一并留痕，避免报告只写一句"带风险"
        try:
            legion.save_checkpoint(
                self.pid, self.run_id, task, plan_text,
                parts_by_wave, gate_reports,
                wave_member_texts=self._wave_member_texts,
                last_completed_wave=wi,
                waves=waves)
        except Exception as e:
            log.warning("带风险接受的 checkpoint 落盘失败: %s", e)
        self._log(f"  ⚠️ {note}")
        self._log(f"     （已推进到第 {wi + 1} 波；本波风险已写入任务板 + 审计 + 结项报告，"
                  f"可随时在报告里回看）")

    def _make_final_report(self, pm, status, waves, parts_by_wave,
                           gate_reports, task):
        """v4.124.16：收尾结项 —— PM 汇报链的最后一环。

        之前 PM 只汇报开工计划 + 逐波验收，跑完却把 raw 产出往日志区一丢了事，
        调度者的职责清单里根本没有「结项」。这里强制它出一份《结项总结报告》：
        任务回顾 / 各波结论 / 最终成果清单 / 风险与遗留 / 下一步建议。

        中止与异常同样要写（force 跳过中止闸门）—— 那时最需要交代
        「做到哪、缺什么、怎么续」。失败返回 ""（由 save_report 标注降级）。
        """
        if not pm:
            return ""
        try:
            wave_lines = []
            for k in sorted(parts_by_wave or {}):
                t = (parts_by_wave.get(k) or "").strip()
                names = ""
                try:
                    w = (waves or [])[k]
                    ms = w.get("members") if isinstance(w, dict) else w
                    names = "、".join(self._role_name(m) for m in (ms or []))
                except Exception:
                    names = ""
                clip = t[:_FINAL_WAVE_BRIEF] + ("…" if len(t) > _FINAL_WAVE_BRIEF else "")
                wave_lines.append(f"第 {k + 1} 波（{names or '—'}）：{clip or '（无产出）'}")
            gate_lines = []
            for g in (gate_reports or [])[-8:]:
                gs = str(g or "").strip()
                gate_lines.append(gs[:_FINAL_GATE_BRIEF]
                                  + ("…" if len(gs) > _FINAL_GATE_BRIEF else ""))
            instr = legion.final_report_instr(
                task, status=status, n_waves=len(waves or []),
                n_done=len(parts_by_wave or {}),
                wave_brief="\n".join(wave_lines),
                gate_brief="\n".join(gate_lines))
            # v4.127：人工介入项必须出现在结项报告里，不许悄悄带过
            _hn = self._human_needed_block()
            if _hn:
                instr += ("\n\n【系统硬约束 · 必须写进结项总结】\n" + _hn
                          + "\n（这些波次没有被放行，你没有放行权。）")
            if len(instr) > _FINAL_CTX_MAX:
                instr = instr[:_FINAL_CTX_MAX] + "\n…（输入已截断）"
            _sum = (self._run_pm(pm, "pm_final_report", instr, force=True) or "").strip()
            # PM 没写就由系统兜底补上（标注不能被漏掉）
            if _hn and "人工介入" not in _sum:
                _sum = (_sum + "\n\n---\n\n## ⚠️ 人工介入项（系统补录）\n" + _hn).strip()
            # v4.140 P0-2：提交自检闸（数据可信度/交付形态）自身故障的成员，
            # 产出处于「未验证」状态 —— 结项报告显式补录，杜绝静默放行。
            if self._gate_errors:
                _ge = []
                for _w, _errs in sorted((self._gate_errors or {}).items(),
                                        key=lambda x: int(x[0])):
                    _ge.append(f"第{int(_w) + 1}波："
                               + "、".join(rn for rn, _e in _errs)
                               + " 的提交自检闸故障（产出未经验证）")
                _sum = (_sum + "\n\n---\n\n## ⚠️ 提交自检闸异常（系统补录）\n"
                        + "\n".join(_ge)).strip()
            # v4.140 P0-1：本波执行失败（异常）的成员，结项报告也显式列出，
            # 避免「异常被伪装成成功」带进最终结论。
            if self._wave_failed_members:
                _wf = []
                for _w, _rns in sorted((self._wave_failed_members or {}).items(),
                                       key=lambda x: int(x[0])):
                    _wf.append(f"第{int(_w) + 1}波："
                               + "、".join(_rns) + " 执行异常失败（产出缺失）")
                _sum = (_sum + "\n\n---\n\n## ⚠️ 成员执行失败（系统补录）\n"
                        + "\n".join(_wf)).strip()
            # v4.168.0（审查 #2 残余）：跑满轮次无正文的成员，结项报告显式补录。
            # 旧实现会把「只跑了工具、什么都没写」当成功交出去，PM 和报告都看不见。
            if getattr(self, "_wave_incomplete_members", None):
                _wf2 = []
                for _w, _rns in sorted(self._wave_incomplete_members.items(),
                                       key=lambda x: int(x[0])):
                    _wf2.append(f"第{int(_w) + 1}波：" + "、".join(_rns)
                                + " 跑满轮次未产出正文（无有效交付物）")
                _sum = (_sum + "\n\n---\n\n## ⚠️ 成员无产出（incomplete · 系统补录）\n"
                        + "\n".join(_wf2)).strip()
            # v4.168.0（审查 #4）：顾问模式「带风险接受」的波次，结项报告必须点名 ——
            # 否则最终状态是 done，但某几波其实没过验收，事后无从追溯。
            _warned = getattr(self, "_warned_waves", None) or {}
            if _warned:
                _wl = [f"· 第 {int(w)} 波：{_warned[w][:160]}" for w in sorted(_warned)]
                _sum = (_sum + "\n\n---\n\n## ⚠️ 带风险接受（顾问模式 · 系统补录）\n"
                        + "\n".join(_wl)
                        + "\n\n> 这些波次**未通过项目经理验收**，因顾问模式重跑上限已到而"
                          "带风险继续。放行权始终在大哥手里：可在任务板按波次回看，"
                          "重新生成对应产出。").strip()
            return _sum
        except Exception as e:
            log.warning("结项总结生成失败: %s", e)
            return ""

    def _human_needed_block(self):
        """v4.127：本项目「打回后人工介入」项清单（结项报告强制标注）。"""
        items = getattr(self, "_human_needed", None) or []
        if not items:
            return ""
        lines = [f"本项目存在 {len(items)} 次打回后人工介入项："]
        for i, it in enumerate(items, 1):
            lines.append(
                f"  {i}. 第 {it.get('wave')} 波 · 问题类型：{it.get('kind')} · "
                f"同一问题已打回 {it.get('count')} 次 · "
                f"你的处置：{'打回重做' if it.get('decision') == 'reject' else '终止'}"
                f" · 涉及成员：{it.get('members') or '—'}")
        lines.append("这些波次**未通过验收**，结论里必须原样列出，不得写成已完成。")
        return "\n".join(lines)

    def _run_preflight(self, pm, wave_no, members, task, plan_text):
        """进高成本波前的入口预检。返回 (ok, gap)：
        ok=True 通过 / False 拦截（gap=缺口说明）/ None 预检本身没出结论（不拦）。
        """
        names = "、".join(self._role_name(r) for r in members)
        lines = [
            f"【入口预检 · 第 {wave_no} 波经停点】",
            f"下一波是高成本波（成员：{names}，含生图/生视频类工具，跑一次烧很多 token）。",
            f"【总任务】{task}",
        ]
        if plan_text:
            lines.append(f"\n【执行计划】\n{plan_text[:_PLAN_CTX_LIMIT]}")
        _tmem = legion.target_memory_block(getattr(self, "project", None), task=getattr(self, "task", ""))
        if _tmem:
            lines.append("\n" + _tmem)
        lines.append(
            "\n【你的动作】用 legion_list_outputs 看已有哪些前置产出，"
            "legion_get_output 抽查关键几份，判断前置产出够不够本波开工：\n"
            "· 本波需要的前置素材/文案/标的是否都在、是否可读、是不是空壳；\n"
            "· 有没有致命缺口（缺了就该拦，别让高成本波白烧）。\n"
            "只输出两行，不要长篇：\n"
            "预检：通过\n缺口：无\n或\n预检：拦截\n缺口：<一句话说清缺什么，"
            "指到哪一波哪份产出>"
        )
        text = self._run_pm(pm, f"pm_preflight_w{wave_no}", "\n".join(lines))
        m = re.search(r"预\s*检\s*[：:]\s*(通过|拦截)", text or "")
        if not m:
            return None, ""
        if m.group(1) == "通过":
            return True, ""
        gap = "前置产出存在致命缺口"
        g = re.search(r"缺\s*口\s*[：:]\s*(.+)", text or "", re.S)
        if g:
            first_line = g.group(1).strip().splitlines()
            gap = (first_line[0] if first_line else "")[:300] or gap
        return False, gap

    # ---- v4.146：开工前能力审计（任务→能力矩阵→覆盖率→分级）----
    def _run_capability_audit_once(self, waves, task):
        """组队/开工前一次性能力审计：算覆盖率 + 缺口分级，报告进对话流，存 proj。

        不代替大哥决策（宪法第二章）：Critical 缺口只**明确拦截提示**，真开工仍由
        gate_mode=human 的人手把关兜底；这里把「覆盖率多少、缺什么」摆到大哥眼前。
        """
        try:
            _crew = [r for w in (waves or []) for r in w if isinstance(r, dict)]
            if not _crew:
                return
            audit = legion.capability_audit(task, _crew, None, threshold=0.95)
            self._capability_audit = audit
            # 存进项目档案（便于复盘/UI 展示），不覆盖用户数据其它字段
            try:
                # 审计修复 A6：与 UI/其它 helper 同锁，load→改→save 整段原子，
                # 防长 run 期间与 UI 编辑互相覆盖丢 briefing/能力档案。
                with legion._LEGION_IO_LOCK:
                    _d = legion.load_legion()
                    _pid = self.project.get("id") if isinstance(self.project, dict) else None
                    for _p in (_d.get("projects") or []):
                        if isinstance(_p, dict) and _p.get("id") == _pid:
                            _p["capability_audit"] = {
                                "coverage": audit.get("coverage"),
                                "critical": [g["cap"] for g in (audit.get("critical") or [])],
                                "important": [g["cap"] for g in (audit.get("important") or [])],
                                "report_text": audit.get("report_text", ""),
                                "ts": __import__("time").strftime("%Y-%m-%d %H:%M"),
                            }
                            legion.save_legion(_d)
                            break
            except Exception:
                pass
            self._log("")
            for _ln in (audit.get("report_text") or "").splitlines():
                if _ln.strip():
                    self._log(_ln)
            self._log("")
            if audit.get("critical"):
                self._log("⛔ ⚠️ 存在 Critical 能力缺口 —— 建议先补位再开工（说「装 X」/"
                          "「挂 X」即可），或由大哥在每波验收时显式放行。")
        except Exception as e:
            log.warning("开工前能力审计异常(放行不拦): %s", e)

    def _capability_preflight(self, members, task):
        """v4.146（阶段三）：高成本波前的「能力再审计」程序闸门。

        按当前波成员跑 capability_audit：有 Critical 缺口 -> 拦截（不白烧 token）；
        Important -> 通过但报告需人工审核；无缺口 -> 通过。返回 (ok, gap, report_text)。
        """
        try:
            members = [m for m in (members or []) if isinstance(m, dict)]
            if not members:
                return True, "", ""
            audit = legion.capability_audit(task, members, None, threshold=0.95)
            crit = audit.get("critical") or []
            if crit:
                gap = "能力 Critical 缺口：" + "、".join(g["cap"] for g in crit)
                return False, gap, audit.get("report_text", "")
            imp = audit.get("important") or []
            if imp:
                gap = ("⚠️ 能力 Important 缺口（可开工但须人工审核）："
                       + "、".join(g["cap"] for g in imp))
                return True, gap, audit.get("report_text", "")
            return True, "", audit.get("report_text", "")
        except Exception as e:
            log.warning("能力再审计异常(放行不拦): %s", e)
            return True, "", ""

    # ---- v4.134：能力配置报告节（透明化：配了什么 / 程序拦了什么）----
    def _capability_report_text(self):
        """给报告用的《本轮能力配置》文本。PM 没配也要明写，不许留白让人猜。"""
        cap = getattr(self, "_capability", None) or {}
        lines = []
        if cap:
            for nm, c in cap.items():
                if not isinstance(c, dict):
                    continue
                seg = "- **%s**" % nm
                if c.get("tools"):
                    seg += "｜工具：%s" % "、".join(c["tools"])
                if c.get("disable"):
                    seg += "｜禁用：%s" % "、".join(c["disable"])
                if c.get("skills"):
                    seg += "｜追加技能：%s" % "、".join(c["skills"])
                if c.get("note"):
                    seg += "｜口径：%s" % c["note"]
                lines.append(seg)
        else:
            lines.append("（本轮项目经理未指定能力配置——成员按角色卡默认能力执行。"
                         "若反复出现「工具用错/口径跑偏」，说明该让 PM 在这一节配能力。）")
        notes = getattr(self, "_cap_notes", None) or []
        if notes:
            lines.append("")
            lines.append("**程序校验（防越权，只收不放）**：")
            for wave_no, role_name, ns in notes[:30]:
                lines.append("- 第 %s 波 · %s：%s" % (wave_no, role_name, "；".join(ns)))
        return "\n".join(lines)

    # ---- v4.139 P1：缺口 → 跑批内自动搜 GitHub 候选 + 汇报到对话流 ----
    def _search_skill_gaps(self):
        """缺口一出就在跑批内搜好 GitHub 候选，并直接汇报到日志/对话流。

        病根（2026-09-12）：搜索只活在「跑完之后 UI 弹模态框」（`legion_ui._on_done`），
        跑批中途发现缺口没出口，大哥也看不到「PM 到底动没动」。这里前移：搜完写
        `self._skill_candidates` 由 meta 带出去，同时把候选**汇报进日志**。
        只搜一次（幂等），且绝不抛异常拖垮跑批。
        """
        if getattr(self, "_skill_candidates", None) is not None:
            return self._skill_candidates
        gaps = list(self._missing_skills or [])
        if not gaps:
            self._skill_candidates = {"candidates": {}, "errors": {}}
            return self._skill_candidates
        # v4.146：先查能力注册表 —— 已装的技能直接标「可挂载」，不重搜 GitHub
        try:
            gaps = legion.gaps_registry_status(gaps, None)
            self._missing_skills = gaps
            _to_search = [g for g in gaps if not g.get("registry_hit")]
            _hit = [g for g in gaps if g.get("registry_hit")]
            if _hit:
                self._log("  🧠 能力注册表命中 %d 个已装技能（直接挂载，不重搜 GitHub）：%s"
                          % (len(_hit), "、".join(g.get("name", "?") for g in _hit)))
        except Exception:
            _to_search = gaps
        try:
            if _to_search:
                self._log(f"  🔧 技能缺口 {len(_to_search)} 个 → 自动去 GitHub 找候选…")
                res = legion.search_skill_candidates_for_gaps(_to_search[:5], limit=5)
            else:
                res = {"candidates": {}, "errors": {}}
        except Exception as e:      # 断网/异常只降级，不算跑批失败
            res = {"candidates": {}, "errors": {"_": "搜索异常：%s" % e}}
        self._skill_candidates = res
        try:
            _txt = legion.skill_candidates_report_text(gaps, res)
            for _ln in (_txt or "").splitlines():
                if _ln.strip():
                    self._log("  " + _ln)
        except Exception:
            pass
        return res

    # ---- v4.134.2：差技能请示报告节（透明化：缺什么 / 怎么补）----
    def _add_missing(self, names, for_role=""):
        """v4.135：把缺口名字回灌进差技能清单（去重，按 name+for_role）。

        三个来源统一汇入 self._missing_skills —— PM《差技能》请示（开工+验收）、
        apply_capability 的幽灵技能 slug —— 这样无论缺口从哪来，UI 都能统一找候选。
        """
        if not names:
            return
        role = (for_role or "").strip()
        have = self._missing_skills or []
        seen = {(g.get("name"), g.get("for_role")) for g in have}
        for nm in names:
            nm = str(nm or "").strip()
            if not nm:
                continue
            key = (nm, role)
            if key in seen:
                continue
            seen.add(key)
            have.append({"name": nm, "for_role": role, "why": "PM 在《能力配置》追加技能时引用了库里没有的 slug"})
        self._missing_skills = have

    def _missing_skills_report_text(self):
        """给报告用的《差技能请示》文本。PM 没报也要明写，不许留白让人猜。"""
        gaps = getattr(self, "_missing_skills", None) or []
        if not gaps:
            return ("（本轮项目经理未报差技能——技能库够用。若反复出现"
                    "「成员没方法论、产出全靠编」，说明该让 PM 在这一节报缺口。）")
        lines = ["⚠️ 以下技能**技能库里没有**，项目经理请示补充"
                 "（去 GitHub 找 / 自己写一个）：", ""]
        for g in gaps[:20]:
            seg = "- **%s**" % g.get("name", "?")
            if g.get("for_role"):
                seg += "｜给 %s 用" % g["for_role"]
            else:
                seg += "｜⚠️ PM 没写给谁（装完手动选角色挂上）"
            if g.get("why"):
                seg += "｜用途：%s" % g["why"]
            else:
                seg += "｜PM 没写用途"
            lines.append(seg)
        lines.append("")
        lines.append("补法：军团窗口点「🔧 装技能（GitHub）」搜名称 → 装上 → "
                     "**装满会自动弹出角色选择**，挂上后下一波 / 打回重跑立即生效。")
        # v4.135：已挂载技能的执行权限缺口——这是「装了也跑不动」，归排波/换角，
        # 不是缺技能、不靠装 GitHub 解决，但一并摆出来，免得大哥漏看。
        _caps = getattr(self, "_skill_cap_warnings", None) or []
        if _caps:
            lines.append("")
            lines.append("⚠️ **已挂载技能的执行权限不足**（与上面「缺技能」是两回事，"
                         "不靠装 GitHub 解决）：")
            for _c in _caps[:20]:
                lines.append("- %s" % _c)
            lines.append("→ 排波时把这类技能换给原生带执行工具的角色，或改用不需要"
                         "执行脚本的方法论（PM 不得给成员新开执行类工具，那是红线）。")
        return "\n".join(lines)

    # ---- v4.124.4：手工补录数据自动注入 ----
    @staticmethod
    def _load_manual_context(project=None, task=None, glob_pattern="manual_*.json"):
        """扫 `legion_runs/<pattern>` 把每份 JSON 的头 _MANUAL_CTX_PER_FILE_LIMIT 字拼起，
        注入到 PM 提示词（开工计划 + 验收指令），让 PM 第一秒就知道有手工补录数据可读。

        v4.124.14 归属化（治「任务早换了，选品还是指回上一份补录文件」）：
          补录数据原本是**全局扫描**，与项目/任务零绑定，放进去就永久污染之后所有任务。
          现在只有满足以下之一才注入：
            ① 显式挂载到本项目（`project["manual_files"]` 列表里）
            ② 文件名或 JSON 内容里带本项目 id / 项目名
          其余一律跳过，并在返回值里给出 skipped 清单供日志提示。

        返回 (text, file_list, skipped)：
          text      提示词嵌入用的字符串（可能为空）
          file_list 实际注入的文件名清单（用于 UI 日志）
          skipped   因未归属而被忽略的文件名清单
        """
        runs_dir = legion.MANUAL_DIR
        if not os.path.isdir(runs_dir):
            return "", [], []
        try:
            all_files = sorted(glob.glob(os.path.join(runs_dir, glob_pattern)))
        except Exception:
            return "", [], []
        all_files = [f for f in all_files if os.path.isfile(f)]
        if not all_files:
            return "", [], []

        # ---- 归属判定 ----
        attached = set()
        proj_name = ""
        if project:
            for n in (project.get("manual_files") or []):
                if isinstance(n, str) and n.strip():
                    attached.add(os.path.basename(n.strip()))
            proj_name = (project.get("name") or "").strip()
        pid = (project or {}).get("id", "") or ""

        files, skipped = [], []
        for fp in all_files:
            name = os.path.basename(fp)
            if name in attached or (pid and pid in name):
                files.append(fp)
                continue
            # 文件名含项目名 → 归属；否则读一点内容看有没有项目标识
            hit = bool(proj_name) and (proj_name in name)
            if not hit:
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        peek = f.read(4096)
                    hit = bool((pid and pid in peek)
                               or (proj_name and proj_name in peek))
                except Exception:
                    hit = False
            (files if hit else skipped).append(fp)

        if not files:
            return "", [], [os.path.basename(f) for f in skipped]

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
            return "", [], [os.path.basename(f) for f in skipped]
        head = (
            "【手工补录数据 · 已读入，PM 可直接引用】\n"
            f"来源目录：{legion.MANUAL_DIR}（🔴 **该目录不在成员工作区内**）\n"
            f"共 {len(file_list)} 份（按文件名时间序）：{' / '.join(file_list)}\n"
            "规则：① 视为真实数据可直接引用 ② 引用时标注『基于人工补录（{filename}）』\n"
            "③ 若引用某条数据后结论强依赖它，请在判定/写作时声明依据。\n"
            "④ 🔴 相关性闸门：若某份补录的品类/方向与本次任务明显不是一回事，"
            "**必须忽略它，不得据此选品或下结论** —— 补录是拐杖不是方向盘。\n"
            "⑤ 🔴 **v4.147.4 修「让成员读不存在的文件」**：这些补录文件**只存在于上面的"
            "来源目录，不在成员工作区**，成员的 `read_file` **读不到**它们。"
            "因此**严禁**在任务书里写「先用 read_file 读 xxx.json」之类指令"
            "（成员必然报「文件不存在」并卡住整波）。"
            "若你需要成员引用其中数据，**必须把相关内容原文直接写进该成员的任务书**。\n"
            "以下是文件内容（已截断；PM 自己可读全文，成员不可）：\n\n"
        )
        text = head + "\n\n".join(chunks)
        if len(text) > _MANUAL_CTX_TOTAL_LIMIT:
            text = text[:_MANUAL_CTX_TOTAL_LIMIT] + "\n…(总长截断)"
        return text, file_list, [os.path.basename(f) for f in skipped]

    def run(self):
        proj = self.project
        pname = f"{proj.get('emoji', '')}{proj.get('name', '军团')}".strip()
        self._pname = pname      # v4.124.8：供 record_message 审计用
        # v4.129：军团期间产物统一归到军团项目名下（覆盖会话标题）
        try:
            import product_layout
            product_layout.set_context(project=pname)
        except Exception:
            pass
        self.last_meta = {}      # v4.124.15：跑完的元信息（UI 写报告抬头用）
        # v4.134：能力配置的落地记录（报告里透明列出「配了什么 / 拦了什么」）
        self._cap_notes = []
        # 本次 run 的能力配置只在开工计划里填；同一执行器被复用时不许残留上一轮
        # （残留=拿旧任务的禁令管新任务，比不配更糟）。续跑场景由下方 load 捞回。
        self._capability = {}
        # v4.134.2：本次 run 的差技能请示（开工计划 + 逐波验收里报上来的）。
        # 同样不许残留上一轮 —— 否则新任务一开跑就报着旧任务缺的技能。
        self._missing_skills = []
        # v4.146：本次 run 的开工前能力审计结果（Critical 拦截提示，不代批）
        self._capability_audit = None
        # v4.139：缺口 → GitHub 候选（跑批内搜好，随 meta 带出去给 UI 展示）
        self._skill_candidates = None
        # v4.135：已挂载技能的执行权限缺口（crew_skill_gaps）—— 与「缺技能要装」是两回事，
        # 这是「装了也跑不动」，归排波/换角，不进 GitHub 安装流，但进报告统一展示。
        self._skill_cap_warnings = []
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
                # v4.124.11：旧 run_id 必须 ensure（补登记进状态仓）——
                # 直接赋值会导致 record_log/record_output 全哑火、PM 三件套全瞎
                self.run_id = legion.ensure_run(_ckpt["run_id"], pname, task)
                # 同时把存档里前几波的产出回填进状态仓：续跑后 PM/审校要审的是全链路，
                # 看不到续跑之前的波，等于瞎审（第 5 波审校读不到产出就是这个坑）。
                for _k in sorted((_ckpt.get("parts_by_wave") or {}), key=lambda x: int(x)):
                    _wi = int(_k)
                    if _wi < self._resume_from:
                        # v4.124.17 M-02：wave 参数与正常运行保持一致用 **1 基**
                        # （正常路径 record_output 传的是 wave_no = wi+1）。
                        # 此前传 _wi（0 基）→ PM 三件套按 wave 查产出整体错位一位：
                        # 查"第 1 波"拿到的是第 2 波内容，第 1 波内容挂在 wave 0 上查不到。
                        legion.record_output(self.run_id, _wi + 1, 0,
                                             f"第{_wi + 1}波（续跑回填）",
                                             _ckpt["parts_by_wave"][_k])
                # v4.140 P2-3：续跑同时还原**成员级**底稿（谁交了什么），而非只还原
                # 波级合并文本。否则续跑后 PM 审校 / 悬空引用检查拿不到成员粒度，
                # 「第 5 波审校读不到产出」类问题会复发。回填 _wave_member_texts
                # 与 _outputs（最新稿），保证与正常路径语义一致。
                _wmt = _ckpt.get("wave_member_texts") or {}
                for _k2 in sorted(_wmt, key=lambda x: int(x)):
                    _wi2 = int(_k2)
                    # v4.166.0 修复：条件曾写反（`<` 就 continue）—— 结果是**跳过历史波**、
                    # 只回填 resume_from 之后的波；而 checkpoint 里根本没有那些波的数据，
                    # 于是「成员级底稿回填」整段空转，方向与上面的波级回填（parts_by_wave）
                    # 恰好相反，注释承诺的「续跑后 PM 能看到谁交了什么」完全落空。
                    # 正确语义：回填 `_wi2 < resume_from` 的历史波，跳过即将重跑的部分。
                    if _wi2 >= self._resume_from:
                        continue
                    _mmap = _wmt[_k2] or {}
                    self._wave_member_texts.setdefault(_wi2, {})
                    for _mi2, _vv in _mmap.items():
                        try:
                            _mi2 = int(_mi2)
                        except Exception:
                            continue
                        if isinstance(_vv, (list, tuple)) and len(_vv) == 2:
                            _rn2, _tx2 = _vv
                            self._wave_member_texts[_wi2][_mi2] = (_rn2, _tx2)
                            self._outputs_map[(_wi2, _rn2)] = _tx2
                # 由 _outputs_map 重建 _outputs（最新稿，与原逻辑保持一致）
                self._outputs = sorted(
                    ((_w, _rn, _t) for (_w, _rn), _t in self._outputs_map.items()),
                    key=lambda x: (x[0], x[1]))
            else:
                self.run_id = legion.start_run(pname, task)
        else:
            self.run_id = legion.start_run(pname, task)

        # v4.131-F：新的一次执行清空上一次的上报（别把旧任务的上报带进本波验收）
        try:
            legion.clear_issues(self.run_id)
        except Exception:
            pass

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
            if gate_mode == "human":
                # v4.153 P2-07：人手把关模式依赖项目经理作为唯一调度 / 验收台，
                # 找不到 PM 却静默降成 off = 整个授权闸门消失，违反宪法第二章
                # 「授权权归用户、PM 无放行权」的设计前提。这种模式下必须阻断启动。
                self._log("⛔ 已开启「人手把关」验收，但角色库里找不到「项目经理」—— "
                          "该模式必须由 PM 承担调度与验收，无法降级绕过。")
                self._log("   请在角色库里添加「项目经理」角色，或把验收模式改为"
                          "「顾问」/「关闭」后再启动。")
                self._board("run", status="refused", run_id=self.run_id,
                            task=task[:80],
                            reason="人手把关模式缺少项目经理角色")
                self.done.emit(
                    "⛔ 启动被拒绝：当前为「人手把关」验收模式，但角色库里没有「项目经理」。"
                    "\n该模式必须由 PM 承担调度与验收，不能静默降级为无闸门。"
                    "\n请先到角色库添加「项目经理」，或把验收模式切到「顾问」/「关闭」后重试。")
                return
            # off / advisory 模式：PM 只出建议权，缺了不影响执行面，降级并说明
            self._log("⚠️ 已开启波次验收，但角色库里找不到「项目经理」—— "
                      "顾问模式下 PM 仅出建议，本次按原流程跑完（已降级为无验收）。")
            gate_enabled = False
            gate_mode = "off"

        # ---- 军团权限闸门（v4.167.0，审查 #1 P0）----
        # 为什么在这里建：run_id / 项目名 / gate_mode 到这一步才全部确定。
        # 授权策略：
        #   · human    —— 不预授：每波放行时逐波授予（人手把关仍在最前）
        #   · advisory / off —— 用户已放弃逐波把关，预授执行类，
        #     否则军团角色的 run_command / run_python 会全被拒、功能直接废掉
        #     （仍受工作区作用域 + 危险命令底线 + 逐笔审计约束，且外发永远要白名单）
        self._perm = None
        try:
            from legion_permissions import LegionPermissionAdapter
            self._perm = LegionPermissionAdapter(
                run_id=self.run_id or "",
                project_id=self.pid or "",
                project_name=pname or "",
                gate_mode=gate_mode,
            )
            if gate_mode != "human":
                self._perm.grant_all(classes=("exec", "external"),
                                     by=f"gate_mode:{gate_mode}",
                                     reason="非人手把关模式，执行类能力预授（仍受作用域/白名单/审计约束）")
                self._log(f"  🔑 权限闸门：{gate_mode} 模式 → 预授执行类能力（仍记审计，可抽查）")
            else:
                self._log("  🔑 权限闸门：人手把关模式 → 成员默认只读，每波放行时才授予危险能力")
        except Exception as _pe:
            self._log(f"  ⚠️ 权限闸门初始化失败：{_pe}（成员工具调用将按保守策略拒绝）")
            self._perm = None


        # 任务板：本次执行开始
        self._board("run", status="running", run_id=self.run_id, task=task[:80])

        # ---- v4.124.5：断点续传装载 ----
        # 如果 __init__ 传了 resume_from → 跳过计划调用 + 直接装入 parts_by_wave
        # _ckpt 已经在 run() 头部加载过一次（用于复用 run_id），这里复用不重读盘
        ckpt_data = _ckpt if (self._resume_from is not None and _ckpt) else None
        if ckpt_data:
            self._log(f"⏵ 续跑请求：跳过前 {self._resume_from} 波（从第 {self._resume_from + 1} 波启动）")
            # v4.153 P1-02：阵容 / 任务不一致 → fail-closed 拒绝续跑。
            # 续跑本质是"接着旧存档的进度跑"；若阵容或任务已变，旧存档的波次索引 /
            # 产出对应关系全错位，静默续跑会产出张冠李戴的结果且极难察觉。
            cur_fp = legion._waves_fingerprint(waves)
            fp_mismatch = cur_fp != ckpt_data.get("waves_fingerprint")
            task_mismatch = (ckpt_data.get("task") or "").strip() != task.strip()
            if fp_mismatch or task_mismatch:
                reasons = []
                if fp_mismatch:
                    reasons.append("阵容与存档不一致（成员 / 工具 / 模型 有改动）")
                if task_mismatch:
                    reasons.append("任务文本与存档不一致")
                self._log("⛔ 续跑被拒绝：" + "；".join(reasons) + "。")
                self._log("   旧存档的波次索引 / 产出对应关系已不可信，静默续跑会张冠李戴。")
                self._log("   请「从头重跑」本任务，或恢复与存档一致的阵容 / 任务后再续跑。")
                self._board("run", status="refused", run_id=self.run_id,
                            task=task[:80], reason="；".join(reasons))
                self.done.emit(
                    "⛔ 续跑已拒绝：阵容或任务与存档不一致（见上方日志）。\n"
                    "为避免张冠李戴的错乱产出，本次未执行任何波次。\n"
                    "请「从头重跑」本任务，或恢复与存档一致的阵容 / 任务后再点续跑。")
                return
            self._log("✅ 阵容与任务均与存档一致 —— 允许续跑")
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
        # v4.146（阶段二/三）：开工前能力审计 —— 任务→能力矩阵→覆盖率→分级，
        # 报告进对话流，Critical 缺口明确拦截提示（宪法第二章：授权权在大哥，不自动放行）；
        # 真开工仍由 gate_mode=human 的人手把关兜底。
        self._run_capability_audit_once(waves, task)

        # ---- 开工计划：PM 拆任务 / 排波次 / 定验收标准（纯建议，不改执行图）----
        plan_text = ""
        # v4.124.4：注入手工补录数据 —— "工具拿不到就用人工" 的接地路径
        # v4.124.14：只注入**归属本项目**的补录（全局扫描会让上一份数据污染新任务）
        # ⚠️ 必须在这里（波次循环之外、711 行旧的重置之前）加载一次并复用：
        # 原实现在下面的 if 块里加载，随后波次段 `manual_ctx = ""` 又把它清空，
        # 结果 PM **验收**指令里从来没拿到过补录数据（只有开工计划拿到了）。
        manual_ctx, manual_files, _skipped = self._load_manual_context(
            project=self.project, task=task)
        if gate_enabled and self._resume_from is None:
            if manual_files:
                self._log(f"📥 已读入手工补录：{' / '.join(manual_files)}（{len(manual_ctx)} 字带进 PM 提示）")
            if _skipped:
                self._log(f"🚫 已忽略 {len(_skipped)} 份未归属本项目的补录文件"
                          f"（{' / '.join(_skipped[:3])}{'…' if len(_skipped) > 3 else ''}）"
                          " —— 防止上一份数据把新任务带跑偏；要用请点「📎 补录数据」挂载")
            # v4.134：把「班子能力现状」摆到 PM 眼前 —— 不给现状，它就只能假设
            # 成员什么都会，第一波必然按角色卡默认能力裸跑（选品波就是这么废的）。
            _crew_all = [r for w in waves for r in w]
            _crew_blk = legion.crew_capability_block(_crew_all)
            # v4.134.3：技能要的执行权限，成员到底有没有 ——
            # 「技能挂了却像没挂」的根因是正文要 run_command 而成员根本没有，
            # 成员只能干瞪眼（实测：选品官挂多模型圆桌，第一波产出全是网页原文）。
            _skgaps = []
            try:
                _skgaps = legion.crew_skill_gaps(_crew_all)
            except Exception:
                _skgaps = []
            for _g in _skgaps:
                self._log("⚠️ " + _g)
            self._skill_cap_warnings = list(_skgaps)
            if _skgaps:
                self._log("   → 技能正文已进成员 prompt，但缺执行工具＝跑不动："
                          "要么换原生带该工具的角色，要么别挂这个技能")
            plan_instr = (
                f"【任务】{task}\n\n"
                f"【军团编制】共 {len(waves)} 波：\n" +
                "\n".join(f"  第 {wi+1} 波：" + "、".join(self._role_name(r) for r in w)
                          for wi, w in enumerate(waves)) + "\n\n" +
                ("\n" + manual_ctx + "\n" if manual_ctx else "") +
                ("\n" + _crew_blk + "\n" if _crew_blk else "") +
                ("\n【⚠️ 技能执行权限不足（程序预检）】\n" + "\n".join(_skgaps)
                 + "\n（这些技能只能挂给原生带 run_command / run_python 的角色；"
                   "PM **不得**给成员新开执行类工具——那是红线。排波时请避开，"
                   "或改用不需要执行脚本的方法论。）\n" if _skgaps else "") +
                "【你要做的】以调度器身份出一份《执行计划》，包含：\n"
                "0. 🔴 **先澄清需求（必须先做，v4.148.1 对标成熟团队范式）**：编计划前先\n"
                "   检查任务描述——细分领域 / 平台或站点 / 目标人群 / 数据源 / 交付形态 /\n"
                "   预算约束，这五项口径是否明确。\n"
                "   · 口径已明确（或大哥写死了）：在计划**开头**用一小节【需求澄清】列出\n"
                "     你依据的关键假设（每条一句），让大哥一眼看到你按什么口径在跑；\n"
                "   · 关键口径缺失到无法编出可执行计划（如「做电商选品」却不知站点/地区/类目）：\n"
                "     **整份计划只输出一节【澄清问题】**——2~4 个问题，每个给出你推荐的默认\n"
                "     选项；程序会暂停军团并把问题转给老板，答案回来你再重编计划。\n"
                "     此时**不要**输出波次安排 —— 带着猜测编计划 = 全队白跑。\n"
                "1. 任务拆解：这个目标要拆成哪几件事，谁做\n"
                "2. 波次编排：先跑哪波、哪波可并行、哪波必须等上波验收\n"
                "3. 每波验收标准：可判定的硬指标（有/没有、对/错），不要写「质量好」这种虚的\n"
                "4. 每波的**交付物形态**：写清楚这一波要交的**是个什么东西**\n"
                "   （例：写手＝1 条 hook + ≥3 条 USP 卖点文案；主图策划＝N 张分镜、\n"
                "   每张含构图 + 卖点文案；竞品分析师＝对比表 + 差异点结论）——\n"
                "   形态写死，验收时才能一眼看出「交的不是这个东西」\n"
                "5. 🔴 标的锁定（本任务若涉及「选品 / 选题 / 选方向」）：必须写明\n"
                "   —— 选品（选题）波次结束时须收敛出**唯一一个**标的，\n"
                "   并写明「后续所有波次一律以该标的为准，不得再换成别的候选」。\n"
                "   若选品波次只给了一堆候选、没锁定唯一标的，你验收时必须判定 FAIL：\n"
                "   标的不锁死，后面每一波都会各跑各的（实测 5 个波换过 3 个方向，全白跑）。\n"
                "6. 本队三条工作纪律：针对这个任务、这个班子，最多 3 条、每条一句话\n"
                "   ——成员第一遍就该守的规矩（如「禁编造数据，引用必带来源」「每波\n"
                "   只交约定形态的东西」）。🔴 本节开头必须带标记【工作纪律】。\n"
                "7. 波间交接物：每一波跑完给下一波留什么（如「选品波交唯一标的+\n"
                "   一句话理由」「脚本波交分镜表」），下一波直接拿来用不用重问。\n"
                "   🔴 本节开头必须带标记【交接物】。\n"
                "8. **能力说明（v4.148.0 起职责变更）**——成员的工具与技能由**角色库**\n"
                "   统一配置，你**不再**输出【能力配置】节（禁用/限定工具/追加技能均已\n"
                "   停用，写了也不生效）。你的职责收窄为：拉队伍、分任务、做总结。\n"
                "   数据口径（如「数字必须带来源+日期，无源一律删」）直接写进各成员的\n"
                "   任务要求里，成员会逐字看到。\n"
                "   若本波确实缺角色或缺技能，走第 9 节【差技能】上报，由老板来建新角色\n"
                "   / 装技能 —— 不要自行发挥。\n"
                "9. 🔴 **差技能请示**——技能库里没有、但本任务确实需要的方法论，\n"
                "   写出来让老板去 GitHub 找回来装上。本节开头必须带标记【差技能】，\n"
                "   一行一个，格式：`- 技能名：给谁用；干什么用`\n"
                "   · 🔴 **技能名必须是具体能力名**（如「TikTok Shop 马来站合规清单校验」），\n"
                "     严禁拿「新增 / 补充 / 需要」这类动词当技能名 —— 程序会当垃圾丢掉；\n"
                "   · 🔴 **必须写「给谁用」**（本波在编角色名）—— 说不清给谁用、干什么用\n"
                "     的缺口**不要报**（报了也装不出去）：先想清楚是哪个成员干这件事缺方法，\n"
                "     再报；真想不清楚就写「无」，宁缺勿滥；\n"
                "   · 只写**确实缺的**：上面《可用技能清单》里已有的一律不许写\n"
                "     （写了会被程序丢掉），也不许自己编 slug；\n"
                "   · 确实不缺就写「无」——不要省略本节。\n"
                "另外：第 4 节（每波交付物形态）开头补一个标记【交付形态】。\n"
                "这三段标记（工作纪律/交接物/交付形态）会被程序抽走存档，\n"
                "作为本队的组织记忆在下次同类任务时复用——务必按标记写。\n"
                "注意：你只出计划，不执行。不需要调用工具读产出（还没开始跑）。"
            )
            # v4.125 ②：注入历史 briefing（组织记忆）——上次同类任务的
            # 纪律/交接物/形态直接给 PM 抄，不用每次重新发明规矩
            try:
                _brief_mem = legion.team_briefing_for(self.legion_data, task)
            except Exception:
                _brief_mem = ""
            if _brief_mem:
                plan_instr = plan_instr + "\n\n" + _brief_mem
            # v4.134：配能力要有依据 —— 注入真实工具池 + 可用技能清单。
            # 此前技能清单只在**组队**阶段给过 PM，开工阶段手里啥也没有，
            # 「追加技能」只能凭空编 slug；工具更是连有哪些都不知道。
            try:
                _skill_blk = legion.skill_menu()
            except Exception:
                _skill_blk = ""
            if _skill_blk:
                plan_instr = (plan_instr
                              + "\n\n## 可用技能清单（追加技能只能从这里取 slug）\n"
                              + _skill_blk)
            try:
                _tool_blk = legion.tool_menu()
            except Exception:
                _tool_blk = ""
            if _tool_blk:
                plan_instr = (plan_instr
                              + "\n\n## 可用工具池（配工具只能从这里取名字）\n"
                              + _tool_blk)
            # v4.136（P0-①）：角色职责 → 应得能力建议清单。让 PM 一眼看到
            # 每个角色按职责「该有什么工具/技能、当前缺什么」，只微调不重猜。
            try:
                _reco_blk = legion.capability_recommend_block(_crew_all)
            except Exception:
                _reco_blk = ""
            if _reco_blk:
                plan_instr = (plan_instr
                              + "\n\n## 本班子建议能力（程序按职责自动推导，参考用）\n"
                              + _reco_blk)
            # 上次同类班子怎么配的能力（组织记忆）——别每次重新发明
            try:
                _cap_mem = legion.team_capability_for(self.legion_data, task)
            except Exception:
                _cap_mem = ""
            if _cap_mem:
                plan_instr = plan_instr + "\n\n" + _cap_mem
            # v4.125 ④：注入资产库清单——PM 要知道货在哪叫什么，
            # 同类资产的图/视频/剧本直接复用别重造（防「技能在库里 PM 不知道」翻版）
            try:
                import asset_store
                _asset_mem = asset_store.asset_catalog(task)
            except Exception:
                _asset_mem = ""
            if _asset_mem:
                plan_instr = plan_instr + "\n\n" + _asset_mem
            # v4.124.13：PM 开工前也要带记忆 —— 标的记忆 + 本项目教训本 + 跨项目教训。
            # 否则它会重编一份和上次被打回的一模一样的计划（每次都是新兵营）。
            _pm_mem = "\n\n".join(
                b for b in (legion.target_memory_block(self.project, task=getattr(self, "task", "")),
                            legion.project_lessons_block(self.pid),
                            legion.global_lessons_block(pm)) if b)
            if _pm_mem:
                plan_instr = plan_instr + "\n\n" + _pm_mem
            self._log("📋 项目经理：正在编制执行计划…")
            plan_text = self._run_pm(pm, "pm_plan", plan_instr)
            if plan_text:
                self._log(f"📋 执行计划已出（{len(plan_text)} 字）\n")
                # v4.147.8：留一份计划全文 —— 交付形态契约要从中解析（见 _submit_gate
                # 的 _contract_src）。此前计划只在局部变量里，形态闸拿不到它。
                self._plan_text = plan_text
                # v4.125 ②：解析 briefing 三段存档（队长可训练的落盘动作）。
                # 解析失败不拦流程——PM 没按格式写就当这次没练成。
                try:
                    _brief = legion.parse_briefing(plan_text)
                    if _brief:
                        self._briefing = _brief   # 成员派发也用（纪律+交接物注 prompt）
                        _rid = (proj or {}).get("recipe_id") or ""
                        # 审计修复 E5：save_team_briefing 返回 (ok, recipe_id) 二元组，
                        # 非空元组真值恒为 True → 失败也会弹"已存档"。显式解包判断。
                        _bok, _brid = legion.save_team_briefing(_rid, task, _brief)
                        if _bok:
                            self._log("📋 本队规矩已存档（工作纪律/交接物/交付形态）"
                                      "——下次同类任务直接复用")
                except Exception as _be:
                    self._log(f"  ⚠️ briefing 存档跳过：{_be}")
                # v4.148.0（架构定调，大哥拍板）：**PM 不再配能力** —— PM 的职责收窄为
                # 「拉队伍、分任务、做总结」。四轮实测证明 PM 配能力弊大于利：
                #   · 禁《浏览器自动化》技能的理由静态分析就错了（run_command 系统会自动补）；
                #   · 「限定工具」漏列 browser → browser 全没，且**存档复用**把缺口带进
                #     后续每一波（TK 马来团浏览器从未被拉起的根因之一）；
                #   · 禁 web_search 割裂了正当手段。
                # 成员能力自此**只由角色库决定**（角色卡 + 职责标签自动垫 + 技能 requires_tools
                # 回填，apply_capability 收到空 cap 自动走该路径）；缺角色 / 缺技能走
                # 【差技能】上报，由老板来建新角色 / 装技能 —— 人来把关，不是 PM。
                self._capability = {}
                self._log("⚙️ 成员能力由角色库统一配置（v4.148.0：PM 不再配能力，"
                          "缺角色/缺技能走【差技能】上报，由老板来建）")
                # v4.134.2：解析【差技能】请示 —— 开工后才发现缺口也得有出口。
                # 此前 PM 只能把「缺 X 技能」写进打回指令，老板看不到、更没处补；
                # 能装技能的两个入口（组队弹窗 / 主窗口）里，只有弹窗能挂给角色。
                _roster = [r.get("name") for w in (waves or []) for r in w
                           if isinstance(r, dict) and r.get("name")]
                try:
                    _gaps = legion.parse_missing_skills_text(plan_text, valid_roles=_roster)
                except Exception as _ge:
                    _gaps = []
                    self._log(f"  ⚠️ 差技能请示解析跳过：{_ge}")
                self._missing_skills = _gaps
                if _gaps:
                    self._log("🔧 项目经理请示补 %d 个技能：%s"
                              % (len(_gaps), "；".join(
                                  "%s（%s）" % (
                                      g["name"],
                                      g.get("for_role")
                                      or "⚠️ PM 没写给谁，装完自选角色")
                                  for g in _gaps)))
                    self._log("   → 在军团窗口点「🔧 装技能（GitHub）」装上，"
                              "装完直接挂给角色，下一波 / 重跑立即生效")
                else:
                    self._log("🔧 项目经理未报差技能（技能库够用）")
            else:
                self._log("⚠️ 执行计划未产出（不影响执行，继续跑）\n")

        # ---- 逐波执行 ----
        state = {"task": task, "query": task}
        parts_by_wave = {}      # wave_idx -> 归并文本（重跑时整体替换，避免残留旧稿）
        gate_reports = []       # 每波的验收结论文本，附在最终结果末尾
        self._ctx = ""
        # v4.125 ②：续跑场景计划不重新生成——从磁盘把上次的 briefing 捞回来，
        # 成员派发继续带纪律/交接物（没有就当没练过，不拦流程）
        if not getattr(self, "_briefing", None):
            try:
                self._briefing = legion.load_team_briefing(self.legion_data, task) or {}
            except Exception:
                self._briefing = {}
        # v4.148.0（PM 不再配能力）：**不再从存档载回**能力配置 —— 旧存档里可能
        # 躺着「限定工具漏列 browser」这类坏配置，载回等于把历史错误注入新波
        # （TK 马来团浏览器从未被拉起的根因之一）。能力自此只由角色库决定。
        self._capability = {}
        # v4.124.14：manual_ctx / manual_files 已在开工处加载（归属化后的一次性结果），
        # 这里**不再重置为空** —— 重置会让 PM 验收指令永远拿不到补录数据。
        # aborted 已升格为 self._aborted（v4.124.3）：在 try 内初始化，
        # 让 _run_pm / _run_wave 入口能查 → 派发前识别已终止
        user_rejects = 0        # 用户亲手打回次数（防无限重跑）
        # v4.124.7：追踪「最后一个验收 PASS 的波」——终止/异常落盘时用它，
        # 不能用 len(parts_by_wave)（那是有产出的波数，FAIL 的波也会被算进去，
        # 导致续跑跳过该 FAIL 波、带着次品进入下一波）。
        last_passed_wave = -1
        # v4.127 Bug-1：同一问题打回 ≥2 次后升级「人工介入」的登记簿。
        # 结项总结必须显式列出这些项，不许悄悄放行。
        self._human_needed = []
        # v4.168.0（审查 #4）：顾问模式「带风险接受」的波次登记簿（波号 → 风险说明）。
        # 结项报告必须显式补录 —— 最终状态是 done，但某几波其实没过验收，不能无从追溯。
        self._warned_waves = {}

        # v4.124.5：续跑注入（skip 计划 → 直接装入 parts_by_wave / plan_text / gate_reports）
        if self._resume_from is not None and ckpt_data:
            parts_by_wave = dict(ckpt_data.get("parts_by_wave") or {})
            plan_text = ckpt_data.get("plan_text") or ""
            gate_reports = list(ckpt_data.get("gate_reports") or [])
            self._ctx = "\n\n".join(parts_by_wave[k] for k in sorted(parts_by_wave))
            # v4.124.8 修复：续跑时前 resume_from 波都已 PASS，须把 last_passed_wave
            # 同步为 resume_from-1；否则续跑途中再次中断/异常时，落盘会写成 -1，
            # 下次续跑 resume_from=0 → 从头重跑，白跑已完成的波。
            last_passed_wave = (self._resume_from or 1) - 1
            self._log(f"📦 续跑装入：plan_text {len(plan_text)} 字 / "
                      f"{len(parts_by_wave)} 波产出 / {len(gate_reports)} 条验收记录\n")
            # v4.168.0（审查 #4）：续跑时把「带风险接受」的波次从审计账本重建回来。
            # 不重建的话，续跑跑出来的结项报告会漏掉上一段生命里带过的风险 ——
            # 而审计账本是 append-only + 哈希链的，正好是这个事实的权威来源。
            try:
                for _r in (legion.read_auth(400) or []):
                    if (_r.get("decision") == "带风险接受"
                            and str(_r.get("project_id")) == str(self.pid)
                            and (not _r.get("run_id") or _r.get("run_id") == self.run_id)):
                        _w = int(_r.get("wave") or 0)
                        if _w:
                            self._warned_waves[_w] = (
                                f"第 {_w} 波未通过验收（{_r.get('time', '')} "
                                f"由顾问模式重跑耗尽后带风险继续，"
                                f"PM 判定：{_r.get('pm_verdict', '')}）")
                if self._warned_waves:
                    self._log(f"⚠️ 续跑已恢复 {len(self._warned_waves)} 个"
                              f"「带风险接受」波次记录：" 
                              + "、".join(f"第{w}波" for w in sorted(self._warned_waves)))
            except Exception as _e:
                log.warning("续跑恢复带风险波次失败: %s", _e)

        # v4.148.1（对标 Omnify TeamWork「澄清需求必须先做」）：PM 若判定关键口径
        # 缺失，计划输出只有【澄清问题】一节 —— 程序在此拦停，问题透给大哥；
        # 答案补进任务描述后重跑，PM 带着答案重编计划。禁止带着模糊口径空跑全队。
        if (self._resume_from is None and plan_text
                and "【澄清问题】" in plan_text and "【工作纪律】" not in plan_text):
            _clarify_q = plan_text.split("【澄清问题】", 1)[1].strip()[:1200]
            self._log("⏸ 项目经理有需求要澄清，军团已暂停 —— 等大哥回答后再重跑：")
            self._log(_clarify_q)
            self._log("\n→ 把答案写进任务描述后点重跑，PM 会带着答案重新编计划。")
            try:
                legion.end_run(self.run_id, "clarify")
                self._board("run", status="clarify", run_id=self.run_id)
            except Exception:
                pass
            self.last_meta = {
                "status": "clarify",
                "n_waves": len(waves), "n_done": 0,
                "gate_reports": [], "run_id": self.run_id, "task": task,
                "summary": ("⏸ 需求澄清中：项目经理提出了关键口径问题，待大哥回答。\n\n"
                            + _clarify_q),
                "capability_text": self._capability_report_text(),
                "capability_audit_text": (self._capability_audit.get("report_text", "")
                                          if getattr(self, "_capability_audit", None) else ""),
                "missing_skills_text": self._missing_skills_report_text(),
                "skill_gaps": [], "skill_candidates": [],
                "wave_failed_members": {}, "gate_errors": {},
            }
            self.done.emit(
                "⏸ 项目经理需要你先澄清需求，军团已暂停。\n\n" + _clarify_q
                + "\n\n（把答案写进任务描述后重跑即可，PM 会带着答案重新编计划。）")
            return

        try:
            # v4.140 P2-1：_aborted 已在 __init__ 初始化为 False，这里**不再重置**
            # （否则会在 abort() 之后被覆盖，导致「点了停止却没停」的竞态窗口）。
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

                    # 上下文：首跑带累积历史；重跑走瘦身版（v4.125）
                    wave_state_in = dict(state)
                    wave_state_in["context"] = self._ctx
                    if advice:
                        # v4.125 重跑瘦身：原来把 self._ctx（开工以来**全量**上下文）
                        # 原样再烧一遍 —— 长任务第 5 波打回一次就把前 4 波全重读，
                        # token 翻倍还把模型注意力泡在旧稿里。
                        # 改为只喂三样：被打回那版旧稿 + 关键前置（上下文尾部）
                        # + 打回指令。改稿比重写便宜，方向不跑偏。
                        _prev = parts_by_wave.get(wi) or ""
                        _ctx_tail = (self._ctx or "")[-2500:]
                        retry_ctx = (
                            (f"【关键前置（上下文摘要尾部）】\n{_ctx_tail}\n\n" if _ctx_tail else "")
                            + f"【被打回的上一版产出（在它基础上改，禁止从零重写）】\n"
                              f"{_prev[:6000]}\n\n"
                              f"【项目经理打回指令 · 必须照做】\n{advice}")
                        if attempt:
                            retry_ctx += (
                                "\n\n【改稿要求】上一版产出就在上面。打回指令指出的"
                                "每一条都要落实；没被点名的部分尽量保留。")
                        wave_state_in["context"] = retry_ctx

                    # v4.124.12 改动④：经停点预检 —— 高成本波（生图/生视频）启动前，
                    # 先花一次便宜调用核对前置产出够不够开工（Omnify 经停点机制）。
                    # 只提醒不拦停：查出缺口注入成员上下文让其补救/声明，不新增卡死模式。
                    if (attempt == 0 and wave_no >= 2
                            and self._is_high_cost_wave(members)
                            and not getattr(self, "_aborted", False)):
                        self._log("  🚧 经停点预检：核对前置产出…")
                        ok, gap = self._run_preflight(
                            pm, wave_no, members, task, plan_text)
                        if ok is True:
                            self._log("  🚧 经停点预检：通过")
                        elif ok is False:
                            self._log(f"  🚧 经停点预检：⚠️ 拦截 —— {gap}")
                            wave_state_in["context"] = (
                                (wave_state_in.get("context") or "")
                                + f"\n\n【入口预检警告 · 项目经理】本波启动前预检发现"
                                  f"前置缺口：{gap}\n无法获得的部分明说「无法获取+缺什么」"
                                  "并交能交的部分，禁止编造凑数。")
                            gate_reports.append(
                                f"## 第 {wave_no} 波经停点预检 · ⚠️ 拦截\n\n缺口：{gap}")
                        else:
                            self._log("  🚧 经停点预检未出结论（不拦，继续）")
                        # v4.146：能力再审计（程序闸门）—— Critical 缺口直接拦截高成本波，
                        # 不白烧 token；Important 通过但提示人工审核。
                        _cok, _cgap, _crep = self._capability_preflight(members, task)
                        if _cok is False:
                            self._log(f"  🧠 能力审计：⛔ 拦截 —— {_cgap}")
                            wave_state_in["context"] = (
                                (wave_state_in.get("context") or "")
                                + f"\n\n【能力审计 · 拦截 · 项目经理】本波启动前能力审计发现"
                                  f"Critical 缺口：{_cgap}\n无法获得的能力明说「无法获取+缺什么」，"
                                  "禁止编造凑数；请先补位（装/挂技能）再开工。")
                            gate_reports.append(
                                f"## 第 {wave_no} 波能力审计 · ⛔ 拦截\n\n缺口：{_cgap}")
                        else:
                            self._log("  🧠 能力审计：通过"
                                      + (f"（{_cgap}）" if _cgap else ""))

                    # v4.125 ③：子任务单项改稿模式 —— PM 打回时点了【仅重跑子项 N】，
                    # 本轮只重生成那一个子项（改子不动父与兄弟），其余产出原样保留。
                    _sub_rerun = getattr(self, "_subtask_rerun", None)
                    _is_sub_rerun = bool(_sub_rerun is not None and _sub_rerun[0] == wi)
                    _is_member_rerun = False      # v4.126.1 ② 定点改稿标记
                    if _is_sub_rerun:
                        self._subtask_rerun = None
                        self._log(f"  🧩 子项改稿模式：仅重做第 {_sub_rerun[1]+1} 个子项"
                                  "（其余子项与兄弟产出不动）")
                        wave_state = self._run_subtask_rerun(
                            wi, members, _sub_rerun[1], advice, state)
                        if wave_state is None:
                            # 子项越界/失败 → 退回整波重跑（子任务清空重建）
                            self._wave_subtasks.pop(wi, None)
                            wave_state = self._run_wave(wi, members, wave_state_in)
                    else:
                        # v4.126.1 ②：定点改稿 —— 打回指令只点名了部分成员时，
                        # 只重做被点名的那几位，其余成员产出原样保留。
                        # 前提：① 是重跑（attempt>0）② 确实点名了 ③ 没点全
                        # ④ 上一版各成员产出都在（否则没法保留）。
                        _is_member_rerun = False
                        _named = []
                        if attempt and not getattr(self, "_aborted", False):
                            _named = self._parse_named_members(advice, members)
                            if (_named and len(_named) < len(members)
                                    and len(self._wave_member_texts.get(wi) or {}) >= len(members) - len(_named)):
                                _is_member_rerun = True
                        if _is_member_rerun:
                            _names = "、".join(self._role_name(members[m]) for m in _named)
                            self._log(f"  🎯 定点改稿：只重做 {_names}"
                                      f"（其余 {len(members)-len(_named)} 位成员产出保留）")
                            wave_state = self._run_member_rerun(
                                wi, members, _named, advice, state)
                            if wave_state is None:
                                wave_state = self._run_wave(wi, members, wave_state_in)
                                _is_member_rerun = False
                        else:
                            wave_state = self._run_wave(wi, members, wave_state_in)

                    # v4.126.1 ②：定点改稿后 —— 被重跑的成员用新稿，其余从底稿回填，
                    # 拼出完整 wave_state 交给下面的通用收集逻辑（不改动收集代码）。
                    if _is_member_rerun:
                        _prev = dict(self._wave_member_texts.get(wi) or {})
                        for _mi in _named:
                            _tid = f"w{wi}_m{_mi}"
                            _no = wave_state.get(_tid, {}) or {}
                            _nt = (_no.get(f"{_tid}_output") or "").strip() if isinstance(_no, dict) else ""
                            if _nt:
                                _prev[_mi] = (self._role_name(members[_mi]), _nt)
                        wave_state = {f"w{wi}_m{_mi}": {f"w{wi}_m{_mi}_output": _t}
                                      for _mi, (_rn, _t) in _prev.items()}

                    # 收集本波产出（从各自节点取，避免并行 context 覆盖丢稿）
                    texts = self._collect_wave_texts(wi, members, wave_state,
                                                     wave_no, attempt)
                    # ---- v4.138 P0：交付闸门前移 ----
                    # 成员产出后**立即**机器自检（数据可信度 + 交付形态），不合格的
                    # 成员当场回炉并重新收集；通过才进下面的 PM 评审。
                    texts, _gate_note = self._submit_gate(
                        wi, members, wave_state, texts, wave_no, attempt, state)
                    # ---- v4.139 P0：缺口兜底检测（不靠 PM 自觉）----
                    # 病根：缺口只有「PM 写【差技能】节」和「能力配置引用幽灵 slug」两个
                    # 来源，两条都靠 PM 自觉 —— 2026-09-12 实战 PM 那节压根没写，
                    # 于是 _missing_skills 为空、UI 的 if _sk: 什么都不发生，看着像
                    # 「PM 一动没动」。这里从成员产出里机器扫缺口信号兜住。
                    try:
                        _det = legion.detect_gaps_from_outputs(texts)
                        if _det:
                            _have = self._missing_skills or []
                            _seen = {(x.get("name"), x.get("for_role")) for x in _have}
                            _added = 0
                            _hint = 0
                            for _g in _det:
                                _k = (_g.get("name"), _g.get("for_role"))
                                if _k in _seen:
                                    continue
                                _seen.add(_k)
                                # v4.140 P1-3：只有高置信（>=0.7）缺口才自动进
                                # 「去 GitHub 搜候选」闭环，避免误报烧 token；
                                # 低置信只提示，需大哥人工确认。
                                if (_g.get("confidence") or 0) >= 0.7:
                                    _have.append(_g)
                                    _added += 1
                                else:
                                    _hint += 1
                            self._missing_skills = _have
                            if _added:
                                self._log(f"  🔎 机器检测到 {_added} 处高置信技能缺口"
                                          "（PM 未报也兜住，自动进候选）")
                            if _hint:
                                self._log(f"  💡 另有 {_hint} 处低置信疑似缺口"
                                          "（仅提示，不自动搜 GitHub，需人工确认）")
                    except Exception:
                        pass

                    if texts:
                        if _is_sub_rerun:
                            # v4.125 ③：子项改稿后按子任务重组（保留未打回的兄弟子项）
                            _subs = self._wave_subtasks.get(wi) or []
                            parts_by_wave[wi] = (
                                f"## 第 {wave_no} 波（子项改稿后重组）\n\n"
                                + legion.merge_subtasks(_subs))
                            self._log(f"  🧩 子项改稿完成，本波产出已重组"
                                      f"（{len(_subs)} 个子项）")
                        else:
                            # v4.140 P0-1：若本波有成员执行失败，显式标注缺失，
                            # 杜绝「异常被伪装成成功」—— 报告/下一波上下文都能看见。
                            _fail_note = ""
                            _failed = self._wave_failed_members.get(wi) or []
                            if _failed:
                                _fail_note = ("\n\n> ⚠️ 本波以下成员执行异常失败（未产出）："
                                              + "、".join(_failed)
                                              + " —— 该部分交付缺失，非「已交付空稿」。")
                            parts_by_wave[wi] = ("\n\n".join(
                                f"## 第 {wave_no} 波 · {rn}\n\n{t}" for rn, t in texts)
                                + (("\n\n> " + _gate_note) if _gate_note else "")
                                + _fail_note)
                            # 累积上下文供下一波使用（本波全部成员，一个不漏；
                            # 失败成员不填上下文，但缺失事实已写进 parts_by_wave 显式可见）
                            self._ctx = (self._ctx + "\n\n" + "\n\n".join(
                                f"【{rn}】{t}" for rn, t in texts)).strip()
                        # v4.125 ③：波产出切子任务（写手按平台拆 / 配图师按张拆）。
                        # 切得出 ≥2 段才记录 —— PM 验收时可见子项清单，
                        # 打回时可用【仅重跑子项 N】只重生成那一个。
                        if not _is_sub_rerun:
                            try:
                                _subs = legion.split_subtasks(
                                    "\n\n".join(t for _rn, t in texts))
                                if _subs:
                                    self._wave_subtasks[wi] = _subs
                                    self._log(f"  🧩 本波产出已拆成 {len(_subs)} 个子项"
                                              "（打回时可【仅重跑子项 N】单项改稿）")
                            except Exception:
                                pass
                    else:
                        # v4.140 P0-1：本波无产出，若伴随成员执行失败，明确区分
                        # 「该波本就空跑」与「成员异常崩了」—— 后者是故障而非正常空稿。
                        _failed = self._wave_failed_members.get(wi) or []
                        if _failed:
                            self._log(f"  ⚠️ 第 {wave_no} 波无产出（成员执行失败："
                                      f"{'、'.join(_failed)}）—— 故障，非正常空稿")
                        else:
                            self._log(f"  ⚠️ 第 {wave_no} 波没有任何产出")

                    # v4.124.17 N-03：off 模式（无验收闸）波跑完有产出即算完成。
                    # 此前 last_passed_wave 只在 gate PASS 分支更新 → off 模式它恒为 -1，
                    # checkpoint 落 last_completed_wave=-1 → 续跑 resume_from=0，
                    # **全部波次从头重跑**（重复生图 / 重复扣费，v4.124.5 per-wave
                    # checkpoint 被完全架空）。游标必须与"实际完成进度"挂钩，
                    # 不能兼任"验收是否放行"的判据。
                    if not gate_enabled and texts:
                        last_passed_wave = wi

                    # v4.124.5：每波完成时落 checkpoint → 崩了可续跑
                    # v4.124.9 修复：last_completed_wave 必须用 last_passed_wave（最后 PASS 的波），
                    # 不能用 wi（当前波索引）——本波刚跑完还没验收，wi 会把 FAIL/未验收的波
                    # 也记成"完成"，续跑就跳过它、带次品进下一波（用户第 3 波 FAIL 就是这个坑）。
                    legion.save_checkpoint(
                        self.pid, self.run_id, task, plan_text,
                        parts_by_wave, gate_reports,
                        wave_member_texts=self._wave_member_texts,
                        last_completed_wave=last_passed_wave,
                        waves=waves)

                    # v4.124.8：急件检查点 —— 波跑完、产出落盘后立即交给 PM 就地响应
                    # （不打断并行成员：一波是原子单元，杀成员会出半成品）
                    if gate_enabled and self._check_pm_inbox(pm, wave_no, urgent_only=True):
                        break

                    if not gate_enabled:
                        break

                    # ---- 波次验收：PM 只出**建议**，放行与否由用户授权（宪法第二章）----
                    self._log(f"📋 项目经理：验收第 {wave_no} 波…")
                    # v4.131-F：本波有上报 → 先喊一嗓子，再塞进验收指令
                    try:
                        _iss_n = len(legion.wave_issues(self.run_id, wave=wave_no))
                    except Exception:
                        _iss_n = 0
                    if _iss_n:
                        self._log(f"  🚨 本波有 {_iss_n} 条成员上报"
                                  f"（已写进验收指令，项目经理必须逐条回应）")
                    gate_instr = self._build_gate_instruction(
                        task, wave_no, attempt, members, texts, plan_text,
                        manual_ctx=manual_ctx)
                    verdict_text = self._run_pm(pm, f"pm_gate_w{wave_no}_{attempt}", gate_instr)
                    # v4.134.2：验收里 PM 也可能发现缺技能（往往正是打回的真原因），
                    # 一并收进请示清单 —— 按名字去重，同一技能不报两遍。
                    # v4.134.3：同样过名册（验收指令里带【差技能】模板，PM 抄模板
                    # 会把「技能名」「判定」「自检」原样写回来，必须挡掉）。
                    try:
                        _v_roster = [r.get("name") for r in (members or [])
                                     if isinstance(r, dict) and r.get("name")]
                        for _g in legion.parse_missing_skills_text(
                                verdict_text, valid_roles=_v_roster):
                            _gnm = _g.get("name")
                            if _gnm and not any(x.get("name") == _gnm
                                                for x in (self._missing_skills or [])):
                                self._missing_skills.append(_g)
                                self._log("🔧 项目经理验收时报差技能：%s（%s）—— "
                                          "可在军团窗口点「🔧 装技能」补"
                                          % (_gnm, _g.get("for_role") or "未指定角色"))
                    except Exception:
                        pass
                    # v4.124.11：空产出必须打回 —— 本波成员一个字都没产出时，
                    # 即便 PM 写了「判定：PASS」也不能放行（PM 可能没读到产出就给了结论）。
                    v = legion.parse_verdict(verdict_text, has_output=bool(texts))
                    # v4.124.12 改动②：判定行补投 —— PM 明明给了建议却没写标准判定行
                    # （截图实锤的高频病，弱模型不守格式），过去直接走「宁可漏检」兜底放行。
                    # 现在花一次极便宜的追问让 PM 只补一行判定，把不确定性消掉再走兜底。
                    if ((not v.get("empty_output")) and (not v.get("parsed"))
                            and verdict_text
                            and not getattr(self, "_aborted", False)):
                        self._log("  🔄 未解析到标准判定行 —— 让项目经理补投一行…")
                        fix_text = self._run_pm(
                            pm, f"pm_verdict_fix_w{wave_no}_{attempt}",
                            "你上一份验收报告结尾缺少标准判定行。\n"
                            "只回一行，不要任何其他内容：判定：PASS  或  判定：FAIL")
                        v2 = legion.parse_verdict(fix_text, has_output=bool(texts))
                        if v2["parsed"] and not v2.get("empty_output"):
                            v = v2
                            verdict_text += ("\n\n【补投判定行】"
                                             + (fix_text or "").strip())
                            self._log("  ✓ 补投成功，按补投的判定处理")
                        else:
                            self._log("  ⚠️ 补投仍无标准判定行 —— 维持兜底"
                                      "（宁可漏检，不可卡死）")
                    # ---- v4.131-A：改法补投 ----
                    # 漏洞：判定行没写会补投，改法没写却不补投 —— 不对称。
                    # 实测病案：PM 只写「判定：FAIL」加一句结论，成员收到的就是
                    # 「（项目经理未给出具体指令，请按质量标准自行复查重做）」，
                    # 重跑纯撞大运；大哥在弹窗里也只看到「建议打回」看不到为什么。
                    if (not v.get("pass")) and not (v.get("advice") or "").strip() \
                            and not getattr(self, "_aborted", False):
                        self._log("  🔄 判定 FAIL 但没给具体改法 —— 让项目经理补一份…")
                        _fix_adv = self._run_pm(
                            pm, f"pm_advice_fix_w{wave_no}_{attempt}",
                            "你上一份验收报告判定 FAIL，但**没有给出具体改法** —— "
                            "成员拿到手只能瞎猜。\n"
                            "只回下面两段，不要任何其他内容：\n"
                            "【问题清单】\n"
                            "1. …（逐条写清哪里不对，尽量引用产出里的原句）\n"
                            "【打回指令】\n"
                            "1. …（逐条写清成员照做什么，可操作、可检查）")
                        _adv2 = legion.extract_advice(_fix_adv)
                        if _adv2:
                            v = dict(v)
                            v["advice"] = _adv2[:2500]
                            verdict_text = ((verdict_text or "").rstrip()
                                            + "\n\n【补投改法】\n"
                                            + (_fix_adv or "").strip())
                            self._log(f"  ✓ 补投成功（{len(_adv2)} 字）—— 成员按此改")
                        else:
                            self._log("  ⚠️ 补投仍无具体改法 —— 维持通用兜底指令")
                    # ---- v4.127 Bug-1：问题指纹 + 打回计数（屡教不改检测）----
                    # v4.131-D：分类先算一次（认不出时沿用本波上次认得出的分类），
                    # 再传进指纹 —— 否则措辞一变指纹就漂，打回计数永远归零。
                    _ifp_kind = legion.stable_issue_kind(
                        self.pid, wave_no, members, verdict_text)
                    _ifp = legion.issue_fingerprint(wave_no, members, verdict_text,
                                                    project_id=self.pid,
                                                    kind=_ifp_kind)
                    _rc_before = legion.reject_count(self.pid, _ifp)
                    if _rc_before:
                        self._log(f"  🔁 同一问题指纹（{_ifp_kind}）已打回 {_rc_before} 次")

                    # ---- v4.127 Bug-2：跨产物引用核验（悬空引用 = 硬伤）----
                    # 成员正文里「沿用第 2 波已写好的《XX》成稿」—— 那个成稿压根不存在。
                    # PM 前两次只抓形态没抓引用，第 3 次 PASS 时幻觉引用还在正文里。
                    _dangling = []
                    if texts:
                        # v4.140 P1-2：悬空引用核对必须基于「当前最新稿」。
                        # 原实现读 _outputs —— 但回炉稿（record=False）不进 _outputs，
                        # 导致：① 旧首稿有引用→误判悬空；② 回炉新增引用→漏判。
                        # 现统一从 _wave_member_texts（始终是最新稿）构建核对清单，
                        # 跨波引用核对也用最新稿，杜绝首稿/回炉稿不一致带来的误判。
                        _outs = []
                        for _w, _mmap in (getattr(self, "_wave_member_texts", None) or {}).items():
                            for _mi, (_rn, _tx) in _mmap.items():
                                if _tx:
                                    _outs.append({"wave": int(_w) + 1, "role": _rn, "text": _tx})
                        for _rn, _tx in texts:
                            for d in legion.verify_cited_outputs(_tx, _outs):
                                d["role"] = _rn
                                _dangling.append(d)
                    if _dangling:
                        _blk = legion.dangling_citation_block(_dangling)
                        self._log(f"  🔴 发现 {len(_dangling)} 处悬空引用"
                                  f"（引用了不存在的产物）—— 强制打回，不看内容质量")
                        v = dict(v)
                        v["pass"] = False
                        v["advice"] = ((v.get("advice") or "") + "\n\n" + _blk).strip()[:2500]
                        verdict_text = ((verdict_text or "")
                                        + "\n\n【悬空引用核验 · 系统硬检查】\n" + _blk)

                    # ---- v4.131：数据可信度硬检（脏数据不许流到下游）----
                    # 实测血案：研究员交 4006 字网页原始抓取堆砌、竞品分析师交 881 字
                    # 无关搜索结果堆砌，PM 只查「形态对不对」给了 PASS，脏数据一路
                    # 流到写手，第 3 波经停点才爆雷 —— 前两波 token 全白烧。
                    self._last_dq_block = ""     # 清掉上一波残留，别串到本波弹窗
                    _dq_items = []
                    for _rn, _tx in (texts or []):
                        try:
                            _a = legion.audit_data_quality(_tx, role_name=_rn, wave=wave_no)
                        except Exception:
                            _a = None
                        if _a and _a.get("verdict") in ("bad", "warn"):
                            _dq_items.append((_rn, _a))
                    _dq_bad = [x for x in _dq_items if x[1].get("verdict") == "bad"]
                    if _dq_bad:
                        _blk = legion.data_quality_block(_dq_items)
                        self._last_dq_block = _blk      # 授权弹窗要给人看原文
                        self._log(f"  🔴 数据可信度硬检不通过（{len(_dq_bad)} 份产出）"
                                  f" —— 强制打回，与项目经理的判定无关")
                        v = dict(v)
                        v["pass"] = False
                        v["advice"] = ((v.get("advice") or "") + "\n\n" + _blk).strip()[:3000]
                        verdict_text = ((verdict_text or "")
                                        + "\n\n【数据可信度硬检 · 系统】\n" + _blk)
                    elif _dq_items:
                        _blk = legion.data_quality_block(_dq_items)
                        self._last_dq_block = _blk
                        verdict_text = ((verdict_text or "")
                                        + "\n\n【数据可信度提示 · 系统】\n" + _blk)
                        self._log("  ⚠️ 数据可信度提示："
                                  + "；".join(
                                      f"{r}·{(a.get('reasons') or [''])[0][:26]}"
                                      for r, a in _dq_items))

                    suggest = "PASS（建议放行）" if v["pass"] else "FAIL（建议打回）"
                    gate_reports.append(
                        f"## 第 {wave_no} 波验收（第 {attempt} 次）· {suggest}\n\n{verdict_text}")

                    if v.get("empty_output"):
                        self._log("  🔴 本波无任何产出 —— 空产出是硬伤，按『建议打回』处理"
                                  "（放行＝把空气传给下一波）")
                    self._log(f"  📋 项目经理建议：{'PASS ✅（放行）' if v['pass'] else 'FAIL ❌（打回）'}")

                    fp = legion.fingerprint(wave_no, members, task)
                    decision = None
                    # v4.127 Bug-1：同一问题打回 ≥2 次，PM 本次仍判 PASS →
                    # **禁止放行**，升级「人工介入」交给大哥拍板（PM 无授权权，
                    # 闸门不能被放水冲垮）。人工介入态不自动结项。
                    _escalated = bool(v["pass"] and _rc_before >= legion.REJECT_ESCALATE_AT)
                    # 本次放行是否**人手拍板**过（决定要不要累积信任围栏计数）
                    _by_hand = False

                    if _escalated:
                        _who = "、".join(self._role_name(r) for r in members)[:120]
                        _detail = (
                            f"同一问题（{_ifp_kind}）已连续打回 {_rc_before} 次，"
                            f"本次（第 {_rc_before + 1} 次交付）项目经理仍建议放行。\n\n"
                            f"闸门规则：**不允许直接 PASS**，交给你拍板。\n\n"
                            f"本波成员：{_who}\n\n"
                            f"项目经理本次验收原文：\n{(verdict_text or '')[:1200]}")
                        self._board(f"gate_w{wave_no}", wave=wave_no,
                                    status="await_approval", pm_verdict="HUMAN_NEEDED")
                        self._log("  🚨 升级人工介入：等你拍板（放行 / 打回 / 终止）")
                        decision = self._request_auth(
                            f"第 {wave_no} 波 · ⚠️ 同一问题已打回 {_rc_before} 次，需你拍板",
                            _detail)
                        if decision is None:
                            legion.record_auth(self.pid, pname, wave_no, fp, suggest,
                                               "未授权", by="timeout",
                                               reason="人工介入等待超时，按不授权处理",
                                               run_id=self.run_id)
                            self._board(f"gate_w{wave_no}", wave=wave_no, status="timeout")
                            self._log("  ⏱ 等待拍板超时 —— 按**不授权**处理，停止推进")
                            self._aborted = True
                            break
                        legion.record_auth(
                            self.pid, pname, wave_no, fp, "HUMAN_NEEDED",
                            {"pass": "放行", "reject": "打回", "abort": "终止"}.get(decision, decision),
                            by="user",
                            reason=f"人工介入拍板（同一问题已打回 {_rc_before} 次）",
                            run_id=self.run_id)
                        _by_hand = True      # 大哥亲手拍板 → 可累积人手信任
                        if decision == "pass":
                            legion.clear_rejects(self.pid, _ifp)
                            self._log("  ✅ 你亲手拍板放行 —— 该问题指纹计数已清零")
                        else:
                            self._human_needed.append({
                                "wave": wave_no, "kind": _ifp_kind,
                                "count": _rc_before, "fp": _ifp,
                                "decision": decision,
                                "members": _who,
                            })
                    elif gate_mode == "human":
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
                                f"第 {wave_no} 波 · 项目经理建议：{suggest}",
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
                            _note = self._apply_auth_note(decision, members)
                            legion.record_auth(
                                self.pid, pname, wave_no, fp, suggest,
                                {"pass": "放行", "reject": "打回", "abort": "终止"}.get(decision, decision),
                                by="user",
                                reason="用户亲手批准" + (f"；{_note}" if _note else ""),
                                run_id=self.run_id)
                            _by_hand = True
                    else:
                        # 顾问模式：按 PM 建议执行（用户已在此项目里选择信任建议）
                        decision = "pass" if v["pass"] else "reject"
                        legion.record_auth(
                            self.pid, pname, wave_no, fp, suggest,
                            "放行" if decision == "pass" else "打回",
                            by="advisory",
                            reason="顾问模式：按 PM 建议执行（PM 无授权权，此处不是授权）",
                            run_id=self.run_id)
                        # v4.124.17 M-07：顾问模式**不累积人手信任**。
                        # grant_trust 的 count 会被 auto_pass_allowed 当作「同类人工批准次数」
                        # 用于 human 模式的自动放行围栏 —— 若顾问模式也 +1，等于把
                        # 「PM 自己建议放行」攒成「大哥亲手批准过 N 次」，切回 human 模式
                        # 并开 auto_after 时围栏被架空，违背宪法第二章（放行权必须在大哥手里）。

                    if decision == "pass":
                        # v4.167.0（审查 #1，P0）：放行本波 = 同时授予本波成员危险能力。
                        # 授权不再只挂在"能不能进下一波"上 —— 成员真要调
                        # run_command / run_python / 外发时还要过 LegionPermissionAdapter
                        # 那道闸门（见 agent_node.py 与 legion_permissions.py）。
                        try:
                            _p = self._ensure_perm()
                            if _p is not None:
                                _p.grant_wave(wave_no, classes=("exec", "external"),
                                              by="user" if _by_hand else "gate",
                                              reason=f"第 {wave_no} 波放行")
                        except Exception as _ge:
                            self._log(f"  ⚠️ 波次权限授权记录失败：{_ge}")

                        # v4.127：只有**人手拍板**过的放行才累积信任（宪法第二章）。
                        # 顾问模式 / 围栏自动放行都不算，否则围栏会被自己攒的次数架空。
                        if _by_hand:
                            legion.grant_trust(self.pid, fp, wave_no)
                        self._board(f"gate_w{wave_no}", wave=wave_no,
                                    status="approved", pm_verdict=suggest)
                        self._log("  ✅ 已授权放行，进入下一波")
                        last_passed_wave = wi
                        # v4.124.13：本波通过 → 回填教训本，把之前的打回标记为已解决（闭环）
                        if legion.resolve_lesson(
                                self.pid, wave_no,
                                f"第 {wave_no} 波通过（重跑 {attempt} 次）"):
                            self._log("  📗 教训本：本波历史打回已标记 ✅已解决")
                        # v4.124.9：PASS 后立即落盘，持久化「最后 PASS 波」。
                        # 否则 610 行用 last_passed_wave（上一波）落盘，本波 PASS 后、
                        # 下一波启动前崩溃，会退回到重跑本波（丢了刚 PASS 的进度）。
                        legion.save_checkpoint(
                            self.pid, self.run_id, task, plan_text,
                            parts_by_wave, gate_reports,
                            wave_member_texts=self._wave_member_texts,
                            last_completed_wave=last_passed_wave,
                            waves=waves)
                        # v4.124.8：备忘检查点 —— 波界（本波 PASS 后、下一波启动前）
                        # 把队列里的备忘（含急件残留）交给 PM；若用户选「停下重编」则 break
                        if self._check_pm_inbox(pm, wave_no, urgent_only=False):
                            break
                        break
                    if decision == "abort":
                        self._board(f"gate_w{wave_no}", wave=wave_no,
                                    status="aborted", pm_verdict=suggest)
                        self._log("  ⛔ 你终止了本次军团执行")
                        self._aborted = True
                        break

                    # ---- decision == "reject"：打回重跑 ----
                    # v4.168.0（审查 #4）：顾问模式重跑耗尽，不再写「rejected 但继续」这种
                    # 语义混搭 —— 改成显式终态 `accepted_with_warning`，同时推进
                    # last_passed_wave / checkpoint / 最终报告，三者与实际执行位置统一。
                    #   改前：任务板 rejected，流程却继续跑后面的波，最终还标 done，
                    #         checkpoint 也没记这波已被接受 —— 事后复盘根本看不懂。
                    if gate_mode == "advisory" and attempt >= max_retry:
                        self._accept_with_warning(
                            wave_no, wi, attempt, max_retry, fp, suggest,
                            task, plan_text, parts_by_wave, gate_reports, waves)
                        last_passed_wave = wi
                        break
                    self._board(f"gate_w{wave_no}", wave=wave_no,
                                status="rejected", pm_verdict=suggest)
                    if user_rejects >= max(3, max_retry + 2):
                        self._log(
                            f"  ⛔ 已连续打回 {user_rejects} 次（硬上限）—— "
                            f"停下来等你决定，不再自动重跑")
                        self._aborted = True
                        break
                    user_rejects += 1
                    attempt += 1
                    _base_advice = (v.get("advice")
                                    or "（项目经理未给出具体指令，请按质量标准自行复查重做）")
                    # v4.131-E：大哥手写的改稿要求优先级最高 —— 拼在最前面，
                    # 免得被 PM 的通用套话稀释掉（打回才生效，放行时忽略）。
                    _mine = (getattr(self, "_auth_revision", "") or "").strip()
                    if len(_mine) > 1200:      # 防手一抖写成长篇大论，把成员上下文撑爆
                        _mine = _mine[:1200] + "\n（…你的改稿要求过长，已截断到 1200 字）"
                    if _mine:
                        _base_advice = (
                            f"【我的改稿要求 · 大哥亲手写的，优先级最高】\n{_mine}\n\n"
                            f"【项目经理指令】\n{_base_advice}")
                        self._log(f"  ✍️ 已并入你的改稿要求（{len(_mine)} 字）—— "
                                  f"排在最前，优先于项目经理指令")
                    # v4.127 Bug-1 配套缓解：第 2 次打回起改为**模板填空式**指令 ——
                    # 直接给交付骨架让成员逐段填，压掉「理解偏了」的空间。
                    advice = legion.reject_instruction(_base_advice, _rc_before, verdict_text)
                    # 同一问题打回计数 +1（指纹 = 波次 + 角色 + 问题分类）
                    _n_now = legion.bump_reject(self.pid, _ifp, wave_no=wave_no,
                                                kind=_ifp_kind, note=_base_advice[:180])
                    if _rc_before >= 1:
                        self._log(f"  📐 第 {_rc_before + 1} 次打回 —— 指令已改为"
                                  f"模板填空式（给骨架，逐段填）")
                    self._log(f"  ↩︎ 打回重跑（同问题累计 {_n_now} 次）：{advice[:120]}")
                    # v4.124.13 中期记忆：打回即入册「失败原因 → 修正方式」（结果待通过时回填）。
                    # 每次 FAIL 的批注 = 下一轮的免费教材，重复失败从浪费变成训练数据。
                    _reason = (verdict_text or "").split("【补投判定行】")[0].strip()[:200]
                    _roles = "、".join(self._role_name(r) for r in members)[:120]
                    if legion.add_lesson(self.pid, wave_no, _roles, _reason, advice):
                        self._log("  📕 教训本：本波打回已入册（重跑/后续波自动提醒勿再犯）")

                    # v4.125 ③：PM 点名【仅重跑子项 N】且本波有子任务结构 →
                    # 下一轮只单项改稿（改子不动父与兄弟）；否则整波重跑
                    # （改父 → 子任务清空重建）。
                    _sub_idx = legion.parse_subtask_rerun(verdict_text)
                    _subs_now = self._wave_subtasks.get(wi) or []
                    if _sub_idx is not None and len(_subs_now) >= 2 \
                            and 0 <= _sub_idx < len(_subs_now):
                        self._subtask_rerun = (wi, _sub_idx)
                        self._log(f"  🧩 项目经理点名仅重跑子项 {_sub_idx + 1}"
                                  f"（共 {len(_subs_now)} 个）—— 单项改稿，兄弟产出保留")
                    else:
                        # 整波重跑：子任务清空（重建语义）
                        self._wave_subtasks.pop(wi, None)
                        # 重跑前清掉本波旧稿，避免残留
                        legion.record_output(self.run_id, wave_no, attempt,
                                             "（本波打回重做，旧产出已作废）", "")

        except Exception as e:
            self._log(f"\n✗ 军团执行失败：{e}")
            # v4.124.5：异常落盘 —— 已完成的波次保留，下次可续跑
            # v4.124.7：last_completed_wave 用 last_passed_wave（最后 PASS 的波），
            # 与终止落盘口径一致（-1 会丢掉已完成波次、被迫从头跑）。
            try:
                legion.save_checkpoint(
                    self.pid, self.run_id, task, plan_text,
                    parts_by_wave, gate_reports,
                    wave_member_texts=self._wave_member_texts,
                    last_completed_wave=last_passed_wave,
                    waves=waves)
            except Exception:
                pass
            legion.end_run(self.run_id, "failed")
            out = "\n\n---\n\n".join(parts_by_wave[k] for k in sorted(parts_by_wave))
            # v4.124.16：异常也要结项 —— 这时最需要 PM 交代「做到哪、缺什么、怎么续」
            _sum = ""
            try:
                self._log("📋 项目经理：正在写结项总结（执行异常）…")
                _sum = self._make_final_report(
                    pm, "failed", waves, parts_by_wave, gate_reports, task)
            except Exception:
                _sum = ""
            # v4.124.15：异常也要交元信息 —— 半成品同样要落盘，不然白跑的部分全丢
            self.last_meta = {
                "status": "failed",
                "n_waves": len(waves),
                "n_done": len(parts_by_wave),
                "gate_reports": list(gate_reports),
                "run_id": self.run_id,
                "task": task,
                "summary": _sum,
                "capability_text": self._capability_report_text(),
                # v4.146：开工前能力审计结论（覆盖率/缺口），报告抬头展示
                "capability_audit_text": (self._capability_audit.get("report_text", "")
                                         if getattr(self, "_capability_audit", None) else ""),
                # v4.134.2：差技能请示一并交出去（UI 落盘报告时用）
                "missing_skills_text": self._missing_skills_report_text(),
                # v4.135：结构化缺口清单（名字+给谁用+干什么用）—— UI 据此自动找候选
                "skill_gaps": list(self._missing_skills or []),
                # v4.139：跑批内已搜好的 GitHub 候选（stars/描述），UI 直接展示
                "skill_candidates": self._search_skill_gaps(),
            }
            # v4.125 M-09：落盘下沉到 worker 线程内 —— 报告落盘原在 UI 槽里，
            # 窗口运行中关闭则槽永不执行（v4.124.15 修过的「报告没给我」在
            # 关窗场景复发）。UI 活着时直接复用该路径挂交付物，不再重复写。
            try:
                self.last_meta["report_path"] = legion.save_report(
                    (self.project.get("name") or "军团"),
                    task, out or "", run_id=self.run_id,
                    n_waves=len(waves), n_done=len(parts_by_wave),
                    status="failed", gate_reports=gate_reports, summary=_sum,
                    capability=self._capability_report_text(),
                    missing_skills=self._missing_skills_report_text())
            except Exception:
                self.last_meta["report_path"] = ""
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
            # v4.124.7：last_completed_wave 改用 last_passed_wave（最后 PASS 的波），
            # 而非 n_out-1（有产出的波数）；否则 FAIL 的波会被跳过、次品带进下一波。
            legion.save_checkpoint(
                self.pid, self.run_id, task, plan_text,
                parts_by_wave, gate_reports,
                wave_member_texts=self._wave_member_texts,
                last_completed_wave=last_passed_wave,
                waves=waves)
            self._log(f"💾 已存 checkpoint：下次点项目「⏵ 续跑」可从第 {last_passed_wave + 2} 波继续")
        else:
            self._log(f"\n✓ 军团执行完毕（{n_out}/{len(waves)} 个波次有产出）。")
            # v4.124.5：执行完毕也清 checkpoint（不占磁盘 / 不让用户以为能续跑已完成项目）
            legion.clear_checkpoint(self.pid)
            legion.end_run(self.run_id, "done")
            self._board("run", status="done", run_id=self.run_id)
        # ---- v4.124.16：结项总结（PM 汇报链最后一环）----
        # 开工汇报计划、每波汇报验收，跑完也必须汇报结果 —— 这是调度者的职责，
        # 不是可选项。中止/异常同样要写（_make_final_report 内 force 跳过中止闸门）。
        summary = ""
        if pm is not None:
            _st = "aborted" if self._aborted else "done"
            if self._aborted:
                # v4.140 P2-4：用户已主动停止，不再烧 PM token 出结项长报告
                # （尊重「停止」意图，也避免无谓扣费）。仅给一句系统级交代，
                # 详细产出见上方各波记录，可点「⏵ 续跑」从断点继续。
                self._log("📋 已停止：跳过项目经理结项长报告（不额外烧 token）")
                summary = (
                    f"⏹ 本任务已由用户在第 {last_passed_wave + 1} 波后主动停止。\n"
                    f"已完成 {last_passed_wave} 个波次，详细产出见上方各波记录。\n"
                    f"可在军团窗口点「⏵ 续跑」从第 {last_passed_wave + 2} 波继续。")
            else:
                self._log("📋 项目经理：正在写结项总结报告…")
                summary = self._make_final_report(
                    pm, _st, waves, parts_by_wave, gate_reports, task)
                if summary:
                    self._log(f"📝 结项总结已出（{len(summary)} 字）")
                else:
                    self._log("⚠️ 项目经理未产出结项总结（报告降级为产出拼接）")
        else:
            self._log("⚠️ 未启用项目经理，无结项总结（报告为各波产出拼接）")

        # v4.124.15：跑完把元信息一并交出去，UI 才能写出一份「带抬头」的报告文件
        self.last_meta = {
            "status": "aborted" if self._aborted else "done",
            "n_waves": len(waves),
            "n_done": n_out,
            "gate_reports": list(gate_reports),
            "run_id": self.run_id,
            "task": task,
            "summary": summary,
            "capability_text": self._capability_report_text(),
            # v4.146：开工前能力审计结论（覆盖率/缺口），报告抬头展示
            "capability_audit_text": (self._capability_audit.get("report_text", "")
                                     if getattr(self, "_capability_audit", None) else ""),
            # v4.134.2：差技能请示一并交出去（UI 落盘报告时用）
            "missing_skills_text": self._missing_skills_report_text(),
            # v4.135：结构化缺口清单 —— UI 据此自动找候选
            "skill_gaps": list(self._missing_skills or []),
            # v4.139：跑批内已搜好的 GitHub 候选（stars/描述），UI 直接展示
            "skill_candidates": self._search_skill_gaps(),
            # v4.140 P0-1：本波执行失败（异常）的成员，显式交出去（假成功堵死后可见）
            "wave_failed_members": {str(k): v
                                    for k, v in (self._wave_failed_members or {}).items()},
            # v4.140 P0-2：提交自检闸故障（产出未验证），结项报告需提示
            "gate_errors": {str(k): v
                            for k, v in (self._gate_errors or {}).items()},
        }
        # v4.125 M-09：落盘下沉到 worker 线程内（关窗也不丢报告），UI 复用该路径。
        try:
            self.last_meta["report_path"] = legion.save_report(
                (self.project.get("name") or "军团"),
                task, output or "", run_id=self.run_id,
                n_waves=len(waves), n_done=n_out,
                status=self.last_meta["status"],
                gate_reports=gate_reports, summary=summary,
                capability=self._capability_report_text(),
                missing_skills=self._missing_skills_report_text())
        except Exception:
            self.last_meta["report_path"] = ""
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
            _tag = "（系统追问后补投）" if "【补投改法】" in (verdict_text or "") else ""
            lines.append("")
            lines.append(f"PM 理由 / 打回指令{_tag}：\n{adv[:900]}")
        else:
            lines.append("")
            lines.append("⚠️ 项目经理没给出具体改法（只有一句结论）—— 打回的话"
                         "成员只会收到通用兜底指令。建议先看下面的报告原文，"
                         "再在「我的改稿要求」里写一句你要的改法。")
        # v4.131-C：报告原文 —— 「依据」「问题清单」都在这里，
        # 过去只喂一句「建议打回」，大哥根本看不到为什么。
        _raw = (verdict_text or "")
        for _mark in ("【补投判定行】", "【补投改法】"):
            _raw = _raw.split(_mark)[0]
        _raw = _raw.strip()
        if _raw:
            lines.append("")
            lines.append(f"📄 项目经理报告原文：\n{_raw[:1600]}")
        # v4.131：数据可信度硬检结论 —— 大哥要能看见「为什么这波数据是脏的」，
        # 而不是只看到一句「建议打回」。
        _dq = getattr(self, "_last_dq_block", "") or ""
        if _dq:
            lines.append("")
            lines.append(f"🔍 数据可信度（系统硬检，与 PM 判定无关）：\n{_dq[:900]}")
        # v4.131-F：成员上报 —— 大哥要看得见「谁在质疑上游什么」
        try:
            _iss = legion.issues_block(self.run_id, wave=wave_no, limit=8)
        except Exception:
            _iss = ""
        if _iss:
            lines.append("")
            lines.append(_iss)
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
        # v4.134.2：打回若因「成员缺方法论技能」，得有地方报 —— 否则缺口只能
        # 烂在打回指令里，老板既看不到也没法补（开工计划的【差技能】只覆盖开工时）。
        lines.append(
            "\n【差技能】若本波打回的原因是「成员缺某个方法论技能」"
            "（不是他偷懒、也不是任务没说清），请在本节写明，一行一个：\n"
            "`- 技能名：给谁用；干什么用` —— 老板会去 GitHub 找了装上再挂给他。\n"
            "不是这个原因（或缺技能压根不是问题）就写「无」。")
        # v4.125 ③：子项清单 —— PM 才知道 N 是几，才能点名【仅重跑子项 N】
        _subs = (getattr(self, "_wave_subtasks", None) or {}).get(wave_no - 1) or []
        if len(_subs) >= 2:
            _sub_lines = "、".join(f"{s['idx']}={s['title']}" for s in _subs)
            lines.append(
                f"\n【本波子项清单】产出已拆成 {len(_subs)} 个子项：{_sub_lines}\n"
                "个别子项不合格、其余都好时，打回可省全波重跑：在判定 FAIL 后写\n"
                "【仅重跑子项 N】（N=要重做的子项序号）—— 只重做那一个，其余保留。\n"
                "多数子项都有问题 / 形态整体不对时，不要点名，走整波重跑。")
        if plan_text:
            lines.append(f"\n【开工时你定的执行计划】\n{plan_text[:_PLAN_CTX_LIMIT]}")
        # v4.124.13：标的记忆 + 项目教训本 —— 让 PM 知道「哪些方向已经被打死、
        # 哪些错已经犯过」，别每次都从头骂一遍同样的错。
        _tmem = legion.target_memory_block(getattr(self, "project", None), task=getattr(self, "task", ""))
        if _tmem:
            lines.append("\n" + _tmem)
        _pless = legion.project_lessons_block(getattr(self, "pid", ""))
        if _pless:
            lines.append("\n" + _pless)
        if manual_ctx:
            # 截断以防 token 爆炸（plan_text 已 800 字，加起来超过 1.6k）
            lines.append(f"\n{manual_ctx[:_MANUAL_CTX_TOTAL_LIMIT]}")
        # v4.131：采集留痕 —— PM 得先看见「成员到底搜了什么」，再判内容对不对。
        # 此前抓取完全黑盒：正文写得像样就放行，实际可能一个字都没搜。
        try:
            _src_blk = legion.sources_block(getattr(self, "run_id", None),
                                            wave=wave_no, limit=10)
        except Exception:
            _src_blk = ""
        if _src_blk:
            lines.append("\n" + _src_blk)
            lines.append("（留痕用法：搜索词/抓取 URL 与任务主题在**平台·地区·时间**"
                         "上对不上 = 数据源跑偏，正文必然不可信；"
                         "更多记录用 legion_get_sources 查。）")
        else:
            lines.append(
                "\n【🔴 采集留痕 · 空】本波成员**一条抓取记录都没有**"
                "（没调用 web_search / web_fetch）。\n"
                "数据类角色（研究员 / 分析师 / 选品官 / 竞品 / 市场调研）出现这种情况，"
                "说明结论多半来自模型记忆而非实搜 —— 直接判定 FAIL，"
                "打回指令写明「必须先用 web_search 实搜，再把来源逐条标注」。")
        # v4.127 Bug-1：同一问题第 2 次打回起，PM 的打回指令必须是**模板填空式**。
        # 光描述要求没用（实测写手连错 3 次），直接给骨架让成员逐段填。
        if attempt >= 1:
            lines.append(
                "\n【🔴 这是第 2 次及以后的验收 —— 打回指令必须模板填空式】\n"
                "若判定 FAIL，除了【问题清单】和【打回指令】，还必须补一段：\n"
                "【交付骨架】\n"
                "【开头 hook】…\n【核心内容 1】…\n【核心内容 2】…（按本波约定交付物"
                "的实际段落写，不要照抄这几个通用名）\n"
                "—— 列清楚这个交付物**必须出现的每一段**，成员逐段填空，"
                "缺任何一段都算未交付。\n"
                "只描述要求（「要写成详情页文案」）不给出骨架，等于没说。")
        # v4.127 Bug-2：跨产物引用核验 —— 让 PM 自己也能抓悬空引用
        lines.append(
            "\n【🔴 跨产物引用核验（硬检查，必做）】\n"
            "扫一遍成员正文里有没有「见第 X 波」「沿用 XX 成稿」「如 XX 报告所述」"
            "这类**引用别的产物**的说法。\n"
            "凡出现，逐条用 legion_list_outputs / legion_get_output 核对它是否真实存在：\n"
            "· 被引用产物在产出清单里**根本不存在** → 直接判定 FAIL，\n"
            "  在【打回指令】里写明：引用原文 + 核对结果「outputs 中无此产物」"
            "（系统已做同样扫描并会把结果并进打回意见）。\n"
            "· 引用真实存在才算过。")
        # v4.136（P2-⑥）：逐角色完成标准 + 结构化契约 —— 验收硬判据。
        # 让 PM 验收时照着「本波每个角色该交什么、必含哪些小节」逐条核对，
        # 缺一条＝未交付＝打回，强化验收卡点。
        try:
            _acc_blk = legion.acceptance_contract_block(members)
        except Exception:
            _acc_blk = ""
        if _acc_blk:
            lines.append("\n" + _acc_blk)
        lines.append(
            "\n【你的动作】\n"
            "1. 用 legion_list_outputs 看产出清单，再用 legion_get_output 读全文"
            "（必要时 legion_read_log 查执行过程）；\n"
            "2. 🔴 **先查交付物形态**（第一道闸，必做）：回到【开工时你定的执行计划】，\n"
            "   找到**本波约定的交付物形态**（例：写手＝1 条 hook + ≥3 条 USP 卖点文案；\n"
            "   主图策划＝N 张分镜、每张含构图 + 卖点文案；竞品分析师＝对比表 + 差异结论），\n"
            "   核对成员交出来的**是不是这个东西**：\n"
            "   · 交的是「工作报告 / 写作说明 / 元评论 / 素材提示词 / 原始搜索结果」\n"
            "     而不是约定的交付物 → **形态不对，直接判定 FAIL**（不必再看内容）；\n"
            "   · 内容写得再好，交的不是约定的东西就是零分 —— 这是最高频的跑题；\n"
            "   · 形态对了，再逐条看硬伤。\n"
            "3. 🔴 **数据可信度闸**（本波含数据类角色时必做，实测最高频的翻车点）：\n"
            "   · 先看上面的【采集留痕】：搜的词/抓的页与任务主题（平台·地区·时间）"
            "对不上 → 数据源跑偏，判定 FAIL；\n"
            "   · 一条抓取记录都没有 → 结论来自模型记忆，判定 FAIL；\n"
            "   · 产出里混着**网页原文 / 搜索结果原文**（导航词、大段无结构文本、"
            "『搜索「X」结果（来源：Y）』）＝未加工的抓取素材，不是交付物 → 判定 FAIL；\n"
            "   · 硬数据（带单位/百分号的数字）没有【来源+采集日期】→ 要求补源，"
            "补不上就删掉（无源数字大概率是编的）。\n"
            "4. 逐条对照验收标准判断有没有硬伤（事实错误 / 跑题 / 缺交付物 / 违反约束）；\n"
            "5. 按输出格式给出结论，最后一行固定写：判定：PASS  或  判定：FAIL。\n"
            "🔴 判 FAIL 时**必须同时写出【问题清单】和【打回指令】**：\n"
            "   · 只写「不合格，重做」这种结论 = 白打回，成员无从下手；\n"
            "   · 改法要可操作、可检查（「每处数字补来源」，不是「再认真点」）；\n"
            "   · 给不出改法会被系统追问补投（多花一轮），不如一次写全。\n"
            "🔴 你**读不到产出**时（工具返回「无执行记录」/ 查无此产出）：\n"
            "   · 禁止凭【已登记产出】名单或记忆**脑补**内容凑评价；\n"
            "   · 禁止把上面我给你的打回指令复述一遍当成你自己的意见；\n"
            "   · 直接写 判定：FAIL，并在【问题清单】写明「产出不可读，需恢复挂载/重跑」，\n"
            "     可执行改法指向「让产出恢复可读」，而不是改内容。\n"
            "🔴 你的判定只是**建议**：放不放行由用户授权，禁止写「准予放行」「已批准」。\n"
            "注意：没有硬伤就建议放行，打回要重跑、很贵。"
        )
        # v4.131-F：成员上报清单 —— PM 验收时**必读**（否则成员上报等于白报，
        # 下次还是硬编数据）。放在指令正文里，PM 绕不过去。
        try:
            _iss = legion.issues_block(self.run_id, wave=wave_no)
        except Exception:
            _iss = ""
        if _iss:
            lines.append("")
            lines.append(_iss)
        return "\n".join(lines)
