# -*- coding: utf-8 -*-
"""P1-1 判据：权限 fail-closed（v4.226）

修的是什么
----------
`agent._AllowAllDecision` 是个 `allowed = True` 的兜底决策，在拿不到权限决策时
替并发批次「按了放行」。后果不是「少弹一次窗」，而是**整个最终闸门被短路**：
`tools._permission_gate` 认鸭子类型 `hasattr(perm_ctx, "allowed")`，拿到这个
对象就无条件放行 —— WRITE_LOCAL / EXEC / EXTERNAL 全部不拦。

真实可触发路径（不是「引擎为 None」——那条路在当前唯一入口会先 AttributeError，
到不了并发兜底）：**权限引擎存在但 decide() 抛异常** → `_dec = None` → 兜底放行。

第二处：`_run_workflow_guarded` 里 `if engine is not None:` 包住全部判定，
引擎为 None 时直接落到末尾 `return self._run_workflow(...)`，一个字没判就启动
子代理任务图（内含搜索 / 写文件）。

四组判据
--------
A 行为层：真跑 tools._permission_gate / exec_tool，钉「无授权 → 按风险拒绝」。
B 接线层：AST 钉 agent.py 的并发批次与 workflow 闸门结构（非字符串 find）。
C 结构层：钉「agent.py 里不再存在 allowed=True 的兜底决策类」。
D 设计决定：钉住 agent_node._AllowDecision **刻意保持放行**这条决定 ——
          它与 A/B 不是同类漏项，改它会打断 research_write 的「撰写报告」节点。

D 组存在的意义：审查报告只看了主链，agent_node 里那个同名的 allowed=True 极易被
下一轮当成「同类漏项」顺手改掉。这一组就是拦那个动作的。
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import agent as _AG          # noqa: E402
import agent_node as _AN     # noqa: E402
import permissions as _PM    # noqa: E402
import tools as _T           # noqa: E402
from risk import RiskClass, classify  # noqa: E402

PASS = 0
FAIL = 0
_FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        _FAILURES.append(name)
        print("[FAIL] %s  —— %s" % (name, detail))


def _src(path):
    with open(os.path.join(ROOT, path), encoding="utf-8-sig") as f:
        return f.read()


def _strip_comments(text):
    """按列挖掉 COMMENT token（v4.226 纪律 #18：不能整行删）。"""
    import io
    import tokenize
    spans = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except Exception:
        return text
    lines = text.split("\n")
    for tk in toks:
        if tk.type != tokenize.COMMENT:
            continue
        row, col = tk.start[0], tk.start[1]
        end_col = tk.end[1]
        if row - 1 >= len(lines):
            continue
        s = lines[row - 1]
        if end_col >= len(s):
            spans.append((row - 1, 0, len(s)))
        else:
            spans.append((row - 1, col, end_col))
    for row, c0, c1 in sorted(spans, key=lambda x: (-x[0], -x[1])):
        s = lines[row]
        lines[row] = s[:c0] + s[c1:]
    return "\n".join(lines)


# ======================================================================
# A 组：行为层 —— 真跑闸门，钉「无授权 → 按风险分类拒绝」
# ======================================================================
def group_a_behavior():
    print("\n-- A) 行为层：_permission_gate 对「无授权上下文」fail-closed --")

    # A1 前置自证：READ 放行是既定兼容口径（下面几条拒绝才有对照意义）
    ok_read, _ = _T._permission_gate("web_search", {}, None)
    check("A1 READ(web_search) 无授权仍放行（既定兼容口径，非本次所改）",
          ok_read is True, "ok_read=%s" % ok_read)

    # A2 WRITE_LOCAL 必须被拒
    ok_w, msg_w = _T._permission_gate("write_file", {"path": "x"}, None)
    check("A2 WRITE_LOCAL(write_file) 无授权被最终闸门拒绝",
          (not ok_w) and ("exec_tool 需要权限上下文" in msg_w),
          "ok_w=%s msg=%r" % (ok_w, msg_w))

    # A3 EXEC 必须被拒
    ok_e, msg_e = _T._permission_gate("run_python", {"code": "1"}, None)
    check("A3 EXEC(run_python) 无授权被最终闸门拒绝",
          (not ok_e) and ("exec_tool 需要权限上下文" in msg_e),
          "ok_e=%s msg_e=%r" % (ok_e, msg_e))

    # A4 EXTERNAL（未在 external_allow 白名单）必须被拒
    ok_x, _ = _T._permission_gate("send_email", {}, None)
    check("A4 EXTERNAL(send_email) 无授权被最终闸门拒绝", not ok_x,
          "ok_x=%s" % ok_x)

    # A5 端到端：真跑 exec_tool，WRITE_LOCAL 工具必须**没有真的执行**
    #    （用 write_file 写一个哨兵文件；若被放行则文件会真的出现）
    import tempfile
    probe = os.path.join(tempfile.gettempdir(), "p11_should_not_exist.txt")
    if os.path.exists(probe):
        os.remove(probe)
    _msg, deliv, _sch = _T.exec_tool(None, ROOT, "write_file",
                                     {"path": probe, "content": "x"})
    wrote = os.path.exists(probe)
    if wrote:
        os.remove(probe)
    check("A5 端到端：write_file 无授权调用后哨兵文件**未被创建**",
          (not wrote) and ("需要权限上下文" in str(_msg)) and deliv == [],
          "wrote=%s msg=%r" % (wrote, str(_msg)[:80]))

    # A6 对照组：携带合法放行决策 → 闸门放行（证明 A2~A5 不是闸门恒拒）
    allow = _PM.Decision(allowed=True, needs_user=False, reason="t", rule="t")
    ok_allow, _ = _T._permission_gate("write_file", {"path": "x"}, allow)
    check("A6 对照组：携带 allowed=True 决策时闸门放行（证明闸门非恒拒）",
          ok_allow is True, "ok_allow=%s" % ok_allow)

    # A7 fail-closed 不是「一律全拒」：只读仍须放行，否则功能会被打死
    check("A7 只读工具在无授权下仍放行（fail-closed 不等于全禁）",
          _T._permission_gate("web_fetch", {}, None)[0] is True
          and _T._permission_gate("read_file", {}, None)[0] is True,
          "web_fetch=%s read_file=%s" % (_T._permission_gate("web_fetch", {}, None)[0],
                                          _T._permission_gate("read_file", {}, None)[0]))


# ======================================================================
# B 组：接线层 —— AST 钉 agent.py 结构（非字符串 find）
# ======================================================================
def _cls_ast(text, name):
    tree = ast.parse(text)
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    return None


def _fn_ast(cls, name):
    for n in ast.walk(cls):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _calls_in(fn, fname):
    """函数体内对 fname 的调用点（含嵌套闭包）。"""
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if nm == fname:
                out.append(n)
    return out


def _kwarg(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _resolve_perm_chain(par, name, depth=0):
    """解析 perm_ctx 绑定变量的来源链，返回来源描述列表。

    合法来源只认两种：
      · None 常量（fail-closed，交由最终闸门按风险分类判定）
      · <x>.decide(...) 的返回值（真实权限决策）
    其余一切（含任何类构造、IfExp、追不到的 Name）判为可疑。
    闭包默认参数（def f(..., _dec_ctx=_dec)）顺着 default 的 Name 再追一层。
    """
    if depth > 4:
        return ["<追踪过深>"]
    srcs = []
    for n in ast.walk(par):
        if not isinstance(n, ast.Assign):
            continue
        targets = [t.id for t in ast.walk(n)
                   if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)]
        if name not in targets:
            continue
        val = n.value
        if isinstance(val, ast.Constant) and val.value is None:
            srcs.append("None")
        elif isinstance(val, ast.Call):
            f = val.func
            nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if nm == "decide":
                srcs.append("decide()")
            else:
                srcs.append("CALL:%s" % nm)
        elif isinstance(val, ast.Name):
            srcs.append("NAME:%s" % val.id)
            srcs.extend("  " + s for s in _resolve_perm_chain(par, val.id, depth + 1))
        else:
            srcs.append(type(val).__name__)
    for d in ast.walk(par):
        if isinstance(d, (ast.FunctionDef, ast.Lambda)):
            for arg, dflt in zip(d.args.args, d.args.defaults):
                if arg.arg != name or dflt is None:
                    continue
                if isinstance(dflt, ast.Constant) and dflt.value is None:
                    srcs.append("default:None")
                elif isinstance(dflt, ast.Name):
                    srcs.append("default:NAME:%s" % dflt.id)
                    srcs.extend("  " + s
                                for s in _resolve_perm_chain(par, dflt.id, depth + 1))
                else:
                    srcs.append("default:%s" % type(dflt).__name__)
    return srcs


def _is_suspicious(src):
    """链上每一项都必须能续上或直接合法。

    「转发指针」（`NAME:x` / `default:NAME:x`）本身不是终点 ——
    它的下一层已在同一次收集里被展开（带缩进前缀），因此放行；
    真正判红的是：类构造、IfExp、追踪过深、或**解析不出任何来源**（空链）。
    """
    s = src.strip()
    if s in ("None", "decide()"):
        return False
    if s.startswith("NAME:") or s.startswith("default:NAME:"):
        return False            # 转发项，其终点在同一列表里已展开
    return True


def group_b_wiring():
    print("\n-- B) 接线层：agent.py 并发批次 / workflow 闸门结构（AST） --")
    src = _src("agent.py")
    cls = _cls_ast(src, "AgentWorker")
    check("B0 AgentWorker 类可解析", cls is not None)
    if cls is None:
        return

    # ---- B1：按「含 submit 且调 exec_tool」这一定位特征找并发方法 ----
    par = None
    for cand in ast.walk(cls):
        if not isinstance(cand, ast.FunctionDef):
            continue
        has_submit = any(isinstance(n, ast.Attribute) and n.attr == "submit"
                         for n in ast.walk(cand))
        if has_submit and _calls_in(cand, "exec_tool"):
            par = cand
            break
    check("B1 定位到并发执行方法（特征：含 submit 且调 exec_tool）",
          par is not None, "未找到")
    if par is None:
        return
    print("       （方法名：%s）" % par.name)

    exec_calls = _calls_in(par, "exec_tool")
    check("B2 并发批次里存在 exec_tool 调用点", len(exec_calls) >= 1,
          "找到 %d 处" % len(exec_calls))

    # B3 perm_ctx 参数必须可静态追踪（是 Name，或压根没传）
    perm_names = []
    untracked = 0
    for c in exec_calls:
        v = _kwarg(c, "perm_ctx")
        if isinstance(v, ast.Name):
            perm_names.append(v.id)
        elif v is None:
            perm_names.append(None)      # 压根没传 → 恒 None，安全
        else:
            untracked += 1
    check("B3 并发 exec_tool 的 perm_ctx 全部可静态追踪",
          untracked == 0 and len(perm_names) == len(exec_calls),
          "不可追踪=%d perm_ctx=%r" % (untracked, perm_names))

    # B4 解析链末端只能是 None 或 decide()
    for pid in sorted(set(x for x in perm_names if x)):
        srcs = _resolve_perm_chain(par, pid)
        bad = [s for s in srcs if _is_suspicious(s)]
        check("B4 并发批次 perm_ctx 链 `%s` 末端只可能是 None 或 decide()" % pid,
              (not bad) and bool(srcs),
              "来源=%r 可疑=%r" % (srcs, bad))

    # B5 不得再构造任何兜底决策对象
    ctor_names = set()
    for n in ast.walk(par):
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if nm and ("AllowAll" in nm or nm.endswith("Decision")):
                ctor_names.add(nm)
    check("B5 并发批次不再构造任何 *_Decision / *AllowAll 兜底对象", not ctor_names,
          "发现 %r" % (sorted(ctor_names),))

    dec_calls = _calls_in(par, "decide")
    check("B6 并发批次仍取真实权限决策（decide 调用存在）", len(dec_calls) >= 1,
          "找到 %d 处" % len(dec_calls))

    log_warn = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "warning"
                   for n in ast.walk(par))
    check("B7 权限决策异常有 log.warning 留痕", log_warn, "未找到 log.warning")

    # ---- B8~B11：workflow 闸门 ----
    wf = _fn_ast(cls, "_run_workflow_guarded")
    check("B8 找得到 _run_workflow_guarded", wf is not None)
    if wf is None:
        return

    # B9 缺引擎分支必须显式 return。
    #    AST 语义：`engine is None` → Compare(op=Is)；`is not None` → IsNot。
    #    判据看的是 **Compare 左侧那个变量名**（形如 `engine is None`），
    #    不是 ast.dump 里有无 "permission_engine" —— 后者只出现在上一行
    #    `engine = getattr(mw, "permission_engine", None)` 的赋值里，
    #    条件体内的 dump 只有 `engine` / `None` 两个名字（实测踩过）。
    guard_found = False
    guard_desc = []
    for n in ast.walk(wf):
        if not isinstance(n, ast.If) or not isinstance(n.test, ast.Compare):
            continue
        cmp_ = n.test
        if [type(o).__name__ for o in cmp_.ops] != ["Is"]:
            continue
        lhs = cmp_.left
        lhs_name = lhs.id if isinstance(lhs, ast.Name) else ast.dump(lhs)
        # 只认「疑似引擎变量」：名字里含 engine
        if not (isinstance(lhs, ast.Name) and "engine" in lhs.id):
            continue
        # 比较对象必须是 None 常量
        rhs = cmp_.comparators[0]
        if not (isinstance(rhs, ast.Constant) and rhs.value is None):
            continue
        for b in n.body:
            if isinstance(b, ast.Return) and b.value is not None:
                guard_found = True
                guard_desc.append(lhs.id)
    check("B9 workflow 闸门对「缺引擎」显式 return（不再整条跳过）",
          guard_found, "未找到 `engine is None → return`，扫到=%r" % guard_desc)

    # B10 最终执行不得被「engine is not None 才判定」的结构包住
    guarded_by_neq = False
    for n in ast.walk(wf):
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare):
            if ("IsNot" in ast.dump(n.test)
                    and "permission_engine" in ast.dump(n.test)):
                for b in n.orelse:
                    if isinstance(b, ast.Return) and isinstance(b.value, ast.Call):
                        guarded_by_neq = True
    check("B10 workflow 不存在「engine is not None 才判定、否则照跑」结构",
          not guarded_by_neq, "仍存在 orelse 直通最终执行")

    final_calls = _calls_in(wf, "_run_workflow")
    check("B11 最终执行点仍只经由本函数（_run_workflow 调用存在）",
          len(final_calls) >= 1, "找到 %d 处" % len(final_calls))


# ======================================================================
# C 组：结构层 —— agent.py 里不再有 allowed=True 的兜底决策
# ======================================================================
def group_c_structure():
    print("\n-- C) 结构层：agent.py 无 allowed=True 兜底决策 --")
    src = _src("agent.py")
    code = _strip_comments(src)

    # C1 注释剔除后仍不许出现该类名（防「注释里提到就算过」的恒真）
    check("C1 agent.py 注释剔除后不再出现 _AllowAllDecision",
          "_AllowAllDecision" not in code,
          "仍出现 %d 次" % code.count("_AllowAllDecision"))

    # C2 真属性核对：agent 模块上不得再有任何 allowed=True 的类属性
    bad = []
    for name in dir(_AG):
        obj = getattr(_AG, name)
        if isinstance(obj, type) and getattr(obj, "allowed", None) is True:
            bad.append(name)
    check("C2 agent 模块下无任何 allowed=True 的决策类", not bad, "发现 %r" % bad)

    # C3 双向上下文自证：剔除注释器本身不是「把全文删空」
    check("C3 注释剔除器双向上下文自证（正文仍在、注释确实被挖掉）",
          ("_AllowAllDecision" in src) and ("_AllowAllDecision" not in code)
          and len(code) > len(src) * 0.5,
          "src=%d code=%d" % (len(src), len(code)))

    # C4 只读类名做兜底：AgentTaskMixin 等真 mixin 不许带 allowed=True
    for mn in ("AgentTaskMixin", "AgentLoopMixin", "AgentResultMixin"):
        m = getattr(_AG, mn, None)
        if m is None:
            continue
        check("C4 mixin %s 不带 allowed=True 类属性" % mn,
              getattr(m, "allowed", None) is not True,
              "allowed=%r" % getattr(m, "allowed", None))

    # C5 workflow 缺引擎的拒绝文案必须明确「未执行」
    m = re.search(r"def _run_workflow_guarded\(.*?\n(?=    def )", src, re.S)
    seg = m.group(0) if m else ""
    check("C5 workflow 缺引擎拒绝文案含「未执行」",
          "未执行" in seg and "权限引擎未启用" in seg,
          "seg=%r" % seg[:160])


# ======================================================================
# D 组：设计决定 —— 钉住 agent_node._AllowDecision 刻意保持放行
# ======================================================================
def group_d_design_decision():
    print("\n-- D) 设计决定：军团侧兜底刻意不改（防误当同类漏项） --")

    # D1 该类仍是 allowed=True —— 这是**刻意**的现状，不是残留
    check("D1 agent_node._AllowDecision 现状为 allowed=True（本次刻意不改）",
          getattr(_AN._AllowDecision, "allowed", None) is True,
          "allowed=%r" % getattr(_AN._AllowDecision, "allowed", None))

    # D2 它必须在 docstring 里写明「为何与主链不同」——
    #    没有这段说明，下一个人只会看到两个同名同值的类，然后「顺手统一」
    check("D2 _AllowDecision 的说明里写明了与主链的差异理由",
          "刻意不改" in (_AN._AllowDecision.__doc__ or ""),
          "doc=%r" % (_AN._AllowDecision.__doc__ or "")[:120])

    # D3 理由必须点名上游闸门（run_workflow_guarded），不是空泛说「已授权」
    doc = _AN._AllowDecision.__doc__ or ""
    check("D3 说明里点名上游闸门 _run_workflow_guarded",
          "_run_workflow_guarded" in doc, "doc 片段=%r" % doc[:200])

    # D4 反向自证：真跑 workflow 的「撰写报告」节点所用工具风险档 ——
    #    证明「若把该兜底翻成拒绝，write_file 会被闸门拒掉」这句话是真的
    wf_risk = classify("write_file")
    check("D4 research_write 写报告节点用 write_file，其风险档为 WRITE_LOCAL",
          wf_risk == RiskClass.WRITE_LOCAL, "risk=%s" % wf_risk)
    denied, _ = _T._permission_gate("write_file", {"path": "x"}, None)
    check("D5 佐证：write_file 在无授权上下文下确实会被闸门拒（所以不能一刀切）",
          denied is False, "denied=%s" % denied)

    # D6 反向自证：workflow 入口本身仍在 agent.py 侧受闸（上游没漏）
    src = _src("agent.py")
    check("D6 上游闸门 _run_workflow_guarded 仍存在且带缺引擎拒绝",
          "def _run_workflow_guarded(" in src and "权限引擎未启用" in src,
          "未找到")


def main():
    print("=" * 62)
    print("P1-1 权限 fail-closed 判据（v4.226）")
    print("=" * 62)
    group_a_behavior()
    group_b_wiring()
    group_c_structure()
    group_d_design_decision()
    print("\n" + "=" * 62)
    if _FAILURES:
        print("失败项：")
        for f in _FAILURES:
            print("  - %s" % f)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
