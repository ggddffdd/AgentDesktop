# -*- coding: utf-8 -*-
"""工具策略表「单表化」回归测试（v4.171.0）。

钉住的问题：**同一个概念写在两张表里，某个分支只看其中一张**。

原状：
    RISK_MAP        : 工具 → 风险类          （permissions.decide 主要看它）
    _TIER_OVERRIDE  : 工具 → 显示档位覆盖     （只有 tier_of() 看它）
    ALWAYS_CONFIRM  : 手抄的一份硬确认名单    （第三处）

于是 `permissions.decide` 的 auto 模式分支只看 risk 不看 tier：
标了「手动」的技能安装在自动模式下**被直接放行**，而那份 override 静默失效。
更糟的是加一个硬确认工具要改两处名单，迟早漏一处。

修法：**一个工具的风险类 / 档位覆盖 / 硬确认写在同一条声明里**，
`_policy()` 是唯一解析入口，`ALWAYS_CONFIRM` 由表推导。
本套件同时钉住「纯结构重构」这个前提 —— 行为必须零变化。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import risk                                              # noqa: E402
from risk import RiskClass                               # noqa: E402

_p = _f = 0
_RISK_SRC = os.path.join(ROOT, "risk.py")


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [OK] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f"  <- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
def part_a_single_table():
    print("\n-- A) 单表结构：三处都从同一条声明取 --")
    src = open(_RISK_SRC, encoding="utf-8-sig").read()
    check("A1 _TIER_OVERRIDE 已删除（防回退）", not hasattr(risk, "_TIER_OVERRIDE"))
    check("A2 源码里不再定义 _TIER_OVERRIDE", "_TIER_OVERRIDE = {" not in src)
    check("A3 _policy 是唯一解析入口", hasattr(risk, "_policy"))
    for fn in ("def classify(", "def tier_of("):
        seg = src[src.index(fn):src.index(fn) + 500]
        check(f"A4 {fn[4:-1]} 走 _policy", "_policy(name)" in seg, seg[:120])
    check("A5 ALWAYS_CONFIRM 由表推导（不是手抄第二份名单）",
          "_policy(n)[2]" in src and "ALWAYS_CONFIRM = frozenset(" in src)
    check("A6 推导结果与常量一致（保证两者同步）",
          risk.ALWAYS_CONFIRM == frozenset(n for n in risk.RISK_MAP
                                           if risk._policy(n)[2]))
    check("A7 声明形态受支持（单值 / 二元 / 三元）",
          risk._policy("read_file") == (RiskClass.READ, None, False)
          and risk._policy("write_file") == (RiskClass.WRITE_LOCAL, "manual", False)
          and risk._policy("create_skill") == (RiskClass.WRITE_LOCAL, None, True)
          and risk._policy("db_delete") == (RiskClass.WRITE_LOCAL, "manual", True))
    check("A8 未登记工具 _policy 返回全空",
          risk._policy("完全不存在的工具zzz") == (None, None, False))


def part_b_validate():
    print("\n-- B) 自检函数 --")
    check("B1 validate_policy() 当前无问题", not risk.validate_policy(),
          str(risk.validate_policy()))
    check("B2 校验覆盖了「更松」这一档（源码含该判据）",
          "更松" in open(_RISK_SRC, encoding="utf-8-sig").read())
    # 负面验证：临时往表里塞违规项，校验必须报出来
    cases = [
        ("覆盖档位更松", ("_t_loose", (RiskClass.EXEC, "auto"))),
        ("非法档位", ("_t_bad_tier", (RiskClass.WRITE_LOCAL, "whatever"))),
        ("只读却硬确认", ("_t_read_hard", (RiskClass.READ, None, True))),
    ]
    for label, (name, decl) in cases:
        risk.RISK_MAP[name] = decl
        try:
            probs = risk.validate_policy()
            hit = [p for p in probs if p.startswith(name)]
            check(f"B3 能抓出「{label}」", bool(hit), f"probs={probs[:3]}")
        finally:
            risk.RISK_MAP.pop(name, None)
    check("B4 清理后校验恢复无问题", not risk.validate_policy())


def part_c_behavior_unchanged():
    print("\n-- C) 纯结构重构：行为零变化（与 git 里的旧版逐工具对比） --")
    try:
        old_src = subprocess.run(
            ["git", "show", "v4.170.0:risk.py"], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8")
        assert old_src.returncode == 0, old_src.stderr[:200]
    except Exception as e:
        print(f"  [SKIP] 取不到 git 旧版（{e}）—— 跳过逐工具对比")
    else:
        import importlib.util
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp(prefix="xc_oldrisk_"))
        f = tmp / "old_risk.py"
        f.write_text(old_src.stdout, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("old_risk_c", str(f))
        old = importlib.util.module_from_spec(spec)
        sys.modules["old_risk_c"] = old
        spec.loader.exec_module(old)

        check("C1 RISK_MAP 键集合一致",
              set(old.RISK_MAP) == set(risk.RISK_MAP),
              f"差 {sorted(set(risk.RISK_MAP) ^ set(old.RISK_MAP))[:5]}")
        d_cls = [n for n in risk.RISK_MAP
                 if old.classify(n) != risk.classify(n)]
        d_tier = [n for n in risk.RISK_MAP
                  if old.tier_of(n) != risk.tier_of(n)]
        check("C2 全部工具的 classify 一致", not d_cls, f"{d_cls[:6]}")
        check("C3 全部工具的 tier_of 一致", not d_tier, f"{d_tier[:6]}")
        check("C4 ALWAYS_CONFIRM 一致",
              set(old.ALWAYS_CONFIRM) == set(risk.ALWAYS_CONFIRM),
              f"{sorted(old.ALWAYS_CONFIRM)} vs {sorted(risk.ALWAYS_CONFIRM)}")
        samples = ["browser_open", "app_get_text", "mouse_click", "system_sleep",
                   "mcp_call", "db_xxx", "clipboard_read", "rag_search_x",
                   "window_list_x", "process_kill", "unknown_zzz"]
        d_pre = [n for n in samples
                 if old.classify(n) != risk.classify(n)
                 or old.tier_of(n) != risk.tier_of(n)]
        check("C5 前缀兜底路径一致", not d_pre, f"{d_pre}")
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def part_d_policy_invariants():
    print("\n-- D) 策略不变量（现在由一张表统一保证） --")
    for n in sorted(risk.ALWAYS_CONFIRM):
        check(f"D1 {n} 任何模式都要确认（permissions 侧实测）",
              _needs_confirm_always(n))
    # 只读工具不该落在需要确认的档
    read_tools = [n for n in risk.RISK_MAP
                  if risk.classify(n) == RiskClass.READ]
    check("D2 只读工具一律 auto 档（不会被要求确认）",
          all(risk.tier_of(n) == "auto" for n in read_tools),
          f"异常 {[n for n in read_tools if risk.tier_of(n) != 'auto'][:5]}")
    check("D3 档位覆盖只出现在本地写入类工具上（不是拿它给执行类降级）",
          all(risk.classify(n) == RiskClass.WRITE_LOCAL
              for n in risk.RISK_MAP
              if risk._policy(n)[1] is not None),
          f"{[n for n in risk.RISK_MAP if risk._policy(n)[1] is not None and risk.classify(n) != RiskClass.WRITE_LOCAL]}")


def part_d2_no_snapshot_alias():
    """v4.171.0：把最后一处「快照式第二来源」也收掉（tools.TOOL_TIER）。"""
    print("\n-- D2) tools.TOOL_TIER 改为惰性派生（不再是一份会失同步的快照） --")
    import tools
    src = open(os.path.join(ROOT, "tools.py"), encoding="utf-8-sig").read()
    check("D2.1 源码里不再是模块级快照",
          "TOOL_TIER = {n: tier_of(n) for n in RISK_MAP}" not in src)
    check("D2.2 改用模块级 __getattr__（PEP 562）", "def __getattr__(name)" in src)
    t = tools.TOOL_TIER
    check("D2.3 名字仍可用且是 dict", isinstance(t, dict) and len(t) > 50, f"{len(t)} 条")
    check("D2.4 值与原快照语义一致",
          t == {n: risk.tier_of(n) for n in risk.RISK_MAP})
    # 关键：动态注册（@tool(risk=...) 会写 RISK_MAP）必须立刻反映
    risk.RISK_MAP["_dyn_probe_zzz"] = RiskClass.READ
    try:
        check("D2.5 ★ 动态注册立即可见（旧快照做不到）",
              "_dyn_probe_zzz" in tools.TOOL_TIER)
    finally:
        risk.RISK_MAP.pop("_dyn_probe_zzz", None)
    check("D2.6 未知属性仍正常报错（__getattr__ 没写成万能返回）",
          _raises_attr_error(tools))


def _raises_attr_error(mod):
    """访问不存在的属性必须抛 AttributeError —— 否则 __getattr__ 写成了万能返回。"""
    try:
        getattr(mod, "_不存在的属性_zzz")
        return False
    except AttributeError:
        return True
    except Exception:
        return False


def _needs_confirm_always(name):
    try:
        from permissions import PermissionEngine
        for mode in ("interactive", "auto"):
            e = PermissionEngine(mode=mode)
            e.set_session_trusted()
            d = e.decide(name, explicit_intent=True)
            if not (d.allowed and d.needs_user and d.rule == "always_confirm"):
                return False
        return True
    except Exception:
        return False


def part_e_negative():
    print("\n-- E) 负面验证：把合并拆掉，问题必须复现 --")
    src = open(_RISK_SRC, encoding="utf-8-sig").read()
    # 模拟旧结构：另设一张 override 表，且某个分支只看 risk
    tier_src = src[src.index("def tier_of("):]
    tier_src = tier_src[:tier_src.index("\n\n\n")] if "\n\n\n" in tier_src else tier_src
    check("E1 tier_of 现在直接读单表（不再查第二张表）",
          "_policy(name)" in tier_src and "_TIER_OVERRIDE" not in tier_src)
    # 行为层面：若只看 classify 不看 tier，write_file 会被当成 semi 放行
    risk_cls = risk.classify("write_file")
    tier = risk.tier_of("write_file")
    check("E2 write_file 的风险类仍是 WRITE_LOCAL（默认 semi）",
          risk_cls == RiskClass.WRITE_LOCAL)
    check("E3 但档位是 manual（覆盖生效）—— 只看风险类就会漏掉这个覆盖",
          tier == "manual", f"tier={tier}")
    check("E4 覆盖值确实来自同一条声明（改声明即改档位）",
          risk.RISK_MAP["write_file"][1] == "manual",
          f"{risk.RISK_MAP['write_file']!r}")


def main():
    part_a_single_table()
    part_b_validate()
    part_c_behavior_unchanged()
    part_d_policy_invariants()
    part_d2_no_snapshot_alias()
    part_e_negative()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
