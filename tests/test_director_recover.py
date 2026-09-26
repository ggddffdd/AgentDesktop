# -*- coding: utf-8 -*-
"""导演台：回调异常后的紧急收口（导演台模块审查 #3）

背景
----
`director_panel._safe()` 是信号槽的异常兜底，但 v4.166.0 前只写状态栏 + 日志，
**不解锁界面、不转失败态**。后果：任一 `*_ready` 回调抛异常时，界面永久停在
「生成中」（后台线程其实早已结束），用户只能重启程序。

现在 `_safe()` 捕获后会调用 `_emergency_recover()`（幂等）：
解码运行锁 → 阶段转 ERROR → 标记失败回执 → 尽力存现场 → 状态栏给出「可重试」。

本套件离屏验证：解锁、转态、回执、文案、幂等、不覆盖 DONE、以及
`_safe` 确实会触发收口。无网络、无真实生成。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

_app = QApplication.instance() or QApplication([])

import director_panel as dp  # noqa: E402

_n_pass = 0
_n_fail = 0


def check(name, ok, detail=""):
    global _n_pass, _n_fail
    if ok:
        _n_pass += 1
        print(f"  [PASS] {name}")
    else:
        _n_fail += 1
        line = f"  [FAIL] {name}"
        if detail:
            line += f"  —— {detail}"
        print(line)


class FakeApp:
    """够用的假主窗：只提供收口逻辑会碰到的属性。"""

    def __init__(self):
        self.director_busy = True
        self.director_phase = dp.DirectorPhase.RUNNING
        self._director_agent_cancelled = False
        self._director_agent_error = None
        self.director_status = QLabel()
        self.director_log = []
        # 不提供 director_pipeline → _save_session 会直接 return（安全）


def main():
    # ---- ① 基本收口 ----
    a = FakeApp()
    dp._emergency_recover(a, "test_cb")
    check("收口后运行锁被解开", a.director_busy is False,
          repr(a.director_busy))
    check("阶段转为 ERROR", a.director_phase == dp.DirectorPhase.ERROR,
          str(a.director_phase))
    check("标记失败回执（含来源函数名）",
          "界面回调异常" in (a._director_agent_error or "")
          and "test_cb" in (a._director_agent_error or ""),
          repr(a._director_agent_error))
    check("状态栏给出「已解锁 / 可重试」", "已解锁" in a.director_status.text(),
          a.director_status.text()[:60])

    # ---- ② 幂等：连打三次不炸、状态稳定 ----
    ok_idem = True
    try:
        for _ in range(3):
            dp._emergency_recover(a, "test_cb")
    except Exception as e:
        ok_idem = False
        check("幂等（重复收口不抛异常）", False, f"{type(e).__name__}: {e}")
    if ok_idem:
        check("幂等（重复收口不抛异常）", True)
        check("幂等后状态仍一致",
              a.director_busy is False and a.director_phase == dp.DirectorPhase.ERROR)

    # ---- ③ 不覆盖已经出片的 DONE ----
    b = FakeApp()
    b.director_phase = dp.DirectorPhase.DONE
    dp._emergency_recover(b, "x")
    check("不覆盖 DONE（成片态保留）", b.director_phase == dp.DirectorPhase.DONE,
          str(b.director_phase))
    check("DONE 态下仍会解锁", b.director_busy is False)

    # ---- ④ 已取消的任务不误标为「失败」----
    c = FakeApp()
    c._director_agent_cancelled = True
    dp._emergency_recover(c, "x")
    check("已取消的任务不写失败回执（保留 cancelled 语义）",
          c._director_agent_error is None, repr(c._director_agent_error))

    # ---- ⑤ _safe 装饰器确实会触发收口 ----
    d = FakeApp()

    @dp._safe
    def _boom(app):
        raise ValueError("模拟回调崩溃")

    try:
        _boom(d)
    except Exception as e:
        check("_safe 吞掉异常不外抛", False, f"{type(e).__name__}: {e}")
    else:
        check("_safe 吞掉异常不外抛", True)
    check("_safe 捕获后触发收口（解锁）", d.director_busy is False,
          repr(d.director_busy))
    check("_safe 捕获后阶段转 ERROR", d.director_phase == dp.DirectorPhase.ERROR)

    # ---- ⑥ 收口本身绝不抛出（属性缺失的极端对象）----
    class Bare:
        pass

    try:
        dp._emergency_recover(Bare(), "bare")
        check("对只有裸对象的输入也不抛（全 try 包裹）", True)
    except Exception as e:
        check("对只有裸对象的输入也不抛（全 try 包裹）", False,
              f"{type(e).__name__}: {e}")

    print(f"\nPASS={_n_pass}  FAIL={_n_fail}")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
