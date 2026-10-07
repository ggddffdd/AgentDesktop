# -*- coding: utf-8 -*-
"""tests/test_toolresult_wiring_226.py —— v4.226 P1-2 硬判据

要钉住的事实
------------
`tools.exec_tool()` 自 v4.223 起返回 `ToolResult`（带 `ok` / `verified` /
`error_code` / `retryable`）。v4.226 之前，agent.py 的串行与并发两条路径都在
调用点**立刻解包成三元组**，字段全丢 → 后续成败判断退化成「看文案」。

而执行后验证（v4.224）的失败信号恰恰不在文案头部：

    ok=False
    error_code='POST_VERIFY_FAILED'
    msg='已写入 5 字符到 x\\n[执行后验证未通过] 同名进程仍残留3个'

`[执行后验证未通过]` 追加在**尾部**，`_TOOL_FAIL_MARKS` 只扫 `s[:400]` ——
正文超 400 字时连头都扫不到 → **验证失败被记成成功**（已复现）。

本判据分三层
------------
  A 行为层：`_tool_outcome` 的四条口径（含「字符串判不出失败但 ToolResult
    说失败」这条关键反例）
  B 接线层：AST 钉「两处 exec_tool 调用都接住了 ToolResult」+
    「两处 UI success 都取自 _okc」+「四处消费同一个判定真源」
  C 结构层：规模红线、mixin 混入、方法归属

判据自身的三条自证（防止「判据恒真」）
------------------------------------
  · A3 反例用**真实**的 agent 判据跑，证明它确实判不出失败 —— 否则
    「ToolResult 说失败」这一条就是拿一个本来就红的判据凑数
  · B 层用 AST 而不是字符串 find，避免注释里写个同名字符串就骗过
  · 每条 check 都带detail，出错时能看出到底哪一环断了
"""
import ast
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s%s" % (name, ("  <%s>" % detail) if detail else ""))


def _strip_comments(src):
    """剔除整行注释（AST 层判据不需要注释内容，故整行删即可）。"""
    return "\n".join(ln for ln in src.split("\n")
                     if not ln.lstrip().startswith("#"))


# ============================================================
# A 行为层：_tool_outcome 的判定口径
# ============================================================
print("\n--- A 行为层：_tool_outcome 判定口径 ---")
from agent_result_mixin import AgentResultMixin  # noqa: E402
import agent as _agent_mod  # noqa: E402


class _FakeWorker(AgentResultMixin):
    """最小宿主：只需 _TOOL_FAIL_MARKS + _tool_result_looks_failed。"""

    _TOOL_FAIL_MARKS = _agent_mod.AgentWorker._TOOL_FAIL_MARKS

    @classmethod
    def _tool_result_looks_failed(cls, name, result_str):
        return _agent_mod.AgentWorker._tool_result_looks_failed(
            name, result_str)


_W = _FakeWorker()


class _TR(object):
    """最小 ToolResult 替身（只带判定要读的字段）。"""

    def __init__(self, ok, msg="", error_code=None, error=None, verified=False):
        self.ok = ok
        self.msg = msg
        self.error_code = error_code
        self.error = error
        self.verified = verified


# A1 有 ok=True 的 ToolResult → 成功
_a1 = _W._tool_outcome(_TR(True, "已写入 5 字符"), "write_file",
                       "已写入 5 字符", _W._tool_result_looks_failed)
check("A1 ToolResult.ok=True → (True, None)", _a1 == (True, None), repr(_a1))

# A2 有 ok=False 的 ToolResult → 失败，且 err_head 带机器可读错误码
_a2 = _W._tool_outcome(
    _TR(False, "已写入 5 字符\n[执行后验证未通过] 残留3个",
        error_code="POST_VERIFY_FAILED", verified=True),
    "write_file",
    "已写入 5 字符\n[执行后验证未通过] 残留3个",
    _W._tool_result_looks_failed)
check("A2 ToolResult.ok=False → ok=False", _a2[0] is False, repr(_a2))
check("A2b err_head 含 error_code（机器可读优先）",
      "POST_VERIFY_FAILED" in (_a2[1] or ""), repr(_a2[1]))

# —— A3 关键反例：字符串判据**确实判不出**失败，但 ToolResult 说失败 ——
# 这一条若不先证明「旧判据在此场景本就失效」，A2 就只是拿一个本来就红的
# 判据凑数（MEMORY.md 二·20：MISS 四类里最常见的「期望串选错」）。
_a3_body = "已写入 5 字符到 output.txt\n[执行后验证未通过] 同名进程仍残留 3 个"
_a3_str_only = _W._tool_result_looks_failed("write_file", _a3_body)
_a3_long = _a3_body + ("x" * 600)          # 正文超 400 字
_a3_str_long = _W._tool_result_looks_failed("write_file", _a3_long)
_a3_full = _W._tool_outcome(
    _TR(False, _a3_body, error_code="POST_VERIFY_FAILED", verified=True),
    "write_file", _a3_body, _W._tool_result_looks_failed)
check("A3 前置自证：旧字符串判据在短正文下也判不出验证失败",
      _a3_str_only is False,
      "旧判据竟判出失败了 → A2 可能靠字符串就够，测不到真问题")
check("A3 前置自证：正文超 400 字时旧判据更判不出（标记在尾部被截掉）",
      _a3_str_long is False)
check("A3 同一场景：字符串判据=成功(假)，ToolResult 判据=失败(真)",
      _a3_full[0] is False,
      "ToolResult 判据也没判出失败 → 接线没生效: %r" % (_a3_full,))

# A4 非结构化（占位/去重/取消）→ 退回旧口径，结论不变
_a4_ok = _W._tool_outcome(None, "x", "（已去重）", _W._tool_result_looks_failed)
check("A4 占位串走旧口径 → 成功", _a4_ok == (True, None), repr(_a4_ok))
_a4_bad = _W._tool_outcome(None, "web_fetch", "抓取失败：超时",
                           _W._tool_result_looks_failed)
check("A4b 非结构化失败串走旧口径 → 失败",
      _a4_bad[0] is False and "抓取失败" in (_a4_bad[1] or ""), repr(_a4_bad))

# A5 fail-open：注入判据本身抛异常 → 按成功记（与 v4.225 同口径）


def _boom(name, s):
    raise RuntimeError("判据炸了")


_a5 = _W._tool_outcome(None, "x", "任意正文", _boom)
check("A5 注入判据抛异常 → fail-open 记成功（不得让主循环崩）",
      _a5 == (True, None), repr(_a5))

# A6 鸭子类型：任何带 .ok 的对象都算结构化结果（不认死类名）
class _Duck(object):
    ok = False
    msg = "duck"
    error_code = "DUCK_FAIL"


_a6 = _W._tool_outcome(_Duck(), "x", "duck", _W._tool_result_looks_failed)
check("A6 鸭子类型（只要求有 .ok）也被认作结构化结果",
      _a6[0] is False and "DUCK_FAIL" in (_a6[1] or ""), repr(_a6))

# A7 _tr_diag 不返回 ok（成败判定只允许一个真源）
_d = _W._tr_diag(_TR(False, "m", error_code="E", verified=True))
check("A7 _tr_diag 取诊断字段", _d.get("error_code") == "E"
      and _d.get("verified") is True, repr(_d))
check("A7b _tr_diag 刻意不返回 ok（避免两处判据）", "ok" not in _d, repr(_d))
check("A7c _tr_diag(None) → 空 dict", _W._tr_diag(None) == {})

# ============================================================
# B 接线层：AST 钉「ToolResult 没在中途被丢掉」
# ============================================================
print("\n--- B 接线层：AST 钉接线 ---")
_ag_path = os.path.join(ROOT, "agent.py")
_ag = open(_ag_path, encoding="utf-8").read()
_tree = ast.parse(_ag)


def _find_func(tree, fname, cls=None):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == fname:
                if cls is None:
                    return node
                for c in ast.walk(node):
                    pass
    return None


def _calls(node, fname):
    """函数体内对 fname 的调用点列表。"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            nm = None
            if isinstance(f, ast.Attribute):
                nm = f.attr
            elif isinstance(f, ast.Name):
                nm = f.id
            if nm == fname:
                out.append(n)
    return out


_serial = _find_func(_tree, "_run_serial")
_conc = _find_func(_tree, "_run_concurrent")
check("B1 能定位 _run_serial", _serial is not None)
check("B2 能定位 _run_concurrent", _conc is not None)

def _tuple_target_names(node):
    """`a, b, c = X` 在 AST 里是**单个 Tuple target**，不是三个 target。"""
    if len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple):
        return [e.id for e in node.targets[0].elts if isinstance(e, ast.Name)]
    return []


# B3 每条路径的 ToolResult 都必须先落到 `_tr` 再用。
# 串行：直接 `exec_tool(...)` 的返回值赋给 _tr。
# 并发：`exec_tool` 在闭包 `_exec_with_ctx` 里（AST 上是嵌套 FunctionDef），
#      ToolResult 是经 `future.result()` 拿回来的 —— 判据必须跟到那条链路，
#      否则会出现「判据扫不到 = 看起来没接线」的假红（本次实测踩过）。
for _tag, _fn in (("串行", _serial), ("并发", _conc)):
    if _fn is None:
        continue
    _execs = _calls(_fn, "exec_tool")
    check("B3 %s路径有 exec_tool 调用" % _tag, len(_execs) >= 1,
          "找到 %d 处" % len(_execs))
    if _tag == "串行":
        _assigned = 0
        for _p in ast.walk(_fn):
            if isinstance(_p, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_tr"
                    for t in _p.targets):
                if _calls(_p, "exec_tool"):
                    _assigned += 1
        check("B3 串行 路径：exec_tool 返回值都赋给了 _tr（字段没在中途丢）",
              _assigned == len(_execs), "%d/%d 处接住" % (_assigned, len(_execs)))
    else:
        #并发：future.result() 必须赋给 _tr（ToolResult 从闭包回到主循环的入口）
        _fut = _calls(_fn, "result")
        check("B3 并发 路径有 future.result() 取回结果", len(_fut) == 1,
              "找到 %d 处" % len(_fut))
        _f_assigned = False
        for _p in ast.walk(_fn):
            if isinstance(_p, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_tr"
                    for t in _p.targets):
                if _calls(_p, "result"):
                    _f_assigned = True
        check("B3 并发 路径：future.result() 的结果赋给了 _tr", _f_assigned,
              "并发路径的 ToolResult 没接住 → 字段在 as_completed 处丢失")
        # 且后续解包用的是 _tr（不是 future.result() 再解一次）
        _unpack_tr = any(
            isinstance(_n, ast.Assign)
            and _tuple_target_names(_n) == ["result_str", "deliverables", "schedule"]
            and isinstance(_n.value, ast.Name) and _n.value.id == "_tr"
            for _n in ast.walk(_fn))
        check("B3 并发 路径：三元组从 _tr 解包（不再调 future.result()）",
              _unpack_tr)

# B4 tool_finished 的 success 必须取自 _okc（不再看文案前缀）
# 本项目写法是 `self.tool_finished.emit({...dict字面量...})` —— **不是 kwargs**，
# 所以要查 dict 字面量里的 "success" 键（查 keywords 会永远扫不到= 假红，
# 本次实测踩过：okc=0 仍用其他=0 就是这个原因）。
_okc_attrs = 0
_success_other = 0
for _fn in (_serial, _conc):
    if _fn is None:
        continue
    for _n in ast.walk(_fn):
        if not (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
                and _n.func.attr == "emit"):
            continue
        if getattr(_n.func.value, "attr", "") != "tool_finished":
            continue
        _d = _n.args[0] if _n.args else None
        if not (isinstance(_d, ast.Dict)):
            continue
        for _k, _v in zip(_d.keys, _d.values):
            if isinstance(_k, ast.Constant) and _k.value == "success":
                if isinstance(_v, ast.Name) and _v.id == "_okc":
                    _okc_attrs += 1
                else:
                    _success_other += 1
check("B4 两处 tool_finished 的 success 都取自 _okc",
      _okc_attrs == 2 and _success_other == 0,
      "okc=%d 仍用其他表达式=%d" % (_okc_attrs, _success_other))

# B5 _okc 必须来自 _tool_outcome（唯一判定真源）
_okc_src = 0
for _fn in (_serial, _conc):
    if _fn is None:
        continue
    for _n in ast.walk(_fn):
        if (isinstance(_n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_okc"
                        for t in _n.targets)):
            if _calls(_n, "_tool_outcome"):
                _okc_src += 1
check("B5 _okc 的来源是 _tool_outcome（唯一判定真源）", _okc_src == 2,
      "找到 %d 处" % _okc_src)

# B6 agent.py 里不应再有「当场解包 exec_tool 成三元组」的写法
# （同样是 Tuple target，故复用 _tuple_target_names）
_bad_unpack = 0
for _fn in (_serial, _conc):
    if _fn is None:
        continue
    for _n in ast.walk(_fn):
        if not isinstance(_n, ast.Assign):
            continue
        if _tuple_target_names(_n) != ["result_str", "deliverables", "schedule"]:
            continue
        _f = _n.value.func if isinstance(_n.value, ast.Call) else None
        _nm = _f.attr if isinstance(_f, ast.Attribute) else getattr(
            _f, "id", "") if _f is not None else ""
        if _nm in ("exec_tool", "result"):
            _bad_unpack += 1
check("B6 agent.py 不再把 exec_tool 直接解包成三元组",
      _bad_unpack == 0, "仍有 %d 处" % _bad_unpack)

# B7 _handle_tool_result 的定义已不在 agent.py（在 mixin 里）
check("B7 _handle_tool_result 已搬出 agent.py",
      _find_func(_tree, "_handle_tool_result") is None)

# ============================================================
# C 结构层：mixin 混入 + 规模红线 + 方法归属
# ============================================================
print("\n--- C 结构层：混入与红线 ---")
_cls = next((n for n in ast.walk(_tree)
             if isinstance(n, ast.ClassDef) and n.name == "AgentWorker"), None)
check("C1 能定位 AgentWorker 类", _cls is not None)
if _cls is not None:
    _bases = [ast.unparse(b) for b in _cls.bases]
    check("C2 AgentWorker 混入 AgentResultMixin",
          "AgentResultMixin" in _bases, repr(_bases))
_rm_path = os.path.join(ROOT, "agent_result_mixin.py")
check("C3 agent_result_mixin.py 存在", os.path.isfile(_rm_path))
_rm_tree = ast.parse(open(_rm_path, encoding="utf-8").read())
_rmf = _find_func(_rm_tree, "_handle_tool_result")
check("C4 _handle_tool_result 定义在 mixin 里", _rmf is not None)
_rmo = _find_func(_rm_tree, "_tool_outcome")
check("C5 _tool_outcome 定义在 mixin 里", _rmo is not None)
check("C6 _tr_err_head 定义在 mixin 里",
      _find_func(_rm_tree, "_tr_err_head") is not None)

# 规模红线：v4.216 立的 D2，阈值**不动**
_ag_lines = len(_ag.split("\n"))
check("C7 agent.py 守住拆分红线 < 2400 行", _ag_lines < 2400,
      "当前 %d 行" % _ag_lines)

# mixin 记账接线把 tool_result 透传下去（v4.226 的实际修复点）
# 注意：这里读**原文**而非剔注释版—— 「..., tool_result=None):」后面跟着
# 行尾注释，整行剔除器不会误伤它，但若签名被拆成多行、行尾注释位置变化，
# 剔注释更稳。两条都查，任一成立即通过（判据只要求「接线存在」）。
_mx_raw = open(os.path.join(ROOT, "agent_task_mixin.py"),
               encoding="utf-8").read()
_mx_code = _strip_comments(_mx_raw)
_rm_raw = open(_rm_path, encoding="utf-8").read()
check("C8 _tstate_record 接受 tool_result 参数",
      "tool_result=None" in _mx_code or "tool_result=None" in _mx_raw,
      "记账若不接 tool_result → 账本仍按文案记 ok")
check("C8b _handle_tool_result 把 tool_result 透传给记账",
      "tool_result=tool_result" in _rm_raw,
      "记账若拿不到 ToolResult → 账本仍按文案记 ok")

# —— C9 行为级：账本必须真的以 ToolResult.ok 为准 ——
# 静态字符串匹配是**恒真**的：把整段换成 `if False: _ok = True`，
# 「hasattr(tool_result」与「bool(tool_result.ok)」两个串都还在，C9 照样绿
# （本次扰动实测：这条变异红 0 条）。故必须行为级—— 真造一个账本，
# 让「ToolResult 说失败但文案判不出失败」的场景走一遍 _tstate_record。
import task_state as _tsmod  # noqa: E402
from agent_task_mixin import AgentTaskMixin  # noqa: E402


class _Rec(object):
    """记录 record_tool 收到的 ok（不碰真账本，避免写盘）。"""

    def __init__(self):
        self.calls = []
        self.current_step = 0
        self.artifacts = []

    def record_tool(self, name, args, result_str, ok=True, step=0):
        self.calls.append((name, ok))

    def note_artifact(self, path, tool=None):
        self.artifacts.append((path, tool))


class _LedgerHost(AgentTaskMixin):
    def __init__(self):
        self._tstate = _Rec()


_H = _LedgerHost()
_TR_FAIL = _TR(False, "已写入 5 字符\n[执行后验证未通过] 残留 3 个",
               error_code="POST_VERIFY_FAILED", verified=True)
_H._tstate_record(_tsmod, "write_file", {"arguments": "{}"},
                  str(_TR_FAIL.msg), [], _W._tool_result_looks_failed,
                  tool_result=_TR_FAIL)
_c9 = _H._tstate.calls
check("C9 账本行为级：ToolResult.ok=False → 账本记失败",
      len(_c9) == 1 and _c9[0][1] is False,
      "账本记的是 %r（文案判不出失败 → 说明它没认 ToolResult）" % (_c9,))

# 反向：不传 tool_result 时必须退回字符串判据（且结论不变）
_H2 = _LedgerHost()
_H2._tstate_record(_tsmod, "web_fetch", {"arguments": "{}"},
                   "抓取失败：超时", [], _W._tool_result_looks_failed)
check("C9b 不传 ToolResult 时退回字符串判据（零行为变化）",
      len(_H2._tstate.calls) == 1 and _H2._tstate.calls[0][1] is False,
      repr(_H2._tstate.calls))

_H3 = _LedgerHost()
_H3._tstate_record(_tsmod, "write_file", {"arguments": "{}"},
                   "已写入 5 字符\n[执行后验证未通过] 残留 3 个", [],
                   _W._tool_result_looks_failed)
check("C9c 不传 ToolResult + 文案判不出 → 按成功记（同 v4.225 口径）",
      len(_H3._tstate.calls) == 1 and _H3._tstate.calls[0][1] is True,
      repr(_H3._tstate.calls))
check("C9d 静态口径仍成立：tool_result 优先于字符串判据",
      "hasattr(tool_result" in _mx_code and "bool(tool_result.ok)" in _mx_raw)

# ============================================================
# D 端到端：真跑 exec_tool + 验证器降级 → 接线后的判定必须为失败
# ============================================================
print("\n--- D 端到端：真ToolResult 走一遍判定 ---")
import tool_contract as _tc  # noqa: E402
import tools as _tools  # noqa: E402


class _Allow(object):
    allowed = True
    needs_user = False
    reason = "probe"
    rule = "probe"


_tc.register_verifier("write_file",
                      lambda a, r: (False, "同名进程仍残留 3 个"))
_tmpd = tempfile.mkdtemp(prefix="tr226_")
try:
    _real = _tools.exec_tool({"agent_mode": True}, _tmpd, "write_file",
                             {"path": "probe_226.txt", "content": "hello"},
                             perm_ctx=_Allow())
    check("D1 exec_tool 返回 ToolResult", hasattr(_real, "ok"), type(_real).__name__)
    check("D2 验证不通过时 ok=False（契约层已降级）", _real.ok is False)
    check("D3 error_code=POST_VERIFY_FAILED",
          getattr(_real, "error_code", None) == "POST_VERIFY_FAILED",
          repr(getattr(_real, "error_code", None)))
    _e = _W._tool_outcome(_real, "write_file", str(_real.msg),
                           _W._tool_result_looks_failed)
    check("D4 接线后的判定 = 失败（v4.226 修的就是这条）", _e[0] is False,
          repr(_e))
    check("D5 证据登记用的 err_head 含错误码",
          "POST_VERIFY_FAILED" in (_e[1] or ""), repr(_e[1]))
    # 反向：对照组（无验证器）必须仍是成功 —— 防「一律判失败」
    _tc._VERIFY_REGISTRY.pop("write_file", None)
    _real2 = _tools.exec_tool({"agent_mode": True}, _tmpd, "write_file",
                              {"path": "probe_226b.txt", "content": "hi"},
                              perm_ctx=_Allow())
    _e2 = _W._tool_outcome(_real2, "write_file", str(_real2.msg),
                           _W._tool_result_looks_failed)
    check("D6 对照组（真写入成功）判定 = 成功", _e2[0] is True, repr(_e2))
finally:
    try:
        for _f in os.listdir(_tmpd):
            os.remove(os.path.join(_tmpd, _f))
        os.rmdir(_tmpd)
    except Exception:
        pass

print("\n" + "=" * 62)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 62)
sys.exit(1 if FAIL else 0)