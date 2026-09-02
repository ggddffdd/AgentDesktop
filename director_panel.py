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
import traceback
import functools

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QLineEdit, QSpinBox,
    QComboBox, QGroupBox, QFileDialog, QCheckBox, QListWidget, QListWidgetItem,
    QStackedWidget, QScrollArea, QWidget, QGridLayout, QFrame, QSizePolicy,
    QInputDialog, QDialog,
)
from PySide6.QtGui import QPixmap, QIcon, QDesktopServices
from PySide6.QtCore import Qt, QSize, QThread, Signal, QUrl

from ui import THEME
from config import APP_DIR


# ---------- 异常兜底装饰器 ----------
# PySide6 信号槽（含 QThread.run / 跨线程 queued 槽）里的未捕获异常
# 不走 sys.excepthook，会直接打到 stderr 然后 PyQt 硬崩。这里统一兜底：
# 崩了也不再静默死，而是写 app.log + 在面板状态栏提示，便于定位。
def _safe(fn):
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
            return None
    return _w

# 分辨率预设（与生视频 / 数字人面板保持一致）
RES_PRESETS = [
    ("竖屏 1080×1920 (9:16)", "1080x1920"),
    ("竖屏 720×1280 (9:16)", "720x1280"),
    ("竖屏 768×1152 (3:4)", "768x1152"),
    ("横屏 1920×1080 (16:9)", "1920x1080"),
    ("横屏 1280×720 (16:9)", "1280x720"),
    ("横屏 1152×768 (4:3)", "1152x768"),
    ("横屏 1088×832 (4:3)", "1088x832"),
    ("方形 1024×1024 (1:1)", "1024x1024"),
]

STYLE_ITEMS = [
    ("写实 realistic", "realistic"),
    ("电影感 cinematic", "cinematic"),
    ("动画 anime", "anime"),
    ("水彩 watercolor", "watercolor"),
    ("霓虹 neon", "neon"),
    ("纪录片 documentary", "documentary"),
]

STEP_LABELS = ["主题", "剧本", "人物", "分镜", "关键帧", "生成", "合成"]


# ---------- 样式 ----------
def _btn_style():
    return (f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
            f"border:1px solid {THEME['border']};border-radius:8px;padding:0 14px;font-size:13px;}}"
            f"QPushButton:hover{{background:{THEME['blue_hover']};}}"
            f"QPushButton:disabled{{color:{THEME['dim']};}}")


def _btn_accent_style():
    return (f"QPushButton{{background:{THEME['accent']};color:#FFFFFF;border:none;"
            f"border-radius:8px;padding:0 18px;font-size:14px;font-weight:500;}}"
            f"QPushButton:hover{{background:{THEME['accent_hover']};}}"
            f"QPushButton:disabled{{background:{THEME['dim']};}}")


def _btn_danger_style():
    return (f"QPushButton{{background:{THEME['card']};color:#ef4444;"
            f"border:1px solid {THEME['border']};border-radius:8px;padding:0 12px;font-size:12px;}}"
            f"QPushButton:hover{{background:#3a1f1f;}}")


def _btn_small_style():
    return (f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
            f"border:1px solid {THEME['border']};border-radius:6px;padding:0 10px;font-size:12px;}}"
            f"QPushButton:hover{{background:{THEME['blue_hover']};}}"
            f"QPushButton:disabled{{color:{THEME['dim']};}}")


def _edit_style():
    return (f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:10px;padding:10px 12px;font-size:13px;color:{THEME['text']};}}"
            f"QTextEdit:focus{{border:1px solid {THEME['accent']};}}")


def _combo_style():
    return (f"QComboBox{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:8px;padding:0 10px;font-size:13px;color:{THEME['text']};}}"
            f"QComboBox::drop-down{{border:none;}}"
            f"QComboBox:disabled{{color:{THEME['dim']};}}")


def _chk_style():
    return (f"QCheckBox{{color:{THEME['text']};font-size:13px;spacing:6px;}}"
            f"QCheckBox::indicator{{width:16px;height:16px;}}")


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
            elif self.task == "merge":
                out, err = p.merge()
                if out:
                    self.merge_ready.emit(True, f"成片完成：{os.path.basename(out)}", out)
                else:
                    self.merge_ready.emit(False, err or "合成失败", "")
        except Exception as e:
            self.error.emit(f"{self.task} 异常：{e}")


# ---------- 面板构建 ----------
def build_director_panel(app):
    page = app.director_page

    # 主滚动区（防止内容溢出压到对话框）
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet(f"QScrollArea{{border:none;background:transparent;}}")
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    body = QWidget()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(24, 16, 24, 20)
    lay.setSpacing(10)

    head = QLabel("导演台 · video-agent")
    head.setStyleSheet(f"font-size:20px;font-weight:700;color:{THEME['text']};")
    lay.addWidget(head)
    sub = QLabel("主题 → ①剧本（可改）→ ②人物三视图（角色锁定）→ ③分镜（逐镜可改）→ "
                 "④关键帧+场景图 → ⑤逐镜生成（每镜可预览/单镜改）→ ⑥合成成片。"
                 "每步都自动把人物三视图/关键帧作参照，减少人物与场景崩坏。")
    sub.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
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
            ar.setStyleSheet(f"color:{THEME['dim']};font-size:14px;")
            step_lay.addWidget(ar)
    lay.addWidget(step_box)

    # ---------- 参数区（步骤0可改，开始后禁用） ----------
    app.director_inputs = []
    params = QGroupBox("创作参数")
    params.setStyleSheet(f"QGroupBox{{background:transparent;border:1px solid {THEME['border']};"
                         f"border-radius:10px;padding:16px 18px 14px;font-size:13px;color:{THEME['text']};}}")
    pl = QVBoxLayout(params)
    pl.setSpacing(14)

    # --- 主题 ---
    tlab = QLabel("视频主题 / 口播原稿（中文，越具体越好）")
    tlab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
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

    # --- 第一行：分镜数 / 每镜时长 ---
    row1 = QHBoxLayout()
    row1.setSpacing(12)
    nlab = QLabel("分镜数")
    nlab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
    nlab.setFixedWidth(50)
    row1.addWidget(nlab)
    app.director_n = _spin((1, 24), 4, " 镜")
    app.director_n.setFixedWidth(90)
    row1.addWidget(app.director_n)
    app.director_inputs.append(app.director_n)

    dlab = QLabel("每镜")
    dlab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
    dlab.setFixedWidth(40)
    row1.addWidget(dlab)
    # 新版 agnes-video-2.5-flash 时长合法范围 4~12 秒（旧版 3~16s）
    app.director_duration = _spin((4, 12), 5, " 秒")
    app.director_duration.setFixedWidth(90)
    row1.addWidget(app.director_duration)
    app.director_inputs.append(app.director_duration)

    rlab = QLabel("分辨率")
    rlab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
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
    slab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
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
    # 复选框紧凑横排
    app.director_dialogue = QCheckBox("台词")
    app.director_portrait = QCheckBox("本人形象口播")
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
    for c in (app.director_dialogue, app.director_portrait, app.director_relay,
              app.director_subtitle, app.director_vision_review):
        c.setStyleSheet(_chk_style())
        row2.addWidget(c)
        app.director_inputs.append(c)
    row2.addStretch(1)
    pl.addLayout(row2)

    # --- 第三行：参考图 ---
    ref = QHBoxLayout()
    ref.setSpacing(12)
    app.director_ref_btn = QPushButton("📎 选择参考图（首帧锁定 / 口播形象）")
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
        f"border-radius:8px;color:{THEME['dim']};font-size:11px;}}")
    ref.addWidget(app.director_ref_preview)
    app.director_ref_label = QLabel("")
    app.director_ref_label.setStyleSheet(f"color:{THEME['dim']};font-size:12px;")
    ref.addWidget(app.director_ref_label, 1)
    app.director_ref_image = None
    pl.addLayout(ref)

    lay.addWidget(params)
    app.director_params_box = params

    # 动作行（开始 / 停止 / 重新开始 / 状态）
    act = QHBoxLayout()
    act.setSpacing(12)
    app.director_go = QPushButton("🎬 开始导演")
    app.director_go.setFixedHeight(38)
    app.director_go.setCursor(Qt.PointingHandCursor)
    app.director_go.setStyleSheet(_btn_accent_style())
    app.director_go.clicked.connect(lambda: _director_start(app))
    act.addWidget(app.director_go)
    app.director_stop = QPushButton("■ 停止")
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
    act.addStretch(1)
    lay.addLayout(act)
    app.director_status = QLabel("")
    app.director_status.setStyleSheet(f"color:{THEME['dim']};font-size:12px;")
    lay.addWidget(app.director_status)

    # ---------- 步骤内容（堆叠） ----------
    app.director_stack = QStackedWidget()
    lay.addWidget(app.director_stack, 1)

    # 页1：剧本
    story_page = QWidget()
    sl = QVBoxLayout(story_page)
    sl.setContentsMargins(0, 0, 0, 0)
    sl.setSpacing(10)
    shint = QLabel("① 剧本已生成。可直接编辑下面文字，或点「重写剧本」并附修改意见；满意后点「采用剧本」。")
    shint.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    sl.addWidget(shint)
    app.director_story_edit = QTextEdit()
    app.director_story_edit.setReadOnly(False)
    app.director_story_edit.setStyleSheet(_edit_style())
    sl.addWidget(app.director_story_edit, 1)
    sbtns = QHBoxLayout()
    sbtns.setSpacing(12)
    app.director_story_revise = QPushButton("✎ 重写剧本（可附意见）")
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
    cl0.setSpacing(10)
    chint = QLabel("② 人物三视图已生成。每个角色含正面/侧面/背面三视图，可据此判断人物是否会崩；"
                   "满意后点「采用人物 → 去分镜」。不满意可「重新生成人物」并附意见。")
    chint.setWordWrap(True)
    chint.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    cl0.addWidget(chint)
    characters_scroll = QScrollArea()
    characters_scroll.setWidgetResizable(True)
    characters_scroll.setStyleSheet(f"QScrollArea{{border:none;background:transparent;}}")
    app.director_characters_body = QWidget()
    app.director_characters_layout = QVBoxLayout(app.director_characters_body)
    app.director_characters_layout.setContentsMargins(0, 0, 0, 0)
    app.director_characters_layout.setSpacing(8)
    characters_scroll.setWidget(app.director_characters_body)
    cl0.addWidget(characters_scroll, 1)
    cbtns = QHBoxLayout()
    cbtns.setSpacing(12)
    app.director_characters_revise = QPushButton("✎ 重新生成人物（可附意见）")
    app.director_characters_revise.setFixedHeight(36)
    app.director_characters_revise.setStyleSheet(_btn_style())
    app.director_characters_revise.clicked.connect(lambda: _revise_characters(app))
    app.director_characters_adopt = QPushButton("✓ 采用人物 → 去分镜")
    app.director_characters_adopt.setFixedHeight(36)
    app.director_characters_adopt.setCursor(Qt.PointingHandCursor)
    app.director_characters_adopt.setStyleSheet(_btn_accent_style())
    app.director_characters_adopt.clicked.connect(lambda: _adopt_characters(app))
    cbtns.addWidget(app.director_characters_revise)
    cbtns.addStretch(1)
    cbtns.addWidget(app.director_characters_adopt)
    cl0.addLayout(cbtns)
    app.director_stack.addWidget(characters_page)
    app.director_character_cards = []

    # 页2：分镜
    shots_page = QWidget()
    shl = QVBoxLayout(shots_page)
    shl.setContentsMargins(0, 0, 0, 0)
    shl.setSpacing(10)
    shhint = QLabel("② 分镜已生成。可逐镜修改「中文/英文提示词/运镜/台词/场景」，也可删镜或加镜；"
                    "满意后点「采用分镜 → 生成」。")
    shhint.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    shl.addWidget(shhint)
    shots_scroll = QScrollArea()
    shots_scroll.setWidgetResizable(True)
    shots_scroll.setStyleSheet(f"QScrollArea{{border:none;background:transparent;}}")
    app.director_shots_body = QWidget()
    app.director_shots_layout = QVBoxLayout(app.director_shots_body)
    app.director_shots_layout.setContentsMargins(0, 0, 0, 0)
    app.director_shots_layout.setSpacing(8)
    shots_scroll.setWidget(app.director_shots_body)
    shl.addWidget(shots_scroll, 1)
    shbtns = QHBoxLayout()
    shbtns.setSpacing(12)
    app.director_shots_revise = QPushButton("✎ 重排分镜（可附意见）")
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
    kl.setSpacing(10)
    khint = QLabel("④ 分镜关键帧+场景图已生成。每镜一张首帧参照图，逐镜生成时会自动作为首帧注入，"
                   "人物与场景更不易崩坏；满意后点「采用关键帧 → 去生成」。可「重新生成关键帧」并附意见。")
    khint.setWordWrap(True)
    khint.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    kl.addWidget(khint)
    keyframes_scroll = QScrollArea()
    keyframes_scroll.setWidgetResizable(True)
    keyframes_scroll.setStyleSheet(f"QScrollArea{{border:none;background:transparent;}}")
    app.director_keyframes_body = QWidget()
    app.director_keyframes_grid = QGridLayout(app.director_keyframes_body)
    app.director_keyframes_grid.setContentsMargins(0, 0, 0, 0)
    app.director_keyframes_grid.setSpacing(12)
    keyframes_scroll.setWidget(app.director_keyframes_body)
    kl.addWidget(keyframes_scroll, 1)
    kbtns = QHBoxLayout()
    kbtns.setSpacing(12)
    app.director_keyframes_revise = QPushButton("✎ 重新生成关键帧（可附意见）")
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
    cl.setSpacing(10)
    app.director_clips_progress = QLabel("⑤ 准备逐镜生成…")
    app.director_clips_progress.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    cl.addWidget(app.director_clips_progress)
    clips_scroll = QScrollArea()
    clips_scroll.setWidgetResizable(True)
    clips_scroll.setStyleSheet(f"QScrollArea{{border:none;background:transparent;}}")
    app.director_clips_body = QWidget()
    app.director_clips_grid = QGridLayout(app.director_clips_body)
    app.director_clips_grid.setContentsMargins(0, 0, 0, 0)
    app.director_clips_grid.setSpacing(12)
    clips_scroll.setWidget(app.director_clips_body)
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
    app.director_clip_cards = []

    # 页4：合成
    merge_page = QWidget()
    ml = QVBoxLayout(merge_page)
    ml.setContentsMargins(0, 0, 0, 0)
    ml.setSpacing(10)
    mhint = QLabel("⑥ 全部片段已生成。可回「生成」步骤单镜修改，或直接点「合成成片」。")
    mhint.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    ml.addWidget(mhint)
    app.director_merge_preview = QLabel("尚未合成")
    app.director_merge_preview.setFixedHeight(200)
    app.director_merge_preview.setAlignment(Qt.AlignCenter)
    app.director_merge_preview.setStyleSheet(
        f"QLabel{{background:{THEME['bg']};border:1px solid {THEME['border']};"
        f"border-radius:10px;color:{THEME['dim']};font-size:13px;}}")
    ml.addWidget(app.director_merge_preview, 1)
    mbtns = QHBoxLayout()
    mbtns.setSpacing(12)
    app.director_merge_btn = QPushButton("🎬 合成成片")
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
        f"border-radius:10px;padding:8px 10px;font-size:12px;color:{THEME['text']};}}")
    lay.addWidget(app.director_log)

    # 把所有内容装入滚动区，挂到页面
    scroll.setWidget(body)
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(scroll)

    # 运行期状态
    app.director_step = 0
    app.director_pipeline = None
    app.director_thread = None
    app.director_paths = []
    app.director_shot_rows = []
    app.director_busy = False
    app.director_body_lay = lay
    _set_step(app, 0)
    _log(app, "导演台就绪。填好主题和参数，点「开始导演」。")

    # 若上次有未完成的任务，顶部提示可继续（不必从头来）
    _maybe_offer_resume(app)


# ---------- 步骤切换 ----------
def _step_style(active, done=False):
    if active:
        return (f"QLabel{{background:{THEME['accent']};color:#FFFFFF;border-radius:14px;"
                f"font-size:13px;font-weight:600;padding:0 10px;}}")
    if done:
        return (f"QLabel{{background:{THEME['card']};color:#22c55e;border:1px solid {THEME['border']};"
                f"border-radius:14px;font-size:13px;padding:0 10px;}}")
    return (f"QLabel{{background:transparent;color:{THEME['dim']};border:1px solid {THEME['border']};"
            f"border-radius:14px;font-size:13px;padding:0 10px;}}")


def _set_step(app, step):
    app.director_step = step
    for i, lb in enumerate(app.director_step_labels):
        lb.setStyleSheet(_step_style(i == step, done=(i < step)))
    if step >= 1:
        app.director_stack.setCurrentIndex(step - 1)


def _log(app, text):
    app.director_log.append(text)


def _set_status(app, text, err=False):
    color = THEME["accent"] if not err else "#ef4444"
    app.director_status.setStyleSheet(f"color:{color};font-size:12px;")
    app.director_status.setText(text)


def _set_busy(app, busy):
    """运行锁：线程跑任务期间禁用所有会再触发线程的按钮，防止并发重入导致崩溃。

    注意：director_go / director_stop 由 start/stop/reset 单独管理，这里不动。
    """
    app.director_busy = busy
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
    # 单镜卡片里的 ↻/✎改/🔍 按钮也一并禁用
    for c in getattr(app, "director_clip_cards", []) or []:
        for key in ("play", "mod", "regen", "view"):
            b = c.get(key)
            if b is not None:
                try:
                    b.setEnabled(not busy)
                except Exception:
                    pass


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


def _director_start(app):
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
    if not topic_raw and not app.director_ref_image:
        _set_status(app, "请填写主题，或上传参考图/本人照片。", err=True)
        return
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
    )
    app.director_params = params

    # 锁定参数、禁用输入
    for w in app.director_inputs:
        w.setEnabled(False)
    app.director_params_box.setEnabled(False)

    from video_pipeline import VideoPipeline
    app.director_pipeline = VideoPipeline(app.cfg, APP_DIR, {}, auto_approve=True)
    app.director_pipeline.prepare(**params)
    # VLM 质检开关在 prepare 之后再挂，避免改动 VideoPipeline.prepare 的签名
    app.director_pipeline.vision_review = app.director_vision_review.isChecked()

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
    th.story_ready.connect(lambda t: _on_story_ready(app, t))
    th.shots_ready.connect(lambda s: _on_shots_ready(app, s))
    th.characters_ready.connect(lambda c: _on_characters_ready(app, c))
    th.keyframes_ready.connect(lambda k: _on_keyframes_ready(app, k))
    th.clip_ready.connect(lambda i, p: _on_clip_ready(app, i, p))
    th.clip_failed.connect(lambda i: _on_clip_failed(app, i))
    th.clips_done.connect(lambda ok, tot, msg: _on_clips_done(app, ok, tot, msg))
    th.merge_ready.connect(lambda ok, msg, p: _on_merge_ready(app, ok, msg, p))
    th.error.connect(lambda e: _on_error(app, e))
    app.director_thread = th
    th.start()


# ---------- ① 剧本 ----------
@_safe
def _on_story_ready(app, text):
    app.director_story_edit.setPlainText(text)
    _set_status(app, "剧本已生成。可编辑后「采用剧本」，或「重写」并附意见。")
    _log(app, "✅ 剧本生成完成。进入编辑/确认。")
    _set_busy(app, False)
    _save_session(app)


@_safe
def _revise_story(app):
    text, ok = QInputDialog.getText(
        app, "重写剧本", "输入修改意见（可留空直接重试）：",
        text="")
    if not ok:
        return
    _set_status(app, "正在按意见重写剧本…")
    _log(app, "✎ 重写剧本…")
    _run_thread(app, "story", feedback=text.strip() or None)


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
def _on_characters_ready(app, characters):
    _build_character_cards(app, characters)
    n = len(characters)
    _set_status(app, f"人物三视图已生成（{n} 个角色）。可查看/重生成，满意后「采用人物」。")
    _log(app, f"✅ 人物三视图完成（{n} 个角色）。")
    _set_busy(app, False)
    _save_session(app)


@_safe
def _revise_characters(app):
    text, ok = QInputDialog.getText(
        app, "重新生成人物", "输入修改意见（可留空直接重试）：", text="")
    if not ok:
        return
    _set_status(app, "正在按意见重新生成人物三视图…")
    _log(app, "✎ 重新生成人物三视图…")
    _run_thread(app, "characters", feedback=text.strip() or None)


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
    _set_status(app, f"分镜就绪（{len(shots)} 镜）。可逐镜修改/增删，满意后「采用分镜」。")
    _log(app, f"✅ 分镜生成完成（{len(shots)} 镜）。进入编辑/确认。")
    _set_busy(app, False)
    _save_session(app)


def _build_shot_rows(app, shots):
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
        no.setStyleSheet(f"color:{THEME['text']};font-size:13px;font-weight:600;")
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
        delb.setFixedSize(28, 28)
        delb.setCursor(Qt.PointingHandCursor)
        delb.setStyleSheet(_btn_danger_style())
        delb.clicked.connect(lambda _, r=row: _del_shot_row(app, r))
        rl.addWidget(delb)
        app.director_shots_layout.addWidget(row)
        app.director_shot_rows.append(
            {"row": row, "scene": sc, "zh": zh, "en": en, "cam": cam, "line": line})
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
    s = {"scene": 1, "zh": "", "en": "", "cam": "", "line": ""}
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
            "line": (r["line"].text().strip() if r["line"] else ""),
        })
    return out


@_safe
def _revise_shots(app):
    text, ok = QInputDialog.getText(
        app, "重排分镜", "输入修改意见（可留空直接重试）：", text="")
    if not ok:
        return
    _set_status(app, "正在按意见重排分镜…")
    _log(app, "✎ 重排分镜…")
    _run_thread(app, "shots", feedback=text.strip() or None)


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
        _set_status(app, "正在逐镜生成视频…")
        _log(app, f"✓ 采用分镜（{len(shots)} 镜）→ 逐镜生成（口播模式跳过关键帧）…")
        _set_step(app, 5)
        _prepare_clip_cards(app, len(shots))
        _run_thread(app, "clips")
    else:
        _set_status(app, "正在生成分镜关键帧 + 场景图…")
        _log(app, f"✓ 采用分镜（{len(shots)} 镜）→ 生成关键帧…")
        _set_step(app, 4)
        _run_thread(app, "keyframes")


@_safe
def _on_keyframes_ready(app, keyframes):
    _build_keyframe_cards(app, keyframes)
    ok_kf = sum(1 for x in keyframes if x)
    _set_status(app, f"关键帧+场景图已生成（{ok_kf}/{len(keyframes)} 镜有效）。可查看/重生成，满意后「采用关键帧」。")
    _log(app, f"✅ 关键帧+场景图完成（{ok_kf}/{len(keyframes)} 镜）。")
    _set_busy(app, False)
    _save_session(app)


@_safe
def _revise_keyframes(app):
    text, ok = QInputDialog.getText(
        app, "重新生成关键帧", "输入修改意见（可留空直接重试）：", text="")
    if not ok:
        return
    _set_status(app, "正在按意见重新生成关键帧+场景图…")
    _log(app, "✎ 重新生成关键帧+场景图…")
    _run_thread(app, "keyframes", feedback=text.strip() or None)


@_safe
def _adopt_keyframes(app):
    _set_status(app, "正在逐镜生成视频…")
    _log(app, "✓ 采用关键帧 → 逐镜生成…")
    _set_step(app, 5)
    _save_session(app)
    _prepare_clip_cards(app, len(app.director_pipeline.shots))
    _run_thread(app, "clips")


# ---------- ③ 逐镜生成 ----------
def _prepare_clip_cards(app, n):
    _clear_layout(app.director_clips_grid)
    app.director_clip_cards = []
    cols = 3
    for i in range(n):
        card = QFrame()
        card.setStyleSheet(f"QFrame{{background:{THEME['card']};border:1px solid {THEME['border']};"
                           f"border-radius:10px;}}")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.setSpacing(6)
        frame = QLabel("生成中…")
        frame.setFixedSize(150, 84)
        frame.setAlignment(Qt.AlignCenter)
        frame.setStyleSheet(f"QLabel{{background:{THEME['bg']};border-radius:6px;color:{THEME['dim']};font-size:11px;}}")
        cl.addWidget(frame)
        info = QLabel(f"镜{i+1} · ⏳ 排队中")
        info.setStyleSheet(f"color:{THEME['text']};font-size:12px;")
        cl.addWidget(info)
        bl = QHBoxLayout()
        bl.setSpacing(4)
        play = QPushButton("▶")
        play.setFixedSize(26, 28)
        play.setStyleSheet(_btn_small_style())
        play.setEnabled(False)
        play.clicked.connect(lambda _, p=None, idx=i: _play_clip(app, idx))
        mod = QPushButton("✎改")
        mod.setFixedSize(34, 28)
        mod.setStyleSheet(_btn_small_style())
        mod.clicked.connect(lambda _, idx=i: _modify_clip(app, idx))
        regen = QPushButton("↻")
        regen.setFixedSize(26, 28)
        regen.setStyleSheet(_btn_small_style())
        regen.clicked.connect(lambda _, idx=i: _regenerate_clip(app, idx))
        view = QPushButton("🔍")
        view.setFixedSize(26, 28)
        view.setStyleSheet(_btn_small_style())
        view.clicked.connect(lambda _, idx=i: _view_prompt(app, idx))
        bl.addWidget(play)
        bl.addWidget(mod)
        bl.addWidget(regen)
        bl.addWidget(view)
        cl.addLayout(bl)
        app.director_clips_grid.addWidget(card, i // cols, i % cols)
        app.director_clip_cards.append(
            {"frame": frame, "info": info, "play": play, "mod": mod,
             "regen": regen, "view": view, "path": None, "error": ""})


def _build_character_cards(app, characters):
    """把每个角色的三视图（正/侧/背）渲染成卡片，供用户判定是否会崩。"""
    _clear_layout(app.director_characters_layout)
    app.director_character_cards = []
    for c in (characters or []):
        box = QFrame()
        box.setStyleSheet(f"QFrame{{background:{THEME['card']};border:1px solid {THEME['border']};"
                          f"border-radius:10px;}}")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 10, 10, 10)
        bl.setSpacing(6)
        name = QLabel(c.get("name", "角色"))
        name.setStyleSheet(f"font-size:14px;font-weight:600;color:{THEME['text']};")
        bl.addWidget(name)
        desc = QLabel(c.get("desc", ""))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size:11px;color:{THEME['dim']};")
        bl.addWidget(desc)
        hl = QHBoxLayout()
        hl.setSpacing(6)
        for v in (c.get("views") or []):
            lbl = QLabel("无")
            lbl.setFixedSize(96, 140)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(f"QLabel{{background:{THEME['bg']};border-radius:6px;"
                                f"color:{THEME['dim']};font-size:10px;}}")
            if v and os.path.isfile(v):
                pm = QPixmap(v).scaled(96, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                lbl.setPixmap(pm)
                lbl.setText("")
            hl.addWidget(lbl)
        bl.addLayout(hl)
        app.director_characters_layout.addWidget(box)
    app.director_characters_layout.addStretch(1)


def _build_keyframe_cards(app, keyframes):
    """把每镜关键帧+场景图渲染成缩略图卡片。"""
    _clear_layout(app.director_keyframes_grid)
    app.director_keyframe_cards = []
    cols = 3
    for i, kf in enumerate(keyframes or []):
        card = QFrame()
        card.setStyleSheet(f"QFrame{{background:{THEME['card']};border:1px solid {THEME['border']};"
                          f"border-radius:10px;}}")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.setSpacing(6)
        frame = QLabel("无")
        frame.setFixedSize(150, 84)
        frame.setAlignment(Qt.AlignCenter)
        frame.setStyleSheet(f"QLabel{{background:{THEME['bg']};border-radius:6px;"
                                f"color:{THEME['dim']};font-size:11px;}}")
        if kf and os.path.isfile(kf):
            pm = QPixmap(kf).scaled(150, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            frame.setPixmap(pm)
            frame.setText("")
        cl.addWidget(frame)
        info = QLabel(f"镜{i+1} · 关键帧" if kf else f"镜{i+1} · 生成失败")
        info.setStyleSheet(f"color:{THEME['text']};font-size:12px;")
        cl.addWidget(info)
        # VLM 质检结果：让「抗崩坏」看得见——崩没崩一眼看到，而不只是日志里一行
        _p = getattr(app, "director_pipeline", None)
        _note = (getattr(_p, "review_notes", {}) or {}).get(i) or ""
        if _note:
            _failed = "VERDICT: FAIL" in _note.upper()
            qc = QLabel("⚠️ 质检未通过" if _failed else "✅ 质检通过")
            qc.setStyleSheet(
                f"color:{'#d98c3f' if _failed else THEME['dim']};font-size:11px;")
            qc.setToolTip(_note[:400])
            cl.addWidget(qc)
        app.director_keyframes_grid.addWidget(card, i // cols, i % cols)
        app.director_keyframe_cards.append({"frame": frame, "info": info, "path": kf})


@_safe
def _on_clip_ready(app, i, path):
    cards = app.director_clip_cards
    if i < 0 or i >= len(cards):
        return
    c = cards[i]
    c["path"] = path
    kf = app.director_pipeline.keyframe(path)
    if kf and os.path.isfile(kf):
        pm = QPixmap(kf).scaled(150, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        c["frame"].setPixmap(pm)
        c["frame"].setText("")
    else:
        c["frame"].setText("无预览")
    c["info"].setText(f"镜{i+1} · ✅ 完成")
    c["play"].setEnabled(True)
    _save_session(app)


@_safe
def _on_clip_failed(app, i, reason=""):
    cards = app.director_clip_cards
    if i < 0 or i >= len(cards):
        return
    c = cards[i]
    c["error"] = reason or ""
    c["info"].setText(f"镜{i+1} · ❌ 失败（🔍看提示词）")
    # 完整失败原因挂到 tooltip，鼠标悬停即可查看，不挤占卡片空间
    c["info"].setToolTip(f"失败原因：{reason}\n\n点 🔍 查看本镜实际发给模型的提示词。")
    _save_session(app)


@_safe
def _on_clips_done(app, ok, total, msg):
    _set_status(app, f"逐镜生成完成：{ok}/{total} 成功。可单镜「✎改/↻」，或「去合成」。")
    _log(app, f"✅ {msg}：{ok}/{total} 成功。")
    _set_busy(app, False)
    _save_session(app)


def _play_clip(app, idx):
    if 0 <= idx < len(app.director_clip_cards):
        _play_video(app, app.director_clip_cards[idx]["path"])


def _play_video(app, path):
    if path and os.path.isfile(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


@_safe
def _view_prompt(app, idx):
    """弹窗显示某镜实际发给模型的提示词 +（若有）失败原因，让用户看得懂、改得准。"""
    if app.director_pipeline is None:
        return
    prompt = getattr(app.director_pipeline, "last_prompts", {}).get(idx, "")
    err = ""
    if 0 <= idx < len(app.director_clip_cards):
        err = app.director_clip_cards[idx].get("error", "")
    dlg = QDialog(app)
    dlg.setWindowTitle(f"镜{idx+1} · 实际发给模型的提示词")
    dlg.setMinimumWidth(540)
    dlg.setStyleSheet(f"QDialog{{background:{THEME['bg']};}}")
    v = QVBoxLayout(dlg)
    v.setSpacing(10)
    v.setContentsMargins(16, 16, 16, 16)
    if err:
        el = QLabel(f"⚠️ 上次失败原因：\n{err}")
        el.setWordWrap(True)
        el.setStyleSheet(f"color:#ef4444;font-size:12px;background:{THEME['card']};"
                         f"border:1px solid #ef4444;border-radius:6px;padding:8px 10px;")
        v.addWidget(el)
    tl = QLabel("本次发给模型（Agnes）的实际提示词：")
    tl.setStyleSheet(f"color:{THEME['text']};font-size:12px;font-weight:600;")
    v.addWidget(tl)
    te = QTextEdit()
    te.setReadOnly(True)
    te.setPlainText(prompt or "（暂无，这镜可能还没生成 / 或被重置）")
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
    if app.director_pipeline is None:
        _set_status(app, "流程未初始化，请先点「开始导演」。", err=True)
        return
    prompt = getattr(app.director_pipeline, "last_prompts", {}).get(idx, "")
    err = ""
    if 0 <= idx < len(app.director_clip_cards):
        err = app.director_clip_cards[idx].get("error", "")
    dlg = QDialog(app)
    dlg.setWindowTitle(f"修改 镜{idx+1}")
    dlg.setMinimumWidth(560)
    dlg.setStyleSheet(f"QDialog{{background:{THEME['bg']};}}")
    v = QVBoxLayout(dlg)
    v.setSpacing(10)
    v.setContentsMargins(16, 16, 16, 16)
    if err:
        el = QLabel(f"⚠️ 上次失败原因：\n{err}")
        el.setWordWrap(True)
        el.setStyleSheet(f"color:#ef4444;font-size:12px;background:{THEME['card']};"
                         f"border:1px solid #ef4444;border-radius:6px;padding:8px 10px;")
        v.addWidget(el)
    tl = QLabel("本次已发给模型的提示词（可照抄其中想保留的设定）：")
    tl.setStyleSheet(f"color:{THEME['text']};font-size:12px;font-weight:600;")
    v.addWidget(tl)
    prev = QTextEdit()
    prev.setReadOnly(True)
    prev.setPlainText(prompt or "（暂无，可能这镜还没生成过）")
    prev.setMaximumHeight(130)
    prev.setStyleSheet(f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
                       f"border-radius:6px;padding:6px 8px;font-size:11px;color:{THEME['dim']};}}")
    v.addWidget(prev)
    il = QLabel("你的修改意见（告诉它这一镜怎么改；留空=直接重生成）：")
    il.setStyleSheet(f"color:{THEME['text']};font-size:12px;font-weight:600;")
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
    replace_chk.setStyleSheet(f"color:{THEME['text']};font-size:12px;")
    replace_chk.setToolTip("勾选后，上面填的内容会直接替换本镜英文提示词，而不是追加。\n"
                           "适用：Agnes 报 content_policy_violation / 400 内容违规时，"
                           "原提示词含触发词必须整段换掉。")
    v.addWidget(replace_chk)
    btns = QHBoxLayout()
    btns.setSpacing(10)
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
    cards = getattr(app, "director_clip_cards", None)
    if not cards:
        _set_status(app, "还没有可重新生成的片段。", err=True)
        return
    if app.director_pipeline is None:
        _set_status(app, "流程未初始化，请先点「开始导演」。", err=True)
        return
    _set_status(app, "正在全部重新生成…")
    _log(app, "↺ 全部重新生成…")
    for c in cards:
        info = c.get("info")
        if info is not None:
            info.setText(info.text().split("·")[0] + "· ⏳ 重生成中")
        play = c.get("play")
        if play is not None:
            play.setEnabled(False)
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
    _set_busy(app, False)
    app.director_merge_btn.setEnabled(True)
    if ok and path and os.path.isfile(path):
        app.director_final_path = path
        kf = app.director_pipeline.keyframe(path)
        if kf and os.path.isfile(kf):
            pm = QPixmap(kf).scaled(356, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            app.director_merge_preview.setPixmap(pm)
            app.director_merge_preview.setText("")
        else:
            app.director_merge_preview.setText("成片已生成（无预览）")
        app.director_merge_play.setEnabled(True)
        _set_status(app, msg)
        _log(app, f"✅ {msg}")
        # 同步进交付物面板
        name = os.path.basename(path)
        app.director_paths.append(path)
        try:
            rel = os.path.relpath(path, APP_DIR).replace("\\", "/")
            app.store.active().deliverables.append(
                {"rel": rel, "kind": "video", "name": name, "desc": rel})
            app.store.save()
            app._refresh_deliverables()
        except Exception:
            pass
        # 成片已完成，任务结束，清掉续跑存盘（下次打开不会再提示）
        _clear_session(app)
    else:
        _set_status(app, "合成失败：" + msg, err=True)
        _log(app, "❌ 合成失败。")
        _save_session(app)


# ---------- 停止 / 重置 / 错误 ----------
def _director_stop(app):
    th = getattr(app, "director_thread", None)
    if th and app.director_pipeline:
        app.director_pipeline.cancelled = True
    _set_status(app, "正在取消…")


def _director_reset(app):
    th = getattr(app, "director_thread", None)
    if th and th.isRunning():
        if app.director_pipeline:
            app.director_pipeline.cancelled = True
        th.wait(2000)
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
    _clear_layout(app.director_clips_grid)
    app.director_clip_cards = []
    app.director_shot_rows = []
    app.director_merge_preview.setText("尚未合成")
    app.director_merge_preview.setPixmap(QPixmap())
    app.director_log.clear()
    _set_step(app, 0)
    _set_busy(app, False)
    _set_status(app, "")
    _log(app, "已重置。重新填写主题和参数即可开始。")


@_safe
def _on_error(app, e):
    _set_busy(app, False)
    _set_status(app, f"出错：{e}", err=True)
    _log(app, f"❌ {e}")


# ---------- 任务持久化（关程序后继续） ----------
def _session_path():
    return os.path.join(APP_DIR, "director_session.json")


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
                "keyframes": getattr(p, "keyframes", []),
                # scene_images 的 key 是场景号(int)，JSON 只认字符串键，存时转 str、读时转回 int
                "scene_images": {str(k): v for k, v in getattr(p, "scene_images", {}).items()},
                "review_notes": {str(k): v for k, v in getattr(p, "review_notes", {}).items()},
                "clip_paths": getattr(p, "clip_paths", []),
                "last_prompts": {str(k): v for k, v in getattr(p, "last_prompts", {}).items()},
                "last_errors": {str(k): v for k, v in getattr(p, "last_errors", {}).items()},
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
                "story", "shots", "characters", "character_lock", "keyframes"):
        if key in pl:
            setattr(p, key, pl[key])
    p.clip_paths = pl.get("clip_paths", [None] * len(pl.get("shots", [])))
    p.last_prompts = {int(k): v for k, v in pl.get("last_prompts", {}).items()}
    p.last_errors = {int(k): v for k, v in pl.get("last_errors", {}).items()}
    # 场景图/质检记录的键是场景号与镜号（int），JSON 只认字符串键，这里转回 int
    p.scene_images = {int(k): v for k, v in (pl.get("scene_images") or {}).items()
                      if str(k).lstrip("-").isdigit()}
    p.review_notes = {int(k): v for k, v in (pl.get("review_notes") or {}).items()
                      if str(k).lstrip("-").isdigit()}
    # 工程目录可能已被清理，确保存在（关键帧预览/重生成要用）
    if p.project_dir:
        try:
            os.makedirs(p.project_dir, exist_ok=True)
        except Exception:
            pass
    app.director_pipeline = p

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
    # 恢复 pipeline 上的质检开关（会话续跑时保持一致）
    p.vision_review = app.director_vision_review.isChecked()
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
    if step >= 3:
        _build_shot_rows(app, p.shots or [])
    if step >= 4 and p.keyframes:
        _build_keyframe_cards(app, p.keyframes)
    if step >= 5:
        _prepare_clip_cards(app, len(p.shots or []))
        for i, c in enumerate(app.director_clip_cards):
            path = p.clip_paths[i] if i < len(p.clip_paths) else None
            err = p.last_errors.get(i, "")
            if path and os.path.isfile(path):
                c["path"] = path
                kf = p.keyframe(path)
                if kf and os.path.isfile(kf):
                    c["frame"].setPixmap(
                        QPixmap(kf).scaled(150, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    c["frame"].setText("")
                else:
                    c["frame"].setText("无预览")
                c["info"].setText(f"镜{i+1} · ✅ 完成")
                c["play"].setEnabled(True)
            elif err:
                c["error"] = err
                c["info"].setText(f"镜{i+1} · ❌ 失败（🔍看提示词）")
                c["info"].setToolTip(f"失败原因：{err}\n\n点 🔍 查看本镜实际发给模型的提示词。")
            else:
                c["info"].setText(f"镜{i+1} · ⏳ 未生成")
    if step >= 6:
        fp = data.get("final_path")
        if fp and os.path.isfile(fp):
            app.director_final_path = fp
            kf = p.keyframe(fp)
            if kf and os.path.isfile(kf):
                app.director_merge_preview.setPixmap(
                    QPixmap(kf).scaled(356, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                app.director_merge_preview.setText("")
            else:
                app.director_merge_preview.setText("成片已生成（无预览）")
            app.director_merge_play.setEnabled(True)

    _set_step(app, step)
    _set_status(app, f"已从上次进度恢复（进行到：{STEP_LABELS[min(step, len(STEP_LABELS)-1)]}）。可继续编辑或直接操作。")
    _log(app, f"💾 已从上次进度恢复，当前步骤：{STEP_LABELS[min(step, len(STEP_LABELS)-1)]}。")
    return True


def _maybe_offer_resume(app):
    """面板构建后调用：若有未完成任务，顶部显示「继续 / 放弃」横幅。"""
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
    label.setStyleSheet(f"color:{THEME['text']};font-size:13px;")
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
