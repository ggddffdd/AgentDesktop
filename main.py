# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 v3 — 主入口"""

import sys
import os
import logging
import traceback
import ctypes
from datetime import datetime

# v4.119：WebEngine 禁 GPU（治本）——必须在 import PySide6 之前设置，晚了不生效。
# Windows 上 GPU 硬件加速撞显卡驱动/HDR/高 DPI 缩放 → Chromium renderer 周期性崩溃
# （约 1 分钟一次），v4.118 的 renderProcessTerminated 自愈只是崩后重建（每分钟闪一次）。
# 禁 GPU 后走软件光栅化，聊天/Markdown 渲染无感知差异。setdefault 不覆盖用户已设值。
# v4.120 修正：去掉 --disable-software-rasterizer——它和 --disable-gpu 叠加后
# SwiftShader 软件 GL 也被禁，Qt AA_ShareOpenGLContexts 拿不到任何 GL 上下文
# （kFatalFailure: Failed to create shared context）→ renderer 启动即被 Killed、
# 崩溃-重建死循环（诊断日志实锤）。保留 SwiftShader 兜底，禁 GPU 才安全。
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-gpu-compositing --disable-dev-shm-usage",
)
# v4.134.8：**别再试 flags 压 QtWebEngine 那条日志了** —— 2026-09-11 在冻结版上
# 做了五组对照实测，无论加什么，每次启动**照写 12 行**：
#   基准(无)         +12 行
#   --log-level=2    +12 行
#   --log-level=3    +12 行
#   --disable-logging +12 行   ← 连"完全关闭"都无效
#   --log-level=2 --enable-logging  +12 行
# 原因：这条（`web_engine_library_info.cpp:63` 的「--webengine-resources-path /
# --webengine-locales-path not passed to renderer process」）是 QtWebEngine 桥接到
# **Qt 消息系统**再落到包目录 debug.log 的，**不走 Chromium 的 --log-level 过滤**。
# （参数本身没毛病：开发机实验里 --log-level=3 把 Chromium 原生 ERROR 从 6 条压到 0 条，
#  所以「档位选 2 还是 3」这个问题在冻结版上是**伪问题** —— 它根本管不着这条。）
# ✅ 唯一有效手段 = **启动时由我们代它滚动**：config.cap_qtwebengine_log()。
#    已实测：人工灌到 1,241,423 字节 → 启动后滚成 debug.log.1，新 debug.log 从 0 开始
#    （2,100 字节 / 12 行）。见 LEARNINGS L055。
# 注：本版（v4.134.8）已把 v4.134.7 误加的 `--log-level=2` 撤掉 —— 它净效果为零，
# 留着只会让人误以为"已经压住了"。真正干活的是下面那个代滚动调用。
# v4.121.6：关 Chromium 沙箱——必须在 import PySide6 之前设置。
# 实测（2026-09-07 冻结 exe）：不带开关时 QtWebEngineProcess 启动即被 Killed
# （exit_code=1），聊天区 renderProcessTerminated 崩溃-重建死循环（日志 9 次/60s）；
# 设了之后同机立刻拉起 6 个渲染进程、页面正常。这是 Qt 官方支持的开关，
# 本地桌面应用不加载外部不可信页面，关闭沙箱无实际安全损失。
# setdefault：用户若已显式设置则尊重其值。
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

# v4.188 P2-14：**早期**崩溃兜底——赶在 import PySide6 / ui 之前装上。
# 原实现的 crash logger 在 main():_install_crash_logger() 才装，而顶层
# `from PySide6...` / `from ui import ...` 若本身崩（DLL 损坏 / 杀软删文件 /
# Qt 插件缺失 / 版本冲突），窗口版（pythonw 无控制台）stderr 不可见 →
# 双击 exe「毫无反应」且零日志，用户和我们都无从排查。
# 这里只用标准库（os/sys/datetime/traceback，上面已 import），把 import 阶段
# 的崩溃也写进 logs/app.log；main() 里的完整版 hook（走滚动入口）稍后覆盖。
# 注：裸 open 追加只发生在「进程即将死掉」的场景，每崩溃一条 traceback，
# 不会像常驻日志那样无限增长，无滚动也安全。
try:
    _EARLY_LOG_DIR = os.path.join(
        os.path.expanduser("~/Documents/小臭玩AI"), "logs")

    def _early_dump(et, ev, tb):
        try:
            os.makedirs(_EARLY_LOG_DIR, exist_ok=True)
            with open(os.path.join(_EARLY_LOG_DIR, "app.log"), "a",
                      encoding="utf-8") as _f:
                _f.write("\n=== 启动早期未捕获异常 %s ===\n%s\n"
                         % (datetime.now().isoformat(),
                            "".join(traceback.format_exception(et, ev, tb))))
        except Exception:
            pass

    sys.excepthook = _early_dump
except Exception:
    pass

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread, Signal, QObject, QAbstractNativeEventFilter, Qt
from ui import ChatWindow, TrayApp, THEME

log = logging.getLogger("dsdesktop")

# v4.134.8：给 QtWebEngine 自己那份 debug.log 套上限 —— **这是唯一有效的手段**。
# （上面那五组 flags 实测全部无效，详见那段注释。）
# 只有冻结版才有该文件；开发机返回 False 不做任何事。异常全吞，绝不影响启动。
# 实测有效性：灌到 1,241,423 字节 → 启动后滚成 debug.log.1，新文件从 0 开始。
try:
    import config as _cfg
    if _cfg.cap_qtwebengine_log():
        log.info("QtWebEngine debug.log 已滚动归档")
except Exception:
    pass

# 性能基线：顶部只依赖标准库（perf_baseline 内部才 import PySide6/业务模块），早期可安全 import
import perf_baseline

# 可观测性：统一任务状态总线（纯标准库）。桥接回调在 HTTP 线程，
# 本模块自身线程安全；界面侧 TaskStatusStrip 用 Qt queued 信号渲染。
import task_status

# 崩溃日志目录（与记忆同级，便于排查）——未捕获异常写这里，崩了能查不静默
LOG_DIR = os.path.join(os.path.expanduser("~/Documents/小臭玩AI"), "logs")


def _install_crash_logger():
    """安装全局未捕获异常兜底：主线程 + 工作线程异常都落盘到 logs/app.log。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        return

    def _dump(et, ev, tb):
        try:
            # v4.134.6：改走公共滚动入口（此前裸 open 追加，实测已 706 KB 且无上限）。
            # 崩溃兜底自己就可能被反复触发，一旦无限增长会在磁盘紧张时雪上加霜。
            from config import append_log_line
            txt = ("\n=== 未捕获异常 %s ===\n" % datetime.now().isoformat()
                   + "".join(traceback.format_exception(et, ev, tb)) + "\n")
            append_log_line(os.path.join(LOG_DIR, "app.log"), txt)
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

    v4.122.1：新增 delay 参数（秒）——延迟到启动空闲后再跑，错开启动期网络；
    默认 0（立即），由 config 的 obsidian_index_delay_sec 控制。
    """
    finished = Signal(object)  # 回传结果字符串

    def __init__(self, cfg, store, timeout, delay=0):
        super().__init__()
        self.cfg = cfg
        self.store = store
        self.timeout = timeout
        self.delay = delay

    def run(self):
        try:
            if self.delay > 0:
                import time
                time.sleep(self.delay)
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
                    # v4.125 P2：wParam/lParam 在 64 位 Windows 是 UINT_PTR/
                    # LONG_PTR（8 字节），此前 c_ulong(4 字节) 字段错位——
                    # 当前只读 message（偏移在 wParam 之前）碰巧正常，但
                    # 一旦读 wParam/lParam（如热键 id、鼠标坐标）必错。
                    _fields_ = [
                        ("hwnd", ctypes.c_void_p),
                        ("message", ctypes.c_ulong),
                        ("wParam", ctypes.c_size_t),
                        ("lParam", ctypes.c_ssize_t),
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
    # v4.188 P3：加 py launcher 兜底——打包版用户机常没有 "python" 在 PATH
    # （只装了官方安装器默认勾选的 py launcher），原实现直接静默放弃。
    # `py -m uvicorn ...` 与 `python -m uvicorn ...` 命令形态相同，下游零改动。
    py = shutil.which("python") or shutil.which("python3") or shutil.which("py")
    if not py:
        log.warning("未找到 python/python3/py，无法自启识图后端"
                    "（可把 Python 加入 PATH 修复）")
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

    # v4.186.0（P1-11 修）：单实例锁。原实现无锁——双开会抢端口、共写 config、
    # 并发踩踏记忆文件。QLockFile 自带陈旧锁检测（进程崩溃留下的锁文件会被识别并
    # 自动接管），只在「另一个活实例持有锁」时才拒绝。
    _single_lock = None
    try:
        import tempfile
        from PySide6.QtCore import QLockFile
        _lock_path = os.path.join(tempfile.gettempdir(), "xiaochou_ai_single.lock")
        _single_lock = QLockFile(_lock_path)
        if not _single_lock.tryLock(0):
            _msg = ("小臭玩AI 已在运行中。\n\n"
                    "为避免配置互相覆盖与端口冲突，本应用不支持双开。\n"
                    "如确认没有实例在运行（上次异常退出），删除以下文件后重试：\n"
                    + _lock_path)
            log.warning("检测到另一实例正在运行，退出（lock=%s）", _lock_path)
            try:
                ctypes.windll.user32.MessageBoxW(
                    0, _msg, "小臭玩AI", 0x00000040)  # MB_ICONINFORMATION
            except Exception:
                print(_msg)
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        # 锁机制本身故障不应挡启动（降级为提示），保留 _single_lock=None 即不加锁
        log.warning("单实例锁初始化失败（降级为不加锁启动）: %s", e)
        _single_lock = None

    # 确保产物目录存在（统一产物落点：~/Documents/小臭玩AI/产物）
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
        try:
            obsidian_delay = max(0, int(cfg.get("obsidian_index_delay_sec", 0)))
        except (TypeError, ValueError):
            obsidian_delay = 0
        obsidian_worker = ObsidianInitWorker(cfg, config.rag_store, timeout=15.0,
                                             delay=obsidian_delay)
        obsidian_worker.finished.connect(
            lambda r: log.info("Obsidian(异步完成): %s", r)
        )
        obsidian_worker.start()
        log.info("Obsidian 初始化已在后台启动（超时 15s，延迟 %d s，不阻塞界面）", obsidian_delay)
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
    # v4.186.0（P1-11）：单实例锁保活——挂到 app 上防 GC 析构导致提前解锁
    if _single_lock is not None:
        app._single_instance_lock = _single_lock
    # v4.117：tooltip 是独立顶层窗口，不继承主窗口 QSS，全局规则必须挂在 QApplication 上
    # （否则交付物卡片/顶栏按钮 tooltip 走系统默认黑底，浅色主题下看不清）
    app.setStyleSheet(
        "QToolTip { background: %s; color: %s; border: 1px solid %s;"
        " border-radius: 8px; padding:8px 12px; font-size: 12px; }"
        % (THEME["white"], THEME["tooltip_text"], THEME["tooltip_border"]))
    perf_baseline.mark("qapp")

    # 保活后台 Obsidian worker（局部变量可能被 GC 导致线程被腰斩）
    if obsidian_worker is not None:
        app.obsidian_worker = obsidian_worker

    window = ChatWindow(cfg)
    # v4.152：把 window_shown 拆成「构造」与「show」两段 —— 实测 window_shown 占启动
    # 总时长的 89%（1.2~1.5s），但此前它把 ChatWindow(cfg) 构造与 show() 混在一起，
    # 看不出是哪一头重。加这一个 mark 后，下次启动即可在 perf/startup.jsonl 里看清。
    perf_baseline.mark("window_built")
    window.show()
    perf_baseline.mark("window_shown")

    tray = TrayApp(app, window, cfg)
    window.tray_app = tray  # 打通剪贴板通知到托盘
    perf_baseline.mark("tray")

    # v4.155 fix4：首次运行把内置技能从 dist 同步进用户目录，使技能不随重打包丢失
    try:
        import skill_review
        skill_review.seed_builtin_skills()
    except Exception as e:
        log.warning("内置技能同步失败（不影响启动）: %s", e)

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

    # v4.125 M-10：跨线程 UI 桥提前建（webhook / browser_bridge 两个 HTTP
    # 线程共用）——此前 webhook 回调在 serve_forever 线程直接调
    # tray.showMessage()，属跨线程 GUI 调用（H-08 残留），Windows 偶发托盘卡死。
    _ui_bridge = UiBridge()

    # v4.165.0：代码执行能力体检（诚实标注，不假装可用）。
    # 打包版**不内置** Python —— 若本机没有可用解释器，「写代码 / 做 PPT /
    # 数据分析」类任务必然失败。提前异步体检并明确告知，比等用户触发后
    # 再报 ModuleNotFoundError 友好得多。
    # 放后台线程（探测要起子进程，不能拖慢启动），再经 _ui_bridge 回 GUI 线程。
    def _probe_python_capability():
        try:
            import tools as _tools
            st = _tools.python_runtime_status(cfg)
        except Exception as e:
            log.debug("代码执行能力体检失败: %s", e)
            return
        available = st.get("available")
        reason = st.get("reason", "")
        missing = st.get("missing_libs") or []

        def _apply():
            try:
                if not available:
                    window.status_label.setText(
                        f"⚠ 代码运行不可用：{reason}（聊天 / 生图等不受影响）")
                    try:
                        tray.tray.showMessage(
                            "小臭玩AI · 能力提示",
                            "未找到可用的 Python 解释器，「写代码 / 做 PPT / 数据分析」"
                            "类任务暂不可用。\n安装 Python 并加入 PATH 后重启即可。")
                    except Exception:
                        pass
                elif missing:
                    log.info("run_python 解释器 %s 缺少库: %s", st.get("exe"), missing)
            except Exception:
                pass

        try:
            _ui_bridge.post(_apply)
        except Exception:
            _apply()

    try:
        import threading as _threading
        _threading.Thread(target=_probe_python_capability, daemon=True).start()
    except Exception:
        pass


    # Webhook 服务
    try:
        from webhook_server import get_webhook_server, set_event_callback

        def _wh_cb(kind, payload):
            def _notify_tray():
                try:
                    if getattr(window, "tray_app", None):
                        from PySide6.QtGui import QSystemTrayIcon
                        window.tray_app.tray.showMessage(
                            "小臭玩AI · Webhook", f"收到 {kind} 事件",
                            QSystemTrayIcon.Information, 4000)
                except Exception:
                    pass
            # v4.125 M-10：HTTP 线程 → 信号 queued 投递 GUI 线程，杜绝跨线程 Qt 调用
            _ui_bridge.post(_notify_tray)

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
                                    set_event_callback as _bridge_set_cb,
                                    set_persist_callback as _bridge_set_persist,
                                    report_delivery as _bridge_report_delivery)

        def _persist_bridge_token(new_tok):
            """v4.125 M-20：/pair 重置 token → 立刻写回 config，重启不再 401。"""
            try:
                cfg["browser_bridge_token"] = new_tok
                from config import save_config
                save_config(cfg)
                log.info("浏览器扩展配对码已更新并持久化")
            except Exception as e:
                log.warning("配对码持久化失败（重启后需重新配对）: %s", e)

        _bridge_set_persist(_persist_bridge_token)

        def _bridge_cb(kind, payload):
            if kind != "browser_page":
                return
            # 可观测性：网页抓取自「已接收」起就在状态栏可见——此前要等注入完
            # 才有托盘提示，中间那段时间用户看到的是「没反应」。
            # 本函数运行在 HTTP 线程，task_status 自身线程安全。
            _task_browser = ""
            try:
                _task_browser = task_status.begin(
                    "browser", "抓取：" + str(payload.get("title") or "网页")[:30])
                task_status.progress(_task_browser, "等待注入对话")
            except Exception:
                _task_browser = ""
            try:
                def _inject():
                    # 收口时要清空该 id（见下方 finally），必须显式 nonlocal，
                    # 否则赋值会让它变成 _inject 的局部变量 → 读取时 UnboundLocalError
                    nonlocal _task_browser
                    _ok = False
                    _detail = ""
                    _delivery_id = ""
                    _append_mode = False
                    try:
                        p = payload
                        _delivery_id = p.get("delivery_id", "")  # #756：投递回执 ID
                        title = p.get("title", "")
                        url = p.get("url", "")
                        text = p.get("text", "")
                        sel = p.get("selection", "")
                        note = p.get("note", "")
                        markdown = p.get("markdown", "") or ""
                        meta = p.get("meta") or {}
                        if not isinstance(meta, dict):
                            meta = {}
                        autosend = bool(p.get("autosend", False))
                        # L1.2/L1.4：优先用 Markdown 正文，其次选中文字，最后纯文本正文
                        body = markdown if markdown else (sel if sel else text)
                        if not body:
                            _ok, _detail = True, "空正文已忽略"
                            return
                        block = "请帮我处理这个网页内容"
                        if note:
                            block += "（要求：" + note + "）"
                        block += "\n\n"
                        block += "标题：" + title + "\n链接：" + url + "\n"
                        # L1.8：页面元信息增强（来源站点 / 字数 / 抓取时间）
                        site = meta.get("siteName", "") or ""
                        wc = meta.get("wordCount", 0) or 0
                        cap = meta.get("capturedAt", "") or ""
                        if site or wc or cap:
                            block += "来源：" + site
                            if wc:
                                block += "　字数：" + str(wc)
                            if cap:
                                block += "　抓取于：" + cap
                            block += "\n"
                        block += "\n---\n" + body + "\n---"

                        # #756(P1)：草稿保护——输入框已有未发送内容时，不覆盖，
                        # 改为追加到末尾，且本批不自动发送（避免把用户半截话一起发出去）。
                        existing = window.input_box.toPlainText() or ""
                        if bool(existing.strip()):
                            window.input_box.setPlainText(
                                existing.rstrip() + "\n\n" + block)
                            autosend = False
                            _append_mode = True
                        else:
                            window.input_box.setPlainText(block)
                        window.input_box.setFocus()

                        if autosend:
                            # L1.1：自动提交，等价于用户按了发送键
                            try:
                                window.send()
                                _ok, _detail = True, "已注入并自动发送"
                            except Exception as e:
                                # #756(P2#2)：自动发送失败 → 诚实报错，不再假装成功
                                log.warning("浏览器扩展自动发送失败（已填入输入框）: %s", e)
                                _ok, _detail = False, "已填入但自动发送失败：" + type(e).__name__
                        else:
                            _ok, _detail = True, ("已追加到草稿末尾（未自动发送）"
                                                  if _append_mode else "已填入输入框（待用户发送）")

                        # 托盘通知：成功/失败用不同图标，不再一律假装成功
                        if getattr(window, "tray_app", None):
                            from PySide6.QtGui import QSystemTrayIcon
                            if autosend and not _ok:
                                _icon = QSystemTrayIcon.Warning
                                _msg = "网页已填入，但自动发送失败：" + (title[:30] or "（无标题）") + "（请手动发送）"
                            elif _append_mode:
                                _icon = QSystemTrayIcon.Information
                                _msg = "输入框有未发送草稿，已把网页追加到末尾：" + (title[:30] or "（无标题）") + "（未自动发送）"
                            elif autosend:
                                _icon = QSystemTrayIcon.Information
                                _msg = "已自动发送网页：" + (title[:30] or "（无标题）")
                            else:
                                _icon = QSystemTrayIcon.Information
                                _msg = "已收到网页：" + (title[:30] or "（无标题）") + "（按发送键让 AI 处理）"
                            window.tray_app.tray.showMessage(
                                "小臭玩AI · 浏览器扩展", _msg, _icon, 4000)
                    except Exception as e:
                        log.warning("浏览器扩展注入失败: %s", e)
                        _ok, _detail = False, "注入异常：" + type(e).__name__
                    finally:
                        # #756(P2#2)：无论成败都回报投递结果，桥接据此前返回真实状态（非假成功）
                        if _delivery_id:
                            try:
                                _bridge_report_delivery(_delivery_id, _ok, _detail)
                            except Exception:
                                pass
                        # 可观测性：网页抓取任务收口（成功/失败都会在状态栏留下痕迹）
                        try:
                            if _task_browser:
                                if _ok:
                                    task_status.done(_task_browser, _detail)
                                else:
                                    task_status.fail(_task_browser, _detail or "注入失败",
                                                     retryable=True)
                                _task_browser = ""
                        except Exception:
                            pass

                # 跨线程（HTTP server 线程）→ 主线程执行 UI 操作（H-08：信号 queued 投递，
                # 替代 QTimer.singleShot——QTimer 依赖创建线程事件循环，HTTP 线程里不触发）
                _ui_bridge.post(_inject)
            except Exception as e:
                log.warning("浏览器扩展回调异常: %s", e)
                # 可观测性：回调整体异常时也要收口，否则任务会永远停在「处理中」
                try:
                    if _task_browser:
                        task_status.fail(_task_browser,
                                         f"回调异常：{type(e).__name__}", retryable=True)
                        _task_browser = ""
                except Exception:
                    pass

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
        # v4.188 P2-16：Obsidian 后台线程有界收尾——不 join 会在进程退出时
        # 触发「QThread: Destroyed while thread is still running」（随机崩）；
        # 但也不能无限 join（init_obsidian 内部 timeout 15s，退出最多挂 15s）。
        # 折中：wait(2000)——绝大多数场景（索引已跑完）立即过；正在跑的给 2s
        # 缓冲，超时放行（进程退出时线程自然终止，不会丢数据：init 是只读索引）。
        try:
            _ow = obsidian_worker
            if _ow is not None and _ow.isRunning():
                _ow.wait(2000)
        except Exception:
            pass
        try:
            gp = getattr(app, "_gateway_proc", None)
            if gp is not None and gp.poll() is None:
                gp.terminate()
                # v4.188 P2-16：terminate 是异步请求——不 wait 收尸，主进程退出时
                # uvicorn 孙进程可能来不及终止 → 孤儿进程占着 8000 端口，
                # 下次自启探测「端口已占用」误判为已在运行，识图静默失效。
                gp.wait(timeout=3)
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

    将用户数据目录（~/Documents/小臭玩AI）整体复制到带时间戳的备份子目录，
    排除体积庞大的「产物」目录，并保留最近 14 份。结果写入 logs/backup.log。
    返回 (ok: bool, msg: str)。
    """
    import shutil
    from datetime import datetime

    data_dir = os.path.join(os.path.expanduser("~/Documents"), "小臭玩AI")
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
        # 排除「产物」（可能含大体积图片/视频）与「backups」自身
        # （v4.125 P2：此前 backups 目录被递归复制进每次新备份——
        # 第 N 次备份写入量 ≈ N×全量数据，O(N²) 膨胀），其余全量复制
        def _ignore(dirname, names):
            if os.path.abspath(dirname) == os.path.abspath(data_dir):
                return {"产物", "backups"}
            return set()

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
