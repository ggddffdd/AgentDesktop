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
import time

logger = logging.getLogger(__name__)

# ②-B.1：控件树硬上限，防极长控件树撑爆 LLM 上下文（与命令大输出同隐患）
MAX_CONTROL_TREE_LINES = 400


def _aborted(should_stop=None, stop_event=None):
    """用户请求停止（agent 的 should_stop 回调或 stop_event）。用于软件控制工具可中断判定。
    P2-3 修：回调本身抛异常时按 fail-closed 处理——视为『已请求停止』并留痕，
    与主链（tools.py _stream_collect）的异常即停哲学对齐；原 except pass 会带着
    不可读的停止信号继续操作（fail-open）。"""
    try:
        if should_stop and callable(should_stop) and should_stop():
            return True
        if stop_event and stop_event.is_set():
            return True
    except Exception as _e:
        logger.warning("停止信号读取异常，按已停止处理（fail-closed）: %r", _e)
        return True
    return False


def _cap_lines(lines, max_lines=MAX_CONTROL_TREE_LINES):
    """控件树/文本列表硬上限截断：超过则截断并附提示。返回 (capped_lines, truncated, total)。"""
    if not lines or len(lines) <= max_lines:
        return lines, False, len(lines)
    capped = lines[:max_lines]
    capped.append(
        f"…[控件树过大已截断：共 {len(lines)} 行，仅显示前 {max_lines} 行]")
    return capped, True, len(lines)


def _taskkill_ok(result):
    """taskkill 的 /IM、/FI 在『未匹配到进程』时仍可能返回 returncode 0（尤其 /FI 打印
    "INFO: No tasks running…"），必须以输出含成功标记判定真实成功，否则会误报已终止。
    P2-1 修：中文 Windows 成功输出为「成功: 已终止进程…」不含 SUCCESS——
    两个语言的成功标记都要认，否则中文系统上杀掉了进程却回报『未找到』。"""
    if result is None:
        return False
    out = (result.stdout or "") + (result.stderr or "")
    up = out.upper()
    return ("SUCCESS" in up) or ("成功" in out) or ("已终止" in out)

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
                        "description": "进程名（如 notepad.exe）、窗口标题（如 无标题 - 记事本）或数字 PID。数字按 PID 精确终止；进程名/标题依次尝试 taskkill /IM、窗口标题模糊匹配、WMI 兜底。",
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
import shlex
import subprocess
import time

from datetime import datetime
from pathlib import Path


def _split_app_args(s):
    """审计修复 C1：把参数字符串按 Windows 习惯切成 argv 列表（posix=False 保留
    反斜杠路径，再剥成对外层引号）。供 shell=False 的进程启动使用，消除 cmd.exe 注入面。"""
    if not s or not str(s).strip():
        return []
    try:
        toks = shlex.split(str(s), posix=False)
    except ValueError:
        toks = str(s).split()
    out = []
    for t in toks:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ("\"", "'"):
            t = t[1:-1]
        out.append(t)
    return out


def _grab_window_image(qapp, x, y, cw, ch):
    """审计修复 C4（同款收口）：窗口截图统一走 GUI 线程 + 负坐标换算 + 转 QImage。

    QPixmap 属 GUI 线程专属；左侧副屏全局坐标为负，max(0,·) 钳位会截错区域。
    返回 QImage（可跨线程安全传递），失败返回 None。
    """
    import threading

    def _screen_at_local(app, px, py):
        scr = app.primaryScreen()
        try:
            for s in (app.screens() or []):
                if s.geometry().contains(px, py):
                    scr = s
                    break
        except Exception:
            pass
        return scr, px - scr.geometry().x(), py - scr.geometry().y()

    box = {}

    def _do():
        scr, lx, ly = _screen_at_local(qapp, x, y)
        box["img"] = scr.grabWindow(0, lx, ly, cw, ch).toImage()

    if qapp.thread().isCurrentThread():
        try:
            _do()
        except Exception:
            return None
    else:
        from PySide6.QtCore import QObject, Signal

        class _Call(QObject):
            go = Signal()
        holder = _Call()
        done = threading.Event()

        def _slot():
            try:
                _do()
            except Exception:
                pass
            finally:
                done.set()

        holder.go.connect(_slot)
        holder.moveToThread(qapp.thread())
        holder.go.emit()
        if not done.wait(15):
            return None
    return box.get("img")


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


MAX_CONTROL_CANDIDATES = 20


def _list_candidates(window, control_type, limit=MAX_CONTROL_CANDIDATES):
    """枚举窗口内某类控件的**可读名字**（去重、保序、带上限）。A 批 G3 用。

    只负责「把候选报出来」，**不替调用方选一个** —— 选错控件的代价（误点/误填）
    远高于让模型多问一轮。
    """
    out = []
    try:
        items = window.descendants(control_type=control_type) or []
    except Exception:
        items = []
    for c in items:
        if len(out) >= limit:
            break
        try:
            t = (c.window_text() or "").strip()
        except Exception:
            t = ""
        if t and t not in out:
            out.append(t)
    return out


def _find_control(window, target, control_type=None):
    """
    在 window 中按 target 查找控件。
    查找优先级：automation_id → name → title（模糊）
    **没有第 4 级「按类型随便选一个」** —— 见下方注释。
    返回第一个匹配的 wrapper；全部落空时抛 RuntimeError（带同类候选清单）。
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

    # 4) 只给了 control_type —— **不再自动挑第一个**（A 批 G3，此行原为静默误点根源）。
    #
    # 原实现：`window.child_window(control_type=...)` 返回「第一个同类控件」。
    # 问题：target 名字**一个都没匹配上**时，第一个同类控件极可能不是目标
    # （同一窗口常有多个 Button/Edit），调用方却照点，并以
    # 「已点击 '<实际控件名>'」报**成功** —— 误点静默，且模型看到"成功"就往下走。
    # 改为「只枚举、不选中」：把同类候选名交回给调用方（最终呈现给模型），
    # 让它拿准确的名字重试；宁可失败一次，也不要错点一次。
    if control_type:
        cands = _list_candidates(window, control_type)
        if cands:
            names = "、".join(f"'{c}'" for c in cands)
            _only = (f"（同类控件仅 1 个：{names}；若确认就是它，"
                     f"请把 target 改成该名字重试）" if len(cands) == 1 else "")
            raise RuntimeError(
                f"未找到控件: target='{target}'。按类型 '{control_type}' 找到 "
                f"{len(cands)} 个候选但无一匹配{_only}：{names}")
        raise RuntimeError(
            f"未找到控件: target='{target}'，且窗口内没有 '{control_type}' 类控件。"
            f"可先用 app_list_controls 查看当前窗口有哪些控件。")

    raise RuntimeError(f"未找到控件: target='{target}', control_type='{control_type}'")


def _connect_window(title, timeout=5):
    """连接到已运行的窗口；失败抛出**可诊断**的 RuntimeError（而非裸 pywinauto 内部错）。

    ④-A：原各 tool 直接 `Application().connect(title=...)`，失败时把 pywinauto 的
    底层异常原样丢回，agent 分不清是「应用没开」「标题写错」还是「窗口未就绪」，
    等于静默失败。这里收口成统一 helper，并给出可操作的下一步提示。
    """
    from pywinauto import Application
    try:
        app = Application(backend="uia").connect(title=title, timeout=timeout)
        return app.window(title=title)
    except Exception as e:
        reason = str(e) or type(e).__name__
        raise RuntimeError(
            f"无法连接到窗口『{title}』——应用可能未运行、窗口标题不匹配，"
            f"或窗口尚未就绪。可先用 app_launch 启动该应用，或核对窗口标题。"
            f"（底层原因：{reason}）"
        )


def _escape_type_keys(s):
    """pywinauto 的 type_keys 把 + ^ % ~ ( ) {{ }} [ ] 当修饰键元字符，
    直接传入会发送错误按键。这里逐个转义为 {{x}} 字面量形式。"""
    out = []
    for ch in (s or ""):
        if ch in "+^%~(){}[]":
            out.append("{" + ch + "}")
        else:
            out.append(ch)
    return "".join(out)


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

def tool_app_launch(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
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
            # ②-B.3：先校验目标存在（绝对路径文件 or PATH 可执行），避免静默失败；返回 PID 便于后续管理。
            # 审计修复 C1：原 shell=True 拼接命令行经 cmd.exe 解析，target/args 里的
            # `&`、`|` 等元字符即任意命令注入 → shell=False + 参数列表。
            # （上面 wait_ready 分支的 pywinauto .start() 走 CreateProcess，不经
            #   cmd.exe，元字符只作字面量参数，非注入面，保持原样。）
            import shutil as _shutil
            _resolved = (os.path.isabs(target) and os.path.isfile(target)) or _shutil.which(target)
            if not _resolved and not os.path.isfile(target):
                return (f"启动失败：找不到可执行文件『{target}』（请检查路径或 PATH）", [], None)
            proc = subprocess.Popen([target] + _split_app_args(shell_args),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return (f"已发起启动 {target}（PID={proc.pid}）", [], None)

    except ImportError:
        return ("缺少依赖：pywinauto。请安装：pip install pywinauto", [], None)
    except Exception as e:
        return (f"启动失败：{e}", [], None)


def tool_app_kill(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    target = args["target"]

    # G4 第二步：系统关键进程一律硬拒绝（在任何 spawn 之前）。
    # 判定 + 文案从 system_control_tools 取 —— 单一事实源，别在这里抄一份
    # （抄一份就等着两处漂移）。函数内 import：与本模块其余延迟导入同风格，
    # 也不给模块级加耦合。
    from system_control_tools import _critical_process_deny
    _deny = _critical_process_deny(target)
    if _deny:
        return (_deny, [], None)

    try:
        # 1) 数字 PID 直杀（最精确）
        if target.isdigit():
            r = subprocess.run(["taskkill", "/PID", target, "/F"],
                               capture_output=True, text=True, encoding="gbk", errors="replace")
            if r.returncode == 0:
                return (f"已强制终止 PID={target}", [], None)
            return (f"未找到 PID={target} 的进程（或无权终止）", [], None)

        # 2) 按镜像名（taskkill /IM，自动补 .exe）
        name = target if target.lower().endswith(".exe") else target + ".exe"
        r1 = subprocess.run(["taskkill", "/IM", name, "/F"],
                            capture_output=True, text=True, encoding="gbk", errors="replace")
        if _taskkill_ok(r1):
            return (f"已强制终止 {name}", [], None)

        # 3) 按窗口标题模糊匹配（注意：/FI 未匹配时仍返回 returncode 0，必须看 SUCCESS 标记）
        r2 = subprocess.run(["taskkill", "/F", "/FI", f"WINDOWTITLE eq {target}"],
                            capture_output=True, text=True, encoding="gbk", errors="replace")
        if _taskkill_ok(r2):
            return (f"已强制终止匹配窗口『{target}』的进程", [], None)

        # 4) WMI 兜底（镜像名精确/模糊，覆盖 taskkill /IM 漏掉的变体）
        # P2-4 修：name 会被拼进 PowerShell 的 WQL 字符串字面量（Name='{name}'），
        # 引号/反引号/分号/$ 可注入。Windows 镜像名本身不可能含这些字符——
        # 直接拒绝并引导用 PID，比转义更稳（转义漏一种就是新洞）。
        if any(ch in name for ch in ("'", '"', "`", ";", "$", "\n", "\r")):
            return (f"进程名『{name}』含引号/分号等非法字符，已拒绝 WMI 查询（防注入）；请改用 PID 精确终止", [], None)
        try:
            ps = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-CimInstance Win32_Process -Filter \"Name='{name}'\" | "
                 f"Select-Object -ExpandProperty ProcessId"],
                capture_output=True, text=True, encoding="gbk", errors="replace", timeout=15)
            pids = [p.strip() for p in (ps.stdout or "").splitlines() if p.strip().isdigit()]
            if pids:
                killed = []
                for pid in pids:
                    kr = subprocess.run(["taskkill", "/PID", pid, "/F"],
                                       capture_output=True, text=True,
                                       encoding="gbk", errors="replace")
                    if kr.returncode == 0:
                        killed.append(pid)
                if killed:
                    return (f"已通过 WMI 兜底强制终止 {name}（PID={'/'.join(killed)}）", [], None)
        except Exception:
            pass

        return (f"未找到匹配的进程：『{target}』（请确认进程名/窗口标题，或用 PID 精确终止）", [], None)
    except Exception as e:
        return (f"终止失败：{e}", [], None)


# ---------- 窗口状态 ----------

def tool_app_focus(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    title = args["title"]
    try:
        from pywinauto import Application
        w = _connect_window(title)
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


def tool_app_window_state(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    title = args["title"]
    action = args["action"]
    try:
        from pywinauto import Application
        w = _connect_window(title)

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

def tool_app_list_controls(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    title = args["title"]
    filter_type = args.get("filter_type")
    max_depth = args.get("max_depth", 4)

    try:
        from pywinauto import Application
        w = _connect_window(title)

        lines = _print_control_tree(w, max_depth=max_depth)

        if filter_type:
            lines = [l for l in lines if f"[{filter_type}]" in l]

        # ②-B.1：控件树硬上限截断，防极长控件树撑爆 LLM 上下文
        lines, _trunc, _total = _cap_lines(lines)

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

def tool_app_click(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    title = args.get("title")
    target = args["target"]
    control_type = args.get("control_type")
    button = args.get("button", "left")

    try:
        from pywinauto import Application

        if title:
            w = _connect_window(title)
        else:
            # 无 title：取前台窗口（②-B.2 补 timeout，防无活动窗口时 connect 挂死）
            app = Application(backend="uia").connect(active_only=True, timeout=5)
            w = app.top_window()

        try:
            ctrl = _find_control(w, target, control_type)
        except RuntimeError as _re:
            return (f"点击失败：{_re}", [], None)

        if button == "right":
            ctrl.click_input(button="right")
        else:
            ctrl.click()

        return (f"已点击 '{ctrl.window_text() or target}' in '{w.window_text()}'", [], None)

    except ImportError:
        return ("缺少依赖：pywinauto", [], None)
    except Exception as e:
        return (f"点击失败：{e}", [], None)


def tool_app_type(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    title = args["title"]
    text = args["text"]
    target = args.get("target")
    append = args.get("append", False)

    try:
        w = _connect_window(title)

        # 定位输入框
        if target:
            try:
                ctrl = _find_control(w, target, control_type="Edit")
            except RuntimeError as _re:
                return (f"输入失败：{_re}", [], None)
            except Exception:
                # 如果 target 不是 Edit 本身，尝试找它旁边的 label
                try:
                    ctrl = w.child_window(title=target)
                    ctrl = ctrl.parent().child_window(control_type="Edit")
                except Exception as _e2:
                    return (f"输入失败：未定位到输入框——{_e2}", [], None)
        else:
            try:
                ctrl = w.child_window(control_type="Edit")
            except Exception as _e3:
                return (f"输入失败：窗口中未找到输入框（可能不是可编辑界面）——{_e3}", [], None)

        ctrl.set_focus()
        if append:
            # 追加：读现有内容拼接后整体写入。set_edit_text 按字面量处理，
            # + ^ % {} 等字符均安全；失败再退回（已转义的）type_keys 保底。
            try:
                current = ctrl.window_text() or ""
                ctrl.set_edit_text(current + text)
            except Exception:
                ctrl.type_keys("{END}")
                ctrl.type_keys(_escape_type_keys(text), with_spaces=True)
        else:
            # 替换：set_edit_text 直接字面量写入，彻底规避 type_keys 把 + ^ % ~ ( ) { }
            # 当修饰键的转义坑（旧实现输 "C++" 会变成 "CB"）。
            try:
                ctrl.set_edit_text(text)
            except Exception:
                ctrl.type_keys(_escape_type_keys(text), with_spaces=True)

        return (f"已输入 {len(text)} 个字符到 '{w.window_text()}'", [], None)

    except ImportError:
        return ("缺少依赖：pywinauto", [], None)
    except Exception as e:
        return (f"输入失败：{e}", [], None)


def tool_app_get_text(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    title = args["title"]
    target = args.get("target")

    try:
        from pywinauto import Application
        w = _connect_window(title)

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

def tool_app_wait_for(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    title = args["title"]
    target = args["target"]
    exists = args.get("exists", True)
    timeout = args.get("timeout", 10)

    try:
        from pywinauto import Application
        w = _connect_window(title)

        # P2-3 修：原实现 ctrl.wait() 是单次阻塞调用（C 层），中途无法响应停止。
        # 改为 0.2s 粒度轮询循环——停止信号在 0.5s 内可见，语义与原版一致。
        try:
            ctrl = w.child_window(title=target)
            deadline = time.time() + max(1, int(timeout))
            if exists:
                while time.time() < deadline:
                    if _aborted(should_stop, stop_event):
                        return ("⏹ 已停止（用户请求）", [], None)
                    try:
                        if ctrl.exists(timeout=0.2, retry_interval=0.2):
                            return (f"控件 '{target}' 已出现（{timeout}s 内）", [], None)
                    except Exception:
                        pass
                    time.sleep(0.2)
                return (f"控件 '{target}' 在 {timeout}s 内未出现", [], None)
            else:
                while time.time() < deadline:
                    if _aborted(should_stop, stop_event):
                        return ("⏹ 已停止（用户请求）", [], None)
                    try:
                        if not ctrl.exists(timeout=0.2, retry_interval=0.2):
                            return (f"控件 '{target}' 已消失（{timeout}s 内）", [], None)
                    except Exception:
                        pass
                    time.sleep(0.2)
                return (f"控件 '{target}' 在 {timeout}s 内未消失", [], None)

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

def tool_app_screenshot(cfg, app_dir, args, progress=None, stop_event=None, should_stop=None):
    if _aborted(should_stop, stop_event):
        return ("⏹ 已停止（用户请求）", [], None)
    title = args["title"]
    save_path = args.get("save_path")
    if not save_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # v4.162.x：默认落到统一产物目录的「截图」子目录，而非程序目录 app_dir/output
        try:
            from config import PRODUCTS_DIR
            base = os.path.join(PRODUCTS_DIR, "截图")
        except Exception:
            base = os.path.join(app_dir, "output")
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        save_path = os.path.join(base, f"app_screenshot_{ts}.png")

    try:
        # 先取 pywinauto 窗口坐标
        from pywinauto import Application
        w = _connect_window(title)
        rect = w.rectangle()

        # 用 QScreen 截图
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance()
        if not qapp:
            return ("Qt Application 未初始化", [], None)

        # 审计修复 C4：原 max(0,·) 钳位丢负坐标 + 工作线程直接用 QPixmap（跨线程未定义）
        img = _grab_window_image(qapp, rect.left, rect.top, rect.width(), rect.height())
        if img is None or img.isNull():
            return ("窗口截图失败：未取到像素（GUI 线程无响应 / 坐标越界？）", [], None)
        w_px, h_px = rect.width(), rect.height()

        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        img.save(save_path, "PNG")
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
