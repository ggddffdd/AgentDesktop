# -*- coding: utf-8 -*-
"""agent_exec_mixin.py —— v4.289.4 工具执行/结算入口（从 agent.py 外移）

为什么要有这个文件
------------------
v4.289.4 给并发停止补「回收已启动 future + 只给未提交的调用补未执行回执」、
给去重签名补「按执行成功登记、工作流同口径」、给工具批次补异常兜底，
agent.py 因此涨到 **2440 行**，撞了 `tests/test_split_216.py` 的 D2 红线
（`agent.py < 2400 行`）。按项目纪律「处理 = 外移，不抬阈值」，整组搬到本 mixin。

承接五件事（agent.py 里只剩调用点）：
  1. `_run_concurrent`              —— 并发执行 + 停止语义
  2. `_safe_exec_tool_calls`        —— 批次异常兜底（保 done.emit 必达）
  3. `_fill_missing_tool_receipts`  —— 补齐 tool 回执（防下轮 API 400）
  4. `_remember_tool_sig`           —— 只登记「成功」的调用签名
  5. `_tool_sig_of`                 —— 单次调用的规范化签名

依赖宿主类提供：`_emit_status` / `tool_started` / `tool_finished` /
`_next_tool_index` / `_ctx_sid` / `_stop_requested` / `_tool_outcome` /
`_tool_result_looks_failed` / `_handle_tool_result` / `messages`。

零行为变化：方法体逐字搬移，`tools` / `context_manager` 仍是同一模块对象
（测试对 `tools.exec_tool` 的 patch 依旧生效）。
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import context_manager
import tools

log = logging.getLogger("dsdesktop")


class AgentExecMixin(object):
    """工具执行与结算入口（并发、回执补齐、签名登记、异常兜底）。"""

    def _fill_missing_tool_receipts(self, tool_calls, content):
        """给还没有 tool 回执的调用补占位消息。

        assistant 消息里的 tool_calls 已落库，缺对应 tool 回执会让下一轮 API 400
        （会话带毒）。停止、异常兜底、工作流接管共用这一份补齐逻辑。
        """
        try:
            _done_ids = {m.get("tool_call_id") for m in self.messages
                         if isinstance(m, dict) and m.get("role") == "tool"}
            for tc in (tool_calls or ()):
                if not isinstance(tc, dict):
                    continue
                _id = tc.get("id")
                if not _id or _id in _done_ids:
                    continue
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": _id,
                    "content": content,
                })
                _done_ids.add(_id)
        except Exception as e:
            log.warning("补占位回执失败（已忽略）: %s", e)

    def _safe_exec_tool_calls(self, tool_calls, mw, APP_DIR):
        """`_exec_tool_calls` 的异常兜底版本。

        v4.289.4（2026-10-27 核实修复）：工具批次里的意外异常（如并发停止时
        补占位段的 AttributeError）会直接穿出 `_run_inner` 主循环 —— 跳过收尾段
        → `done.emit()` 不发 → UI busy 不复位、断点不写、会话缺 tool 回执。
        这里兜住并补齐回执，保证收尾一定跑得到。
        """
        try:
            self._exec_tool_calls(tool_calls, mw, APP_DIR)
        except Exception as e:
            log.error("工具批次执行异常（已兜底，防会话带毒）: %s", e)
            self._fill_missing_tool_receipts(
                tool_calls, "（系统提示：该工具调用执行异常，未取得结果。）")

    def _remember_tool_sig(self, name, args):
        """登记「已成功执行」的调用签名，供死循环护栏判定。

        v4.289.4：原实现把签名登记放在执行**之前**，失败的调用也留表 →
        下一轮同参重试被当死循环直接拦掉。改为只记成功（`_handle_tool_result`
        与 run_workflow 分支在确认成功后才调本方法），护栏语义收敛为
        「同参数重复**成功**调用」才算空转；签名统一 `sort_keys`，与批内去重同口径。
        """
        try:
            if not isinstance(getattr(self, "_recent_tool_sigs", None), list):
                self._recent_tool_sigs = []
            _args = args if isinstance(args, dict) else {}
            sig = (str(name), json.dumps(_args, sort_keys=True, ensure_ascii=False))
            if sig not in self._recent_tool_sigs:
                self._recent_tool_sigs.append(sig)
            self._recent_tool_sigs = self._recent_tool_sigs[-12:]
        except Exception:
            pass

    def _tool_sig_of(self, tool_call):
        """取单个 tool_call 的执行签名（与批内去重、死循环护栏同口径）。"""
        try:
            fn = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
            name = str(fn.get("name", "") or "")
            try:
                args = json.loads(fn.get("arguments", "{}") or "{}")
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            return (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        except Exception:
            return ("", "{}")

    def _run_concurrent(self, tool_calls, mw, APP_DIR):
        """并发执行工具调用（`tool_calls` 是 `[(tool_call, decision), ...]`）。

        v4.289.4（2026-10-27 核实修复）停止语义对齐串行路径：
          ① 停止后不再提交新工具；
          ② 已提交的 future **全部回收**，走 `_handle_tool_result` 记真实结果
             （工具已执行完、副作用可能已发生，结果不能丢）；
          ③ 只有确实没提交的调用才补「未执行」回执。
        原实现在 `as_completed` 里 `if stop: continue` 跳过回收，收尾又对
        `(tc, dec)` 元组调 `.get()` → 停止并发批次必抛 AttributeError，
        已执行工具的账本/ledger/交付物全丢。
        """
        # v4.245.0（审查报告 T-3）：空批次直接返回，防 max_workers=0 抛 ValueError。
        if not tool_calls:
            return
        max_workers = min(len(tool_calls), 5)
        total = len(tool_calls)
        self._emit_status(f"并发执行 {total} 个工具（{max_workers} 线程）…")
        # 收尾补占位要用纯 tool_call 列表（入参是 (tc, dec) 元组）
        _all_tc = [(_x[0] if isinstance(_x, (tuple, list)) else _x)
                   for _x in tool_calls]

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            _seen_sigs = set()  # v4.120：批内完全重复调用去重（同 _run_serial）
            for _seq_idx, (tc, _dec) in enumerate(tool_calls):
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
                    break  # v4.289.4：未提交的调用留给下方统一补「未执行」回执
                # 发射工具开始信号
                self.tool_started.emit({
                    "name": name, "args": args, "index": idx, "total": total
                })
                t0 = time.time()
                # 审计修复 E1：线程池工作线程是全新线程，不继承 thread-local——
                # 在任务函数内先注入默认 sid，并发执行的 context 工具同样命中当前会话。
                _ctx_sid = self._ctx_sid
                # v4.244.0（审查报告 T-2）：_dec 由 _exec_tool_calls 批次级 decide 统一传入
                # （已含 task_risk + needs_user 校验），worker 内不再二次 decide——那处重算
                # 既漏 task_risk 又不校验 needs_user，且只带来风险不带来信息。
                def _exec_with_ctx(_name=name, _args=args, _sid=_ctx_sid,
                                  _stop=lambda: self._stop_requested, _dec_ctx=_dec):
                    context_manager.set_default_sid(_sid)
                    return tools.exec_tool(mw.cfg, APP_DIR, _name, _args,
                                           should_stop=_stop, perm_ctx=_dec_ctx)
                futures[pool.submit(_exec_with_ctx)] = (tc, t0, idx)

            for future in as_completed(futures):
                # v4.289.4：不再因停止而跳过——future 已提交且必然执行完（with 会 join），
                # 跳过回收等于把真实结果、账本记录和交付物一并丢掉。
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
                # 停止后不再刷 UI 卡片，但结果照常入账（诚实记录优先于界面安静）
                if not self._stop_requested:
                    self.tool_finished.emit({
                        "name": name,
                        "result_preview": str(result_str)[:200],
                        "index": idx,
                        "success": _okc,
                        "duration_ms": dt,
                    })
                self._handle_tool_result(
                    tc, name, fn, result_str, deliverables, schedule,
                    tool_result=_tr)

        # v4.108 H-03 / v4.289.4：为所有未拿到回执的调用补占位（防下轮 API 400）。
        # 已执行的走上面的结算入口；落到这里的只有「停止后未提交」的调用。
        self._fill_missing_tool_receipts(
            _all_tc, "（系统提示：用户已停止任务，该工具调用未执行。）")
