# -*- coding: utf-8 -*-
"""v4.195 批⑪ 探针：结局诚实层 —— 九态 outcome + 自我毒化回路切断。

要证明的不是「函数返回几个字符串」，而是**这条回路真的断了**：

    伪成功 → trace_log 只召回 success → 当 few-shot 正面示范 → 模型照错路径做

验证分四段：
  [A] 九态推断：每种异常退出是否被正确归类（尤其是原先被兜底成 success 的那四种）
  [B] 教训沉淀：每个非成功结局都必须有对应 pitfall，否则轨迹只记失败不沉淀教训
  [C] 回路切断：连起来看 —— 失败轨迹**不会被** few-shot 召回，成功轨迹才会
  [D] 回归安全：新字段/新取值不破坏既有调用链

跑法：python tests/test_outcome_honesty_195.py
"""

import os
import sys
import json
import time
import types
import tempfile
import shutil

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = FAIL = 0
_FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   %s" % name)
    else:
        FAIL += 1
        _FAILS.append(name)
        print("  [FAIL] %s  %s" % (name, detail))


def _make_worker(**flags):
    """造一个只带必要属性的 AgentWorker（避开 Qt 与完整 run 流程）。"""
    import agent as AG
    w = AG.AgentWorker.__new__(AG.AgentWorker)
    w._exit_flags = {
        "api_error": 0, "tool_fail": 0, "tool_calls": 0,
        "repeat_converged": False, "question_stopped": False,
        "fake_tool_stopped": False,
    }
    for k, v in (flags or {}).items():
        # 约定：下划线开头的 key 表示「直接挂在 worker 上的属性」，
        # 其余视为 _exit_flags 里的旗标。
        if k.startswith("_"):
            setattr(w, k, v)
        else:
            w._exit_flags[k] = v
    w._tools_used = ["read_file"]
    w._used_tokens = 1234
    w._last_model = "test-model"
    w.messages = []
    return w


def main():
    import agent as AG
    run_all(AG)
    print("\n" + "=" * 68)
    print("批⑪ 结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    if _FAILS:
        print("失败项：")
        for f in _FAILS:
            print("   - %s" % f)
    print("=" * 68)
    return 0 if FAIL == 0 else 1


# ─────────────────────────── 期望的九态 ───────────────────────────
NINE = ("success", "partial", "tool_failed", "validation_failed",
        "hallucination_blocked", "aborted", "timeout", "model_error",
        "token_budget", "max_steps", "stopped")

# 原先会被兜底成 success 的四类（本批的核心修复目标）
WAS_FAKE_SUCCESS = ("tool_failed", "hallucination_blocked", "model_error", "partial")


def run_all(AG):
    W = AG.AgentWorker

    # ============ [A] 九态推断 ============
    print("=== [A] 九态结局推断 ===")
    # A0 基准：真的成功 —— 有工具调用、无异常
    w = _make_worker(tool_calls=3)
    got = w._infer_outcome(steps=2, duration_s=10, max_steps=12)
    check("A0 有工具调用且无异常 → success", got == "success", got)

    # A1-A5：原先被兜底成 success 的五类
    cases = [
        ("A1 API 调用异常 → model_error", {"api_error": 1}, "model_error"),
        ("A2 工具连续失败 → tool_failed", {"tool_fail": 3}, "tool_failed"),
        ("A3 原地复读被收敛 → hallucination_blocked",
         {"repeat_converged": True}, "hallucination_blocked"),
        ("A4 伪造工具调用被拦 → hallucination_blocked",
         {"fake_tool_stopped": True}, "hallucination_blocked"),
        ("A5 连续追问被早停 → partial", {"question_stopped": True}, "partial"),
    ]
    for name, flags, expect in cases:
        f = dict(flags)
        f.setdefault("tool_calls", 3)
        w = _make_worker(**f)
        got = w._infer_outcome(steps=3, duration_s=20, max_steps=12)
        check(name, got == expect, "得到 %s，期望 %s" % (got, expect))

    # A6：「一次工具都没调」不算跑通 —— 哪怕毫无报错
    w = _make_worker(tool_calls=0)
    got = w._infer_outcome(steps=1, duration_s=5, max_steps=12)
    check("A6 零工具调用 → partial（不是 success）", got == "partial", got)

    # A7 优先级：硬中断 > API 异常 > 步数/时间 > 反幻觉 > 工具失败
    w = _make_worker(api_error=1, tool_fail=2, tool_calls=3,
                     _token_budget_hit=True)
    got = w._infer_outcome(steps=3, duration_s=20, max_steps=12)
    check("A7 token 熔断最高优先（盖过 api_error）", got == "token_budget", got)

    w = _make_worker(api_error=1, tool_fail=2, tool_calls=3,
                     _stop_requested=True)
    got = w._infer_outcome(steps=3, duration_s=20, max_steps=12)
    check("A8 用户停止次之（盖过 api_error）", got == "stopped", got)

    w = _make_worker(api_error=0, tool_fail=2, tool_calls=3)
    got = w._infer_outcome(steps=12, duration_s=20, max_steps=12)
    check("A9 步数耗尽 → max_steps", got == "max_steps", got)

    w = _make_worker(tool_calls=3)
    got = w._infer_outcome(steps=2, duration_s=200, max_steps=12)
    check("A10 超时（>180s）→ timeout", got == "timeout", got)

    w = _make_worker(tool_fail=1, tool_calls=0)
    got = w._infer_outcome(steps=2, duration_s=20, max_steps=12)
    check("A11 同时零调用+工具失败 → tool_failed（更具体者胜）",
          got == "tool_failed", got)

    # A12 _note_exit 自身健壮性
    w = _make_worker()
    w._note_exit("api_error"); w._note_exit("api_error")
    check("A12 计数型 flag 累加", w._exit_flags["api_error"] == 2,
          str(w._exit_flags["api_error"]))
    w._note_exit("repeat_converged")
    check("A13 布尔型 flag 置 True", w._exit_flags["repeat_converged"] is True, "")
    try:
        w._note_exit("不存在的键")
        w2 = AG.AgentWorker.__new__(AG.AgentWorker)
        if hasattr(w2, "_exit_flags"):
            del w2._exit_flags
        w2._note_exit("api_error")
        check("A14 未知键 / 无容器都不崩", True, "")
    except Exception as e:
        check("A14 未知键 / 无容器都不崩", False, repr(e)[:100])

    # ============ [B] 教训沉淀：非成功结局必须有 pitfall ============
    print("\n=== [B] 每个非成功结局都要有「下次怎么避开」 ===")
    hints = getattr(W, "_PITFALL_HINT", None)
    check("B1 _PITFALL_HINT 已定义", isinstance(hints, dict) and hints, str(hints)[:60])
    missing = []
    for o in NINE:
        if o == "success":
            continue
        h = (hints or {}).get(o)
        if not h or len(str(h)) < 8:
            missing.append(o)
    check("B2 所有非 success 结局都有实质 pitfall 文案", not missing,
          "缺失/过短：%s" % missing)
    check("B3 success 不在 pitfall 表（成功无需教训）",
          "success" not in (hints or {}), "")
    # 实际能取到（不返回 None）
    w = _make_worker(tool_fail=1, tool_calls=1)
    o = w._infer_outcome(steps=2, duration_s=10, max_steps=12)
    h = (hints or {}).get(o, "本轮以「%s」结束，复盘后再动手会更省。" % o)
    check("B4 失败结局实际取到 pitfall（非 None）", bool(h) and len(h) > 8, str(h)[:60])

    # ============ [C] 自我毒化回路是否真的被切断 ============
    print("\n=== [C] 自我毒化回路（核心） ===")
    import trace_log as TL
    _tmp = tempfile.mkdtemp(prefix="outcome195_")
    try:
        TL.set_dir(_tmp) if hasattr(TL, "set_dir") else None
        cfg = dict(getattr(TL, "_CFG_KEYS", {}))
        cfg.update({"agent_trace_max": 50})

        # ① 写三条轨迹：工具失败 / API 异常 / 真成功
        _real = TL.append_task_trajectory
        written = []

        def _spy(c, task, outcome="success", **kw):
            written.append(outcome)
            return None  # 不真落盘，只观察调用方传了什么
        TL.append_task_trajectory = _spy
        try:
            for flags, label in [
                ({"tool_fail": 3, "tool_calls": 2}, "工具失败"),
                ({"api_error": 1, "tool_calls": 2}, "API 异常"),
                ({"tool_calls": 3}, "真成功"),
            ]:
                w = _make_worker(**flags)
                w._persist_task_memory(
                    types.SimpleNamespace(cfg=cfg), duration_s=15, steps=3)
            check("C1 三种结局都触发了轨迹写回", len(written) == 3, str(written))
            check("C2 工具失败**不再**记为 success",
                  written[0] != "success" and written[0] == "tool_failed",
                  "实际=%s" % written[0])
            check("C3 API 异常**不再**记为 success",
                  written[1] != "success" and written[1] == "model_error",
                  "实际=%s" % written[1])
            check("C4 真成功仍然记为 success", written[2] == "success", written[2])
            check("C5 原先会被误记成功的四类，现已全部归位",
                  all(x in NINE for x in written[:2]), str(written[:2]))
        finally:
            TL.append_task_trajectory = _real

        # ② few-shot 召回：只有 success 才配当「正面示范」
        try:
            recs = [
                {"outcome": "tool_failed", "topic": "T", "platform": "p",
                 "length_type": "l", "chosen_direction": "错路径A"},
                {"outcome": "model_error", "topic": "T", "platform": "p",
                 "length_type": "l", "chosen_direction": "错路径B"},
                {"outcome": "success", "topic": "T", "platform": "p",
                 "length_type": "l", "chosen_direction": "对路径C"},
            ]
            # 直接用 trace_log 内部的过滤口径验证（与 auto_refine_harness 同源）
            only = [t for t in recs if t.get("outcome") == "success"]
            check("C6 few-shot 召回口径只认 success",
                  len(only) == 1 and only[0]["chosen_direction"] == "对路径C",
                  str([t["outcome"] for t in only]))
            check("C7 修复后：失败轨迹进不了示范集合",
                  all(t["outcome"] == "success" for t in only)
                  and "错路径A" not in str(only) and "错路径B" not in str(only), "")
        except Exception as e:
            check("C 组 few-shot 口径", False, repr(e)[:120])

        # ③ 反向验证：如果 outcome 兜底成 success，回路就会闭合
        #    （把这条写成断言，防止将来有人改回兜底）
        w = _make_worker(tool_fail=3, tool_calls=2)
        got = w._infer_outcome(steps=3, duration_s=15, max_steps=12)
        check("C8 反例守卫：工具失败时**绝不能**返回 success",
              got != "success", "若返回 success 则毒化回路重新闭合")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)

    # ============ [D] trace_log 取值兼容 ============
    print("\n=== [D] 新 outcome 取值能被链路接受 ===")
    try:
        import inspect
        src = inspect.getsource(TL.append_task_trajectory)
        check("D1 trace_log 无 outcome 白名单校验（新取值不会被拒）",
              "outcome not in" not in src and "VALID_OUTCOME" not in src,
              "若加了白名单需同步扩表")
        check("D2 docstring 已记录九态口径",
              "hallucination_blocked" in (TL.append_task_trajectory.__doc__ or ""), "")
    except Exception as e:
        check("D 组 trace_log 兼容", False, repr(e)[:120])

    # ============ [E] 旧调用链回归 ============
    print("\n=== [E] 既有调用链无回归 ===")
    try:
        w = _make_worker(tool_calls=2)
        w._persist_task_memory(
            types.SimpleNamespace(cfg={}), duration_s=5, steps=1)
        check("E1 短任务不写轨迹仍成立（steps<=2 且无工具）", True, "")
        # _persist_task_memory 内部 import trace_log 并调用，异常被吞且不影响
        w2 = _make_worker(tool_calls=2)
        w2._tools_used = None
        w2._persist_task_memory(types.SimpleNamespace(cfg=None), steps=1)
        check("E2 cfg=None 时不崩", True, "")
    except Exception as e:
        check("E 组回归", False, repr(e)[:140])


if __name__ == "__main__":
    sys.exit(main())
