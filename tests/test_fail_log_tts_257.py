# -*- coding: utf-8 -*-
r"""v4.257.0 判据：工具失败日志真落库（P0-5）+ TTS 失败不再二次抛错（P0-6）。

judge-first：本文件先于实现编写，预期红。改完源码后转绿。

来源：外部评审报告《小臭玩AI-评审报告与P1-patch.md》（AI 生成，基线 v4.253.0）。
本轮挑两条「小改动、立刻兑现」的，且**实地核对过才立项**（报告这两条都成立，
但**范围要自己收窄**，照抄会改坏）：

  · P0-5 属实 —— `structured_logger.StructuredLogger.error` 签名是
    `(message, module=None, extra=None, session_id=None)`，**没有 exc_info**；
    而 tools.py 两处（964/978）传了 `exc_info=True` → TypeError → 外层
    `except Exception: pass` 吞掉。实证：`TypeError ... unexpected keyword
    argument 'exc_info'`。
    **结论：v4.155 fix1 号称补的「工具失败 ERROR 级落库」从未成功过一次。**
    ⚠ 范围收窄：全仓 8 处 `exc_info=True` 里**只有 tools.py 两处是真 bug**，
    其余 6 处（context_manager / empty_state / legion_ui ×2 / main ×2）用的是
    **std logging**（`logging.getLogger`），`exc_info` 是合法参数 —— 一并改会改坏。

  · P0-6 属实 —— `ui_workers.py:598` `_TTSWorker.run()` 的 except 分支裸用
    `log.error(...)`，但模块级**根本没有 logger**（`hasattr(ui_workers,'log')
    == False`；`log` 只是 `OrchestrateWorker` 的类属性 `Signal(str)`）→ NameError。
    更糟：这是**异常处理器里的异常** —— TTS 失败的真正原因被二次抛错吞掉，
    而 `_TTSWorker` 只有 `sig_mp3` 一个信号、调用方（ui.py:1497/1523）只连了它，
    **用户端完全无感知**。同族的 `_ASRWorker` 有 `sig_error`，本类漏了。

纪律（与项目同源）：
  · 行为级断言：走真实函数返回值 / 真实落库查询，**不查源码字符串**（静态串恒真）。
  · 反向用例必配：每条正向判据配「不该触发」的反例。
  · 只加不减：不传 exc_info 的老调用行为逐字不变；`_TTSWorker` 只加一个信号，
    既有调用方（只连 sig_mp3）不受影响。
  · 端到端优先：光修签名不够 —— 必须证明「工具真失败时 ERROR 真的进了库」。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def ck(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, extra))


print("=" * 74)
print("v4.257.0 失败日志落库 + TTS 错误处理判据")
print("=" * 74)

# ================================================================ F1 P0-5
print("\n[F1] P0-5 工具失败日志：exc_info 必须接得住，且 ERROR 真落库")
try:
    import structured_logger as sl

    # 临时库：绝不能写真实 ~/Documents/小臭玩AI/agent_log.db
    _tmpdir = tempfile.mkdtemp(prefix="logprobe_")
    _db = os.path.join(_tmpdir, "probe.db")
    _lg = sl.StructuredLogger(db_path=_db)

    # F1-1：签名接得住（现状必 TypeError）
    _err = None
    try:
        _lg.error("工具执行失败: probe | args={}", module="probe", exc_info=True)
    except TypeError as e:
        _err = e
    ck(_err is None, "F1-1 error(exc_info=True) 不再 TypeError", "→ %r" % _err)

    # F1-2：真落库（不是"接住了但没写"）
    _rows = _lg.query(level="ERROR", module="probe", limit=10)
    ck(len(_rows) >= 1, "F1-2 ERROR 真的进了库", "→ 查到 %d 条" % len(_rows))
    _row = _rows[0] if _rows else {}
    ck("probe" in str(_row.get("message", "")),
       "F1-3 落库内容含原始 message（不是被截断成空）", "→ %r" % _row.get("message"))

    # F1-4：exc_info 必须有信息增量 —— 只在 try 里调（能拿到 sys.exc_info()）
    _lg2 = sl.StructuredLogger(db_path=os.path.join(_tmpdir, "probe2.db"))
    try:
        raise ValueError("BOOM_PROBE_257")
    except ValueError:
        _lg2.error("带异常的失败", module="probe2", exc_info=True)
    _r2 = _lg2.query(level="ERROR", module="probe2", limit=5)
    _blob = str(_r2)
    ck("BOOM_PROBE_257" in _blob or "ValueError" in _blob,
       "F1-4 exc_info 把异常信息带进日志（不是只接住不记）", "→ %r" % _blob[:200])

    # F1-5 反向：不传 exc_info 行为不变（老调用点逐字不受影响）
    _lg3 = sl.StructuredLogger(db_path=os.path.join(_tmpdir, "probe3.db"))
    _lg3.error("朴素消息", module="plain")
    _r3 = _lg3.query(level="ERROR", module="plain", limit=5)
    ck(len(_r3) == 1 and _r3[0].get("message") == "朴素消息",
       "F1-5R 不传 exc_info 的调用行为不变（不回归）",
       "→ %r" % (_r3[0].get("message") if _r3 else None))

    # F1-6 同族一致：只修 error 会在 warning/info/critical 留同样的坑。
    # ⚠ 不能只断言「不抛 TypeError」—— 那样「签名接住了但没往下传」也能绿
    #   （扰动 V3 实测打脸）。必须断言**真生效**：异常信息真进了这条日志。
    for _lvl in ("info", "warning", "critical"):
        _lgf = sl.StructuredLogger(db_path=os.path.join(_tmpdir, "fam_%s.db" % _lvl))
        try:
            raise ValueError("BOOM_%s_257" % _lvl)
        except ValueError:
            try:
                getattr(_lgf, _lvl)("同族探测", module="fam_" + _lvl, exc_info=True)
            except TypeError as e:
                ck(False, "F1-6 %s 接不住 exc_info" % _lvl, "→ %r" % e)
                continue
        _rf = _lgf.query(level=_lvl.upper(), module="fam_" + _lvl, limit=5)
        ck("BOOM_%s_257" % _lvl in str(_rf),
           "F1-6 %s 的 exc_info 真生效（不是只接住不传）" % _lvl,
           "→ %r" % str(_rf)[:160])
except Exception as e:  # noqa: BLE001
    ck(False, "F1 structured_logger 导入/落库", repr(e))

# F1-7 端到端：工具真失败 → ERROR 真落库（光修签名不够，这条才证明病好了）
print("\n[F1-7] 端到端：exec_tool 里工具抛异常，ERROR 必须真进库")
try:
    import tools as _tools
    import structured_logger as sl2

    _tmpdir2 = tempfile.mkdtemp(prefix="e2e257_")
    _db2 = os.path.join(_tmpdir2, "e2e.db")
    _orig_logger = sl2._logger
    _probe = sl2.StructuredLogger(db_path=_db2)
    sl2._logger = _probe  # 替换单例，绝写不到真实库
    _saved = _tools.TOOL_REGISTRY.get("_probe_boom_257")
    try:
        def _boom(cfg, app_dir, args, **_kw):
            raise RuntimeError("BOOM_E2E_257")
        _tools.TOOL_REGISTRY["_probe_boom_257"] = {"handler": _boom, "dangerous": False}
        # 权限闸门 fail-closed：未登记风险的工具兜底成 EXTERNAL，会被挡在
        # handler 之前（根本走不到失败日志那一段）。探针工具必须显式登记 READ。
        try:
            from risk import RiskClass as _RC, RISK_MAP as _RM
            _RM["_probe_boom_257"] = _RC.READ
        except Exception as _re:  # noqa: BLE001
            print("  [warn] 风险登记失败，端到端可能被闸门挡下: %r" % _re)
        _before = len(_probe.query(level="ERROR", module="tools", limit=200))
        _res = _tools.exec_tool({}, ROOT, "_probe_boom_257", {})
        _after = _probe.query(level="ERROR", module="tools", limit=200)
        ck(len(_after) > _before,
           "F1-7 工具失败 → ERROR 真的落库（v4.155 fix1 首次真正生效）",
           "→ 前 %d 后 %d" % (_before, len(_after)))
        ck(any("_probe_boom_257" in str(r.get("message", "")) for r in _after),
           "F1-7 落库标明是哪个工具失败（可归因）",
           "→ %r" % [str(r.get("message"))[:80] for r in _after[-2:]])
        ck("BOOM_E2E_257" in str(_after),
           "F1-7 失败原因进日志（不是只剩一句『执行异常』）",
           "→ %r" % str(_after)[:200])
        ck(_res is not None and not _res.ok,
           "F1-7R 失败仍按既有契约返回失败态（不回归）", "→ ok=%r" % getattr(_res, "ok", None))
    finally:
        sl2._logger = _orig_logger
        if _saved is None:
            _tools.TOOL_REGISTRY.pop("_probe_boom_257", None)
        else:
            _tools.TOOL_REGISTRY["_probe_boom_257"] = _saved
except Exception as e:  # noqa: BLE001
    ck(False, "F1-7 端到端落库", repr(e))

# ================================================================ F2 P0-6
print("\n[F2] P0-6 TTS 失败：异常处理器不许自己再抛（NameError 吞掉真因）")
try:
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv[:1])
    import ui_workers as uw

    ck(getattr(uw, "log", None) is not None,
       "F2-1 ui_workers 模块级有 logger（不再裸用未定义名）",
       "→ %r" % getattr(uw, "log", None))

    # F2-2 行为级：真跑一遍失败路径，断言不再 NameError
    _real_synth = uw.voice_mod.synthesize
    _got = {}
    try:
        def _bad_synth(text, tts):
            raise RuntimeError("TTS_BOOM_257")
        uw.voice_mod.synthesize = _bad_synth
        _w = uw._TTSWorker("测试文本", None)
        if hasattr(_w, "sig_error"):
            _w.sig_error.connect(lambda m: _got.setdefault("err", m))
        _crash = None
        try:
            _w.run()  # 直接调 run()，不启线程（避免真线程里的异常无处捕获）
        except NameError as e:
            _crash = e
        except Exception:
            _crash = None  # 其他异常不在本条射程内
        ck(_crash is None, "F2-2 TTS 失败路径不再抛 NameError", "→ %r" % _crash)
        ck(_got.get("err") is not None and "TTS_BOOM_257" in str(_got.get("err")),
           "F2-3 失败原因经 sig_error 上送（与 _ASRWorker 同构，用户端可见）",
           "→ %r" % _got.get("err"))
    finally:
        uw.voice_mod.synthesize = _real_synth

    # F2-4 反向：成功路径照常 emit sig_mp3（不回归）
    try:
        uw.voice_mod.synthesize = lambda text, tts: "/tmp/fake.mp3"
        _w2 = uw._TTSWorker("测试文本", None)
        _mp3 = {}
        _got2 = {}
        _w2.sig_mp3.connect(lambda p: _mp3.setdefault("p", p))
        if hasattr(_w2, "sig_error"):
            _w2.sig_error.connect(lambda m: _got2.setdefault("err", m))
        _w2.run()
        ck(_mp3.get("p") == "/tmp/fake.mp3",
           "F2-4R 成功路径照常 emit sig_mp3（不回归）", "→ %r" % _mp3.get("p"))
        # 反向的另一半：成功时**不许**有错误信号。
        # 只测「成功仍 emit mp3」抓不到「成功路径被拖进 except」——扰动 V7 实测打脸：
        # emit 已经发生，mp3 断言照样绿。必须连「没有 sig_error」一起断言。
        ck(_got2.get("err") is None,
           "F2-4R2 成功路径不 emit sig_error（成功就是成功，不许报假错）",
           "→ %r" % _got2.get("err"))
    finally:
        uw.voice_mod.synthesize = _real_synth

    # F2-5 同族护栏：_ASRWorker 已有 sig_error，_TTSWorker 必须对齐
    ck(hasattr(uw._ASRWorker, "sig_error") and hasattr(uw._TTSWorker, "sig_error"),
       "F2-5 两个语音 worker 都有 sig_error（同族一致）",
       "→ ASR=%s TTS=%s" % (hasattr(uw._ASRWorker, "sig_error"),
                            hasattr(uw._TTSWorker, "sig_error")))
except Exception as e:  # noqa: BLE001
    ck(False, "F2 ui_workers 导入/探针", repr(e))

print("\n" + "=" * 74)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
