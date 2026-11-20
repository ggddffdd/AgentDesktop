# -*- coding: utf-8 -*-
"""v4.306.0 — MCP 客户端 F-06/F-07 的确定性纯判据（无第三方依赖）。

对应第三方审查报告（小臭玩AI_代码审查报告_v4.303.0.docx）：
  F-06 MCP 子进程 stderr 从不读取、stdin 写入未串行化
       → stderr_policy / serialized_write_ok / pending_result
  F-07 MCP 初始化在 QApplication 创建前同步阻塞（每服务器最多两个 30s 等待）
       → init_should_be_background / server_budget_ok / split_budget

另外收录一个报告未提、但位于 F-06 同一代码路径的真 bug：
  mcp_client._send_request 末尾 `return event["result"]` 对 threading.Event
  下标访问，server 一旦正常回包即抛 TypeError → pending_result 判据覆盖。

设计纪律（沿用 v4.297/303/304/305）：
  · 纯函数 / 仅依赖注入的确定性逻辑，不读真实用户数据、不依赖网络、不启线程；
  · 业务模块（mcp_client / main / ui）只接线，不放判定细节；
  · fail-closed：判不准就按红处理。
"""

import ast

# ---- F-06 常量 ----
# stderr 允许的处理方式：独立 drain 线程（保留尾部日志）或 DEVNULL。
# 裸 subprocess.PIPE 而无人读 = 禁止（写满 4~64KB 后子进程卡死）。
STDERR_DRAIN = "drain"
STDERR_DEVNULL = "devnull"
STDERR_RAW_PIPE = "raw_pipe"
_ALLOWED_STDERR = frozenset({STDERR_DRAIN, STDERR_DEVNULL})

# 单服务器总预算（initialize + tools/list 合计）上限，秒。报告验收口径 ≤5s。
MAX_SERVER_BUDGET_SEC = 5.0
DEFAULT_SERVER_BUDGET_SEC = 5.0


# ==================== F-06a stderr ====================
def stderr_policy(mode):
    """stderr 处理方式是否安全。

    mode 取值见 STDERR_* 常量。裸 PIPE 无人读会让子进程写满管道后阻塞，
    进而无法处理 stdin 请求（表现为 MCP 偶发全部超时）。只允许 drain/devnull。
    """
    return mode in _ALLOWED_STDERR


def stderr_tail_kept(length, keep_bytes):
    """drain 线程保留的尾部日志是否落在给定上限内（防止无限占内存）。

    length 为已累计字节数，keep_bytes 为环形缓冲上限；保留量不得超过上限，
    且上限本身必须为正数。这里返回"是否会按上限截断"——非正数上限判红。
    """
    if keep_bytes <= 0:
        return False
    return length > keep_bytes  # True 表示需要截断（drain 逻辑应做）


# ==================== F-06b 写串行化 ====================
def serialized_write_ok(write_guarded, flush_in_same_guard):
    """整条 JSON 的 write + flush 是否在同一把锁内串行化。

    两个布尔都来自源码接线（write 在 with lock 内、flush 也在同一 with 内）。
    只有两者都为真，才保证 5 路并发 tools/call 不会交错成半行 JSON。
    """
    return bool(write_guarded) and bool(flush_in_same_guard)


# ==================== F-06c pending 结果取回（含 event bug） ====================
def pending_result(pending_entry, event_obj=None):
    """从 pending 表项取回 server 响应。

    reader 线程把响应写在 entry["result"]；event 只是唤醒信号（threading.Event），
    不能被下标访问。传入 event_obj 且它不支持字符串下标时，正确实现必须忽略它、
    返回 entry["result"]。判据负责把"从 event 下标取"的错误实现挡在门外。
    """
    if not isinstance(pending_entry, dict):
        return None
    return pending_entry.get("result")


def event_is_subscriptable(event_obj):
    """探测 event 对象是否支持字符串下标——threading.Event 应为 False。

    供判据自检：若返回 True，说明传入的不是标准 Event，pending_result 的
    "忽略 event"策略是否必要需重新评估。正常接线 Threading.Event → False。
    """
    try:
        event_obj["result"]
        return True
    except Exception:
        return False


# ==================== F-07a 后台初始化 ====================
def init_should_be_background(in_background, after_qapplication):
    """MCP 初始化是否满足"不阻塞启动"。

    in_background: 初始化是否在后台线程/worker 执行（非主线程同步）；
    after_qapplication: 主线程是否已在初始化发起前构建 QApplication（或初始化
    根本不挡在主链上）。两者都真才不会造成冷启动白屏。
    """
    return bool(in_background) and bool(after_qapplication)


# ==================== F-07b 短超时 / 预算拆分 ====================
def server_budget_ok(total_sec):
    """单服务器 initialize + tools/list 总预算是否在上限内。"""
    try:
        return 0 < float(total_sec) <= MAX_SERVER_BUDGET_SEC
    except (TypeError, ValueError):
        return False


def split_budget(total_sec, init_ratio=0.5):
    """把单服务器总预算拆成 initialize / tools/list 两段超时（秒）。

    两段相加恰等于 total_sec，避免旧的 30s+30s=60s 线性叠加。init_ratio 限制在
    (0,1) 内；非法比例回退到对半分。返回 (init_timeout, tools_timeout)。
    """
    try:
        total = float(total_sec)
    except (TypeError, ValueError):
        total = DEFAULT_SERVER_BUDGET_SEC
    if total <= 0:
        total = DEFAULT_SERVER_BUDGET_SEC
    try:
        ratio = float(init_ratio)
    except (TypeError, ValueError):
        ratio = 0.5
    if not (0 < ratio < 1):
        ratio = 0.5
    init_t = round(total * ratio, 3)
    tools_t = round(total - init_t, 3)
    return init_t, tools_t


# ==================== 源码级接线判据（AST） ====================
def count_stderr_pipe_without_drain(source_text, func_name="start"):
    """统计指定函数里 stderr=PIPE 且没有配套 drain 线程的行数。

    返回计数：>0 表示存在"裸 PIPE 无人读"的风险。修好后应为 0。
    用 AST+源码文本混合判断：找到 Popen 调用里 stderr=subprocess.PIPE，
    再看函数体内是否出现 drain 线程启动（含 _drain_stderr / daemon=True）。
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return -1
    lines = source_text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            seg = "\n".join(lines[node.lineno - 1: getattr(node, "end_lineno", node.lineno)])
            if "stderr=subprocess.PIPE" in seg or "stderr = subprocess.PIPE" in seg:
                has_drain = ("_drain_stderr" in seg or "drain" in seg.lower()) and "daemon=True" in seg
                if not has_drain:
                    return 1
    return 0


def count_event_subscript_return(source_text):
    """统计 `return ...event[...]` 这种错误结果取回的行数，修好后应为 0。"""
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return -1
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Subscript):
            base = node.value.value
            if isinstance(base, ast.Name) and "event" in base.id.lower():
                n += 1
    return n


def count_locked_stdin_writes(source_text):
    """统计 `with self._write_lock:` 内包含 stdin.write 的代码块个数。

    纯字符串搜 `_write_lock` 会被 __init__ 定义 / 另一方法里的引用骗过（假绿）。
    必须按 AST 确认每个 stdin.write 都实际落在写锁上下文里。修好后应为 2
    （_send_request + _send_notification 各一处）。
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return -1
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        is_write_lock = False
        for item in node.items:
            ctx = item.context_expr
            if (isinstance(ctx, ast.Attribute) and ctx.attr == "_write_lock"):
                is_write_lock = True
        if not is_write_lock:
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "write"):
                n += 1
                break
    return n


def count_drain_starts(source_text):
    """统计以 _drain_stderr 为 target 的 Thread 启动个数（应为 1）。

    纯字符串搜 `_drain_stderr` 会被方法定义本身骗过。这里按 AST 找
    `Thread(target=self._drain_stderr, ...)` 调用。修好后应为 1。
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return -1
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "target" and isinstance(kw.value, ast.Attribute) \
                    and kw.value.attr == "_drain_stderr":
                n += 1
    return n
