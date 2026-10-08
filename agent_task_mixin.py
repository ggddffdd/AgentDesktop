# -*- coding: utf-8 -*-
"""agent_task_mixin.py —— v4.225 Agent 任务账本接线（从 agent.py 抽出）

为什么要有这个文件
------------------
v4.225 给 agent.py 接任务状态机时净增了 121 行，把文件推到 **2468 行**，
撞了 v4.216 拆分时立的**拆分红线**（`tests/test_split_216.py` 的
`D2 agent.py < 2400 行` —— 那条判据的作用正是「防慢慢回弹」，把
ChatWindow / 判据族一点点搬出主类的成果又堆回来）。

处理方式沿用 v4.216 拆 `ChatAuditMixin` 的同一思路：**接线代码搬出
agent.py，主类只留调用点**。判据 `D2` 的阈值**不动**（那是红线，不是
可以商量的参数）—— 抬阈值等于把红线挪到自己脚下，测不出真回弹。

本Mixin 承接三件事（都在 agent.py 里只有一行调用点）：
  1. `_tstate_init(...)`     —— run() 开头建账本
  2. `_tstate_record(...)`   —— _handle_tool_result 里记一次工具调用
  3. `_tstate_step(...)`     —— 每步同步步号（含续跑轮不归零）

`task_state.should_nudge` / `nudge_instruction` 的**判定逻辑仍在
task_state.py**（那里才是账本本体，且已被判据 126 项覆盖）。本文件
只做「取账本 → 调判定 → 返回要不要 continue」的接线，不重复实现任何
判定 —— 避免两处判据漂移。

零行为变化保证：所有 try/except 都在，异常时返回「不介入」，主循环完全
按旧路径走（与v4.224 引入时的 fail-open 口径一致）。
"""
import json
import logging

log = logging.getLogger("dsdesktop")

VERSION = "v4.236.0"


class AgentTaskMixin(object):
    """AgentWorker 的任务账本接线（mixin，无 self 状态假设之外的东西）。

    被 mixin 的类必须能拿到：
      self._last_user_text()   —— 取最近一条 user 原话
      self._tstate             —— 账本（由本 mixin 建立）
      self._tstate_nudged      —— 本轮是否已注入过补做提示
    """

    # --------------------------------------------------------
    # 0) 取最近一条 user 原话
    # --------------------------------------------------------
    def _last_user_text(self):
        """取最近一条 user 消息原文（给 intent.classify / 建账本用）。

        没有 user 消息（极端边界：system-only 上下文）时返回空串 ——
        classify 对空串返回 KIND_UNKNOWN，required_from_text 返回空，
        两者都不抛异常、不阻断。
        """
        try:
            for m in reversed(getattr(self, "messages", None) or []):
                if isinstance(m, dict) and m.get("role") == "user":
                    c = m.get("content")
                    if isinstance(c, str):
                        return c
                    if isinstance(c, list):
                        # 多模态：把 text part 拼起来给判据看（图片不参与路由）
                        return "".join(
                            str(p.get("text") or "")
                            for p in c if isinstance(p, dict)
                            and p.get("type") == "text")
        except Exception:
            pass
        return ""

    # --------------------------------------------------------
    # 1) 建账本
    # --------------------------------------------------------
    def _tstate_init(self, task_state):
        """run() 开头调一次：建账本 + 复位 nudge 标记。

        硬要求只认「用户**字面点名**了工具」，不认关键词命中 ——
        否则「帮我写一段口播文案」会因命中「口播」被逼去调 video_gen。
        """
        try:
            _t = self._last_user_text()
            self._tstate = task_state.TaskState(
                _t, [n for n, _w in task_state.required_from_text(_t)])
        except Exception as e:
            log.warning("任务账本建立失败（已忽略，退化为不介入）: %s", e)
            try:
                self._tstate = task_state.TaskState()
            except Exception:
                self._tstate = None
        self._tstate_nudged = False

    # --------------------------------------------------------
    # 2) 记账
    # --------------------------------------------------------
    def _tstate_record(self, task_state, name, fn, result_str,
                       deliverables, failed_checker, tool_result=None):
        """_handle_tool_result 里调：记一次工具调用。

        `failed_checker(name, result_str) -> bool` 由调用方注入（本项目用
        既有 `_tool_result_looks_failed`）—— **不重新判定失败**，避免与
        主类里那份判据漂移；注入方报错则按成功记（fail-open：宁可少提示，
        不可误报未完成）。

        v4.226：`tool_result` 是 `tools.exec_tool()` 的原对象（ToolResult）。
        传了就**以它的 `.ok` 为准** —— 执行后验证失败（`error_code=
        POST_VERIFY_FAILED`）的信号追加在 msg 尾部、且不在任何字符串判据
        里，只看文案会把「验证失败」记成「成功」。判定真源仍是
        `agent_result_mixin._tool_outcome`，本方法不重复实现。
        """
        try:
            _ts = getattr(self, "_tstate", None)
            if _ts is None:
                return
            if tool_result is not None and hasattr(tool_result, "ok"):
                _ok = bool(tool_result.ok)
            else:
                try:
                    _ok = not failed_checker(name, result_str)
                except Exception:
                    _ok = True
            _args = fn.get("arguments") if isinstance(fn, dict) else None
            if isinstance(_args, str):
                try:
                    _args = json.loads(_args)
                except Exception:
                    _args = None
            _ts.record_tool(name, _args, result_str, ok=_ok,
                            step=_ts.current_step)
            # 工具自己报的产物也登记（覆盖「参数是相对路径、实际落在别处」）
            for _d in (deliverables or []):
                if isinstance(_d, (tuple, list)) and _d and _d[0]:
                    _ts.note_artifact(str(_d[0]), name)
        except Exception as e:
            log.warning("任务状态机记账失败（已忽略）: %s", e)

    # --------------------------------------------------------
    # 3) 步号同步
    # --------------------------------------------------------
    def _tstate_step(self, step):
        """主循环每步开头调：同步步号到账本。"""
        try:
            _ts = getattr(self, "_tstate", None)
            if _ts is not None:
                _ts.current_step = int(step)
        except Exception:
            pass

    def _tstate_resume_step(self, base_steps, rstep):
        """续跑轮调：步号**延续**（base + 本轮），不归零。"""
        try:
            _ts = getattr(self, "_tstate", None)
            if _ts is not None:
                _ts.current_step = int(base_steps) + int(rstep)
        except Exception:
            pass

    def _tstate_resume_reset_nudge(self):
        """续跑轮调：放开 nudge 闸（主轮那次已注入过）。

        放开后仍受 `should_nudge` 的「本轮注入过」闸约束 → 续跑轮最多补
        一轮，不会变成无限补做循环。
        """
        self._tstate_nudged = False

    # --------------------------------------------------------
    # 4) 收尾闸门
    # --------------------------------------------------------
    def _tstate_nudge_now(self, task_state, step, max_steps):
        """收尾 `break` 前调：该不该补做？该则注入 messages 并返回 True。

        返回 True = 已注入指令，调用方应 `continue`（再给模型一轮）；
        返回 False = 不介入，调用方按旧路径 break（零行为变化）。

        四道 gate 全部在 `task_state.should_nudge` 里判定（该处有 126 项
        判据覆盖），本函数只负责执行副作用。
        """
        try:
            _ts = getattr(self, "_tstate", None)
            if _ts is None:
                return False
            if not _ts.should_nudge(step, max_steps,
                                    already_injected=self._tstate_nudged):
                return False
            _instr = _ts.nudge_instruction()
            if not _instr:
                return False
            _ts.mark_nudged()
            self._tstate_nudged = True
            self._force_next = True
            self._idle_steps = 0
            self._emit_status("⚠ 任务要求未完成，正在补做…")
            self.messages.append({"role": "user", "content": _instr,
                                  "_internal": True})
            log.info("任务状态机补做：%s", _ts.summary())
            return True
        except Exception as e:
            log.warning("任务状态机收尾闸门异常（已忽略）: %s", e)
            return False

    def _tstate_trace_nudge(self, step, tracer):
        """收尾闸门放行后调：往步级追踪记一笔（tracer 可为 None）。"""
        try:
            if tracer is None:
                return
            _ts = getattr(self, "_tstate", None)
            tracer.trace(step, "nudge",
                         reason="任务状态机：子目标/产物未达标",
                         pending=(_ts.pending()[:4] if _ts else []))
        except Exception:
            pass

    # --------------------------------------------------------
    # 5) 结构化工具结果判定（v4.226）
    # --------------------------------------------------------
    def _tool_outcome(self, tool_result, name, result_str, failed_checker):
        """判定一次工具调用**实质**成功与否 —— 返回 (ok, err_head)。

        v4.226 存在的理由：`tools.exec_tool()` 自v4.223 起返回的是
        `ToolResult`，带 `ok` / `verified` / `error_code` / `retryable`
        这些**机器可读**字段；但调用方（agent.py 的串行与并发两条路径）
        立刻 `result_str, deliverables, schedule = ...` 解包成三元组，
        字段全丢。后续三处判断便退化成**看文案**：

          · `tool_finished` 的 UI 绿勾/红叉 → `result_str[:20]` 前缀匹配
          · 任务账本记账               → `_TOOL_FAIL_MARKS` 头 400 字匹配
          · 证据登记 + 幂等账本        → 同上

        而执行后验证（v4.224）把失败信号**追加在 msg 尾部**：

            '已写入 5 字符到 x\\n[执行后验证未通过] 同名进程仍残留3个'

        `error_code=POST_VERIFY_FAILED` 不在任何字符串判据里，标记又在尾部
        —— 工具正文一旦超过 400 字，`_TOOL_FAIL_MARKS` 连头 400 字都扫不到，
        于是**验证失败被记成成功**（实测：UI 绿勾 + 账本 ok=True）。
        这不是理论风险，是已复现的数据丢失。

        口径（优先级从高到低）：
          1. `tool_result` 是 `ToolResult`（有 `.ok`）→ **以它为准**。
             字符串一律不再参与判定，纯展示。
          2. 不是 `ToolResult`（占位/ 去重 / 用户取消 / 异常兜底这些
             根本没走 exec_tool 的分支）→ 退回注入的 `failed_checker`
             旧口径，不改变它们原有的成败结论。

        零行为变化：非 `ToolResult` 时与v4.225 完全一致。

        返回的 `err_head` 供证据登记用（截到 300 字，与旧口径同长度）。
        """
        # ---- 1) 有结构化结果：以 .ok 为唯一权威 ----
        if tool_result is not None and hasattr(tool_result, "ok"):
            try:
                _ok = bool(tool_result.ok)
            except Exception:
                _ok = True
            _err = None
            if not _ok:
                #优先用机器可读原因，缺失才退回文案；都不好用完整 msg。
                _parts = []
                try:
                    _ec = getattr(tool_result, "error_code", None)
                    if _ec:
                        _parts.append("[%s]" % _ec)
                except Exception:
                    pass
                try:
                    _em = getattr(tool_result, "error", None)
                    if _em:
                        _parts.append(str(_em))
                except Exception:
                    pass
                if not _parts:
                    _parts.append(str(getattr(tool_result, "msg", "") or ""))
                _err = " ".join(_parts)[:300]
            return (bool(_ok), _err)

        # ---- 2) 非结构化：沿用注入的旧判据 ----
        try:
            _failed = bool(failed_checker(name, result_str))
        except Exception:
            # 注入方报错 → 按成功记（fail-open，与 v4.225 同口径）
            return (True, None)
        return (not _failed,
                ((str(result_str) or "")[:300] if _failed else None))

    def _tr_notable_flags(self, tool_result):
        """取ToolResult 上值得记账的诊断字段（供日志/排障，不参与判定）。

        刻意**不**返回 ok —— 成败判定只有 `_tool_outcome` 一个口径，
        避免两处判据漂移（与 `_tstate_record` 注入判据的同一原则）。
        """
        if tool_result is None or not hasattr(tool_result, "ok"):
            return {}
        out = {}
        for _f in ("verified", "error_code", "retryable"):
            try:
                _v = getattr(tool_result, _f, None)
            except Exception:
                continue
            if _v not in (None, False):
                out[_f] = _v
        return out


__all__ = ["VERSION", "AgentTaskMixin"]