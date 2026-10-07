# -*- coding: utf-8 -*-
"""run_python 参数 schema 判据（v4.227，外部审查遗留项）。

背景：`exec_tool` 在副作用前统一调 `validate_for_tool`，**未登记 schema 的工具
原样放行**（v4.223 的零破坏设计）。run_python 是唯一没登记的 EXEC 类高危工具 ——
代码正文直接落盘成 .py 再由子进程执行，却没有任何参数上限。

本套件的核心主张（对应审查报告那句话「run_python 没有参数 schema」）：

  A  组 行为层：上限/类型真在 `validate_for_tool` 生效，且**真在 exec_tool 里
       生效**（只测校验函数等于没测接线 —— 校验函数写对了但 exec_tool 没调用，
       一样是零防护）。
  B  组 零破坏：三条刻意的不作为都必须保持 —— 空 code 不被硬拒（不许标
       required）、未知字段仍透传（不许改 unknown 策略）、int 仍被强转。
       这些都是「修 schema 时最容易顺手改坏」的地方。
  C  组 口径一致：与 run_command 同档（EXEC 类同上限），不因工具不同而更严。
  D  组 覆盖度自证：登记的名字必须与真实注册表核对，杜绝幽灵名/漏项
       （P2-2 分档时踩过：凭印象列的清单有 11 漏项 + 5 幽灵名）。
  E  组 接线层：tools.py 真调用 validate_for_tool（AST 判定，防 if False 短路）。
"""

import ast
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PY = sys.executable

_p = 0
_f = 0
_fail_names = []


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        _fail_names.append(name)
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _src(name):
    return open(os.path.join(ROOT, name), "rb").read().decode("utf-8-sig")


def _strip_comment_cols(text):
    """按列挖掉 COMMENT token（整行删会误伤「代码 + 行尾注释」的主力写法）。"""
    import io
    import tokenize
    lines = text.split("\n")
    drops = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and tok.start[0] == tok.end[0]:
                drops.append((tok.start[0], tok.start[1], tok.end[1]))
    except Exception:
        return text
    for i, ln in enumerate(lines, 1):
        for (s, a, b) in drops:
            if s == i:
                lines[i - 1] = ln[:a] + ln[b:]
                break
    return "\n".join(lines)


def _fn_body(src, name):
    """按 AST 行号取函数真实源码段（比 find('\\ndef ') 稳，新函数插入不串段）。"""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ============================================================
def group_a_behavior():
    print("\n-- A) 行为层：校验与 exec_tool 接线都真生效 --")
    from tool_contract import validate_for_tool, ARG_MAX_LEN_CAP
    import tools

    # A1 已登记（前置自证：下面所有断言都建立在「登记存在」之上）
    ent = validate_for_tool("run_python", {"code": "print(1)"})
    check("A1 run_python 已登记 schema（不是未登记原样放行）",
          "run_python" in __import__("tool_contract").registered_tool_schemas(),
          "registered_tool_schemas 里没有 run_python")
    # A2 正常代码通过且**原样返回**（不被截断/改写）
    clean, err = ent
    check("A2 正常 code 原样放行",
          err is None and clean.get("code") == "print(1)", f"clean={clean!r} err={err}")

    # A3 超大 code 被拒，且错误信息带上真实长度与上限（模型据此能自我修正）
    _c, e3 = validate_for_tool("run_python", {"code": "x" * (ARG_MAX_LEN_CAP + 1)})
    check("A3 超上限 code 被拒", e3 is not None, "未拒绝")
    check("A3b 错误信息含真实长度与上限（模型可据此调整）",
          bool(e3) and str(ARG_MAX_LEN_CAP) in e3 and "长度" in e3, str(e3))

    # A4 恰好等于上限（边界闭合：< 还是 <=，差一就是形同虚设）
    _c, e4 = validate_for_tool("run_python", {"code": "y" * ARG_MAX_LEN_CAP})
    check("A4 恰好等于上限可通过（边界闭合，非差一失效）", e4 is None, str(e4))
    _c, e4b = validate_for_tool("run_python", {"code": "y" * (ARG_MAX_LEN_CAP + 1)})
    check("A4b 上限+1 即拒（边界严丝合缝）", e4b is not None, "未拒绝")

    # A5 类型强转而非放行原容器（list 转成 str）
    clean5, e5 = validate_for_tool("run_python", {"code": ["a", "b"]})
    check("A5 list code 被强转成 str（不再原样透传给 f.write）",
          e5 is None and isinstance(clean5.get("code"), str)
          and "a" in clean5["code"], f"clean={clean5!r}")

    # ---- A6~A8：接线层，只测 validate_for_tool 不够 ----
    # 真跑 exec_tool：超大 code 必须在**副作用之前**被拦下。
    #
    # ⚠️ 哨兵用「code 自己写文件」，不能比「执行前后磁盘 .py 集合差」：
    #    tool_run_python 在 finally 里会删掉脚本文件，无校验时虽然**真的
    #    执行了**，但 .py 已消失 → 集合差为空 → 断言照样绿（首跑踩过：
    #    V1/V2 都 MISS 在 A7）。让 code 自己留一个删不掉的哨兵才抓得住。
    #
    # ⚠️ 必须传**合法权限决策**：run_python 是 EXEC 类，无 perm_ctx 时上一轮
    #    P1-1 修好的 fail-closed 闸门会先拒（"exec_tool 需要权限上下文"）。
    #    那样这条断言就是**假阳性** —— 拒的原因与参数校验无关，却照样绿。
    import glob
    import tempfile
    import time
    import permissions as _PM
    allow_ctx = _PM.Decision(allowed=True, needs_user=False,
                             reason="test", rule="test")

    sentinel = os.path.join(tempfile.gettempdir(), "_rp_schema_sentinel.txt")
    _rm(sentinel)
    # 超长但带副作用：校验生效 → 一个字都不执行，哨兵不存在；
    # 校验失效 → 照常执行，哨兵被创建。
    bomb = ("open(%r,'w').write('ran')\n" % sentinel) + "#" + "z" * (ARG_MAX_LEN_CAP + 500)

    ws = os.path.join(tempfile.gettempdir(), "workspace")
    before = set(glob.glob(os.path.join(ws, "*.py"))) if os.path.isdir(ws) else set()

    # A6b 前置自证：合法 perm_ctx 下，小代码确实能走到执行（否则下面全是假阳性）
    tr_small = tools.exec_tool(None, ROOT, "run_python",
                               {"code": "print('ok227')"}, perm_ctx=allow_ctx)
    check("A0 前置自证：合法 perm_ctx 下 exec_tool 能走到执行",
          "需要权限上下文" not in (getattr(tr_small, "msg", "") or ""),
          repr((getattr(tr_small, "msg", "") or "")[:100]))

    # ⚠️ exec_tool 首参是 cfg（位置参数），不是 keyword-only。
    tr = tools.exec_tool(None, ROOT, "run_python", {"code": bomb},
                         perm_ctx=allow_ctx)
    time.sleep(0.3)
    ran = os.path.exists(sentinel)
    _rm(sentinel)
    after = set(glob.glob(os.path.join(ws, "*.py"))) if os.path.isdir(ws) else set()
    new_py = after - before

    check("A6 exec_tool 对超大 code 返回拒绝（ok=False）",
          not getattr(tr, "ok", False), f"ok={getattr(tr, 'ok', None)}")
    check("A6b 拒绝信息提到参数校验（说明确实被闸门拦，不是执行失败）",
          "参数校验未通过" in (getattr(tr, "msg", "") or ""),
          repr((getattr(tr, "msg", "") or "")[:120]))
    check("A7 ★副作用为零：code 里的哨兵没被执行（拦在副作用之前）",
          not ran, "哨兵文件被创建 → 超长 code 真的跑起来了")
    check("A7b 也没留下脚本产物",
          not new_py, "多出=%r" % sorted(os.path.basename(p) for p in new_py)[:5])

    # A8 反向对照：合法小代码要能真的执行成功（防「上限改成 0 全部拒」的假绿）
    tr_ok = tr_small
    check("A8 合法小代码真跑通（上限不是形同虚设的全拒）",
          getattr(tr_ok, "ok", False) and "ok227" in (getattr(tr_ok, "msg", "") or ""),
          f"ok={getattr(tr_ok, 'ok', None)} msg={repr((getattr(tr_ok, 'msg', '') or '')[:120])}")


# ============================================================
def group_b_zero_break():
    print("\n-- B) 零破坏：三条刻意的不作为 --")
    from tool_contract import validate_for_tool

    # B1 空 code 必须仍被放行 → 由 tool_run_python 给出「未提供代码」友好返回。
    #    标了 required 就会变成校验层硬拒 = 行为变更。
    for args in ({"code": ""}, {}):
        _c, e = validate_for_tool("run_python", args)
        check("B1 空/缺 code 不被校验层硬拒（保持友好返回）%r" % (args,),
              e is None, str(e))

    # B2 未知字段仍透传（不许改 unknown 策略成 strip/reject）
    clean, e = validate_for_tool("run_python", {"code": "x=1", "zzz": 1, "timeout": 9})
    check("B2 未知字段仍透传（unknown 策略未改成 strip/reject）",
          e is None and "zzz" in clean and "timeout" in clean, f"clean={clean!r}")

    # B3 int 仍被强转成 str（str 分支语义，不因登记而变严）
    clean3, e3 = validate_for_tool("run_python", {"code": 123})
    check("B3 int code 仍被强转为 str（未引入新拒绝）",
          e3 is None and clean3.get("code") == "123", f"clean={clean3!r}")

    # B4 空 code 的真实下游行为未变：仍应返回「未提供代码」
    import tools
    out, _d = tools.tool_run_python(ROOT, "")
    check("B4 空 code 下游仍返回「未提供代码」（真行为未变）",
          "未提供代码" in (out or ""), repr(out))


# ============================================================
def group_c_parity():
    print("\n-- C) 口径一致：与 run_command 同档 --")
    from tool_contract import (validate_for_tool, ARG_MAX_LEN_CAP,
                               registered_tool_schemas)

    # ⚠️ registered_tool_schemas 返回的是 {name: (schema_list, unknown)}，不是裸 list。
    sch = registered_tool_schemas()

    def _entries(name):
        return (sch.get(name) or ([], "ignore"))[0]

    rpy = _entries("run_python")
    rcmd = _entries("run_command")

    def _spec(entries, key):
        for e in entries:
            if isinstance(e, dict) and e.get("key") == key:
                return e
        return {}

    a = _spec(rpy, "code")
    b = _spec(rcmd, "command")
    check("C1 run_python.code 与 run_command.command 上限同值",
          a.get("max_len") == b.get("max_len") == ARG_MAX_LEN_CAP,
          f"code={a.get('max_len')} command={b.get('max_len')} cap={ARG_MAX_LEN_CAP}")
    check("C2 两者都标 str（同类大字符串同处理）",
          a.get("type") == b.get("type") == "str",
          f"code={a.get('type')} command={b.get('type')}")

    # C3 不得比 run_command 更严（更严 = 会误拒正常长脚本，无依据）
    check("C3 未比 run_command 更严（没把上限调到更小）",
          not (isinstance(a.get("max_len"), int)
               and isinstance(b.get("max_len"), int)
               and a["max_len"] < b["max_len"]),
          f"code={a.get('max_len')} command={b.get('max_len')}")

    # C4 两个 EXEC 类工具都登记了（不许只补一个）
    check("C4 EXEC 类 run_command/run_python 双双有 schema",
          "run_command" in sch and "run_python" in sch,
          f"已登记={sorted(sch)}")

    # C5 schema 只声明 code 一个键：多声明就是「登记了却从不改变结论」
    keys = [e.get("key") for e in rpy if isinstance(e, dict)]
    check("C5 run_python schema 只声明 code（无幽灵键）", keys == ["code"], str(keys))

    # C6 未标 required —— 标了就是行为变更（见 B1）
    check("C6 run_python.code 未标 required（零破坏）",
          a.get("required") is not True, f"required={a.get('required')}")


# ============================================================
def group_d_coverage():
    print("\n-- D) 覆盖度自证：与真实注册表双向核对 --")
    from tool_contract import registered_tool_schemas
    import tools

    names = set(tools.TOOL_REGISTRY.keys())
    sch = registered_tool_schemas()

    # D1 无幽灵名：登记项必须都是真存在的工具
    ghost = sorted(k for k in sch if k not in names)
    check("D1 schema 登记项零幽灵名", not ghost, f"幽灵={ghost}")

    # D2 run_python 确属真实注册的高危工具（钉住这条前提）
    check("D2 run_python 真在工具注册表里",
          "run_python" in names, "注册表里没有 run_python")
    ent = tools.TOOL_REGISTRY.get("run_python") or {}
    check("D2b run_python 仍标记为 dangerous（本次未顺手改风险等级）",
          bool(ent.get("dangerous")), f"dangerous={ent.get('dangerous')}")

    # D3 全部 EXEC/dangerous 工具的登记状况可枚举（不要求全登记，只要求可查）
    dangerous = sorted(k for k, v in tools.TOOL_REGISTRY.items()
                       if (v or {}).get("dangerous"))
    covered = sorted(k for k in dangerous if k in sch)
    missing = sorted(set(dangerous) - set(sch))
    print("       dangerous 工具已登记 schema：%d/%d" % (len(covered), len(dangerous)))
    print("       dangerous 但未登记（留待后续，非本轮范围）：%s" % missing)
    check("D3 dangerous 工具的 schema 登记状况可枚举（本轮两项都在内）",
          "run_python" in sch and "run_command" in sch,
          f"已登记={covered}")


# ============================================================
def group_e_wiring():
    print("\n-- E) 接线层：tools.py 真调用 validate_for_tool（AST 判定）--")
    src = _src("tools.py")
    body = _fn_body(src, "exec_tool")
    check("E0 exec_tool 可解析（否则本组无意义）", bool(body), "未找到 exec_tool")
    if not body:
        return
    body_code = _strip_comment_cols(body)

    # E1 真调用，且**不在恒假分支里**。
    #    ⚠️ 只用 ast.walk 找 Call 会被 `if False:` 变异打穿 —— walk 会递归进
    #    If 的 body，Call 节点照样在（首跑 V4 就栽在这，红的是 E1b 不是 E1）。
    #    必须判「这 Call 是否真的会被执行」。
    tree = ast.parse(body)

    def _always_false(test):
        """恒假条件：字面 False / None / 0 / 空容器。"""
        if isinstance(test, ast.Constant):
            return not test.value
        if isinstance(test, (ast.Tuple, ast.List, ast.Dict, ast.Set)):
            return len(test.elts if hasattr(test, "elts") else test.keys) == 0
        return False

    # 收集所有恒假分支的 body 节点集合
    dead_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _always_false(node.test):
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    dead_nodes.add(id(sub))

    calls, dead_calls = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if nm == "validate_for_tool":
                (dead_calls if id(node) in dead_nodes else calls).append(node)
    check("E1 exec_tool 体内真调用 validate_for_tool（AST，非找串）",
          len(calls) >= 1, f"可执行 {len(calls)} 处 / 死分支 {len(dead_calls)} 处")
    check("E1b 该调用不在 `if False` 之类恒假分支里（源码层判据防短路变异）",
          not dead_calls, f"死分支里发现 {len(dead_calls)} 处调用")

    # E2 校验失败必须 return 拒绝（不是仅记日志继续执行）
    check("E2 校验失败分支有 return（拦在副作用之前）",
          bool(re.search(r"_ve\b", body_code)) and "return" in body_code,
          "找不到 _ve / return")

    # E3 校验后的 args 真的被使用（args = _va），否则校验形同虚设
    check("E3 清洗后的 args 被赋回（args = _va），校验结果真的生效",
          bool(re.search(r"args\s*=\s*_va", body_code)), "找不到 args = _va")


def main():
    print("=== run_python 参数 schema 判据（v4.227）===")
    for fn in (group_a_behavior, group_b_zero_break, group_c_parity,
               group_d_coverage, group_e_wiring):
        try:
            fn()
        except Exception as ex:
            check(f"{fn.__name__} 组执行崩溃", False, repr(ex))
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())