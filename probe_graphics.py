# -*- coding: utf-8 -*-
"""探针 v0：最小 QGraphicsView —— 给「视频节点画布」先踩三坑（设计稿第 0 步）。

为什么要先做这一个 30 行的画布：正式做画布编排之前，有三件事只有把 Qt 真跑起来
才知道答案，看文档、问模型都不算数。本文件就是那三件事的**实测答卷**，
不追求好看、不接任何业务数据、也不打算长期留在代码里。

坑① windowed 打包
------------------
PyInstaller `--noconsole`（本项目小臭玩AI.spec 第 90 行就是这个值）打出来的进程
没有控制台，`sys.stdout` / `sys.stderr` **都是 None**。此时任何一句裸 print 都是
`AttributeError: 'NoneType' object has no attribute 'write'`，而且因为没人看得见
stderr，表现为「双击 exe 毫无反应、零日志」，是最难查的死法。
→ 本文件所有对外输出走 self_emit()：能写流就写、不能写就只写日志文件，
  两条路都断也绝不抛。
→ 怎么证明它真的有效：selftest 里把 sys.stdout/stderr 临时换成 None 再跑一遍
  （模拟），外加 PyInstaller --noconsole 打真 exe 跑一遍（真证据）。

坑② ffmpeg 子进程
------------------
节点缩略图要靠 ffmpeg 抽帧（兄弟文件 probe_ffmpeg_twin.py 验证过命令本身可用）。
但那是在控制台里跑的。windowed 下父进程没有控制台句柄，两个细节必须做对：
  * `stdin=subprocess.DEVNULL` —— 不给的话子进程继承到一个无效句柄，
    在 windowed 下会卡住或直接 ValueError；
  * `creationflags=CREATE_NO_WINDOW` + `STARTUPINFO(STARTF_USESHOWWINDOW, SW_HIDE)`
    —— 不给的话每抽一帧闪一个黑框。项目里 ui.py / voice.py / video_pipeline.py
    已经统一用 `_NO_WINDOW`，这里向它对齐（写法保持一致，便于以后照抄）。

坑③ 高 DPI
----------
QGraphicsView 的坐标全程是**逻辑像素**；系统缩放 125%/150% 只放大设备像素。
推论（也是本探针要实证明的三条）：
  * item 的 sceneBoundingRect、proxy 控件 geometry、view viewport 逻辑尺寸，
    在 100%/125%/150% 三档下必须**逐字段相等**；
  * 真随档位变的是 grab() 出来的**物理像素** = 逻辑尺寸 × devicePixelRatio；
  * ffmpeg 抽出来的图是纯物理像素、没有 DPR 概念，直接丢给 QGraphicsPixmapItem
    在 150% 屏上会「看着只有 2/3 大、还发虚」，必须 `setDevicePixelRatio(dpr)`。
    （这一条不修的话，画布上的缩略图在高分屏永远小一圈，属于肉眼看得出、
     但没人会怀疑是 DPR 的 bug。）

用法
----
    python probe_graphics.py                          # GUI 模式：真开一个窗口看一眼
    python probe_graphics.py --selftest               # 离屏自检，打印指标（不弹窗）
    python probe_graphics.py --selftest --json a.json # 指标写 JSON（判据套件消费）
    python probe_graphics.py --selftest --scale 1.5   # 指定 QT_SCALE_FACTOR 档位

退出码：0=全部通过，1=有一项不过。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

# --------------------------------------------------------------------------
# 常量：够踩坑就行的几何量，与以后正式画布的版式无关
# --------------------------------------------------------------------------
SCENE_W, SCENE_H = 640.0, 280.0
NODE_W, NODE_H = 160.0, 80.0
THUMB_W, THUMB_H = 320, 180          # ffmpeg 抽帧图的物理像素
THUMB_DISPLAY_W, THUMB_DISPLAY_H = 160.0, 90.0   # 画布上按逻辑像素展示的尺寸

IS_NT = os.name == "nt"
# 与 ui.py / voice.py / video_pipeline.py 完全同款写法
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_NT else 0


# --------------------------------------------------------------------------
# 坑①：安全输出。这是本文件唯一的「对外发声」通道。
# --------------------------------------------------------------------------
_LINES = []


def _log_path():
    """日志文件落点：优先程序所在目录，写不进就退回系统临时目录。

    windowed 打包时 cwd 可能是系统目录（不够写权限），所以必须兜底；
    更不能写 exe 同级的 _internal —— 那是 PyInstaller 的产物目录。
    """
    try:
        base = os.path.dirname(os.path.abspath(sys.executable or __file__))
    except Exception:
        base = tempfile.gettempdir()
    try:
        with open(os.path.join(base, "._w"), "w") as _f:
            _f.write("")
        os.remove(os.path.join(base, "._w"))
    except Exception:
        base = tempfile.gettempdir()
    return os.path.join(base, "_probe_graphics.log")


LOG_FILE = _log_path()


def self_emit(msg):
    """写一行诊断：先内存留底 → 再试 stdout → 再落日志文件。

    三步各自独立 try：任何一步失败都不能拖垮调用方（探针崩了等于没测）。
    """
    _LINES.append(msg)
    try:
        s = sys.stdout
        if s is not None:
            s.write(msg + "\n")
            s.flush()
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _install_excepthook():
    """把未捕获异常也写进日志 —— windowed 下这是唯一的遗言通道。"""
    def _hook(etype, val, tb):
        import traceback
        try:
            self_emit("[FATAL] " + "".join(traceback.format_exception(etype, val, tb)))
        except Exception:
            pass
        try:
            sys.__excepthook__(etype, val, tb)
        except Exception:
            pass
    sys.excepthook = _hook


class NoConsole(object):
    """把 sys.stdout / sys.stderr 临时换成 None —— 等价于 windowed 的运行环境。

    为什么不用 redirect：None 才是 PyInstaller --noconsole 的真实形态，
    用 io.StringIO 兜着反而测不出裸 print 的崩法。
    """

    def __enter__(self):
        self._out, self._err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = None, None
        return self

    def __exit__(self, *exc):
        sys.stdout, sys.stderr = self._out, self._err
        return False


# --------------------------------------------------------------------------
# 坑②：ffmpeg 子进程
# --------------------------------------------------------------------------
def _startupinfo():
    """构造隐藏子窗口的 STARTUPINFO（Windows 专用，其它平台返回 None）。"""
    if not IS_NT:
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


def find_ffmpeg():
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def run_ffmpeg(args, timeout=90):
    """跑一条 ffmpeg 命令。返回 dict：rc / out / err / error。

    三件套缺一不可（见文件头的坑②说明）：stdin=DEVNULL、creationflags、
    startupinfo。text=True 但显式给 encoding —— 不写的话 Windows 下按 GBK 解，
    ffmpeg 的 UTF-8 输出会直接抛 UnicodeDecodeError。
    """
    try:
        p = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW,
            startupinfo=_startupinfo(),
        )
        return {"rc": p.returncode, "out": p.stdout or "", "err": p.stderr or "",
                "error": None}
    except Exception as e:
        return {"rc": -1, "out": "", "err": "", "error": "%s: %s" % (type(e).__name__, e)}


# --------------------------------------------------------------------------
# 坑③：最小画布。build_canvas_view() 是给以后真画布留的骨架
# --------------------------------------------------------------------------
def build_canvas_view(thumb_pixmap=None, parent=None):
    """构造最小节点画布：2 个节点 + 1 条连线 + 1 个 proxy 控件 + 1 张抽帧缩略图。

    返回 (view, parts)。parts 里保留对象句柄，供自检阶段读坐标：
      nodes  —— 节点方块（QGraphicsRectItem）
      line   —— 连线（QGraphicsLineItem）
      proxy  —— 画布里嵌真实 QWidget 的能力（以后节点上的下拉/开关就靠它）
      thumb  —— 抽帧缩略图（QGraphicsPixmapItem）

    这个函数业务无关：只保证「结构与坐标系语义正确」，是以后正式画布的种子。
    """
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QPainter, QPen
    from PySide6.QtWidgets import (QGraphicsItem, QGraphicsLineItem,
                                   QGraphicsPixmapItem, QGraphicsProxyWidget,
                                   QGraphicsRectItem, QGraphicsScene,
                                   QGraphicsView, QLabel)

    scene = QGraphicsScene(QRectF(0, 0, SCENE_W, SCENE_H))
    pen = QPen(QColor("#8AB4F8"), 1.5)
    brush = QBrush(QColor("#202124"))

    parts = {"nodes": {}, "scene": scene}

    def _node(x, y, w, h, name):
        it = scene.addRect(QRectF(x, y, w, h), pen, brush)
        it.setFlag(QGraphicsItem.ItemIsMovable, True)
        it.setFlag(QGraphicsItem.ItemIsSelectable, True)
        it.setData(0, name)
        parts["nodes"][name] = it
        return it

    _node(20.0, 20.0, NODE_W, NODE_H, "nodeA")
    _node(300.0, 20.0, NODE_W, NODE_H, "nodeB")
    # 连线：从 A 右边中点连到 B 左边中点（逻辑坐标，单位=逻辑像素）
    parts["line"] = scene.addLine(20.0 + NODE_W, 20.0 + NODE_H / 2.0,
                                  300.0, 20.0 + NODE_H / 2.0, pen)

    # proxy 控件：验证「画布里能塞真 QWidget」—— 这是节点是否可做交互的分水岭
    lab = QLabel("节点里的真控件")
    lab.setMinimumWidth(int(NODE_W))
    parts["proxy"] = scene.addWidget(lab)
    parts["proxy"].setPos(300.0, 160.0)
    parts["proxy"].widget().resize(int(NODE_W), 60)

    # 抽帧缩略图：给了 ffmpeg 图就放上去，没给就用纯色占位，方便无 ffmpeg 环境也能测
    if thumb_pixmap is not None and not thumb_pixmap.isNull():
        pm = thumb_pixmap
        parts["thumb"] = QGraphicsPixmapItem(pm)
        scene.addItem(parts["thumb"])
        parts["thumb"].setPos(20.0, 160.0)
    else:
        parts["thumb"] = None

    view = QGraphicsView(scene, parent)
    view.setRenderHint(QPainter.Antialiasing, True)
    view.setRenderHint(QPainter.SmoothPixmapTransform, True)
    view.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
    view.setDragMode(QGraphicsView.RubberBandDrag)
    view.resize(int(SCENE_W), int(SCENE_H))
    parts["view"] = view
    return view, parts


def _rect_tuple(rf):
    def _r(v):
        return round(float(v), 3)
    return [_r(rf.x()), _r(rf.y()), _r(rf.width()), _r(rf.height())]


# --------------------------------------------------------------------------
# 自检：把三坑的答案写成一组可比较的指标
# --------------------------------------------------------------------------
def collect_metrics():
    """起一个离屏 QApplication，构造画布，把三坑的实测值采集成 dict。"""
    from PySide6.QtCore import QCoreApplication, qVersion
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    m = {}
    _install_excepthook()

    # ---- 坑① 环境事实：是不是打包态、有没有 stdout ----
    m["frozen"] = bool(getattr(sys, "frozen", False))
    m["meipass"] = getattr(sys, "_MEIPASS", None) is not None
    m["exe"] = sys.executable
    m["py"] = sys.version.split()[0]
    m["stdout_none"] = sys.stdout is None
    m["stderr_none"] = sys.stderr is None

    app = QApplication.instance() or QApplication(sys.argv)
    m["app_started"] = True
    m["qt"] = qVersion()
    m["platform"] = QCoreApplication.instance().applicationName() or app.platformName()

    scr = app.primaryScreen()
    m["scale_factor_env"] = os.environ.get("QT_SCALE_FACTOR")
    m["dpr"] = round(float(scr.devicePixelRatio()), 4)
    m["logical_dpi"] = round(float(scr.logicalDotsPerInch()), 3)
    m["physical_dpi"] = round(float(scr.physicalDotsPerInch()), 3)
    m["screen_logical"] = [scr.size().width(), scr.size().height()]

    # ---- 坑② ffmpeg：先跑 --version，再抽一帧当缩略图 ----
    ff = find_ffmpeg()
    m["ffmpeg_path"] = ff
    work = tempfile.mkdtemp(prefix="probe_gfx_")
    thumb_png = os.path.join(work, "thumb.png")

    v = run_ffmpeg([ff or "ffmpeg", "-version"], timeout=60) if ff else {
        "rc": -1, "out": "", "err": "", "error": "ffmpeg not found"}
    m["ffmpeg_version_rc"] = v["rc"]
    m["ffmpeg_version_line"] = (v["out"].splitlines() or [""])[0][:120]
    m["ffmpeg_version_err"] = (v["err"] or v["error"] or "")[:200]

    if ff and v["rc"] == 0:
        r = run_ffmpeg([ff, "-y", "-f", "lavfi", "-i",
                        "color=c=navy:s=%dx%d:d=1" % (THUMB_W, THUMB_H),
                        "-frames:v", "1", thumb_png], timeout=90)
        m["ffmpeg_thumb_rc"] = r["rc"]
        m["thumb_exists"] = os.path.isfile(thumb_png)
        m["thumb_bytes"] = os.path.getsize(thumb_png) if os.path.isfile(thumb_png) else 0
    else:
        m["ffmpeg_thumb_rc"] = -1
        m["thumb_exists"] = False
        m["thumb_bytes"] = 0

    # 无控制台态（等价于 windowed）下再跑一次 ffmpeg：这里若有裸 print 就会崩
    try:
        with NoConsole():
            v2 = run_ffmpeg([ff or "ffmpeg", "-version"], timeout=60)
            self_emit("noconsole: rc=%s" % v2["rc"])   # self_emit 必须在 None 流下也不抛
            m["ffmpeg_noconsole_rc"] = v2["rc"]
            m["ffmpeg_noconsole_err"] = None
    except Exception as e:
        m["ffmpeg_noconsole_rc"] = -1
        m["ffmpeg_noconsole_err"] = "%s: %s" % (type(e).__name__, e)

    # ---- 坑③ 高 DPI：构造画布，采逻辑几何 / grab 物理像素 / pixmap DPR ----
    dpr = m["dpr"] or 1.0
    thumb_for_scene = None
    pix = QPixmap()
    m["thumb_raw_size"] = [0, 0]
    m["thumb_logical_before"] = [0, 0]
    m["thumb_logical_after"] = [0, 0]
    if m.get("thumb_exists") and m.get("thumb_bytes", 0) > 0:
        pix = QPixmap(thumb_png)
        m["thumb_raw_size"] = [pix.width(), pix.height()]
        # ① ffmpeg 抽出来的图只有「物理像素」概念：不标 DPR 时它的逻辑宽 == 物理宽。
        m["thumb_logical_before"] = [pix.width(), pix.height()]
        pm2 = QPixmap(pix)
        pm2.setDevicePixelRatio(dpr)
        # ② 「标了 DPR」不是让 width() 变小（实测 Qt6 的 QPixmap.width() 恒等于
        #    设备像素宽，setDevicePixelRatio 只影响它在 device-independent 坐标系里
        #    怎么被绘制）。真正的证据在下面那组对照量：同一个图处理/不处理，
        #    进画布后占的逻辑宽度不同 —— 这才是高分屏上「缩略图偏大发虚」的根因。
        m["thumb_logical_after"] = [round(pm2.width(), 3), round(pm2.height(), 3)]
        # 缩略图按逻辑尺寸 160x90 铺进画布：源图先按 DPR 倍放大，再标回 DPR，
        # 这样逻辑尺寸仍是 160x90，物理像素却是 160x90xDPR（高分屏上不虚）。
        thumb_for_scene = pm2.scaled(int(THUMB_DISPLAY_W * dpr),
                                     int(THUMB_DISPLAY_H * dpr))
        thumb_for_scene.setDevicePixelRatio(dpr)

    # ---- 对照实验：同一张 ffmpeg 图，处理 DPR / 不处理 DPR，进画布后占位差多少 ----
    # 这是「坑③」唯一有说服力的证据——光看 QPixmap.width() 是看不出问题的。
    m["thumb_untreated_w"] = None
    m["thumb_treated_w"] = None
    if m.get("thumb_exists") and m.get("thumb_bytes", 0) > 0:
        from PySide6.QtWidgets import QGraphicsPixmapItem
        src = QPixmap(thumb_png)
        if not src.isNull():
            # 对照组：ffmpeg 原始产物原样放大后丢进画布（模拟没有 DPR 意识的写法）
            raw = src.scaled(int(THUMB_DISPLAY_W * dpr), int(THUMB_DISPLAY_H * dpr))
            m["thumb_untreated_w"] = round(
                float(QGraphicsPixmapItem(raw).boundingRect().width()), 3)
            if thumb_for_scene is not None:
                m["thumb_treated_w"] = round(
                    float(QGraphicsPixmapItem(thumb_for_scene).boundingRect().width()), 3)

    view, parts = build_canvas_view(thumb_pixmap=thumb_for_scene)
    view.resize(int(SCENE_W), int(SCENE_H))
    view.show()
    app.processEvents()

    m["viewport_size"] = [view.viewport().width(), view.viewport().height()]
    m["view_size"] = [view.width(), view.height()]
    m["scene_rect"] = _rect_tuple(parts["scene"].sceneRect())
    m["transform_m11"] = round(float(view.transform().m11()), 4)
    m["node_rects"] = {k: _rect_tuple(v.sceneBoundingRect())
                       for k, v in parts["nodes"].items()}
    ln = parts["line"].line()
    m["line"] = [round(float(ln.x1()), 3), round(float(ln.y1()), 3),
                 round(float(ln.x2()), 3), round(float(ln.y2()), 3)]
    pw = parts["proxy"].widget()
    m["proxy_widget_size"] = [pw.width(), pw.height()]
    m["proxy_scene_rect"] = _rect_tuple(parts["proxy"].sceneBoundingRect())
    if parts.get("thumb") is not None:
        m["thumb_scene_rect"] = _rect_tuple(parts["thumb"].sceneBoundingRect())
    else:
        m["thumb_scene_rect"] = None

    # grab：验证「物理像素 == 逻辑像素 × DPR」
    # ⚠️ 实测坑（Qt6/PySide6 6.11）：QWidget.grab() 返回的 QPixmap **自带
    #    devicePixelRatio**，它的 width()/height() 是**物理像素**不是逻辑像素。
    #    把它当逻辑尺寸用、或再乘一次 DPR，都会得到 1.5 倍的错位。
    gp = view.grab()
    m["grab_physical"] = [gp.width(), gp.height()]
    m["grab_dpr"] = round(float(gp.devicePixelRatio()), 4)
    m["grab_logical"] = [round(gp.width() / (gp.devicePixelRatio() or 1.0), 3),
                         round(gp.height() / (gp.devicePixelRatio() or 1.0), 3)]
    m["expect_physical"] = [round(view.width() * dpr, 3),
                            round(view.height() * dpr, 3)]

    view.close()
    try:
        shutil.rmtree(work, ignore_errors=True)
    except Exception:
        pass

    # ---- 结论位（判据套件据此判红绿）----
    m["checks"] = {
        "app_started": bool(m.get("app_started")),
        "ffmpeg_ok": m.get("ffmpeg_version_rc") == 0,
        "ffmpeg_noconsole_ok": m.get("ffmpeg_noconsole_rc") == 0,
        "thumb_ok": bool(m.get("thumb_exists")) and m.get("thumb_bytes", 0) > 0,
        "nodes_ok": len(m.get("node_rects", {})) == 2,
        "proxy_ok": all(x > 0 for x in m.get("proxy_widget_size", [0, 0])),
        # grab() 抓的是整个 widget（含边框）；除回 DPR 后的逻辑尺寸必须等于 view 尺寸
        "grab_logical_full": (m.get("grab_logical") == list(m.get("view_size") or [0, 0])),
        # 核心：物理像素 == 逻辑像素 × DPR（1.25 档应是 800×350 而不是 640×280）
        "grab_physical_matches": (list(m.get("grab_physical") or [])
                                  == list(m.get("expect_physical") or [])),
        # 画布上的缩略图逻辑占位必须是 160 宽（不是原图/未补 DPR 时的 320/200）
        "thumb_logic_size": (
            m.get("thumb_scene_rect") is not None
            and abs(m["thumb_scene_rect"][2] - THUMB_DISPLAY_W) <= 0.6
            and abs(m["thumb_scene_rect"][3] - THUMB_DISPLAY_H) <= 0.6),
        # 补了 DPR 的那张：逻辑宽 == 目标 160（容差 0.6 吸收 scaled 取整误差）
        "pixmap_treated_logic": (
            m.get("thumb_treated_w") is not None
            and abs(m["thumb_treated_w"] - THUMB_DISPLAY_W) <= 0.6),
        # 对照：没补 DPR 的那张，在 dpr>1 时必然比处理后宽（这正是「偏大发虚」的来源）
        "pixmap_untreated_wider": (
            (m["thumb_untreated_w"] > m["thumb_treated_w"] + 0.5)
            if (m.get("thumb_untreated_w") is not None
                and m.get("thumb_treated_w") is not None and dpr > 1.0)
            else True),
    }
    m["ok"] = all(m["checks"].values())
    return m


# --------------------------------------------------------------------------
# GUI 模式：不传参数时真开一个窗口，肉眼看一眼三件事是否成立
# --------------------------------------------------------------------------
def run_gui():
    from PySide6.QtWidgets import QApplication, QMainWindow, QPlainTextEdit, \
        QPushButton, QSplitter, QWidget, QVBoxLayout
    from PySide6.QtGui import QPixmap

    _install_excepthook()
    app = QApplication.instance() or QApplication(sys.argv)

    ff = find_ffmpeg()
    pix = None
    if ff:
        import tempfile as _tf
        _w = _tf.mkdtemp(prefix="probe_gfx_gui_")
        _png = os.path.join(_w, "t.png")
        run_ffmpeg([ff, "-y", "-f", "lavfi", "-i",
                    "color=c=navy:s=%dx%d:d=1" % (THUMB_W, THUMB_H),
                    "-frames:v", "1", _png])
        if os.path.isfile(_png):
            pix = QPixmap(_png)
            dpr = app.primaryScreen().devicePixelRatio()
            pix = pix.scaled(int(THUMB_DISPLAY_W * dpr), int(THUMB_DISPLAY_H * dpr))
            pix.setDevicePixelRatio(dpr)

    view, _parts = build_canvas_view(thumb_pixmap=pix)

    report = QPlainTextEdit()
    report.setReadOnly(True)

    win = QMainWindow()
    central = QWidget()
    lay = QVBoxLayout(central)
    btn = QPushButton("跑一次三坑自检（含 ffmpeg 子进程）")
    lay.addWidget(btn)
    split = QSplitter()
    split.addWidget(view)
    split.addWidget(report)
    lay.addWidget(split)
    win.setCentralWidget(central)
    win.resize(900, 520)
    win.setWindowTitle("最小 QGraphicsView 探针")

    def _go():
        try:
            m = collect_metrics()
        except Exception as e:
            report.setPlainText("自检异常：%s" % e)
            return
        lines = ["退出结论：%s" % ("全通过" if m.get("ok") else "有不过的项")]
        for k, v in m["checks"].items():
            lines.append("  %s %s" % ("OK  " if v else "FAIL", k))
        lines.append("")
        for k in ("frozen", "dpr", "logical_dpi", "physical_dpi",
                  "viewport_size", "scene_rect", "node_rects",
                  "proxy_widget_size", "proxy_scene_rect", "thumb_scene_rect",
                  "grab_logical", "grab_dpr", "grab_physical", "expect_physical",
                  "ffmpeg_version_line", "ffmpeg_version_rc", "ffmpeg_noconsole_rc"):
            lines.append("%s = %s" % (k, m.get(k)))
        report.setPlainText("\n".join(lines))

    btn.clicked.connect(_go)
    win.show()
    _go()
    return app.exec()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    selftest = "--selftest" in argv
    json_path = None
    for i, a in enumerate(argv):
        if a == "--json" and i + 1 < len(argv):
            json_path = argv[i + 1]
        if a == "--scale" and i + 1 < len(argv):
            os.environ["QT_SCALE_FACTOR"] = argv[i + 1]

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not selftest:
        return run_gui()

    # 自检必须在 QApplication 之前定好平台（设晚了不生效）
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        m = collect_metrics()
    except Exception as e:
        import traceback
        self_emit("[FATAL] " + traceback.format_exc())
        m = {"ok": False, "checks": {}, "fatal": "%s: %s" % (type(e).__name__, e)}

    for k, v in m.get("checks", {}).items():
        self_emit("%s %s" % ("OK  " if v else "FAIL", k))
    for k in ("frozen", "meipass", "dpr", "logical_dpi", "physical_dpi",
              "scale_factor_env", "viewport_size", "scene_rect", "node_rects",
              "proxy_widget_size", "proxy_scene_rect", "thumb_scene_rect",
              "thumb_raw_size", "thumb_logical_before", "thumb_logical_after",
              "thumb_untreated_w", "thumb_treated_w",
              "grab_logical", "grab_dpr", "grab_physical", "expect_physical",
              "ffmpeg_path", "ffmpeg_version_rc", "ffmpeg_version_line",
              "ffmpeg_thumb_rc", "thumb_bytes", "ffmpeg_noconsole_rc",
              "ffmpeg_noconsole_err"):
        self_emit("%s = %s" % (k, m.get(k)))
    self_emit("RESULT ok=%s" % m.get("ok"))

    if json_path:
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(m, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self_emit("写 JSON 失败：%s" % e)
    return 0 if m.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
