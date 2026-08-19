"""
软件操控 Tool 集
---------------
pywinauto → Windows UI 自动化主力
  - 启动/强杀应用
  - 窗口置顶/最小化/最大化/恢复
  - 定位按钮/输入框/菜单项/列表/树节点
  - 点击控件/输入文字/读取文字
  - 等待控件出现
  - 控件树遍历

注册方式：TOOL_DEFS 声明式 list → tools.py 的 exec_tool() 路由分发
每项遵循 OpenAI function calling schema。
"""
import logging

logger = logging.getLogger(__name__)

# ============================================================
# 1. Schema 注册
# ============================================================

SOFTWARE_CONTROL_TOOL_DEFS = [
    # ---- 应用生命周期 ----
    {
        "type": "function",
        "function": {
            "name": "app_launch",
            "description": "启动一个应用程序，支持可执行文件路径和 UWP 应用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "应用路径（如 C:/Program Files/App/app.exe）或应用名称（如 notepad.exe、calc.exe）。也支持 UWP 应用名称。",
                    },
                    "args": {
                        "type": "string",
                        "description": "命令行参数。可选。",
                    },
                    "wait_ready": {
                        "type": "boolean",
                        "description": "是否等待应用主窗口就绪后再返回。默认 True。",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_kill",
            "description": "强制终止一个正在运行的应用程序。⚠️ 可能导致未保存数据丢失。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "进程名（如 notepad.exe）或窗口标题（如 无标题 - 记事本）。优先按进程名匹配。",
                    },
                },
                "required": ["target"],
            },
        },
    },
    # ---- 窗口状态 ----
    {
        "type": "function",
        "function": {
            "name": "app_focus",
            "description": "将指定应用的窗口切换到前台并获得焦点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "窗口标题，模糊匹配。也可以用进程名。",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_window_state",
            "description": "改变指定窗口的状态：最大化、最小化、还原、置顶。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题，模糊匹配。"},
                    "action": {
                        "type": "string",
                        "enum": ["maximize", "minimize", "restore", "topmost_on", "topmost_off", "close"],
                        "description": "要执行的操作。",
                    },
                },
                "required": ["title", "action"],
            },
        },
    },
    # ---- 控件定位 ----
    {
        "type": "function",
        "function": {
            "name": "app_list_controls",
            "description": "列出指定窗口中的所有可交互控件（按钮、输入框、菜单、列表等），含控件名称、类型、automation_id、位置。用于了解窗口结构、定位目标控件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题，模糊匹配。"},
                    "filter_type": {
                        "type": "string",
                        "description": "按控件类型过滤，如 Button、Edit、ComboBox、ListItem、Menu。省略则列出所有。",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "遍历最大深度。默认 4。过深可能导致响应变慢。",
                    },
                },
                "required": ["title"],
            },
        },
    },
    # ---- 控件交互 ----
    {
        "type": "function",
        "function": {
            "name": "app_click",
            "description": "点击指定窗口中的某个控件。支持按文本、automation_id、class_name、control_type 多种方式定位。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题，模糊匹配。如果省略则操作最前台的匹配窗口。"},
                    "target": {
                        "type": "string",
                        "description": "控件标识。按优先级：automation_id（精确）、name/text（精确）、title（模糊）。",
                    },
                    "control_type": {
                        "type": "string",
                        "description": "控件类型进一步限定。可选值：Button、Edit、ComboBox、CheckBox、RadioButton、TabItem、MenuItem、ListItem、TreeItem、Hyperlink。",
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "鼠标按钮。默认 left。",
                    },
                },
                "required": ["title", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_type",
            "description": "在指定窗口的输入框中输入文本。先清空再输入（除非 append=True）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "窗口标题，模糊匹配。",
                    },
                    "text": {"type": "string", "description": "要输入的文本。"},
                    "target": {
                        "type": "string",
                        "description": "输入框的标识（name / automation_id / 前一个 Label 的文字）。如果省略，定位到窗口内第一个 Edit 控件。",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "是否追加而非替换。默认 False（清空后输入）。",
                    },
                },
                "required": ["title", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_get_text",
            "description": "读取指定窗口中某个控件或整个窗口的可见文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题，模糊匹配。"},
                    "target": {
                        "type": "string",
                        "description": "控件标识。省略则读取整个窗口的可见文本。",
                    },
                },
                "required": ["title"],
            },
        },
    },
    # ---- 控件等待 ----
    {
        "type": "function",
        "function": {
            "name": "app_wait_for",
            "description": "等待某个控件出现或消失。常用于应用启动后的就绪等待。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题。"},
                    "target": {"type": "string", "description": "控件标识。"},
                    "exists": {
                        "type": "boolean",
                        "description": "True=等待出现，False=等待消失。默认 True。",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "最大等待秒数。默认 10。",
                    },
                },
                "required": ["title", "target"],
            },
        },
    },
    # ---- 截图 ----
    {
        "type": "function",
        "function": {
            "name": "app_screenshot",
            "description": "对指定应用窗口截图。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题，模糊匹配。"},
                    "save_path": {
                        "type": "string",
                        "description": "保存路径。省略则存到 output/app_screenshot_时间戳.png。",
                    },
                },
                "required": ["title"],
            },
        },
    },
]

# ============================================================
# 2. 工具实现
# ============================================================

import os
import subprocess
import time

from datetime import datetime
from pathlib import Path


# ---------- pywinauto 初始化 ----------

def _connect_or_launch(target, title=None, timeout=10):
    """
    返回 (app, window) 元组。
    优先按窗口标题 connect；失败则尝试按进程名 connect；再失败则 launch。
    """
    from pywinauto import Application

    # 1) 尝试按窗口标题连接
    if title:
        try:
            app = Application(backend="uia").connect(title=title, timeout=3)
            return app, app.window(title=title)
        except Exception:
            pass

    # 2) 尝试按进程名连接（target 是 .exe）
    if target and target.lower().endswith(".exe"):
        try:
            app = Application(backend="uia").connect(path=target, timeout=3)
            if title:
                return app, app.window(title=title)
            return app, app.top_window()
        except Exception:
            pass

    # 3) 全新启动
    try:
        app = Application(backend="uia").start(target, timeout=timeout)
        if title:
            return app, app.window(title=title)
        return app, app.top_window()
    except Exception as e:
        raise RuntimeError(f"无法启动或连接 {target}: {e}")


def _find_control(window, target, control_type=None):
    """
    在 window 中按 target 查找控件。
    查找优先级：automation_id → name → title（模糊）→ control_type
    返回第一个匹配的 wrapper。
    """
    # 1) 精确 automation_id
    try:
        ctrl = window.child_window(auto_id=target, control_type=control_type)
        ctrl.wait("exists", timeout=0.5)
        return ctrl
    except Exception:
        pass

    # 2) 精确 name
    try:
        ctrl = window.child_window(title=target, control_type=control_type)
        ctrl.wait("exists", timeout=0.5)
        return ctrl
    except Exception:
        pass

    # 3) 模糊 title
    try:
        ctrl = window.child_window(title_re=f".*{target}.*", control_type=control_type)
        ctrl.wait("exists", timeout=0.5)
        return ctrl
    except Exception:
        pass

    # 4) 仅按 control_type 找第一个
    if control_type:
        try:
            ctrl = window.child_window(control_type=control_type)
            ctrl.wait("exists", timeout=0.5)
            return ctrl
        except Exception:
            pass

    raise RuntimeError(f"未找到控件: target='{target}', control_type='{control_type}'")


def _ctrl_type_to_str(ctrl):
    """pywinauto 控件类型转可读字符串。"""
    try:
        return ctrl.element_info.control_type or "Unknown"
    except Exception:
        return "Unknown"


def _print_control_tree(ctrl, depth=0, max_depth=4):
    """递归遍历控件树，生成可读的文本列表。"""
    if depth > max_depth:
        return []
    lines = []
    indent = "  " * depth
    try:
        info = ctrl.element_info
        name = info.name or ""
        auto_id = info.automation_id or ""
        ctrl_type = info.control_type or "Unknown"
        rect = info.rectangle
        pos = f"({rect.left},{rect.top} {rect.right-rect.left}x{rect.bottom-rect.top})" if rect else ""

        label = f"{indent}[{ctrl_type}]"
        if auto_id:
            label += f" id='{auto_id}'"
        if name:
            label += f" '{name[:40]}'"
        if pos:
            label += f" {pos}"
        if not name and not auto_id:
            label += " (无标识)"

        lines.append(label)
    except Exception:
        lines.append(f"{indent}[?] (读取失败)")
        return lines

    # 递归子控件
    try:
        children = ctrl.children()
        for child in children:
            lines.extend(_print_control_tree(child, depth + 1, max_depth))
    except Exception:
        pass

    return lines


# ---------- 应用生命周期 ----------

def tool_app_launch(cfg, app_dir, args):
    target = args["target"]
    shell_args = args.get("args", "")
    wait_ready = args.get("wait_ready", True)

    try:
        from pywinauto import Application

        full_cmd = target
        if shell_args:
            full_cmd += " " + shell_args

        if wait_ready:
            app = Application(backend="uia").start(full_cmd, timeout=15)
            try:
                w = app.top_window()
                return (f"已启动 {target}，当前窗口: {w.window_text()}", [], None)
            except Exception:
                return (f"已启动 {target}（窗口未就绪）", [], None)
        else:
            subprocess.Popen(full_cmd, shell=True)
            return (f"已发起启动 {target}", [], None)

    except ImportError:
        return ("缺少依赖：pywinauto。请安装：pip install pywinauto", [], None)
    except Exception as e:
        return (f"启动失败：{e}", [], None)


def tool_app_kill(cfg, app_dir, args):
    target = args["target"]
    try:
        # 先尝试 taskkill /IM
        cmd = ["taskkill", "/IM", target, "/F"]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="gbk", errors="replace")
        if result.returncode == 0:
            return (f"已强制终止 {target}", [], None)

        # 尝试 taskkill /F /FI（按窗口标题模糊匹配）
        cmd2 = ["taskkill", "/F", "/FI", f"WINDOWTITLE eq {target}"]
        result2 = subprocess.run(cmd2, capture_output=True, text=True, encoding="gbk", errors="replace")
        if result2.returncode == 0:
            return (f"已强制终止匹配窗口 '{target}' 的进程", [], None)

        return ("未找到匹配的进程", [], None)
    except Exception as e:
        return (f"终止失败：{e}", [], None)


# ---------- 窗口状态 ----------

def tool_app_focus(cfg, app_dir, args):
    title = args["title"]
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(title=title, timeout=5)
        w = app.window(title=title)
        w.set_focus()
        return (f"窗口 '{w.window_text()}' 已获得焦点", [], None)
    except ImportError:
        return ("缺少依赖：pywinauto", [], None)
    except Exception as e:
        # 兜底：pygetwindow
        try:
            import pygetwindow as gw
            wins = gw.getWindowsWithTitle(title)
            if wins:
                w = wins[0]
                if w.isMinimized:
                    w.restore()
                w.activate()
                return (f"窗口 '{w.title}' 已切换到前台（pygetwindow 兜底）", [], None)
        except Exception:
            pass
        return (f"切换失败：{e}", [], None)


def tool_app_window_state(cfg, app_dir, args):
    title = args["title"]
    action = args["action"]
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(title=title, timeout=5)
        w = app.window(title=title)

        actions = {
            "maximize": lambda: w.maximize(),
            "minimize": lambda: w.minimize(),
            "restore": lambda: w.restore(),
            "close": lambda: w.close(),
        }
        if action in actions:
            actions[action]()
            return (f"窗口 '{w.window_text()}' 已{action}", [], None)

        # 置顶需要 Win32 API
        if action in ("topmost_on", "topmost_off"):
            import ctypes
            from ctypes import wintypes

            hwnd = w.handle
            flag = -1 if action == "topmost_on" else -2
            ctypes.windll.user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(flag),
                0, 0, 0, 0,
                0x0001 | 0x0002,  # SWP_NOSIZE | SWP_NOMOVE
            )
            state_text = "已置顶" if action == "topmost_on" else "已取消置顶"
            return (f"{state_text}: '{w.window_text()}'", [], None)

        return (f"不支持的操作: {action}", [], None)

    except ImportError:
        return ("缺少依赖：pywinauto", [], None)
    except Exception as e:
        return (f"操作失败：{e}", [], None)


# ---------- 控件定位 ----------

def tool_app_list_controls(cfg, app_dir, args):
    title = args["title"]
    filter_type = args.get("filter_type")
    max_depth = args.get("max_depth", 4)

    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(title=title, timeout=5)
        w = app.window(title=title)

        lines = _print_control_tree(w, max_depth=max_depth)

        if filter_type:
            lines = [l for l in lines if f"[{filter_type}]" in l]

        if not lines:
            return (f"窗口 '{w.window_text()}' 中未找到匹配控件", [], None)

        header = f"窗口 '{w.window_text()}' 控件树（深度≤{max_depth}）"
        if filter_type:
            header += f" 类型={filter_type}"
        return (header + "\n" + "\n".join(lines), [], None)

    except ImportError:
        return ("缺少依赖：pywinauto", [], None)
    except Exception as e:
        return (f"枚举控件失败：{e}", [], None)


# ---------- 控件交互 ----------

def tool_app_click(cfg, app_dir, args):
    title = args.get("title")
    target = args["target"]
    control_type = args.get("control_type")
    button = args.get("button", "left")

    try:
        from pywinauto import Application

        if title:
            app = Application(backend="uia").connect(title=title, timeout=5)
            w = app.window(title=title)
        else:
            # 无 title：取前台窗口
            app = Application(backend="uia").connect(active_only=True)
            w = app.top_window()

        ctrl = _find_control(w, target, control_type)

        if button == "right":
            ctrl.click_input(button="right")
        else:
            ctrl.click()

        return (f"已点击 '{ctrl.window_text() or target}' in '{w.window_text()}'", [], None)

    except ImportError:
        return ("缺少依赖：pywinauto", [], None)
    except Exception as e:
        return (f"点击失败：{e}", [], None)


def tool_app_type(cfg, app_dir, args):
    title = args["title"]
    text = args["text"]
    target = args.get("target")
    append = args.get("append", False)

    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(title=title, timeout=5)
        w = app.window(title=title)

        # 定位输入框
        if target:
            try:
                ctrl = _find_control(w, target, control_type="Edit")
            except Exception:
                # 如果 target 不是 Edit 本身，尝试找它旁边的 label
                ctrl = w.child_window(title=target)
                ctrl = ctrl.parent().child_window(control_type="Edit")
        else:
            ctrl = w.child_window(control_type="Edit")

        ctrl.set_focus()
        if not append:
            ctrl.set_edit_text("")
        ctrl.type_keys(text, with_spaces=True)
        return (f"已输入 {len(text)} 个字符到 '{w.window_text()}'", [], None)

    except ImportError:
        return ("缺少依赖：pywinauto", [], None)
    except Exception as e:
        return (f"输入失败：{e}", [], None)


def tool_app_get_text(cfg, app_dir, args):
    title = args["title"]
    target = args.get("target")

    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(title=title, timeout=5)
        w = app.window(title=title)

        if target:
            ctrl = _find_control(w, target)
            text = ctrl.window_text()
            return (text if text else "（控件无文本）", [], None)
        else:
            # 递归收集所有可见文本
            texts = []

            def collect(node):
                try:
                    t = node.window_text()
                    if t and t.strip():
                        texts.append(t.strip())
                except Exception:
                    pass
                try:
                    for child in node.children():
                        collect(child)
                except Exception:
                    pass

            collect(w)
            return ("\n".join(texts[:100]) if texts else "（窗口无可见文本）", [], None)

    except ImportError:
        return ("缺少依赖：pywinauto", [], None)
    except Exception as e:
        return (f"读取文本失败：{e}", [], None)


# ---------- 控件等待 ----------

def tool_app_wait_for(cfg, app_dir, args):
    title = args["title"]
    target = args["target"]
    exists = args.get("exists", True)
    timeout = args.get("timeout", 10)

    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(title=title, timeout=5)
        w = app.window(title=title)

        try:
            ctrl = w.child_window(title=target)
            if exists:
                ctrl.wait("exists", timeout=timeout)
                return (f"控件 '{target}' 已出现（{timeout}s 内）", [], None)
            else:
                ctrl.wait_not("exists", timeout=timeout)
                return (f"控件 '{target}' 已消失（{timeout}s 内）", [], None)
        except Exception:
            if exists:
                return (f"控件 '{target}' 在 {timeout}s 内未出现", [], None)
            else:
                return (f"控件 '{target}' 已不存在", [], None)

    except ImportError:
        return ("缺少依赖：pywinauto", [], None)
    except Exception as e:
        return (f"等待失败：{e}", [], None)


# ---------- 截图 ----------

def tool_app_screenshot(cfg, app_dir, args):
    title = args["title"]
    save_path = args.get("save_path")
    if not save_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(app_dir, "output", f"app_screenshot_{ts}.png")

    try:
        # 先取 pywinauto 窗口坐标
        from pywinauto import Application
        app = Application(backend="uia").connect(title=title, timeout=5)
        w = app.window(title=title)
        rect = w.rectangle()

        # 用 QScreen 截图
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance()
        if not qapp:
            return ("Qt Application 未初始化", [], None)

        x, y = max(0, rect.left), max(0, rect.top)
        w_px, h_px = rect.width(), rect.height()
        pixmap = qapp.primaryScreen().grabWindow(0, x, y, w_px, h_px)

        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        pixmap.save(save_path, "PNG")
        return (f"窗口截图已保存到 {save_path} ({w_px}x{h_px})", [save_path], None)

    except ImportError:
        return ("缺少依赖：pywinauto", [], None)
    except Exception as e:
        return (f"截图失败：{e}", [], None)


# ============================================================
# 3. 路由表
# ============================================================

SOFTWARE_CONTROL_TOOL_TABLE = {
    "app_launch":          tool_app_launch,
    "app_kill":            tool_app_kill,
    "app_focus":           tool_app_focus,
    "app_window_state":    tool_app_window_state,
    "app_list_controls":   tool_app_list_controls,
    "app_click":           tool_app_click,
    "app_type":            tool_app_type,
    "app_get_text":        tool_app_get_text,
    "app_wait_for":        tool_app_wait_for,
    "app_screenshot":      tool_app_screenshot,
}
