# -*- coding: utf-8 -*-
r"""截图工具归一到单一真源 —— 判据演进史（v4.260.0 P1-7 → v4.283.2）。

v4.260.0（P1-7）：同族扫描 + 运行时实证查出 `tools.py` 里有一份 **PIL ImageGrab
实现的死代码**（从来没被调用，注册表 `screenshot` 直接指向
`system_control_tools.tool_screenshot`），且两份契约已漂移（入参/停止/取图/返回
全不一样）——死代码是误导源。当时修法：`tools.tool_screenshot` 改成显式委托
wrapper（保留函数名不断潜在引用），判据用「打补丁看命中次数」证明委托真生效。

v4.283.2（做减法）：委托 wrapper 自诞生起同样零调用（注册表从未指向它），
「保留函数名不断引用」的理由不成立——**删除 wrapper**。本判据同步反转：

  · 原 T1/T2/T4/T5（委托行为：命中次数/透传/冒泡）→ 已无 wrapper 可测，
    反转为 **D1/D2 回流守护**：tools.py 不得再定义 tool_screenshot（含属性形式），
    删了又长回来即红；
  · 原 T3/T6（注册真源：注册表仍指向 system_control_tools 生效版）→ **原样保留**，
    这是本议题的锚：生效实现必须在位、单一真源不许被反客为主。

纪律不变：行为级断言优先（注册表剥闭包取真实 handler），源码字符串判据只作辅助。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tools  # noqa: E402
import system_control_tools as sct  # noqa: E402

PASS = 0
FAIL = 0


def ck(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s%s" % (name, ("  -> " + str(extra)) if extra else ""))
    return bool(cond)


def _registry_impl():
    """剥开注册包装，拿到真正被执行的那个函数对象。"""
    v = tools.TOOL_REGISTRY.get("screenshot")
    h = v.get("handler") if isinstance(v, dict) else v
    for c in (getattr(h, "__closure__", None) or ()):
        fn = c.cell_contents
        if callable(fn):
            return fn
    return None


def main():
    print("---- D 回流守护：死 wrapper 不许长回来（v4.283.2 反向判据）----")
    ck(not hasattr(tools, "tool_screenshot"),
       "D1 tools 模块无 tool_screenshot 属性（wrapper 已删）",
       "实测 %r" % (getattr(tools, "tool_screenshot", None),))
    src = open(os.path.join(ROOT, "tools.py"), encoding="utf-8").read()
    ck("def tool_screenshot" not in src,
       "D2 tools.py 源码无 def tool_screenshot（防换形式回流）")
    ck("tool_screenshot" not in src.replace("def tool_screenshot", ""),
       "D3 tools.py 无 tool_screenshot 字样残留（连委托 import 都不留）")

    print("\n---- T3 接线不变：注册表仍指向 system_control_tools 生效版 ----")
    ck("screenshot" in tools.TOOL_REGISTRY, "T3-0 screenshot 仍在注册表")
    impl = _registry_impl()
    ck(impl is sct.tool_screenshot,
       "T3-1 ★ 注册表 screenshot 必须剥出 system_control_tools.tool_screenshot",
       "实测 %r" % (impl,))
    ck(callable(sct.tool_screenshot),
       "T3-2 生效实现健在可调用（单一真源没被连坐删掉）")

    print("\n---- T6 生效版契约锚：入参契约是 system_control_tools 那份 ----")
    import inspect
    try:
        sig = inspect.signature(sct.tool_screenshot)
        params = list(sig.parameters)
        ck("stop_event" in params and "should_stop" in params,
           "T6-1 生效版签名带停止信号（截图可被停止按钮打断）",
           "实测参数 %r" % (params,))
    except (TypeError, ValueError) as e:
        ck(False, "T6-1 取签名失败", repr(e))

    print("\n" + "=" * 70)
    print("截图单一真源判据（v4.260.0 P1-7 + v4.283.2 减法反转）：PASS=%d FAIL=%d"
          % (PASS, FAIL))
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
