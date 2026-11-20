# -*- coding: utf-8 -*-
"""v4.309.0：工具执行后结算链路修复的回归判据。

三层：
  A 纯函数层——execution_settle_309 决策逻辑（确定性、无 I/O）；
  B 源码接线层——agent / mixins / task_graph / agent_node 真实接线（AST + 文本）；
  C 内联桩层——不碰真实用户数据，验证「未执行调用经真实 tool_outcome
                结算判失败」「工作流 guard ok=False」等关键链路。

旧代码（v4.308.0）无 execution_settle_309、未执行分支传 None，
探测式调用返回哨兵/源码判据读到裸分支 → 对应判据翻红。
"""
import os
import sys
import tempfile
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_309_settle_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_WORKSPACE_DIR"] = os.path.join(_SBX, "ws")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


class _Sentinel:
    def __getitem__(self, k): return self
    def __eq__(self, o): return False
    def __ne__(self, o): return True
    def __bool__(self): return False
    def __iter__(self): return iter((self, self))
    def __len__(self): return 2
    def __repr__(self): return "<OLD-CODE-SENTINEL>"


_S = _Sentinel()


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def probe(mod, fn, *a, **kw):
    if mod is None: return _S
    f = getattr(mod, fn, None)
    if f is None: return _S
    try: return f(*a, **kw)
    except Exception: return _S


try:
    import execution_settle_309 as G
except Exception:
    G = None


def read_src(name):
    try: return (ROOT / name).read_text(encoding="utf-8")
    except Exception: return ""


AGENT_SRC = read_src("agent.py")
RES_SRC = read_src("agent_result_mixin.py")
EXEC_SRC = read_src("agent_exec_mixin.py")
TASK_SRC = read_src("agent_task_mixin.py")
GRAPH_SRC = read_src("task_graph.py")
NODE_SRC = read_src("agent_node.py")
G_SRC = read_src("execution_settle_309.py")  # v4.310.0 B20：unexecuted 归一登记


# ============ A 纯函数层 ============
def part_a():
    print("== A 纯函数层 ==")
    # P1-1/P2-1 未执行结算
    check("A1 拒绝码归一", probe(G, "not_executed_code", "denied") == "DENIED")
    check("A2 取消码归一", probe(G, "not_executed_code", "用户取消") == "CANCELLED")
    check("A3 停止码归一", probe(G, "not_executed_code", "已停止") == "STOPPED")
    check("A4 崩溃码归一", probe(G, "not_executed_code", "crash") == "CRASH")
    check("A5 去重码归一", probe(G, "not_executed_code", "批内去重") == "DUPLICATE_SKIPPED")
    check("A6 未知原因 fail-closed", probe(G, "not_executed_code", "????") == "NOT_EXECUTED")
    check("A7 DENIED 识别为未执行", probe(G, "is_not_executed", "DENIED") is True)
    check("A8 普通错误码不算未执行", probe(G, "is_not_executed", "POST_VERIFY_FAILED") is False)
    check("A9 未执行+假 ok 仍判失败", probe(G, "should_settle_fail", True, "DENIED") is True)
    check("A10 executed=False 判失败", probe(G, "should_settle_fail", True, None, False) is True)
    check("A11 真成功 ok=True 判成功", probe(G, "should_settle_fail", True, None, True) is False)
    check("A12 未执行不登记签名", probe(G, "should_register_sig", True, "CANCELLED", True) is False)
    check("A13 占位(无 tool_result)不登记签名", probe(G, "should_register_sig", True, None, False) is False)
    check("A14 真成功才登记签名", probe(G, "should_register_sig", True, None, True) is True)
    check("A15 失败账本记 failed", probe(G, "ledger_status", True) == "failed")
    check("A16 成功账本记 done", probe(G, "ledger_status", False) == "done")
    check("A17 failed 不进 done_hashes", probe(G, "ledger_done_hash_ok", "failed") is False)
    check("A18 done 进 done_hashes", probe(G, "ledger_done_hash_ok", "done") is True)

    # P1-2 工作流
    check("A19 早退一律 ok=False", probe(G, "workflow_guard_ok", False, True, True) is False)
    check("A20 未执行 ok=False", probe(G, "workflow_guard_ok", False, False, False) is False)
    check("A21 incomplete ok=False", probe(G, "workflow_guard_ok", "waiting", True, False) is False)
    check("A22 全过才 ok=True", probe(G, "workflow_guard_ok", None, True, False) is True)
    check("A23 工具全失败+有正文→完整(v4.312放宽)", probe(G, "node_should_incomplete", 2, True, True) is False)
    check("A23b 工具全失败+无正文→incomplete(防线)", probe(G, "node_should_incomplete", 2, True, False) is True)
    check("A24 工具成功+有正文→完整", probe(G, "node_should_incomplete", 0, True, True) is False)
    check("A25 模型调用失败→incomplete", probe(G, "node_should_incomplete", 0, True, True, True) is True)
    check("A26 全节点 completed 才算完成", probe(G, "workflow_finalize_ok", ["completed", "completed"]) is True)
    check("A27 有 cancelled 节点→未完成", probe(G, "workflow_finalize_ok", ["completed", "cancelled"]) is False)
    check("A28 空节点列表 fail-closed", probe(G, "workflow_finalize_ok", []) is False)

    # P1-3 续跑
    check("A29 执行工具后应复位 force", probe(G, "continuation_should_reset_force", True) is True)
    check("A30 没执行工具不复位", probe(G, "continuation_should_reset_force", False) is False)
    check("A31 token 熔断必须 mark_resumable", probe(G, "token_break_should_mark_resumable") is True)

    # P2-3 结局
    check("A32 账本未完成→非success", probe(G, "infer_outcome_success", False, False, True, True) is False)
    check("A33 工作流未完成→非success", probe(G, "infer_outcome_success", True, True, True, True) is False)
    check("A34 产物未满足→非success", probe(G, "infer_outcome_success", True, False, False, True) is False)
    check("A35 没调工具→非success", probe(G, "infer_outcome_success", True, False, True, False) is False)
    check("A36 全满足→success", probe(G, "infer_outcome_success", True, False, True, True) is True)
    check("A37 硬编码180判不合规", probe(G, "timeout_is_configured", True, 600) is False)
    check("A38 配置预算合规", probe(G, "timeout_is_configured", False, 600) is True)
    check("A39 无预算 fail-closed", probe(G, "timeout_is_configured", False, None) is False)

    # P2-5 / P3-1 / P3-2
    check("A40 cancelled 上游触发下游 skip", probe(G, "should_skip_dependents", "cancelled") is True)
    check("A41 failed 上游触发下游 skip", probe(G, "should_skip_dependents", "failed") is True)
    check("A42 incomplete 上游触发下游 skip", probe(G, "should_skip_dependents", "incomplete") is True)
    check("A43 completed 上游不触发 skip", probe(G, "should_skip_dependents", "completed") is False)
    check("A44 兄弟调用诚实标注未执行", probe(G, "sibling_call_label_is_honest", "该调用未执行") is True)
    check("A45 旧『无需执行』文案判不诚实", probe(G, "sibling_call_label_is_honest", "无需单独执行") is False)
    check("A46 缺失依赖不就绪", probe(G, "is_ready_with_missing_dep", ["ghost"], {}) is False)
    check("A47 依赖齐全就绪", probe(G, "is_ready_with_missing_dep", ["a"], {"a": object()}) is True)


# ============ B 源码接线层 ============
def part_b():
    print("== B 源码接线层 ==")
    # P1-1：串行未执行分支构造结构化失败（经 _unexecuted_result + reason 归一）
    # 用 AST 级判据核对 _run_serial 内确实调用 _unexecuted_result（防字符串假绿）
    check("B1 串行未执行分支构造结构化失败(_run_serial)",
          probe(G, "function_has_call", AGENT_SRC, "_run_serial", "_unexecuted_result"))
    check("B2 串行覆盖 denied/cancelled/stopped/duplicate 四种原因",
          all('"denied"' in AGENT_SRC or "'denied'" in AGENT_SRC for _ in [0]) and
          all(r in AGENT_SRC for r in ('"denied"', '"cancelled"', '"stopped"', '"duplicate"')))
    check("B3 崩溃分支构造结构化失败(_invoke_tool)",
          probe(G, "function_has_call", EXEC_SRC, "_invoke_tool", "_unexecuted_result"))
    check("B4 exec_mixin 崩溃原因=crash",
          '"crash"' in EXEC_SRC or "'crash'" in EXEC_SRC)
    check("B5 并发去重构造结构化失败(_run_concurrent)",
          probe(G, "function_has_call", EXEC_SRC, "_run_concurrent", "_unexecuted_result"))
    check("B6 两处去重原因=duplicate",
          AGENT_SRC.count('"duplicate"') >= 1 and EXEC_SRC.count('"duplicate"') >= 1)
    check("B7 结算层真实调用 _es309.should_register_sig",
          "_es309.should_register_sig(" in RES_SRC)

    # P1-2 工作流外层/内层
    check("B8 guard 拒绝分支经 _wf_early 标失败",
          '_wf_early(self, "denied", f"（子代理工作流未执行' in AGENT_SRC and
          "not_executed_code" in EXEC_SRC)
    check("B9 外层 wf_ok 用专属判据(非只看 incomplete)",
          "_wf_outcome_ok(self)" in AGENT_SRC and "workflow_guard_ok" in EXEC_SRC)
    check("B10 AgentNode 工具失败累计并设 INCOMPLETE_FLAG",
          probe(G, "function_has_call", NODE_SRC, "run", "node_should_incomplete") and
          "INCOMPLETE_FLAG" in NODE_SRC and "_tc_failed" in NODE_SRC)
    check("B11 _finalize_workflow 识别四类非成功状态",
          all(s in AGENT_SRC for s in ('"failed"', '"skipped"', '"incomplete"', '"cancelled"')))

    # P1-3 续跑（续跑循环内联在 run() 中，用 AST 定位 _force_next=False 赋值 +
    # continuation_should_reset_force 判据接线；token 分支的 mark_resumable("token_budget")）
    check("B12 续跑接线 force 复位 helper + force=False 赋值",
          "_reset_force_after_tool(self)" in AGENT_SRC and
          "continuation_should_reset_force" in EXEC_SRC and
          probe(G, "function_assigns_in", EXEC_SRC, "reset_force_after_tool", "_force_next", False))
    check("B13 续跑 token 中断 mark_resumable(token_budget)",
          '_mark_resumable("token_budget")' in AGENT_SRC)

    # P2-3 结局
    check("B14 _infer_outcome 接线 success 闸门 + 读 _effective_duration_budget",
          "_infer_success_ok(self" in AGENT_SRC and "infer_outcome_success" in EXEC_SRC and
          "_effective_duration_budget" in AGENT_SRC)
    check("B15 超时去硬编码180(用 duration_over_budget 判据)",
          "duration_s > 180" not in AGENT_SRC and
          "_duration_over_budget" in AGENT_SRC and "timeout_is_configured" in EXEC_SRC)

    # P2-5 取消传播 / P3-2 依赖校验
    check("B16 取消传播接线 should_skip_dependents + has_failed 含 cancelled",
          "should_skip_dependents" in GRAPH_SRC and
          'has_failed = any(t.status in ("failed", "cancelled")' in GRAPH_SRC)
    check("B17 is_ready 校验缺失依赖",
          "is_ready_with_missing_dep" in GRAPH_SRC)
    check("B18 卡片 started/finished 成对(停止也发 finished)",
          "_final_ok = (_okc and not self._stop_requested)" in EXEC_SRC)
    check("B19 兄弟调用文案诚实标未执行",
          "**未执行**" in AGENT_SRC and "无需单独执行" not in AGENT_SRC)

    # v4.310.0（第三方复核 P2-1/P2-2/P3-1/P3-2 四项收尾）
    check("B20 P2-1 未知工作流类型经 _wf_early 标失败(非 return '')",
          '_wf_early(self, "unexecuted"' in AGENT_SRC and
          '"unexecuted": "NOT_EXECUTED"' in G_SRC)
    check("B21 P2-2 续跑 break 过收尾闸门+账本小结(主循环之外唯一接线)",
          "_tstate_resume_finish(" in AGENT_SRC and
          "def _tstate_resume_finish" in TASK_SRC and
          "_tstate_nudge_now(" in TASK_SRC and
          "self._loop_summary(agent_loop_mod)" in TASK_SRC)
    check("B22 P3-1 并发 future 异常构造结构化失败(crash)",
          '_unexecuted_result(str(e), "crash")' in EXEC_SRC and
          '"crash"' in EXEC_SRC)
    check("B23 P3-2 异常兜底补发 finished 关闭开放卡片",
          probe(G, "function_has_call", EXEC_SRC, "_safe_exec_tool_calls",
                "_close_open_tool_cards") and
          "_tool_open_cards" in EXEC_SRC and
          "_open[idx] = name" in EXEC_SRC)
    # v4.311.0（第三方复验 P3-2 未生效）：补发必须用**原名**（{idx:name}），
    # 否则 UI rec["name"]==name 不匹配 → 新建卡、原卡照旧 running（v4.310 用假名踩坑）。
    check("B23b P3-2 补发用登记原名({idx:name} 而非硬编码)",
          "_open[idx] = name" in EXEC_SRC and
          "for _idx, _nm in list(_open.items()):" in EXEC_SRC and
          "_emit_tool_finished(_nm, result_str, _idx, 0, False)" in EXEC_SRC)
    # v4.311.0：续跑收尾闸门给 _tstate_nudge_now 传账本对象而非模块形参（防以后踩空）
    check("B24 续跑收尾闸门传 _tstate 账本对象(非模块形参)",
          "self._tstate_nudge_now(self._tstate, step, max_steps)" in TASK_SRC)


# ============ C 内联桩层（不碰真实用户数据） ============
def part_c():
    print("== C 内联桩层 ==")
    try:
        import tool_contract as tc
    except Exception as e:
        check("C0 tool_contract 可导入", False, e); return
    check("C0 tool_contract 可导入", True)

    def fail_checker(name, s):
        # 复刻 agent._tool_result_looks_failed 的关键行为
        marks = ("[RESULT NOT FOUND]", "文件不存在", "抓取失败", "timeout", "Traceback")
        return any(m in str(s or "")[:400] for m in marks)

    # C1-C6：报告实测的 6 条文案，走真实 tool_outcome，传结构化失败码后必须判失败
    settled = [
        ("用户取消了该操作", "CANCELLED"),
        ("已停止（用户请求）", "STOPPED"),
        ("仅讨论模式：不执行任何操作", "DENIED"),
        ("规划模式：仅允许只读，不实际执行写入/命令", "DENIED"),
        ("（子代理工作流未执行：权限引擎未启用）", "WORKFLOW_NOT_EXECUTED"),
        ("工具执行崩溃：boom", "CRASH"),
    ]
    for i, (text, code) in enumerate(settled, 1):
        tr = tc.ToolResult.fail(text, error_code=code)
        ok, _err = tc.tool_outcome(tr, "write_file", text, fail_checker)
        check("C%d 未执行文案[%s]判失败" % (i, code), ok is False, (ok, _err))

    # C7：对照——真的成功 ToolResult 仍判成功（没回归）
    tr_ok = tc.ToolResult.ok_result("已写入文件")
    ok, _ = tc.tool_outcome(tr_ok, "write_file", "已写入文件", fail_checker)
    check("C7 真成功仍判成功", ok is True)

    # C8：对照——没传 ToolResult 的旧行为保持不变（零回归保证）
    ok, _ = tc.tool_outcome(None, "write_file", "文件不存在：/x", fail_checker)
    check("C8 旧占位路径仍按文案判失败", ok is False)


def main():
    print("=== A 纯函数层 ==="); part_a()
    print("=== B 源码接线层 ==="); part_b()
    print("=== C 内联桩层 ==="); part_c()
    print("\n汇总：PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
