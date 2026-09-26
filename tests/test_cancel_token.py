# -*- coding: utf-8 -*-
"""统一取消令牌（cancel_token.py）回归测试。

钉住的行为：
  · 一次性 + 幂等：重复 cancel 不覆盖首次原因/阶段
  · 父链继承：父取消 → 子即取消；子取消不影响父
  · 阶段追踪与兜底：cancel 未给 stage 时回落最后经过的阶段（供终态归类）
  · 回调：注册即触发（已取消后注册也不丢事件）、回调内再操作不自死锁
  · raise_if_cancelled：未取消不抛，已取消抛 CancelledError 且带 reason/stage
  · 线程安全：多线程同时 cancel 只有一个赢

纯标准库，无 Qt 依赖。
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cancel_token as ct   # noqa: E402

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
    print("=== 1) 初始态与阶段追踪 ===")
    t = ct.CancellationToken(name="run")
    check("初始未取消", not t.is_cancelled)
    check("初始 reason 为空", t.reason == "")
    check("初始 stage 为空", t.stage == "")
    t.mark_stage("dispatch")
    check("mark_stage 生效", t.last_stage == "dispatch")
    check("未取消时 stage 仍为空（stage 只表达取消点）", t.stage == "")

    print("\n=== 2) 一次性 + 幂等 ===")
    t = ct.CancellationToken(name="t")
    first = t.cancel("用户点了停止", stage="wave_dispatch")
    second = t.cancel("另一个原因", stage="other")
    check("第一次 cancel 返回 True", first is True)
    check("重复 cancel 返回 False", second is False)
    check("原因保留首次（不被覆盖）", t.reason == "用户点了停止",
          f"实际 {t.reason!r}")
    check("阶段保留首次", t.stage == "wave_dispatch", f"实际 {t.stage!r}")
    check("cancelled_at 已记录", t.cancelled_at > 0)

    print("\n=== 3) 阶段兜底：cancel 未给 stage → 用最后经过的阶段 ===")
    t = ct.CancellationToken(name="t")
    t.mark_stage("vlm_download")
    t.cancel("断网了")
    check("stage 回落到 last_stage", t.stage == "vlm_download", f"实际 {t.stage!r}")

    t2 = ct.CancellationToken(name="t2")
    t2.cancel("无阶段可回落")
    check("无 last_stage 时 stage 为空串（不崩）", t2.stage == "")

    print("\n=== 4) 父链继承 ===")
    parent = ct.CancellationToken(name="parent")
    child = parent.child("wave1/role")
    check("父未取消时子未取消", not child.is_cancelled)
    parent.cancel("整场停")
    check("父取消后子视为已取消", child.is_cancelled)
    check("子继承父的 reason", child.reason == "整场停", f"实际 {child.reason!r}")
    check("owner 指向真正取消的那个（父）", child.owner is parent)
    check("父自身也取消", parent.is_cancelled)

    p2 = ct.CancellationToken(name="p2")
    c2 = p2.child("c2")
    c2.cancel("只停这一波")
    check("子取消不影响父", not p2.is_cancelled)
    check("子自身已取消", c2.is_cancelled)

    # 多级：孙（c2 已取消 → 它的子 g 立即继承为已取消）
    g = c2.child("grand")
    check("多级继承：已取消的父 → 新生的孙立即视为已取消", g.is_cancelled)
    check("孙继承的是 c2 的原因（不是 p2 的）", g.reason == "只停这一波",
          f"实际 {g.reason!r}")

    # 另一条链：整链未取消 → 祖父取消传到孙
    pa = ct.CancellationToken(name="pa")
    mid = pa.child("mid")
    gk = mid.child("gk")
    check("三级链初始未取消", not gk.is_cancelled)
    pa.cancel("整场停")
    check("祖父取消 → 孙也取消", gk.is_cancelled)
    check("孙取到祖父的原因", gk.reason == "整场停", f"实际 {gk.reason!r}")

    print("\n=== 5) 回调 ===")
    t = ct.CancellationToken(name="t")
    got = []
    t.on_cancel(lambda tok: got.append(tok.reason))
    check("未取消时回调不触发", got == [])
    t.cancel("停了")
    check("取消时回调触发一次", got == ["停了"], f"实际 {got}")

    t2 = ct.CancellationToken(name="t2")
    t2.cancel("先取消")
    got2 = []
    t2.on_cancel(lambda tok: got2.append(tok.reason))
    check("已取消后注册 → 立即触发（不丢事件）", got2 == ["先取消"], f"实际 {got2}")

    # 回调内再操作令牌 → 不应自死锁
    t3 = ct.CancellationToken(name="t3")

    def _cb(tok):
        tok.is_cancelled        # 读
        tok.cancel("二次")       # 再取消（幂等，应直接返回 False）
        tok.state()             # 再取快照

    t3.on_cancel(_cb)
    done = {"ok": False}

    def _run():
        try:
            t3.cancel("首次")
            done["ok"] = True
        except Exception as e:
            done["err"] = repr(e)

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout=5)
    check("回调内再操作令牌不自死锁", not th.is_alive() and done.get("ok") is True,
          f"alive={th.is_alive()} done={done}")

    # 回调抛异常 → 不影响 cancel 本身
    t4 = ct.CancellationToken(name="t4")
    t4.on_cancel(lambda tok: (_ for _ in ()).throw(RuntimeError("回调炸了")))
    ok = t4.cancel("试试")
    check("回调抛异常不影响 cancel", ok is True and t4.is_cancelled)

    print("\n=== 6) raise_if_cancelled ===")
    t = ct.CancellationToken(name="t")
    try:
        t.raise_if_cancelled("model_call")
        check("未取消时不抛", True)
    except ct.CancelledError:
        check("未取消时不抛", False, "却抛了")
    check("raise_if_cancelled 会记录阶段", t.last_stage == "model_call")

    t.cancel("中途被停")
    try:
        t.raise_if_cancelled("tool_call")
        check("已取消时抛 CancelledError", False, "没抛")
    except ct.CancelledError as e:
        check("已取消时抛 CancelledError", True)
        check("异常带 reason", e.reason == "中途被停", f"实际 {e.reason!r}")
        check("异常带 stage", e.stage in ("model_call", "tool_call"),
              f"实际 {e.stage!r}")

    # 取消时未标注 stage → 异常里回落到最后经过的阶段
    t2 = ct.CancellationToken(name="t2")
    t2.raise_if_cancelled("vlm_poll")
    t2.cancel("轮询中被停")
    try:
        t2.raise_if_cancelled("vlm_poll")
        check("fallback stage", False)
    except ct.CancelledError as e:
        check("异常 stage 回落到最后经过的阶段", e.stage == "vlm_poll",
              f"实际 {e.stage!r}")

    print("\n=== 7) state() 快照 ===")
    t = ct.CancellationToken(name="snap")
    s = t.state()
    check("快照含 cancelled=False", s["cancelled"] is False)
    check("快照含 name", s["name"] == "snap")
    t.cancel("停", stage="wave")
    s = t.state()
    check("快照反映取消", s["cancelled"] is True and s["reason"] == "停")
    check("快照 by_self=True", s["by_self"] is True)

    child = ct.CancellationToken(name="c", parent=t)
    sc = child.state()
    check("子的快照 cancelled=True（继承）", sc["cancelled"] is True)
    check("子的快照 by_self=False（不是自己取消的）", sc["by_self"] is False)

    print("\n=== 8) 线程安全：并发 cancel 只有一个赢 ===")
    t = ct.CancellationToken(name="race")
    wins = []
    lock = threading.Lock()

    def _race(i):
        r = t.cancel(f"来自-{i}")
        with lock:
            wins.append(r)

    ths = [threading.Thread(target=_race, args=(i,), daemon=True) for i in range(24)]
    for x in ths:
        x.start()
    for x in ths:
        x.join(timeout=5)
    check("并发 cancel 恰好 1 个返回 True", sum(1 for w in wins if w) == 1,
          f"实际 True 数={sum(1 for w in wins if w)}")
    check("全部线程结束（无死锁）", all(not x.is_alive() for x in ths))
    check("取消后状态稳定", t.is_cancelled)

    print("\n=== 9) 并发读写不炸 ===")
    t = ct.CancellationToken(name="rw")
    stop = threading.Event()
    errs = []

    def _reader():
        while not stop.is_set():
            try:
                t.is_cancelled
                t.state()
                t.mark_stage("x")
            except Exception as e:
                errs.append(repr(e))
                return

    readers = [threading.Thread(target=_reader, daemon=True) for _ in range(6)]
    for r in readers:
        r.start()
    t.cancel("停")
    stop.set()
    for r in readers:
        r.join(timeout=5)
    check("并发读写无异常", not errs, f"errs={errs[:3]}")

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
