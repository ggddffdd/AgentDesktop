# -*- coding: utf-8 -*-
"""v4.316.0 判据：主线程卡死必须能自证（faulthandler 全线程栈 dump）。

背景（2026-11-21 现场）：自动化任务执行中 app 主线程停响应，Windows 事件日志
Application Hang(1002) + WER AppHangB1 强关；冻结包无符号，WER 抓不到 Python 栈，
无法定位卡点。本判据要求新增一个纯可观测 watchdog：
  - 主线程 QTimer 每秒心跳；
  - daemon watchdog 线程监测心跳年龄，超阈值（默认 20s）就把**全线程** Python 栈
    dump 到独立日志；dump 后按周期重复（避免日志爆炸），心跳恢复自动静默。
零行为变化：不dump 时一个字节都不写；不改任何业务路径。

判据分两层：
  A 层 —— 直接调模块函数（is_hung 边界语义 / install 幂等 / tick dump all_threads），
          用 mock 拦 faulthandler，绝不真起线程真 dump、绝不写真实用户目录。
  B 层 —— 源码契约（main.py 接线、faulthandler.enable、all_threads=True、daemon）。
"""
import os
import re
import sys
import tempfile
import shutil
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import hang_watchdog as hw
    IMPORT_ERR = ""
except Exception as e:
    hw = None
    IMPORT_ERR = "%s: %s" % (type(e).__name__, e)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print("  [PASS] %s" % name)
        _p += 1
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        _f += 1


def read_src(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


MAIN_SRC = read_src(os.path.join(ROOT, "main.py"))
HW_PATH = os.path.join(ROOT, "hang_watchdog.py")
HW_SRC = read_src(HW_PATH) if os.path.isfile(HW_PATH) else ""


# ---------------------------------------------------------------- A 层：行为
A_NAMES = [
    "A2  is_hung 超阈值判卡死",
    "A3  is_hung 心跳新鲜不判卡死",
    "A4  is_hung 边界相等判卡死（>= 而非 >）",
    "A5  is_hung 阈值可配",
    "A6  install 建独立日志目录与文件",
    "A7  install 幂等（重复调用返回 False）",
    "A8  beat 刷新内部心跳时间戳",
    "A9  心跳新鲜时 tick 不 dump",
    "A10 心跳停滞超阈值 tick dump 全线程栈",
    "A11 dump 后短周期内不重复 dump（防日志爆炸）",
    "A12 uninstall 停止 watchdog 且幂等",
]
check("A1  hang_watchdog 模块可导入", hw is not None, IMPORT_ERR)
if hw is None:
    for n in A_NAMES:
        check(n, False, "模块不存在")
else:
    check(A_NAMES[0], hw.is_hung(100.0, 79.0, 20.0) is True)
    check(A_NAMES[1], hw.is_hung(100.0, 81.0, 20.0) is False)
    check(A_NAMES[2], hw.is_hung(100.0, 80.0, 20.0) is True,
          "边界 now-beat==threshold 必须判死，防改 >= 为 >")
    check(A_NAMES[3],
          hw.is_hung(100.0, 95.0, 3.0) is True
          and hw.is_hung(100.0, 95.0, 10.0) is False)

    tmp = tempfile.mkdtemp(prefix="xc_hangwd_")
    try:
        logp = os.path.join(tmp, "sub", "hang_traceback.log")
        ok1 = hw.install(logp, threshold=5.0)
        check(A_NAMES[4], ok1 is True and os.path.isfile(logp),
              "install=%r exists=%s" % (ok1, os.path.isfile(logp)))
        ok2 = hw.install(logp, threshold=5.0)
        check(A_NAMES[5], ok2 is False, "second install=%r" % (ok2,))

        with mock.patch.object(hw.time, "monotonic", return_value=1234.5):
            hw.beat()
        check(A_NAMES[6], abs(getattr(hw, "_beat_ts", -1) - 1234.5) < 1e-6,
              "_beat_ts=%r" % (getattr(hw, "_beat_ts", None),))

        # tick 用受控时间戳：新鲜心跳不 dump
        with mock.patch.object(hw.time, "monotonic", return_value=1234.5):
            hw.beat()
        with mock.patch.object(hw.faulthandler, "dump_traceback") as dmp:
            r = hw._watchdog_tick(now=1235.0)
        check(A_NAMES[7], r is False and dmp.call_count == 0,
              "ret=%r calls=%d" % (r, dmp.call_count))

        # 心跳停滞 6s（阈值 5s）→ dump，且 all_threads=True
        with mock.patch.object(hw.faulthandler, "dump_traceback") as dmp:
            r = hw._watchdog_tick(now=1240.5)
        kw = dmp.call_args[1] if dmp.call_args else {}
        check(A_NAMES[8],
              r is True and dmp.call_count == 1 and kw.get("all_threads") is True,
              "ret=%r calls=%d kw=%r" % (r, dmp.call_count, kw))

        # 刚 dump 完，5s 后再 tick（< REPEAT_DUMP_SEC）不重复
        with mock.patch.object(hw.faulthandler, "dump_traceback") as dmp:
            r = hw._watchdog_tick(now=1245.5)
        check(A_NAMES[9], r is False and dmp.call_count == 0,
              "ret=%r calls=%d" % (r, dmp.call_count))

        ok3 = hw.uninstall()
        ok4 = hw.uninstall()
        check(A_NAMES[10], ok3 is True and ok4 is False and getattr(hw, "_enabled", True) is False,
              "uninstall=%r/%r enabled=%r" % (ok3, ok4, getattr(hw, "_enabled", None)))
    finally:
        try:
            hw.uninstall()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- B 层：源码契约
check("B1  main.py 调用 hang_watchdog.install",
      "hang_watchdog.install(" in MAIN_SRC)
check("B2  main.py 用 QTimer timeout 接 hang_watchdog.beat",
      re.search(r"\.timeout\.connect\(\s*hang_watchdog\.beat\s*\)", MAIN_SRC) is not None
      and "QTimer" in MAIN_SRC)
check("B3  main.py 退出路径调用 hang_watchdog.uninstall",
      "hang_watchdog.uninstall(" in MAIN_SRC)
# 行首锚定真实调用，杜绝 docstring/注释里的示例文本造成假绿
check("B4  watchdog 启用 faulthandler.enable(file=...)",
      re.search(r"^\s+faulthandler\.enable\(\s*file\s*=", HW_SRC, re.M) is not None)
check("B5  dump 用 all_threads=True（能抓主线程栈）",
      "all_threads=True" in HW_SRC)
check("B6  is_hung 用 >= 边界语义",
      re.search(r"def is_hung[\s\S]{0,200}?return[^\n]*>=", HW_SRC) is not None)
check("B7  watchdog 线程 daemon=True（不阻止退出）",
      re.search(r"threading\.Thread\([^)]*daemon=True", HW_SRC) is not None)

print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
