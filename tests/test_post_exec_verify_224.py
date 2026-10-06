# -*- coding: utf-8 -*-
"""执行后验证（审查报告 P2，v4.224）验收判据。

v4.218 报告点名：写文件 / 浏览器 / 系统控制这类**有副作用**的工具，执行完就
照着返回值报成功，没有回头验证副作用是否真的生效（进程真没了？回收站真空了？）。
v4.223 建的「结局契约」只解决「成败别靠文案猜」，不解决「跑完再验一次」。

本轮加**执行后验证**层：
  PV1 验证器注册表：process_kill / clean_recycle_bin 已登记真实信号验证器。
  PV2 语义硬约束——**只降级不升级**：
      验证不通过 → ok 打成 False + error_code=POST_VERIFY_FAILED + 证据尾巴；
      验证通过   → 绝不能把原本失败的调用翻成成功（否则"工具不存在"也能被验成成功）。
  PV3 fail-open：无验证器 / 验证器不表态（查不到真信号）→ 结论一字不动。
  PV4 形态豁免：process_kill 按 PID 终止时不表态（同名进程可能仍有多实例，
      查总数会假红 —— 那不是失败，是我们验错了）。
  PV5 exec_tool 端到端接入：给无害工具挂上"验证不通过"的钩子，调用结果必须被降级。

扰动脚本（退化验证/允许升级/去掉 PID 豁免）跑完后，对应判据必须翻红（哑弹 0）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tool_contract as tc  # noqa: E402
import tools  # noqa: E402
import config  # noqa: E402
import system_control_tools  # noqa: E402
from permissions import Decision  # noqa: E402

_ALLOW = Decision(allowed=True, needs_user=False, reason="test", rule="test")
# sys_info 这类工具需要真 cfg（cfg=None 会崩），端到端用默认配置副本
_CFG = dict(config.DEFAULT_CONFIG)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, detail))


def main():
    # ---------- PV1 验证器注册表 ----------
    print("\n-- PV1 验证器注册表（真实信号，不靠文案）--")
    regs = tc.registered_verifiers()
    check("PV1-1 process_kill 已登记执行后验证器",
          "process_kill" in regs, "实际=%s" % regs)
    check("PV1-2 clean_recycle_bin 已登记执行后验证器",
          "clean_recycle_bin" in regs, "实际=%s" % regs)

    # ---------- PV2 只降级不升级 ----------
    print("\n-- PV2 只降级不升级（核心语义）--")
    # a) 验证不通过 → 必须把 ok 打成 False
    # 用可控的临时验证器，避免依赖真实进程状态
    tc.register_verifier("_pv224_fail", lambda a, r: (False, "同名进程仍残留 3 个"))
    try:
        tr_a = tc.ToolResult(ok=True, msg="已终止", verified=False)
        changed = tc.apply_post_verification(tr_a, "_pv224_fail", {})
        check("PV2-1 验证不通过 → ok 被降级为 False",
              tr_a.ok is False, "ok=%s" % tr_a.ok)
        check("PV2-2 验证不通过 → error_code=POST_VERIFY_FAILED",
              tr_a.error_code == "POST_VERIFY_FAILED",
              "error_code=%s" % tr_a.error_code)
        check("PV2-3 验证不通过 → 证据写进 msg（模型看得见）",
              "执行后验证未通过" in (tr_a.msg or ""), "msg=%s" % tr_a.msg)
        check("PV2-4 验证不通过 → apply 返回 True（确曾降级）",
              changed is True, "changed=%s" % changed)
        check("PV2-5 验证结论标记 verified=True（不再冒充/遗漏）",
              getattr(tr_a, "verified", None) is True,
              "verified=%s" % getattr(tr_a, "verified", None))
    finally:
        tc._VERIFY_REGISTRY.pop("_pv224_fail", None)

    # b) 验证通过 → 绝不能把失败翻成成功
    tc.register_verifier("_pv224_ok", lambda a, r: (True, "已无同名进程"))
    try:
        tr_b = tc.ToolResult(ok=False, msg="工具执行异常", error_code="TOOL_FAILED")
        tc.apply_post_verification(tr_b, "_pv224_ok", {})
        check("PV2-6 验证通过也不许把失败翻成成功（只降级不升级）",
              tr_b.ok is False, "ok=%s" % tr_b.ok)
        check("PV2-7 验证通过不许篡改原 error_code",
              tr_b.error_code == "TOOL_FAILED", "error_code=%s" % tr_b.error_code)
        tr_c = tc.ToolResult(ok=True, msg="ok")
        tc.apply_post_verification(tr_c, "_pv224_ok", {})
        check("PV2-8 验证通过 + 原本成功 → 仍成功", tr_c.ok is True, "ok=%s" % tr_c.ok)
    finally:
        tc._VERIFY_REGISTRY.pop("_pv224_ok", None)

    # ---------- PV3 fail-open ----------
    print("\n-- PV3 fail-open（查不到真信号就不表态）--")
    check("PV3-1 未登记验证器的工具 → verify_after 返回 None",
          tc.verify_after("_no_such_tool_224", {}, None) is None, "")
    tc.register_verifier("_pv224_none", lambda a, r: None)
    try:
        tr_d = tc.ToolResult(ok=True, msg="x")
        changed = tc.apply_post_verification(tr_d, "_pv224_none", {})
        check("PV3-2 验证器不表态 → ok 一字不动", tr_d.ok is True, "ok=%s" % tr_d.ok)
        check("PV3-3 验证器不表态 → 返回 False（未降级）",
              changed is False, "changed=%s" % changed)
        check("PV3-4 验证器不表态 → verified 不被标 True",
              getattr(tr_d, "verified", None) is not True,
              "verified=%s" % getattr(tr_d, "verified", None))
    finally:
        tc._VERIFY_REGISTRY.pop("_pv224_none", None)

    # 验证器抛异常也应 fail-open
    def _boom(a, r):
        raise RuntimeError("boom")

    tc.register_verifier("_pv224_boom", _boom)
    try:
        tr_e = tc.ToolResult(ok=True, msg="x")
        tc.apply_post_verification(tr_e, "_pv224_boom", {})
        check("PV3-5 验证器抛异常 → fail-open，ok 不变",
              tr_e.ok is True, "ok=%s" % tr_e.ok)
    finally:
        tc._VERIFY_REGISTRY.pop("_pv224_boom", None)

    # ---------- PV4 形态豁免（PID 不表态） ----------
    print("\n-- PV4 process_kill 按 PID 终止时不表态（防假红）--")
    v_pid = tc.verify_after("process_kill", {"name": "1234"}, None)
    check("PV4-1 name 为纯数字（PID 形态）→ 不表态",
          v_pid is None, "实际=%s" % (v_pid,))
    v_empty = tc.verify_after("process_kill", {}, None)
    check("PV4-2 缺 name → 不表态", v_empty is None, "实际=%s" % (v_empty,))
    # 按名字形态则必须真的去查（monkey-patch 计数函数，避免依赖真机进程）
    _orig = system_control_tools._count_processes

    def _fake_count(name):
        return 2

    system_control_tools._count_processes = _fake_count
    try:
        v_name = tc.verify_after("process_kill", {"name": "notepad.exe"}, None)
        check("PV4-3 按名字形态 → 会去查并表态（残留 2 → False）",
              isinstance(v_name, tuple) and v_name[0] is False,
              "实际=%s" % (v_name,))
        check("PV4-4 按名字形态 → 证据串说明残留数",
              isinstance(v_name, tuple) and "残留" in v_name[1],
              "实际=%s" % (v_name,))

        def _zero(name):
            return 0

        system_control_tools._count_processes = _zero
        v_zero = tc.verify_after("process_kill", {"name": "notepad.exe"}, None)
        check("PV4-5 查到 0 个 → 表态 True（附带证据）",
              isinstance(v_zero, tuple) and v_zero[0] is True,
              "实际=%s" % (v_zero,))
    finally:
        system_control_tools._count_processes = _orig

    # ---------- PV5 exec_tool 端到端接入 ----------
    print("\n-- PV5 exec_tool 端到端：验证不通过必须降级调用结果 --")
    ws = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "_pv224_ws")
    os.makedirs(ws, exist_ok=True)
    # 载体选 write_file：它有结局契约，文件真落地 → ok=True，降级才观察得到
    probe = os.path.join(ws, "pv224.txt")
    r0 = tools.exec_tool(_CFG, ws, "write_file",
                         {"path": probe, "content": "v4.224"}, perm_ctx=_ALLOW)
    check("PV5-1 前置：write_file 真落地 → ok=True（契约判定）",
          isinstance(r0, tc.ToolResult) and r0.ok is True,
          "ok=%s msg=%s" % (getattr(r0, "ok", None),
                            (getattr(r0, "msg", "") or "")[:80]))

    tc.register_verifier("write_file", lambda a, r: (False, "副作用复查未通过"))
    try:
        r1 = tools.exec_tool(_CFG, ws, "write_file",
                             {"path": probe, "content": "v4.224"}, perm_ctx=_ALLOW)
        check("PV5-2 执行后验证不通过 → 端到端 ok=False（成功被降级）",
              getattr(r1, "ok", None) is False, "ok=%s" % getattr(r1, "ok", None))
        check("PV5-3 端到端 error_code=POST_VERIFY_FAILED",
              getattr(r1, "error_code", None) == "POST_VERIFY_FAILED",
              "error_code=%s" % getattr(r1, "error_code", None))
        check("PV5-4 端到端 msg 带验证证据",
              "执行后验证未通过" in (getattr(r1, "msg", "") or ""),
              "msg=%s" % (getattr(r1, "msg", "") or "")[:120])
    finally:
        tc._VERIFY_REGISTRY.pop("write_file", None)

    # 对照组：不挂验证器时，同一调用不得出现任何验证痕迹，且仍为成功
    r2 = tools.exec_tool(_CFG, ws, "write_file",
                         {"path": probe, "content": "v4.224"}, perm_ctx=_ALLOW)
    check("PV5-5 对照组（无验证器）仍为成功",
          getattr(r2, "ok", None) is True, "ok=%s" % getattr(r2, "ok", None))
    check("PV5-6 对照组（无验证器）msg 无验证痕迹",
          "执行后验证未通过" not in (getattr(r2, "msg", "") or ""),
          "msg=%s" % (getattr(r2, "msg", "") or "")[:120])

    print("\n" + "=" * 60)
    print("PASS=%d FAIL=%d" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
