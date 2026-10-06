# -*- coding: utf-8 -*-
"""B 批（任务板 #3 / 盘点 G5）判据：`system_control_tools` 功能契约。

盘点现状（G5）：该模块 741 行 / 14 个工具，**零判据**——授权闸门（risk/permissions）
有 7~9 个套件守着，可闸门后面那只手（键鼠/窗口/进程）一个都没有。于是 G2/G3/G4
那种"改坏了没人知道"的改动进来，不会有任何套件翻红。

本套件把它变成"改坏了必然翻红"，六组：

  A 组 声明 ↔ 实现 ↔ 分发 三向一致（schema required 逐个键对照实现的缺参校验）
  B 组 通用契约：14 个工具恒返回三元组；**永不抛异常**（含畸形/缺参入参）
  C 组 依赖缺失降级三条线（pyautogui / pyperclip / pygetwindow）
  D 组 行为契约（记录型假模块全链路，验参数真的传对了）
  E 组 内部 helper（_aborted / _split_win_args / _resolve_save_path /
        _screen_at_local / _run_on_gui_thread）
  F 组 源码契约防回退

**零副作用保证（本套件的第一原则）**：模块导入后立刻把 pyautogui / pyperclip /
pygetwindow / subprocess 换成假实现，QtWidgets 只在需要时临时替换。任何一条判据
都不会真的移动鼠标、敲键盘、读写剪贴板、杀进程、截图。
（A 批之前的一次探针正是漏了这一步，真的点了一次鼠标 —— 见 LEARNINGS L247。）

扰动用环境变量指向变异副本：SCT_PATH。
用法：python tests/test_system_control_b.py
"""
import importlib.util
import inspect
import io
import os
import re
import sys
import types

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
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)


_load_override("SCT_PATH", "system_control_tools")

import system_control_tools as sct      # noqa: E402

SCT_PATH = os.environ.get("SCT_PATH") or os.path.join(ROOT, "system_control_tools.py")
SCT_SRC = open(SCT_PATH, encoding="utf-8").read()

APP_DIR = ROOT


# ===========================================================================
# 隔离层：把所有副作用入口换成假实现（本套件第一原则）
# ===========================================================================
class FakePag:
    """记录型假 pyautogui：只记账，绝不真动键鼠。"""

    FAILSAFE = False

    def __init__(self):
        self.calls = []

    def _rec(self, name, *a, **k):
        self.calls.append((name, a, k))

    def moveTo(self, *a, **k):
        self._rec("moveTo", *a, **k)

    def click(self, *a, **k):
        self._rec("click", *a, **k)

    def scroll(self, *a, **k):
        self._rec("scroll", *a, **k)

    def typewrite(self, *a, **k):
        self._rec("typewrite", *a, **k)

    def hotkey(self, *a, **k):
        self._rec("hotkey", *a, **k)


class FakeClip:
    def __init__(self):
        self.text = "clip-初始"
        self.copied = []

    def paste(self):
        return self.text

    def copy(self, t):
        self.copied.append(t)
        self.text = t


class FakeWin:
    def __init__(self, title="T", left=10, top=20, width=300, height=200,
                 visible=True, minimized=False, maximized=False):
        self.title = title
        self.left, self.top, self.width, self.height = left, top, width, height
        self.visible = visible
        self.isMinimized = minimized
        self.isMaximized = maximized
        self.restored = 0
        self.activated = 0

    def restore(self):
        self.restored += 1

    def activate(self):
        self.activated += 1


class FakeGW:
    def __init__(self, wins=None):
        self.wins = list(wins or [])
        self.lookup = {}

    def getAllWindows(self):
        return list(self.wins)

    def getWindowsWithTitle(self, t):
        return list(self.lookup.get(t, []))


class _Completed:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class FakeProc:
    def __init__(self, pid=4321):
        self.pid = pid


class FakeSubprocess:
    """记录型假 subprocess：run/Popen 都不真起进程。"""

    def __init__(self):
        self.runs = []
        self.popens = []
        self.run_result = _Completed()
        self.popen_exc = None

    def run(self, cmd, **kw):
        self.runs.append((list(cmd), kw))
        return self.run_result

    def Popen(self, argv, **kw):
        self.popens.append((list(argv), kw))
        if self.popen_exc:
            raise self.popen_exc
        return FakeProc()


PAG = FakePag()
CLIP = FakeClip()
GW = FakeGW()
SP = FakeSubprocess()

sys.modules["pyautogui"] = PAG
sys.modules["pyperclip"] = CLIP
sys.modules["pygetwindow"] = GW
sct.subprocess = SP


def _last_kill(sp):
    """最近一次 taskkill 调用的 argv（筛掉 tasklist 记录）。

    G4 起 `process_kill` 的 /IM 路径会**先**调一次 `tasklist` 统计同名进程数
    （`_count_processes`），于是 `sp.runs[-1]` 不再是 taskkill —— 判 /IM、/F
    必须筛出 taskkill 记录，否则新判据会把 tasklist 当成终止命令。
    """
    for c, _kw in reversed(sp.runs):
        if c and c[0] == "taskkill":
            return c
    return None


def _fake_qt_app():
    """假 QApplication：thread() 报"当前就是 GUI 线程"→ 让 _run_on_gui_thread 就地跑 _do。

    注意 _run_on_gui_thread 里 `from PySide6.QtCore import QObject, Signal` 在函数第二行，
    用真 PySide6 也没关系 —— 只要 app.thread().isCurrentThread() 为真，就完全不走
    信号投递那条路，不碰事件循环。
    """

    class _Thr:
        def isCurrentThread(self):
            return True

    class _App:
        def thread(self):
            return _Thr()

        def primaryScreen(self):
            return None

    return _App()


class _QT_CTX:
    """临时把 PySide6.QtWidgets 换成只提供 QApplication.instance() 的假模块，用完还原。"""

    def __init__(self, app):
        self.app = app

    def __enter__(self):
        self.old = sys.modules.get("PySide6.QtWidgets")
        m = types.ModuleType("PySide6.QtWidgets")
        m.QApplication = types.SimpleNamespace(
            instance=staticmethod(lambda: self.app))
        sys.modules["PySide6.QtWidgets"] = m
        return self

    def __exit__(self, *_a):
        if self.old is not None:
            sys.modules["PySide6.QtWidgets"] = self.old
        else:
            sys.modules.pop("PySide6.QtWidgets", None)
        return False


def call(name, args, **kw):
    """调工具，返回 (out, exc)。exc 不为 None 即"抛异常了"。"""
    fn = getattr(sct, "tool_" + name)
    try:
        return fn(APP_DIR, APP_DIR, args, **kw), None
    except Exception as e:                                     # noqa: BLE001
        return None, e


def first(out):
    if isinstance(out, (tuple, list)) and out:
        return out[0]
    return out


def txt(name, args, **kw):
    out, exc = call(name, args, **kw)
    return None if exc else str(first(out))


SYS_TOOLS = (
    "screenshot", "mouse_move", "mouse_click", "mouse_scroll",
    "keyboard_type", "keyboard_press",
    "clipboard_read", "clipboard_write",
    "window_list", "window_focus", "window_get_info",
    "process_list", "process_kill", "process_start",
)

# 每个工具的必填参数（从 schema 现取，不硬编码）
SCHEMA_REQUIRED = {
    d["function"]["name"]: set(((d["function"].get("parameters") or {})
                                .get("required")) or [])
    for d in sct.SYSTEM_CONTROL_TOOL_DEFS
}

# 填参用的样本值（够"合法但找不到"，避免真产生副作用）
SAMPLE = {
    "x": 10, "y": 20, "text": "hi", "keys": "ctrl+c",
    "title": "NoSuchWindow_ZZZ", "name": "nosuchproc_zzz.exe",
    "target": "nosuchprog_zzz.exe",
}


# ===========================================================================
# A 组：声明 ↔ 实现 ↔ 分发 三向一致
# ===========================================================================
def test_triple_consistency():
    print("== A 组 声明/实现/分发三向一致 ==")
    names = [d["function"]["name"] for d in sct.SYSTEM_CONTROL_TOOL_DEFS]
    check("schema 声明 15 个工具", len(names) == 15, f"n={len(names)}")
    check("schema 无重名", len(set(names)) == len(names), f"names={names}")

    table = set(sct.SYSTEM_CONTROL_TOOL_TABLE)
    check("路由表键集合 == schema 名集合", set(names) == table,
          f"only_schema={sorted(set(names) - table)} only_table={sorted(table - set(names))}")
    check("路由表 15 个目标都是可调用", all(callable(v) for v in sct.SYSTEM_CONTROL_TOOL_TABLE.values()))

    import tools
    miss = [n for n in table if n not in tools.TOOL_REGISTRY]
    check("15 个工具都进了 tools.TOOL_REGISTRY（否则分发层查不到）", not miss, f"缺={miss}")

    # 分发层必须能透传三个停止参数（G2 的另一半：注册侧）
    bad = []
    for n in sorted(table):
        h = tools.TOOL_REGISTRY.get(n, {}).get("handler")
        if h is None:
            continue
        ps = inspect.signature(h).parameters
        for w in ("progress", "stop_event", "should_stop"):
            if w not in ps:
                bad.append(f"{n}:缺{w}")
    check("★ 分发层 15 个 handler 都声明了三停止参数", not bad, f"bad={bad}")


def test_required_parity():
    print("== A 组 schema.required ↔ 实现缺参校验 ==")
    bad_missing_report = []
    for n in SYS_TOOLS:
        req = SCHEMA_REQUIRED.get(n) or set()
        for k in sorted(req):
            args = {kk: SAMPLE[kk] for kk in req if kk != k}
            out, exc = call(n, args)
            if exc is not None:
                bad_missing_report.append(f"{n}:缺{k} 抛出 {type(exc).__name__}")
                continue
            s = str(first(out))
            if "缺少必填参数" not in s or k not in s:
                bad_missing_report.append(f"{n}:缺{k} 未报缺参 -> {s[:40]}")
    check("★ 每个必填键缺失时都返回『缺少必填参数：<键>』（不是 KeyError）",
          not bad_missing_report, f"bad={bad_missing_report}")

    # 反向：给全参数时不该误报缺参
    false_pos = []
    for n in SYS_TOOLS:
        req = SCHEMA_REQUIRED.get(n) or set()
        args = {kk: SAMPLE[kk] for kk in req}
        out, exc = call(n, args)
        s = "" if exc else str(first(out))
        if "缺少必填参数" in s:
            false_pos.append(f"{n}:{s[:40]}")
    check("★ 参数齐全时不误报缺参（防过度校验）", not false_pos, f"false_pos={false_pos}")


# ===========================================================================
# B 组：通用契约 —— 恒返回三元组、永不抛异常
# ===========================================================================
def test_universal_contract():
    print("== B 组 通用契约 ==")
    bad_shape = []
    for n in SYS_TOOLS:
        for label, args in (("空", {}),
                            ("None值", {k: None for k in (SCHEMA_REQUIRED.get(n) or set())}),
                            ("错类型", {k: 12345 for k in (SCHEMA_REQUIRED.get(n) or set())})):
            out, exc = call(n, args)
            if exc is not None:
                bad_shape.append(f"{n}/{label}:抛 {type(exc).__name__}: {exc}")
                continue
            if not (isinstance(out, tuple) and len(out) == 3):
                bad_shape.append(f"{n}/{label}:形状={type(out).__name__}")
                continue
            if not isinstance(out[0], str) or not isinstance(out[1], list):
                bad_shape.append(f"{n}/{label}:类型={type(out[0]).__name__}/{type(out[1]).__name__}")
                continue
            if out[2] is not None:
                bad_shape.append(f"{n}/{label}:schedule={out[2]!r}")
    check("★ 15 个工具 × 3 种畸形入参：恒返回 (str, list, None)，一处不抛",
          not bad_shape, f"bad={bad_shape[:6]}")

    # 空入参单独再看一次「不抛」（缺参是最常见的模型失误）
    raised = []
    for n in SYS_TOOLS:
        _out, exc = call(n, {})
        if exc is not None:
            raised.append(f"{n}:{type(exc).__name__}:{exc}")
    check("★ 空入参 15 个工具全部不抛异常（文件头承诺『永不抛异常』）",
          not raised, f"raised={raised}")


# ===========================================================================
# C 组：依赖缺失降级三条线
# ===========================================================================
def _without(modname, fn):
    """临时让 `import modname` 抛 ImportError（sys.modules 置 None 是标准技巧）。"""
    old = sys.modules.get(modname, "")
    sys.modules[modname] = None
    try:
        return fn()
    finally:
        if old == "":
            sys.modules.pop(modname, None)
        else:
            sys.modules[modname] = old


def test_dependency_degrade():
    print("== C 组 依赖缺失降级 ==")

    cases = (
        ("pyautogui", ["mouse_move", "mouse_click", "mouse_scroll",
                       "keyboard_type", "keyboard_press"]),
        ("pyperclip", ["clipboard_read", "clipboard_write"]),
        ("pygetwindow", ["window_list", "window_focus", "window_get_info"]),
    )
    for modname, tools_list in cases:
        bad = []

        def _probe(_list=tools_list, _mod=modname, _bad=bad):
            for n in _list:
                req = SCHEMA_REQUIRED.get(n) or set()
                args = {kk: SAMPLE[kk] for kk in req}
                s = txt(n, args)
                if s is None:
                    _bad.append(f"{n}:抛异常")
                elif _mod not in s or "缺少依赖包" not in s:
                    _bad.append(f"{n}:{s[:38]}")
        _without(modname, _probe)
        check(f"缺 {modname} → {len(tools_list)} 个工具报『缺少依赖包：{modname}』",
              not bad, f"bad={bad}")

    # screenshot 走 window_title 分支时同样依赖 pygetwindow。
    # 注意这里的 import 在 _do 闭包内，异常先被 _run_on_gui_thread 收下再转成
    # 「截图失败：…」，所以不会命中外层那句「缺少依赖包」——判据只要求
    # 「点名 pygetwindow 且明确失败」，不锁死具体措辞（另见 LEARNINGS：别赌文案）。
    def _shot():
        with _QT_CTX(_fake_qt_app()):
            return txt("screenshot", {"window_title": "NoSuchWindow_ZZZ"})

    s = _without("pygetwindow", _shot)
    check("★ screenshot(window_title) 缺 pygetwindow → 点名依赖且明确失败",
          s is not None and "pygetwindow" in s and ("失败" in s or "缺少依赖" in s),
          f"s={s!r}")


# ===========================================================================
# D 组：行为契约（参数真的传对了吗）
# ===========================================================================
def test_mouse_contract():
    print("== D 组 鼠标契约 ==")
    PAG.calls.clear()
    s = txt("mouse_move", {"x": 10, "y": 20, "duration": 0.5})
    check("mouse_move 文案含坐标", s is not None and "(10, 20)" in s, f"s={s!r}")
    check("★ mouse_move 真的把 x/y/duration 传给了 moveTo",
          PAG.calls == [("moveTo", (10, 20), {"duration": 0.5})], f"calls={PAG.calls}")

    PAG.calls.clear()
    s = txt("mouse_click", {"x": 5, "y": 6, "clicks": 2, "button": "right"})
    check("mouse_click 有坐标 → 双击文案",
          s is not None and "双击" in s and "right" in s and "(5, 6)" in s, f"s={s!r}")
    check("★ mouse_click 传位置 + clicks + button",
          PAG.calls == [("click", (5, 6), {"clicks": 2, "button": "right"})], f"calls={PAG.calls}")

    PAG.calls.clear()
    s = txt("mouse_click", {})
    check("mouse_click 无坐标 → 当前位置单击",
          s is not None and "单击" in s and "当前位置" in s, f"s={s!r}")
    check("★ 无坐标时不传位置（点当前位置）",
          PAG.calls == [("click", (), {"clicks": 1, "button": "left"})], f"calls={PAG.calls}")

    PAG.calls.clear()
    s = txt("mouse_scroll", {"clicks": 3})
    check("mouse_scroll 正数 → 向上",
          s is not None and "向上" in s and "3" in s, f"s={s!r}")
    check("★ 正数 scroll 传正数", PAG.calls == [("scroll", (3,), {})], f"calls={PAG.calls}")

    PAG.calls.clear()
    s = txt("mouse_scroll", {"clicks": -2, "x": 7, "y": 8})
    check("mouse_scroll 负数 → 向下且取绝对值显示",
          s is not None and "向下" in s and "2" in s, f"s={s!r}")
    check("★ 有坐标时先 moveTo 再 scroll",
          PAG.calls == [("moveTo", (7, 8), {}), ("scroll", (-2,), {})], f"calls={PAG.calls}")


def test_keyboard_clipboard_contract():
    print("== D 组 键盘/剪贴板契约 ==")
    PAG.calls.clear()
    s = txt("keyboard_type", {"text": "hello", "interval": 0})
    check("keyboard_type 文案含字数", s is not None and "5" in s, f"s={s!r}")
    check("★ typewrite 收到原文与 interval",
          PAG.calls == [("typewrite", ("hello",), {"interval": 0})], f"calls={PAG.calls}")

    PAG.calls.clear()
    s = txt("keyboard_press", {"keys": "ctrl+c"})
    check("keyboard_press 文案回显组合键", s is not None and "ctrl+c" in s, f"s={s!r}")
    check("★ 'ctrl+c' 拆成两个键交给 hotkey",
          PAG.calls == [("hotkey", ("ctrl", "c"), {})], f"calls={PAG.calls}")

    CLIP.text = ""
    check("clipboard_read 空 → 『剪贴板为空』", txt("clipboard_read", {}) == "剪贴板为空")
    CLIP.text = "内容ABC"
    check("★ clipboard_read 非空 → 原样返回内容（不加前缀）",
          txt("clipboard_read", {}) == "内容ABC")

    CLIP.copied.clear()
    s = txt("clipboard_write", {"text": "写进去"})
    check("clipboard_write 文案", s is not None and "剪贴板" in s, f"s={s!r}")
    check("★ copy 收到原文", CLIP.copied == ["写进去"], f"copied={CLIP.copied}")


def test_window_contract():
    print("== D 组 窗口契约 ==")
    GW.wins = [FakeWin("甲窗口"), FakeWin(""), FakeWin("乙窗口"), FakeWin("   ")]
    s = txt("window_list", {})
    check("window_list 无 filter → 过滤掉空标题", s is not None and "2 个窗口" in s, f"s={s!r}")
    check("★ 空标题窗口不出现在列表里", "甲窗口" in (s or "") and "乙窗口" in (s or ""))

    s = txt("window_list", {"filter": "甲"})
    check("window_list filter 模糊匹配", s is not None and "1 个窗口" in s and "甲窗口" in s, f"s={s!r}")

    GW.wins = [FakeWin(f"W{i:03d}") for i in range(60)]
    s = txt("window_list", {})
    check("window_list 无 filter 时上限 50", s is not None and "50 个窗口" in s, f"s={s!r}")
    GW.wins = []

    GW.lookup = {}
    s = txt("window_focus", {"title": "NoSuchWindow_ZZZ"})
    check("window_focus 未找到 → 明确文案",
          s is not None and "未找到" in s and "NoSuchWindow_ZZZ" in s, f"s={s!r}")

    w = FakeWin("目标任务", minimized=True)
    GW.lookup = {"目标任务": [w]}
    s = txt("window_focus", {"title": "目标任务"})
    check("window_focus 找到 → 切前台文案", s is not None and "前台" in s, f"s={s!r}")
    check("★ 最小化的窗口先 restore 再 activate",
          w.restored == 1 and w.activated == 1, f"restored={w.restored} activated={w.activated}")

    GW.lookup = {}
    s = txt("window_get_info", {"title": "NoSuchWindow_ZZZ"})
    check("window_get_info 未找到 → 明确文案", s is not None and "未找到" in s, f"s={s!r}")

    for label, w, want in (("正常", FakeWin("A"), "正常"),
                           ("最小化", FakeWin("B", minimized=True), "最小化"),
                           ("最大化", FakeWin("C", maximized=True), "最大化")):
        GW.lookup = {"t": [w]}
        s = txt("window_get_info", {"title": "t"})
        check(f"window_get_info 状态：{label}", s is not None and f"状态: {want}" in s, f"s={s!r}")


def test_process_contract():
    print("== D 组 进程契约 ==")
    FAKE = ('"System Idle Process","0","Services","0","8 K"\n'
            '"notepad.exe","1234","Console","1","12,345 K"\n'
            '"chrome.exe","5678","Console","1","1,234,567 K"\n'
            '"tiny.exe","99","Console","1","640 K"\n')
    SP.run_result = _Completed(stdout=FAKE)
    s = txt("process_list", {}) or ""

    check("★ 千分位内存不被逗号切碎：12,345 K → 12 MB",
          "12 MB" in s, f"s={s!r}")
    check("★ 千分位内存不被逗号切碎：1,234,567 K → 1205 MB",
          "1205 MB" in s, f"s={s!r}")
    check("★ 排序按真实内存降序（chrome > notepad > tiny）",
          s.find("chrome.exe") < s.find("notepad.exe") < s.find("tiny.exe")
          if all(k in s for k in ("chrome.exe", "notepad.exe", "tiny.exe")) else False,
          f"s={s!r}")
    check("进程名与 PID 正确解析",
          "notepad.exe" in s and "1234" in s, f"s={s!r}")

    s = txt("process_list", {"filter": "note"}) or ""
    check("process_list filter 只留匹配项",
          "notepad.exe" in s and "chrome.exe" not in s, f"s={s!r}")

    SP.run_result = _Completed(stdout="\n".join(
        f'"p{i}.exe","{i}","Console","1","{i + 1} K"' for i in range(60)))
    s = txt("process_list", {}) or ""
    check("process_list 无 filter 时上限 50", "50 个进程" in s, f"s={s!r}")
    SP.run_result = _Completed()

    SP.runs.clear()
    SP.run_result = _Completed(returncode=0)
    s = txt("process_kill", {"name": "1234"})
    check("process_kill PID → 用 /PID", SP.runs and "/PID" in SP.runs[-1][0], f"cmd={SP.runs[-1][0] if SP.runs else None}")
    check("process_kill 成功文案", s is not None and "已终止" in s and "1234" in s, f"s={s!r}")

    SP.runs.clear()
    s = txt("process_kill", {"name": "notepad.exe"})
    _k = _last_kill(SP)
    check("★ 进程名 → 用 /IM", _k and "/IM" in _k and "/PID" not in _k,
          f"cmd={_k}")

    SP.runs.clear()
    txt("process_kill", {"name": "notepad.exe", "force": True})
    _k = _last_kill(SP)
    check("★ force=True → 追加 /F", _k and "/F" in _k, f"cmd={_k}")

    # ---- G4（D 批）：/IM 影响面计数 ----
    # `taskkill /IM <name>` 会杀掉**所有**同名进程；此前返回值只写 "已终止进程: <name>"，
    # 用户看不到影响了几个。G4 在杀之前先只读统计（_count_processes），返回值回报影响面。
    SP.runs.clear()
    SP.run_result = _Completed(stdout=('"notepad.exe","1","Console","1","1 K"\n'
                                       '"notepad.exe","2","Console","1","1 K"\n'),
                               returncode=0)
    s = txt("process_kill", {"name": "notepad.exe"})
    check("★ G4 /IM 杀之前先统计同名进程数",
          any(c[0] == "tasklist" and any("IMAGENAME eq notepad.exe" in x for x in c)
              for c, _ in SP.runs),
          f"runs={[c for c, _ in SP.runs]}")
    check("★ G4 /IM 返回值回报「同名进程共 2 个」",
          s is not None and "同名进程共 2 个" in s, f"s={s!r}")

    SP.runs.clear()
    SP.run_result = _Completed(stdout='"notepad.exe","5","Console","1","1 K"', returncode=0)
    s = txt("process_kill", {"name": "notepad"})
    check("★ G4 名字不带 .exe → 回退查 <name>.exe 一次",
          s is not None and "同名进程共 1 个" in s, f"s={s!r}")

    SP.runs.clear()
    SP.run_result = _Completed(stdout="", returncode=0)
    s = txt("process_kill", {"name": "notepad.exe"})
    check("★ G4 计数为 0 时不加影响面后缀（不谎报）",
          s is not None and "同名" not in s and "已终止" in s, f"s={s!r}")

    SP.runs.clear()
    SP.run_result = _Completed(returncode=0)
    s = txt("process_kill", {"name": "1234"})
    # G4 第二步（v4.211.7）起，PID 路径**会**调一次 tasklist —— 但那是反查镜像名
    # （过关键进程黑名单，见 tests/test_critical_proc_deny.py），不是查影响面计数。
    # 所以这里判的是「没有 IMAGENAME 式的计数查询」，而不是「一次 tasklist 都没调」。
    check("★ G4 PID 路径不做影响面计数（精确 1 个，不必查 IMAGENAME）",
          not any(c[0] == "tasklist" and any("IMAGENAME" in x for x in c)
                  for c, _ in SP.runs),
          f"runs={[c for c, _ in SP.runs]}")

    SP.runs.clear()
    SP.run_result = _Completed(stdout="", stderr="ERROR: 没有找到进程", returncode=128)
    s = txt("process_kill", {"name": "nope.exe"})
    check("process_kill 失败 → 回传 stderr 且不谎报成功",
          s is not None and "失败" in s and "没有找到进程" in s, f"s={s!r}")

    SP.popens.clear()
    SP.popen_exc = None
    s = txt("process_start", {"target": "notepad.exe", "args": '-a -b "C:\\a b\\c.txt"'})
    check("process_start 成功文案含 PID", s is not None and "4321" in s, f"s={s!r}")
    check("★ shell=False + 参数列表：'C:\\a b\\c.txt' 作为一个 argv 元素",
          SP.popens and SP.popens[-1][0] == ["notepad.exe", "-a", "-b", "C:\\a b\\c.txt"],
          f"argv={SP.popens[-1][0] if SP.popens else None}")
    check("★ process_start 不传 shell=True",
          SP.popens and not SP.popens[-1][1].get("shell"), f"kw={SP.popens[-1][1] if SP.popens else None}")

    SP.popens.clear()
    s = txt("process_start", {"target": "x.exe", "working_dir": "D:\\wd"})
    check("★ working_dir 透传给 Popen.cwd",
          SP.popens and SP.popens[-1][1].get("cwd") == "D:\\wd",
          f"kw={SP.popens[-1][1] if SP.popens else None}")

    SP.popens.clear()
    SP.popen_exc = FileNotFoundError("nope")
    s = txt("process_start", {"target": "nosuchprog_zzz.exe"})
    check("process_start 找不到可执行文件 → 明确文案",
          s is not None and "找不到" in s, f"s={s!r}")
    SP.popen_exc = None


def test_screenshot_contract():
    print("== D 组 截图契约 ==")
    # 本进程没有 QApplication（offscreen）→ 命中"未初始化"分支
    s = txt("screenshot", {})
    check("screenshot 无 Qt Application → 明确文案",
          s is not None and "Qt" in s, f"s={s!r}")

    # 注入假 QtWidgets（假 app 的 thread() 报"已在 GUI 线程"→ 就地执行 _do），
    # 此时 pygetwindow 是假模块、找不到该标题 → 命中"未找到窗口"分支
    with _QT_CTX(_fake_qt_app()):
        s = txt("screenshot", {"window_title": "NoSuchWindow_ZZZ"})
    check("★ screenshot(window_title) 找不到窗口 → 明确文案（不是崩）",
          s is not None and "未找到" in s, f"s={s!r}")


# ===========================================================================
# E 组：内部 helper 契约
# ===========================================================================
def test_helpers():
    print("== E 组 内部 helper ==")

    def _boom():
        raise RuntimeError("bad-callback")

    class _Ev:
        def is_set(self):
            raise RuntimeError("bad-event")

    check("_aborted 无信号 → False", sct._aborted() is False)
    check("_aborted should_stop=True → True", sct._aborted(lambda: True, None) is True)
    check("★ _aborted 回调抛异常 → True（fail-closed：绝不误当成继续干）",
          sct._aborted(_boom, None) is True)
    check("★ _aborted stop_event 抛异常 → True（fail-closed）",
          sct._aborted(None, _Ev()) is True)
    check("_aborted 非 callable 的 should_stop → False（不误停）",
          sct._aborted("yes", None) is False)

    sp = sct._split_win_args
    check("_split_win_args 空串 → []", sp("") == [])
    check("_split_win_args 纯空白 → []", sp("   ") == [])
    check("_split_win_args 普通切分", sp("-a -b") == ["-a", "-b"])
    check("★ _split_win_args 剥外层引号但保留空格",
          sp('"C:\\Program Files\\x.exe" -v') == ["C:\\Program Files\\x.exe", "-v"])
    # v4.211.4：原写法 f"got={sp('-a\\\\b')}" 的表达式部分含反斜杠，
    # 3.10/3.11 下是 SyntaxError（python 3.12 的 PEP 701 才放开）。
    # 先算出来再格式化，语义不变：输入仍是双反斜杠的 -a\\\\b。
    _got_bs = sp("-a\\\\b")
    check("★ _split_win_args 反斜杠不当作转义（posix=False）",
          sp("-a\\b") == ["-a\\b"], f"got={_got_bs!r}")
    check("_split_win_args 单引号同样剥离", sp("'D:\\a b\\c.txt'") == ["D:\\a b\\c.txt"])

    p = sct._resolve_save_path("X:/tmp/a.png", app_dir=APP_DIR)
    check("_resolve_save_path 显式路径原样尊重", p == "X:/tmp/a.png", f"p={p!r}")
    p = sct._resolve_save_path(None, app_dir=APP_DIR)
    check("_resolve_save_path 默认落「截图」子目录", "截图" in p, f"p={p!r}")
    check("_resolve_save_path 默认文件名带前缀与时间戳",
          os.path.basename(p).startswith("screenshot_") and p.endswith(".png"), f"p={p!r}")
    check("_resolve_save_path 目录已创建", os.path.isdir(os.path.dirname(p)), f"p={p!r}")
    p2 = sct._resolve_save_path(None, prefix="shot2", app_dir=APP_DIR)
    check("_resolve_save_path prefix 生效", os.path.basename(p2).startswith("shot2_"), f"p2={p2!r}")

    class _Geo:
        def __init__(self, x, y, w, h):
            self._t = (x, y, w, h)

        def x(self):
            return self._t[0]

        def y(self):
            return self._t[1]

        def contains(self, x, y):
            gx, gy, gw, gh = self._t
            return gx <= x < gx + gw and gy <= y < gy + gh

    class _Scr:
        def __init__(self, x, y, w, h):
            self._g = _Geo(x, y, w, h)

        def geometry(self):
            return self._g

    class _App:
        def __init__(self):
            self._p = _Scr(0, 0, 1920, 1080)
            self._all = [_Scr(-1920, 0, 1920, 1080)]

        def primaryScreen(self):
            return self._p

        def screens(self):
            return self._all

    app = _App()
    _s, lx, ly = sct._screen_at_local(app, -500, 300)
    check("★ _screen_at_local 左副屏负坐标换算为正（不再被 max(0,·) 钳掉）",
          lx == 1420 and ly == 300, f"lx={lx} ly={ly}")
    _s, lx, ly = sct._screen_at_local(app, 100, 50)
    check("_screen_at_local 主屏坐标原样", lx == 100 and ly == 50, f"lx={lx} ly={ly}")

    # _run_on_gui_thread 同线程分支（真 PySide6 可用；不建 QApplication 也能走）
    class _Thr:
        def isCurrentThread(self):
            return True

    class _QApp:
        def thread(self):
            return _Thr()

    box = []
    ok, err = sct._run_on_gui_thread(_QApp(), lambda: box.append(1))
    check("_run_on_gui_thread 已在 GUI 线程 → 就地执行、返回 (True, None)",
          ok is True and err is None and box == [1], f"ok={ok} err={err}")

    def _bad():
        raise ValueError("inner")

    ok, err = sct._run_on_gui_thread(_QApp(), _bad)
    check("★ _run_on_gui_thread 闭包抛异常 → (False, 原异常)",
          ok is False and isinstance(err, ValueError), f"ok={ok} err={err!r}")


# ===========================================================================
# F 组：源码契约防回退
# ===========================================================================
def test_source_contracts():
    print("== F 组 源码契约防回退 ==")
    n_sig = len(re.findall(r"^def tool_\w+\(", SCT_SRC, re.M))
    check("15 个 def tool_*", n_sig == 15, f"n={n_sig}")
    for w in ("progress", "stop_event", "should_stop"):
        n = SCT_SRC.count(f"{w}=None")
        check(f"15 个工具签名含 {w}", n >= 15, f"n={n}")

    check("★ 有统一的必填参数校验 helper（_require）", "def _require(" in SCT_SRC)
    n_req = len(re.findall(r"^\s+_miss = _require\(args,", SCT_SRC, re.M))
    check("★ 8 个缺参工具都走了 _require（一处不少）", n_req == 8, f"n={n_req}")

    # 只看非注释行：源码里那句「原 shell=True 拼接…」是解释性注释，不是代码。
    # （判据扫字符不扫意图 —— 首版写成全文件 not in，被注释骗红了一次。）
    _code = "\n".join(l for l in SCT_SRC.splitlines()
                      if not l.strip().startswith("#"))
    check("★ process_start 不再用 shell=True（C1：命令注入）",
          "shell=True" not in _code,
          "有代码行仍带 shell=True")
    check("★ process_start 走 _split_win_args 拆参数",
          "_split_win_args(shell_args)" in SCT_SRC)

    check("★ process_list 用 csv 模块解析（不再按逗号裸切）",
          "csv.reader(" in SCT_SRC)
    check("★ 旧的裸切写法已清除",
          'line.replace(\'"\', "").split(",")' not in SCT_SRC,
          "残留 line.replace('\"','').split(',')")

    check("★ _aborted 的 fail-closed 留痕仍在",
          "fail-closed" in SCT_SRC and "logger.warning" in SCT_SRC)


def main():
    test_triple_consistency()
    test_required_parity()
    test_universal_contract()
    test_dependency_degrade()
    test_mouse_contract()
    test_keyboard_clipboard_contract()
    test_window_contract()
    test_process_contract()
    test_screenshot_contract()
    test_helpers()
    test_source_contracts()
    print(f"\n==== B 批（G5 系统控制功能契约）结果：PASS={_p}  FAIL={_f} ====")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
