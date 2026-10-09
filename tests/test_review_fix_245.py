# -*- coding: utf-8 -*-
"""审查报告第三批修复（v4.245.0）验收判据。

覆盖第三方审查报告（2026-10-09）第三批健壮性/防御项：
  T-3 空批次保护（max_workers=0 防抛 ValueError）
  T-6 后验验证异常留痕（不再 except: pass 静默吞）
  T-8 定时缺时间不再静默补 09:00（反问用户）
  T-4 复读护栏幂等豁免（读类工具重复调用不误当死循环）
  I-7 兜底判据收窄 + 闲聊 False 固定

judge-first：本文件引用的是修复后的行为，实现前必红，实现后转绿。
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_text  # noqa: E402

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


def test_t3_empty_batch():
    print("== T-3 空批次保护 ==")
    import agent
    _rc = inspect.getsource(agent.AgentWorker._run_concurrent)
    _ec = inspect.getsource(agent.AgentWorker._exec_tool_calls)
    check("T-3 _run_concurrent 有空批次保护", "if not tool_calls:" in _rc)
    check("T-3 _exec_tool_calls 有空批次保护", "if not tool_calls:" in _ec)


def test_t6_verify_log():
    print("== T-6 后验验证异常留痕 ==")
    import tools
    _src = inspect.getsource(tools.exec_tool)
    check("T-6 后验验证异常有 log.warning 留痕",
          "后验验证异常" in _src and "log.warning" in _src)


def test_t8_at_time_ask():
    print("== T-8 定时缺时间不再静默补 09:00 ==")
    import tools
    _src = inspect.getsource(tools._h_create_automation)
    check("T-8 缺时间时反问补充", "补充" in _src and "at_time" in _src)
    # 行为级：daily 缺时间 → 反问，不静默补 09:00（不创建任务）
    r = tools._h_create_automation(
        None, ".", {"name": "喝水提醒", "message": "喝水", "schedule_type": "daily"})
    check("T-8 daily 缺时间返回反问而非创建", "请补充" in (r[0] or ""), str(r[0]))


def test_t4_idempotent():
    print("== T-4 复读护栏幂等豁免 ==")
    import agent
    _src = inspect.getsource(agent.AgentWorker._exec_tool_calls)
    check("T-4 幂等读类豁免集合存在", "_IDEMPOTENT_READ" in _src)


def test_i7_chitchat_false():
    print("== I-7 闲聊不判 action（兜底收窄 + 固定） ==")
    for s in ("今天天气不错", "这个方案我觉得挺好的", "随便聊聊",
              "快看这个", "动一下"):
        got = agent_text._detect_action_intent([{"role": "user", "content": s}])
        check("I-7 闲聊不判 action：%s" % s, got is False, "got=%s" % got)


def test_t5_gate_needs_user():
    print("== T-5 闸门校验 needs_user（第二道锁） ==")
    from permissions import Decision
    import tools
    d1 = Decision(allowed=True, needs_user=True, reason="需确认", rule="risk")
    ok1, _ = tools._permission_gate("write_file", {"path": "x"}, d1)
    check("T-5 needs_user 未确认 → 拒绝", ok1 is False, str(ok1))
    d2 = Decision(allowed=True, needs_user=True, reason="需确认", rule="risk",
                  confirmed=True)
    ok2, _ = tools._permission_gate("write_file", {"path": "x"}, d2)
    check("T-5 needs_user 已确认 → 放行", ok2 is True, str(ok2))
    d3 = Decision(allowed=True, needs_user=False, reason="ok", rule="auto")
    ok3, _ = tools._permission_gate("write_file", {"path": "x"}, d3)
    check("T-5 无需确认 → 放行", ok3 is True, str(ok3))


def main():
    test_t3_empty_batch()
    test_t6_verify_log()
    test_t8_at_time_ask()
    test_t4_idempotent()
    test_t5_gate_needs_user()
    test_i7_chitchat_false()
    print("\nREVIEW_FIX_245 PASS=%d FAIL=%d" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
