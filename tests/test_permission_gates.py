# -*- coding: utf-8 -*-
"""权限闸回归测试（v4.169.0 批次 A / P0-3 + P0-4 + P0-5 + P0-6）。

钉住的四件事：

**P0-3 副作用工具默认免确认 + auto 模式忽略「手动」标记**
  `image_gen / video_gen / use_skill / remember / create_automation /
   delete_automation / run_workflow / db_delete` 都被归为 WRITE_LOCAL → tier=semi
  → 交互模式下**直接执行**。一旦前面的路由误判，生图/写记忆/建自动化都不问。
  更隐蔽的是：`risk.py` 的 `_TIER_OVERRIDE` 把几个工具标成 manual（意图＝必须确认），
  但 `permissions.decide` 的 auto 分支**只看 risk 不看 tier**，那份 override
  在 auto 模式下整套失效 —— 标了"手动"的技能安装能被自动装掉。
  修法：① 新增 `explicit_intent` 来源闸（用户没表达执行意图时，模型自发的非只读操作要确认）；
        ② 新增 `ALWAYS_CONFIRM` 硬确认档（装/建技能、删自动化、删数据）——
           任何模式、任何信任都不放行，且 `_maybe_confirm(force=True)` 会跳过信任短路。

**P0-4 run_workflow 权限旁路**：直接调 `_run_workflow` 起任务图，不过引擎。

**P0-5 信任只绑工具名**：确认 `run_command("echo ok")` 后，同会话任意 run_command 免确认。

**P0-6 信任跨会话**：切/新建/关闭会话都不清 `session_allow`。
"""

import ast
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from permissions import PermissionEngine, args_fingerprint, Decision   # noqa: E402
from risk import (ALWAYS_CONFIRM, RiskClass, classify, tier_of,  # noqa: E402
                  task_risk_level, TASK_CONFIRM_LEVELS)

log = logging.getLogger("test_permission_gates")

_p = _f = 0
_AGENT_PATH = os.path.join(ROOT, "agent.py")
_UI_PATH = os.path.join(ROOT, "ui.py")


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [OK] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f"  <- {detail}" if detail else ""))


def _dec(mode, name, explicit=True, args=None, trust_all=False, trust=None):
    e = PermissionEngine(mode=mode)
    if trust_all:
        e.set_session_trusted()
    for t in (trust or []):
        e.trust_tool(t[0], t[1] if len(t) > 1 else None)
    return e.decide(name, args, explicit_intent=explicit), e


# ---------------------------------------------------------------------------
def part_a_always_confirm():
    print("\n-- A) 硬确认档：任何模式 / 任何信任都不放行（P0-3） --")
    check("A1 ALWAYS_CONFIRM 含四类不可逆操作",
          ALWAYS_CONFIRM >= {"skill_install", "create_skill",
                             "delete_automation", "db_delete"},
          f"{sorted(ALWAYS_CONFIRM)}")
    for mode in ("interactive", "auto", "custom"):
        for tool in sorted(ALWAYS_CONFIRM):
            d, _ = _dec(mode, tool)
            check(f"A2 [{mode}] {tool} 需确认", d.allowed and d.needs_user,
                  f"allow={d.allowed} need={d.needs_user} rule={d.rule}")
    # 全部信任也拦得住
    for tool in sorted(ALWAYS_CONFIRM):
        d, _ = _dec("auto", tool, trust_all=True)
        check(f"A3 全信任下 {tool} 仍需确认", d.allowed and d.needs_user,
              f"rule={d.rule}")
    # discuss / plan 模式是"禁止"，仍优先
    d, _ = _dec("discuss", "skill_install")
    check("A4 discuss 模式直接拒（硬档也要服从模式）", not d.allowed, f"rule={d.rule}")
    d, _ = _dec("plan", "skill_install")
    check("A5 plan 模式拒绝非只读", not d.allowed, f"rule={d.rule}")


def part_b_implicit_intent_gate():
    print("\n-- B) 来源闸：用户没表达执行意图时，模型自发的副作用操作要确认（P0-3） --")
    # 无意图：只读放行，副作用确认
    for tool in ("read_file", "web_search", "legion_board", "director_status",
                 "legion_get_output"):
        d, _ = _dec("interactive", tool, explicit=False)
        check(f"B1 无意图时只读放行：{tool}", d.allowed and not d.needs_user,
              f"rule={d.rule}")
    for tool in ("image_gen", "video_gen", "remember", "use_skill",
                 "create_automation", "run_python", "write_file", "run_command"):
        d, _ = _dec("interactive", tool, explicit=False)
        check(f"B2 无意图时副作用要确认：{tool}",
              d.allowed and d.needs_user and d.rule == "implicit_intent",
              f"rule={d.rule} need={d.needs_user}")
    # 有意图：照旧（这是"智能确认"的关键 —— 你明确要的直接做）
    for tool in ("image_gen", "video_gen", "create_automation", "remember"):
        d, _ = _dec("interactive", tool, explicit=True)
        check(f"B3 有意图时照旧直行：{tool}", d.allowed and not d.needs_user,
              f"rule={d.rule}")
    # 未登记工具 fail-closed（classify 默认落 EXTERNAL）也要被来源闸纳管
    d, _ = _dec("interactive", "totally_unknown_tool_xyz", explicit=False)
    check("B4 未登记工具在无意图时也要确认（fail-closed）",
          d.needs_user or not d.allowed, f"rule={d.rule}")


def part_c_trust_bound_to_args():
    print("\n-- C) 信任绑参数指纹（P0-5） --")
    e = PermissionEngine(mode="interactive")
    args_ok = {"command": "echo ok"}
    args_bad = {"command": "rm -rf /"}
    d = e.decide("run_command", args_ok, explicit_intent=True)
    check("C1 首次调用要确认", d.needs_user)
    e.trust_tool("run_command", args_ok)
    d2 = e.decide("run_command", args_ok, explicit_intent=True)
    check("C2 同工具同参数 → 免确认",
          d2.allowed and not d2.needs_user and d2.rule == "session", f"rule={d2.rule}")
    d3 = e.decide("run_command", args_bad, explicit_intent=True)
    check("C3 ★ 同工具**不同参数** → 仍要确认（旧版这里会放行）",
          d3.needs_user, f"rule={d3.rule} need={d3.needs_user}")
    check("C4 指纹对 dict 顺序不敏感（否则会被反复问）",
          args_fingerprint({"a": 1, "b": 2}) == args_fingerprint({"b": 2, "a": 1}))
    check("C5 指纹对内容敏感",
          args_fingerprint({"a": 1}) != args_fingerprint({"a": 2}))
    check("C6 is_trusted 只认已登记的参数形态",
          e.is_trusted("run_command", args_ok)
          and not e.is_trusted("run_command", args_bad))
    # 显式"全部信任"仍保留粗粒度语义
    e2 = PermissionEngine(mode="interactive")
    e2.set_session_trusted()
    d4 = e2.decide("run_python", {"code": "whatever"}, explicit_intent=True)
    check("C7 显式『本次会话全部信任』仍是粗粒度放行",
          d4.allowed and not d4.needs_user and d4.rule.startswith("session"),
          f"rule={d4.rule}")


def part_d_run_workflow_gate():
    print("\n-- D) run_workflow 必须过权限引擎（P0-4） --")
    src = open(_AGENT_PATH, encoding="utf-8-sig").read()
    check("D1 存在 _run_workflow_guarded", "def _run_workflow_guarded(" in src)
    check("D2 调用点已切到 guarded 版本",
          "self._run_workflow_guarded(_wf_type, _task, mw, _args)" in src)
    check("D3 不再有裸调 _run_workflow(_wf_type, _task)",
          "= self._run_workflow(_wf_type, _task)" not in src)
    seg = src[src.index("def _run_workflow_guarded("):]
    seg = seg[:seg.index("\n    def ", 10)]
    check("D4 guarded 里调了 engine.decide", "engine.decide(" in seg)
    check("D5 未通过时返回说明而不是照样执行",
          'not dec.allowed' in seg and "未执行" in seg)
    check("D6 需要确认时走 _maybe_confirm", "_maybe_confirm(" in seg)

    # 行为：AST 抽出来，用假 self 跑（阻止时应根本不碰 _run_workflow）
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "AgentWorker")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
              and n.name == "_run_workflow_guarded")
    # v4.196 批⑬：exec 出来的函数要能见到模块级常量 FORCE_CONFIRM_RULES
    # （以及 v4.196 新增的任务风险闸）。用**真模块**取值而不是手抄一份，
    # 否则探针手里的清单与源码漂移，等于没校验。
    try:
        import agent as _AG
    except Exception:
        _AG = None
    ns = {
        "FORCE_CONFIRM_RULES": tuple(getattr(_AG, "FORCE_CONFIRM_RULES", ()) or ()),
    }
    exec(ast.get_source_segment(src, fn), ns)

    class _FakeMW:
        def __init__(self, engine):
            self.permission_engine = engine

    calls = []

    class _FakeSelf:
        explicit_intent = True

        def _confirm_text(self, name, args):
            return ("t", "d")

        def _build_confirm_detail(self, name, args):
            # v4.220：_run_workflow_guarded 现会调 _build_confirm_detail 注入影响范围，
            # 抽出来的真实方法源码在 fake self 上跑时也需要这个方法（与 _confirm_text 同性质）。
            return ("t", "d")

        def _maybe_confirm(self, t, d, force=False):
            return False          # 用户拒绝

        def _run_workflow(self, wf, task):
            calls.append(wf)
            return "RAN"

    # ns 里的是**普通函数**，显式把假 self 当第一个参数传（不要挂到实例上 —— 会多一个 self）
    fn_guarded = ns["_run_workflow_guarded"]

    out = fn_guarded(_FakeSelf(), "research_write", "t",
                     _FakeMW(PermissionEngine("discuss")), {})
    check("D7 discuss 模式下被拦下（未启动任务图）",
          not calls and "未执行" in str(out), f"out={out!r} calls={calls}")
    out2 = fn_guarded(_FakeSelf(), "research_write", "t",
                      _FakeMW(PermissionEngine("auto")), {})
    check("D8 auto 模式下（无需确认）正常启动", calls == ["research_write"], f"out={out2}")

    # D9 用户没表达执行意图 → 来源闸要求确认；确认被拒 → 不能启动
    class _FakeSelfNoIntent(_FakeSelf):
        explicit_intent = False

    out3 = fn_guarded(_FakeSelfNoIntent(), "research_write", "t",
                      _FakeMW(PermissionEngine("interactive")), {})
    check("D9 无执行意图时需确认，被拒则不启动",
          calls == ["research_write"] and "取消" in str(out3), f"out={out3!r}")


def part_e_session_trust_reset():
    print("\n-- E) 会话信任不跨会话（P0-6） --")
    src = open(_UI_PATH, encoding="utf-8-sig").read()
    check("E1 存在 _reset_session_trust", "def _reset_session_trust(" in src)
    for ent in ("def _switch_session(", "def _new_session("):
        seg = src[src.index(ent):]
        seg = seg[:seg.index("\n    def ", 10)]
        check(f"E2 {ent[4:-1]} 调用清信任", "_reset_session_trust()" in seg, seg[:120])
    seg = src[src.index("def _close_session("):]
    seg = seg[:seg.index("\n    def ", 10)]
    check("E3 _close_session 关当前会话时清信任",
          "_reset_session_trust()" in seg and "_was_active" in seg)
    # 清信任确实清掉引擎状态
    e = PermissionEngine(mode="auto")
    e.set_session_trusted()
    e.trust_tool("run_python", {"code": "x"})
    e.session_allow.clear()
    e.session_trusted = False
    check("E4 清空后不再信任",
          not e.is_trusted("run_python", {"code": "x"})
          and e.decide("run_python", {"code": "x"}).rule != "session:*")


def part_f_force_confirm_channel():
    print("\n-- F) 硬确认档的 force 通道（否则『全信任』会把硬档短路掉） --")
    src = open(_AGENT_PATH, encoding="utf-8-sig").read()
    check("F1 _maybe_confirm 支持 force", "def _maybe_confirm(self, title, detail, force=False)" in src)
    seg = src[src.index("def _maybe_confirm("):]
    seg = seg[:seg.index("\n    def ", 10)]
    check("F2 force=True 时跳过信任短路", "if not force:" in seg)
    # v4.196 批⑬：原先校验的是**字面量** `force=(dec.rule in ("always_confirm",
    # "high_risk_exec"))`，新规则一加进来就假失败。改为**语义化**核验：
    # 从源码里解析出 force 用到的那份规则清单，再看该含的是不是都在里面 ——
    # 以后加规则不该让这条探针失效，新增规则漏配才是它该报的事。
    try:
        import agent as _AG
        _rules = tuple(getattr(_AG, "FORCE_CONFIRM_RULES", ()) or ())
    except Exception:
        _rules = ()
    check("F3 调用点按 rule 传 force（含 ①-B high_risk_exec）",
          "always_confirm" in _rules and "high_risk_exec" in _rules,
          "force 规则清单=%s" % (_rules,))
    check("F3b force 清单与源码调用点同源（不是两处手抄）",
          src.count("FORCE_CONFIRM_RULES)") >= 2 and "self.FORCE_CONFIRM_RULES" not in src,
          "调用点未使用同一份常量")
    ui = open(_UI_PATH, encoding="utf-8-sig").read()
    check("F4 UI 侧确认弹窗识别 _confirm_force",
          "_confirm_force" in ui and "session_trusted" in ui)


def part_g_negative():
    """负面验证：把两道闸分别关掉，旧行为必须能复现出来。

    手法：AST 抽出 `decide` 源码 → 把目标条件改成永假 → exec 后挂回真实
    PermissionEngine 实例（self 的属性全由真实例提供）→ 观察行为回退。
    """
    print("\n-- G) 负面验证：关掉任一道闸，旧行为必须复现 --")
    import types
    src = open(os.path.join(ROOT, "permissions.py"), encoding="utf-8-sig").read()
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "PermissionEngine")
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == "decide")
    src_decide = ast.get_source_segment(src, fn)

    def _variant(anchor, neutralized):
        patched = src_decide.replace(anchor, neutralized)
        assert patched != src_decide, f"锚点未命中：{anchor[:48]}"
        ns = {"os": os, "log": log, "Decision": Decision,
              "RiskClass": RiskClass, "classify": classify, "tier_of": tier_of,
              "ALWAYS_CONFIRM": ALWAYS_CONFIRM,
              "args_fingerprint": args_fingerprint,
              # v4.196 批⑬：decide 源码里新增的任务风险闸依赖
              "task_risk_level": task_risk_level,
              "TASK_CONFIRM_LEVELS": TASK_CONFIRM_LEVELS}
        exec(patched, ns)
        return ns["decide"]

    def _engine(mode, decide_fn, trust_all=False):
        e = PermissionEngine(mode=mode)
        if trust_all:
            e.set_session_trusted()
        e.decide = types.MethodType(decide_fn, e)
        return e

    # G1 关掉来源闸 → 用户没表达执行意图时，生图/写记忆又会直接放行
    dec_no_gate = _variant("if not explicit_intent and risk != RiskClass.READ:",
                           "if False and not explicit_intent and risk != RiskClass.READ:")
    old = _engine("interactive", dec_no_gate).decide("image_gen", explicit_intent=False)
    check("G1 关掉来源闸 → 无意图时 image_gen 直接放行（旧版行为复现）",
          old.allowed and not old.needs_user, f"rule={old.rule}")

    # G2 关掉硬确认档 → auto + 全信任时，装技能可以绕过去
    dec_no_hard = _variant("if name in ALWAYS_CONFIRM:",
                           "if False and name in ALWAYS_CONFIRM:")
    old2 = _engine("auto", dec_no_hard, trust_all=True).decide("skill_install")
    check("G2 关掉硬档 → auto+全信任时 skill_install 被放行（旧版行为复现）",
          old2.allowed and not old2.needs_user, f"rule={old2.rule}")

    # G3 现状对照：两道闸都在时，以上两例都必须被拦
    e3 = PermissionEngine(mode="interactive")
    now = e3.decide("image_gen", explicit_intent=False)
    e4 = PermissionEngine(mode="auto")
    e4.set_session_trusted()
    now2 = e4.decide("skill_install")
    check("G3 现状：无意图生图要确认", now.needs_user, f"rule={now.rule}")
    check("G3 现状：全信任装技能仍要确认", now2.needs_user, f"rule={now2.rule}")


def main():
    part_a_always_confirm()
    part_b_implicit_intent_gate()
    part_c_trust_bound_to_args()
    part_d_run_workflow_gate()
    part_e_session_trust_reset()
    part_f_force_confirm_channel()
    part_g_negative()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
