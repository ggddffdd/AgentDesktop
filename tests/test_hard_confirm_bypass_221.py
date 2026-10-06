# -*- coding: utf-8 -*-
"""v4.221 P1#1：硬确认必须绕过「会话信任」（不可被 session_trusted 放行）。

审查报告 P1#1 指出：app_close / clean_recycle_bin / process_kill / app_kill 本是
EXEC 单值声明，不进 `ALWAYS_CONFIRM`，于是「本次会话全部信任」(`permissions.py`
的 session_trusted 分支) 会直接放行，违背工具描述里的「必须确认」。

修复：把这四个工具声明成 `(RiskClass.EXEC, None, True)`，第三元 True → 进
`ALWAYS_CONFIRM` 硬确认档（位于会话信任之前），任何模式/信任都免不了人工确认。

本判据钉死两件事：
  A) 四工具确实在 ALWAYS_CONFIRM 里；
  B) 普通模式下它们正常需要确认（附带，不依赖修复）；
  C) ★ 关键：即便「本次会话全部信任」(trust_all)，它们仍 needs_user=True ——
     这一条在修复前必 FAIL（会话信任直接放行），是证明修复有效的核心。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from permissions import PermissionEngine          # noqa: E402
from risk import ALWAYS_CONFIRM                    # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


def dec(mode, name, trust_all=False, args=None):
    e = PermissionEngine(mode=mode)
    if trust_all:
        e.set_session_trusted()
    return e.decide(name, args or {}, explicit_intent=True)


TOOLS = {"process_kill", "app_kill", "app_close", "clean_recycle_bin"}


def main():
    print("-- P1#1 硬确认绕过会话信任 --")
    check("A ALWAYS_CONFIRM 含四工具",
          TOOLS <= set(ALWAYS_CONFIRM),
          "missing=%s" % sorted(TOOLS - set(ALWAYS_CONFIRM)))
    for tool in sorted(TOOLS):
        for mode in ("interactive", "auto", "custom"):
            d = dec(mode, tool)
            check("B [%s] %s 需确认" % (mode, tool),
                  d.allowed and d.needs_user,
                  "rule=%s need=%s" % (d.rule, d.needs_user))
    # ★ 核心：全部信任也拦得住（修复前 session_trusted 直接放行 → needs_user=False）
    for tool in sorted(TOOLS):
        d = dec("auto", tool, trust_all=True)
        check("C 全信任下 %s 仍需确认" % tool,
              d.allowed and d.needs_user,
              "rule=%s need=%s" % (d.rule, d.needs_user))
    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
