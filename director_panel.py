"""导演台面板（小臭内嵌工作台 · 多步编排版）。

交互参照成熟项目（OpenMontage 多 Agent 编排 + 每步人工确认）的方式重做：
主题 → ① 剧本（可编辑/重写）→ ② 分镜（逐镜可编辑/增删）→ ③ 逐镜生成
（每镜实时出关键帧预览 + 可播放 + 可单镜修改/重生成）→ ④ 合成成片（可预览）。

UI 壳只做参数采集、分步编排与结果展示；真正工作委托 video_pipeline.VideoPipeline
的分阶段接口（prepare / gen_story / gen_shots / generate_all_clips / regenerate_clip / merge），
每个阶段跑完停在 UI 上等用户确认，才进入下一步。

build_director_panel(app): 在 app.director_page 上构建 UI。app 为 MainWindow 实例。
"""
import os
import sys
import json
import copy
import shutil
import logging
import traceback
import functools
import threading

log = logging.getLogger("dsdesktop")

# v4.108 M-07：导演指令派发互斥锁——app._director_agent_event 是单实例通道，
# 两个隔离会话并发调用 director_* 工具时后一个会覆盖前一个的 Event，导致前一个
# 永远等不到回执（静默超时）。此锁串行化「emit+等待」临界段，杜绝覆盖。
_DIR_DISPATCH_LOCK = threading.Lock()

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QLineEdit, QSpinBox,
    QComboBox, QGroupBox, QFileDialog, QCheckBox, QListWidget, QListWidgetItem,
    QStackedWidget, QScrollArea, QWidget, QGridLayout, QFrame, QSizePolicy,
    QInputDialog, QDialog, QRadioButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QTabWidget, QSlider, QDoubleSpinBox, QSplitter,
)
from PySide6.QtGui import QPixmap, QIcon, QDesktopServices, QColor
from PySide6.QtCore import Qt, QSize, QThread, Signal, QUrl, QObject

from ui import THEME
from ui import _brief_err
from ui import clamp_dialog_to_screen as _clamp_dlg
from ui import RES_PRESETS   # v4.188 P2-7：分辨率预设唯一来源（原本地一份已删，防三处漂移）
from config import APP_DIR, WORKSPACE_DIR
from director_web import (
    DirectorWebView, register_localres_scheme,
    clip_card_html, keyframe_card_html, character_card_html, merge_card_html,
    clue_card_html,
    project_token, set_project_token,
)


def _fit_dlg(dlg, w, h):
    """v4.180.0：按屏幕可用区钳制弹窗尺寸 + 居中，保证底部按钮可见可点。

    替代裸 dlg.resize(w, h)：硬编码尺寸在笔记本缩放屏（可用高度可能只有
    600~880px）下会把底部按钮顶到屏幕外。
    """
    try:
        _clamp_dlg(dlg, want_w=w, want_h=h)
    except Exception:
        try:
            dlg.resize(w, h)
        except Exception:
            pass
    return dlg


# ---------- 异常兜底装饰器 ----------
# PySide6 信号槽（含 QThread.run / 跨线程 queued 槽）里的未捕获异常
# 不走 sys.excepthook，会直接打到 stderr 然后 PyQt 硬崩。这里统一兜底：
# 崩了也不再静默死，而是写 app.log + 在面板状态栏提示，便于定位。
def _safe(fn):
    """信号槽异常兜底。

    PySide6 信号槽（含 QThread.run / 跨线程 queued 槽）里的未捕获异常不走
    sys.excepthook，会直接打到 stderr 然后硬崩。这里统一兜底：写 app.log +
    面板状态栏提示。

    v4.166.0 增强：捕获后**必须做紧急收口**（幂等）—— 否则任何一个 *_ready
    回调抛异常，界面都会永久停在「生成中」（而后台线程早已结束），用户只能重启。
    _safe 仍只是最后一道保护，不代替正常的任务收口。
    """
    @functools.wraps(fn)
    def _w(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            app = a[0] if a else None
            msg = f"{fn.__name__} 异常：{e}"
            try:
                if app is not None and hasattr(app, "director_status"):
                    _set_status(app, msg, err=True)
                if app is not None and hasattr(app, "director_log"):
                    _log(app, "❌ " + msg)
            except Exception:
                pass
            try:  # 路由到 main.py 装的 crash logger（写 app.log）
                if sys.excepthook:
                    sys.excepthook(type(e), e, e.__traceback__)
            except Exception:
                pass
            # v4.166.0：强制收口 —— 让界面重新可用，绝不假装成功
            try:
                if app is not None:
                    _emergency_recover(app, fn.__name__)
            except Exception:
                pass
            return None
    return _w


def _emergency_recover(app, where=""):
    """回调崩溃后的紧急收口（幂等）：解锁界面 + 转失败态 + 尽力保存现场。

    只做「让用户能继续操作」这一件事：不重试、不改已有产出、不假装成功。
    每一小步都独立 try —— 收口本身绝不能再抛出。
    """
    # 1) 解锁运行锁（本就空闲则不动，免得误清别的标记）
    try:
        if getattr(app, "director_busy", False):
            _set_busy(app, False)
    except Exception:
        pass
    # 2) 阶段转 ERROR（不覆盖已经出片的 DONE）
    try:
        if getattr(app, "director_phase", "") != DirectorPhase.DONE:
            _set_director_phase(app, DirectorPhase.ERROR)
    except Exception:
        pass
    # 3) 标记失败回执 —— 让抓快照的 Agent 拿到 failed，而不是「无结论」
    try:
        if not getattr(app, "_director_agent_cancelled", False):
            app._director_agent_error = f"界面回调异常（{where}）"
    except Exception:
        pass
    # 4) 尽力保存现场（失败也不抛）
    try:
        _save_session(app)
    except Exception:
        pass
    # 5) 状态栏给出明确下一步，不让用户猜
    try:
        _set_status(app, f"⚠ 步骤异常已中止（{where}）—— 已解锁，可直接重试", err=True)
    except Exception:
        pass

def _style_items():
    """风格下拉项 [(显示名, key), ...]。

    v4.151：**不再在面板里自存一份表**。旧写法在 director_panel 与 video_pipeline
    各存一张风格表，一旦漂移就会出现「下拉框里有、提示词里没有」的静默失效 ——
    大哥的「水墨动画」就是这么丢的：他写在主题里，但两张表都根本没有水墨这一项。
    现在统一从 video_pipeline 的唯一来源派生。
    """
    try:
        from video_pipeline import style_items
        return style_items()
    except Exception:
        return [("写实 realistic", "realistic")]


STYLE_ITEMS = _style_items()

STEP_LABELS = ["主题", "剧本", "人物", "分镜", "关键帧", "生成", "合成"]


# ---------- 样式 ----------
# v4.182.0 收编：以下七函数改为 theme_qss 中心层兼容壳（真源已迁 theme_qss.py，
# 旧函数名保留 → 调用点零改动；惰性导入防循环依赖）。
def _btn_style():
    from theme_qss import btn_outline
    return btn_outline()


def _btn_accent_style():
    # 强调按钮 14px（分场景归档决策：主输入框/强调按钮专用值）
    from theme_qss import btn_primary, F, W
    return btn_primary(font_size=F["input"], weight=W["medium"],
                       disabled_color=THEME["dim"])


def _btn_danger_style():
    from theme_qss import btn_danger
    return btn_danger()


def _btn_small_style():
    from theme_qss import btn_small
    return btn_small()


def _edit_style():
    from theme_qss import edit_style
    return edit_style()


def _combo_style():
    from theme_qss import combo_style
    return combo_style()


def _chk_style():
    from theme_qss import chk_style
    return chk_style()


def _chip_style():
    """v4.112 UI 美化：增强选项改「圆角芯片」——无原生方块指示框，选中=蓝底胶囊。
    仅改视觉，isChecked() 语义零变化。"""
    return (
        f"QCheckBox{{background:{THEME['card']};color:{THEME['dim']};"
        f"border:1px solid {THEME['border']};border-radius:10px;"
        f"padding:4px 12px;font-size:12px;}}"
        f"QCheckBox:hover{{border-color:{THEME['border_highlight']};color:{THEME['text']};}}"
        f"QCheckBox:checked{{background:{THEME['accent']};color:white;"
        f"border-color:{THEME['accent']};font-weight:600;}}"
        f"QCheckBox::indicator{{width:0;height:0;}}")


def _segment_style(checked=False):
    """v4.112 UI 美化：「剧情短片 / 本人形象口播」互斥分段钮（QRadioButton）。
    checked=左侧首选段。仅改视觉，isChecked() 语义零变化。"""
    side_r = "border-top-right-radius:8px;border-bottom-right-radius:8px;" if not checked else ""
    side_l = "border-top-left-radius:8px;border-bottom-left-radius:8px;" if checked else ""
    if checked:
        return (f"QRadioButton{{background:{THEME['accent']};color:white;border:1px solid {THEME['accent']};"
                f"{side_l}padding:8px 12px;font-size:12px;font-weight:600;}}"
                f"QRadioButton::indicator{{width:0;height:0;}}")
    return (f"QRadioButton{{background:{THEME['card']};color:{THEME['dim']};"
            f"border:1px solid {THEME['border']};border-left:none;"
            f"{side_r}padding:8px 12px;font-size:12px;}}"
            f"QRadioButton:hover{{color:{THEME['text']};border-color:{THEME['border_highlight']};}}"
            f"QRadioButton::indicator{{width:0;height:0;}}")


def _line_style():
    return (f"QLineEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:6px;padding:4px 8px;font-size:12px;color:{THEME['text']};}}"
            f"QLineEdit:focus{{border:1px solid {THEME['accent']};}}")


# ---------- 后台线程（按阶段驱动 pipeline） ----------
class DirectorThread(QThread):
    log = Signal(str)
    status = Signal(str, bool)
    story_ready = Signal(str)
    shots_ready = Signal(object)
    characters_ready = Signal(object)
    clues_ready = Signal(object)
    keyframes_ready = Signal(object)
    clip_ready = Signal(int, str)
    clip_failed = Signal(int, str)
    clips_done = Signal(int, int, str)
    merge_ready = Signal(bool, str, str)
    error = Signal(str)

    def __init__(self, pipeline, task, feedback=None, idx=None, note=None):
        super().__init__()
        self.pipeline = pipeline
        self.task = task
        self.feedback = feedback
        self.idx = idx
        self.note = note

    def run(self):
        p = self.pipeline
        p.cb = {
            "log": lambda t: self.log.emit(t),
            "status": lambda t, e=False: self.status.emit(t, e),
        }
        try:
            if self.task == "story":
                p.gen_story(feedback=self.feedback)
                self.story_ready.emit(p.story)
            elif self.task == "shots":
                p.gen_shots(feedback=self.feedback)
                self.shots_ready.emit(p.shots)
            elif self.task == "characters":
                p.gen_characters(feedback=self.feedback)
                self.characters_ready.emit(p.characters)
            elif self.task == "clues":
                # 关键道具 / 场景资产追踪（抗崩坏 v3）
                p.gen_clues(feedback=self.feedback)
                self.clues_ready.emit(p.clues)
            elif self.task == "clue_one":
                name, image = p.regenerate_clue(self.idx, feedback=self.feedback)
                if image:
                    self.clues_ready.emit(p.clues)
                else:
                    self.error.emit(f"道具/资产「{name or self.idx + 1}」参考图重生成失败")
            elif self.task == "keyframes":
                p.gen_keyframes(feedback=self.feedback)
                self.keyframes_ready.emit(p.keyframes)
            elif self.task == "clips":
                def on_clip(i, path):
                    if path:
                        self.clip_ready.emit(i, path)
                    else:
                        self.clip_failed.emit(i, p.last_errors.get(i, "生成失败（未返回视频）"))
                ok = p.generate_all_clips(on_clip=on_clip)
                self.clips_done.emit(ok, len(p.shots), "逐镜生成完成")
            elif self.task == "clip_one":
                path = p.regenerate_clip(self.idx, feedback=self.note)
                if path:
                    self.clip_ready.emit(self.idx, path)
                else:
                    self.clip_failed.emit(self.idx, p.last_errors.get(self.idx, "生成失败（未返回视频）"))
                ok = sum(1 for x in p.clip_paths if x)
                self.clips_done.emit(ok, len(p.shots), "单镜重生成完成")
            elif self.task == "keyframe_one":
                # v4.106 对话框指令：只重生成第 idx 镜的关键帧
                path = p.regenerate_keyframe(self.idx, feedback=self.feedback)
                if path:
                    self.keyframes_ready.emit(p.keyframes)
                else:
                    self.error.emit(f"镜{self.idx + 1} 关键帧重生成失败")
            elif self.task == "character_one":
                # v4.106 对话框指令：只重生成第 idx 个角色的三视图
                name, views = p.regenerate_character(self.idx, feedback=self.feedback)
                if views:
                    self.characters_ready.emit(p.characters)
                else:
                    self.error.emit(f"角色「{name or self.idx + 1}」三视图重生成失败")
            elif self.task == "merge":
                out, err = p.merge()
                if out:
                    # v4.188 P2-6：成片消息明示缺镜——不用翻卡片才知道少了几镜
                    #（对照数字人面板 failed_segs 的既有明示做法）。
                    try:
                        _miss = sum(1 for x in (p.clip_paths or []) if not x)
                        _total = len(p.shots or [])
                    except Exception:
                        _miss = _total = 0
                    _note = (f"（{_total - _miss}/{_total} 镜，缺 {_miss} 镜"
                             f"——可回「生成」单镜补齐后重新合成）"
                             if (_miss and _total) else "")
                    # v4.188 P2-8：无声警告（若有）显式拼进成片消息，
                    # 用户不必翻日志才知道成片可能哑了
                    _warn = getattr(p, "last_audio_warn", None)
                    if _warn:
                        _note += f"；{_warn}"
                    self.merge_ready.emit(
                        True, f"成片完成：{os.path.basename(out)}{_note}", out)
                else:
                    self.merge_ready.emit(False, err or "合成失败", "")
        except Exception as e:
            self.error.emit(f"{self.task} 异常：{e}")
        finally:
            # v4.133.1：线程跑完必须把 cb 摘掉。
            # 否则 cb 里的 lambda 一直指向这个「已结束的 QThread」的信号——之后
            # 任何在别的线程（低清预览渲染）或 UI 线程（导出工程）里调用 p.log()，
            # 都会往一个 C++ 对象可能已销毁的 QThread emit → 随机崩。
            # 摘掉后 p.log() 回落到 print，安全。
            try:
                p.cb = {}
            except Exception:
                pass


class KfQueueThread(QThread):
    """v4.125 P2-3：后台串行抽帧队列——把 keyframe()（同步 ffmpeg，未命中缓存
    单次最长 30s）从 UI 线程挪走。恢复多镜项目时 N 帧排队串行抽，UI 不再冻结。

    item_done(idx, kf)：每帧抽完发一次（kf 为 PNG 路径或 None），
    回调里按 idx 定位卡片补图重渲染。all_done()：队列跑完。
    """
    item_done = Signal(int, object)
    all_done = Signal()

    def __init__(self, pipeline, jobs):
        super().__init__()
        self.pipeline = pipeline
        self.jobs = list(jobs or [])   # [(idx, clip_path), ...]
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            for idx, path in self.jobs:
                if self._cancelled:
                    break
                kf = None
                try:
                    kf = self.pipeline.keyframe(path)
                    if not (kf and os.path.isfile(kf)):
                        kf = None
                except Exception:
                    kf = None
                self.item_done.emit(idx, kf)
            self.all_done.emit()
        except Exception:
            pass


def _kick_kf_queue(app, jobs, on_item, on_done=None, cancel_old=False):
    """v4.125 P2-3：启动后台抽帧队列（串行）。

    jobs=[(idx, path)]；on_item(idx, kf) 在 UI 线程执行（更新卡片）。
    cancel_old=True 时先取消在跑的队列（恢复场景全部重建）；逐镜/合成场景
    追加式并存——线程完成自动从引用池移除，防 GC 早回收。
    """
    if cancel_old:
        for old in list(getattr(app, "director_kf_threads", []) or []):
            try:
                old.cancel()
                old.item_done.disconnect()
                old.all_done.disconnect()
            except Exception:
                pass
    if not jobs or getattr(app, "director_pipeline", None) is None:
        return
    th = KfQueueThread(app.director_pipeline, jobs)
    # v4.167.0（审查 §4）：回调前校验项目令牌 —— 旧项目的抽帧结果
    # 不得写进新项目的卡片（重置后旧线程晚到是真实会发生的）。
    _tok = project_token()

    def _guarded_item(i, k, _t=_tok):
        if _t != project_token():
            return
        on_item(i, k)

    th.item_done.connect(_guarded_item)
    if on_done:
        def _guarded_all_done(_t=_tok):
            if _t != project_token():
                return
            on_done()
        th.all_done.connect(_guarded_all_done)
    pool = getattr(app, "director_kf_threads", None)
    if pool is None:
        pool = []
        app.director_kf_threads = pool
    pool.append(th)
    th.all_done.connect(lambda t=th: (pool.remove(t) if t in pool else None))
    th.start()


class BgTaskThread(QThread):
    """v4.133.1：通用后台任务——把同步阻塞的活挪出 UI 线程。

    低清预览要真跑一遍 ffmpeg（12 镜十几秒到几十秒），放在 UI 线程里等于
    点一下按钮整个窗口假死、系统标「无响应」。这里跑完用 done 信号回 UI 线程。

    pipeline 不为空时会**临时接管它的日志回调**：p.cb 可能还绑着上一个
    DirectorThread 的信号（见 DirectorThread.run 的 finally），直接在本线程
    调用就是往别人的 QThread emit。这里换成自己的 logged 信号，跑完还原。
    """
    done = Signal(object, str)
    logged = Signal(str)

    def __init__(self, fn, pipeline=None):
        super().__init__()
        self._fn = fn
        self._pipeline = pipeline
        self._saved_cb = None

    def run(self):
        if self._pipeline is not None:
            try:
                self._saved_cb = self._pipeline.cb
                self._pipeline.cb = {"log": lambda t: self.logged.emit(str(t))}
            except Exception:
                self._saved_cb = None
        try:
            r = self._fn()
        except Exception as e:
            self._finish(None, str(e))
            return
        self._finish(r, "")

    def _finish(self, result, err):
        if self._pipeline is not None and self._saved_cb is not None:
            try:
                self._pipeline.cb = self._saved_cb
            except Exception:
                pass
        try:
            self.done.emit(result, err)
        except RuntimeError:
            pass


def _kick_bg(app, fn, on_done, pipeline=None, on_log=None):
    """起一个后台任务，结束回调在 UI 线程。返回线程对象（失败返回 None）。"""
    try:
        th = BgTaskThread(fn, pipeline)
        # v4.167.0（审查 §4）：同抽帧队列 —— 旧项目的预览结果不得写进新项目
        _tok = project_token()

        def _guarded_done(r, e, _t=_tok):
            if _t != project_token():
                return
            on_done(r, e)

        th.done.connect(_guarded_done)
        if on_log:
            th.logged.connect(on_log)
        pool = getattr(app, "director_bg_threads", None)
        if pool is None:
            pool = []
            app.director_bg_threads = pool
        pool.append(th)
        th.done.connect(lambda _r, _e, t=th: (pool.remove(t) if t in pool else None))
        th.start()
        return th
    except Exception:
        return None


def _cancel_all_director_bg(app, reason="项目已重置", stage="reset"):
    """v4.167.0（审查 §4 P1）：把导演台的后台任务统一收口（五步）。

    为什么需要：原来 `_director_reset` 只完整收口了主 director_thread，
    抽帧队列（KfQueueThread）与低清预览（BgTaskThread）**没有统一取消与隔离** ——
    已经重置成新项目后，旧项目的抽帧/预览任务晚到，仍可能往新卡片或
    新状态里写数据。

    五步（顺序不能换）：
      ① 广播取消   —— 让还在跑的尽早在检查点退出（协作式，不杀线程）
      ② 断开回调   —— 即便它跑完，也碰不到 UI
      ③ 标记孤儿   —— 留下"谁还在收尾"的痕迹，便于排查
      ④ 清空引用池 —— 新项目不再持有旧线程
      ⑤ 项目令牌   —— 调用方已换发；漏网的迟到结果会被令牌校验丢弃
    """
    killed = {"kf": 0, "bg": 0, "zombie": 0}
    for attr, key in (("director_kf_threads", "kf"),
                      ("director_bg_threads", "bg")):
        pool = getattr(app, attr, None) or []
        for th in list(pool):
            try:
                if hasattr(th, "cancel"):
                    th.cancel()             # ①
            except Exception:
                pass
            try:
                th.disconnect()             # ②
            except Exception:
                pass
            try:
                if th.isRunning():          # ③
                    killed["zombie"] += 1
            except Exception:
                pass
        killed[key] = len(pool)
        try:
            setattr(app, attr, [])          # ④
        except Exception:
            pass
    if killed["kf"] or killed["bg"] or killed["zombie"]:
        try:
            _log(app, f"🧹 后台任务已隔离（抽帧 {killed['kf']} / 预览 {killed['bg']}"
                      f"，其中仍在收尾 {killed['zombie']}）—— 旧结果不会再写进新项目")
        except Exception:
            pass
    return killed



# ---------- 面板构建 ----------
def build_director_panel(app):
    from theme_qss import scroll_transparent
    from theme_qss import label_body, label_micro, label_second, label_title_xl
    page = app.director_page

    # 主滚动区（防止内容溢出压到对话框）
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet(scroll_transparent())
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    body = QWidget()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(24, 16, 24, 20)
    lay.setSpacing(12)

    head = QLabel("导演台 · video-agent")
    head.setStyleSheet(label_title_xl())
    lay.addWidget(head)
    sub = QLabel("主题 → ①剧本（可改）→ ②人物三视图（角色锁定）→ ③分镜（逐镜可改）→ "
                 "④关键帧+场景图 → ⑤逐镜生成（每镜可预览/单镜改）→ ⑥合成成片。"
                 "每步都自动把人物三视图/关键帧作参照，减少人物与场景崩坏。")
    sub.setStyleSheet(label_second())
    lay.addWidget(sub)

    # 步骤指示器
    step_box = QWidget()
    step_lay = QHBoxLayout(step_box)
    step_lay.setContentsMargins(0, 0, 0, 0)
    step_lay.setSpacing(8)
    app.director_step_labels = []
    for i, name in enumerate(STEP_LABELS):
        lb = QLabel(f"{i} {name}")
        lb.setAlignment(Qt.AlignCenter)
        lb.setFixedHeight(28)
        lb.setStyleSheet(_step_style(i == 0))
        step_lay.addWidget(lb, 1)
        app.director_step_labels.append(lb)
        if i < len(STEP_LABELS) - 1:
            ar = QLabel("›")
            ar.setAlignment(Qt.AlignCenter)
            ar.setStyleSheet(f"color:{THEME['dim']};font-size:{THEME['font_body']};")
            step_lay.addWidget(ar)
    lay.addWidget(step_box)

    # ---------- 参数区（步骤0可改，开始后禁用） ----------
    app.director_inputs = []
    params = QGroupBox("创作参数")
    params.setStyleSheet(f"QGroupBox{{background:transparent;border:1px solid {THEME['border']};"
                         f"border-radius:10px;padding:16px 16px 12px;font-size:13px;color:{THEME['text']};}}")
    pl = QVBoxLayout(params)
    pl.setSpacing(12)

    # --- 主题 ---
    tlab = QLabel("视频主题 / 口播原稿（中文，越具体越好）")
    tlab.setStyleSheet(label_body())
    pl.addWidget(tlab)
    app.director_topic = QTextEdit()
    app.director_topic.setFixedHeight(64)
    app.director_topic.setPlaceholderText("例：一只柯基早上在院子里追蝴蝶的治愈微故事 / "
                                          "口播模式可贴原稿（≥60字或前缀「原稿：」走直通）")
    app.director_topic.setStyleSheet(_edit_style())
    pl.addWidget(app.director_topic)
    app.director_inputs.append(app.director_topic)

    def _spin(rng, val, suffix=""):
        s = QSpinBox()
        s.setRange(*rng)
        s.setValue(val)
        if suffix:
            s.setSuffix(suffix)
        s.setFixedHeight(34)
        s.setStyleSheet(_combo_style())
        return s

    # --- 第一行：分镜数 / 每镜时长 / 智能开关 ---
    row1 = QHBoxLayout()
    row1.setSpacing(12)
    nlab = QLabel("分镜数")
    nlab.setStyleSheet(label_body())
    nlab.setFixedWidth(50)
    row1.addWidget(nlab)
    # v4.154：上限放宽到 40（对齐「AI 智能分镜」可能给出的丰富科普/纪录片上限）
    app.director_n = _spin((1, 40), 4, " 镜")
    app.director_n.setFixedWidth(90)
    app.director_n.setToolTip("「AI 智能分镜」开启时：此为兜底默认，实际镜数由 AI 按剧本决定；\n关闭时：严格按此数拆分。")
    row1.addWidget(app.director_n)
    app.director_inputs.append(app.director_n)

    dlab = QLabel("每镜")
    dlab.setStyleSheet(label_body())
    dlab.setFixedWidth(40)
    row1.addWidget(dlab)
    # 新版 agnes-video-2.5-flash 时长合法范围 4~12 秒（旧版 3~16s）
    app.director_duration = _spin((4, 12), 5, " 秒")
    app.director_duration.setFixedWidth(90)
    app.director_duration.setToolTip("「AI 智能分镜」开启时：此为每镜时长兜底默认，AI 可按内容给每镜不同的 4~12 秒；\n关闭时：所有镜统一用此秒数。")
    row1.addWidget(app.director_duration)
    app.director_inputs.append(app.director_duration)

    # v4.154：AI 智能分镜开关（默认勾选）。开启后 AI 自主决定分镜数量与每镜时长，
    # 下拉框降为兜底默认；关闭则严格按下拉框硬执行（保留旧行为兜底）。
    app.director_smart = QCheckBox("🤖 AI 智能分镜")
    app.director_smart.setChecked(True)
    app.director_smart.setStyleSheet(label_body())
    app.director_smart.setToolTip("勾选：AI 按剧本情节自主决定分镜数量与每镜时长（4~12秒），下拉框作为兜底默认；\n取消：完全按下拉框的「分镜数 / 每镜秒数」硬执行。")
    row1.addWidget(app.director_smart)

    rlab = QLabel("分辨率")
    rlab.setStyleSheet(label_body())
    rlab.setFixedWidth(50)
    row1.addWidget(rlab)
    app.director_resolution = QComboBox()
    for label, val in RES_PRESETS:
        app.director_resolution.addItem(label, val)
    app.director_resolution.setCurrentIndex(2)
    app.director_resolution.setFixedHeight(34)
    app.director_resolution.setStyleSheet(_combo_style())
    row1.addWidget(app.director_resolution, 1)
    app.director_inputs.append(app.director_resolution)
    pl.addLayout(row1)

    # --- 第二行：风格 + 复选框 ---
    row2 = QHBoxLayout()
    row2.setSpacing(12)
    slab = QLabel("风格")
    slab.setStyleSheet(label_body())
    slab.setFixedWidth(40)
    row2.addWidget(slab)
    app.director_style = QComboBox()
    for label, val in STYLE_ITEMS:
        app.director_style.addItem(label, val)
    app.director_style.setCurrentIndex(0)
    app.director_style.setFixedHeight(34)
    app.director_style.setStyleSheet(_combo_style())
    app.director_style.setMinimumWidth(160)
    row2.addWidget(app.director_style)
    app.director_inputs.append(app.director_style)

    row2.addSpacing(20)
    # v4.112 UI 美化：模式二选一分段钮（剧情短片 / 本人形象口播）。
    # portrait 改用 QRadioButton，isChecked()/setChecked() 接口与 QCheckBox 一致，
    # 下游读取点（L1087/L1921/L2057）语义零变化。
    app.director_dialogue = QCheckBox("台词")
    app.director_portrait = QRadioButton("本人形象口播")
    mode_seg = QWidget()
    seg_lay = QHBoxLayout(mode_seg)
    seg_lay.setContentsMargins(0, 0, 0, 0)
    seg_lay.setSpacing(0)
    app.director_mode_film = QRadioButton("剧情短片")
    app.director_mode_film.setChecked(True)
    app.director_mode_film.setStyleSheet(_segment_style(checked=True))
    app.director_mode_film.setCursor(Qt.PointingHandCursor)
    app.director_mode_film.setToolTip("AI 编故事 + 分镜，走完整导演流水线")
    app.director_portrait.setStyleSheet(_segment_style(checked=False))
    app.director_portrait.setCursor(Qt.PointingHandCursor)
    app.director_portrait.setToolTip("锁本人照片做口播形象，跳过三视图/关键帧人物生成，台词走原稿直通")
    seg_lay.addWidget(app.director_mode_film)
    seg_lay.addWidget(app.director_portrait)
    # 两枚 QRadioButton 同父（mode_seg）默认互斥，无需手动联动；
    # 默认态在布局挂载后统一设置（挂载前 setChecked 会被父级互斥组重置）
    app.director_mode_film.setChecked(True)
    row2.addWidget(mode_seg)
    mode_hint = QLabel("模式")
    mode_hint.setStyleSheet(label_micro())
    row2.addWidget(mode_hint)

    row2.addSpacing(14)
    # 内容/增强项 → 圆角芯片
    app.director_relay = QCheckBox("尾帧接力")
    app.director_relay.setChecked(True)
    app.director_subtitle = QCheckBox("烧录字幕")
    app.director_subtitle.setChecked(True)
    # VLM 质检：调用 DeepSeek 视觉模型审查关键帧，会产生费用并延长生成时间，
    # 因此必须给用户开关（默认开，因为它是抗崩坏的关键一环）。
    app.director_vision_review = QCheckBox("VLM 质检")
    app.director_vision_review.setChecked(True)
    app.director_vision_review.setToolTip(
        "生成关键帧后用 DeepSeek 视觉模型审查人物/场景是否崩坏，"
        "不通过自动带诊断重生成（每镜最多重试 2 次）。\n"
        "会产生 DeepSeek 调用费用并延长生成时间；关闭后仅靠参考图锁定。")
    # v4.127 多参考图增强：逐镜自动装配最多 5 张参考图（关键帧/角色三视图/场景/
    # 尾帧/道具），角色与场景图缺失时从资产库取存货补位。默认开。
    app.director_multiref = QCheckBox("多参考图增强")
    app.director_multiref.setChecked(True)
    app.director_multiref.setToolTip(
        "逐镜自动装配最多 5 张参考图：本镜关键帧 + 角色三视图（正/侧）+ 场景图 + "
        "上一镜尾帧 + 关键道具。\n角色/场景图本任务没生成过时，自动去资产库找同项目存货补位。\n"
        "关闭后退回旧的单张关键帧驱动链路（一致性变差，但出图更快）。")
    app.director_dialogue.setToolTip("让画面人物开口说台词（口播模式恒开，无需勾选）")
    app.director_dialogue.setStyleSheet(_chip_style())
    # v4.132 Prompt 预审：生成关键帧/视频前先给可编辑草稿，确认后才烧钱。
    # 不进 director_inputs —— 开拍后仍要能开（它在每步采用时才用得上）。
    app.director_preview = QCheckBox("Prompt 预审")
    app.director_preview.setChecked(True)
    app.director_preview.setToolTip(
        "生成关键帧 / 视频前，先把每一镜的提示词列出来给你改，确认后才调用生成接口。\n"
        "改过的稿子之后重生成这一镜也继续用（不会悄悄用回老提示词）。")
    for c in (app.director_relay, app.director_subtitle, app.director_vision_review,
              app.director_multiref):
        c.setStyleSheet(_chip_style())
        row2.addWidget(c)
        app.director_inputs.append(c)
    app.director_preview.setStyleSheet(_chip_style())
    row2.addWidget(app.director_preview)
    row2.addStretch(1)
    pl.addLayout(row2)

    # --- 2.5 行：模式/内容开关灰字说明 ---
    app.director_mode_hint = QLabel(
        "剧情短片：AI 编故事分镜，走完整流水线 · 本人形象口播：锁照片说话，台词默认直通（台词芯片仅在短片模式下生效）")
    app.director_mode_hint.setStyleSheet(f"font-size:{THEME['font_micro']};color:{THEME['faint']};padding-left:4px;")
    pl.addWidget(app.director_mode_hint)

    # --- 第三行：参考图 ---
    ref = QHBoxLayout()
    ref.setSpacing(12)
    app.director_ref_btn = QPushButton("选择参考图（首帧锁定 / 口播形象）")
    app.director_ref_btn.setFixedHeight(34)
    app.director_ref_btn.setCursor(Qt.PointingHandCursor)
    app.director_ref_btn.setStyleSheet(_btn_style())
    app.director_ref_btn.clicked.connect(lambda: _director_pick_ref(app))
    ref.addWidget(app.director_ref_btn)
    app.director_inputs.append(app.director_ref_btn)
    app.director_ref_preview = QLabel("未选")
    app.director_ref_preview.setFixedSize(72, 72)
    app.director_ref_preview.setAlignment(Qt.AlignCenter)
    app.director_ref_preview.setStyleSheet(
        f"QLabel{{background:{THEME['bg']};border:1px solid {THEME['border']};"
        f"border-radius:8px;color:{THEME['dim']};font-size:{THEME['font_micro']};}}")
    ref.addWidget(app.director_ref_preview)
    app.director_ref_label = QLabel("")
    app.director_ref_label.setStyleSheet(label_second())
    ref.addWidget(app.director_ref_label, 1)
    app.director_ref_image = None
    pl.addLayout(ref)

    lay.addWidget(params)
    app.director_params_box = params

    # 动作行（开始 / 停止 / 重新开始 / 状态）
    act = QHBoxLayout()
    act.setSpacing(12)
    app.director_go = QPushButton("开始导演")
    app.director_go.setFixedHeight(38)
    app.director_go.setCursor(Qt.PointingHandCursor)
    app.director_go.setStyleSheet(_btn_accent_style())
    app.director_go.clicked.connect(lambda: _director_start(app))
    act.addWidget(app.director_go)
    app.director_stop = QPushButton("停止")
    app.director_stop.setFixedHeight(38)
    app.director_stop.setCursor(Qt.PointingHandCursor)
    app.director_stop.setStyleSheet(_btn_style())
    app.director_stop.setEnabled(False)
    app.director_stop.clicked.connect(lambda: _director_stop(app))
    act.addWidget(app.director_stop)
    app.director_reset = QPushButton("↺ 重新开始")
    app.director_reset.setFixedHeight(38)
    app.director_reset.setCursor(Qt.PointingHandCursor)
    app.director_reset.setStyleSheet(_btn_style())
    app.director_reset.clicked.connect(lambda: _director_reset(app))
    act.addWidget(app.director_reset)
    # v4.132 专业导演台：项目制作规格（Flova「文档区」同款）
    app.director_spec_btn = QPushButton("项目设定")
    app.director_spec_btn.setFixedHeight(38)
    app.director_spec_btn.setCursor(Qt.PointingHandCursor)
    app.director_spec_btn.setStyleSheet(_btn_style())
    app.director_spec_btn.setToolTip(
        "写一次全片规格：故事设定 / 视觉风格 / 镜头语言 / 禁用内容 / 输出要求。\n"
        "之后剧本·分镜·人物·道具·关键帧·视频每一步都自动带上，不用每轮重说。")
    app.director_spec_btn.clicked.connect(lambda: _open_project_spec(app))
    act.addWidget(app.director_spec_btn)
    # v4.132 媒体库：本片全部素材一览（含「未关联」标记）
    app.director_media_btn = QPushButton("媒体库")
    app.director_media_btn.setFixedHeight(38)
    app.director_media_btn.setCursor(Qt.PointingHandCursor)
    app.director_media_btn.setStyleSheet(_btn_style())
    app.director_media_btn.setToolTip("本片全部素材：人物三视图 / 道具 / 关键帧 / 场景图 / 片段 / 成片，"
                                      "标注各素材挂在哪一镜，可预览与设为参考图。")
    app.director_media_btn.clicked.connect(lambda: _open_media_library(app))
    act.addWidget(app.director_media_btn)
    act.addStretch(1)
    lay.addLayout(act)
    app.director_status = QLabel("")
    app.director_status.setStyleSheet(label_second())
    lay.addWidget(app.director_status)

    # ---------- 步骤内容（堆叠） ----------
    app.director_stack = QStackedWidget()
    lay.addWidget(app.director_stack, 1)

    # 页1：剧本
    story_page = QWidget()
    sl = QVBoxLayout(story_page)
    sl.setContentsMargins(0, 0, 0, 0)
    sl.setSpacing(12)
    shint = QLabel("① 剧本已生成。可直接编辑下面文字，或点「✎ 重写剧本」→ 在下方「导演对话」里补一句意见。"
                   "满意后点「采用剧本」，也可以在对话框里直接说「采用」。")
    shint.setStyleSheet(label_second())
    sl.addWidget(shint)
    app.director_story_edit = QTextEdit()
    app.director_story_edit.setReadOnly(False)
    app.director_story_edit.setStyleSheet(_edit_style())
    sl.addWidget(app.director_story_edit, 1)
    sbtns = QHBoxLayout()
    sbtns.setSpacing(12)
    app.director_story_revise = QPushButton("✎ 重写剧本（去对话框说）")
    app.director_story_revise.setFixedHeight(36)
    app.director_story_revise.setStyleSheet(_btn_style())
    app.director_story_revise.clicked.connect(lambda: _revise_story(app))
    app.director_story_adopt = QPushButton("✓ 采用剧本 → 去分镜")
    app.director_story_adopt.setFixedHeight(36)
    app.director_story_adopt.setCursor(Qt.PointingHandCursor)
    app.director_story_adopt.setStyleSheet(_btn_accent_style())
    app.director_story_adopt.clicked.connect(lambda: _adopt_story(app))
    sbtns.addWidget(app.director_story_revise)
    sbtns.addStretch(1)
    sbtns.addWidget(app.director_story_adopt)
    sl.addLayout(sbtns)
    app.director_stack.addWidget(story_page)

    # 页1.5：人物三视图（角色锁定，抗崩坏）
    characters_page = QWidget()
    cl0 = QVBoxLayout(characters_page)
    cl0.setContentsMargins(0, 0, 0, 0)
    cl0.setSpacing(12)
    chint = QLabel("② 人物三视图已生成。每个角色含正面/侧面/背面三视图，可据此判断人物是否会崩；"
                   "满意后点「采用人物 → 去分镜」。不满意可「✎ 重新生成人物」→ 在下方「导演对话」"
                   "里说清改谁、怎么改（如「角色1换成红衣服」），也可以点角色卡上的「✎ 说一句怎么改」。")
    chint.setWordWrap(True)
    chint.setStyleSheet(label_second())
    cl0.addWidget(chint)
    characters_scroll = QScrollArea()
    characters_scroll.setWidgetResizable(True)
    characters_scroll.setStyleSheet(scroll_transparent())
    app.director_characters_web = DirectorWebView(THEME, lambda s: _on_web_action(app, s))
    characters_scroll.setWidget(app.director_characters_web)
    cl0.addWidget(characters_scroll, 1)
    # 线索子区：关键道具 / 场景资产（clue，抗崩坏 v3，参考 ArcReel）
    clue_hint = QLabel("附加·可选：关键道具 / 场景资产。人物锁只锁人，跨镜复用的道具与陈设"
                       "（同一把剑、同一块招牌）照样会漂移。抽取后每件生成一张参考图，"
                       "并写进逐镜提示词与参考图；不抽取则完全不影响原流程。")
    clue_hint.setWordWrap(True)
    clue_hint.setStyleSheet(label_second())
    cl0.addWidget(clue_hint)
    app.director_clues_web = DirectorWebView(THEME, lambda s: _on_web_action(app, s))
    app.director_clues_web.setMinimumHeight(200)
    app.director_clues_web.render_empty("尚未抽取关键道具 / 场景资产（可选）")
    cl0.addWidget(app.director_clues_web)

    cbtns = QHBoxLayout()
    cbtns.setSpacing(12)
    app.director_clues_btn = QPushButton("🔎 抽取关键道具/场景资产")
    app.director_clues_btn.setFixedHeight(36)
    app.director_clues_btn.setStyleSheet(_btn_style())
    app.director_clues_btn.clicked.connect(lambda: _gen_clues(app))
    app.director_characters_revise = QPushButton("✎ 重新生成人物（去对话框说）")
    app.director_characters_revise.setFixedHeight(36)
    app.director_characters_revise.setStyleSheet(_btn_style())
    app.director_characters_revise.clicked.connect(lambda: _revise_characters(app))
    app.director_characters_adopt = QPushButton("✓ 采用人物 → 去分镜")
    app.director_characters_adopt.setFixedHeight(36)
    app.director_characters_adopt.setCursor(Qt.PointingHandCursor)
    app.director_characters_adopt.setStyleSheet(_btn_accent_style())
    app.director_characters_adopt.clicked.connect(lambda: _adopt_characters(app))
    cbtns.addWidget(app.director_characters_revise)
    cbtns.addWidget(app.director_clues_btn)
    cbtns.addStretch(1)
    cbtns.addWidget(app.director_characters_adopt)
    cl0.addLayout(cbtns)
    app.director_stack.addWidget(characters_page)
    app.director_character_cards = []

    # 页2：分镜
    shots_page = QWidget()
    shl = QVBoxLayout(shots_page)
    shl.setContentsMargins(0, 0, 0, 0)
    shl.setSpacing(12)
    shhint = QLabel("② 分镜已生成。可逐镜修改「中文/英文提示词/运镜/台词/场景」，也可删镜或加镜；"
                    "满意后点「采用分镜 → 生成」。要让分镜整体重排，点「✎ 重排分镜」→ 在下方"
                    "「导演对话」里说怎么改（如「开头太慢，第一镜给个特写」）。")
    shhint.setStyleSheet(label_second())
    shl.addWidget(shhint)
    shots_scroll = QScrollArea()
    shots_scroll.setWidgetResizable(True)
    shots_scroll.setStyleSheet(scroll_transparent())
    app.director_shots_body = QWidget()
    app.director_shots_layout = QVBoxLayout(app.director_shots_body)
    app.director_shots_layout.setContentsMargins(0, 0, 0, 0)
    app.director_shots_layout.setSpacing(8)
    shots_scroll.setWidget(app.director_shots_body)
    shl.addWidget(shots_scroll, 1)
    shbtns = QHBoxLayout()
    shbtns.setSpacing(12)
    app.director_shots_revise = QPushButton("✎ 重排分镜（去对话框说）")
    app.director_shots_revise.setFixedHeight(36)
    app.director_shots_revise.setStyleSheet(_btn_style())
    app.director_shots_revise.clicked.connect(lambda: _revise_shots(app))
    app.director_shots_add = QPushButton("＋ 加一镜")
    app.director_shots_add.setFixedHeight(36)
    app.director_shots_add.setStyleSheet(_btn_style())
    app.director_shots_add.clicked.connect(lambda: _add_shot_row(app))
    app.director_shots_adopt = QPushButton("✓ 采用分镜 → 去生成")
    app.director_shots_adopt.setFixedHeight(36)
    app.director_shots_adopt.setCursor(Qt.PointingHandCursor)
    app.director_shots_adopt.setStyleSheet(_btn_accent_style())
    app.director_shots_adopt.clicked.connect(lambda: _adopt_shots(app))
    shbtns.addWidget(app.director_shots_revise)
    shbtns.addWidget(app.director_shots_add)
    shbtns.addStretch(1)
    shbtns.addWidget(app.director_shots_adopt)
    shl.addLayout(shbtns)
    app.director_stack.addWidget(shots_page)

    # 页2.5：分镜关键帧 + 场景图（每镜首帧参照，抗崩坏）
    keyframes_page = QWidget()
    kl = QVBoxLayout(keyframes_page)
    kl.setContentsMargins(0, 0, 0, 0)
    kl.setSpacing(12)
    khint = QLabel("④ 分镜关键帧+场景图已生成。每镜一张首帧参照图，逐镜生成时会自动作为首帧注入，"
                   "人物与场景更不易崩坏；满意后点「采用关键帧 → 去生成」。"
                   "只想改某一镜 → 点那张卡上的「✎ 说一句怎么改」（如「改成雨夜，灯笼亮起来」），"
                   "要整批重来才点「✎ 重新生成关键帧」。")
    khint.setWordWrap(True)
    khint.setStyleSheet(label_second())
    kl.addWidget(khint)
    keyframes_scroll = QScrollArea()
    keyframes_scroll.setWidgetResizable(True)
    keyframes_scroll.setStyleSheet(scroll_transparent())
    app.director_keyframes_web = DirectorWebView(THEME, lambda s: _on_web_action(app, s))
    keyframes_scroll.setWidget(app.director_keyframes_web)
    kl.addWidget(keyframes_scroll, 1)
    kbtns = QHBoxLayout()
    kbtns.setSpacing(12)
    app.director_keyframes_revise = QPushButton("✎ 重新生成关键帧（去对话框说）")
    app.director_keyframes_revise.setFixedHeight(36)
    app.director_keyframes_revise.setStyleSheet(_btn_style())
    app.director_keyframes_revise.clicked.connect(lambda: _revise_keyframes(app))
    app.director_keyframes_adopt = QPushButton("✓ 采用关键帧 → 去生成")
    app.director_keyframes_adopt.setFixedHeight(36)
    app.director_keyframes_adopt.setCursor(Qt.PointingHandCursor)
    app.director_keyframes_adopt.setStyleSheet(_btn_accent_style())
    app.director_keyframes_adopt.clicked.connect(lambda: _adopt_keyframes(app))
    kbtns.addWidget(app.director_keyframes_revise)
    kbtns.addStretch(1)
    kbtns.addWidget(app.director_keyframes_adopt)
    kl.addLayout(kbtns)
    app.director_stack.addWidget(keyframes_page)
    app.director_keyframe_cards = []

    # 页3：逐镜生成
    clips_page = QWidget()
    cl = QVBoxLayout(clips_page)
    cl.setContentsMargins(0, 0, 0, 0)
    cl.setSpacing(12)
    app.director_clips_progress = QLabel("⑤ 准备逐镜生成…")
    app.director_clips_progress.setStyleSheet(label_second())
    cl.addWidget(app.director_clips_progress)
    clips_scroll = QScrollArea()
    clips_scroll.setWidgetResizable(True)
    clips_scroll.setStyleSheet(scroll_transparent())
    app.director_clips_web = DirectorWebView(THEME, lambda s: _on_web_action(app, s))
    clips_scroll.setWidget(app.director_clips_web)
    cl.addWidget(clips_scroll, 1)
    clbtns = QHBoxLayout()
    clbtns.setSpacing(12)
    app.director_clips_regen_all = QPushButton("↺ 全部重新生成")
    app.director_clips_regen_all.setFixedHeight(36)
    app.director_clips_regen_all.setStyleSheet(_btn_style())
    app.director_clips_regen_all.clicked.connect(lambda: _regenerate_all(app))
    app.director_clips_adopt = QPushButton("✓ 全部就绪 → 去合成")
    app.director_clips_adopt.setFixedHeight(36)
    app.director_clips_adopt.setCursor(Qt.PointingHandCursor)
    app.director_clips_adopt.setStyleSheet(_btn_accent_style())
    app.director_clips_adopt.clicked.connect(lambda: _adopt_clips(app))
    clbtns.addWidget(app.director_clips_regen_all)
    clbtns.addStretch(1)
    clbtns.addWidget(app.director_clips_adopt)
    cl.addLayout(clbtns)
    app.director_stack.addWidget(clips_page)
    app.director_clips_state = []

    # 页4：合成
    merge_page = QWidget()
    ml = QVBoxLayout(merge_page)
    ml.setContentsMargins(0, 0, 0, 0)
    ml.setSpacing(12)
    mhint = QLabel("⑥ 全部片段已生成。可回「生成」步骤单镜修改，或直接点「合成成片」。")
    mhint.setStyleSheet(label_second())
    ml.addWidget(mhint)
    app.director_merge_web = DirectorWebView(THEME, lambda s: _on_web_action(app, s))
    ml.addWidget(app.director_merge_web, 1)
    app.director_merge_web.render_empty("尚未合成")
    mbtns = QHBoxLayout()
    mbtns.setSpacing(12)
    # v4.133 可视化时间线（重排/裁切/转场）+ 音频轨（BGM/闪避）+ 字幕样式
    app.director_timeline_btn = QPushButton("时间线编排")
    app.director_timeline_btn.setFixedHeight(38)
    app.director_timeline_btn.setCursor(Qt.PointingHandCursor)
    app.director_timeline_btn.setStyleSheet(_btn_style())
    app.director_timeline_btn.setToolTip("成片前最后一道编排：调镜序 / 裁入出点 / 加转场，"
                                         "顺便挂 BGM、调音量与人声闪避、改字幕样式")
    app.director_timeline_btn.clicked.connect(lambda: _open_timeline(app))
    mbtns.addWidget(app.director_timeline_btn)
    app.director_merge_btn = QPushButton("合成成片")
    app.director_merge_btn.setFixedHeight(38)
    app.director_merge_btn.setCursor(Qt.PointingHandCursor)
    app.director_merge_btn.setStyleSheet(_btn_accent_style())
    app.director_merge_btn.clicked.connect(lambda: _do_merge(app))
    mbtns.addWidget(app.director_merge_btn)
    app.director_merge_play = QPushButton("▶ 播放成片")
    app.director_merge_play.setFixedHeight(38)
    app.director_merge_play.setStyleSheet(_btn_style())
    app.director_merge_play.setEnabled(False)
    app.director_merge_play.clicked.connect(lambda: _play_video(app, getattr(app, "director_final_path", None)))
    mbtns.addWidget(app.director_merge_play)
    # v4.132 导出三件套：成片 / 工程文件（EDL+FCPXML）/ 素材包
    app.director_export_btn = QPushButton("导出…")
    app.director_export_btn.setFixedHeight(38)
    app.director_export_btn.setStyleSheet(_btn_style())
    app.director_export_btn.setCursor(Qt.PointingHandCursor)
    app.director_export_btn.setToolTip("导出成片 / 剪辑工程文件（EDL·FCPXML）/ 全部素材包（zip）")
    app.director_export_btn.clicked.connect(lambda: _open_export_dialog(app))
    mbtns.addWidget(app.director_export_btn)
    mbtns.addStretch(1)
    ml.addLayout(mbtns)
    app.director_stack.addWidget(merge_page)
    app.director_final_path = None

    # 日志
    app.director_log = QTextEdit()
    app.director_log.setReadOnly(True)
    app.director_log.setFixedHeight(80)
    app.director_log.setStyleSheet(
        f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
        f"border-radius:10px;padding:8px 12px;font-size:12px;color:{THEME['text']};}}")
    lay.addWidget(app.director_log)

    # 把所有内容装入滚动区，挂到页面
    scroll.setWidget(body)
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(scroll, 1)

    # v4.107：导演台底部常驻「导演对话」条——独立会话，主对话框零交集。
    # 局部懒导入，避免 director_chat → agent → ui 的循环依赖在模块加载期炸。
    from director_chat import DirectorChatBar
    bar = DirectorChatBar(app)
    app.director_chat = bar
    outer.addWidget(bar)

    # 运行期状态
    app.director_step = 0
    app.director_pipeline = None
    app.director_thread = None
    app.director_paths = []
    app.director_shot_rows = []
    app.director_busy = False
    _set_director_phase(app, DirectorPhase.IDLE)   # v4.153.3 P3-11
    app.director_spec = {}          # v4.132 项目制作规格（故事设定/风格/镜头/禁词/输出）
    app.director_body_lay = lay
    _set_step(app, 0)
    _log(app, "导演台就绪。填好主题和参数，点「开始导演」。")

    # 若上次有未完成的任务，顶部提示可继续（不必从头来）
    _maybe_offer_resume(app)
    # v4.150：这四个「去对话框说」按钮统一话术——点了只是把模板填进下方对话框，
    # 真正执行要用户补完意见按 Enter（保留「不点确认不烧钱」的原有习惯）。
    _prefill_tip = ("点了会把这个步骤的指令模板填进下方「导演对话」输入框（如「重写剧本：」），\n"
                    "你补完意见按 Enter 发送即可；不改也行，直接回原样重跑。\n"
                    "所有修改都从那个对话框走，不再弹小窗。")
    for _b in (getattr(app, "director_story_revise", None),
               getattr(app, "director_shots_revise", None),
               getattr(app, "director_characters_revise", None),
               getattr(app, "director_keyframes_revise", None)):
        if _b is not None:
            _b.setToolTip(_prefill_tip)
    # v4.106：装 Agent 桥，让聊天对话框也能指挥导演台（改镜/关键帧/三视图/合成）
    install_agent_bridge(app)


# ---------- 步骤切换 ----------
def _step_style(active, done=False):
    if active:
        return (f"QLabel{{background:{THEME['accent']};color:white;border-radius:14px;"
                f"font-size:13px;font-weight:600;padding:0 12px;}}")
    if done:
        return (f"QLabel{{background:{THEME['card']};color:{THEME["live_green"]};border:1px solid {THEME['border']};"
                f"border-radius:14px;font-size:13px;padding:0 12px;}}")
    return (f"QLabel{{background:transparent;color:{THEME['dim']};border:1px solid {THEME['border']};"
            f"border-radius:14px;font-size:13px;padding:0 12px;}}")


def _set_step(app, step):
    app.director_step = step
    for i, lb in enumerate(app.director_step_labels):
        lb.setStyleSheet(_step_style(i == step, done=(i < step)))
    if step >= 1:
        app.director_stack.setCurrentIndex(step - 1)


def _log(app, text):
    app.director_log.append(text)


def _set_status(app, text, err=False):
    color = THEME["accent"] if not err else THEME["danger_text"]
    app.director_status.setStyleSheet(f"color:{color};font-size:12px;")
    app.director_status.setText(text)


# v4.210.0（BUG 审核 P2-4）：动态文案进状态栏前的**压缩入口**。
# 为什么需要：状态栏是单行 QLabel，v4.209.1 实测后明确**不开 wordWrap、也没加 elide**
# —— 超长文本不会被换行也不会出现省略号，而是**直接被裁掉**。异常信息、文件路径
# 动辄上百字，用户看到的是半句话。
# 为什么是 36：不是拍的数。v4.209.1 实测 460px 是"贴边"阈值（容器 460px 时
# 标签可用 432px），12px 中文约 12px/字 → 460/12 ≈ 38 字，留 2 字余量取 36。
# 改这个数前请先重算：状态栏宽度或字号一变，这个上限就失效了。
_STATUS_MAX = 36


def _status_dyn(text):
    """把动态文案（异常 e / 路径 / 后端 msg）压到状态栏放得下的长度。

    只压**进状态栏的那一份** —— `_log()` / `app._director_agent_error` 仍记原文，
    排障要看完整堆栈时去日志和错误标记里找，不要靠状态栏那一行的字数。
    """
    return _brief_err(text, limit=_STATUS_MAX)


# ---------- 导演阶段状态机（v4.153.3 P3-11，轻量，不重写现有 60+ 读取点） ----------
class DirectorPhase:
    """收敛导演台「阶段」语义，替代散落 director_busy / clips_state / final_path 等属性
    在「生命周期」维度上的隐式表达。

    仅作集中表述，不替代任何运行态——现有 60+ 处 getattr 读取点全部保留，本类零破坏。
    阶段机是「会话级生命周期」指示：IDLE=无活动项目；RUNNING=已开导演尚未产出成片；
    DONE=成片已产出；ERROR=某任务失败。运行锁 director_busy 仍按原逻辑独立管理。
    """
    IDLE = "idle"        # 初始 / 已重置：无活动项目
    RUNNING = "running"  # 有活动项目（已开导演，尚未产出成片）
    DONE = "done"        # 成片已产出
    ERROR = "error"      # 某任务失败


def _set_director_phase(app, phase):
    """集中设置阶段。失败静默，绝不打断主流程。"""
    try:
        app.director_phase = phase
    except Exception:
        pass


def _set_busy(app, busy):
    """运行锁：线程跑任务期间禁用所有会再触发线程的按钮，防止并发重入导致崩溃。

    注意：director_go / director_stop 由 start/stop/reset 单独管理，这里不动。
    """
    app.director_busy = busy
    # v4.153.3 P3-11：进入任务即进入 RUNNING 阶段（阶段机仅表述，不动运行态）。
    if busy:
        _set_director_phase(app, DirectorPhase.RUNNING)
    # v4.108 H-14：新任务启动时清掉上次失败标记，避免旧错误污染新回执。
    # v4.141 P1：取消标记同理清零（任务结果三态 success / failed / cancelled）。
    if busy:
        app._director_agent_error = None
        app._director_agent_cancelled = False
    for w in (getattr(app, "director_clips_regen_all", None),
              getattr(app, "director_clips_adopt", None),
              getattr(app, "director_merge_btn", None),
              getattr(app, "director_story_revise", None),
              getattr(app, "director_story_adopt", None),
              getattr(app, "director_characters_revise", None),
              getattr(app, "director_characters_adopt", None),
              getattr(app, "director_shots_revise", None),
              getattr(app, "director_shots_add", None),
              getattr(app, "director_shots_adopt", None),
              getattr(app, "director_keyframes_revise", None),
              getattr(app, "director_keyframes_adopt", None)):
        if w is not None:
            try:
                w.setEnabled(not busy)
            except Exception:
                pass
    # 单镜卡片按钮已在网页内（DirectorWebView）；运行期防重入改由
    # _on_web_action 检查 app.director_busy 实现，这里无需禁用 Qt 按钮。
    # v4.106 Agent 桥接：任务收尾时通知正在等待的对话框指令（如有）。
    if not busy:
        # v4.150：对话发起的改动跑完了 → 自动切到对应步骤页（先切页，再解锁，
        # 让等待中的 Agent 工具与用户同时看到新产物）。
        _th = getattr(app, "director_thread", None)
        _want = getattr(_th, "_chat_pending_step", None) if _th is not None else None
        if _want is not None and _want != getattr(app, "director_step", None):
            try:
                _set_step(app, int(_want))
            except Exception:
                pass
        ev = getattr(app, "_director_agent_event", None)
        if ev is not None:
            app._director_agent_event = None
            try:
                snap = _agent_result_snapshot(app)
                # v4.108 H-14：任务失败时回执必须如实报失败与原因，
                # 禁止回填 ok:True 让导演 Agent 向用户谎报"已完成"。
                _err = getattr(app, "_director_agent_error", None)
                _cancelled = getattr(app, "_director_agent_cancelled", False)
                if _err:
                    snap["ok"] = False
                    snap["state"] = "failed"
                    snap["msg"] = f"任务失败：{_err}"
                elif _cancelled:
                    # v4.141 P1：取消是独立结果态，不能让 Agent 误读成「完成」或「失败」。
                    snap["ok"] = False
                    snap["state"] = "cancelled"
                    snap["msg"] = "任务已被用户取消（未完成，也非失败）"
                app._director_agent_result = snap
            except Exception as e:
                app._director_agent_result = {"ok": False, "msg": f"状态收集失败：{e}"}
            try:
                ev.set()
            except Exception:
                pass


# ---------- v4.106 对话框指令桥接（Agent 工具 → 导演台） ----------
def _agent_result_snapshot(app):
    """任务收尾时抓一份结果摘要，回给等待中的 Agent 工具调用。"""
    p = getattr(app, "director_pipeline", None)
    status_txt = ""
    try:
        status_txt = app.director_status.text()
    except Exception:
        pass
    clips_ok = clips_total = 0
    if p is not None:
        clips_total = len(p.shots or [])
        clips_ok = sum(1 for x in (getattr(p, "clip_paths", None) or []) if x)
    # v4.141：补 state 三态与 final（成片路径）——Agent 第一份回执就能拿到真实
    # 结果与成片位置，不必再二次调用 director_status 纠偏。
    return {"ok": True, "state": "success",
            "step": getattr(app, "director_step", 0),
            "status": status_txt, "clips_ok": clips_ok, "clips_total": clips_total,
            "final": getattr(app, "director_final_path", "") or ""}


def _agent_status(app):
    """导演项目全量状态快照（供 director_status 工具），JSON 友好。"""
    p = getattr(app, "director_pipeline", None)
    if p is None:
        return {"ok": True, "active": False,
                "msg": "导演台当前没有进行中的项目。请先在导演台页填好主题点「开始导演」。"}
    state = getattr(app, "director_clips_state", []) or []
    clips = getattr(p, "clip_paths", None) or []
    kfs = getattr(p, "keyframes", None) or []

    def _clip_mark(i):
        if i < len(clips) and clips[i] and os.path.isfile(str(clips[i])):
            return "已生成"
        if i < len(state) and state[i].get("error"):
            return "失败"
        return "未生成"

    n_shots = len(getattr(p, "shots", None) or [])
    n_chars = len(getattr(p, "characters", None) or [])
    n_kf = sum(1 for x in kfs if x)
    n_clip = sum(1 for x in clips if x)
    step_now = getattr(app, "director_step", 0)
    step_label = (STEP_LABELS[step_now] if 0 <= step_now < len(STEP_LABELS) else "?")
    # v4.127 P2：进度口径补全——角色/关键帧阶段也要能报「角色 X 件、关键帧 N/M、视频未开始」。
    # 旧口径只有片段数据，跑到人物/分镜阶段查进度会答「什么都没有」，与面板实际不符。
    bits = [f"阶段：{step_label}（step={step_now}）",
            f"角色 {n_chars} 件",
            f"关键帧 {n_kf}/{n_shots}",
            ("视频 " + (f"{n_clip}/{n_shots}" if n_clip else "未开始")),
            f"分镜 {n_shots} 镜"]
    summary = "、".join(bits)
    if getattr(app, "director_final_path", ""):
        summary += "、成片已出"
    return {
        "ok": True, "active": True,
        "busy": bool(getattr(app, "director_busy", False)),
        "step": step_now,
        "step_label": step_label,
        "summary": summary,
        "characters_count": n_chars,
        "keyframes_ok": n_kf,
        "keyframes_total": n_shots,
        "video_started": bool(n_clip),
        "portrait_mode": bool(getattr(p, "portrait_mode", False)),
        "characters": [
            {"i": j + 1, "name": (c.get("name") or ""),
             "desc": (c.get("desc") or "")[:80],
             "views_ok": sum(1 for v in (c.get("views") or []) if v)}
            for j, c in enumerate(getattr(p, "characters", None) or [])],
        "clues": [
            {"i": j + 1, "name": (c.get("name") or ""),
             "kind": (c.get("kind") or "prop"),
             "desc": (c.get("desc") or "")[:80],
             "image": "有" if c.get("image") else "无"}
            for j, c in enumerate(getattr(p, "clues", None) or [])],
        "shots": [
            {"i": i + 1, "zh": (s.get("zh") or "")[:60],
             "keyframe": ("有" if (i < len(kfs) and kfs[i]) else "无"),
             "clip": _clip_mark(i),
             "error": (state[i].get("error", "")[:100] if i < len(state) else "")}
            for i, s in enumerate(getattr(p, "shots", None) or [])],
        "clips_ok": sum(1 for x in clips if x),
        "clips_total": len(getattr(p, "shots", None) or []),
        "final": getattr(app, "director_final_path", "") or "",
    }


class _DirectorCmdBridge(QObject):
    """跨线程指令桥：Agent 工具线程 emit(dict) → UI 线程排队执行。

    生成类指令只负责在 UI 线程启动 DirectorThread，随即返回；
    完成通知由 _set_busy(False) 钩子 set 事件，等待方在 Agent 线程。
    """
    cmd = Signal(dict)

    def __init__(self, app):
        super().__init__(app)
        self._app = app
        self.cmd.connect(self._exec)

    def _exec(self, c):
        app = self._app
        ev = c.get("_ev")
        # v4.150：标记「本次是对话发起」——_run_thread 据此记录待切换页；命令返回即清，
        # 不会污染随后用户手动点按钮的操作。
        app._director_chat_driven = True
        app._director_last_artifact = None      # 清掉上一笔，避免回错缩略图
        try:
            res = agent_director_command(app, c)
        except Exception as e:
            res = {"ok": False, "msg": f"导演台指令执行异常：{e}"}
        finally:
            app._director_chat_driven = False
        if res is not None:  # 同步完成（status / 校验失败），直接回
            app._director_agent_result = res
            app._director_agent_event = None
            if ev is not None:
                try:
                    ev.set()
                except Exception:
                    pass
        # res is None → 已开后台线程，等待 _set_busy(False) 钩子回填结果


def install_agent_bridge(app):
    """build_director_panel 末尾调用：装桥并把执行器登记给 director_agent_tools。"""
    bridge = _DirectorCmdBridge(app)
    app._director_cmd_bridge = bridge

    def _dispatch(cmd, timeout=1200):
        import threading as _th
        ev = _th.Event()
        cmd = dict(cmd)
        cmd["_ev"] = ev
        # v4.108 M-07：互斥锁串行化派发——并发指令排队等待，不让单实例事件通道被覆盖。
        # （busy 检查在 UI 线程 slot 内做，两个指令几乎同时到达时都能过检，必须在此串行。）
        with _DIR_DISPATCH_LOCK:
            app._director_agent_result = None
            app._director_agent_event = ev
            bridge.cmd.emit(cmd)
            if not ev.wait(max(30, min(int(timeout or 1200), 3600))):
                app._director_agent_event = None
                r = app._director_agent_result
                if isinstance(r, dict):
                    return r
                return {"ok": False, "msg": "等待导演台任务完成超时（任务可能仍在后台跑，"
                                            "可用 director_status 查询进度）。"}
            return app._director_agent_result or {"ok": False, "msg": "无结果返回"}

    try:
        import director_agent_tools as _dat
        _dat.set_dispatcher(_dispatch)
    except Exception as e:
        print(f"[director] agent bridge install failed: {e}")


def agent_director_command(app, cmd):
    """在 UI 线程执行一条对话框导演指令。

    返回 dict = 同步结果；返回 None = 已启动后台任务（结果稍后经 _set_busy 钩子回填）。
    """
    action = cmd.get("action", "")
    p = getattr(app, "director_pipeline", None)
    if action == "status":
        return _agent_status(app)
    if p is None:
        return {"ok": False,
                "msg": "导演台当前没有进行中的项目。请先在导演台页填好主题点「开始导演」。"}
    if getattr(app, "director_busy", False):
        return {"ok": False, "msg": "上一步还在跑，请等它完成后再下指令。"}

    if action == "revise_clip":
        try:
            idx = int(cmd.get("idx", 0)) - 1  # 用户视角从 1 数
        except Exception:
            return {"ok": False, "msg": "分镜号格式不对"}
        note = (cmd.get("note") or "").strip() or None
        if not (0 <= idx < len(p.shots)):
            return {"ok": False, "msg": f"分镜号超出范围（共 {len(p.shots)} 镜）"}
        if cmd.get("replace") and note:
            # 内容审核被拦时需整段替换英文提示词
            p.shots[idx]["en"] = note
            note = None
        _set_status(app, f"[对话框指令] 正在按意见重生成 镜{idx + 1}…")
        _log(app, f"✎ [对话框] 修改 镜{idx + 1}：{note or '（直接重生成）'}")
        _run_thread(app, "clip_one", idx=idx, note=note)
        return None

    if action == "revise_keyframe":
        try:
            idx = int(cmd.get("idx", 0)) - 1
        except Exception:
            return {"ok": False, "msg": "分镜号格式不对"}
        if getattr(p, "portrait_mode", False):
            return {"ok": False, "msg": "口播模式没有关键帧（画面已锁定本人形象）"}
        if not (0 <= idx < len(p.shots or [])):
            return {"ok": False, "msg": f"分镜号超出范围（共 {len(p.shots or [])} 镜）"}
        note = (cmd.get("note") or "").strip() or None
        _set_status(app, f"[对话框指令] 正在重生成 镜{idx + 1} 关键帧…")
        _log(app, f"✎ [对话框] 重生成 镜{idx + 1} 关键帧：{note or '（无修改意见）'}")
        _run_thread(app, "keyframe_one", idx=idx, feedback=note)
        return None

    if action == "revise_character":
        try:
            idx = int(cmd.get("idx", 0)) - 1  # 角色序号（director_status 里可见）
        except Exception:
            return {"ok": False, "msg": "角色序号格式不对"}
        if getattr(p, "portrait_mode", False):
            return {"ok": False, "msg": "口播模式没有人物三视图（形象已锁定本人照片）"}
        chars = getattr(p, "characters", None) or []
        if not chars:
            return {"ok": False, "msg": "还没有人物三视图（可能尚未生成到该步骤）"}
        if not (0 <= idx < len(chars)):
            return {"ok": False, "msg": f"角色序号超出范围（共 {len(chars)} 个角色）"}
        note = (cmd.get("note") or "").strip() or None
        name = chars[idx].get("name") or f"角色{idx + 1}"
        _set_status(app, f"[对话框指令] 正在重生成「{name}」三视图…")
        _log(app, f"✎ [对话框] 重生成角色「{name}」三视图：{note or '（无修改意见）'}")
        _run_thread(app, "character_one", idx=idx, feedback=note)
        return None

    if action == "gen_clues":
        if getattr(p, "portrait_mode", False):
            return {"ok": False, "msg": "口播模式没有跨镜道具（画面已锁定本人形象）"}
        if not getattr(p, "story", ""):
            return {"ok": False, "msg": "还没有剧本，无法抽取道具/场景资产"}
        note = (cmd.get("note") or "").strip() or None
        _set_status(app, "[对话框指令] 正在抽取关键道具 / 场景资产…")
        _log(app, f"🔎 [对话框] 抽取关键道具/场景资产：{note or '（无修改意见）'}")
        _run_thread(app, "clues", feedback=note)
        return None

    if action == "revise_clue":
        try:
            idx = int(cmd.get("idx", 0)) - 1
        except Exception:
            return {"ok": False, "msg": "道具序号格式不对"}
        clues = getattr(p, "clues", None) or []
        if not clues:
            return {"ok": False, "msg": "还没有道具/场景资产（先执行 gen_clues）"}
        if not (0 <= idx < len(clues)):
            return {"ok": False, "msg": f"道具序号超出范围（共 {len(clues)} 件）"}
        note = (cmd.get("note") or "").strip() or None
        name = clues[idx].get("name") or f"道具{idx + 1}"
        _set_status(app, f"[对话框指令] 正在重生成「{name}」参考图…")
        _log(app, f"✎ [对话框] 重生成道具/资产「{name}」：{note or '（无修改意见）'}")
        _run_thread(app, "clue_one", idx=idx, feedback=note)
        return None

    if action in ("rollback_clip", "rollback_keyframe", "rollback_character",
                  "rollback_clue"):
        kind = action.split("_", 1)[1]  # clip / keyframe / character / clue
        try:
            idx = int(cmd.get("idx", 0)) - 1  # 用户视角从 1 数
        except Exception:
            return {"ok": False, "msg": "序号格式不对"}
        try:
            version = int(cmd.get("version", -1))
        except Exception:
            version = -1
        if kind == "clip":
            if not (0 <= idx < len(p.shots or [])):
                return {"ok": False, "msg": f"分镜号超出范围（共 {len(p.shots or [])} 镜）"}
            res = p.rollback_clip(idx, version)
        elif kind == "keyframe":
            if getattr(p, "portrait_mode", False):
                return {"ok": False, "msg": "口播模式没有关键帧（画面已锁定本人形象）"}
            if not (0 <= idx < len(p.shots or [])):
                return {"ok": False, "msg": f"分镜号超出范围（共 {len(p.shots or [])} 镜）"}
            res = p.rollback_keyframe(idx, version)
        elif kind == "clue":
            if getattr(p, "portrait_mode", False):
                return {"ok": False, "msg": "口播模式没有跨镜道具（画面已锁定本人形象）"}
            clues = getattr(p, "clues", None) or []
            if not (0 <= idx < len(clues)):
                return {"ok": False, "msg": f"道具序号超出范围（共 {len(clues)} 件）"}
            res = p.rollback_clue(idx, version)
        else:  # character
            if getattr(p, "portrait_mode", False):
                return {"ok": False, "msg": "口播模式没有人物三视图（形象已锁定本人照片）"}
            chars = getattr(p, "characters", None) or []
            if not (0 <= idx < len(chars)):
                return {"ok": False, "msg": f"角色序号超出范围（共 {len(chars)} 个角色）"}
            res = p.rollback_character(idx, version)
        if not res:
            return {"ok": False, "msg": "该条目没有可回滚的历史版本（或历史文件已丢失）"}
        label = {"clip": f"镜{idx + 1}", "keyframe": f"镜{idx + 1} 关键帧",
                 "character": f"角色「{res}」", "clue": f"道具/资产「{res}」"}[kind]
        _set_status(app, f"[对话框指令] {label} 已回滚到上一版本")
        _log(app, f"↩ [对话框] {label} 回滚")
        return {"ok": True, "restored": label}

    # ---------- v4.150 对话化：剧本重写 / 分镜重排 / 显式整批 / 对话内采用 ----------
    if action == "revise_story":
        note = (cmd.get("note") or "").strip() or None
        if not getattr(p, "story", ""):
            return {"ok": False, "msg": "还没有剧本（可能还没生成到这一步）。"}
        _set_status(app, "[对话框指令] 正在按意见重写剧本…")
        _log(app, f"✎ [对话框] 重写剧本：{note or '（原样重试）'}")
        _run_thread(app, "story", feedback=note)
        return None

    if action == "revise_shots":
        note = (cmd.get("note") or "").strip() or None
        if not getattr(p, "story", ""):
            return {"ok": False, "msg": "还没有剧本，先让剧本就绪再重排分镜。"}
        _set_status(app, "[对话框指令] 正在按意见重排分镜…")
        _log(app, f"✎ [对话框] 重排分镜：{note or '（原样重试）'}")
        _run_thread(app, "shots", feedback=note)
        return None

    if action == "revise_keyframe_all":
        note = (cmd.get("note") or "").strip() or None
        if getattr(p, "portrait_mode", False):
            return {"ok": False, "msg": "口播模式没有关键帧（画面已锁定本人形象）"}
        if not getattr(p, "shots", None):
            return {"ok": False, "msg": "还没有分镜，无法重生成关键帧。"}
        _set_status(app, "[对话框指令] 正在按意见重生成全部关键帧…")
        _log(app, f"✎ [对话框] 重生成全部关键帧（{len(p.shots)} 镜）：{note or '（无修改意见）'}")
        _run_thread(app, "keyframes", feedback=note)
        return None

    if action == "revise_characters_all":
        note = (cmd.get("note") or "").strip() or None
        if getattr(p, "portrait_mode", False):
            return {"ok": False, "msg": "口播模式没有人物三视图（形象已锁定本人照片）"}
        if not getattr(p, "characters", None):
            return {"ok": False, "msg": "还没有人物三视图（可能尚未生成到该步骤）"}
        _set_status(app, "[对话框指令] 正在按意见重生成全部角色三视图…")
        _log(app, f"✎ [对话框] 重生成全部角色三视图：{note or '（无修改意见）'}")
        _run_thread(app, "characters", feedback=note)
        return None

    if action == "confirm":
        return _agent_confirm(app, cmd)

    if action == "merge":
        clips = getattr(p, "clip_paths", None) or []
        if not any(clips):
            return {"ok": False, "msg": "还没有可合成的视频片段（先逐镜生成）"}
        _set_status(app, "[对话框指令] 正在合成成片…")
        _log(app, "🎬 [对话框] 合成成片…")
        try:
            app.director_merge_btn.setEnabled(False)
        except Exception:
            pass
        _run_thread(app, "merge")
        return None

    return {"ok": False, "msg": f"未知导演台指令：{action}"}


# v4.150 对话内「采用当前步 → 下一步」的映射：与面板按钮一一对应，不新增推进路径。
_CONFIRM_ADOPT = {
    1: ("剧本", "_adopt_story"),
    2: ("人物", "_adopt_characters"),
    3: ("分镜", "_adopt_shots"),
    4: ("关键帧", "_adopt_keyframes"),
    5: ("生成", "_adopt_clips"),
}


def _agent_confirm(app, cmd=None):
    """对话里说「采用 / 下一步」时执行：采用**当前停留步骤**并推进。

    返回 None = 已启动后台生成（dispatcher 等 _set_busy(False) 回落结果）；
    返回 dict = 同步完成，或未执行（附原因）。**绝不假装成功**——被 Prompt 预审
    取消、产物不满足条件时明确回 ok=False，避免模型报「已完成」骗用户。
    """
    cmd = cmd or {}
    step_now = getattr(app, "director_step", 0)
    want = cmd.get("step")
    if want is not None:
        try:
            want = int(want)
        except Exception:
            return {"ok": False, "msg": "step 必须是整数或留空。"}
        if want != step_now:
            label_w = (STEP_LABELS[want] if 0 <= want < len(STEP_LABELS) else want)
            return {"ok": False,
                    "msg": f"当前停留在第 {step_now} 步（{_step_label(step_now)}），"
                           f"不是你指定的第 {want} 步（{label_w}）。采用只作用于当前步。"}
    if step_now == 0:
        return {"ok": False, "msg": "导演台还没开拍。请先在导演台填好主题点「开始导演」。"}
    if step_now >= 6:
        return {"ok": False, "msg": "已经在合成步骤了。要出成片请调 director_merge。"}
    ent = _CONFIRM_ADOPT.get(step_now)
    if ent is None:
        return {"ok": False, "msg": f"第 {step_now} 步不支持对话内采用。"}
    label, fname = ent
    fn = globals().get(fname)
    if fn is None:
        return {"ok": False, "msg": f"内部错误：找不到 {fname}。"}
    before_thread = getattr(app, "director_thread", None)
    _set_status(app, f"[对话框指令] 采用第 {step_now} 步（{label}）→ 进入下一步…")
    _log(app, f"✓ [对话框] 采用{label} → 下一步")
    fn(app)                      # 与面板按钮走同一个函数，行为完全一致
    started = (getattr(app, "director_thread", None) is not before_thread
               or getattr(app, "director_busy", False))
    if started:
        return None              # 后台在跑，结果由 _set_busy(False) 钩子回填
    now = getattr(app, "director_step", 0)
    if now != step_now:
        return {"ok": True, "step_from": step_now, "step_now": now,
                "msg": f"已采用「{label}」，现在到第 {now} 步（{_step_label(now)}）。"}
    return {"ok": False,
            "msg": f"「{label}」这一波没有推进：可能被 Prompt 预审取消了，或当前产物"
                   f"还不满足采用条件（先 director_status 看看现状，别直接说已完成）。"}


def _step_label(step):
    return STEP_LABELS[step] if 0 <= step < len(STEP_LABELS) else "?"


# v4.150：对话发起的改动，完成后自动切到对应步骤页，省掉「自己翻回去找新图」。
# 由 _run_thread 在「本次是对话发起」时记到线程对象上，_set_busy(False) 消费一次。
_PENDING_VIEW_TASK2STEP = {
    "story": 1, "shots": 3, "characters": 2, "clues": 2,
    "keyframes": 4, "keyframe_one": 4, "character_one": 2,
    "clue_one": 2, "clips": 5, "clip_one": 5, "merge": 6,
}

# v4.150：对话发起的改动，完成后「回缩略图」用——记下改动的是哪一类产物的第几件。
_PENDING_VIEW_TASK2ART = {
    "keyframe_one": "keyframe", "clip_one": "clip",
    "character_one": "character", "clue_one": "clue",
}


def artifact_thumb(app):
    """给对话条回缩略图用：返回 (图片绝对路径 or None, 说明文字 or None)。

    只读，不改任何状态；任何异常都退化成 (None, None)，绝不影响对话收尾。
    """
    a = getattr(app, "_director_last_artifact", None) or {}
    kind = a.get("kind")
    idx = a.get("idx")
    p = getattr(app, "director_pipeline", None)
    if p is None or kind is None:
        return None, None

    def _ok(x):
        return bool(x) and os.path.isfile(str(x))

    try:
        if kind == "keyframe":
            kfs = getattr(p, "keyframes", None) or []
            if idx is not None and 0 <= idx < len(kfs) and _ok(kfs[idx]):
                return kfs[idx], f"镜{idx + 1} 关键帧"
            return None, None
        if kind == "clip":
            cp = getattr(p, "clip_paths", None) or []
            if idx is not None and 0 <= idx < len(cp) and _ok(cp[idx]):
                # 视频取同镜关键帧当封面（没有就只回文字）
                kfs = getattr(p, "keyframes", None) or []
                cover = kfs[idx] if (0 <= idx < len(kfs) and _ok(kfs[idx])) else None
                return cover, f"镜{idx + 1} 视频片段"
            return None, None
        if kind == "character":
            chs = getattr(p, "characters", None) or []
            if idx is not None and 0 <= idx < len(chs):
                for v in (chs[idx].get("views") or []):
                    if _ok(v):
                        return v, f"角色{idx + 1}（{chs[idx].get('name') or ''}）三视图"
            return None, None
        if kind == "clue":
            cls_ = getattr(p, "clues", None) or []
            if idx is not None and 0 <= idx < len(cls_):
                img = cls_[idx].get("image")
                if _ok(img):
                    return img, f"道具/资产{idx + 1}（{cls_[idx].get('name') or ''}）"
            return None, None
    except Exception:
        return None, None
    return None, None


# ---------- 启动 ----------
def _director_pick_ref(app):
    path, _ = QFileDialog.getOpenFileName(
        app, "选择参考图", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
    if not path:
        return
    app.director_ref_image = path
    pm = QPixmap(path)
    if not pm.isNull():
        pm = pm.scaled(76, 76, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        app.director_ref_preview.setPixmap(pm)
        app.director_ref_preview.setText("")
    app.director_ref_label.setText(os.path.basename(path))


class _UnderstandThread(QThread):
    """后台跑一次「需求理解」模型调用，避免 UI 冻结（这步要联网，可能几秒）。"""
    done = Signal(object)               # dict | None

    def __init__(self, cfg, topic, style_key, n, duration, spec, parent=None):
        super().__init__(parent)
        self._args = (cfg, topic, style_key, n, duration, spec)

    def run(self):
        try:
            from video_pipeline import understand_topic
            res = understand_topic(*self._args)
        except Exception:
            res = None
        self.done.emit(res)


def _apply_style_from_topic(app, topic_raw):
    """主题里写了画风就以**主题为准**：自动切下拉框 + 状态栏与日志明确告知。

    大哥 2026-09-15 定下的规则：主题优先，但要说明我改了什么、允许他改回。
    纯确定性关键词匹配（`video_pipeline.style_from_topic`）——不调模型、不花钱、
    结果可预期、可单测。返回实际生效的 style_key（未识别到则 None）。
    """
    try:
        from video_pipeline import style_from_topic, style_label
    except Exception:
        return None
    try:
        got = style_from_topic(topic_raw)
    except Exception:
        return None
    if not got:
        return None
    cur = app.director_style.currentData() or "realistic"
    if got == cur:
        return got
    idx = app.director_style.findData(got)
    if idx < 0:
        return None
    app.director_style.setCurrentIndex(idx)
    msg = (f"🎨 主题里写着「{style_label(got)}」，已把画风从「{style_label(cur)}」"
           f"改为「{style_label(got)}」（在上方的风格下拉框可以改回）。")
    _log(app, msg)
    _set_status(app, msg)
    return got


def _open_understand_dialog(app, topic_raw, on_ok):
    """先做「需求理解」（一次模型调用，只理解不写剧本），再弹确认卡。

    画风初值按**主题优先**算好塞进卡里（`style_from_topic`），但不在这里改下拉框 ——
    用户点「取消」时保证**一个字都没被改过**。
    """
    cur_style = app.director_style.currentData() or "realistic"
    try:
        from video_pipeline import style_from_topic
        detected = style_from_topic(topic_raw) or cur_style
    except Exception:
        detected = cur_style

    _set_status(app, "正在把你的主题整理成需求卡…（只理解，不写剧本、不生图）")
    _log(app, "🧭 正在理解你的需求…")
    app.director_go.setEnabled(False)

    th = _UnderstandThread(app.cfg, topic_raw, detected,
                           app.director_n.value(), app.director_duration.value(),
                           dict(getattr(app, "director_spec", {}) or {}), parent=app)
    app._director_understand_thread = th      # 持引用，防被 GC 提前回收

    def _fin(res):
        app.director_go.setEnabled(True)
        app._director_understand_thread = None
        if not isinstance(res, dict):
            # 降级：理解失败绝不挡路，按原流程直接开写（与旧行为一致）
            _log(app, "⚠️ 需求理解失败（模型不可用或返回异常），跳过确认卡，直接按原流程开写。")
            _set_status(app, "需求理解失败，已按原流程直接开写。")
            _apply_style_from_topic(app, topic_raw)
            on_ok()
            return
        res["style_key"] = detected            # 主题优先于下拉框（卡上仍可改）
        _show_understand_card(app, topic_raw, res, on_ok)

    th.done.connect(_fin)
    th.start()


def _apply_understand(app, style_key, n, dur, audio, genre, narrative, source,
                      notes, extra):
    """把需求卡上的字段落到各处（**纯函数式落盘，便于单测与复用**）。

    落点全部走既有通道，不新造管道：
      画风 → `style_key`（画面提示词的唯一画风来源）
      题材/立意 + 叙事方式 + 原文使用 + 声音 + 备注 + 补充 → `spec["setting"]`
        （中文硬约束，剧本 / 分镜 / 角色 / 道具四处都会读到；**追加**而非覆盖
         用户原先在「项目设定」里写的内容）
      声音 → 台词开关；镜头数 / 每镜秒数 → 面板数字框
    返回一份「实际改了什么」的摘要，供日志与断言使用。
    """
    changed = []
    # ① 画风
    if style_key:
        _i = app.director_style.findData(style_key)
        if _i >= 0 and app.director_style.currentData() != style_key:
            _cur = app.director_style.currentData() or ""
            app.director_style.setCurrentIndex(_i)
            try:
                from video_pipeline import style_label
                changed.append(f"画风「{style_label(_cur)}」→「{style_label(style_key)}」")
            except Exception:
                changed.append(f"画风 → {style_key}")
    # ② 镜头数 / 每镜秒数
    if int(n) != app.director_n.value():
        changed.append(f"镜头数 {app.director_n.value()} → {int(n)}")
    app.director_n.setValue(int(n))
    if int(dur) != app.director_duration.value():
        changed.append(f"每镜秒数 {app.director_duration.value()} → {int(dur)}")
    app.director_duration.setValue(int(dur))
    # ③ 声音 → 台词开关（"旁白解说"不是管线特性，写进 spec 由编剧落实）
    try:
        from video_pipeline import UNDERSTAND_AUDIO_CHOICES as _AUD
        want_dialogue = (audio == _AUD[1])
    except Exception:
        want_dialogue = False
    if app.director_dialogue.isChecked() != want_dialogue:
        changed.append(f"台词 {'开' if want_dialogue else '关'}")
    app.director_dialogue.setChecked(want_dialogue)
    # ④ 其余全部并进 spec["setting"]
    try:
        from video_pipeline import build_setting_block
        _blk = build_setting_block(genre=genre, narrative=narrative, source=source,
                                   audio=audio, notes=notes, extra=extra)
    except Exception:
        _blk = ""
    if _blk:
        _old = str((getattr(app, "director_spec", {}) or {}).get("setting") or "").strip()
        app.director_spec = dict(getattr(app, "director_spec", {}) or {})
        app.director_spec["setting"] = (f"{_old}\n{_blk}").strip() if _old else _blk
        changed.append("已写入全片规格（题材/叙事方式/原文使用/声音）")
    return {"changed": changed, "setting": _blk}


def _show_understand_card(app, topic_raw, info, on_ok):
    """需求确认卡：把「我以为你要什么」摆到台面上，改完点确认才开写。

    确认后的字段落点**全部走既有通道**，不新造管道：
      画风 → `style_key`（画面提示词的唯一画风来源）
      题材/叙事方式/原文使用/声音/补充意见 → `spec["setting"]`（中文硬约束，
        剧本·分镜·角色·道具四处都会读到）
      声音 → 台词开关；镜头数/每镜秒数 → 面板数字框
    「取消」= 什么都不发生（不换令牌、不清存档、不建工程）。
    """
    from theme_qss import label_body, label_second
    try:
        from ui import _NoWheelCombo
    except Exception:
        _NoWheelCombo = QComboBox
    # 选项白名单从 pipeline 侧取（同一份来源）；取不到就给一份最小兜底，绝不因此崩。
    try:
        from video_pipeline import (UNDERSTAND_SOURCE_CHOICES as _SRC,
                                    UNDERSTAND_AUDIO_CHOICES as _AUD)
    except Exception:
        _SRC = ("不引用原文",)
        _AUD = ("无台词（纯画面 + 字幕）",)

    dlg = QDialog(app)
    dlg.setWindowTitle("开拍前，先跟你对一下需求")
    dlg.setMinimumWidth(660)
    dlg.setStyleSheet(f"QDialog{{background:{THEME['bg']};}}")
    v = QVBoxLayout(dlg)
    v.setSpacing(12)
    v.setContentsMargins(18, 18, 18, 18)

    head = QLabel(f"你的主题：{topic_raw}")
    head.setWordWrap(True)
    head.setStyleSheet(
        label_body(weight="semibold") + "background:#FFFFFF;border:1px solid #E5E7EB;border-radius:8px;padding:12px 12px;")
    v.addWidget(head)

    tip = QLabel("下面是它对你这句主题的理解。**不对就直接改**，改完点「确认并开写」——"
                 "在你点确认之前，它一个字都不会写、一张图都不会生成。")
    tip.setWordWrap(True)
    tip.setStyleSheet(label_second())
    v.addWidget(tip)

    def _lbl(t):
        from theme_qss import label_second
        lb = QLabel(t)
        lb.setStyleSheet(label_second(color_key="text", weight="semibold"))
        return lb

    def _te(text, h, ph=""):
        t = QTextEdit()
        t.setPlainText(text or "")
        t.setPlaceholderText(ph)
        t.setFixedHeight(h)
        t.setStyleSheet(_edit_style())
        return t

    v.addWidget(_lbl("题材 / 立意（它以为你要拍的是什么）"))
    te_genre = _te(info.get("genre"), 58)
    v.addWidget(te_genre)

    # 画风 + 镜头数 + 每镜秒数 同一行
    row = QHBoxLayout()
    row.setSpacing(12)
    row.addWidget(_lbl("画风"))
    cb_style = _NoWheelCombo()
    cb_style.setMinimumWidth(150)
    for _lab, _val in STYLE_ITEMS:
        cb_style.addItem(_lab, _val)
    _i = cb_style.findData(info.get("style_key") or app.director_style.currentData())
    cb_style.setCurrentIndex(_i if _i >= 0 else 0)
    row.addWidget(cb_style)
    row.addWidget(_lbl("镜头数"))
    sp_n = QSpinBox()
    sp_n.setRange(1, 40)
    sp_n.setValue(int(app.director_n.value()))
    sp_n.setMinimumWidth(70)
    row.addWidget(sp_n)
    row.addWidget(_lbl("每镜秒数"))
    sp_d = QSpinBox()
    sp_d.setRange(2, 15)
    sp_d.setValue(int(app.director_duration.value()))
    sp_d.setMinimumWidth(70)
    row.addWidget(sp_d)
    row.addStretch(1)
    v.addLayout(row)

    v.addWidget(_lbl("叙事方式"))
    te_narr = _te(info.get("narrative"), 52)
    v.addWidget(te_narr)

    row2 = QHBoxLayout()
    row2.setSpacing(12)
    row2.addWidget(_lbl("原文使用"))
    cb_src = _NoWheelCombo()
    cb_src.setMinimumWidth(190)
    cb_src.addItems(list(_SRC))
    if info.get("source") in _SRC:
        cb_src.setCurrentText(info["source"])
    row2.addWidget(cb_src)
    row2.addWidget(_lbl("声音"))
    cb_audio = _NoWheelCombo()
    cb_audio.setMinimumWidth(190)
    cb_audio.addItems(list(_AUD))
    if info.get("audio") in _AUD:
        cb_audio.setCurrentText(info["audio"])
    row2.addWidget(cb_audio)
    row2.addStretch(1)
    v.addLayout(row2)

    v.addWidget(_lbl("给编剧的补充要求（可留空；模型建议：%s）"
                     % (info.get("notes") or "无")))
    te_extra = _te("", 52, "例：全片无对白，只用画面和字幕；结尾停在原词最后一句上")
    v.addWidget(te_extra)

    btns = QHBoxLayout()
    btns.setSpacing(12)
    btns.addStretch(1)
    b_cancel = QPushButton("取消")
    b_cancel.setFixedHeight(36)
    b_cancel.setCursor(Qt.PointingHandCursor)
    b_cancel.setStyleSheet(_btn_style())
    b_ok = QPushButton("✓ 确认并开写")
    b_ok.setFixedHeight(36)
    b_ok.setCursor(Qt.PointingHandCursor)
    b_ok.setStyleSheet(_btn_accent_style())
    btns.addWidget(b_cancel)
    btns.addWidget(b_ok)
    v.addLayout(btns)

    b_cancel.clicked.connect(dlg.reject)

    def _accept():
        res = _apply_understand(
            app,
            style_key=cb_style.currentData(),
            n=sp_n.value(), dur=sp_d.value(),
            audio=cb_audio.currentText(),
            genre=te_genre.toPlainText(), narrative=te_narr.toPlainText(),
            source=cb_src.currentText(), notes=info.get("notes", ""),
            extra=te_extra.toPlainText())
        if res.get("changed"):
            _log(app, "🧾 已按需求卡设定：" + "；".join(res["changed"]))
        _log(app, "✅ 需求已确认，开始写剧本。")
        dlg.accept()
        on_ok()

    b_ok.clicked.connect(_accept)
    dlg.exec()


def _director_start(app):
    """点「开始导演」的入口。

    v4.151：大哥反馈「每次我写入主题，它首先给我编造个故事」「它对我的需求理解太差」。
    根因是「填主题 → 直接开写」中间**没有任何对齐环节**，而系统定位就是
    「把主题扩展成微故事」，所以它必然自己编。现在拆成两步：
      ① 主题里写了画风（如"水墨动画"）→ 以主题为准自动切下拉框并明确告知；
      ② 非口播且有主题 → 先出一张**需求理解卡**（题材/画风/镜头数/叙事方式/
         原文使用/声音 + 补充意见），**确认后才真正开写**。
    选「取消」= 什么都不发生：不换令牌、不清存档、不建工程。
    """
    topic_raw = app.director_topic.toPlainText().strip()
    portrait = app.director_portrait.isChecked()
    if not topic_raw and not app.director_ref_image:
        _set_status(app, "请填写主题，或上传参考图/本人照片。", err=True)
        return
    if getattr(app, "director_busy", False):
        _set_status(app, "正在生成中，等这一波跑完再开新片。", err=True)
        return
    # 非口播 + 有文字主题 → 先出需求确认卡；确认前**不改任何东西**（连画风下拉也不动，
    # 卡上会直接显示按主题识别出的画风，用户确认后才真正落盘）。
    if topic_raw and not portrait:
        _open_understand_dialog(app, topic_raw,
                                on_ok=lambda: _director_start_now(app))
        return
    # 无卡路径（口播 / 仅参考图）：仍按主题自动切画风并告知
    _apply_style_from_topic(app, topic_raw)
    _director_start_now(app)


def _director_start_now(app):
    """真正开工：换项目令牌 → 清旧存档 → 锁定参数 → 建工程 → 跑剧本。"""
    # v4.141 P2：新项目开工 → 换发项目令牌。此后旧页面携带的旧令牌一律失效，
    # 其迟到动作会被 _on_web_action 丢弃，不会误打到新项目的同序号镜。
    set_project_token("p" + os.urandom(4).hex())
    # 若顶部还挂着「续跑」横幅，说明用户选择从头开始新的任务 → 清掉横幅与旧存盘
    banner = getattr(app, "director_resume_banner", None)
    if banner is not None:
        try:
            banner.deleteLater()
        except Exception:
            pass
        app.director_resume_banner = None
    _clear_session(app)

    topic_raw = app.director_topic.toPlainText().strip()
    portrait = app.director_portrait.isChecked()
    if not app.director_ref_image and portrait:
        _set_status(app, "口播模式建议上传本人照片作参考图（不传也能跑，但形象可能漂移）。")

    passthrough = None
    topic = topic_raw
    if portrait:
        if topic_raw.startswith(("原稿：", "原稿:", "直通：", "直通:")):
            passthrough = topic_raw[3:].strip()
            topic = "本人形象口播（原稿直通）"
        elif len(topic_raw) >= 60:
            passthrough = topic_raw
            topic = "本人形象口播（原稿直通）"
        elif not topic_raw:
            topic = ("本人形象口播：围绕主题自由发挥，用口语化方式讲述。"
                     "每段5秒左右，像在跟朋友聊天。")

    res = app.director_resolution.currentData() or "768x1152"
    style_key = app.director_style.currentData() or "realistic"
    params = dict(
        topic=topic, n=app.director_n.value(), duration=app.director_duration.value(),
        resolution=res, style_key=style_key, ref_image_path=app.director_ref_image,
        portrait_mode=portrait, with_dialogue=app.director_dialogue.isChecked(),
        relay=app.director_relay.isChecked(), transition="black", transition_dur=0.4,
        burn_subtitles=app.director_subtitle.isChecked(), passthrough_script=passthrough,
        # v4.154：AI 智能分镜开关（决定 AI 是否自主定镜数 + 每镜时长）
        smart=app.director_smart.isChecked(),
        # v4.132 项目制作规格（写一次，全片各步自动带上）
        spec=dict(getattr(app, "director_spec", {}) or {}),
    )
    app.director_params = params

    # 锁定参数、禁用输入
    for w in app.director_inputs:
        w.setEnabled(False)
    app.director_params_box.setEnabled(False)

    from video_pipeline import VideoPipeline
    app.director_pipeline = VideoPipeline(app.cfg, APP_DIR, {}, auto_approve=True)
    app.director_pipeline.prepare(**params)
    # v4.107：新项目 → 切换导演对话历史到本项目目录（跟随项目可回溯）
    if getattr(app, "director_chat", None) is not None:
        app.director_chat.reload_for_project()
    # VLM 质检开关在 prepare 之后再挂，避免改动 VideoPipeline.prepare 的签名
    app.director_pipeline.vision_review = app.director_vision_review.isChecked()
    # v4.127 多参考图增强开关（同理挂在 prepare 之后）
    app.director_pipeline.multiref = app.director_multiref.isChecked()

    app.director_log.clear()
    _set_status(app, "正在生成剧本…")
    _log(app, "▶ 开始：生成剧本…")
    app.director_go.setEnabled(False)
    app.director_stop.setEnabled(True)
    _set_step(app, 1)
    _run_thread(app, "story")


def _run_thread(app, task, feedback=None, idx=None, note=None):
    # 运行锁：已有线程在跑时拒绝新任务，避免两个 generate_all_clips 并发
    # 操作同一 pipeline / 同时跑 ffmpeg / 网络导致冻结 EXE 硬崩。
    if getattr(app, "director_busy", False):
        _set_status(app, "上一步还在跑，请等它完成（或点「■ 停止」）。")
        return
    if app.director_pipeline is None:
        _set_status(app, "流程未初始化，请先点「开始导演」。", err=True)
        return
    _set_busy(app, True)
    th = DirectorThread(app.director_pipeline, task, feedback=feedback, idx=idx, note=note)
    th.log.connect(lambda t: _log(app, t))
    th.status.connect(lambda t, e=False: _set_status(app, t, e))
    # v4.153 P2-03：统一 generation 守卫（复用 _on_web_action 的项目令牌机制）。
    # 跨线程信号是排队投递——线程跑完、emit 已入队，但项目已「开始导演/重置」换发新令牌，
    # 旧项目的迟到结果若直接落盘会污染新项目的剧本框 / 分镜 / 成片。
    # 这里在「交付侧」比对令牌：捕获线程创建时的令牌，交付时若已切换则丢弃（不误杀正常流程）。
    tok = project_token()
    th.story_ready.connect(lambda t, _tok=tok: _gen_guard(_tok, app, _on_story_ready, app, t))
    th.shots_ready.connect(lambda s, _tok=tok: _gen_guard(_tok, app, _on_shots_ready, app, s))
    th.characters_ready.connect(lambda c, _tok=tok: _gen_guard(_tok, app, _on_characters_ready, app, c))
    th.clues_ready.connect(lambda c, _tok=tok: _gen_guard(_tok, app, _on_clues_ready, app, c))
    th.keyframes_ready.connect(lambda k, _tok=tok: _gen_guard(_tok, app, _on_keyframes_ready, app, k))
    th.clip_ready.connect(lambda i, p, _tok=tok: _gen_guard(_tok, app, _on_clip_ready, app, i, p))
    th.clip_failed.connect(lambda i, r, _tok=tok: _gen_guard(_tok, app, _on_clip_failed, app, i, r))
    th.clips_done.connect(lambda ok, tot, msg, _tok=tok: _gen_guard(_tok, app, _on_clips_done, app, ok, tot, msg))
    th.merge_ready.connect(lambda ok, msg, p, _tok=tok: _gen_guard(_tok, app, _on_merge_ready, app, ok, msg, p))
    th.error.connect(lambda e, _tok=tok: _gen_guard(_tok, app, _on_error, app, e))
    app.director_thread = th
    # v4.150：对话发起的任务记下「该看哪一页」，跑完自动切过去，省掉用户翻页找新图。
    # 只对对话发起生效（面板按钮本来就在对应页上），避免手动浏览时被强行拽走。
    if getattr(app, "_director_chat_driven", False):
        th._chat_pending_step = _PENDING_VIEW_TASK2STEP.get(task)
        _k = _PENDING_VIEW_TASK2ART.get(task)
        if _k:
            app._director_last_artifact = {"kind": _k, "idx": idx}
    th.start()


# ---------- ① 剧本 ----------
def _gen_guard(tok, app, fn, *args, **kwargs):
    """v4.153 P2-03：丢弃「已切换 / 已重置项目」的迟到线程结果。

    与 _on_web_action 的令牌守卫同语义：令牌为空（旧线程未注入 / 向后兼容）时
    不做校验，保持原行为，绝不误杀正常操作；令牌非空且已切换 → 丢弃并记日志。
    这是「旧上下文喂新任务」类 bug 在导演台的闭环：线程结束但 emit 排队投递，
    等交付时项目已换发新令牌，迟到结果若落盘会污染新剧本框 / 分镜 / 成片。
    """
    if tok and tok != project_token():
        _log(app, "⚠️ 收到来自旧项目的迟到结果（令牌已切换），已忽略。")
        return
    return fn(*args, **kwargs)


@_safe
def _on_story_ready(app, text):
    app.director_story_edit.setPlainText(text)
    _set_status(app, "剧本已生成。可编辑后「采用剧本」，或「重写」并附意见。")
    _log(app, "✅ 剧本生成完成。进入编辑/确认。")
    _set_busy(app, False)
    _save_session(app)


def _prefill_chat(app, text):
    """把意见模板预填进底部导演对话输入框（v4.150 取代 QInputDialog 弹窗）。

    设计意图：**只预填、不自动发送**——用户补完意见自己按 Enter。这样「点按钮」
    和「真的开始烧钱」之间始终隔着用户的一次确认，跟面板原来的弹窗习惯一致，
    但不再抢占焦点、不再是一个孤立的小窗。
    """
    bar = getattr(app, "director_chat", None)
    if bar is None:
        _set_status(app, "导演对话条未就绪（重启程序可恢复）。", err=True)
        return False
    ok = bar.prefill(text)
    if ok:
        _set_status(app, f"已把指令模板填进下方对话框，补完意见按 Enter 发送 → {text}")
    return ok


@_safe
def _revise_story(app):
    _prefill_chat(app, "重写剧本：")


@_safe
def _adopt_story(app):
    app.director_pipeline.set_story(app.director_story_edit.toPlainText())
    _save_session(app)
    if app.director_pipeline.portrait_mode:
        # 本人形象口播：照片已锁定形象，跳过人物三视图，直接去分镜
        _set_status(app, "正在拆分分镜…")
        _log(app, "✓ 采用剧本 → 拆分分镜（口播模式跳过人物设定）…")
        _set_step(app, 3)
        _run_thread(app, "shots")
    else:
        _set_status(app, "正在生成人物三视图…")
        _log(app, "✓ 采用剧本 → 生成人物三视图…")
        _set_step(app, 2)
        _run_thread(app, "characters")


@_safe
def _register_assets(app, what):
    """v4.125 ④：导演台产出自动登记进资产库（命名+元数据规范先行）。

    三视图/关键帧/场景图/成片是第一批存货——军团 PM 下次同类任务
    直接查库复用，别重造。登记失败不影响导演台流程。
    """
    try:
        import asset_store
        p = getattr(app, "director_pipeline", None)
        if p is None:
            return
        topic = (getattr(p, "topic", "") or "")[:60]
        n = 0
        if what == "characters":
            for c in (getattr(p, "characters", None) or []):
                views = [v for v in (c.get("views") or []) if v and os.path.isfile(v)]
                for vi, v in enumerate(views):
                    _pos = ("正面", "侧面", "背面")[vi] if vi < 3 else f"视角{vi+1}"
                    ok, _ = asset_store.register_asset(
                        f"{c.get('name', '角色')}三视图·{_pos}", "character_views", v,
                        tags=[c.get("name", ""), "三视图", _pos] if c.get("name") else ["三视图"],
                        project=topic or "导演台", task="角色三视图生成",
                        meta={"character": c.get("name", ""), "desc": (c.get("desc") or "")[:80]})
                    n += 1 if ok else 0
        elif what == "keyframes":
            for i, kf in enumerate(getattr(p, "keyframes", None) or []):
                if kf and os.path.isfile(kf):
                    ok, _ = asset_store.register_asset(
                        f"分镜关键帧·镜{i+1}", "keyframe", kf,
                        tags=["关键帧", f"镜{i+1}"],
                        project=topic or "导演台", task="分镜关键帧+场景图生成",
                        meta={"shot": i + 1, "desc": _shot_desc(p, i)})
                    n += 1 if ok else 0
        elif what == "clues":
            for c in (getattr(p, "clues", None) or []):
                img = c.get("image")
                if img and os.path.isfile(img):
                    ok, _ = asset_store.register_asset(
                        f"场景资产·{c.get('name', '道具')}", "scene", img,
                        tags=[c.get("name", ""), "场景", c.get("kind", "")],
                        project=topic or "导演台", task="关键道具/场景资产生成",
                        meta={"desc": (c.get("desc") or "")[:80]})
                    n += 1 if ok else 0
        elif what == "final":
            fp = getattr(app, "director_final_path", None)
            if fp and os.path.isfile(fp):
                ok, _ = asset_store.register_asset(
                    f"成片·{os.path.basename(fp)}", "final", fp,
                    tags=["成片", "视频"],
                    project=topic or "导演台", task="导演台合成成片",
                    meta={"shots": len(getattr(p, "shots", None) or [])})
                n += 1 if ok else 0
        if n:
            _log(app, f"🗃️ 已登记 {n} 件资产入库（军团下次同类任务可复用）")
    except Exception:
        pass


def _shot_desc(p, i):
    try:
        s = (getattr(p, "shots", None) or [])[i]
        return str((s or {}).get("action") or (s or {}).get("desc") or "")[:80]
    except Exception:
        return ""


def _on_characters_ready(app, characters):
    _build_character_cards(app, characters)
    _register_assets(app, "characters")
    n = len(characters)
    _set_status(app, f"人物三视图已生成（{n} 个角色）。可查看/重生成，满意后「采用人物」。")
    _log(app, f"✅ 人物三视图完成（{n} 个角色）。")
    _set_busy(app, False)
    _save_session(app)


@_safe
def _revise_characters(app):
    _prefill_chat(app, "重新生成人物：")


@_safe
def _gen_clues(app):
    """抽取（或按意见重抽）关键道具 / 场景资产。"""
    p = getattr(app, "director_pipeline", None)
    if p is None:
        _set_status(app, "流程未初始化，请先点「开始导演」。", err=True)
        return
    if getattr(p, "portrait_mode", False):
        _set_status(app, "口播模式没有跨镜道具（画面已锁定本人形象）。", err=True)
        return
    if not getattr(p, "story", ""):
        _set_status(app, "请先生成并采用剧本。", err=True)
        return
    if getattr(p, "clues", None):
        # v4.150：已有道具资产时改成「预填模板」而不是弹窗——重抽走对话链路
        # （用户可补意见，如「那把剑改成木头的」）。
        _prefill_chat(app, "重新抽取道具/场景资产：")
        return
    _set_status(app, "正在抽取关键道具 / 场景资产…")
    _log(app, "🔎 抽取关键道具/场景资产…")
    _run_thread(app, "clues")


@_safe
def _on_clues_ready(app, clues):
    _build_clue_cards(app, clues)
    _register_assets(app, "clues")
    n = len(clues or [])
    if n:
        _set_status(app, f"关键道具/场景资产已就绪（{n} 件）。逐镜生成时会自动锁定它们。")
        _log(app, f"✅ 关键道具/场景资产完成（{n} 件）。")
    else:
        _set_status(app, "本剧本没有跨镜复用的关键道具/场景资产（不影响后续流程）。")
        _log(app, "ℹ️ 未抽出跨镜道具/场景资产，本次不做道具锁定。")
    _set_busy(app, False)
    _save_session(app)


def _build_clue_cards(app, clues):
    """把每件关键道具/场景资产渲染成网页卡片（含重生成 / 回滚按钮）。"""
    web = getattr(app, "director_clues_web", None)
    if web is None:
        return
    p = getattr(app, "director_pipeline", None)
    vers = (getattr(p, "clue_versions", {}) or {}) if p is not None else {}
    html = "".join(
        clue_card_html(i, c, can_rollback=bool(vers.get(i)),
                       can_versions=bool(vers.get(i)))
        for i, c in enumerate(clues or [])
    )
    if not html:
        web.render_empty("未抽出跨镜复用的道具 / 场景资产（可选，不影响流程）")
        return
    web.render_cards(html)


@_safe
def _adopt_characters(app):
    _set_status(app, "正在拆分分镜…")
    _log(app, "✓ 采用人物 → 拆分分镜…")
    _set_step(app, 3)
    _save_session(app)
    _run_thread(app, "shots")


# ---------- ② 分镜 ----------
@_safe
def _on_shots_ready(app, shots):
    _build_shot_rows(app, shots)
    # v4.154：AI 实际决定的镜数回写数字框（智能模式让框反映真实结果；手动模式本就相等）
    try:
        app.director_n.setValue(len(shots))
    except Exception:
        pass
    _set_status(app, f"分镜就绪（{len(shots)} 镜）。可逐镜修改/增删，满意后「采用分镜」。")
    _log(app, f"✅ 分镜生成完成（{len(shots)} 镜）。进入编辑/确认。")
    _set_busy(app, False)
    _save_session(app)


def _build_shot_rows(app, shots):
    from theme_qss import label_body
    _clear_layout(app.director_shots_layout)
    app.director_shot_rows = []
    with_dialogue = app.director_pipeline.with_dialogue
    for i, s in enumerate(shots):
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        no = QLabel(f"镜{i+1}")
        no.setFixedWidth(34)
        no.setStyleSheet(label_body(weight="semibold"))
        rl.addWidget(no)
        # 场景
        sc = QSpinBox()
        sc.setRange(1, 24)
        sc.setValue(int(s.get("scene", 1)))
        sc.setFixedWidth(54)
        sc.setToolTip("场景编号（同场分镜会尾帧接力）")
        sc.setStyleSheet(_combo_style())
        rl.addWidget(sc)
        # 中文
        zh = QLineEdit(s.get("zh", ""))
        zh.setPlaceholderText("中文字幕/旁白")
        zh.setStyleSheet(_line_style())
        rl.addWidget(zh, 3)
        # 英文
        en = QLineEdit(s.get("en", ""))
        en.setPlaceholderText("英文画面提示词")
        en.setStyleSheet(_line_style())
        rl.addWidget(en, 4)
        # 运镜
        cam = QLineEdit(s.get("cam", ""))
        cam.setPlaceholderText("运镜（景别/运动/机位）")
        cam.setStyleSheet(_line_style())
        rl.addWidget(cam, 3)
        # 每镜时长（秒）—— 智能模式由 AI 给出，可手动微调（Agnes 硬约束 4~12）
        dur = QSpinBox()
        dur.setRange(4, 12)
        dur.setValue(int(s.get("dur") or app.director_duration.value()))
        dur.setFixedWidth(54)
        dur.setToolTip("这一镜的时长（秒），4~12。智能模式由 AI 按内容决定，可手动微调。")
        dur.setStyleSheet(_combo_style())
        rl.addWidget(dur)
        # 台词
        if with_dialogue:
            line = QLineEdit(s.get("line", ""))
            line.setPlaceholderText("台词（中文）")
            line.setStyleSheet(_line_style())
            rl.addWidget(line, 3)
        else:
            line = None
        # 删除
        delb = QPushButton("✕")
        # v4.210.3：28→32 命中区（UI_QA §8）。注意样式来自共享的 btn_danger()，
        # 只调这一处调用点尺寸，不动共享样式函数本体。
        delb.setFixedSize(32, 32)
        delb.setCursor(Qt.PointingHandCursor)
        delb.setStyleSheet(_btn_danger_style())
        delb.clicked.connect(lambda _, r=row: _del_shot_row(app, r))
        rl.addWidget(delb)
        app.director_shots_layout.addWidget(row)
        app.director_shot_rows.append(
            {"row": row, "scene": sc, "zh": zh, "en": en, "cam": cam, "dur": dur, "line": line})
    app.director_shots_layout.addStretch(1)


def _del_shot_row(app, row):
    idx = -1
    for i, r in enumerate(app.director_shot_rows):
        if r["row"] is row:
            idx = i
            break
    if idx < 0:
        return
    app.director_shot_rows.pop(idx)
    row.deleteLater()
    # 重排序号
    for i, r in enumerate(app.director_shot_rows):
        r["row"].layout().itemAt(0).widget().setText(f"镜{i+1}")


def _add_shot_row(app):
    i = len(app.director_shot_rows)
    s = {"scene": 1, "zh": "", "en": "", "cam": "", "line": "",
         "dur": int(app.director_duration.value())}
    # 复用 _build 的单行构造：简单起见直接重建全部行
    shots = _read_shot_rows(app)
    shots.append(s)
    _build_shot_rows(app, shots)


def _read_shot_rows(app):
    out = []
    for r in app.director_shot_rows:
        out.append({
            "scene": r["scene"].value(),
            "zh": r["zh"].text().strip(),
            "en": r["en"].text().strip(),
            "cam": r["cam"].text().strip(),
            "dur": int(r["dur"].value()) if r.get("dur") else int(app.director_duration.value()),
            "line": (r["line"].text().strip() if r["line"] else ""),
        })
    return out


@_safe
def _revise_shots(app):
    _prefill_chat(app, "重排分镜：")


@_safe
def _adopt_shots(app):
    shots = _read_shot_rows(app)
    if not shots:
        _set_status(app, "分镜为空，无法继续。", err=True)
        return
    app.director_pipeline.set_shots(shots)
    _save_session(app)
    if app.director_pipeline.portrait_mode:
        # 口播模式：照片已锁定形象，跳过关键帧，直接逐镜生成
        if not _preview_prompts(app, "clip"):
            return
        _set_status(app, "正在逐镜生成视频…")
        _log(app, f"✓ 采用分镜（{len(shots)} 镜）→ 逐镜生成（口播模式跳过关键帧）…")
        _set_step(app, 5)
        _prepare_clip_cards(app, len(shots))
        _run_thread(app, "clips")
    else:
        # v4.132：关键帧先出草稿给你改，确认后才烧钱
        if not _preview_prompts(app, "keyframe"):
            return
        _set_status(app, "正在生成分镜关键帧 + 场景图…")
        _log(app, f"✓ 采用分镜（{len(shots)} 镜）→ 生成关键帧…")
        _set_step(app, 4)
        _run_thread(app, "keyframes")


@_safe
def _on_keyframes_ready(app, keyframes):
    # v4.141：整批关键帧重新生成完成，本阶段的过期标记作废
    _get_stale(app)["kf"].clear()
    _build_keyframe_cards(app, keyframes)
    _register_assets(app, "keyframes")
    ok_kf = sum(1 for x in keyframes if x)
    _set_status(app, f"关键帧+场景图已生成（{ok_kf}/{len(keyframes)} 镜有效）。可查看/重生成，满意后「采用关键帧」。")
    _log(app, f"✅ 关键帧+场景图完成（{ok_kf}/{len(keyframes)} 镜）。")
    _set_busy(app, False)
    _save_session(app)


@_safe
def _revise_keyframes(app):
    _prefill_chat(app, "重新生成全部关键帧：")


@_safe
def _adopt_keyframes(app):
    if not _preview_prompts(app, "clip"):
        return
    _set_status(app, "正在逐镜生成视频…")
    _log(app, "✓ 采用关键帧 → 逐镜生成…")
    _set_step(app, 5)
    _save_session(app)
    _prepare_clip_cards(app, len(app.director_pipeline.shots))
    _run_thread(app, "clips")


# ---------- ③ 逐镜生成 ----------
def _prepare_clip_cards(app, n):
    """初始化分镜状态并渲染占位卡片（网页网格）。"""
    app.director_clips_state = [
        {"status": "queued", "path": None, "error": "", "kf": None, "info": None}
        for _ in range(n)
    ]
    _render_clips(app)


def _versions_of(app, attr):
    """取 pipeline 上某类历史版本栈（缺失时返回空 dict），供卡片决定是否显示回滚按钮。"""
    p = getattr(app, "director_pipeline", None)
    if p is None:
        return {}
    return getattr(p, attr, {}) or {}


def _get_stale(app):
    """v4.141 P1：下游标脏表 —— 记录「上游已回滚、本产物不再对应当前资产」。

    结构：{"kf": {镜索引...}, "clip": {镜索引...}, "final": bool}
    空表时所有渲染逻辑与旧版完全一致（零行为变化），因此是低风险增量。
    """
    st = getattr(app, "_director_stale", None)
    if not isinstance(st, dict):
        st = {}
        app._director_stale = st
    if not isinstance(st.get("kf"), set):
        st["kf"] = set(st.get("kf") or [])
    if not isinstance(st.get("clip"), set):
        st["clip"] = set(st.get("clip") or [])
    st["final"] = bool(st.get("final"))
    return st


def _render_merge_stale(app):
    """按当前标脏状态重画合成预览卡（成片过期时显示 ⚠️）。"""
    fp = getattr(app, "director_final_path", None)
    if fp and os.path.isfile(fp):
        app.director_merge_web.render_cards(
            merge_card_html(fp, None, stale=bool(_get_stale(app)["final"])))


def _render_clips(app):
    """全量重渲染分镜网格（逐镜生成完/失败时调用）。"""
    cards = getattr(app, "director_clips_state", []) or []
    vers = _versions_of(app, "clip_versions")
    stale = _get_stale(app)["clip"]

    def _info(i, c):
        txt = c.get("info") or ""
        if i in stale:
            txt = (txt + " · ⚠️ 基于旧资产") if txt else "⚠️ 基于旧资产"
        return txt

    html = "".join(
        clip_card_html(i, c.get("status", "queued"), c.get("path"),
                       c.get("kf"), c.get("error", ""), _info(i, c),
                       can_rollback=bool(vers.get(i)),
                       can_versions=bool(vers.get(i)))
        for i, c in enumerate(cards)
    )
    app.director_clips_web.render_cards(html)


# 网页回滚按钮 kind → 对话指令 action（复用 agent_director_command 里已验证的回滚逻辑）
_ROLLBACK_KINDS = {
    "rollback_clip": "rollback_clip",
    "rollback_kf": "rollback_keyframe",
    "rollback_char": "rollback_character",
    "rollback_clue": "rollback_clue",
}


@_safe
def _rollback_from_web(app, kind, idx):
    """卡片上的「↩回滚」：走同一套回滚校验逻辑，成功后重渲染对应预览区。"""
    action = _ROLLBACK_KINDS.get(kind)
    if not action:
        return
    res = agent_director_command(app, {"action": action, "idx": idx + 1})
    if not isinstance(res, dict) or not res.get("ok"):
        msg = (res or {}).get("msg") if isinstance(res, dict) else None
        _set_status(app, msg or "回滚失败：还没有历史版本可回滚。", err=True)
        return
    p = getattr(app, "director_pipeline", None)
    if p is None:
        return
    if kind == "rollback_clip":
        cards = getattr(app, "director_clips_state", None) or []
        path = p.clip_paths[idx] if idx < len(p.clip_paths or []) else None
        if idx < len(cards) and path:
            # v4.125 P2-3：抽帧挪后台（与恢复/逐镜同机制）
            cards[idx].update({"status": "done", "path": path, "error": "",
                               "kf": None,
                               "info": f"镜{idx + 1} · ↩ 已回滚上一版"})
            _render_clips(app)

            def _kf_item(i2, kf):
                cs = getattr(app, "director_clips_state", None)
                if cs is not None and 0 <= i2 < len(cs):
                    cs[i2]["kf"] = kf
                    _render_clips(app)

            _kick_kf_queue(app, [(idx, path)], _kf_item)
    elif kind == "rollback_kf":
        _build_keyframe_cards(app, getattr(p, "keyframes", []) or [])
    elif kind == "rollback_char":
        _build_character_cards(app, getattr(p, "characters", []) or [])
    elif kind == "rollback_clue":
        _build_clue_cards(app, getattr(p, "clues", []) or [])
    # v4.141 P1：回滚上游资产 → 下游产物标脏。此前回滚角色/线索后，关键帧、
    # 视频、成片仍按原样显示，界面看着「角色 V1 + 关键帧 V3 + 视频 V4」自洽，
    # 其实下游全是基于已废弃的旧版本生成的（假一致性）。现在标脏并在卡片上
    # 明示；是否重做交给人决定，不自动重生成。
    _st = _get_stale(app)
    if kind in ("rollback_char", "rollback_clue"):
        for _i, _kf in enumerate(getattr(p, "keyframes", []) or []):
            if _kf:
                _st["kf"].add(_i)
        for _i, _cp in enumerate(getattr(p, "clip_paths", []) or []):
            if _cp:
                _st["clip"].add(_i)
        if getattr(app, "director_final_path", None):
            _st["final"] = True
        # 标脏后必须重画下游，否则 ⚠️ 要等下次刷新才出现
        _build_keyframe_cards(app, getattr(p, "keyframes", []) or [])
        _render_clips(app)
        _render_merge_stale(app)
        # v4.209.1：原文36 字 / 432px，是导演台 55 条 _set_status 里最贴边的一条
        #（实测容器 460px 就卡满）。去掉「下游」二字 → 408px，留出余量。
        # 信息不丢：前面已有"上游资产"，"下游"是冗余限定词。
        _set_status(app, "已回滚上游资产：关键帧/视频/成片已标记「基于旧资产」，建议重新生成")
        _log(app, "⚠️ 上游资产已回滚，下游产物已标为过期（不自动重做）")
    elif kind == "rollback_kf":
        _clips = getattr(p, "clip_paths", []) or []
        if idx < len(_clips) and _clips[idx]:
            _st["clip"].add(idx)
            _render_clips(app)
        if getattr(app, "director_final_path", None):
            _st["final"] = True
            _render_merge_stale(app)
    elif kind == "rollback_clip":
        if getattr(app, "director_final_path", None):
            _st["final"] = True
            _render_merge_stale(app)
    _save_session(app)


def _on_web_action(app, sig):
    """网页预览区按钮回调（kind:idx[:token]），来自 chat_web 的 __xc__ 通道。

    v4.141 P2：动作可携带项目令牌，用于丢弃「已切换/已重置项目」的迟到点击
    （旧 WebView 的重生成晚到 → 误打到新项目的同序号镜）。令牌为空（未注入的
    旧页面）时不做校验，保持向后兼容，绝不误杀正常操作。
    """
    if not sig or ":" not in sig:
        return
    _parts = sig.split(":")
    kind = _parts[0]
    idx_s = _parts[1] if len(_parts) > 1 else ""
    _tok = _parts[2] if len(_parts) > 2 else ""
    if _tok and _tok != project_token():
        _set_status(app, "该操作来自已切换的旧项目，已忽略。", err=True)
        return
    try:
        idx = int(idx_s)
    except ValueError:
        return
    # 运行期防重入：生成中只允许查看提示词/播放，禁止改/重生成/回滚
    if kind not in ("view", "play") and getattr(app, "director_busy", False):
        _set_status(app, "正在生成中，请稍候再操作。", err=True)
        return
    if kind == "mod":
        _modify_clip(app, idx)
    elif kind == "regen":
        _regenerate_clip(app, idx)
    elif kind == "view":
        _view_prompt(app, idx)
    elif kind == "play":
        if idx == -1:
            _play_video(app, getattr(app, "director_final_path", None))
        else:
            _play_clip(app, idx)
    elif kind in _ROLLBACK_KINDS:
        _rollback_from_web(app, kind, idx)
    elif kind == "regen_kf":
        _set_status(app, f"正在重生成 镜{idx + 1} 关键帧…")
        _log(app, f"↻ 重生成 镜{idx + 1} 关键帧…")
        _run_thread(app, "keyframe_one", idx=idx)
    elif kind == "regen_char":
        _set_status(app, f"正在重生成 角色{idx + 1} 三视图…")
        _log(app, f"↻ 重生成 角色{idx + 1} 三视图…")
        _run_thread(app, "character_one", idx=idx)
    elif kind == "regen_clue":
        _set_status(app, f"正在重生成 道具/资产{idx + 1} 参考图…")
        _log(app, f"↻ 重生成 道具/资产{idx + 1} 参考图…")
        _run_thread(app, "clue_one", idx=idx)
    elif kind in ("ask_kf", "ask_char", "ask_clue", "ask_clip"):
        # v4.150：卡片上的「✎ 说一句怎么改」——只把意见模板预填进底部对话输入框，
        # 不执行、不烧钱。用户补完意见自己按 Enter，改动走正常对话链路。
        p = getattr(app, "director_pipeline", None)
        if p is None:
            return
        try:
            if kind == "ask_kf":
                _prefill_chat(app, f"镜{idx + 1}关键帧：")
            elif kind == "ask_clip":
                _prefill_chat(app, f"镜{idx + 1}视频：")
            elif kind == "ask_char":
                chs = getattr(p, "characters", None) or []
                nm = (chs[idx].get("name") if 0 <= idx < len(chs) else "") or ""
                _prefill_chat(app, f"角色{idx + 1}（{nm}）：")
            else:  # ask_clue
                cls_ = getattr(p, "clues", None) or []
                nm = (cls_[idx].get("name") if 0 <= idx < len(cls_) else "") or ""
                _prefill_chat(app, f"道具{idx + 1}（{nm}）：")
        except Exception as e:
            _set_status(app, f"预填输入框失败：{_status_dyn(e)}", err=True)
    # v4.133 多版本对比（四类卡片共用同一个弹窗，靠 sig 区分）
    elif kind == "vers":
        _open_versions(app, "clip", idx)
    elif kind == "verskf":
        _open_versions(app, "keyframe", idx)
    elif kind == "verschar":
        _open_versions(app, "character", idx)
    elif kind == "versclue":
        _open_versions(app, "clue", idx)


def _build_character_cards(app, characters):
    """把每个角色的三视图渲染成网页卡片（可点击灯箱放大，看清人物会不会崩）。"""
    vers = _versions_of(app, "character_versions")
    html = "".join(
        character_card_html(c, idx=i, can_rollback=bool(vers.get(i)),
                            can_versions=bool(vers.get(i)))
        for i, c in enumerate(characters or [])
    )
    if not html:
        html = '<div class="empty">无角色</div>'
    app.director_characters_web.render_cards(html)


def _build_keyframe_cards(app, keyframes):
    """把每镜关键帧+场景图渲染成网页缩略图卡片（含 VLM 质检状态，可灯箱放大）。"""
    _pl = getattr(app, "director_pipeline", None)
    notes = (getattr(_pl, "review_notes", {}) or {}) if _pl else {}
    # v4.168.0（审查 #6）：传真实质检状态 —— 只传 note 的话，「质检跳过」的镜头
    # 界面上什么都不显示，看着跟"没问题"一样。
    qcs = (getattr(_pl, "qc_status", {}) or {}) if _pl else {}
    vers = _versions_of(app, "keyframe_versions")
    stale = _get_stale(app)["kf"]
    html = "".join(
        keyframe_card_html(i, kf, notes.get(i, ""), can_rollback=bool(vers.get(i)),
                           can_versions=bool(vers.get(i)), stale=(i in stale),
                           qc_status=qcs.get(i, ""))
        for i, kf in enumerate(keyframes or [])
    )
    if not html:
        html = '<div class="empty">无关键帧</div>'
    app.director_keyframes_web.render_cards(html)


@_safe
def _on_clip_ready(app, i, path):
    cards = getattr(app, "director_clips_state", None)
    if cards is None or i < 0 or i >= len(cards):
        return
    # v4.125 P2-3：抽帧挪后台线程——先落 done 状态渲染卡片，帧抽完再补图
    cards[i].update({
        "status": "done", "path": path, "error": "", "kf": None,
    })
    # v4.141：本镜已重新生成，过期标记作废（否则 ⚠️ 会一直挂着误导）
    _get_stale(app)["clip"].discard(i)
    _render_clips(app)
    _save_session(app)

    def _kf_item(idx, kf):
        cs = getattr(app, "director_clips_state", None)
        if cs is not None and 0 <= idx < len(cs):
            cs[idx]["kf"] = kf
            _render_clips(app)

    _kick_kf_queue(app, [(i, path)], _kf_item)


@_safe
def _on_clip_failed(app, i, reason=""):
    cards = getattr(app, "director_clips_state", None)
    if cards is None or i < 0 or i >= len(cards):
        return
    cards[i].update({"status": "fail", "error": reason or "", "path": None})
    _render_clips(app)
    _save_session(app)


@_safe
def _on_clips_done(app, ok, total, msg):
    _set_status(app, f"逐镜生成完成：{ok}/{total} 成功。可单镜「✎改/↻」，或「去合成」。")
    _log(app, f"✅ {msg}：{ok}/{total} 成功。")
    # v4.125 M-15：部分镜失败必须置错误标记——此前只更新卡片不置
    # _director_agent_error，线程正常结束时快照仍 ok:True，导演 Agent 会
    # 以为全部成功、基于残缺片段去合成（H-14 残留）。
    if ok < total:
        app._director_agent_error = (
            f"逐镜生成有失败：{total - ok}/{total} 镜未成功，请先单镜重试失败项")
        _log(app, f"⚠️ 有 {total - ok} 镜失败，快照将如实报失败，不可直接合成")
    _set_busy(app, False)
    _save_session(app)


def _play_clip(app, idx):
    state = getattr(app, "director_clips_state", []) or []
    if 0 <= idx < len(state):
        _play_video(app, state[idx].get("path"))


def _play_video(app, path):
    if path and os.path.isfile(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


@_safe
def _view_prompt(app, idx):
    """弹窗显示某镜实际发给模型的提示词 +（若有）失败原因，让用户看得懂、改得准。"""
    from theme_qss import label_second
    if app.director_pipeline is None:
        return
    prompt = getattr(app.director_pipeline, "last_prompts", {}).get(idx, "")
    err = ""
    state = getattr(app, "director_clips_state", []) or []
    if 0 <= idx < len(state):
        err = state[idx].get("error", "")
    dlg = QDialog(app)
    dlg.setWindowTitle(f"镜{idx+1} · 实际发给模型的提示词")
    dlg.setMinimumWidth(540)
    dlg.setStyleSheet(f"QDialog{{background:{THEME['bg']};}}")
    v = QVBoxLayout(dlg)
    v.setSpacing(12)
    v.setContentsMargins(16, 16, 16, 16)
    if err:
        el = QLabel(f"⚠️ 上次失败原因：\n{err}")
        el.setWordWrap(True)
        el.setStyleSheet(f"color:{THEME["danger_text"]};font-size:12px;background:{THEME['card']};"
                         f"border:1px solid {THEME["danger_text"]};border-radius:6px;padding:8px 12px;")
        v.addWidget(el)
    tl = QLabel("本次发给模型（Agnes）的实际提示词：")
    tl.setStyleSheet(label_second(color_key="text", weight="semibold"))
    v.addWidget(tl)
    te = QTextEdit()
    te.setReadOnly(True)
    # v4.208.0：只统一文案口径，**不接 empty_state**（DESIGN §12.5 已记理由）。
    # 与 #2 长期记忆同类：这是 QTextEdit 里的占位串，框本身是内容容器，空时框也必须在。
    te.setPlainText(prompt or "暂无，这镜可能还没生成 / 或被重置")
    te.setMinimumHeight(200)
    te.setStyleSheet(f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
                     f"border-radius:6px;padding:8px;font-size:12px;color:{THEME['text']};}}")
    v.addWidget(te)
    ok = QPushButton("关闭")
    ok.setStyleSheet(_btn_style())
    ok.clicked.connect(dlg.accept)
    v.addWidget(ok, alignment=Qt.AlignRight)
    dlg.exec()


@_safe
def _modify_clip(app, idx):
    """升级版修改：弹对话框，先展示本镜已发的提示词和失败原因，再让用户填修改意见。"""
    from theme_qss import label_second
    if app.director_pipeline is None:
        _set_status(app, "流程未初始化，请先点「开始导演」。", err=True)
        return
    prompt = getattr(app.director_pipeline, "last_prompts", {}).get(idx, "")
    err = ""
    state = getattr(app, "director_clips_state", []) or []
    if 0 <= idx < len(state):
        err = state[idx].get("error", "")
    dlg = QDialog(app)
    dlg.setWindowTitle(f"修改 镜{idx+1}")
    dlg.setMinimumWidth(560)
    dlg.setStyleSheet(f"QDialog{{background:{THEME['bg']};}}")
    v = QVBoxLayout(dlg)
    v.setSpacing(12)
    v.setContentsMargins(16, 16, 16, 16)
    if err:
        el = QLabel(f"⚠️ 上次失败原因：\n{err}")
        el.setWordWrap(True)
        el.setStyleSheet(f"color:{THEME["danger_text"]};font-size:12px;background:{THEME['card']};"
                         f"border:1px solid {THEME["danger_text"]};border-radius:6px;padding:8px 12px;")
        v.addWidget(el)
    tl = QLabel("本次已发给模型的提示词（可照抄其中想保留的设定）：")
    tl.setStyleSheet(label_second(color_key="text", weight="semibold"))
    v.addWidget(tl)
    prev = QTextEdit()
    prev.setReadOnly(True)
    # v4.208.0：同上，只统一文案口径，不接组件（QTextEdit 占位串，框必须保留）。
    prev.setPlainText(prompt or "暂无，可能这镜还没生成过")
    prev.setMaximumHeight(130)
    prev.setStyleSheet(f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
                       f"border-radius:6px;padding:8px 8px;font-size:{THEME['font_micro']};color:{THEME['dim']};}}")
    v.addWidget(prev)
    il = QLabel("你的修改意见（告诉它这一镜怎么改；留空=直接重生成）：")
    il.setStyleSheet(label_second(color_key="text", weight="semibold"))
    v.addWidget(il)
    te = QTextEdit()
    te.setPlaceholderText("例：主体换成小孩 / 背景去掉文字水印 / 镜头拉远一点 / 时长改 3 秒 / "
                          "画面太暗调亮 / 不要出现手部特写")
    te.setMinimumHeight(90)
    te.setStyleSheet(f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
                     f"border-radius:6px;padding:8px;font-size:12px;color:{THEME['text']};}}")
    v.addWidget(te)
    # 完全替换模式：内容审核被拦（content_policy_violation / 400）时，
    # 追加修改意见没用——原提示词的触发词还在。需整段覆盖原提示词。
    replace_chk = QCheckBox("完全替换原提示词（整段覆盖，不再追加修改意见）")
    replace_chk.setStyleSheet(label_second("text"))
    replace_chk.setToolTip("勾选后，上面填的内容会直接替换本镜英文提示词，而不是追加。\n"
                           "适用：Agnes 报 content_policy_violation / 400 内容违规时，"
                           "原提示词含触发词必须整段换掉。")
    v.addWidget(replace_chk)
    btns = QHBoxLayout()
    btns.setSpacing(12)
    cancel = QPushButton("取消")
    cancel.setStyleSheet(_btn_style())
    cancel.clicked.connect(dlg.reject)
    ok = QPushButton("重生成这镜")
    ok.setStyleSheet(_btn_style())
    ok.clicked.connect(dlg.accept)
    btns.addStretch(1)
    btns.addWidget(cancel)
    btns.addWidget(ok)
    v.addLayout(btns)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        note = te.toPlainText().strip() or None
        if replace_chk.isChecked() and note:
            # 整段替换 en，用 note=None 走纯重生成路径（不再追加修改意见）
            try:
                app.director_pipeline.shots[idx]["en"] = note
            except Exception:
                pass
            _set_status(app, f"已替换 镜{idx+1} 提示词，正在重生成…")
            _log(app, f"✎ 镜{idx+1}：完全替换提示词 → 重生成")
            _run_thread(app, "clip_one", idx=idx, note=None)
        else:
            _set_status(app, f"正在按意见重生成 镜{idx+1}…")
            _log(app, f"✎ 修改 镜{idx+1}：{note or '（直接重生成）'}")
            _run_thread(app, "clip_one", idx=idx, note=note)


@_safe
def _regenerate_clip(app, idx):
    _set_status(app, f"正在重生成 镜{idx+1}…")
    _log(app, f"↻ 重生成 镜{idx+1}…")
    _run_thread(app, "clip_one", idx=idx, note=None)


@_safe
def _regenerate_all(app):
    state = getattr(app, "director_clips_state", None)
    if not state:
        _set_status(app, "还没有可重新生成的片段。先跑一遍生成或重生成。")
        return
    if app.director_pipeline is None:
        _set_status(app, "流程未初始化，请先点「开始导演」。", err=True)
        return
    # v4.108 M-09：先把各卡片状态重置为排队中并立即刷新——否则全量重生成期间
    # 卡片仍显示旧的「已生成」结果，用户无法感知进度、容易误以为没生效而重复点击。
    for _c in state:
        _c["status"] = "queued"
        _c["path"] = None
        _c["error"] = ""
    try:
        _render_clips(app)
    except Exception:
        pass
    _set_status(app, "正在全部重新生成…")
    _log(app, "↺ 全部重新生成…")
    _run_thread(app, "clips")


# ---------- ④ 合成 ----------
@_safe
def _adopt_clips(app):
    _set_step(app, 6)
    _set_status(app, "进入合成步骤。可回「生成」单镜修改，或直接合成成片。")
    _log(app, "✓ 进入合成步骤。")
    _save_session(app)


@_safe
def _do_merge(app):
    _set_status(app, "正在合成成片…")
    _log(app, "🎬 合成成片…")
    app.director_merge_btn.setEnabled(False)
    _run_thread(app, "merge")


@_safe
def _on_merge_ready(app, ok, msg, path):
    # v4.141 P0：绝不在结果落地前解锁。_set_busy(False) 会立刻抓快照回给等待中的
    # Agent 工具，而本函数原本第一行就解锁 → ① 合成失败时 _director_agent_error
    # 尚未置位（失败分支原本从不置），H-14 保护形同虚设，Agent 收到 ok=True 的假
    # 成功；② 合成成功时 director_final_path 还没写入，Agent 拿到旧状态。
    # 约定同 _on_error：先写结果，最后才解锁。
    app.director_merge_btn.setEnabled(True)
    if ok and path and os.path.isfile(path):
        app.director_final_path = path
        _set_director_phase(app, DirectorPhase.DONE)   # v4.153.3 P3-11
        app._director_agent_error = None
        app._director_agent_cancelled = False
        # v4.141：新成片已产出，成片的「上游已变更」标记作废
        _get_stale(app)["final"] = False
        # v4.125 P2-3：抽帧挪后台——先渲染无帧卡片，帧到了再补
        app.director_merge_web.render_cards(merge_card_html(path, None))
        app.director_merge_play.setEnabled(True)

        def _kf_item(_idx, kf):
            fp = getattr(app, "director_final_path", None)
            if fp and os.path.isfile(fp):
                app.director_merge_web.render_cards(merge_card_html(fp, kf))

        _kick_kf_queue(app, [(-1, path)], _kf_item)
        _register_assets(app, "final")
        _set_status(app, msg)
        _log(app, f"✅ {msg}")
        # 同步进交付物面板
        name = os.path.basename(path)
        app.director_paths.append(path)
        try:
            try:  # v4.108 H-10：跨盘符回退绝对路径，避免登记静默失败
                rel = os.path.relpath(path, APP_DIR).replace("\\", "/")
            except ValueError:
                rel = path.replace("\\", "/")
            app.store.active().deliverables.append(
                {"rel": rel, "kind": "video", "name": name, "desc": rel})
            app.store.save()
            app._refresh_deliverables()
        except Exception:
            pass
        # 成片已完成，任务结束，清掉续跑存盘（下次打开不会再提示）
        _clear_session(app)
    else:
        # v4.153.3 P3-11：合成失败 → ERROR 阶段（解锁后快照如实报 failed）。
        _set_director_phase(app, DirectorPhase.ERROR)
        # v4.141 P0：失败必须先置错误标记，解锁后抓的快照才会如实报 failed。
        app._director_agent_error = f"合成失败：{msg or '未产出成片文件'}"
        app._director_agent_cancelled = False
        # v4.210.0：msg 来自后端，长度不可控 → 进状态栏前先压缩（原文仍在 _log 里）
        # 兜底串与上一行错误标记保持一致：msg 可能是 None，否则状态栏会显示"None"。
        _set_status(app, "合成失败：" + _status_dyn(msg or "未产出成片文件"), err=True)
        _log(app, "❌ 合成失败。")
        _save_session(app)
    # 结果已全部落地，此刻解锁才安全（快照拿到的一定是最终状态）。
    _set_busy(app, False)


# ---------- 停止 / 重置 / 错误 ----------
def _director_stop(app):
    """停止当前生成任务。

    v4.167.0（审查 §1 P0）：不再给一个**永不复位**的全局布尔置 True。
    现在只取消**当前这一轮 job**（`cancel_job`）—— 用户随后点
    「重试 / 续生成 / 单镜重生成」时 `begin_job()` 会换发新令牌，
    于是"停止了就只能重置整个项目"的困境解除：剧本、分镜、关键帧都不必丢。
    """
    th = getattr(app, "director_thread", None)
    _pl = getattr(app, "director_pipeline", None)
    if th and _pl is not None:
        if hasattr(_pl, "cancel_job"):
            try:
                _pl.cancel_job("用户点了停止", stage="user_stop")
            except Exception:
                _pl.cancelled = True        # 兜底：至少保持旧语义
        else:
            _pl.cancelled = True
    # v4.141 P1：取消是独立结果态。解锁抓快照时据此回报 cancelled，
    # 避免 Agent 把「用户主动取消」理解成任务完成或任务失败。
    app._director_agent_cancelled = True
    # v4.167.0：把"还能从哪继续"直接告诉用户，别让人以为只能重置
    _hint = ""
    try:
        if _pl is not None and hasattr(_pl, "job_state"):
            _st = _pl.job_state()
            if _st.get("done_clips"):
                _hint = f"（已完成 {_st['done_clips']} 镜，可继续或单镜重生成）"
    except Exception:
        _hint = ""
    _set_status(app, f"已停止{_hint} —— 可直接重试 / 续生成，不必重置项目")


def _director_reset(app):
    # v4.141 P2：重置即换发项目令牌，旧视图的迟到动作自此失效；
    # 同时清空下游标脏表（项目都没了，谈不上谁过期）。
    set_project_token("p" + os.urandom(4).hex())
    app._director_stale = {"kf": set(), "clip": set(), "final": False}
    # v4.167.0（审查 §4 P1）：后台任务统一收口（抽帧队列 / 低清预览）。
    # 原来只收口主线程，旧项目的抽帧/预览晚到会往新项目写数据。
    # 放在换发令牌之后：新项目身份已确立，旧 token 的结果一律被丢弃。
    try:
        _cancel_all_director_bg(app, reason="项目已重置", stage="reset")
    except Exception:
        pass
    th = getattr(app, "director_thread", None)
    if th and th.isRunning():
        if app.director_pipeline:
            _pl0 = app.director_pipeline
            if hasattr(_pl0, "cancel_job"):
                try:
                    _pl0.cancel_job("项目已重置", stage="reset")
                except Exception:
                    _pl0.cancelled = True
            else:
                _pl0.cancelled = True
        th.disconnect()
        # v4.145 修复④：不再在 UI 线程 th.wait(2000) 冻结界面最长 2s——
        # 已 disconnect 全部信号 + 换发项目令牌，旧线程即便仍在收尾也只是安静退出，
        # 不再触碰 UI，可直接置空引用（与 KfQueueThread/BgTaskThread 的异步清理一致）。
        # v4.125 M-13：wait 超时旧 ffmpeg 线程还在跑（zombie）——此前照样清空
        # 状态，旧线程回调会操作已被替换/清空的卡片（错误被吞、任务静默丢失、
        # 旧结果写进新任务）。现在先 disconnect 全部信号再清状态，旧线程即便
        # 完成也只是安静退出，不再触碰 UI。
        try:
            th.disconnect()
        except Exception:
            pass
        if th.isRunning():
            _log(app, "⚠️ 旧任务线程仍在收尾（已隔离其回调），稍后自行退出")
    app.director_thread = None  # v4.125 M-13：不再持有 zombie 引用
    app.director_pipeline = None
    app.director_final_path = None
    _clear_session(app)
    for w in app.director_inputs:
        w.setEnabled(True)
    app.director_params_box.setEnabled(True)
    app.director_go.setEnabled(True)
    app.director_stop.setEnabled(False)
    app.director_merge_play.setEnabled(False)
    app.director_story_edit.clear()
    _clear_layout(app.director_shots_layout)
    app.director_clips_state = []
    app.director_shot_rows = []
    app.director_clips_web.render_empty("尚未生成")
    app.director_keyframes_web.render_empty("尚未生成")
    app.director_characters_web.render_empty("尚未生成")
    app.director_clues_web.render_empty("尚未抽取关键道具 / 场景资产（可选）")
    app.director_merge_web.render_empty("尚未合成")
    app.director_log.clear()
    _set_step(app, 0)
    _set_busy(app, False)
    _set_director_phase(app, DirectorPhase.IDLE)   # v4.153.3 P3-11
    _set_status(app, "")
    _log(app, "已重置。重新填写主题和参数即可开始。")


@_safe
def _on_error(app, e):
    # v4.108 H-14：先记失败原因再解锁（_set_busy(False) 会抓快照回给 Agent）。
    app._director_agent_error = str(e)
    _set_director_phase(app, DirectorPhase.ERROR)   # v4.153.3 P3-11
    _set_busy(app, False)
    # v4.210.0：_on_error 是全局兜底，e 可能是任意异常；完整原文在
    # app._director_agent_error 与 _log 里，状态栏只放压缩后的一行。
    _set_status(app, f"出错：{_status_dyn(e)}", err=True)
    _log(app, f"❌ {e}")


# ---------- 任务持久化（关程序后继续） ----------
def _session_path():
    # v4.164.0：任务存档属运行数据，落 WORKSPACE_DIR（不再落 APP_DIR = dist = 分发源）
    return os.path.join(WORKSPACE_DIR, "director_session.json")


def _save_session(app):
    """把当前导演台任务（参数 + 各阶段产物 + 已生成片段）存盘，关程序后可续跑。

    在每次阶段产物落定（剧本/分镜/逐镜/合成）时调用；程序退出钩子也会兜底调用。
    只存 step>=1（已开始）的任务。写临时文件再原子替换，避免半截文件。
    """
    p = getattr(app, "director_pipeline", None)
    if p is None:
        return
    step = getattr(app, "director_step", 0)
    if step < 1:
        return
    try:
        state = {
            "version": 1,
            "step": step,
            "final_path": getattr(app, "director_final_path", None),
            # v4.132 项目制作规格（写一次全片带着，续跑必须还在）
            "spec": getattr(p, "spec", {}) or {},
            "params": {
                "topic": app.director_topic.toPlainText(),
                "n": app.director_n.value(),
                "duration": app.director_duration.value(),
                "resolution": app.director_resolution.currentData(),
                "style": app.director_style.currentData(),
                "dialogue": app.director_dialogue.isChecked(),
                "portrait": app.director_portrait.isChecked(),
                "relay": app.director_relay.isChecked(),
                "subtitle": app.director_subtitle.isChecked(),
                "vision_review": app.director_vision_review.isChecked(),
                "multiref": app.director_multiref.isChecked(),
                "ref_image": app.director_ref_image,
            },
            "pipeline": {
                "topic": getattr(p, "topic", ""),
                "n": getattr(p, "n", 0),
                "duration": getattr(p, "duration", 0),
                "style_key": getattr(p, "style_key", "realistic"),
                "style_prompt": getattr(p, "style_prompt", ""),
                "ref_image_path": getattr(p, "ref_image_path", None),
                "portrait_mode": getattr(p, "portrait_mode", False),
                "with_dialogue": getattr(p, "with_dialogue", False),
                "relay": getattr(p, "relay", True),
                "transition": getattr(p, "transition", "black"),
                "transition_dur": getattr(p, "transition_dur", 0.4),
                "burn_subtitles": getattr(p, "burn_subtitles", True),
                "passthrough_script": getattr(p, "passthrough_script", None),
                "width": getattr(p, "width", 768),
                "height": getattr(p, "height", 1152),
                "project_dir": getattr(p, "project_dir", None),
                "story": getattr(p, "story", ""),
                "shots": getattr(p, "shots", []),
                "characters": getattr(p, "characters", []),
                "character_lock": getattr(p, "character_lock", ""),
                "clues": getattr(p, "clues", []),
                "clue_lock": getattr(p, "clue_lock", ""),
                "keyframes": getattr(p, "keyframes", []),
                # scene_images 的 key 是场景号(int)，JSON 只认字符串键，存时转 str、读时转回 int
                "scene_images": {str(k): v for k, v in getattr(p, "scene_images", {}).items()},
                "review_notes": {str(k): v for k, v in getattr(p, "review_notes", {}).items()},
                "clip_paths": getattr(p, "clip_paths", []),
                # 版本回滚历史栈（键是镜号/序号 int，JSON 只认字符串键 → 存 str、读转回 int）
                "clip_versions": {str(k): v for k, v in getattr(p, "clip_versions", {}).items()},
                "keyframe_versions": {str(k): v for k, v in getattr(p, "keyframe_versions", {}).items()},
                "character_versions": {str(k): v for k, v in getattr(p, "character_versions", {}).items()},
                "clue_versions": {str(k): v for k, v in getattr(p, "clue_versions", {}).items()},
                "last_prompts": {str(k): v for k, v in getattr(p, "last_prompts", {}).items()},
                "last_errors": {str(k): v for k, v in getattr(p, "last_errors", {}).items()},
                # v4.132 预审改过的提示词（键是镜号 int → 存 str，读时转回）
                "keyframe_prompt_override": {str(k): v for k, v in
                                             getattr(p, "keyframe_prompt_override", {}).items()},
                "clip_prompt_override": {str(k): v for k, v in
                                         getattr(p, "clip_prompt_override", {}).items()},
                # v4.133 时间线编排（重排/裁切/转场/BGM/字幕）。没开过就是 None。
                # 内部键（trim/trans）本来就是 str，order 是 int 列表 → JSON 直接吃。
                "timeline": getattr(p, "timeline", None),
            },
        }
        sp = _session_path()
        tmp = sp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, sp)
    except Exception as e:
        try:
            _log(app, "⚠️ 任务存盘失败：" + str(e))
        except Exception:
            pass


def _clear_session(app):
    sp = _session_path()
    try:
        if os.path.isfile(sp):
            os.remove(sp)
    except Exception:
        pass


def _heal_characters(p):
    """v4.151 存档自愈：把被误当成角色的道具/陈设从人物列表清掉，并**重建**人物锁。

    为什么必须做：人物锁会被注入**每一镜**的画面提示词，一旦混进道具（大哥那次是
    「酒樽」），全片都会被误导「这件器物有脸有头发有身材」；而存盘里的 `character_lock`
    本身就是被污染的那一份，所以**不信任它、直接按过滤后的角色重建**。

    返回 `(清掉的名字列表, 保留数)`；干净数据时返回 `([], 原数量)` 且**什么都不改**
    （零副作用）。任何异常都返回空结果，绝不影响恢复流程。
    """
    try:
        from video_pipeline import looks_non_person
        old = list(getattr(p, "characters", None) or [])
        kept = [c for c in old
                if not looks_non_person(c.get("name"), c.get("desc"))]
        gone = [str(c.get("name") or "?") for c in old
                if looks_non_person(c.get("name"), c.get("desc"))]
        if gone:
            p.characters = kept
            p.character_lock = p._build_character_lock(kept)
        return gone, len(kept)
    except Exception:
        return [], len(getattr(p, "characters", None) or [])


def _load_session(app):
    """从存盘恢复任务：重建 pipeline + UI 到原步骤，已生成片段恢复预览、失败保留原因。

    返回 True 表示成功恢复；失败（文件损坏/缺失）返回 False。
    """
    sp = _session_path()
    if not os.path.isfile(sp):
        return False
    try:
        with open(sp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    step = data.get("step", 0)
    if step < 1:
        return False
    pp = data.get("params", {})
    pl = data.get("pipeline", {})
    try:
        from video_pipeline import VideoPipeline
    except Exception:
        return False
    p = VideoPipeline(app.cfg, APP_DIR, {}, auto_approve=True)
    for key in ("topic", "n", "duration", "style_key", "style_prompt", "ref_image_path",
                "portrait_mode", "with_dialogue", "relay", "transition", "transition_dur",
                "burn_subtitles", "passthrough_script", "width", "height", "project_dir",
                "story", "shots", "characters", "character_lock", "keyframes",
                "clues", "clue_lock"):
        if key in pl:
            setattr(p, key, pl[key])
    p.clip_paths = pl.get("clip_paths", [None] * len(pl.get("shots", [])))
    # v4.151 存档自愈：旧版会把道具当成角色存下来（大哥那次是「酒樽」），恢复时清掉
    # 并重建人物锁。逻辑抽成 _heal_characters 便于单测（真实存档已作为回归样本）。
    _dropped, _kept = _heal_characters(p)
    if _dropped:
        data["_v4151_healed"] = {"dropped": _dropped, "kept": _kept}
    # v4.132 项目制作规格 + 预审改稿（关程序续跑后仍生效，不被悄悄用回老提示词）
    p.spec = data.get("spec", {}) or {}
    app.director_spec = dict(p.spec)
    for _attr in ("keyframe_prompt_override", "clip_prompt_override"):
        setattr(p, _attr, {int(k): v for k, v in (pl.get(_attr) or {}).items()
                           if str(k).lstrip("-").isdigit()})
    p.last_prompts = {int(k): v for k, v in pl.get("last_prompts", {}).items()}
    p.last_errors = {int(k): v for k, v in pl.get("last_errors", {}).items()}
    # 场景图/质检记录的键是场景号与镜号（int），JSON 只认字符串键，这里转回 int
    p.scene_images = {int(k): v for k, v in (pl.get("scene_images") or {}).items()
                      if str(k).lstrip("-").isdigit()}
    p.review_notes = {int(k): v for k, v in (pl.get("review_notes") or {}).items()
                      if str(k).lstrip("-").isdigit()}
    # 版本回滚：历史栈键转回 int
    for _attr in ("clip_versions", "keyframe_versions",
                  "character_versions", "clue_versions"):
        setattr(p, _attr, {int(k): v for k, v in (pl.get(_attr) or {}).items()
                           if str(k).lstrip("-").isdigit()})
    # v4.133 时间线：存的是 dict 就沿用（关了程序重开，编排不丢）；不是 dict 一律 None
    tl = pl.get("timeline")
    p.set_timeline(tl if isinstance(tl, dict) else None)
    # 工程目录可能已被清理，确保存在（关键帧预览/重生成要用）
    if p.project_dir:
        try:
            os.makedirs(p.project_dir, exist_ok=True)
        except Exception:
            pass
        # versions/ 只在 prepare 里建过，续跑时必须补上，
        # 否则 _backup_file 因 versions_dir=None 静默不存版本 → 回滚永远无历史
        p.versions_dir = os.path.join(p.project_dir, "versions")
        try:
            os.makedirs(p.versions_dir, exist_ok=True)
        except Exception as e:
            # v4.125 P3：建目录失败要留痕——静默吞掉则回滚历史悄悄丢失无从排查。
            log.warning("versions 目录创建失败（回滚历史将不保存）: %s", e)
    app.director_pipeline = p
    # v4.168.0（审查 #5）：恢复态也要补齐工程脚手架（clips/ manifest.json 等）。
    # 恢复路径是直接 setattr 拼的 pipeline，不走 prepare —— 不补这一步，
    # 续跑时片段又掉回公共产物目录（正是本次要消灭的老问题）。
    try:
        p.ensure_project_scaffold()
    except Exception as e:
        log.warning("工程脚手架补齐失败（片段可能落回公共目录）: %s", e)
    # v4.107：载入续跑任务 → 切换导演对话历史到本项目目录
    if getattr(app, "director_chat", None) is not None:
        app.director_chat.reload_for_project()

    # 还原参数控件（仅用于展示/一致性；任务已锁定，输入框禁用）
    app.director_topic.setPlainText(pp.get("topic", ""))
    app.director_n.setValue(int(pp.get("n", 4) or 4))
    app.director_duration.setValue(int(pp.get("duration", 5) or 5))
    ri = app.director_resolution.findData(pp.get("resolution"))
    if ri >= 0:
        app.director_resolution.setCurrentIndex(ri)
    si = app.director_style.findData(pp.get("style"))
    if si >= 0:
        app.director_style.setCurrentIndex(si)
    app.director_dialogue.setChecked(bool(pp.get("dialogue", False)))
    app.director_portrait.setChecked(bool(pp.get("portrait", False)))
    app.director_relay.setChecked(bool(pp.get("relay", True)))
    app.director_subtitle.setChecked(bool(pp.get("subtitle", True)))
    app.director_vision_review.setChecked(bool(pp.get("vision_review", True)))
    app.director_multiref.setChecked(bool(pp.get("multiref", True)))
    # 恢复 pipeline 上的质检开关（会话续跑时保持一致）
    p.vision_review = app.director_vision_review.isChecked()
    p.multiref = app.director_multiref.isChecked()
    ref = pp.get("ref_image")
    app.director_ref_image = ref if (ref and os.path.isfile(ref)) else None
    if app.director_ref_image:
        pm = QPixmap(app.director_ref_image)
        if not pm.isNull():
            app.director_ref_preview.setPixmap(
                pm.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            app.director_ref_preview.setText("")
        app.director_ref_label.setText(os.path.basename(app.director_ref_image))
    else:
        app.director_ref_preview.setText("未选")
        app.director_ref_label.setText("")
    app.director_params = {
        "topic": p.topic, "n": p.n, "duration": p.duration,
        "resolution": pp.get("resolution"), "style_key": p.style_key,
        "ref_image_path": p.ref_image_path, "portrait_mode": p.portrait_mode,
        "with_dialogue": p.with_dialogue, "relay": p.relay,
        "transition": p.transition, "transition_dur": p.transition_dur,
        "burn_subtitles": p.burn_subtitles, "passthrough_script": p.passthrough_script,
    }

    # 锁定输入（任务已在进行中）
    for w in app.director_inputs:
        w.setEnabled(False)
    app.director_params_box.setEnabled(False)
    app.director_go.setEnabled(False)
    app.director_stop.setEnabled(False)

    # 还原各步骤内容
    if step >= 1:
        app.director_story_edit.setPlainText(p.story or "")
    if step >= 2 and p.characters:
        _build_character_cards(app, p.characters)
    if step >= 2 and getattr(p, "clues", None):
        _build_clue_cards(app, p.clues)
    if step >= 3:
        _build_shot_rows(app, p.shots or [])
    if step >= 4 and p.keyframes:
        _build_keyframe_cards(app, p.keyframes)
    if step >= 5:
        n = len(p.shots or [])
        state = [{"status": "queued", "path": None, "error": "", "kf": None} for _ in range(n)]
        kf_jobs = []
        for i in range(n):
            path = p.clip_paths[i] if i < len(p.clip_paths) else None
            err = p.last_errors.get(i, "")
            if path and os.path.isfile(path):
                # v4.125 P2-3：恢复时不再同步抽帧（N×30s 卡 UI）——先渲染
                # 无帧卡片，全部帧排队后台串行抽，抽到一张补一张。
                state[i] = {"status": "done", "path": path, "error": "", "kf": None}
                kf_jobs.append((i, path))
            elif err:
                state[i] = {"status": "fail", "error": err, "path": None, "kf": None}
        app.director_clips_state = state
        _render_clips(app)

        def _kf_item(idx, kf):
            cs = getattr(app, "director_clips_state", None)
            if cs is not None and 0 <= idx < len(cs):
                cs[idx]["kf"] = kf
                _render_clips(app)

        if kf_jobs:
            _kick_kf_queue(app, kf_jobs, _kf_item, cancel_old=True)
    if step >= 6:
        fp = data.get("final_path")
        if fp and os.path.isfile(fp):
            app.director_final_path = fp
            app.director_merge_web.render_cards(merge_card_html(fp, None))
            app.director_merge_play.setEnabled(True)

            def _kf_final(_idx, kf):
                f2 = getattr(app, "director_final_path", None)
                if f2 and os.path.isfile(f2):
                    app.director_merge_web.render_cards(merge_card_html(f2, kf))

            _kick_kf_queue(app, [(-1, fp)], _kf_final)

    _set_step(app, step)
    _set_status(app, f"已从上次进度恢复（进行到：{STEP_LABELS[min(step, len(STEP_LABELS)-1)]}）。可继续编辑或直接操作。")
    _log(app, f"💾 已从上次进度恢复，当前步骤：{STEP_LABELS[min(step, len(STEP_LABELS)-1)]}。")
    # v4.151：把存档自愈的结果明确告诉用户（不静默改数据）
    _heal = data.get("_v4151_healed")
    if _heal and _heal.get("dropped"):
        _log(app, "🧹 已清理上次误判为「角色」的条目：" + "、".join(_heal["dropped"]) +
                 "。它们是道具/陈设，人物锁里已不再包含；"
                 "如需锁定它们的外观，请点「🔎 抽取关键道具/场景资产」。")
    # v4.168.0（审查 #2）：恢复后若还有挂在远端的任务，给大哥两个明确选择
    try:
        _maybe_offer_remote_resume(app)
    except Exception as e:
        log.warning("远端任务接回横幅创建失败: %s", e)
    return True


def _maybe_offer_resume(app):
    """面板构建后调用：若有未完成任务，顶部显示「继续 / 放弃」横幅。"""
    from theme_qss import label_body
    sp = _session_path()
    if not os.path.isfile(sp):
        return
    try:
        with open(sp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    step = data.get("step", 0)
    if step < 1:
        return
    banner = QFrame()
    banner.setStyleSheet(
        f"QFrame{{background:{THEME['card']};border:1px solid {THEME['accent']};"
        f"border-radius:10px;}}")
    bl = QHBoxLayout(banner)
    bl.setContentsMargins(14, 10, 14, 10)
    bl.setSpacing(12)
    label = QLabel(
        f"💾 发现上次未完成的导演台任务（进行到：{STEP_LABELS[min(step, len(STEP_LABELS)-1)]}）。"
        f"可继续编辑，不必从头开始。")
    label.setStyleSheet(label_body())
    bl.addWidget(label, 1)
    cont = QPushButton("▶ 继续")
    cont.setFixedHeight(32)
    cont.setCursor(Qt.PointingHandCursor)
    cont.setStyleSheet(_btn_accent_style())
    disc = QPushButton("✕ 放弃重开")
    disc.setFixedHeight(32)
    disc.setCursor(Qt.PointingHandCursor)
    disc.setStyleSheet(_btn_style())

    def _do_continue():
        try:
            banner.deleteLater()
        except Exception:
            pass
        _load_session(app)

    def _do_discard():
        try:
            banner.deleteLater()
        except Exception:
            pass
        _clear_session(app)
        _log(app, "已放弃上次任务。重新填写主题和参数即可开始。")

    cont.clicked.connect(_do_continue)
    disc.clicked.connect(_do_discard)
    bl.addWidget(cont)
    bl.addWidget(disc)
    app.director_resume_banner = banner
    try:
        app.director_body_lay.insertWidget(0, banner)
    except Exception:
        pass


# v4.168.0（审查 #2）：远端任务接回横幅
def _maybe_offer_remote_resume(app):
    """恢复会话后调用：manifest 里还有挂在远端的任务时，给大哥两个明确选择。

    为什么必须有这一步：视频提交后拿到远端 task_id，一旦关软件 / 断网 / 点停止，
    远端任务**可能还在跑、还在消耗额度**，而本地完全不知道。以前只能重新提交
    → 重复生成、重复扣费。现在把 task_id 存进工程 manifest，重启时问一句：

        · 继续查询已提交任务 —— 只轮询 + 下载，不再提交（不重复扣费）
        · 放弃远端任务并重新生成 —— 标记放弃，之后正常重新生成
    """
    from theme_qss import label_body
    p = getattr(app, "director_pipeline", None)
    if p is None:
        return
    try:
        pend = p.pending_remote_shots()
    except Exception:
        return
    if not pend:
        return
    shots = "、".join(f"第{n}镜" for n, _ in pend[:8])
    more = "" if len(pend) <= 8 else f" 等 {len(pend)} 镜"
    banner = QFrame()
    banner.setStyleSheet(
        f"QFrame{{background:{THEME['card']};border:1px solid {THEME['accent2']};"
        f"border-radius:10px;}}")
    bl = QHBoxLayout(banner)
    bl.setContentsMargins(14, 10, 14, 10)
    bl.setSpacing(12)
    label = QLabel(
        f"🔌 发现 {len(pend)} 个**已提交但没接回**的远端视频任务（{shots}{more}）。\n"
        f"远端可能仍在生成、仍在消耗额度。建议先「继续查询」，别急着重新生成。")
    label.setWordWrap(True)
    label.setStyleSheet(label_body())
    bl.addWidget(label, 1)
    btn_resume = QPushButton("🔌 继续查询已提交任务")
    btn_resume.setFixedHeight(32)
    btn_resume.setCursor(Qt.PointingHandCursor)
    btn_resume.setStyleSheet(_btn_accent_style())
    btn_abandon = QPushButton("🗑 放弃并重新生成")
    btn_abandon.setFixedHeight(32)
    btn_abandon.setCursor(Qt.PointingHandCursor)
    btn_abandon.setStyleSheet(_btn_style())

    def _do_resume():
        try:
            banner.deleteLater()
        except Exception:
            pass
        _log(app, f"🔌 开始接回 {len(pend)} 个远端任务（只轮询下载，不重新提交）…")

        def _work(_p=p):
            # 不在后台线程碰 UI：done 回调里统一重渲染（_kick_bg 的 done 已带项目令牌校验）
            return _p.resume_remote_clips()

        def _done(res, err):
            if err:
                _log(app, f"❌ 接回远端任务失败：{err}")
                return
            try:
                ok_n, fails = res
            except Exception:
                ok_n, fails = 0, []
            _log(app, f"🔌 接回完成：成功 {ok_n} 镜"
                      + (f"，失败 {len(fails)} 镜（可单镜重生成）" if fails else ""))
            try:
                _render_clips(app)
            except Exception:
                pass

        _kick_bg(app, _work, _done, pipeline=p, on_log=lambda t: _log(app, t))

    def _do_abandon():
        try:
            banner.deleteLater()
        except Exception:
            pass
        try:
            marked = p.abandon_remote_clips()
            _log(app, f"🗑 已放弃 {len(marked)} 个远端任务（不再轮询）。"
                      f"需要的话点「重生成」重新出片；远端无法真正撤销，"
                      f"但它不会再影响本项目。")
        except Exception as e:
            _log(app, f"⚠️ 放弃远端任务失败：{e}")

    btn_resume.clicked.connect(_do_resume)
    btn_abandon.clicked.connect(_do_abandon)
    bl.addWidget(btn_resume)
    bl.addWidget(btn_abandon)
    app.director_remote_banner = banner
    try:
        app.director_body_lay.insertWidget(0, banner)
    except Exception:
        pass


# ---------- 小工具 ----------
def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        else:
            sub = item.layout()
            if sub is not None:
                _clear_layout(sub)


# =====================================================================
# v4.132 专业导演台（对标 Flova 工作台）
#   ① 项目制作规格（文档区）  ② 媒体库  ③ Prompt 草稿预审  ④ 导出三件套
# =====================================================================

# 制作规格五个字段：key / 界面名 / 占位提示
SPEC_FIELDS = (
    ("setting", "故事设定", "世界观、人物关系、时代背景。写一次，后面每步自动带上。"),
    ("style", "视觉风格", "色调 / 质感 / 光影 / 画风，会写进每一镜的画面提示词。"),
    ("camera", "镜头语言", "景别与运镜规则，例：多用中近景，禁止快速变焦。"),
    ("forbid", "禁用内容", "画面与台词里绝对不许出现的东西，例：不许出现文字招牌。"),
    ("output", "输出要求", "平台、总时长、字幕、节奏等交付要求。"),
)


@_safe
def _open_project_spec(app):
    """项目制作规格（Flova「文档区」同款）：写一次，全片每一步都带着。"""
    from theme_qss import label_body, label_second
    spec = dict(getattr(app, "director_spec", {}) or {})
    dlg = QDialog(app)
    dlg.setWindowTitle("项目设定 · 制作规格")
    _fit_dlg(dlg, 680, 640)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 18, 20, 16)
    lay.setSpacing(8)

    hint = QLabel("写一次，之后 剧本 / 分镜 / 人物 / 道具 / 关键帧 / 视频 每一步都自动带上。"
                  "留空 = 不约束（与没填时行为完全一致，不会多加一个字）。")
    hint.setWordWrap(True)
    hint.setStyleSheet(label_second())
    lay.addWidget(hint)

    edits = {}
    for key, label, ph in SPEC_FIELDS:
        lb = QLabel(label)
        lb.setStyleSheet(label_body())
        lay.addWidget(lb)
        e = QTextEdit()
        e.setPlainText(spec.get(key, "") or "")
        e.setPlaceholderText(ph)
        e.setFixedHeight(62)
        e.setStyleSheet(_edit_style())
        edits[key] = e
        lay.addWidget(e)

    btns = QHBoxLayout()
    btns.addStretch(1)
    cancel = QPushButton("取消")
    cancel.setFixedHeight(38)
    cancel.setCursor(Qt.PointingHandCursor)
    cancel.setStyleSheet(_btn_style())
    ok_btn = QPushButton("保存并生效")
    ok_btn.setFixedHeight(38)
    ok_btn.setCursor(Qt.PointingHandCursor)
    ok_btn.setStyleSheet(_btn_accent_style())
    btns.addWidget(cancel)
    btns.addWidget(ok_btn)
    lay.addLayout(btns)
    cancel.clicked.connect(dlg.reject)
    ok_btn.clicked.connect(dlg.accept)

    if not dlg.exec():
        return
    new = {k: (e.toPlainText() or "").strip() for k, e in edits.items()}
    app.director_spec = new
    p = getattr(app, "director_pipeline", None)
    if p is not None:
        try:
            p.set_spec(new)
        except Exception:
            pass
    _save_session(app)
    n = sum(1 for v in new.values() if v)
    _log(app, f"📐 项目制作规格已保存（{n} 项生效），后续每一步都会带上。")
    _set_status(app, f"项目制作规格已生效（{n} 项）。")


# ---------- ② 媒体库 ----------
def _char_linked_shots(p, name):
    """角色出现在哪几镜（名字在分镜文本里命中即算）。"""
    idxs = []
    for i, s in enumerate(p.shots or []):
        txt = " ".join(str((s or {}).get(k) or "") for k in ("zh", "en", "line"))
        if name and name in txt:
            idxs.append(str(i + 1))
    return ("镜" + "、".join(idxs)) if idxs else "未关联"


def _clue_linked_shots(p, k):
    """道具/资产出现在哪几镜。"""
    try:
        name = str((p.clues or [])[k].get("name") or "").strip()
    except Exception:
        return "未关联"
    if not name:
        return "未关联"
    idxs = []
    for i in range(len(p.shots or [])):
        try:
            hits = p._shot_clues(i) or []
        except Exception:
            hits = []
        if any(str((c or {}).get("name") or "").strip() == name for c in hits):
            idxs.append(str(i + 1))
    return ("镜" + "、".join(idxs)) if idxs else "未关联"


def _media_items(app):
    """收集本片全部素材 → [(类别, 名称, 关联, 路径), ...]"""
    p = getattr(app, "director_pipeline", None)
    items = []
    if p is None:
        return items
    for ci, c in enumerate(p.characters or []):
        nm = str((c or {}).get("name") or f"角色{ci + 1}")
        linked = _char_linked_shots(p, nm)
        for j, v in enumerate((c or {}).get("views") or []):
            tag = ("正面", "侧面", "背面")[j] if j < 3 else f"视图{j + 1}"
            items.append(("人物三视图", f"{nm} · {tag}", linked, v))
    for k, c in enumerate(p.clues or []):
        items.append(("道具/资产", str((c or {}).get("name") or f"道具{k + 1}"),
                      _clue_linked_shots(p, k), (c or {}).get("image")))
    for i, kf in enumerate(p.keyframes or []):
        items.append(("关键帧", f"镜{i + 1} 关键帧", f"镜{i + 1}", kf))
    try:
        for sc in sorted((p.scene_images or {}).keys(), key=lambda x: str(x)):
            items.append(("场景图", f"场景{sc}", f"场景{sc}", (p.scene_images or {}).get(sc)))
    except Exception:
        pass
    for i, cp in enumerate(p.clip_paths or []):
        items.append(("视频片段", f"镜{i + 1} 片段", f"镜{i + 1}", cp))
    fp = getattr(app, "director_final_path", None)
    if fp:
        items.append(("成片", os.path.basename(fp), "全片", fp))
    return items


@_safe
def _open_media_library(app):
    """媒体库：本片全部素材一览，标注挂在哪一镜 / 未关联。"""
    from theme_qss import label_second
    items = _media_items(app)
    if not items:
        _set_status(app, "还没有素材。先点「开始导演」，跑起来后这里就有东西。")
        return

    dlg = QDialog(app)
    dlg.setWindowTitle("媒体库 · 本片素材")
    _fit_dlg(dlg, 920, 600)
    root = QHBoxLayout(dlg)
    root.setContentsMargins(18, 16, 18, 16)
    root.setSpacing(12)

    left = QVBoxLayout()
    tip = QLabel("「关联」= 这件素材用在哪一镜；标「未关联」的说明它还没参与任何一镜的生成。")
    tip.setWordWrap(True)
    tip.setStyleSheet(label_second())
    left.addWidget(tip)

    tbl = QTableWidget(len(items), 4)
    tbl.setHorizontalHeaderLabels(["类型", "名称", "关联", "文件"])
    tbl.verticalHeader().setVisible(False)
    tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setStyleSheet(
        f"QTableWidget{{background:{THEME['card']};border:1px solid {THEME['border']};"
        f"border-radius:10px;color:{THEME['text']};font-size:12px;gridline-color:{THEME['border']};}}"
        f"QHeaderView::section{{background:{THEME['bg']};color:{THEME['dim']};"
        f"border:none;padding:8px;font-size:12px;}}")
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
    paths = []
    for r, (kind, name, linked, path) in enumerate(items):
        paths.append(path)
        vals = [kind, name, linked, (path or "（缺失）")]
        for c, v in enumerate(vals):
            it = QTableWidgetItem(str(v))
            if c == 2 and str(v) == "未关联":
                it.setForeground(QColor(THEME["warn_amber"]))
            tbl.setItem(r, c, it)
    left.addWidget(tbl, 1)

    right = QVBoxLayout()
    right.setSpacing(12)
    prev = QLabel("预览")
    prev.setFixedSize(260, 260)
    prev.setAlignment(Qt.AlignCenter)
    prev.setStyleSheet(f"QLabel{{background:{THEME['bg']};border:1px solid {THEME['border']};"
                       f"border-radius:10px;color:{THEME['dim']};font-size:12px;}}")
    right.addWidget(prev)
    right.addStretch(1)

    def _cur_path():
        r = tbl.currentRow()
        return paths[r] if 0 <= r < len(paths) else None

    def _refresh_preview():
        path = _cur_path()
        if not path or not os.path.isfile(path):
            prev.setText("文件缺失")
            prev.setPixmap(QPixmap())
            return
        if os.path.splitext(path)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            pm = QPixmap(path)
            prev.setPixmap(pm.scaled(248, 248, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            prev.setText("")
        else:
            prev.setPixmap(QPixmap())
            prev.setText("视频 / 音频文件\n（点「播放」查看）")

    tbl.itemSelectionChanged.connect(_refresh_preview)

    def _on_dbl():
        path = _cur_path()
        if not path or not os.path.isfile(path):
            return
        if os.path.splitext(path)[1].lower() in (".mp4", ".mov", ".webm", ".mkv"):
            _play_video(app, path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    tbl.doubleClicked.connect(_on_dbl)

    def _open_folder():
        path = _cur_path()
        if path and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def _set_as_ref():
        path = _cur_path()
        if not path or not os.path.isfile(path):
            _set_status(app, "这个文件不存在，不能设为参考图。", err=True)
            return
        if os.path.splitext(path)[1].lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            _set_status(app, "只有图片能设为参考图。", err=True)
            return
        app.director_ref_image = path
        p = getattr(app, "director_pipeline", None)
        if p is not None:
            p.ref_image_path = path
        try:
            app.director_ref_label.setText(os.path.basename(path))
            app.director_ref_preview.setPixmap(
                QPixmap(path).scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            pass
        _log(app, f"📎 已把「{os.path.basename(path)}」设为参考图（后续生成锁这张）。")
        _set_status(app, f"参考图已设为 {os.path.basename(path)}。")

    for txt, fn in (("打开所在文件夹", _open_folder),
                    ("设为参考图（锁这张）", _set_as_ref),
                    ("▶ 播放", lambda: _play_video(app, _cur_path()))):
        b = QPushButton(txt)
        b.setFixedHeight(34)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(_btn_small_style())
        b.clicked.connect(fn)
        right.addWidget(b)

    root.addLayout(left, 1)
    root.addLayout(right)

    close = QPushButton("关闭")
    close.setFixedHeight(36)
    close.setCursor(Qt.PointingHandCursor)
    close.setStyleSheet(_btn_style())
    close.clicked.connect(dlg.accept)
    right.addWidget(close)

    try:
        tbl.selectRow(0)
    except Exception:
        pass
    _refresh_preview()
    dlg.exec()


# ---------- ③ Prompt 草稿预审 ----------
def _prompt_preview_dialog(app, title, tip, drafts):
    """列出每镜 prompt 草稿供编辑。返回 {镜号: 文本}；点取消返回 None。"""
    from theme_qss import scroll_transparent
    from theme_qss import label_second
    dlg = QDialog(app)
    dlg.setWindowTitle(title)
    _fit_dlg(dlg, 780, 640)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(18, 16, 18, 14)
    lay.setSpacing(12)

    hint = QLabel(tip)
    hint.setWordWrap(True)
    hint.setStyleSheet(label_second())
    lay.addWidget(hint)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet(scroll_transparent())
    body = QWidget()
    bl = QVBoxLayout(body)
    bl.setContentsMargins(0, 0, 0, 0)
    bl.setSpacing(8)
    edits = {}
    for idx, text in drafts:
        lb = QLabel(f"镜 {idx}")
        lb.setStyleSheet(label_second())
        bl.addWidget(lb)
        e = QTextEdit()
        e.setPlainText(text or "")
        e.setFixedHeight(62)
        e.setStyleSheet(_edit_style())
        edits[idx] = e
        bl.addWidget(e)
    scroll.setWidget(body)
    lay.addWidget(scroll, 1)

    btns = QHBoxLayout()
    btns.addStretch(1)
    cancel = QPushButton("取消")
    cancel.setFixedHeight(38)
    cancel.setCursor(Qt.PointingHandCursor)
    cancel.setStyleSheet(_btn_style())
    go = QPushButton("按此生成")
    go.setFixedHeight(38)
    go.setCursor(Qt.PointingHandCursor)
    go.setStyleSheet(_btn_accent_style())
    btns.addWidget(cancel)
    btns.addWidget(go)
    lay.addLayout(btns)
    cancel.clicked.connect(dlg.reject)
    go.clicked.connect(dlg.accept)

    if not dlg.exec():
        return None
    return {idx: e.toPlainText() for idx, e in edits.items()}


def _preview_prompts(app, kind):
    """生成前预审草稿。返回 False=用户取消（调用方 return），True=继续生成。"""
    p = getattr(app, "director_pipeline", None)
    if p is None:
        return True
    try:
        if not getattr(app, "director_preview", None) or not app.director_preview.isChecked():
            return True
    except Exception:
        return True
    try:
        if kind == "keyframe":
            drafts = p.build_keyframe_drafts()
            title = "关键帧提示词预审"
            tip = ("这些是即将送去生图的提示词（已含人物锁定与项目规格）。"
                   "可以直接改，改完点「按此生成」——不烧钱之前先看清楚。")
        else:
            drafts = p.build_clip_drafts()
            title = "视频提示词预审"
            tip = ("这些是即将送去生成视频的提示词。"
                   "上一镜尾帧那张参考图要等上一镜生成完才有，会在生成时自动追加，草稿里看不到。")
    except Exception as e:
        _log(app, f"⚠️ 提示词预审草稿生成失败（{e}），按原样继续。")
        return True
    if not drafts:
        return True
    res = _prompt_preview_dialog(app, title, tip, drafts)
    if res is None:
        _set_status(app, "已取消这一步生成（不想要预审可在参数区关掉「Prompt 预审」）。")
        _log(app, "⏸ 预审取消：未调用生成接口，没有产生费用。")
        return False
    try:
        n = p.set_prompt_overrides(kind, res)
    except Exception:
        n = 0
    if n:
        _log(app, f"✎ 预审改稿：{n} 镜用你改后的提示词（之后重生成这一镜也继续用）。")
    return True


# ---------- ④ 导出三件套 ----------
@_safe
def _open_export_dialog(app):
    """导出：成片 / 工程文件（EDL+FCPXML）/ 素材包。"""
    from theme_qss import label_second
    p = getattr(app, "director_pipeline", None)
    if p is None:
        _set_status(app, "还没有工程可导出。先点「开始导演」建一个。")
        return

    dlg = QDialog(app)
    dlg.setWindowTitle("导出")
    _fit_dlg(dlg, 560, 300)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(22, 20, 22, 18)
    lay.setSpacing(12)
    tip = QLabel("三选一，按需导出：\n"
                 "· 成片：把 mp4 另存到你要的位置\n"
                 "· 工程文件：EDL + FCPXML，可直接拖进剪映 / PR / 达芬奇精修\n"
                 "· 素材包：zip，含片段 + 关键帧 + 三视图 + 道具图 + 分镜表 + 剧本")
    tip.setWordWrap(True)
    tip.setStyleSheet(label_second())
    lay.addWidget(tip)

    def _do(kind):
        try:
            if kind == "final":
                src = getattr(app, "director_final_path", None)
                if not src or not os.path.isfile(src):
                    _set_status(app, "还没合成成片，无法导出。", err=True)
                    return
                dst, _ = QFileDialog.getSaveFileName(
                    app, "导出成片", os.path.basename(src), "视频 (*.mp4)")
                if not dst:
                    return
                shutil.copy2(src, dst)
                _log(app, f"💾 成片已导出：{dst}")
                _set_status(app, f"成片已导出到 {dst}")
            elif kind == "project":
                d = QFileDialog.getExistingDirectory(app, "选择工程文件保存位置")
                if not d:
                    return
                base = os.path.basename((p.project_dir or "director").rstrip("\\/")) or "director"
                ok1, m1 = p.export_edl(os.path.join(d, f"{base}.edl"))
                ok2, m2 = p.export_fcpxml(os.path.join(d, f"{base}.fcpxml"))
                if ok1 or ok2:
                    _log(app, f"💾 工程文件已导出：{d}（EDL {'✓' if ok1 else '✗ ' + m1}"
                              f" / FCPXML {'✓' if ok2 else '✗ ' + m2}）")
                    _set_status(app, f"工程文件已导出到 {d}")
                else:
                    # v4.210.0：m1/m2 是后端原始报错，长度不可控 → 压缩
                    # （两个都失败才走到这里，取先出现的那条；完整原文在 _log）
                    _set_status(app,
                                f"工程文件导出失败：{_status_dyn(m1 or m2)}", err=True)
            else:
                dst, _ = QFileDialog.getSaveFileName(
                    app, "导出素材包", "导演台素材包.zip", "压缩包 (*.zip)")
                if not dst:
                    return
                ok, msg = p.export_bundle(dst, final_path=getattr(app, "director_final_path", None))
                if ok:
                    _log(app, f"💾 素材包已导出：{msg}")
                    _set_status(app, "素材包已导出。")
                else:
                    _set_status(app, msg, err=True)
        except Exception as e:
            _set_status(app, f"导出失败：{_status_dyn(e)}", err=True)
            _log(app, f"❌ 导出失败：{e}")
        dlg.accept()

    for txt, kind in (("导出成片（mp4 另存）", "final"),
                      ("导出工程文件（EDL + FCPXML）", "project"),
                      ("导出素材包（zip）", "bundle")):
        b = QPushButton(txt)
        b.setFixedHeight(40)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(_btn_style())
        b.clicked.connect(lambda _=False, k=kind: _do(k))
        lay.addWidget(b)
    lay.addStretch(1)
    close = QPushButton("关闭")
    close.setFixedHeight(36)
    close.setCursor(Qt.PointingHandCursor)
    close.setStyleSheet(_btn_style())
    close.clicked.connect(dlg.accept)
    lay.addWidget(close)
    dlg.exec()


# =====================================================================
# v4.133 专业导演台（二）：可视化时间线 / 音频轨 / 多版本对比
# =====================================================================
# 对标 Flova 的「剪辑时间线 + 音频轨 + 资产多版本」。
# 设计红线同 v4.132：不动时间线 = 完全不碰原有合成链路。

# ASS 的 PrimaryColour 是 &HAABBGGRR（BGR 反序），别按 RGB 填
_SUB_COLORS = (
    ("白字黑边（默认）", "&H00FFFFFF"),
    ("黄字黑边", "&H0000FFFF"),
    ("青字黑边", "&H00FFFF00"),
    ("绿字黑边", "&H0000FF00"),
    ("红字黑边", "&H000000FF"),
)

_VER_KIND_NAME = {"clip": "片段", "keyframe": "关键帧",
                  "character": "角色", "clue": "道具"}


@_safe
def _open_timeline(app):
    """时间线编排（重排 / 裁切 / 转场）+ 音频轨（BGM / 音量 / 闪避）+ 字幕样式。"""
    from theme_qss import label_second
    p = getattr(app, "director_pipeline", None)
    if p is None or not [x for x in (getattr(p, "clip_paths", None) or []) if x]:
        _set_status(app, "还没有可用片段。先去「生成」出片，再来编排时间线。")
        return
    # v4.133.1：留一份打开前的编排。预览会把当前界面参数写进 p.timeline，
    # 用户点「取消」时得还原回去——否则自以为没改，内存里其实已经改了。
    tl_snapshot = copy.deepcopy(p.timeline) if isinstance(p.timeline, dict) else None
    if not isinstance(getattr(p, "timeline", None), dict):
        p.set_timeline(p.timeline_default())
    tl = p.timeline
    info = p.timeline_info()
    order = [int(x) for x in (info.get("order") or [])]
    meta = {}
    for it in (info.get("items") or []):
        try:
            meta[int(it["idx"])] = it
        except (TypeError, ValueError, KeyError):
            continue

    dlg = QDialog(app)
    dlg.setWindowTitle("时间线编排 · 音频与字幕")
    _fit_dlg(dlg, 900, 590)
    root = QVBoxLayout(dlg)
    root.setContentsMargins(18, 16, 18, 14)
    root.setSpacing(12)
    tabs = QTabWidget()
    root.addWidget(tabs, 1)

    # ---------------- Tab2：音频与字幕（先建，Tab1 的「应用」要读这些控件） ----------------
    aud0 = tl.get("audio") if isinstance(tl.get("audio"), dict) else {}
    sub0 = tl.get("sub") if isinstance(tl.get("sub"), dict) else {}
    tab2 = QWidget()
    l2 = QVBoxLayout(tab2)
    l2.setContentsMargins(12, 12, 12, 12)
    l2.setSpacing(12)

    g1 = QGroupBox("背景音乐")
    f1 = QVBoxLayout(g1)
    f1.setSpacing(8)
    r1 = QHBoxLayout()
    bgm_edit = QLineEdit(str(aud0.get("bgm") or ""))
    bgm_edit.setPlaceholderText("选一个音频文件（mp3 / wav / m4a），留空就是不加 BGM")
    bgm_edit.setStyleSheet(_edit_style())
    r1.addWidget(bgm_edit, 1)

    def _pick_bgm():
        f, _ = QFileDialog.getOpenFileName(
            app, "选择背景音乐", "", "音频 (*.mp3 *.wav *.m4a *.aac *.flac)")
        if f:
            bgm_edit.setText(f)

    b_bgm = QPushButton("浏览…")
    b_bgm.setFixedHeight(32)
    b_bgm.setStyleSheet(_btn_small_style())
    b_bgm.clicked.connect(_pick_bgm)
    r1.addWidget(b_bgm)
    b_bgmc = QPushButton("清除")
    b_bgmc.setFixedHeight(32)
    b_bgmc.setStyleSheet(_btn_small_style())
    b_bgmc.clicked.connect(lambda: bgm_edit.setText(""))
    r1.addWidget(b_bgmc)
    f1.addLayout(r1)

    r2 = QHBoxLayout()
    r2.addWidget(QLabel("音量"))
    vol_slider = QSlider(Qt.Horizontal)
    vol_slider.setRange(0, 100)
    try:
        vol0 = int(round(float(aud0.get("vol", 0.3)) * 100))
    except (TypeError, ValueError):
        vol0 = 30
    vol_slider.setValue(max(0, min(100, vol0)))
    vol_lab = QLabel(f"{vol_slider.value()}%")
    vol_lab.setFixedWidth(46)
    vol_slider.valueChanged.connect(lambda v: vol_lab.setText(f"{v}%"))
    r2.addWidget(vol_slider, 1)
    r2.addWidget(vol_lab)
    f1.addLayout(r2)

    duck_chk = QCheckBox("有人声时自动压低 BGM（闪避）")
    duck_chk.setChecked(bool(aud0.get("duck", True)))
    duck_chk.setStyleSheet(_chk_style())
    f1.addWidget(duck_chk)
    l2.addWidget(g1)

    g2 = QGroupBox("字幕")
    f2 = QVBoxLayout(g2)
    f2.setSpacing(8)
    sub_burn = QCheckBox("成片烧录字幕（台词）")
    sub_burn.setChecked(bool(sub0.get("burn", True)))
    sub_burn.setStyleSheet(_chk_style())
    f2.addWidget(sub_burn)
    r3 = QHBoxLayout()
    r3.addWidget(QLabel("字号"))
    sub_size = QSpinBox()
    sub_size.setRange(10, 48)
    try:
        sub_size.setValue(int(sub0.get("size") or 18))
    except (TypeError, ValueError):
        sub_size.setValue(18)
    r3.addWidget(sub_size)
    r3.addSpacing(14)
    r3.addWidget(QLabel("颜色"))
    sub_color = QComboBox()
    for nm, val in _SUB_COLORS:
        sub_color.addItem(nm, val)
    cur_c = str(sub0.get("color") or "&H00FFFFFF").upper()
    hit = 0
    for k, (_nm, val) in enumerate(_SUB_COLORS):
        if val.upper() == cur_c:
            hit = k
            break
    sub_color.setCurrentIndex(hit)
    sub_color.setStyleSheet(_combo_style())
    r3.addWidget(sub_color, 1)
    r3.addSpacing(14)
    r3.addWidget(QLabel("距底"))
    sub_pos = QSpinBox()
    sub_pos.setRange(0, 200)
    try:
        sub_pos.setValue(int(sub0.get("pos") or 30))
    except (TypeError, ValueError):
        sub_pos.setValue(30)
    r3.addWidget(sub_pos)
    f2.addLayout(r3)
    l2.addWidget(g2)

    tip2 = QLabel("BGM 只在「合成成片」时混入，片段自带的人声/音效照常保留。\n"
                  "音量 20~35% 是人声为主时的经验值；开着闪避，说话时 BGM 会自动让路。")
    tip2.setWordWrap(True)
    tip2.setStyleSheet(label_second())
    l2.addWidget(tip2)
    l2.addStretch(1)

    # ---------------- Tab1：时间线 ----------------
    tab1 = QWidget()
    l1 = QVBoxLayout(tab1)
    l1.setContentsMargins(12, 12, 12, 10)
    l1.setSpacing(8)
    hint = QLabel("顺序用 ↑↓ 调；入点/出点按素材实际秒数裁；转场指这一镜「后面」接什么。"
                  "改完点「应用」，之后合成与导出都按这条时间线走。")
    hint.setWordWrap(True)
    hint.setStyleSheet(label_second())
    l1.addWidget(hint)

    cols = ["#", "镜", "内容", "素材", "入点", "出点", "转场", "转场时长", "本镜"]
    tbl = QTableWidget(0, len(cols))
    tbl.setHorizontalHeaderLabels(cols)
    tbl.verticalHeader().setVisible(False)
    tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
    l1.addWidget(tbl, 1)

    total_lab = QLabel("")
    total_lab.setStyleSheet(label_second())

    def _refresh_total():
        t = 0.0
        for pos in range(tbl.rowCount()):
            a = tbl.cellWidget(pos, 4)
            b = tbl.cellWidget(pos, 5)
            c = tbl.cellWidget(pos, 6)
            d = tbl.cellWidget(pos, 7)
            if a is not None and b is not None:
                t += max(0.0, float(b.value()) - float(a.value()))
            if c is not None and d is not None and c.currentData():
                t += float(d.value())
        total_lab.setText(f"成片总时长约 {t:.1f} 秒 · 共 {tbl.rowCount()} 个镜")

    def _fill():
        tbl.setRowCount(0)
        for pos, i in enumerate(order):
            it = meta.get(i, {})
            try:
                src = float(it.get("src_dur") or 5)
            except (TypeError, ValueError):
                src = 5.0
            tbl.insertRow(pos)

            def _cell(txt):
                w = QTableWidgetItem(str(txt))
                w.setTextAlignment(Qt.AlignCenter)
                return w

            tbl.setItem(pos, 0, _cell(pos + 1))
            tbl.setItem(pos, 1, _cell(f"镜{i + 1}"))
            d = str(it.get("desc") or "")
            if it.get("scene"):
                d = f"[{it['scene']}] {d}"
            tbl.setItem(pos, 2, QTableWidgetItem(d[:60]))
            tbl.setItem(pos, 3, _cell(f"{src:.1f}s"))

            sp_in = QDoubleSpinBox()
            sp_in.setRange(0.0, max(0.2, src))
            sp_in.setDecimals(1)
            sp_in.setSingleStep(0.1)
            sp_out = QDoubleSpinBox()
            sp_out.setRange(0.0, max(0.2, src))
            sp_out.setDecimals(1)
            sp_out.setSingleStep(0.1)
            try:
                sp_in.setValue(float(it.get("in") or 0))
            except (TypeError, ValueError):
                sp_in.setValue(0.0)
            try:
                sp_out.setValue(float(it.get("out") or src))
            except (TypeError, ValueError):
                sp_out.setValue(src)
            dur_lab = QLabel()
            dur_lab.setAlignment(Qt.AlignCenter)

            def _sync(_v=0, _a=sp_in, _b=sp_out, _lab=dur_lab, _src=src):
                a, b = float(_a.value()), float(_b.value())
                if b < a + 0.2:
                    _b.blockSignals(True)
                    _b.setValue(min(_src, a + 0.2))
                    _b.blockSignals(False)
                    b = float(_b.value())
                _lab.setText(f"{max(0.0, b - a):.1f}s")
                _refresh_total()

            sp_in.valueChanged.connect(_sync)
            sp_out.valueChanged.connect(_sync)

            cb = QComboBox()
            cb.addItem("无", "")
            cb.addItem("黑场", "black")
            cb.addItem("白场", "white")
            cur_tr = str(it.get("trans") or "")
            if cur_tr.startswith("black"):
                cb.setCurrentIndex(1)
            elif cur_tr.startswith("white"):
                cb.setCurrentIndex(2)
            sd = QDoubleSpinBox()
            sd.setRange(0.2, 3.0)
            sd.setDecimals(1)
            sd.setSingleStep(0.1)
            try:
                sd.setValue(float(cur_tr.split(":")[1]) if ":" in cur_tr else 0.4)
            except (ValueError, IndexError):
                sd.setValue(0.4)
            sd.valueChanged.connect(lambda _v: _refresh_total())
            cb.currentIndexChanged.connect(lambda _v: _refresh_total())

            tbl.setCellWidget(pos, 4, sp_in)
            tbl.setCellWidget(pos, 5, sp_out)
            tbl.setCellWidget(pos, 6, cb)
            tbl.setCellWidget(pos, 7, sd)
            tbl.setCellWidget(pos, 8, dur_lab)
            _sync()
        tbl.resizeRowsToContents()
        _refresh_total()

    def _collect():
        trim, trans = {}, {}
        for pos, i in enumerate(order):
            a = tbl.cellWidget(pos, 4)
            b = tbl.cellWidget(pos, 5)
            c = tbl.cellWidget(pos, 6)
            d = tbl.cellWidget(pos, 7)
            if a is not None and b is not None:
                try:
                    src = float(meta.get(i, {}).get("src_dur") or 0)
                except (TypeError, ValueError):
                    src = 0.0
                va, vb = float(a.value()), float(b.value())
                if va > 0.01 or (src and vb < src - 0.01):
                    trim[str(i)] = [round(va, 2), round(vb, 2)]
            if c is not None and c.currentData():
                dv = float(d.value()) if d is not None else 0.4
                trans[str(i)] = f"{c.currentData()}:{dv:.2f}"
        try:
            vol = round(vol_slider.value() / 100.0, 2)
        except Exception:
            vol = 0.3
        return {"order": list(order), "trim": trim, "trans": trans,
                "audio": {"bgm": bgm_edit.text().strip(),
                          "vol": vol,
                          "duck": duck_chk.isChecked()},
                "sub": {"burn": sub_burn.isChecked(),
                        "size": int(sub_size.value()),
                        "color": sub_color.currentData() or "&H00FFFFFF",
                        "pos": int(sub_pos.value())}}

    def _move(delta):
        r = tbl.currentRow()
        if r < 0:
            _set_status(app, "先选中一行再移动。", err=True)
            return
        n = r + delta
        if n < 0 or n >= len(order):
            return
        order[r], order[n] = order[n], order[r]
        _fill()
        tbl.selectRow(n)

    def _reset_trim():
        r = tbl.currentRow()
        if r < 0:
            return
        try:
            src = float(meta.get(order[r], {}).get("src_dur") or 5)
        except (TypeError, ValueError):
            src = 5.0
        a = tbl.cellWidget(r, 4)
        b = tbl.cellWidget(r, 5)
        if a is not None:
            a.setValue(0.0)
        if b is not None:
            b.setValue(src)

    def _restore_order():
        order[:] = [i for i in range(len(p.clip_paths or []))
                    if (p.clip_paths or [])[i]]
        _fill()

    def _drop():
        r = tbl.currentRow()
        if r < 0:
            return
        if len(order) <= 1:
            _set_status(app, "至少保留一个镜。", err=True)
            return
        order.pop(r)
        _fill()

    def _preview():
        # v4.133.1：真跑一遍 ffmpeg，十几秒起步。放 UI 线程 = 窗口假死标「无响应」，
        # 所以走后台线程，渲染期间把预览按钮禁用防重复点。
        p.set_timeline(_collect())
        _set_status(app, "正在渲染低清预览（不烧字幕），十几秒到一分钟，界面可继续操作…")
        if b_prev is not None:
            b_prev.setEnabled(False)
            b_prev.setText("⏳ 渲染中…")

        def _done(res, err):
            if b_prev is not None:
                b_prev.setEnabled(True)
                b_prev.setText("👁 低清预览")
            out = None
            perr = err or ""
            if isinstance(res, tuple) and len(res) == 2:
                out, perr = res[0], (res[1] or perr)
            if out:
                _play_video(app, out)
                _set_status(app, "预览已生成并播放（低清，仅供确认顺序/裁切/转场）。")
                _log(app, f"👁 时间线预览：{out}")
            else:
                _set_status(app, f"预览失败：{perr or '未知原因'}", err=True)

        if _kick_bg(app, p.render_preview, _done, pipeline=p,
                    on_log=lambda t: _log(app, t)) is None:
            if b_prev is not None:
                b_prev.setEnabled(True)
                b_prev.setText("👁 低清预览")
            _set_status(app, "预览启动失败。", err=True)

    b_prev = None
    brow = QHBoxLayout()
    brow.setSpacing(8)
    for txt, fn, tip in (("↑ 上移", lambda: _move(-1), "把选中的镜往前挪一位"),
                         ("↓ 下移", lambda: _move(1), "把选中的镜往后挪一位"),
                         ("✂ 重置裁切", _reset_trim, "这一镜恢复用完整素材"),
                         ("✖ 移出", _drop, "成片里不要这一镜"),
                         ("↺ 恢复原序", _restore_order, "按分镜原始顺序重排"),
                         ("👁 低清预览", _preview, "渲一版小尺寸预览确认效果")):
        b = QPushButton(txt)
        b.setFixedHeight(32)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(_btn_small_style())
        b.setToolTip(tip)
        b.clicked.connect(fn)
        brow.addWidget(b)
        if txt.startswith("👁"):
            b_prev = b
    brow.addStretch(1)
    l1.addLayout(brow)
    l1.addWidget(total_lab)

    tabs.addTab(tab1, "时间线")
    tabs.addTab(tab2, "音频与字幕")
    _fill()

    # ---------------- 底部 ----------------
    brow2 = QHBoxLayout()
    brow2.setSpacing(12)

    def _apply():
        p.set_timeline(_collect())
        _save_session(app)
        tl2 = p.timeline or {}
        tot = p.timeline_info().get("total")
        _log(app, f"🎞 时间线已应用：{len(tl2.get('order') or [])} 镜 / "
                  f"裁切 {len(tl2.get('trim') or {})} 处 / "
                  f"转场 {len(tl2.get('trans') or {})} 处 / 总时长 {tot} 秒")
        _set_status(app, "时间线已应用，点「合成成片」按这条线出片。")
        dlg.accept()

    def _off():
        p.set_timeline(None)
        _save_session(app)
        _log(app, "🎞 已停用时间线：按原始镜序合成。")
        _set_status(app, "时间线已停用，恢复按原始镜序合成。")
        dlg.accept()

    b_apply = QPushButton("应用（按这条时间线合成）")
    b_apply.setFixedHeight(38)
    b_apply.setCursor(Qt.PointingHandCursor)
    b_apply.setStyleSheet(_btn_accent_style())
    b_apply.clicked.connect(_apply)
    brow2.addWidget(b_apply)
    b_off = QPushButton("停用时间线")
    b_off.setFixedHeight(38)
    b_off.setCursor(Qt.PointingHandCursor)
    b_off.setStyleSheet(_btn_style())
    b_off.setToolTip("回到原始镜序，不做任何重排/裁切/转场")
    b_off.clicked.connect(_off)
    brow2.addWidget(b_off)
    brow2.addStretch(1)
    b_cancel = QPushButton("取消")
    b_cancel.setFixedHeight(38)
    b_cancel.setCursor(Qt.PointingHandCursor)
    b_cancel.setStyleSheet(_btn_style())
    b_cancel.clicked.connect(dlg.reject)
    brow2.addWidget(b_cancel)
    root.addLayout(brow2)
    if not dlg.exec():
        p.set_timeline(tl_snapshot)   # 取消 → 回到打开前的编排（含「未启用」）


@_safe
def _open_versions(app, kind, idx):
    """单镜/角色/道具的多版本并排对比：看清差别后挑一版，一键换上。"""
    from theme_qss import label_second
    p = getattr(app, "director_pipeline", None)
    if p is None:
        return
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return
    items = p.version_items(kind, idx)
    if len(items) < 2:
        _set_status(app, "还没有历史版本。改一次（✎改 / ↻重生成）就有了。")
        return
    kname = _VER_KIND_NAME.get(kind, kind)

    dlg = QDialog(app)
    dlg.setWindowTitle(f"版本对比 · {kname}{idx + 1}（共 {len(items)} 版）")
    _fit_dlg(dlg, 800, 480)
    lay = QHBoxLayout(dlg)
    lay.setContentsMargins(16, 14, 16, 12)
    lay.setSpacing(12)

    lst = QListWidget()
    lst.setFixedWidth(280)
    for it in items:
        tag = " · 当前" if it.get("current") else ""
        lst.addItem(f"第 {it['no']} 版 · {it.get('ts') or '—'} · "
                    f"{it.get('size_kb', 0)}KB{tag}")
    lay.addWidget(lst)

    right = QVBoxLayout()
    right.setSpacing(12)
    prev = QLabel("—")
    prev.setFixedSize(380, 300)
    prev.setAlignment(Qt.AlignCenter)
    prev.setStyleSheet(f"border:1px solid {THEME['border']};background:{THEME['card']};"
                       f"color:{THEME['dim']};font-size:12px;")
    right.addWidget(prev, 0, Qt.AlignCenter)
    info_lab = QLabel("")
    info_lab.setWordWrap(True)
    info_lab.setStyleSheet(label_second())
    right.addWidget(info_lab)

    def _show(row):
        if row < 0 or row >= len(items):
            return
        it = items[row]
        path = it.get("path")
        pm = QPixmap()
        if kind == "clip":
            kf = None
            try:
                kf = p._head_frame_path(path)
            except Exception:
                kf = None
            if kf and os.path.isfile(kf):
                pm = QPixmap(kf)
        elif path and os.path.isfile(path):
            pm = QPixmap(path)
        if pm.isNull():
            prev.setPixmap(QPixmap())
            prev.setText("（无法预览）")
        else:
            prev.setText("")
            prev.setPixmap(pm.scaled(prev.width() - 10, prev.height() - 10,
                                     Qt.KeepAspectRatio, Qt.SmoothTransformation))
        d = it.get("desc") or ""
        info_lab.setText(f"第 {it['no']} 版 · {it.get('ts') or '—'} · "
                         f"{it.get('size_kb', 0)}KB"
                         f"{' · 当前正在使用' if it.get('current') else ''}"
                         + (f"\n{d}" if d else ""))
        btn_use.setEnabled(not it.get("current"))

    def _play_cur():
        row = lst.currentRow()
        if 0 <= row < len(items):
            _play_video(app, items[row].get("path"))

    def _open_dir():
        row = lst.currentRow()
        if 0 <= row < len(items):
            fp = items[row].get("path")
            if fp:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(fp)))

    def _use():
        row = lst.currentRow()
        if row < 0 or row >= len(items):
            return
        it = items[row]
        if it.get("current"):
            return
        # 先给当前版留个「回得来」的备份（入历史栈，界面立刻看得到）。
        # v4.133.1：此前只 _backup_file 不入栈（=磁盘孤儿，界面找不到），
        # 且角色/道具两种 kind 压根没备份——切走就再也回不来了。
        try:
            p._snapshot_current(kind, idx)
        except Exception:
            pass
        fn = {"clip": p.rollback_clip, "keyframe": p.rollback_keyframe,
              "character": p.rollback_character, "clue": p.rollback_clue}.get(kind)
        if fn is None:
            return
        r = fn(idx, it.get("vi", -1))
        # 角色/道具回滚成功返回的是名字（可能为空串），只用 None 判失败
        if r is None:
            _set_status(app, "换版失败（该版本文件可能已丢失）。", err=True)
            return
        if kind == "clip":
            cards = getattr(app, "director_clips_state", None) or []
            if idx < len(cards):
                cards[idx].update({"status": "done", "path": r, "error": "",
                                   "kf": None,
                                   "info": f"镜{idx + 1} · 🔀 已换用第 {it['no']} 版"})
                _render_clips(app)

                def _kf_item(i2, kf2):
                    cs = getattr(app, "director_clips_state", None)
                    if cs is not None and 0 <= i2 < len(cs):
                        cs[i2]["kf"] = kf2
                        _render_clips(app)

                _kick_kf_queue(app, [(idx, r)], _kf_item)
        elif kind == "keyframe":
            _build_keyframe_cards(app, getattr(p, "keyframes", []) or [])
        elif kind == "character":
            _build_character_cards(app, getattr(p, "characters", []) or [])
        else:
            _build_clue_cards(app, getattr(p, "clues", []) or [])
        _save_session(app)
        _log(app, f"🔀 {kname}{idx + 1} 已换用第 {it['no']} 版。")
        _set_status(app, f"{kname}{idx + 1} 已换用第 {it['no']} 版。")
        dlg.accept()

    rbtn = QHBoxLayout()
    rbtn.setSpacing(8)
    for txt, fn, acc in (("▶ 播放/打开", _play_cur, False),
                         ("📂 所在文件夹", _open_dir, False),
                         ("✅ 用这一版", _use, True)):
        b = QPushButton(txt)
        b.setFixedHeight(34)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(_btn_accent_style() if acc else _btn_small_style())
        b.clicked.connect(fn)
        if acc:
            btn_use = b
        rbtn.addWidget(b)
    rbtn.addStretch(1)
    right.addLayout(rbtn)
    right.addStretch(1)
    lay.addLayout(right, 1)

    lst.currentRowChanged.connect(_show)
    lst.setCurrentRow(len(items) - 1)
    dlg.exec()
