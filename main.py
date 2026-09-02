# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 v3 — 主入口"""

import sys
import os
import logging
import traceback
import ctypes
from datetime import datetime

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread, Signal, QObject, QAbstractNativeEventFilter, Qt
from ui import ChatWindow, TrayApp, THEME

log = logging.getLogger("dsdesktop")

# 性能基线：顶部只依赖标准库（perf_baseline 内部才 import PySide6/业务模块），早期可安全 import
import perf_baseline

# 崩溃日志目录（与记忆同级，便于排查）——未捕获异常写这里，崩了能查不静默
LOG_DIR = os.path.join(os.path.expanduser("~/Documents/AgentDesktop"), "logs")


def _install_crash_logger():
    """安装全局未捕获异常兜底：主线程 + 工作线程异常都落盘到 logs/app.log。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        return

    def _dump(et, ev, tb):
        try:
            with open(os.path.join(LOG_DIR, "app.log"), "a", encoding="utf-8") as f:
                f.write("\n=== 未捕获异常 %s ===\n" % datetime.now().isoformat())
                f.write("".join(traceback.format_exception(et, ev, tb)))
                f.write("\n")
        except Exception:
            pass

    sys.excepthook = _dump
    try:
        import threading
        threading.excepthook = lambda args: _dump(args.exc_type, args.exc_value, args.exc_traceback)
    except Exception:
        pass


class ObsidianInitWorker(QThread):
    """后台异步初始化 Obsidian 索引，避免阻塞冷启动。

    配合 config.init_obsidian 的 timeout 护栏与 obsidian_enabled 开关，
    实现「异步 + 短超时 + 可跳过」三件套。主线程只 dispatch，UI 先出来。
    """
    finished = Signal(object)  # 回传结果字符串

    def __init__(self, cfg, store, timeout):
        super().__init__()
        self.cfg = cfg
        self.store = store
        self.timeout = timeout

    def run(self):
        try:
            import config  # 本模块顶层未 import config，缺这行会 NameError（沉默吞掉知识库索引）
            result = config.init_obsidian(self.cfg, self.store, timeout=self.timeout)
        except Exception as e:
            result = "Obsidian 初始化异常: %s" % e
            log.warning(result)
        self.finished.emit(result)


# ---- v4.79：全局唤起快捷键（系统级 Ctrl+Alt+X，唤起/隐藏窗口）----
class GlobalHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hwnd, callback):
        super().__init__()
        self._hwnd = hwnd
        self._cb = callback

    def nativeEventFilter(self, eventType, message):
        # v4.108 M-19：eventType 是 QByteArray，须用 bytes 比较（str 比较恒 False，
        # 热键 Ctrl+Alt+X 静默失效）。与 ui.py:1222 写法保持一致。
        if eventType == b"windows_generic_MSG":
            try:
                class MSG(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", ctypes.c_void_p),
                        ("message", ctypes.c_ulong),
                        ("wParam", ctypes.c_ulong),
                        ("lParam", ctypes.c_ulong),
                        ("time", ctypes.c_ulong),
                        ("pt", ctypes.c_ulong * 2),
                    ]
                msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
                if msg.message == 0x0312:  # WM_HOTKEY
                    self._cb()
            except Exception:
                pass
        return False, 0


class UiBridge(QObject):
    """跨线程投递桥（v4.108 H-08 修复）：HTTP/工作线程 emit，GUI 线程执行。

    背景：browser_bridge 的 HTTP server 线程回调里直接 QTimer.singleShot(0, fn)
    不触发（QTimer 依赖创建线程的事件循环）。标准做法是 signal 跨线程 queued 投递：
    本对象在 GUI 线程创建并 connect，任意线程 post() 都会排到 GUI 事件循环执行。
    """

    _go = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._go.connect(self._run)

    def post(self, fn):
        """任意线程调用：把 fn 排到 GUI 线程执行。"""
        try:
            self._go.emit(fn)
        except Exception:
            logging.getLogger("dsdesktop").warning("UI 桥投递异常", exc_info=True)

    def _run(self, fn):
        try:
            fn()
        except Exception:
            logging.getLogger("dsdesktop").warning("UI 桥执行异常", exc_info=True)


def _register_global_hotkey(app, window, hotkey_id=1):
    """注册系统级热键 Ctrl+Alt+X，随时唤起/隐藏窗口。失败优雅降级。"""
    try:
        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        user32.RegisterHotKey.restype = ctypes.c_bool
        user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.UnregisterHotKey.restype = ctypes.c_bool

        MOD_CONTROL = 0x0002
        MOD_ALT = 0x0001
        MOD_NOREPEAT = 0x4000
        VK_X = 0x58
        hwnd = int(window.winId())
        if not user32.RegisterHotKey(hwnd, hotkey_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_X):
            log.warning("全局热键 Ctrl+Alt+X 注册失败（可能被其他程序占用），跳过")
            return None

        def _toggle():
            try:
                if window.isVisible() and window.isActiveWindow():
                    window.hide()
                else:
                    window.show()
                    window.raise_()
                    window.activateWindow()
            except Exception as e:
                log.warning("热键唤起窗口失败: %s", e)

        flt = GlobalHotkeyFilter(hwnd, _toggle)
        app.installNativeEventFilter(flt)
        app._global_hotkey_filter = flt  # 保活，避免被 GC
        log.info("全局热键已注册：Ctrl+Alt+X（唤起/隐藏窗口）")
        return flt
    except Exception as e:
        log.warning("全局热键初始化失败（不影响主程序）: %s", e)
        return None


def _launch_gateway_on_startup(cfg, app):
    """v4.79：若启用且 8000 端口未占用，随 APP 拉起 free-api-gateway（识图后端）。

    网关是独立 Python(uvicorn) 项目，路径由 gateway_dir 配置（默认发现值）。
    全程非阻塞、失败静默，绝不拖累主程序启动。
    """
    import subprocess, shutil, socket, threading, time

    if not cfg.get("gateway_autostart", True):
        return
    gw_dir = cfg.get("gateway_dir", "")
    if not gw_dir or not os.path.isdir(gw_dir):
        log.info("识图后端目录不存在，跳过自启: %r", gw_dir)
        return
    # 端口已占用 → 视为已在运行（用户手动拉起或上次残留），不重复拉起
    try:
        s = socket.socket(); s.settimeout(1)
        s.connect(("127.0.0.1", 8000)); s.close()
        log.info("识图后端(8000)已在运行，跳过自启")
        return
    except Exception:
        pass
    py = shutil.which("python") or shutil.which("python3")
    if not py:
        log.warning("未找到 python，无法自启识图后端")
        return

    def _run():
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=gw_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags)
        app._gateway_proc = proc
        log.info("识图后端启动中（PID %s，目录 %s）", proc.pid, gw_dir)
        # 等待就绪；若进程很快退出（多半缺依赖），补装依赖后重试一次
        ready = False
        for _ in range(15):
            time.sleep(1)
            try:
                s = socket.socket(); s.settimeout(1)
                s.connect(("127.0.0.1", 8000)); s.close()
                ready = True
                break
            except Exception:
                if proc.poll() is not None:
                    break
        if not ready:
            try:
                subprocess.run([py, "-m", "pip", "install", "-r", "requirements.txt"],
                               cwd=gw_dir, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=240)
            except Exception:
                pass
            try:
                proc2 = subprocess.Popen(
                    [py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                    cwd=gw_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=flags)
                app._gateway_proc = proc2
                log.info("识图后端重试启动（PID %s）", proc2.pid)
            except Exception as e:
                log.warning("识图后端启动失败: %s", e)

    threading.Thread(target=_run, daemon=True).start()


def main():
    import config
    from config import load_config

    cfg = load_config()
    perf_baseline.mark("config_loaded")

    # v4.74：先装崩溃兜底（任何后续初始化失败都能留痕），再做记忆自愈
    _install_crash_logger()
    perf_baseline.mark("crash_logger")

    # 确保产物目录存在（统一产物落点：~/Documents/AgentDesktop/产物）
    try:
        os.makedirs(config.PRODUCTS_DIR, exist_ok=True)
    except Exception as e:
        log.warning("创建产物目录失败: %s", e)
    perf_baseline.mark("products_dir")

    # v4.74：启动即自检记忆文件（缺失/畸形从备份恢复），必须先于记忆层任何读写
    try:
        import memory_store
        _heal = memory_store.repair_memory()
        if _heal != "healthy":
            log.warning("记忆自愈：%s", _heal)
    except Exception as e:
        log.warning("记忆自愈检查失败（不影响启动）: %s", e)
    perf_baseline.mark("memory_heal")

    # 初始化 MCP 客户端（启动时遍历 mcp_servers 配置）
    config.init_mcp_clients(cfg)
    log.info("MCP 初始化完成，已连接 %d 个服务器", len(config.mcp_clients))
    perf_baseline.mark("mcp")

    # 初始化 RAG 知识库
    config.init_rag(cfg)
    log.info("RAG 知识库初始化完成")
    perf_baseline.mark("rag")

    # 初始化 Obsidian 集成：异步 + 超时 + 可跳过（不阻塞冷启动）
    # 旧逻辑同步遍历整个仓库逐个 index_file，曾占冷启动 20s+；
    # 现改为后台线程跑，主路径只 dispatch（mark 近似 0），UI 先出来。
    obsidian_worker = None
    if cfg.get("obsidian_enabled", True):
        obsidian_worker = ObsidianInitWorker(cfg, config.rag_store, timeout=15.0)
        obsidian_worker.finished.connect(
            lambda r: log.info("Obsidian(异步完成): %s", r)
        )
        obsidian_worker.start()
        log.info("Obsidian 初始化已在后台启动（超时 15s，不阻塞界面）")
    else:
        log.info("Obsidian 已禁用（obsidian_enabled=false），跳过初始化")
    perf_baseline.mark("obsidian")

    # v4.104：QtWebEngine 硬性要求——必须在 QApplication 实例化之前设置，
    # 否则 Chromium 渲染进程共享上下文创建失败（聊天区白屏）。
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    # v4.105：导演台预览区 localres:// scheme——必须在 QApplication 前注册（Qt 硬性要求），
    # 否则自定义 scheme 不生效，分镜/关键帧/合成预览的图片/视频无法加载（被 CORS 拦）。
    try:
        from director_web import register_localres_scheme
        register_localres_scheme()
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    perf_baseline.mark("qapp")

    # 保活后台 Obsidian worker（局部变量可能被 GC 导致线程被腰斩）
    if obsidian_worker is not None:
        app.obsidian_worker = obsidian_worker

    window = ChatWindow(cfg)
    window.show()
    perf_baseline.mark("window_shown")

    tray = TrayApp(app, window, cfg)
    window.tray_app = tray  # 打通剪贴板通知到托盘
    perf_baseline.mark("tray")

    # v4.79：首次启动新手引导（看过则不再弹；全 try 包裹不影响启动）
    try:
        if not cfg.get("onboarded", False):
            from onboarding import OnboardingWizard
            dlg = OnboardingWizard(cfg, THEME, parent=window)
            dlg.exec()
            window.raise_()
            window.activateWindow()
    except Exception as e:
        log.warning("新手引导显示失败（不影响启动）: %s", e)

    # v4.79：全局唤起快捷键 Ctrl+Alt+X（唤起/隐藏窗口），失败优雅降级
    _register_global_hotkey(app, window)
    perf_baseline.mark("hotkey")

    # Webhook 服务
    try:
        from webhook_server import get_webhook_server, set_event_callback

        def _wh_cb(kind, payload):
            try:
                if getattr(window, "tray_app", None):
                    from PySide6.QtGui import QSystemTrayIcon
                    window.tray_app.tray.showMessage(
                        "AgentDesktop · Webhook", f"收到 {kind} 事件",
                        QSystemTrayIcon.Information, 4000)
            except Exception:
                pass

        set_event_callback(_wh_cb)

        if cfg.get("webhook_enabled", False):
            # v4.108 M-28：无 token 时自动生成并持久化（存量 config 无该字段）
            if not cfg.get("webhook_token"):
                import uuid
                cfg["webhook_token"] = uuid.uuid4().hex[:16]
                try:
                    from config import save_config
                    save_config(cfg)
                except Exception:
                    pass
            srv = get_webhook_server(cfg)
            ok = srv.start()
            if ok is True:
                log.info("Webhook 服务器已自动启动（端口 %s）", cfg.get("webhook_port", 9000))
            else:
                log.warning("Webhook 自动启动失败: %s", ok)
    except Exception as e:
        log.warning("Webhook 集成失败（不影响主程序）: %s", e)

    # 浏览器扩展桥接服务（v4.103：抓网页进对话）
    # 仅本机 127.0.0.1:9100，带 token 校验，安全无外部暴露。
    try:
        from browser_bridge import (browser_bridge_start, browser_bridge_stop,
                                    set_event_callback as _bridge_set_cb)
        _ui_bridge = UiBridge()  # GUI 线程创建；HTTP 线程 post() 排到 GUI 执行

        def _bridge_cb(kind, payload):
            if kind != "browser_page":
                return
            try:
                def _inject():
                    try:
                        p = payload
                        title = p.get("title", "")
                        url = p.get("url", "")
                        text = p.get("text", "")
                        sel = p.get("selection", "")
                        note = p.get("note", "")
                        body = sel if sel else text
                        if not body:
                            return
                        block = "请帮我处理这个网页内容"
                        if note:
                            block += "（要求：" + note + "）"
                        block += "\n\n"
                        block += "标题：" + title + "\n链接：" + url + "\n\n"
                        block += "---\n" + body + "\n---"
                        window.input_box.setPlainText(block)
                        window.input_box.setFocus()
                        if getattr(window, "tray_app", None):
                            from PySide6.QtGui import QSystemTrayIcon
                            window.tray_app.tray.showMessage(
                                "AgentDesktop · 浏览器扩展",
                                "已收到网页：" + (title[:30] or "（无标题）")
                                + "（按 Enter 让 AI 处理）",
                                QSystemTrayIcon.Information, 4000)
                    except Exception as e:
                        log.warning("浏览器扩展注入失败: %s", e)

                # 跨线程（HTTP server 线程）→ 主线程执行 UI 操作（H-08：信号 queued 投递，
                # 替代 QTimer.singleShot——QTimer 依赖创建线程事件循环，HTTP 线程里不触发）
                _ui_bridge.post(_inject)
            except Exception as e:
                log.warning("浏览器扩展回调异常: %s", e)

        _bridge_set_cb(_bridge_cb)
        tok = browser_bridge_start(cfg)
        if isinstance(tok, str) and tok:
            cfg["browser_bridge_token"] = tok
            try:
                from config import save_config
                save_config(cfg)
            except Exception:
                pass
            log.info("浏览器扩展桥接已启动（配对码已生成并持久化）")
        else:
            log.warning("浏览器扩展桥接启动失败: %s", tok)
    except Exception as e:
        log.warning("浏览器扩展桥接集成失败（不影响主程序）: %s", e)

    # 识图后端（free-api-gateway）随 APP 自动拉起（联网识图不再需手动启动）
    try:
        _launch_gateway_on_startup(cfg, app)
    except Exception as e:
        log.warning("识图后端自启失败（不影响主程序）: %s", e)

    # 技能管理器（Ctrl+Shift+S 呼出）
    try:
        from PySide6.QtGui import QShortcut
        from PySide6.QtGui import QKeySequence
        from skill_manager_ui import open_skill_manager

        sc = QShortcut(QKeySequence("Ctrl+Alt+S"), window)
        sc.activated.connect(lambda: open_skill_manager(cfg))
        log.info("技能管理器快捷键已注册：Ctrl+Alt+S（Ctrl+Shift+S 在中文 Windows 与输入法切换冲突）")

        from workflow_manager_ui import open_workflow_manager
        scw = QShortcut(QKeySequence("Ctrl+Alt+W"), window)
        scw.activated.connect(lambda: open_workflow_manager(cfg, window))
        log.info("工作流模板快捷键已注册：Ctrl+Alt+W")
    except Exception as e:
        log.warning("技能管理器快捷键注册失败（不影响主程序）: %s", e)

    # 性能基线：启动埋点收尾（全 try 包裹，失败不影响启动）
    perf_baseline.mark("ready")
    try:
        perf_baseline.finalize_startup(extra={
            "version": getattr(config, "APP_VERSION", ""),
            "frozen": bool(getattr(sys, "frozen", False)),
        })
    except Exception:
        pass

    # 退出时关闭 Webhook 服务 / 识图后端
    def _on_quit():
        try:
            from webhook_server import webhook_stop
            webhook_stop()
        except Exception:
            pass
        try:
            gp = getattr(app, "_gateway_proc", None)
            if gp is not None and gp.poll() is None:
                gp.terminate()
        except Exception:
            pass
        # 导演台任务存盘（关程序后可在下次继续，不必从头来）
        try:
            from director_panel import _save_session
            _save_session(window)
        except Exception:
            pass

    app.aboutToQuit.connect(_on_quit)

    ret = app.exec()

    # 程序退出前关闭所有 MCP 客户端
    config.shutdown_mcp()
    log.info("MCP 已全部关闭")

    return ret


def run_autobackup():
    """v4.76：OS 级自动备份入口（由 Windows 任务计划程序调用，无 GUI）。

    将用户数据目录（~/Documents/AgentDesktop）整体复制到带时间戳的备份子目录，
    排除体积庞大的「产物」目录，并保留最近 14 份。结果写入 logs/backup.log。
    返回 (ok: bool, msg: str)。
    """
    import shutil
    from datetime import datetime

    data_dir = os.path.join(os.path.expanduser("~/Documents"), "AgentDesktop")
    if not os.path.isdir(data_dir):
        return False, f"数据目录不存在：{data_dir}"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = os.path.join(data_dir, "backups")
    dest = os.path.join(backup_root, ts)
    os.makedirs(backup_root, exist_ok=True)
    log_dir = os.path.join(data_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "backup.log")

    def _log(line):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} {line}\n")
        except Exception:
            pass

    try:
        # 排除「产物」（可能含大体积图片/视频），其余全量复制
        def _ignore(dirname, names):
            return {"产物"} if os.path.abspath(dirname) == os.path.abspath(data_dir) else set()

        shutil.copytree(data_dir, dest, ignore=_ignore)
        # 保留最近 14 份
        subs = sorted(
            (d for d in os.listdir(backup_root)
             if os.path.isdir(os.path.join(backup_root, d)) and d != ts),
            reverse=True,
        )
        for old in subs[13:]:
            try:
                shutil.rmtree(os.path.join(backup_root, old))
            except Exception:
                pass
        msg = f"备份完成：{dest}（保留最近 {min(len(subs) + 1, 14)} 份）"
        _log("OK " + msg)
        print(msg)
        return True, msg
    except Exception as e:
        msg = f"备份失败：{e}"
        _log("ERR " + msg)
        print(msg, file=sys.stderr)
        return False, msg


if __name__ == "__main__":
    if "--autobackup" in sys.argv:
        ok, msg = run_autobackup()
        sys.exit(0 if ok else 1)
    if "--perf" in sys.argv:
        # 性能基线无 GUI 入口：offscreen 跑基准并输出报告后退出
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        perf_baseline.main_cli()
        sys.exit(0)
    sys.exit(main())
