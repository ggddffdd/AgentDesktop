# -*- coding: utf-8 -*-
"""agent_result_mixin.py —— v4.226 工具结果结算接线（从 agent.py 抽出）

为什么单独一个文件
------------------
与 v4.225 的 `agent_task_mixin.py`、v4.226 的 `agent_loop_mixin.py` 同理：
v4.216 立的拆分红线（`tests/test_split_216.py` 的 `D2 agent.py < 2400 行`）
作用是**防慢慢回弹**，阈值不能抬。所以「拿到工具结果之后怎么结算」的接线
搬出 agent.py，主类只留调用点。

v4.226 这次接线的起因
---------------------
`tools.exec_tool()` 自 v4.223 起返回 `ToolResult`（带 `ok` / `verified` /
`error_code` / `retryable` 这些机器可读字段），但调用方**立刻**解包成三元组：

    result_str, deliverables, schedule = tools.exec_tool(...)

字段全丢，于是后续所有成败判断退化成**看文案**。而执行后验证（v4.224）
的失败信号恰恰**不在文案头部**：

    ToolResult.ok = False
    error_code   = POST_VERIFY_FAILED
    msg          = '已写入 5 字符到 x\\n[执行后验证未通过] 同名进程仍残留3个'

`[执行后验证未通过]` 是**追加在尾部的**，`_TOOL_FAIL_MARKS` 只扫 `s[:400]`
—— 工具正文一旦超过 400 字，标记连头 400 字符都进不去。

复现证据（实测，非推演）
------------------------
        ok         = False
        error_code = POST_VERIFY_FAILED
        前20字      = '已写入 5 字符到 <路径已脱敏>\\n[执'
        _TOOL_FAIL_MARKS 命中 = False
        UI success  = True     ← 绿勾
        账本 ok= True          ← 记成成功

**验证失败被当成成功**。本 mixin 把 `ToolResult.ok` 接成唯一权威。

分工（刻意不重叠）
------------------
    tool_contract.py        ToolResult 定义 / 结局契约 / 执行后验证（纯逻辑）
    agent_task_mixin.py     任务账本接线（init / record / step / nudge）
    agent_result_mixin.py   结果结算接线（判定成败 → 发信号 → 记账 → 证据）
    agent.py                执行工具（串行 / 并发）+ 一行调用点

**判定逻辑只有 `_tool_outcome` 一个口径** —— UI 绿勾、任务账本、证据登记、
幂等账本四处消费全部取它的返回值，杜绝两处判据漂移（本项目明确吃过的坑）。

零行为变化保证
--------------
传入的不是 `ToolResult` 时（占位 / 批内去重 / 用户取消 / 异常兜底这些
根本没走 exec_tool 的分支），退回注入的 `_tool_result_looks_failed` 旧口径，
结论与 v4.225 完全一致。所有 try/except 都在，异常时按旧路径走。
"""
import json
import logging
import os

import task_state  # v4.226：原代码在 agent.py 内可见，本文件须显式 import
from config import TOOL_RESULT_LIMIT
from token_compressor import compress

log = logging.getLogger("dsdesktop")

VERSION = "v4.249.0"


def _wrap_tool_content_local(name, content, evidence_id=None):
    """不可信内容边界（v4.226：从 agent.py 原样搬来，保持行为一致）。

    与 agent.py 的 `_wrap_tool_content` 同源同参。**刻意不在此重新实现**
    边界规则（清单/ 标签格式由 agent.py 的 `wrap_untrusted` 单点定义）——
    这里只做「找得到就用，找不到就原样返回」的兜底，避免两处判据漂移。
    """
    try:
        import agent as _agent_mod
        _fn = getattr(_agent_mod, "_wrap_tool_content", None)
        if callable(_fn):
            return _fn(name, content, evidence_id)
    except Exception:
        pass
    return content


class AgentResultMixin(object):
    """工具结果结算接线（mixin，无 self 状态假设之外的东西）。

    被 mixin 的类必须能拿到：
      self.tool_log / tool_finished / deliverable_added / render（Signal）
      self.messages（对话上下文 list）
      self._tstate_record(...)（由 AgentTaskMixin 提供）
      self._record_exec_ledger(...)（幂等账本）
      self._app_dir（交付物绝对化用）
      self._tool_result_looks_failed（classmethod，旧字符串判据）
    """

    # --------------------------------------------------------
    # 1) 成败判定：ToolResult.ok 为唯一权威
    # --------------------------------------------------------
    def _tool_outcome(self, tool_result, name, result_str, failed_checker):
        """判定一次工具调用**实质**成功与否 —— 返回 (ok, err_head)。

        口径（优先级从高到低）：

          1. `tool_result` 是 `ToolResult`（有 `.ok`）→ **以它为准**，
             字符串一律不参与判定，纯展示。
          2. 不是 `ToolResult`（占位 / 批内去重 / 用户取消 / 异常兜底这些
             根本没走 exec_tool 的分支）→ 退回注入的 `failed_checker`
             旧口径，**不改变**它们原有的成败结论。

        `err_head` 供证据登记用（截到 300 字，与旧口径同长度）。
        优先取机器可读的 `error_code` / `error`，都没有才退回 msg。
        """
        # ---- 1) 有结构化结果：以 .ok 为唯一权威 ----
        if tool_result is not None and hasattr(tool_result, "ok"):
            try:
                _ok = bool(tool_result.ok)
            except Exception:
                _ok = True
            if _ok:
                return (True, None)
            return (False, self._tr_err_head(tool_result))

        # ---- 2) 非结构化：沿用注入的旧判据 ----
        try:
            _failed = bool(failed_checker(name, result_str))
        except Exception:
            # 注入方报错 → 按成功记（fail-open，与 v4.225 同口径：
            # 宁可少提示，不可误报未完成）
            return (True, None)
        return (not _failed,
                ((str(result_str) or "")[:300] if _failed else None))

    @staticmethod
    def _tr_err_head(tool_result):
        """从失败的 ToolResult 里取一句话原因（最多 300 字）。"""
        parts = []
        try:
            _ec = getattr(tool_result, "error_code", None)
            if _ec:
                parts.append("[%s]" % _ec)
        except Exception:
            pass
        try:
            _em = getattr(tool_result, "error", None)
            if _em:
                parts.append(str(_em))
        except Exception:
            pass
        if not parts:
            try:
                parts.append(str(getattr(tool_result, "msg", "") or ""))
            except Exception:
                parts.append("")
        return " ".join(parts)[:300]

    def _tr_diag(self, tool_result):
        """取 ToolResult 上值得记日志的诊断字段（**不参与**成败判定）。

        刻意不返回 ok —— 成败只有 `_tool_outcome` 一个口径。
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

    # --------------------------------------------------------
    # 2) 结果结算（v4.226 从 agent.py 整体搬来）
    # --------------------------------------------------------
    def _handle_tool_result(self, tc, name, fn, result_str, deliverables, schedule,
                            tool_result=None):
        """统一处理工具执行结果：发射信号、追加 tool message。

        v4.226：`tool_result` 是 `tools.exec_tool()` 的**原对象**（ToolResult）。
        传了就以它的 `.ok` 判成败；没传（占位 / 去重 / 取消 / 兜底）走旧
        字符串口径。**旧调用方不传此参数时行为与 v4.225 完全一致。**

        v4.103 修复：交付物必须标准化为 (rel, kind, name) 三元组再 emit。历史 bug——
        browser_open 等工具返回裸路径字符串，emit(*d) 把路径字符串拆成数十个字符当
        多个参数 → TypeError；该异常发生在 Agent 主循环内且未被捕获，直接冲出 run()
        绕过末尾 finally 的 done.emit()，导致 _busy 永久 True、输入框锁死、「Agent 工作中」
        永远转圈。现对交付物做防御性标准化 + 异常吞掉，任何工具返回异常格式都不再卡死。
        """
        # ---- 成败判定（单一真源，四处消费） ----
        _ok, _err = self._tool_outcome(
            tool_result, name, result_str, self._tool_result_looks_failed)
        if tool_result is not None:
            _diag = self._tr_diag(tool_result)
            if _diag:
                log.debug("工具结果结构化字段 %s: %s", name, _diag)

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
            # v4.222：累积进本次运行产物清单（路径绝对化，供 _infer_outcome 做产物级验收）
            try:
                if isinstance(d, (tuple, list)) and len(d) >= 1 and d[0]:
                    _p = str(d[0])
                    if not os.path.isabs(_p) and getattr(self, "_app_dir", ""):
                        _p = os.path.join(self._app_dir, _p)
                    _kind = str(d[1]) if len(d) > 1 else ""
                    _nm = str(d[2]) if len(d) > 2 else os.path.basename(_p)
                    if not hasattr(self, "_deliverables"):
                        self._deliverables = []
                    if not any(_x[0] == _p for _x in self._deliverables):
                        self._deliverables.append((_p, _kind, _nm))
            except Exception:
                pass
        # v4.225（P2 任务状态机）：记账（接线在 agent_task_mixin）
        self._tstate_record(task_state, name, fn, result_str, deliverables,
                            self._tool_result_looks_failed, tool_result=tool_result)
        # 定时提醒
        if schedule:
            msg, delay_secs = schedule[0], schedule[1]
            repeat_secs = schedule[2] if len(schedule) > 2 else 0
            self.schedule_reminder.emit(int(delay_secs * 1000), msg, repeat_secs)

        # v4.195 批⑨：持久化工具记录 + **证据登记**。
        #
        # 现状（外部审查问题 7 的真身）：result 一共被存了三处，全是压过的——
        #   ① 模型上下文 compress(…, TOOL_RESULT_LIMIT=6000)
        #   ② UI tool_log → ui._on_tool_log 里 _clip(result, 500)
        #   ③ 会话 store（同 ②）
        # 等于**没有任何一处保留完整原文** → 事后无法回验「这个结论到底从哪来的」。
        # 本批把完整原文登记进独立的 Evidence Registry（SQLite，
        # 位于 USER_DATA_DIR/evidence/），后续 claim-evidence 回验只认这里的原文。
        _eid = None
        try:
            import evidence
            _eid = evidence.register(
                str(name or ""), fn.get("arguments", ""), result_str,
                ok=_ok, err=_err)
        except Exception as e:
            log.warning("证据登记异常（已忽略，不影响主流程）: %s", e)
        # v4.222：登记执行 ledger（供断点恢复时幂等查询）
        try:
            self._record_exec_ledger(name, fn.get("arguments", ""), _ok)
        except Exception:
            pass

        self.tool_log.emit({
            "name": name,
            "args": fn.get("arguments", ""),
            "result": result_str,
            # v4.195 批⑨：把证据编号带回 UI —— UI 侧不再只依赖被裁到 500 的 result
            "evidence_id": _eid,
            "evidence_ok": bool(_ok),
        })
        self.render.emit()

        # v4.195 批⑨：给模型看的内容加 [EV#n] **首尾双钉**。
        # 沿用批⑥（tool result 首尾双钉 + [RESULT NOT FOUND]）已被 v4.191.0
        # 真机对照实验验证有效的同一手法：标记放在模型要读的文本里，
        # 而不是指望它从 system prompt 里记住。
        _content = compress(str(result_str), TOOL_RESULT_LIMIT)
        if _eid:
            try:
                _n_lines = str(result_str).count("\n") + 1
                _head = evidence.model_header(
                    _eid, tool=str(name or ""),
                    n_chars=len(str(result_str)), n_lines=_n_lines,
                    ok=_ok, err=_err,
                    args_head=str(fn.get("arguments", ""))[:90])
                _content = _head + _content + evidence.model_footer(_eid)
            except Exception as e:
                log.warning("证据抬头拼装失败（降级为无标记）: %s", e)
        self.messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "name": name,
            # v4.60：先 Token 压缩（去HTML/去重/智能截断），再取上限
            # v4.195 批⑨：压缩后套证据编号首尾钉
            "content": _wrap_tool_content_local(name, _content, _eid),
        })