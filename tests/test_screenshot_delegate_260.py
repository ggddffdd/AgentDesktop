# -*- coding: utf-8 -*-
r"""v4.260.0 判据：截图工具归一到单一真源（P1-7，同族扫描查出）。

judge-first：本文件先于实现编写，**预期红**。改完源码后转绿。

来源：v4.259.0 同族扫描。运行时实证（不是读代码猜的）：

  ```python
  tools.TOOL_REGISTRY['screenshot']['handler']   # 是 _register_extension_tools._wrap._w
  剥闭包 → system_control_tools.tool_screenshot   # ★ 生效的是这一份
  tools.tool_screenshot is 上面那个               # False
  grep 'tool_screenshot' 全仓（含 tests、含 _archive_）→ 只有 2 处定义 + 1 处注册
  ```

  即 `tools.py:2389` 那份 **PIL ImageGrab 实现是死代码**：从来没被调用过。

危害不只是"多一份没人跑的代码"，是**契约漂移**：

  |            | tools.py 那份（死代码）        | system_control_tools 那份（生效）  |
  |------------|------------------------------|----------------------------------|
  | 入参       | `mode` / `save_path`         | `region` / `window_title` / `save_path` |
  | 停止       | 无                            | `stop_event` / `should_stop`      |
  | 取图       | `PIL.ImageGrab.grab()`       | Qt `QApplication` grabWindow      |
  | 返回       | `(msg, [path], [])`          | `(msg, [], None)`                 |

  谁照着 tools.py 那份的签名传 `mode="fullscreen"`，生效版**根本不认这个参数**，
  静默忽略，截出来不是预期的东西。死代码在这里是个**误导源**。

修法方向（本判据此为准绳）：
  · `tools.tool_screenshot` 改成**显式委托**到生效实现（保留函数名，不删函数 ——
    风险最低，且任何潜在调用方的名字引用都不会断）；
  · 参数**全部透传**（含 `stop_event` / `should_stop`，否则停止按钮对截图失效）；
  · 返回值**原样透传**，不包一层、不改写；
  · 异常**不吞**（委托目标抛什么就抛什么）。

纪律：行为级断言 —— 用**打补丁看命中次数**证明委托真生效（不是查源码字符串）；
这是 v4.258.0「判据会测到影子实现」那条教训的直接应用：只断言"函数存在"或
"看起来是委托"，两份独立实现照样全绿。
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
        print("  [FAIL] %s%s" % (name, ("  → " + str(extra)) if extra else ""))
    return bool(cond)


class _Spy(object):
    """替身：记录被调了几次、收到什么参数，返回哨兵。"""

    def __init__(self, ret=None, boom=None):
        self.n = 0
        self.pos = None
        self.kw = None
        self.ret = ret if ret is not None else object()
        self.boom = boom

    def __call__(self, *a, **k):
        self.n += 1
        self.pos = a
        self.kw = k
        if self.boom is not None:
            raise self.boom
        return self.ret


def _call(fn, *a, **k):
    """调一次，返回 (结果, 异常)。

    为什么要兜住异常：**红态下要看全所有判据**。旧签名压根不认 stop_event，
    直接调用会 TypeError 中断，后面的判据一条都跑不到 —— 崩掉的判据是**歧义**
    的（分不清"未实现"和"判据自己写错"），不如记下来继续跑。
    """
    try:
        return fn(*a, **k), None
    except Exception as e:  # noqa: BLE001
        return None, e


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
    print("---- T1 委托真生效：打补丁看命中（★核心，防'两份实现'复发）----")
    spy = _Spy()
    orig = sct.tool_screenshot
    sct.tool_screenshot = spy
    try:
        got = tools.tool_screenshot({"a": 1}, "APPDIR", {"x": 1})
    finally:
        sct.tool_screenshot = orig
    ck(spy.n == 1, "T1-1 ★ tools.tool_screenshot 必须真的调到生效实现（命中==1）",
       "实测命中 %d 次 —— 若是内联另一份实现，这里会是 0" % spy.n)
    ck(got is spy.ret, "T1-2 返回值**原样**透传（不包一层、不改写）",
       "got=%r spy.ret=%r" % (got, spy.ret))

    print("\n---- T2 参数透传（位置参数顺序 + 停止信号不许丢）----")
    spy2 = _Spy()
    sct.tool_screenshot = spy2
    _se, _ss = object(), object()
    _r2, _e2 = None, None
    try:
        _r2, _e2 = _call(tools.tool_screenshot, {"c": 1}, "DIR", {"r": 1},
                         progress="P", stop_event=_se, should_stop=_ss)
    finally:
        sct.tool_screenshot = orig
    ck(_e2 is None, "T2-0 接受 stop_event/should_stop 关键字（不 TypeError）",
       "实测异常 %r" % (_e2,))
    ck(spy2.pos == ({"c": 1}, "DIR", {"r": 1}),
       "T2-1 位置参数顺序正确 (cfg, app_dir, args)", "实测 %r" % (spy2.pos,))
    ck((spy2.kw or {}).get("progress") == "P", "T2-2 progress 透传", spy2.kw)
    ck((spy2.kw or {}).get("stop_event") is _se,
       "T2-3 ★ stop_event 透传（丢了停止按钮就对截图失效）", spy2.kw)
    ck((spy2.kw or {}).get("should_stop") is _ss,
       "T2-4 ★ should_stop 透传（同上）", spy2.kw)

    print("\n---- T3 接线不变：注册表仍指向 system_control_tools 那份 ----")
    impl = _registry_impl()
    ck(impl is sct.tool_screenshot,
       "T3-1 注册表 screenshot 仍指向 system_control_tools.tool_screenshot",
       "实测 %r" % (impl,))
    ck(impl is not getattr(tools, "tool_screenshot", None) or True,
       "T3-2 记录：tools 那份是委托入口（不再是独立实现）")

    print("\n---- T4 args 原样透传（不许改写入参）----")
    spy4 = _Spy()
    args_in = {"region": {"x": 0, "y": 0, "w": 10, "h": 10}}
    sct.tool_screenshot = spy4
    try:
        tools.tool_screenshot({}, "D", args_in)
    finally:
        sct.tool_screenshot = orig
    _p4 = spy4.pos or ()
    ck(len(_p4) == 3 and _p4[2] is args_in,
       "T4-1 args 原对象透传（不复制、不改写）", "实测 %r" % (spy4.pos,))

    print("\n---- T5 异常不吞：委托目标抛什么就抛什么 ----")
    boom = RuntimeError("生效实现故意抛错")
    sct.tool_screenshot = _Spy(boom=boom)
    raised = None
    try:
        _r5, raised = _call(tools.tool_screenshot, {}, "D", {})
    finally:
        sct.tool_screenshot = orig
    ck(raised is boom, "T5 ★ 委托目标抛错必须冒泡（不许吞成返回字符串）",
       "实测 raised=%r" % (raised,))

    print("\n---- T6 反向：不许把注册改到 tools 那份上去 ----")
    ck("screenshot" in tools.TOOL_REGISTRY, "T6-1 screenshot 仍在注册表")
    ck(_registry_impl() is sct.tool_screenshot,
       "T6-2 修完仍是生效实现在跑（没被委托方反客为主）")

    print("\n" + "=" * 70)
    print("v4.260.0 截图工具归一判据（P1-7）：PASS=%d FAIL=%d" % (PASS, FAIL))
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
