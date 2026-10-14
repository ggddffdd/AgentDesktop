# -*- coding: utf-8 -*-
"""v4.264 本周安全与语义对齐（审阅 P0-2 / P0-3 / P1-3 / P1-4）。

修之前必须红、修之后必须绿的四件事：

**P0-2** `implicit_intent` 在 decide() 里先于会话信任，但弹窗层
`_maybe_confirm(force=False)` 见到 `session_trusted` 会直接返回 True。
来源闸因此被「本次会话全部信任」架空。force 必须把这条规则算进去，
并且真的走到 `confirm_action.emit`，而不是信任短路。

**P0-3** auto 分支原先只看 `risk == EXEC`，`write_file` 等
`(WRITE_LOCAL, "manual")` 在自动模式下直行。manual 档必须仍要确认；
半自主本地写（image_gen）与「全部信任」的粗粒度放行保持原语义。

**P1-3** `run_workflow` 确认后 `trust_tool("run_workflow")` 不带参数，
记的是空指纹。确认一笔具体 type/task 不得给空参数或另一笔参数背书。

**P1-4** `read_xlsx_json` 已在 registry，靠 `read_` 前缀被分成 READ，
RISK_MAP 里没有显式键。删掉前缀兜底后它会掉进 EXTERNAL。必须显式登记 READ。
另：DEFS⊄REG、或「已注册且无 RISK 且 classify==EXTERNAL」必须被
`tool_consistency_gaps()` 报出来（反向注入，防恒空）。
"""
import ast
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from permissions import PermissionEngine, args_fingerprint  # noqa: E402
from risk import RISK_MAP, RiskClass, classify, _policy  # noqa: E402

_p = _f = 0
_AGENT = os.path.join(ROOT, "agent.py")


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


def _src(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def _fn(src, name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    return ""


def _force_rules(src):
    tree = ast.parse(src)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "FORCE_CONFIRM_RULES":
                if isinstance(node.value, (ast.Tuple, ast.List)):
                    return tuple(ast.literal_eval(node.value))
    return ()


def _confirm_force_exprs(src):
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "_maybe_confirm"):
            continue
        expr = None
        for kw in node.keywords:
            if kw.arg == "force":
                expr = ast.unparse(kw.value)
        out.append(expr)
    return out


class _Emit:
    def __init__(self, owner):
        self.owner = owner
        self.n = 0

    def emit(self, title, detail):
        self.n += 1
        self.owner._confirm_val = True
        self.owner._confirm_event.set()


class _MW:
    def __init__(self, engine):
        self.permission_engine = engine
        self.session_trusted = True


def _reached_confirm(maybe, engine, force):
    """跑真实 _maybe_confirm。force=False 且会话已信任时应短路、不 emit。"""
    box = type("Box", (), {})()
    box.mw = _MW(engine)
    box._confirm_event = threading.Event()
    box._confirm_val = False
    box._confirm_force = None
    box.confirm_action = _Emit(box)
    ok = maybe(box, "标题", "详情", force)
    return box.confirm_action.n > 0 and ok is True and box._confirm_force is True


def part_p02():
    print("\n-- P0-2 来源闸不被会话全信在弹窗层架空 --")
    src = _src(_AGENT)
    rules = _force_rules(src)
    check("P02 FORCE_CONFIRM_RULES 含 implicit_intent",
          "implicit_intent" in rules, "rules=%s" % (rules,))
    exprs = _confirm_force_exprs(src)
    check("P02 两处确认都按 FORCE_CONFIRM_RULES 传 force",
          len(exprs) >= 2 and all(e == "dec.rule in FORCE_CONFIRM_RULES" for e in exprs),
          "exprs=%s" % (exprs,))

    ns = {}
    exec(_fn(src, "_maybe_confirm"), ns)
    maybe = ns["_maybe_confirm"]
    engine = PermissionEngine(mode="interactive")
    engine.set_session_trusted()
    missed = []
    for tool, args in (
        ("write_file", {"path": "notes/a.txt", "content": "x"}),
        ("image_gen", {"prompt": "一只猫"}),
        ("create_automation", {"name": "每日"}),
    ):
        dec = engine.decide(tool, args, explicit_intent=False)
        if dec.rule != "implicit_intent" or not dec.needs_user:
            missed.append("%s rule=%s need=%s" % (tool, dec.rule, dec.needs_user))
            continue
        force = dec.rule in rules
        if not _reached_confirm(maybe, engine, force):
            missed.append("%s force=%s 未弹窗" % (tool, force))
    check("P02 会话全信任下来源闸必须真弹确认", not missed,
          "; ".join(missed))


def part_p03():
    print("\n-- P0-3 auto 尊重 RISK_MAP 的 manual 档 --")
    e = PermissionEngine(mode="auto")
    manual_tools = (
        ("write_file", {"path": "notes/a.txt", "content": "x"}),
        ("db_insert", {"table": "t", "row": {}}),
        ("db_update", {"table": "t", "where": {}, "row": {}}),
        ("schedule", {"delay_seconds": 60, "message": "hi"}),
    )
    bad = []
    for name, args in manual_tools:
        d = e.decide(name, args, explicit_intent=True)
        if not (d.allowed and d.needs_user and d.rule == "auto:gate"):
            bad.append("%s rule=%s need=%s" % (name, d.rule, d.needs_user))
    check("P03 auto 下 write_file 等 manual 档仍需确认", not bad, "; ".join(bad))

    d_img = e.decide("image_gen", {"prompt": "x"}, explicit_intent=True)
    check("P03 auto 下半自主本地写仍直行",
          d_img.allowed and not d_img.needs_user and d_img.rule == "auto",
          "rule=%s need=%s" % (d_img.rule, d_img.needs_user))
    d_rd = e.decide("read_file", {"path": "notes/a.txt"}, explicit_intent=True)
    check("P03 auto 下只读仍直行",
          d_rd.allowed and not d_rd.needs_user, "rule=%s" % d_rd.rule)
    d_ex = e.decide("run_command", {"command": "echo ok"}, explicit_intent=True)
    check("P03 auto 下执行类仍需确认",
          d_ex.allowed and d_ex.needs_user and d_ex.rule == "auto:gate",
          "rule=%s" % d_ex.rule)

    trusted = PermissionEngine(mode="auto")
    trusted.set_session_trusted()
    d_tr = trusted.decide("write_file", {"path": "notes/a.txt", "content": "x"},
                          explicit_intent=True)
    check("P03 显式全部信任仍放行 manual 档写入",
          d_tr.allowed and not d_tr.needs_user and d_tr.rule.startswith("session"),
          "rule=%s need=%s" % (d_tr.rule, d_tr.needs_user))


def part_p13():
    print("\n-- P1-3 run_workflow 信任记账带参数指纹 --")
    src = _src(_AGENT)
    seg = _fn(src, "_run_workflow_guarded")
    ns = {
        "FORCE_CONFIRM_RULES": _force_rules(src),
        "log": __import__("logging").getLogger("test_security_align_264"),
    }
    exec(seg, ns)
    fn = ns["_run_workflow_guarded"]

    class _Self:
        explicit_intent = False

        def _build_confirm_detail(self, name, args):
            return ("t", "d")

        def _maybe_confirm(self, title, detail, force=False):
            return True

        def _run_workflow(self, wf, task):
            return "RAN"

    args = {"wf_type": "research_write", "task": "写一份完全不同的稿"}
    other = {"wf_type": "research_write", "task": "另一份"}
    engine = PermissionEngine(mode="interactive")
    out = fn(_Self(), "research_write", "写一份完全不同的稿",
             type("MW", (), {"permission_engine": engine})(), args)
    key = "run_workflow#%s" % args_fingerprint(args)
    empty = "run_workflow#%s" % args_fingerprint({})
    other_key = "run_workflow#%s" % args_fingerprint(other)
    check("P13 确认后的信任键含本次参数",
          out == "RAN" and key in engine.session_allow
          and empty not in engine.session_allow
          and other_key not in engine.session_allow
          and engine.is_trusted("run_workflow", args)
          and not engine.is_trusted("run_workflow", {})
          and not engine.is_trusted("run_workflow", other),
          "out=%r allow=%s" % (out, sorted(engine.session_allow)))


def _gaps_fn():
    try:
        from config import tool_consistency_gaps
    except ImportError:
        return None
    if not callable(tool_consistency_gaps):
        return None
    return tool_consistency_gaps


def part_p14():
    print("\n-- P1-4 read_xlsx_json 显式 READ + 三表一致性 --")
    risk, tier, hard = _policy("read_xlsx_json")
    check("P14 read_xlsx_json 显式写入 RISK_MAP 且为 READ",
          "read_xlsx_json" in RISK_MAP
          and risk == RiskClass.READ
          and classify("read_xlsx_json") == RiskClass.READ
          and tier is None and hard is False,
          "map=%r policy=%r classify=%r" % (
              RISK_MAP.get("read_xlsx_json"), (risk, tier, hard),
              classify("read_xlsx_json")))

    import tool_defs
    import tools
    declared = {d.get("function", {}).get("name") for d in tool_defs.TOOL_DEFS}
    declared.discard(None)
    declared.discard("")
    check("P14 read_xlsx_json 同时在 TOOL_DEFS 与 registry",
          "read_xlsx_json" in declared and "read_xlsx_json" in tools.TOOL_REGISTRY)

    gaps_fn = _gaps_fn()
    check("P14 tool_consistency_gaps 可用", gaps_fn is not None)
    if gaps_fn is None:
        check("P14 现状三表无缺口", False, "函数不存在")
        check("P14 反向：未登记 EXTERNAL 必须被报出", False, "函数不存在")
        check("P14 反向：DEFS 无 handler 必须被报出", False, "函数不存在")
        return
    gaps = gaps_fn()
    check("P14 现状三表无缺口", not gaps, "gaps=%s" % (gaps,))

    tools.TOOL_REGISTRY["_probe_unmapped_zzz"] = {"handler": None, "dangerous": False}
    try:
        probed = gaps_fn()
    finally:
        tools.TOOL_REGISTRY.pop("_probe_unmapped_zzz", None)
    check("P14 反向：未登记 EXTERNAL 必须被报出",
          any("unmapped_external:_probe_unmapped_zzz" == g for g in probed),
          "probed=%s" % (probed,))

    fake = {"type": "function", "function": {
        "name": "_probe_def_only_zzz", "description": "x",
        "parameters": {"type": "object", "properties": {}},
    }}
    tool_defs.TOOL_DEFS.append(fake)
    try:
        probed2 = gaps_fn()
    finally:
        tool_defs.TOOL_DEFS.pop()
    check("P14 反向：DEFS 无 handler 必须被报出",
          any(g == "defs_not_in_reg:_probe_def_only_zzz" for g in probed2),
          "probed=%s" % (probed2,))


def main():
    part_p02()
    part_p03()
    part_p13()
    part_p14()
    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
