"""
系统操控 Tool 集
---------------
pyautogui/pynput → 键鼠模拟
pygetwindow      → 窗口管理
subprocess       → 进程控制
pyperclip        → 剪贴板
QScreen.grabWindow → 截图

注册方式：TOOL_DEFS 声明式 list → tools.py 的 exec_tool() 路由分发
每项遵循 OpenAI function calling schema：
  {type: "function", function: {name, description, parameters: {type, properties, required}}}
"""
import logging

logger = logging.getLogger(__name__)

# ============================================================
# 1. Schema 注册 — 声明式 list，完全对齐 OpenAI function calling
# ============================================================

SYSTEM_CONTROL_TOOL_DEFS = [
    # ---- 截图 ----
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "截取屏幕、指定区域或指定窗口的图像。返回截图保存路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "object",
                        "description": "截取区域。省略则全屏截图。格式 {x, y, w, h}，以屏幕左上角为原点。",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "w": {"type": "integer"},
                            "h": {"type": "integer"},
                        },
                    },
                    "window_title": {
                        "type": "string",
                        "description": "按窗口标题模糊匹配截取指定窗口。与 region 互斥，region 优先。",
                    },
                    "save_path": {
                        "type": "string",
                        "description": "保存路径。省略则存到 output/screenshot_时间戳.png",
                    },
                },
            },
        },
    },
    # ---- 鼠标 ----
    {
        "type": "function",
        "function": {
            "name": "mouse_move",
            "description": "移动鼠标到指定坐标。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "目标 X 坐标"},
                    "y": {"type": "integer", "description": "目标 Y 坐标"},
                    "duration": {
                        "type": "number",
                        "description": "移动持续时间（秒）。默认 0 即瞬间移动。设为 0.3~0.5 可模拟人类操作。",
                    },
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_click",
            "description": "在当前位置或指定坐标点击鼠标。支持点击、双击、右键。",
            "parameters": {
                "type": "object",
                "properties": {
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "按钮。默认 left。",
                    },
                    "clicks": {
                        "type": "integer",
                        "description": "连击次数。1=单击，2=双击。默认 1。",
                    },
                    "x": {
                        "type": "integer",
                        "description": "X 坐标。省略则点击当前位置。",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y 坐标。省略则点击当前位置。",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_scroll",
            "description": "滚动鼠标滚轮。",
            "parameters": {
                "type": "object",
                "properties": {
                    "clicks": {
                        "type": "integer",
                        "description": "滚动格数。正数向上，负数向下。默认 3。",
                    },
                    "x": {
                        "type": "integer",
                        "description": "先移动到该坐标再滚动。省略则在当前位置滚动。",
                    },
                    "y": {"type": "integer"},
                },
            },
        },
    },
    # ---- 键盘 ----
    {
        "type": "function",
        "function": {
            "name": "keyboard_type",
            "description": "模拟键盘输入一段文本。支持中英文，自动处理输入法切换。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要输入的文本"},
                    "interval": {
                        "type": "number",
                        "description": "每字间隔（秒）。默认 0 瞬间输入；设为 0.05 模拟人工打字。",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_press",
            "description": "按下组合键或单个键。键名参考 pynput 规范。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "string",
                        "description": "键名。组合键用 + 连接，如 'ctrl+c'、'alt+tab'、'win+r'、'enter'、'esc'。",
                    },
                },
                "required": ["keys"],
            },
        },
    },
    # ---- 剪贴板 ----
    {
        "type": "function",
        "function": {
            "name": "clipboard_read",
            "description": "读取系统剪贴板内容。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_write",
            "description": "向系统剪贴板写入文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要写入的文本"},
                },
                "required": ["text"],
            },
        },
    },
    # ---- 窗口管理 ----
    {
        "type": "function",
        "function": {
            "name": "window_list",
            "description": "列出当前桌面上所有可见窗口的标题和位置。用于定位目标窗口。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "可选，按标题模糊过滤。省略则列出全部。",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_focus",
            "description": "将指定窗口切换到前台并获得焦点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "窗口标题，模糊匹配。取 window_list 返回的 title。",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_get_info",
            "description": "获取指定窗口的详细信息：位置、大小、状态（最小化/最大化/正常）、进程名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题，模糊匹配。"},
                },
                "required": ["title"],
            },
        },
    },
    # ---- 进程控制 ----
    {
        "type": "function",
        "function": {
            "name": "process_list",
            "description": "列出当前正在运行的进程。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "按进程名模糊过滤。省略则列出前 50 个占用最高的进程。",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_kill",
            "description": "终止指定进程。⚠️ 强制杀进程可能导致未保存数据丢失。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "进程名（如 notepad.exe）或 PID（如 1234）。优先按 PID 精确匹配。",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "是否强制终止（taskkill /F）。默认 False，先尝试优雅关闭。",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_start",
            "description": "启动一个程序或打开文件/URL。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "要启动的程序路径（如 notepad.exe）、文件路径或 URL。",
                    },
                    "args": {
                        "type": "string",
                        "description": "命令行参数。可选。",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "工作目录。可选，默认使用目标所在目录。",
                    },
                },
                "required": ["target"],
            },
        },
    },
]

# ============================================================
# 2. 工具实现 — 每个 tool_xxx() 返回 (result_str, deliverables, schedule)
#    永不抛异常，所有错误转为自然语言 str 返回
# ============================================================

import csv
import io
import os
import time
import shlex
import subprocess
import threading

from datetime import datetime
from pathlib import Path


# ---------- 内部工具 ----------

def _aborted(should_stop=None, stop_event=None):
    """用户请求停止（agent 的 should_stop 回调或 stop_event）。用于系统控制工具可中断判定。

    与 software_control_tools._aborted 同款语义（此前系统控制整条线**没有**这一层：
    14 个工具签名清一色 (cfg, app_dir, args)，tools.py 的 _w 包装器只给声明了对应
    参数的 handler 透传停止信号 → 点了「停止」键鼠/进程操作照样跑完）。
    P2-3：回调本身抛异常时按 fail-closed 处理——视为『已请求停止』并留痕，
    与原 except pass 的 fail-open 行为相反：不可读的停止信号绝不该被当成「继续干」。
    """
    try:
        if should_stop and callable(should_stop) and should_stop():
            return True
        if stop_event and stop_event.is_set():
            return True
    except Exception as _e:
        logger.warning("停止信号读取异常，按已停止处理（fail-closed）: %r", _e)
        return True
    return False


def _require(args, *keys):
    """必填参数校验：缺哪个就回报哪个，参数齐则返回 None。

    此前 8 个工具直接 `args["x"]` 取必填参数，缺键会抛 KeyError —— 既违背本模块
    文件头「永不抛异常，所有错误转为自然语言 str 返回」的承诺，又被 tools.exec_tool
    的兜底吞成英文 `工具执行异常：'x'`，模型根本不知道缺了哪个参数。
    只判「键不存在」，不把空串/None 当缺（保持与原先 args[...] 的语义一致）。
    """
    missing = [k for k in keys if k not in args]
    if not missing:
        return None
    return "缺少必填参数：" + "、".join(missing) + "。请补齐后重试。"


def _split_win_args(s):
    """审计修复 C1：把 LLM 传入的参数字符串按 Windows 习惯切成 argv 列表。

    shlex posix=False 不把反斜杠当转义符（C:\\Users\\... 路径原样保留），
    再把成对的外层引号剥掉即可交给 subprocess 的列表模式（shell=False）。
    """
    if not s or not str(s).strip():
        return []
    try:
        toks = shlex.split(str(s), posix=False)
    except ValueError:  # 引号不闭合等畸形输入 → 退化为空白切分
        toks = str(s).split()
    out = []
    for t in toks:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ("\"", "'"):
            t = t[1:-1]
        out.append(t)
    return out

def _resolve_save_path(save_path, prefix="screenshot", app_dir=None):
    """解析截图保存路径。
    v4.162.x：默认落到统一产物目录的「截图」子目录（~/Documents/小臭玩AI/产物/截图），
    不再塞进程序目录 app_dir/output（dist 打包后随清理丢失）。
    调用方显式给 save_path 时仍优先尊重。"""
    if save_path:
        return save_path
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if app_dir is None:
        app_dir = os.getcwd()
    try:
        from config import PRODUCTS_DIR
        base = os.path.join(PRODUCTS_DIR, "截图")
    except Exception:
        base = os.path.join(app_dir, "output")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return os.path.join(base, f"{prefix}_{ts}.png")


# ---------- 审计修复 C4：截图必须在 GUI 线程执行 ----------
# exec_tool 跑在 AgentWorker 线程（agent.py），而 QPixmap 系列 API 属 GUI 线程
# 专属，Qt6 下跨线程创建/使用属未定义行为（release 构建常"碰巧能跑"）。
# 这里用 QObject+Signal 把闭包投递回 GUI 线程同步执行（自持 Event 等待，
# 不依赖 BlockingQueuedConnection 对 Python 槽的支持）；QPixmap 在 GUI 线程内
# 就地转 QImage（可安全跨线程）后回传。

def _run_on_gui_thread(app, fn, timeout=15):
    """在 GUI 线程执行 fn()（已在则直接执行）。返回 (ok, err)。"""
    import threading
    from PySide6.QtCore import QObject, Signal
    if app.thread().isCurrentThread():
        try:
            fn()
            return True, None
        except Exception as e:
            return False, e
    box = {}
    done = threading.Event()

    class _Call(QObject):
        go = Signal()

    holder = _Call()

    def _slot():
        try:
            fn()
        except Exception as e:
            box["err"] = e
        finally:
            done.set()

    holder.go.connect(_slot)
    holder.moveToThread(app.thread())   # 只能在创建者线程 move：此处即 worker 线程
    holder.go.emit()                    # AutoConnection → 队列到 GUI 线程执行
    if not done.wait(timeout):
        return False, RuntimeError(f"GUI 线程 {timeout}s 内未响应（界面正忙？），本次截图放弃")
    if box.get("err") is not None:
        return False, box["err"]
    return True, None


def _screen_at_local(app, x, y):
    """找到包含全局坐标 (x,y) 的屏幕，返回 (screen, 该屏幕内的正坐标 lx,ly)。

    左侧副屏的全局 left/top 是负数：原实现 max(0,·) 钳位后宽高不校正 → 截错区。
    换算成所在屏幕局部坐标后恒为非负，grabWindow 不再依赖负坐标语义。
    """
    scr = app.primaryScreen()
    try:
        for s in (app.screens() or []):
            if s.geometry().contains(x, y):
                scr = s
                break
    except Exception:
        pass
    gx, gy = scr.geometry().x(), scr.geometry().y()
    return scr, x - gx, y - gy


# ---------- 截图 ----------

def tool_screenshot(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    region = args.get("region")
    window_title = args.get("window_title")
    save_path = _resolve_save_path(args.get("save_path"), app_dir=app_dir)

    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if not app:
            return ("调用失败：Qt Application 未初始化。screenshot 必须在主进程内执行。", [], None)

        box = {}

        def _do():
            if region:
                x, y = region.get("x", 0), region.get("y", 0)
                w, h = region.get("w", 0), region.get("h", 0)
                scr, lx, ly = _screen_at_local(app, x, y)
                box["img"] = scr.grabWindow(0, lx, ly, w, h).toImage()
            elif window_title:
                import pygetwindow as gw
                wins = gw.getWindowsWithTitle(window_title)
                if not wins:
                    box["ret"] = (f"未找到标题含 '{window_title}' 的窗口", [], None)
                    return
                win = wins[0]
                # 用窗口中心点定位所在屏幕（跨屏大窗也稳定），不再 max(0,·) 钳负坐标
                scr, lx, ly = _screen_at_local(
                    app, win.left + win.width // 2, win.top + win.height // 2)
                box["img"] = scr.grabWindow(0, lx, ly, win.width, win.height).toImage()
            else:
                # wId=0 全域截图：保持原语义（Windows 下覆盖所有显示器）
                box["img"] = app.primaryScreen().grabWindow(0).toImage()

        ok, err = _run_on_gui_thread(app, _do)
        if "ret" in box:
            return box["ret"]
        if not ok:
            return (f"截图失败：{err}", [], None)
        img = box.get("img")
        if img is None or img.isNull():
            return ("截图失败：未取到屏幕像素（坐标越界 / 屏幕休眠？）", [], None)

        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        img.save(save_path, "PNG")
        return (f"截图已保存到 {save_path}", [save_path], None)

    except ImportError as e:
        return (f"缺少依赖包：{e}。请先安装：pip install pygetwindow", [], None)
    except Exception as e:
        logger.exception("screenshot 失败")
        return (f"截图失败：{e}", [], None)


# ---------- 鼠标 ----------

def _init_pyautogui():
    import pyautogui
    pyautogui.FAILSAFE = True  # 移到屏幕角落中止
    return pyautogui


def tool_mouse_move(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    _miss = _require(args, "x", "y")
    if _miss:
        return (_miss, [], None)
    x, y = args["x"], args["y"]
    duration = args.get("duration", 0)
    try:
        pag = _init_pyautogui()
        pag.moveTo(x, y, duration=duration)
        return (f"鼠标已移动到 ({x}, {y})", [], None)
    except ImportError:
        return ("缺少依赖包：pyautogui。请先安装：pip install pyautogui", [], None)
    except Exception as e:
        return (f"鼠标移动失败：{e}", [], None)


def tool_mouse_click(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    button = args.get("button", "left")
    clicks = args.get("clicks", 1)
    x = args.get("x")
    y = args.get("y")
    try:
        pag = _init_pyautogui()
        if x is not None and y is not None:
            pag.click(x, y, clicks=clicks, button=button)
        else:
            pag.click(clicks=clicks, button=button)
        desc = f"{'双击' if clicks == 2 else '单击'}{button}键"
        pos = f"({x}, {y})" if x is not None else "当前位置"
        return (f"{desc}完成 {pos}", [], None)
    except ImportError:
        return ("缺少依赖包：pyautogui。请先安装：pip install pyautogui", [], None)
    except Exception as e:
        return (f"鼠标点击失败：{e}", [], None)


def tool_mouse_scroll(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    clicks = args.get("clicks", 3)
    x = args.get("x")
    y = args.get("y")
    try:
        pag = _init_pyautogui()
        if x is not None and y is not None:
            pag.moveTo(x, y)
        pag.scroll(clicks)
        direction = "向上" if clicks > 0 else "向下"
        return (f"滚轮{direction}滚动 {abs(clicks)} 格", [], None)
    except ImportError:
        return ("缺少依赖包：pyautogui。请先安装：pip install pyautogui", [], None)
    except Exception as e:
        return (f"滚轮滚动失败：{e}", [], None)


# ---------- 键盘 ----------

def tool_keyboard_type(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    _miss = _require(args, "text")
    if _miss:
        return (_miss, [], None)
    text = args["text"]
    interval = args.get("interval", 0)
    try:
        pag = _init_pyautogui()
        pag.typewrite(text, interval=interval)
        return (f"已输入文本（{len(text)} 字）", [], None)
    except ImportError:
        return ("缺少依赖包：pyautogui。请先安装：pip install pyautogui", [], None)
    except Exception as e:
        return (f"键盘输入失败：{e}", [], None)


def tool_keyboard_press(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    _miss = _require(args, "keys")
    if _miss:
        return (_miss, [], None)
    keys = args["keys"]
    try:
        pag = _init_pyautogui()
        pag.hotkey(*keys.split("+"))
        return (f"已按下组合键 {keys}", [], None)
    except ImportError:
        return ("缺少依赖包：pyautogui。请先安装：pip install pyautogui", [], None)
    except Exception as e:
        return (f"按键失败：{e}", [], None)


# ---------- 剪贴板 ----------

def tool_clipboard_read(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    try:
        import pyperclip
        text = pyperclip.paste()
        if not text:
            return ("剪贴板为空", [], None)
        return (text, [], None)
    except ImportError:
        return ("缺少依赖包：pyperclip。请先安装：pip install pyperclip", [], None)
    except Exception as e:
        return (f"读取剪贴板失败：{e}", [], None)


def tool_clipboard_write(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    _miss = _require(args, "text")
    if _miss:
        return (_miss, [], None)
    text = args["text"]
    try:
        import pyperclip
        pyperclip.copy(text)
        return ("已写入剪贴板", [], None)
    except ImportError:
        return ("缺少依赖包：pyperclip。请先安装：pip install pyperclip", [], None)
    except Exception as e:
        return (f"写入剪贴板失败：{e}", [], None)


# ---------- 窗口管理 ----------

def tool_window_list(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    filt = args.get("filter", "")
    try:
        import pygetwindow as gw
        all_wins = gw.getAllWindows()
        if filt:
            all_wins = [w for w in all_wins if filt.lower() in w.title.lower() and w.title.strip()]
        else:
            all_wins = [w for w in all_wins if w.title.strip()][:50]

        lines = []
        for w in all_wins:
            visible = "👁" if w.visible else ""
            lines.append(f"  [{w.left},{w.top} {w.width}x{w.height}] {visible} {w.title}")
        header = f"找到 {len(all_wins)} 个窗口"
        return (header + "\n" + "\n".join(lines) if lines else header + "（无匹配窗口）", [], None)
    except ImportError:
        return ("缺少依赖包：pygetwindow。请先安装：pip install pygetwindow", [], None)
    except Exception as e:
        return (f"列出窗口失败：{e}", [], None)


def tool_window_focus(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    _miss = _require(args, "title")
    if _miss:
        return (_miss, [], None)
    title = args["title"]
    try:
        import pygetwindow as gw
        wins = gw.getWindowsWithTitle(title)
        if not wins:
            return (f"未找到标题含 '{title}' 的窗口", [], None)
        w = wins[0]
        if w.isMinimized:
            w.restore()
        w.activate()
        return (f"窗口 '{w.title}' 已切换到前台", [], None)
    except ImportError:
        return ("缺少依赖包：pygetwindow。请先安装：pip install pygetwindow", [], None)
    except Exception as e:
        return (f"切换窗口失败：{e}", [], None)


def tool_window_get_info(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    _miss = _require(args, "title")
    if _miss:
        return (_miss, [], None)
    title = args["title"]
    try:
        import pygetwindow as gw
        wins = gw.getWindowsWithTitle(title)
        if not wins:
            return (f"未找到标题含 '{title}' 的窗口", [], None)
        w = wins[0]
        state = "最小化" if w.isMinimized else "最大化" if w.isMaximized else "正常"
        info = (
            f"标题: {w.title}\n"
            f"位置: ({w.left}, {w.top})\n"
            f"尺寸: {w.width} x {w.height}\n"
            f"状态: {state}\n"
            f"可见: {'是' if w.visible else '否'}"
        )
        return (info, [], None)
    except ImportError:
        return ("缺少依赖包：pygetwindow。请先安装：pip install pygetwindow", [], None)
    except Exception as e:
        return (f"获取窗口信息失败：{e}", [], None)


# ---------- 进程控制 ----------

def tool_process_list(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    filt = args.get("filter", "")
    try:
        # tasklist 输出稳定，不依赖 psutil
        cmd = ["tasklist", "/FO", "CSV", "/NH"]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="gbk", errors="replace")

        # tasklist /FO CSV 的内存列自带千分位（"12,345 K"）且被引号包住。原先用
        # line.replace('"','').split(",") 会把这一列切成两段 -> 内存值只取到 "12"，
        # 1,234,567 K 的进程显示成 0 MB，排序也整体错乱。改用 csv 模块按 RFC4180
        # 解析引号字段，再只去掉千分位逗号。
        processes = []
        for parts in csv.reader(io.StringIO(result.stdout)):
            if len(parts) < 5:
                continue
            name, pid, mem_str = parts[0], parts[1], parts[4]
            if filt and filt.lower() not in name.lower():
                continue
            try:
                mem_kb = int(mem_str.replace("K", "").replace(",", "").strip() or 0)
            except ValueError:
                mem_kb = 0
            processes.append((name.strip(), pid.strip(), mem_kb))

        # 按内存降序
        processes.sort(key=lambda x: x[2], reverse=True)
        if not filt:
            processes = processes[:50]

        lines = []
        for name, pid, mem_kb in processes:
            lines.append(f"  {name:30s} PID:{pid:>6s}  {mem_kb//1024:>5d} MB")
        header = f"找到 {len(processes)} 个进程"
        return (header + "\n" + "\n".join(lines) if lines else header + "（无匹配进程）", [], None)
    except Exception as e:
        return (f"列出进程失败：{e}", [], None)


def _count_processes(name):
    """只读统计同名进程数（tasklist 过滤查询）；查询失败返回 None。

    G4：`taskkill /IM <name>` 会杀掉**所有**同名进程，此前返回值只写
    「已终止进程: <name>」，用户看不到究竟影响了几个。计数失败一律返回 None
    （tasklist 缺失 / 超时 / 权限 / 输出畸形），**绝不阻断终止动作本身**。
    名字不带 .exe 时回退试 <name>.exe 一次（`taskkill /IM` 能匹配无扩展名，
    `tasklist /FI IMAGENAME` 不能）。

    只读：仅调 tasklist，不 spawn 任何有副作用的子进程。
    """
    cands = [name] if name.lower().endswith(".exe") else [name, name + ".exe"]
    for cand in cands:
        try:
            r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {cand}",
                                "/FO", "CSV", "/NH"],
                               capture_output=True, text=True, encoding="gbk",
                               errors="replace", timeout=10)
        except Exception:
            return None
        n = 0
        for parts in csv.reader(io.StringIO(r.stdout or "")):
            if parts and parts[0].strip().lower() == cand.lower():
                n += 1
        if n:
            return n
    return 0


def tool_process_kill(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    _miss = _require(args, "name")
    if _miss:
        return (_miss, [], None)
    name = args["name"]
    force = args.get("force", False)
    try:
        # 判断是 PID 还是进程名
        flag = "/PID" if name.isdigit() else "/IM"
        cmd = ["taskkill", flag, name]
        if force:
            cmd.append("/F")

        # G4：/IM 会杀掉**所有**同名进程 → 先只读统计影响面（PID 路径天然只影响 1 个，
        # 不查）。统计失败返回 None → 不阻断、不加后缀、不谎报。
        n_same = None if name.isdigit() else _count_processes(name)

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="gbk", errors="replace")
        if result.returncode == 0:
            if n_same:
                return (f"已终止进程: {name}（同名进程共 {n_same} 个，已一并终止）", [], None)
            return (f"已终止进程: {name}", [], None)
        else:
            return (f"终止失败: {result.stderr.strip() or result.stdout.strip()}", [], None)
    except Exception as e:
        return (f"终止进程失败: {e}", [], None)


def tool_process_start(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    _miss = _require(args, "target")
    if _miss:
        return (_miss, [], None)
    target = args["target"]
    shell_args = args.get("args", "")
    working_dir = args.get("working_dir", "")

    try:
        # 审计修复 C1：原 shell=True 拼接命令行经 cmd.exe 解析，target/args 里的
        # `&`、`|`、`%VAR%` 等元字符即任意命令注入。改为 shell=False + 参数列表：
        # 可执行文件与参数分离，元字符只作字面量传给目标进程，不再被 shell 解释。
        argv = [target] + _split_win_args(shell_args)
        if working_dir:
            proc = subprocess.Popen(argv, cwd=working_dir)
        else:
            proc = subprocess.Popen(argv)

        return (f"已启动进程，PID: {proc.pid}", [], None)
    except FileNotFoundError:
        return (f"找不到可执行文件: {target}", [], None)
    except Exception as e:
        return (f"启动失败: {e}", [], None)


# ============================================================
# 3. 路由表 — tools.py 中的 exec_tool() 通过此表分发
# ============================================================

SYSTEM_CONTROL_TOOL_TABLE = {
    "screenshot":       tool_screenshot,
    "mouse_move":       tool_mouse_move,
    "mouse_click":      tool_mouse_click,
    "mouse_scroll":     tool_mouse_scroll,
    "keyboard_type":    tool_keyboard_type,
    "keyboard_press":   tool_keyboard_press,
    "clipboard_read":   tool_clipboard_read,
    "clipboard_write":  tool_clipboard_write,
    "window_list":      tool_window_list,
    "window_focus":     tool_window_focus,
    "window_get_info":  tool_window_get_info,
    "process_list":     tool_process_list,
    "process_kill":     tool_process_kill,
    "process_start":    tool_process_start,
}
