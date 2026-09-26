# -*- coding: utf-8 -*-
"""军团取消机制（TaskGraph + CancellationToken）回归测试。

钉住审查 §3 要求的三件事：
  1. **未开始**的成员在取消后**不得执行**（不再出现"点了停止还继续扣费"）
  2. **已开始**的成员在拿到令牌检查点时**立刻停手**（不发起下一次调用）
  3. 终态要能区分"谁真干了、谁被停了"——
     cancelled_before_start / cancelled_during_model_call /
     cancelled_after_tool_call / completed（取消前已完成的保留 completed）
  4. 不传令牌时行为与改动前完全一致（回归保护）

纯标准库，无 Qt 依赖。
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cancel_token import CancellationToken, CancelledError   # noqa: E402
from task_graph import TaskGraph                             # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def main():
    print("=== 1) 终态归类（stage → 可读标签）===")
    of = TaskGraph._cancel_state_of
    check("空 stage → before_start", of("") == "cancelled_before_start", of(""))
    check("before_start → before_start", of("before_start") == "cancelled_before_start")
    check("model_call → during_model_call",
          of("model_call") == "cancelled_during_model_call", of("model_call"))
    check("tool_call → after_tool_call",
          of("tool_call") == "cancelled_after_tool_call", of("tool_call"))
    check("自定义阶段 → 带原名兜底",
          of("download").startswith("cancelled_mid_stage"), of("download"))

    print("\n=== 2) 取消后：未开始的成员一个都不执行 ===")
    ran = []
    tg = TaskGraph()
    for i in range(4):
        tg.create(f"m{i}", f"成员{i}", lambda s, i=i: (ran.append(i), s)[1])
    tok = CancellationToken(name="run")
    tok.cancel("用户点了停止", stage="wave_dispatch")   # 派发前就已取消
    out = tg.run({"query": "x"}, token=tok)

    check("★ executor 零调用（没人为停止后的动作付费）", ran == [], ran)
    states = {t.id: t.status for t in tg._tasks.values()}
    check("★ 全部标为 cancelled", all(v == "cancelled" for v in states.values()), states)
    check("终态标签是 cancelled_before_start",
          all((t.result or {}).get("cancel_state") == "cancelled_before_start"
              for t in tg._tasks.values()))
    check("取消原因写进 state", isinstance(out.get("__cancelled__"), dict),
          out.get("__cancelled__"))
    check("取消原因是用户那条", out["__cancelled__"]["reason"] == "用户点了停止",
          out.get("__cancelled__"))

    print("\n=== 3) 执行中取消：已开始的成员立刻停手 ===")
    started = []
    finished = []
    gate = threading.Event()
    tok2 = CancellationToken(name="run2")     # 必须在 _slow 之前建好（闭包要用）

    def _slow(state):
        started.append(1)
        # 模拟"成员跑到一半、用户此刻点了停止"
        gate.wait(timeout=5)
        # 成员在**阶段之间**查令牌（对应 agent_node 的 model_call / tool_call 检查）
        tok2.raise_if_cancelled("model_call")
        finished.append(1)
        return state

    tg2 = TaskGraph()
    tg2.create("a", "成员A", _slow)

    def _cancel_soon():
        # 等 executor 真的开跑，再取消
        for _ in range(200):
            if started:
                break
            time.sleep(0.01)
        tok2.cancel("中途停止", stage="user_stop")
        gate.set()

    th = threading.Thread(target=_cancel_soon, daemon=True)
    th.start()
    out2 = tg2.run({"query": "y"}, token=tok2)
    th.join(timeout=5)

    check("成员确实开跑过（否则测不到「已开始」分支）", len(started) == 1, started)
    check("★ 已开始的成员被中止（未跑到结尾）", finished == [], finished)
    check("★ 该节点标 cancelled（不是 failed）",
          tg2._tasks["a"].status == "cancelled", tg2._tasks["a"].status)
    check("★ 终态标签记下取消发生在模型调用阶段",
          (tg2._tasks["a"].result or {}).get("cancel_state")
          == "cancelled_during_model_call",
          tg2._tasks["a"].result)

    print("\n=== 4) 取消前已完成的成员保留 completed ===")
    ran3 = []
    tg3 = TaskGraph()

    def _ok(state):
        ran3.append(1)
        return {"done": True}

    tg3.create("done1", "先跑完", _ok)
    tok3 = CancellationToken(name="run3")
    out3 = tg3.run({"query": "z"}, token=tok3)
    check("未取消时正常完成", tg3._tasks["done1"].status == "completed",
          tg3._tasks["done1"].status)
    check("executor 正常跑过一次", len(ran3) == 1, ran3)
    check("state 里没有取消标记", "__cancelled__" not in out3)

    print("\n=== 5) 中途取消时，已完成 + 未开始 混合场景 ===")
    order = []
    tg4 = TaskGraph()

    def _first(state):
        order.append("first")
        tok4.cancel("跑完第一个就停", stage="after_first")
        return {"done": 1}

    def _second(state):
        order.append("second")     # 不该出现
        return {}

    tg4.create("w1", "第一波", _first)
    tg4.create("w2", "第二波", _second)
    tok4 = CancellationToken(name="run4")
    out4 = tg4.run({"query": "q"}, token=tok4)

    check("第一个跑完了", "first" in order, order)
    check("★ 第二个没跑（取消后不再派发）", "second" not in order, order)
    check("完成者保持 completed", tg4._tasks["w1"].status == "completed",
          tg4._tasks["w1"].status)
    check("未开始者标 cancelled", tg4._tasks["w2"].status == "cancelled",
          tg4._tasks["w2"].status)

    print("\n=== 6) 回归：不传令牌 → 行为与改动前一致 ===")
    ran5 = []
    tg5 = TaskGraph()

    def _n(state):
        ran5.append(1)
        return {"ok": 1}

    tg5.create("n1", "节点", _n)
    tg5.create("n2", "节点2", _n)
    out5 = tg5.run({"query": "no-token"})       # 不传 token
    check("两个节点都执行", len(ran5) == 2, ran5)
    check("都标 completed",
          all(t.status == "completed" for t in tg5._tasks.values()))
    check("state 无取消标记", "__cancelled__" not in out5)

    print("\n=== 7) 回归：空图仍然直接返回 ===")
    tg6 = TaskGraph()
    check("空图返回原 state（不抛）", tg6.run({"x": 1}, token=None) == {"x": 1})
    check("空图 + 已取消令牌也不炸",
          tg6.run({"x": 1}, token=CancellationToken()) == {"x": 1})

    print("\n=== 8) 回归：失败仍标 failed（取消不影响失败语义）===")
    tg7 = TaskGraph()

    def _boom(state):
        raise RuntimeError("成员崩了")

    tg7.create("bad", "坏节点", _boom)
    tok7 = CancellationToken(name="run7")
    tg7.run({"q": 1}, token=tok7)
    check("异常仍标 failed", tg7._tasks["bad"].status == "failed",
          tg7._tasks["bad"].status)
    check("失败不是 cancelled", tg7._tasks["bad"].status != "cancelled")

    print("\n=== 9) 检查点 2：submit 循环途中被取消（并发窗口）===")
    # 这一条专门覆盖「检查点 2」：submit 循环执行期间被另一个线程取消。
    # 为什么必须单独测：检查点 1 在 while 顶部，通常在它之前就拦住了，
    # 检查点 2 只在"一轮里已开始 submit、尚未提交完"的竞态窗口才起作用 ——
    # 不做注入就永远测不到（实测发现：去掉它，其他场景仍全绿）。
    import task_graph as tgmod

    real_pool = tgmod.ThreadPoolExecutor
    ran6 = []
    tok6 = CancellationToken(name="run6")

    class _TrapPool:
        """在第 1 次 submit 时取消令牌，模拟并发取消。"""
        def __init__(self, *a, **k):
            self._real = real_pool(*a, **k)
            self.calls = 0

        def submit(self, fn, *a, **k):
            self.calls += 1
            if self.calls == 1:
                tok6.cancel("submit 途中取消", stage="submit")
            return self._real.submit(fn, *a, **k)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._real.__exit__(*exc)

    def _member(state, i=None):
        ran6.append(i)
        return {"i": i}

    tgmod.ThreadPoolExecutor = _TrapPool
    try:
        tg6 = TaskGraph()
        for i in range(4):
            tg6.create(f"m{i}", f"成员{i}", lambda s, i=i: _member(s, i))
        tg6.run({"query": "q"}, token=tok6)
    finally:
        tgmod.ThreadPoolExecutor = real_pool

    check("★ 提交途中取消后，后续成员不再被执行", len(ran6) <= 1, ran6)
    states6 = {t.id: t.status for t in tg6._tasks.values()}
    cancelled_n = sum(1 for v in states6.values() if v == "cancelled")
    check("★ 未跑成的成员被标 cancelled", cancelled_n >= 3, states6)
    check("没有成员标 failed（取消 != 失败）",
          not any(v == "failed" for v in states6.values()), states6)

    # 上面只证明了"取消后不执行"，但**检查点 2 与检查点 3 互为冗余**
    # （检查点 3 在 executor 开头兜底），行为层分不出是哪个生效。
    # 所以检查点 2 只能用**源码契约**锁：submit 之前必须有一次取消检查。
    # 不锁的话，将来有人删掉它，所有行为测试仍全绿（实测确认过）。
    import re as _re
    src_tg = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "task_graph.py"), encoding="utf-8").read()
    take = src_tg[src_tg.index("for tid in ready:"):]
    take = take[:take.index("for f in as_completed")]
    check("★ 源码契约：submit 之前存在取消检查（检查点 2 双保险之一）",
          "if token is not None and token.is_cancelled:" in take, take[:120])
    check("★ 源码契约：跳过时标记 cancelled 且 continue（不进线程池）",
          't.status = "cancelled"' in take and "continue" in take)
    check("★ 源码契约：executor 开头也有取消检查（检查点 3）",
          "token.raise_if_cancelled(\"before_start\")" in src_tg)

    print("\n=== 10) 回归：CancelledError 的特征 ===")
    tk = CancellationToken(name="t")
    tk.raise_if_cancelled("tool_call")
    tk.cancel("停")
    try:
        tk.raise_if_cancelled("tool_call")
        check("应抛 CancelledError", False)
    except CancelledError as ce:
        check("CancelledError 是 RuntimeError 子类（兼容既有 except）",
              isinstance(ce, RuntimeError))
        check("stage 可读（用于终态归类）", ce.stage in ("tool_call",), ce.stage)

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
