# -*- coding: utf-8 -*-
"""v4.309.0 — 工具「执行后结算链路」的确定性纯判据（无第三方依赖、无真实 I/O）。

对应第三方只读审查报告（工具调用链路）——核心结论：权限闸门本身无
「拒绝后仍执行」的绕过，问题集中在**执行后的结算层**：被拒绝/取消/停止/
未执行/崩溃/去重的调用，因为传 `tool_result=None` 退回文案判断，被统一
记成成功，进而污染 UI 绿勾、证据链(ok=True)、任务账本(succeeded_tools)、
幂等账本(exec_ledger=done)、签名登记和断点续跑。

覆盖的判定点（每个都是纯函数 / 源码 AST 判据）：
  P1-1 未执行调用必须判失败      → not_executed_code / should_settle_fail / should_register_sig
  P2-1 批内去重不得洗白失败      → (同上，DUPLICATE_SKIPPED 进未执行集合)
  P2-2 断点账本同步              → ledger_done_hash_ok
  P1-2 工作流双层 ok 判定        → workflow_guard_ok / node_should_incomplete
  P1-3 续跑自锁复位              → continuation_should_reset_force / token_break_should_mark_resumable
  P2-3 结局推断                  → infer_outcome_success / timeout_is_configured
  P2-4 卡片成对                  → (源码判据 count_unpaired_started)
  P2-5 取消向下游传播            → should_skip_dependents
  P3-1 同批兄弟调用标注          → sibling_call_label_is_honest
  P3-2 缺失依赖校验              → is_ready_with_missing_dep

设计纪律（沿用 v4.297/303/304/305/306/307/308）：
  · 纯函数、无真实网络/文件/用户数据访问；
  · 业务模块（agent / agent_result_mixin / agent_exec_mixin / task_graph /
    agent_node）只接线这些判定，不在业务里散落 if；
  · fail-closed：判不准一律按「失败/未完成」处理，绝不轻易判成功。
"""

# ==================== P1-1 / P2-1 结算层 ====================
# 未执行的机器可读错误码：这些码代表的调用**根本没有真实执行**，
# 一律不得进 succeeded_tools / exec_ledger=done / 签名登记 / 证据 ok=True。
NOT_EXECUTED_CODES = frozenset({
    "DENIED",          # 权限引擎拒绝（讨论/规划模式、路径越界）
    "CANCELLED",       # 用户在确认框取消
    "STOPPED",         # 用户请求停止后不再执行
    "CRASH",           # exec_tool 抛异常，根本没得到结果
    "DUPLICATE_SKIPPED",  # 批内重复调用被去重，未真实执行
    "NOT_EXECUTED",    # 兜底：其它未执行原因
})

# 原因 → 错误码归一表（业务分支用语义名，这里统一映射）
_REASON_TO_CODE = {
    "denied": "DENIED", "拒绝": "DENIED", "权限拒绝": "DENIED",
    "cancelled": "CANCELLED", "cancel": "CANCELLED", "取消": "CANCELLED",
    "用户取消": "CANCELLED",
    "stopped": "STOPPED", "stop": "STOPPED", "停止": "STOPPED", "已停止": "STOPPED",
    "crash": "CRASH", "崩溃": "CRASH", "异常": "CRASH", "执行崩溃": "CRASH",
    "duplicate": "DUPLICATE_SKIPPED", "去重": "DUPLICATE_SKIPPED",
    "重复": "DUPLICATE_SKIPPED", "批内去重": "DUPLICATE_SKIPPED",
    "not_executed": "NOT_EXECUTED", "未执行": "NOT_EXECUTED",
    "unexecuted": "NOT_EXECUTED",  # v4.310.0 P2-1：未知工作流类型（早退）
}


def not_executed_code(reason):
    """把「未执行原因」归一成机器可读 error_code。

    未知原因 fail-closed 归 NOT_EXECUTED（宁可多判失败，不可漏判成功）。
    """
    return _REASON_TO_CODE.get(str(reason or "").strip().lower(), "NOT_EXECUTED")


def is_not_executed(code):
    """error_code 是否属于「没真实执行」集合。"""
    return str(code or "").strip().upper() in NOT_EXECUTED_CODES


def should_settle_fail(ok, code=None, executed=None):
    """一次工具调用是否应结算为失败。

    - 带未执行错误码 → 必失败（即使文案看起来成功）；
    - 显式 executed=False → 必失败；
    - 否则按结构化 ok。fail-closed：ok 非布尔一律当失败。
    """
    if code and is_not_executed(code):
        return True
    if executed is False:
        return True
    return not (ok is True)


def should_register_sig(ok, code=None, has_tool_result=True):
    """是否应登记「成功签名」供死循环护栏使用。

    只有：真的走过 exec_tool（has_tool_result）+ 结构化 ok=True + 非未执行码，
    才允许登记。这挡住 P1-1/P2-1 的「假成功签名污染后续合法重试」。
    """
    if not has_tool_result:
        return False
    if code and is_not_executed(code):
        return False
    return ok is True


def ledger_status(failed):
    """幂等账本的 side_effect_status。失败/未执行一律 failed，不进 done_hashes。"""
    return "failed" if failed else "done"


def ledger_done_hash_ok(status):
    """恢复时是否把该账本条目算进「已执行完成」哈希集合。

    只有 done 才算——failed（含所有未执行码）必须允许重试。
    """
    return str(status or "").lower() == "done"


# ==================== P1-2 工作流双层 ====================
def workflow_guard_ok(incomplete=False, executed=True, early_return=False):
    """工作流外层结果 ok 判定（_run_workflow_guarded 的结论）。

    - early_return=True（缺权限引擎/权限拒绝/判定异常/用户取消/未知类型）
      → 必 False，哪怕返回的是字符串文案；
    - 未真实执行 → False；
    - _workflow_incomplete 非空 → False；
    - 三者都过才 True。fail-closed。
    """
    if early_return:
        return False
    if executed is not True:
        return False
    if incomplete:
        return False
    return True


def node_should_incomplete(tool_failed=0, tool_called=False, has_text=False,
                           model_call_failed=False):
    """AgentNode 是否应标记 INCOMPLETE_FLAG。

    报告核心场景：本轮有工具调用且**全部失败**，模型却补了正文就 break，
    TaskGraph 只看 status 标 completed。
    v4.311.0 之前：只要有工具失败（哪怕模型补了正文）就标 incomplete（fail-closed），
    会让「失败一次后换法成功并交付」的节点误标未完整。
    v4.312.0 调整：以「是否真正交付正文」为核心——模型交付了非空正文
    （换法成功并交付）即认完整；无正文交付才判 incomplete。保留三条防线：
    - 模型调用本身失败 → incomplete；
    - 有工具调用且失败数 > 0 且无正文 → incomplete；
    - 无任何工具调用且无正文 → incomplete。
    单纯「有正文且工具都成功」不算 incomplete。
    """
    if model_call_failed:
        return True
    if tool_called and int(tool_failed or 0) > 0 and not has_text:
        return True
    if not tool_called and not has_text:
        return True
    return False


def workflow_finalize_ok(node_statuses):
    """_finalize_workflow：只有全部节点都 completed 才算真正完成。

    fail-closed：空列表不算成功；任一 failed/skipped/incomplete/cancelled
    都判未完整。
    """
    statuses = [str(s or "").lower() for s in (node_statuses or [])]
    if not statuses:
        return False
    return all(s == "completed" for s in statuses)


# ==================== P1-3 续跑自锁 ====================
def continuation_should_reset_force(tool_executed=False):
    """续跑循环每轮执行工具后，是否应复位 _force_next。

    True → 调用方须置 _force_next=False、_force_retries=0，
    否则 tool_choice=required 永久自锁，模型无法进入纯文本收尾。
    """
    return tool_executed is True


def token_break_should_mark_resumable():
    """续跑 token 预算中断是否必须调 _mark_resumable('token_budget')。

    常量返回，供源码判据核对调用方确实接了这条；True 表示「必须」。
    """
    return True


# ==================== P2-3 结局推断 ====================
def infer_outcome_success(tstate_complete=True, wf_incomplete=False,
                          deliverables_ok=True, tool_called=True):
    """success 结局的严格「且」判定。

    任务账本未完成 / 工作流未完整 / 产物未满足 / 本轮没真调用工具，
    任一条不满足都不能记 success（否则污染 success few-shot 与记忆）。
    """
    if tstate_complete is not True:
        return False
    if wf_incomplete:
        return False
    if deliverables_ok is not True:
        return False
    if tool_called is not True:
        return False
    return True


def timeout_is_configured(hardcoded_180=False, budget_sec=None):
    """超时判定是否用配置预算而非硬编码 180 秒。"""
    if hardcoded_180:
        return False
    return budget_sec is not None and float(budget_sec) > 0


# ==================== P2-5 取消向下游传播 ====================
# 上游进入这些终态时，下游依赖应立即 skipped，不能留在 pending 等成死锁。
_TERMINAL_SKIP_STATUSES = frozenset({"failed", "cancelled", "incomplete"})


def should_skip_dependents(upstream_status):
    """上游终态是否应触发下游 skipped（修「取消下游误报死锁」）。

    原实现只处理 failed；cancelled/incomplete 也会让下游永不可达。
    """
    return str(upstream_status or "").lower() in _TERMINAL_SKIP_STATUSES


# ==================== P3-1 同批兄弟调用 ====================
def sibling_call_label_is_honest(text):
    """被 run_workflow 取代的同批兄弟调用，文案是否诚实标注「未执行」。

    旧文案「无需单独执行」会让账本/用户以为执行过；应明确写「未执行」。
    """
    s = str(text or "")
    return ("未执行" in s) and ("已自动去重" not in s)


# ==================== P3-2 缺失依赖 ====================
def is_ready_with_missing_dep(blocked_by, task_map):
    """存在缺失依赖 ID 时，任务是否仍可被 is_ready 判为就绪。

    期望 False：blocked_by 引用不存在的任务 → 不得静默放行，必须校验报错。
    """
    missing = [bid for bid in (blocked_by or []) if bid not in (task_map or {})]
    return not missing  # 有缺失 → 不就绪（修「把缺失依赖当已满足」）


# ==================== 源码 AST 判据（接线检查，防假绿） ====================
def count_toolresult_fail_in(src, marker_code):
    """源码里是否出现构造带指定 error_code 的 ToolResult.fail / NOT_EXECUTED 码。

    用于核对业务分支真的构造了结构化失败结果，而不是只改文案。
    """
    return str(src or "").count(marker_code)


def function_has_call(module_src, func_name, call_name):
    """指定函数体内是否出现对 call_name 的调用（AST 级，防字符串假绿）。"""
    import ast
    try:
        tree = ast.parse(module_src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == func_name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    if (isinstance(f, ast.Name) and f.id == call_name) or \
                       (isinstance(f, ast.Attribute) and f.attr == call_name):
                        return True
    return False


def function_assigns_in(module_src, func_name, attr_name, value_repr=None):
    """指定函数体内是否对 self.<attr_name> 赋值（可选匹配字面量）。

    用于核对续跑循环复位 _force_next=False、token 熔断 mark_resumable 等。
    """
    import ast
    try:
        tree = ast.parse(module_src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == func_name:
            for sub in ast.walk(node):
                if not isinstance(sub, (ast.Assign, ast.AugAssign)):
                    continue
                targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                for t in targets:
                    if isinstance(t, ast.Attribute) and t.attr == attr_name:
                        if value_repr is None:
                            return True
                        try:
                            if ast.literal_eval(sub.value) == value_repr:
                                return True
                        except Exception:
                            continue
    return False
