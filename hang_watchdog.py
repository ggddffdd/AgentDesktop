# -*- coding: utf-8 -*-
"""v4.316.0 主线程卡死自证 watchdog —— 纯可观测加固，零行为变化。

现场（2026-11-21）：自动化任务执行中 app 主线程停响应，Windows 事件日志
Application Hang(1002) + WER AppHangB1 强关。冻结包无符号，WER 抓不到 Python 栈，
无法定位卡点。本模块提供「下次卡死自证」能力：

  * 主线程 QTimer 每秒调 beat() 刷心跳；
  * daemon watchdog 线程每 HEARTBEAT_INTERVAL_SEC 检查一次心跳年龄，
    超过阈值（默认 20s）就把**全线程** Python 栈 dump 到独立日志文件；
  * dump 后按 REPEAT_DUMP_SEC 周期重复（避免日志爆炸），心跳恢复自动静默。

设计约束：
  1. 不 dump 时不写一个字节，不改任何业务路径 —— 纯观测，零副作用。
  2. 启用 faulthandler 并指定独立日志文件，让致命信号（segfault 等）也写进同一处。
  3. 线程 daemon=True，uninstall() 幂等，退出时不阻止进程。
  4. is_hung 用 `>=` 边界语义（边界也算卡死，不放过刚好卡死的样本）。
"""
import faulthandler
import os
import threading
import time

# 判据可见常量
HEARTBEAT_INTERVAL_SEC = 1.0   # watchdog 检查周期（秒）
REPEAT_DUMP_SEC = 30.0         # 卡死后重复 dump 周期（防日志爆炸）
HANG_MARK = "=== HANG DETECTED: main thread event loop stalled ==="

_lock = threading.Lock()
_beat_ts = 0.0
_last_dump_ts = 0.0
_threshold = 20.0
_enabled = False
_stop = None
_thread = None
_file = None


def is_hung(now, beat_ts, threshold=None):
    """纯函数：主线程是否已停滞超阈值（>= 边界也算，防放过一个刚好卡死的样本）。"""
    if threshold is None:
        threshold = _threshold
    return (now - beat_ts) >= threshold


def beat():
    """主线程事件循环心跳 —— 由 QTimer 每秒调用。"""
    global _beat_ts
    with _lock:
        _beat_ts = time.monotonic()


def _emit(text):
    try:
        if _file is not None:
            _file.write(text)
            _file.flush()
    except Exception:
        pass


def _watchdog_tick(now=None):
    """一轮检测：心跳停滞超阈值且距上次 dump 够久，则 dump 全线程栈。

    `now` 仅供判据注入受控时间；生产由 _watchdog_loop 传真实 monotonic。
    """
    global _last_dump_ts
    now = time.monotonic() if now is None else now
    with _lock:
        b = _beat_ts
    if not is_hung(now, b):
        return False
    if now - _last_dump_ts < REPEAT_DUMP_SEC:
        return False
    _last_dump_ts = now
    _emit("%s at %s (idle %.1fs)\n"
          % (HANG_MARK, time.strftime("%F %T"), now - b))
    try:
        faulthandler.dump_traceback(file=_file, all_threads=True)
    except Exception as e:
        _emit("dump_traceback failed: %r\n" % (e,))
    return True


def _watchdog_loop():
    while not _stop.wait(HEARTBEAT_INTERVAL_SEC):
        _watchdog_tick()


def install(log_path, threshold=20.0):
    """启动 watchdog。返回 True=本次真启动，False=已启动过（幂等）。"""
    global _enabled, _stop, _thread, _file, _beat_ts, _last_dump_ts, _threshold
    if _enabled:
        return False
    _threshold = threshold
    d = os.path.dirname(log_path)
    if d:
        os.makedirs(d, exist_ok=True)
    _file = open(log_path, "a", encoding="utf-8", buffering=1)
    _stop = threading.Event()
    with _lock:
        _beat_ts = time.monotonic()
    _last_dump_ts = 0.0
    try:
        faulthandler.enable(file=_file)
    except Exception:
        pass
    _thread = threading.Thread(target=_watchdog_loop, name="hang-watchdog", daemon=True)
    _thread.start()
    _enabled = True
    return True


def uninstall():
    """停止 watchdog（进程退出前调用）。幂等。"""
    global _enabled, _stop, _thread, _file
    if not _enabled:
        return False
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=2)
    try:
        faulthandler.disable()
    except Exception:
        pass
    if _file is not None:
        try:
            _file.flush()
            _file.close()
        except Exception:
            pass
    _enabled = False
    _thread = None
    _file = None
    return True
