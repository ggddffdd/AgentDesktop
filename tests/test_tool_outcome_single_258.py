# -*- coding: utf-8 -*-
r"""v4.258.0 判据：工具成败判定 `_tool_outcome` 归一到单一真源（P1-6）。

judge-first：本文件先于实现编写，预期红。改完源码后转绿。

来源：外部评审报告 P1-6（「两个 `_tool_outcome`」）。**实地核对后发现问题比
报告说的更严重**，三条实证摆在这里：

  ① 两份实现**已经漂移**。同一个 ToolResult（`msg` 是抛异常的 property）：
       · `agent_task_mixin.py:257`  → 崩（RuntimeError 冒泡，无 try 保护）
       · `agent_result_mixin.py:100` → (False, '')
     不是"重复代码"，是"两份判定、结论不同"。

  ② **运行时跑的是没保护的那份**。`AgentWorker(AgentTaskMixin, AgentLoopMixin,
     AgentResultMixin, ...)` —— MRO 里 AgentTaskMixin 在前，`agent_task_mixin:257`
     遮蔽了 `agent_result_mixin:100`。后者是**死代码**。

  ③ 判据测了个寂寞。`tests/test_toolresult_wiring_226.py` 的桩
     `_FakeWorker(AgentResultMixin)` **只继承 AgentResultMixin**，于是那 44 条
     判据全打在**运行时根本不会被调用**的影子实现上。真出 bug 的那份零覆盖。

修法方向（本判据此为准绳）：
  · 判定逻辑抽到**单一真源**（`tool_contract.tool_outcome` 纯函数）；
  · 两个 mixin 保留 `_tool_outcome` 方法名并**委托**过去（agent.py:1513/1663 不动）；
  · 语义取**健壮的那份**（带 try 保护），只加不减；
  · 判据一律打在**与运行时 MRO 一致**的宿主上，杜绝再测影子。

纪律：行为级断言（走真实返回值，不查源码字符串）；反向用例必配；fail-open 保持
（注入判据报错 → 按成功记，宁可少提示不可误报未完成）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def ck(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, extra))


print("=" * 74)
print("v4.258.0 工具成败判定单一真源判据")
print("=" * 74)

# ---------- 公共夹具 ----------
class _TR(object):
    """最小 ToolResult 替身（只带判定要读的字段）。"""

    def __init__(self, ok, msg="", error_code=None, error=None):
        self.ok = ok
        self.msg = msg
        self.error_code = error_code
        self.error = error


class _Boom(object):
    """`msg` 是抛异常的 property —— 两份实现的分水岭。"""

    ok = False
    error_code = None
    error = None

    @property
    def msg(self):
        raise RuntimeError("msg 访问崩了")


class _OkBoom(object):
    """`.ok` 访问抛异常 —— 考察 fail-open。"""

    error_code = None
    error = None
    msg = "x"

    @property
    def ok(self):
        raise RuntimeError("ok 访问崩了")


# ================================================================ T1 单一真源
print("\n[T1] 单一真源：判定逻辑必须只有一份")
try:
    import tool_contract as tc
    _fn = getattr(tc, "tool_outcome", None)
    ck(_fn is not None, "T1 tool_contract.tool_outcome 纯函数存在")
except Exception as e:  # noqa: BLE001
    _fn = None
    ck(False, "T1 tool_contract 导入", repr(e))

if _fn is not None:
    # 纯函数可直接单测（不依赖 mixin / Qt）
    ck(_fn(_TR(True, "已写入 5 字符"), "write_file", "已写入", lambda *a: False)
       == (True, None),
       "T1-1 ok=True → (True, None)",
       "→ %r" % (_fn(_TR(True), "w", "s", lambda *a: False),))
    _r = _fn(_TR(False, "写入失败", error_code="TOOL_FAILED"), "w", "s",
             lambda *a: False)
    ck(_r[0] is False and "TOOL_FAILED" in str(_r[1]),
       "T1-2 ok=False → err_head 带机器可读错误码", "→ %r" % (_r,))

# T1-3 行为级单一真源：打补丁后，**两个 mixin 入口都必须受影响**
print("\n[T1-3] 打补丁验证：两个入口都真的走同一份实现")
try:
    import agent_task_mixin as tm
    import agent_result_mixin as rm

    # 与运行时 MRO 一致的宿主（AgentTaskMixin 在前，和 AgentWorker 同序）
    class _RuntimeLike(tm.AgentTaskMixin, rm.AgentResultMixin):
        pass

    class _ResultOnly(rm.AgentResultMixin):
        pass

    _h1 = _RuntimeLike()
    _h2 = _ResultOnly()
    _orig = getattr(tc, "tool_outcome", None)
    if _orig is None:
        ck(False, "T1-3 单一真源打补丁", "tool_contract.tool_outcome 不存在")
    else:
        _sentinel = {"hit": 0}

        def _spy(*a, **k):
            _sentinel["hit"] += 1
            return (True, "SPY")

        tc.tool_outcome = _spy
        try:
            _o1 = _h1._tool_outcome(_TR(True), "w", "s", lambda *a: False)
            _o2 = _h2._tool_outcome(_TR(True), "w", "s", lambda *a: False)
        finally:
            tc.tool_outcome = _orig
        ck(_sentinel["hit"] == 2,
           "T1-3 两个 mixin 入口**都**走 tool_contract.tool_outcome（真归一）",
           "→ 命中 %d 次（应为 2）" % _sentinel["hit"])
        ck(_o1 == (True, "SPY") and _o2 == (True, "SPY"),
           "T1-3 两个入口返回值一致（不再各判各的）",
           "→ %r / %r" % (_o1, _o2))
except Exception as e:  # noqa: BLE001
    ck(False, "T1-3 单一真源打补丁", repr(e))

# ================================================================ T2 不再漂移
print("\n[T2] 两份不再漂移：同一个输入必须同一个结论")
try:
    _h1 = _RuntimeLike()
    _h2 = _ResultOnly()
    _never_fail = lambda *a: False  # noqa: E731

    _boom1 = None
    _crash1 = None
    try:
        _boom1 = _h1._tool_outcome(_Boom(), "w", "s", _never_fail)
    except Exception as e:  # noqa: BLE001
        _crash1 = e
    ck(_crash1 is None,
       "T2-1 运行时生效那份：msg 访问崩了也不许冒泡（现状会崩）",
       "→ %r" % _crash1)

    _boom2 = None
    _crash2 = None
    try:
        _boom2 = _h2._tool_outcome(_Boom(), "w", "s", _never_fail)
    except Exception as e:  # noqa: BLE001
        _crash2 = e
    ck(_crash2 is None, "T2-2 result_mixin 那份同样不崩", "→ %r" % _crash2)
    ck(_boom1 == _boom2 == (False, ""),
       "T2-3 两份结论严格相等（漂移已消除）",
       "→ %r / %r" % (_boom1, _boom2))

    # 反向：正常对象两份也必须一致（别只对齐了异常分支）
    _n1 = _h1._tool_outcome(_TR(False, "写入失败", error_code="TOOL_FAILED"),
                            "write_file", "写入失败", _never_fail)
    _n2 = _h2._tool_outcome(_TR(False, "写入失败", error_code="TOOL_FAILED"),
                            "write_file", "写入失败", _never_fail)
    ck(_n1 == _n2, "T2-4 正常失败对象两份也一致", "→ %r / %r" % (_n1, _n2))
except Exception as e:  # noqa: BLE001
    ck(False, "T2 漂移消除", repr(e))

# ================================================================ T3 不回归
print("\n[T3] 不回归：既有口径逐条保住（只加不减）")
try:
    _h = _RuntimeLike()

    # 非结构化 → 走注入的旧判据
    ck(_h._tool_outcome(None, "x", "抓取失败：超时", lambda *a: True)
       == (False, "抓取失败：超时"),
       "T3-1 非 ToolResult：判据说失败 → (False, 前300字)",
       "→ %r" % (_h._tool_outcome(None, "x", "抓取失败：超时", lambda *a: True),))
    ck(_h._tool_outcome(None, "x", "已写入", lambda *a: False) == (True, None),
       "T3-2 非 ToolResult：判据说成功 → (True, None)")

    def _bad_checker(*a):
        raise RuntimeError("注入判据崩了")

    ck(_h._tool_outcome(None, "x", "任意", _bad_checker) == (True, None),
       "T3-3 注入判据崩 → fail-open 按成功记（v4.225 同口径）",
       "→ %r" % (_h._tool_outcome(None, "x", "任意", _bad_checker),))

    # .ok 访问崩 → fail-open
    ck(_h._tool_outcome(_OkBoom(), "x", "任意", _bad_checker) == (True, None),
       "T3-4 .ok 访问崩 → fail-open 按成功记",
       "→ %r" % (_h._tool_outcome(_OkBoom(), "x", "任意", _bad_checker),))

    # err_head 截断 300
    _long = _h._tool_outcome(_TR(False, "长" * 500), "x", "y", lambda *a: False)
    ck(_long[1] is not None and len(_long[1]) <= 300,
       "T3-5 err_head 截到 300 字（与旧口径同长度）",
       "→ len=%d" % (len(_long[1]) if _long[1] else -1))

    # error_code 优先于 error，两者都优先于 msg
    _pri = _h._tool_outcome(
        _TR(False, "msg 兜底", error_code="CODE_A", error="ERR_B"),
        "x", "y", lambda *a: False)
    ck("CODE_A" in str(_pri[1]) and "ERR_B" in str(_pri[1]),
       "T3-6 error_code / error 都进 err_head（机器可读优先）", "→ %r" % (_pri,))
except Exception as e:  # noqa: BLE001
    ck(False, "T3 不回归", repr(e))

# ================================================================ T4 判据别测影子
print("\n[T4] 运行时那条真的接到单一真源上了（杜绝再测影子实现）")
try:
    import agent as _agent_mod
    _cls = getattr(_agent_mod, "AgentWorker", None)
    ck(_cls is not None, "T4 AgentWorker 可获取")
    if _cls is not None:
        # MRO 顺序必须仍是 Task 在前（本包不改 MRO，只确认宿主没选错）
        _names = [c.__name__ for c in _cls.__mro__]
        ck(_names.index("AgentTaskMixin") < _names.index("AgentResultMixin"),
           "T4-1 宿主 MRO 与运行时一致（Task 在 Result 之前）", "→ %s" % _names[:5])
        # 不打补丁时，运行时那份的结论必须和 result_mixin 那份一致
        _inst = _cls.__new__(_cls)  # 绕开 __init__（不启线程、不碰 Qt）
        _a = _inst._tool_outcome(_Boom(), "w", "s", lambda *a: False)
        _b = _ResultOnly()._tool_outcome(_Boom(), "w", "s", lambda *a: False)
        ck(_a == _b,
           "T4-2 AgentWorker 实例的结论 == result_mixin 单独宿主的结论（无影子）",
           "→ %r / %r" % (_a, _b))
        ck(_a == (False, ""),
           "T4-3 AgentWorker 实例不再因 msg 抛异常而崩", "→ %r" % (_a,))
except Exception as e:  # noqa: BLE001
    ck(False, "T4 运行时接线", repr(e))

print("\n" + "=" * 74)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
