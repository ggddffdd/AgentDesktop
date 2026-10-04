# -*- coding: utf-8 -*-
"""A 批（任务板 #3）判据：系统控制可中断 + 控件查找不再按类型兜底 + 声明/登记全等。

覆盖三件（对应盘点报告 G6 / G2 / G3）：
  A 组 G6 —— 工具声明表（TOOL_DEFS）与风险登记表（RISK_MAP）**全等**。
             原先没有任何判据守着这条：新增工具忘了登记 → classify() 落 EXTERNAL
             → 被外发白名单静默拦掉（v4.169.0 手工修过同一类 bug）。
  B 组 G2 —— system_control 14 个工具「入口即停」（原 0/14 可中断）。
             含**端到端透传**：走 tools.py 的 _w 包装器，而不只是直接调函数。
  C 组 G3 —— `_find_control` 第 4 级兜底**不再自动选中**任何控件。
             原实现 target 名字全不匹配时返回「第一个同类控件」，调用方照点并报成功
             = 静默误点。

无头可跑，且**扰动时也不会碰到真实鼠标/键盘/进程**：
  本套件在跑 B 组前会装上「保险桩」（_init_pyautogui / _run_on_gui_thread /
  subprocess / pyperclip / pygetwindow 全换成抛错桩）—— 于是即便「入口即停」被删掉，
  工具也只会返回"缺少依赖"，绝不会真的动系统。C 组全程使用 stub 窗口，无真实 GUI。

扰动用环境变量指向变异副本：SCT_PATH / SWC_PATH / RISK_PATH。
用法：python tests/test_system_control_a.py
"""
import importlib.util
import inspect
import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}  {detail}")


def _load_override(env_key, modname):
    """按环境变量把某模块换成变异副本（扰动用）；未设则什么都不做。"""
    p = os.environ.get(env_key)
    if not p:
        return
    spec = importlib.util.spec_from_file_location(modname, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod          # 关键：先占住 sys.modules，
    spec.loader.exec_module(mod)        # 之后 import tools 时拿到的就是变异副本


def _load_fresh(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load_override("SCT_PATH", "system_control_tools")
_load_override("SWC_PATH", "software_control_tools")

import system_control_tools as sct      # noqa: E402
import software_control_tools as swc   # noqa: E402
import tool_defs                        # noqa: E402
import tools                            # noqa: E402

SCT_SRC = open(os.path.join(ROOT, "system_control_tools.py"),
               encoding="utf-8").read()
SWC_SRC = open(os.path.join(ROOT, "software_control_tools.py"),
               encoding="utf-8").read()
if os.environ.get("SCT_PATH"):
    SCT_SRC = open(os.environ["SCT_PATH"], encoding="utf-8").read()
if os.environ.get("SWC_PATH"):
    SWC_SRC = open(os.environ["SWC_PATH"], encoding="utf-8").read()

RISK_SRC_PATH = os.environ.get("RISK_PATH") or os.path.join(ROOT, "risk.py")
risk_ut = _load_fresh(RISK_SRC_PATH, "risk_under_test")

APP_DIR = ROOT
STOP_MARK = "已停止"

SYS_TOOLS = (
    "screenshot", "mouse_move", "mouse_click", "mouse_scroll",
    "keyboard_type", "keyboard_press",
    "clipboard_read", "clipboard_write",
    "window_list", "window_focus", "window_get_info",
    "process_list", "process_kill", "process_start",
)


# ---------------------------------------------------------------------------
# A 组 G6：工具声明表 vs 风险登记表全等
# ---------------------------------------------------------------------------
def test_declaration_registration_parity():
    print("== A 组（G6）声明表 vs 风险登记表全等 ==")
    declared = {d["function"]["name"] for d in tool_defs.TOOL_DEFS
                if (d.get("function") or {}).get("name")}
    registered = set(risk_ut.RISK_MAP.keys())

    check("声明表非空", len(declared) > 0, f"declared={len(declared)}")
    missing = sorted(declared - registered)
    check("没有『声明了但未登记』的工具（未登记会被 fail-closed 拦掉）",
          not missing, f"漏登记={missing}")
    extra = sorted(registered - declared)
    check("没有『登记了但已无声明』的僵尸条目", not extra, f"僵尸={extra}")
    check("两表集合完全相等", declared == registered,
          f"declared={len(declared)} registered={len(registered)}")

    # 每条登记的声明都要能被 _policy 解析，且风险类合法
    bad = []
    for n in sorted(registered):
        r, _t, _h = risk_ut._policy(n)
        if r not in risk_ut._RISK_TO_TIER:
            bad.append(f"{n}:{r!r}")
    check("全部登记条目 _policy 可解析且风险类合法", not bad, f"bad={bad[:5]}")

    # fail-closed 语义仍在：未登记的名字必须落 EXTERNAL（= 必须白名单才放行）
    check("未登记工具仍是 fail-closed（EXTERNAL）",
          risk_ut.classify("__no_such_tool_xyz__") == risk_ut.RiskClass.EXTERNAL)


# ---------------------------------------------------------------------------
# B 组 G2：system_control 可中断
# ---------------------------------------------------------------------------
class _Raiser:
    """万能桩：任何属性访问都返回一个抛指定异常的函数。"""

    def __init__(self, exc):
        self._exc = exc

    def __getattr__(self, _name):
        def _boom(*_a, **_k):
            raise self._exc
        return _boom


_INSTALLED = []


def _install_safety_stubs():
    """把系统控制的所有副作用入口换成抛错桩。

    目的不是"模拟失败"，而是 **保险** ：B 组要证明"给了停止信号就停住"，
    万一（扰动后）工具真的往下跑了，这里必须保证它撞桩返回，而绝不去动
    真实鼠标/键盘/进程/剪贴板。这样扰动验证才敢反复跑。
    """
    if _INSTALLED:
        return

    def _no_pyautogui():
        raise ImportError("safety-stub")

    def _no_gui_thread(app, fn, timeout=15):
        return False, RuntimeError("safety-stub")

    def _stub_path(*_a, **_k):
        return os.path.join(ROOT, "output", "_stub_never_written.png")

    sct._init_pyautogui = _no_pyautogui
    sct._run_on_gui_thread = _no_gui_thread
    sct._resolve_save_path = _stub_path
    sct.subprocess = _Raiser(RuntimeError("safety-stub"))
    sys.modules["pyperclip"] = _Raiser(RuntimeError("safety-stub"))
    sys.modules["pygetwindow"] = _Raiser(RuntimeError("safety-stub"))
    _INSTALLED.append(True)


def _stopped(fn, args=None, **kw):
    """调用工具，判断是否「入口即停」。抛异常也算没停住（fail-closed 测试口径）。"""
    try:
        out = fn(APP_DIR, APP_DIR, args if args is not None else {}, **kw)
    except Exception as e:                                    # noqa: BLE001
        return False, f"抛出 {type(e).__name__}: {e}"
    first = out[0] if isinstance(out, (tuple, list)) and out else out
    return (STOP_MARK in str(first)), str(first)[:70]


def test_system_control_stop_signal():
    print("== B 组（G2）system_control 入口即停（14 个工具）==")
    _install_safety_stubs()

    # B1 should_stop 回调
    bad = []
    for n in SYS_TOOLS:
        fn = getattr(sct, "tool_" + n, None)
        if fn is None:
            bad.append(f"{n}:缺失")
            continue
        ok, det = _stopped(fn, should_stop=lambda: True)
        if not ok:
            bad.append(f"{n}:{det}")
    check("should_stop=True → 全部 14 个工具入口即停", not bad, f"没停住={bad}")

    # B2 stop_event
    ev = threading.Event()
    ev.set()
    bad2 = []
    for n in SYS_TOOLS:
        fn = getattr(sct, "tool_" + n, None)
        if fn is None:
            bad2.append(f"{n}:缺失")
            continue
        ok, det = _stopped(fn, stop_event=ev)
        if not ok:
            bad2.append(f"{n}:{det}")
    check("stop_event 已 set → 全部 14 个工具入口即停", not bad2, f"没停住={bad2}")

    # B3 签名契约（静态，防回退到 (cfg, app_dir, args)）
    lack = []
    for n in SYS_TOOLS:
        fn = getattr(sct, "tool_" + n, None)
        if fn is None:
            continue
        ps = set(inspect.signature(fn).parameters)
        if not {"progress", "stop_event", "should_stop"} <= ps:
            lack.append(n)
    check("全部 14 个工具签名都声明了三个扩展参数", not lack, f"缺={lack}")

    # B4 不误停：不传停止信号时不该停在入口（用只读且无副作用的 process_list）
    ok4, det4 = _stopped(sct.tool_process_list)
    check("不传停止信号时不会误停", not ok4, f"out={det4!r}")

    # B5 fail-closed：停止回调本身抛异常 → 按已停止处理
    def _boom():
        raise RuntimeError("停止回调自身炸了")

    ok5, det5 = _stopped(sct.tool_process_list, should_stop=_boom)
    check("停止回调抛异常 → fail-closed 按已停止", ok5, f"out={det5!r}")

    # B6 端到端：走 tools.py 的 _w 包装器（证明信号真被透传，不只是函数自己能接）
    bad6 = []
    for n in SYS_TOOLS:
        reg = tools.TOOL_REGISTRY.get(n)
        if not reg:
            bad6.append(f"{n}:未注册")
            continue
        ok, det = _stopped(reg["handler"], should_stop=lambda: True)
        if not ok:
            bad6.append(f"{n}:{det}")
    check("TOOL_REGISTRY 包装器把停止信号透传到底（14/14）", not bad6,
          f"没停住={bad6}")


# ---------------------------------------------------------------------------
# C 组 G3：_find_control 不再按类型兜底选中
# ---------------------------------------------------------------------------
class _StubCtrl:
    def __init__(self, text, log):
        self._t = text
        self._log = log

    def wait(self, *_a, **_k):
        return True

    def window_text(self):
        return self._t

    def click(self, **_k):
        self._log.append(("click", self._t))

    def click_input(self, **_k):
        self._log.append(("click_input", self._t))

    def set_focus(self, **_k):
        self._log.append(("set_focus", self._t))

    def type_keys(self, *_a, **_k):
        self._log.append(("type_keys", self._t))


class _StubWindow:
    """模拟 pywinauto 的 WindowSpecification，只实现 _find_control / app_* 会走到的入口。

    分工刻意如此：
      · 带 auto_id / title / title_re 的查找 → **一律找不到**（前 3 级全落空）；
      · **只给 control_type** 的查找 → **返回第一个同类控件**。
        这正是本次被修掉的那条兜底路径。留着它，是为了让「悄悄选了一个」
        这个 bug 在判据里能被真实复现出来（否则判据只会看到"抛错"，
        抓不住误点）。
    """

    def __init__(self, texts, log):
        self._log = log
        self._ctrls = [_StubCtrl(t, log) for t in texts]

    def child_window(self, **kw):
        self._log.append(("child_window", tuple(sorted(kw))))
        if any(k in kw for k in ("auto_id", "title", "title_re")):
            raise RuntimeError("ElementNotFound（stub）")
        if kw.get("control_type") and self._ctrls:
            return self._ctrls[0]
        raise RuntimeError("ElementNotFound（stub）")

    def descendants(self, control_type=None, **kw):
        self._log.append(("descendants", control_type))
        return list(self._ctrls)

    def window_text(self):
        return "StubWindow"


def test_find_control_no_type_fallback():
    print("== C 组（G3）第 4 级兜底不再自动选中 ==")

    # C1：两个同类候选，无一匹配 target → 必须抛错，且一个都没被点
    log1 = []
    w1 = _StubWindow(["删除", "取消"], log1)
    err1 = ""
    try:
        swc._find_control(w1, "保存", "Button")
        err1 = ""
    except RuntimeError as e:
        err1 = str(e)
    check("同类多候选不匹配 → 抛 RuntimeError", bool(err1), f"err={err1!r}")
    check("异常里列出候选清单", ("删除" in err1 and "取消" in err1), f"err={err1!r}")
    check("异常说明『无一匹配』", "无一匹配" in err1, f"err={err1!r}")
    clicked = [x for x in log1 if x[0] in ("click", "click_input")]
    check("★ 一个控件都没被碰过（没有静默选中）", not clicked, f"log={log1}")

    # C2：**只有 1 个同类控件也仍然不许自动选中**（这条钉死"不再兜底"）
    log2 = []
    w2 = _StubWindow(["唯一按钮"], log2)
    err2 = ""
    try:
        swc._find_control(w2, "保存", "Button")
    except RuntimeError as e:
        err2 = str(e)
    check("★ 同类仅 1 个也不自动选中（仍抛错）", bool(err2), f"err={err2!r}")
    check("异常给出可操作提示（改成该名字重试）",
          "仅 1 个" in err2 and "target" in err2, f"err={err2!r}")
    clicked2 = [x for x in log2 if x[0] in ("click", "click_input")]
    check("★ 该控件同样没被碰过", not clicked2, f"log={log2}")

    # C3：窗口里没有该类控件 → 抛错并提示可用 app_list_controls 自查
    log3 = []
    w3 = _StubWindow([], log3)
    err3 = ""
    try:
        swc._find_control(w3, "保存", "Button")
    except RuntimeError as e:
        err3 = str(e)
    check("无同类控件 → 抛错", bool(err3), f"err={err3!r}")
    check("提示用 app_list_controls 自查", "app_list_controls" in err3, f"err={err3!r}")

    # C4：确能找到时照旧返回（别把正常路径一起改死）
    class _HitWindow(_StubWindow):
        def child_window(self, **kw):
            self._log.append(("child_window", tuple(sorted(kw))))
            if kw.get("auto_id") == "ok_btn":
                return _StubCtrl("确定", self._log)
            raise RuntimeError("ElementNotFound（stub）")

    hit = swc._find_control(_HitWindow([], []), "ok_btn", "Button")
    check("精确 auto_id 命中时正常返回", hit.window_text() == "确定")

    # C5：端到端 app_click —— 必须报「点击失败」，绝不能报「已点击」
    log5 = []
    _orig_connect = swc._connect_window
    swc._connect_window = lambda title, timeout=5: _StubWindow(["删除", "取消"], log5)
    try:
        out5, _, _ = swc.tool_app_click(
            APP_DIR, APP_DIR, {"title": "T", "target": "保存", "control_type": "Button"})
    finally:
        swc._connect_window = _orig_connect
    check("★ app_click 报「点击失败」而不是「已点击」",
          out5.startswith("点击失败") and "已点击" not in out5, f"out={out5!r}")
    check("★ app_click 全程零点击", not [x for x in log5 if x[0] in ("click", "click_input")],
          f"log={log5}")

    # C6：端到端 app_type —— 同样必须失败，不能往错控件里打字
    log6 = []
    _orig_connect2 = swc._connect_window
    swc._connect_window = lambda title, timeout=5: _StubWindow(["下拉框"], log6)
    try:
        out6, _, _ = swc.tool_app_type(
            APP_DIR, APP_DIR, {"title": "T", "text": "hello", "target": "用户名"})
    finally:
        swc._connect_window = _orig_connect2
    check("★ app_type 报「输入失败」", out6.startswith("输入失败"), f"out={out6!r}")
    check("★ app_type 全程零输入", not [x for x in log6 if x[0] in ("type_keys",
                                                                "set_focus")],
          f"log={log6}")


# ---------------------------------------------------------------------------
# D 组：源码级防回退（钉住契约写法本身）
# ---------------------------------------------------------------------------
def test_source_contracts():
    print("== D 组 源码契约防回退 ==")
    n_sig = len(re.findall(r"^def tool_\w+\(", SCT_SRC, re.M))
    check("system_control 有 14 个 def tool_*", n_sig == 14, f"n={n_sig}")
    check("system_control 定义了 _aborted helper", "def _aborted(" in SCT_SRC)
    n_guard = SCT_SRC.count("    if _aborted(should_stop, stop_event):")
    check("★ 14 处入口检查一处不少", n_guard == 14, f"n={n_guard}")

    # 第 4 级兜底：不许再出现「裸 child_window(control_type=...) 取第一个」
    check("software_control 定义了 _list_candidates", "def _list_candidates(" in SWC_SRC)
    check("★ 源码里不再有按类型裸取一个的回落",
          "仅按 control_type 找第一个" not in SWC_SRC,
          "残留了旧注释/旧实现")
    check("★ 不再无条件用 child_window 兜底选中",
          "    if control_type:\n        cands = _list_candidates(" in SWC_SRC,
          "第 4 级不是枚举候选写法")


def main():
    test_declaration_registration_parity()
    test_system_control_stop_signal()
    test_find_control_no_type_fallback()
    test_source_contracts()
    print(f"\n==== A 批结果：PASS={_p}  FAIL={_f} ====")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
