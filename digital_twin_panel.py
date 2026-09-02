"""数字人分身面板（Agent内嵌工作台）。

承接桌面端「数字人分身 / 我自己」Agent 的核心能力，做成Agent里的一个真嵌入面板：
  - 本人形象（参考图）库：添加 / 选择 / 预览，落盘到 APP_DIR/avatars/
  - 衣橱 / 场景描述（轻量版）：文本描述着装与场景，注入视频 prompt
  - 口播视频生成：复用 tools.tool_video_gen（Agnes 直连）
        · first_frame = 本人参考图（首帧锁定人脸）
        · dialogue    = 中文口播台词（Agnes 合成中文语音 + 对口型）
        · 额外加一段英文 face-locking 指令，抑制后半段人脸漂移

MVP 范围：不含实时 ASR / LLM 对话循环 / TTS 实时驱动（桌面端分身仍由独立 app 承担）。

build_twin_panel(app): 在 app.twin_page 上构建 UI。app 为 MainWindow 实例，
直接复用其 _file_to_datauri / store / _refresh_deliverables 等能力。
"""
import os
import shutil
import subprocess
import time

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QLineEdit,
    QSpinBox, QComboBox, QListWidget, QListWidgetItem, QFileDialog, QSizePolicy,
    QGroupBox, QFrame, QMenu, QCheckBox,
)
from PySide6.QtGui import QPixmap, QIcon, QAction
from PySide6.QtCore import Qt, QSize, QThread, Signal

from ui import THEME, _GenThread
from config import APP_DIR
import tools as tools_mod
import vision_qc as vq


# 分辨率预设（与「生视频」页保持一致）
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

AVATAR_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# 低于此体积视为程序内置图标（如 26x26 的 avatar_user.png），不是本人照片，
# 迁移旧照片时跳过，免得把占位图标当成形象搬进用户目录。
MIN_PHOTO_BYTES = 10 * 1024


def _avatar_dir():
    """本人形象目录（用户数据，必须落在 Documents 下，不能落程序目录）。

    ⚠️ 2026-08-30 修正：原先是 APP_DIR/avatars，而 APP_DIR 在冻结环境就是
    dist/AgentDesktop/ —— 重打包时整个 dist 会被搬走重建，用户照片（还是本人
    隐私数据）会直接丢失。现统一迁到 USER_DATA_DIR 下，并一次性迁移旧照片。
    """
    d = os.path.join(_user_data_dir(), "avatars")
    os.makedirs(d, exist_ok=True)
    _migrate_legacy_avatars(d)
    return d


def _user_data_dir():
    """用户数据根目录（抽出来是为了可注入、可单测）。"""
    try:
        from config import USER_DATA_DIR
        return USER_DATA_DIR
    except Exception:
        return APP_DIR


def _migrate_legacy_avatars(new_dir):
    """把旧位置（程序目录/avatars）里的照片一次性搬到用户目录。

    用 copy 而非 move：搬失败不丢原图，用户可自行处理。
    """
    legacy = os.path.join(APP_DIR, "avatars")
    try:
        if os.path.abspath(legacy) == os.path.abspath(new_dir):
            return
        if not os.path.isdir(legacy):
            return
        # 目标已有照片就说明搬过了，不重复搬
        if any(f.lower().endswith(AVATAR_EXTS) for f in os.listdir(new_dir)):
            return
        moved = 0
        for fn in os.listdir(legacy):
            if not fn.lower().endswith(AVATAR_EXTS):
                continue
            src = os.path.join(legacy, fn)
            if not os.path.isfile(src):
                continue
            # 跳过程序内置的小图标（如 26x26 的 avatar_user.png），只搬真实照片
            try:
                if os.path.getsize(src) < MIN_PHOTO_BYTES:
                    continue
            except OSError:
                continue
            dst = os.path.join(new_dir, fn)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                moved += 1
        if moved:
            print(f"[数字人] 已迁移 {moved} 张本人照片到用户目录：{new_dir}")
    except Exception:
        pass


def build_twin_panel(app):
    page = app.twin_page
    lay = QVBoxLayout(page)
    lay.setContentsMargins(32, 24, 32, 24)
    lay.setSpacing(16)

    head = QLabel("数字人分身 · 我自己")
    head.setStyleSheet(f"font-size:20px;font-weight:700;color:{THEME['text']};")
    lay.addWidget(head)
    sub = QLabel("本人形象 + 口播台词 → 数字人口播视频（Agnes 直连，免费）。"
                 "长台词自动分段生成并拼接，段间用上一片段末帧接力（脸不跳变）；"
                 "成片自动烧录「AI 生成」标识。")
    sub.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    lay.addWidget(sub)

    # ---------------- 本人形象库 ----------------
    avatar_box = QGroupBox("本人形象（参考图）")
    avatar_box.setStyleSheet(
        f"QGroupBox{{background:{THEME['card']};border:1px solid {THEME['border']};"
        f"border-radius:10px;padding:12px 14px;font-size:13px;color:{THEME['text']};"
        f"margin-top:8px;}} QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 6px;}}")
    ab_lay = QHBoxLayout(avatar_box)
    ab_lay.setSpacing(14)

    # 左：缩略图列表
    app.twin_portrait_list = QListWidget()
    app.twin_portrait_list.setViewMode(QListWidget.IconMode)
    app.twin_portrait_list.setIconSize(QSize(84, 84))
    app.twin_portrait_list.setMovement(QListWidget.Static)
    app.twin_portrait_list.setResizeMode(QListWidget.Adjust)
    app.twin_portrait_list.setSpacing(8)
    app.twin_portrait_list.setFixedHeight(110)
    app.twin_portrait_list.setStyleSheet(
        f"QListWidget{{background:{THEME['bg']};border:1px solid {THEME['border']};"
        f"border-radius:8px;padding:6px;}}"
        f"QListWidget::item{{border-radius:6px;padding:2px;}}"
        f"QListWidget::item:selected{{outline:2px solid {THEME['accent']};"
        f"background:{THEME['blue_hover']};}}")
    app.twin_portrait_list.itemClicked.connect(lambda it: _on_twin_portrait_picked(app, it))
    # 右键菜单：删除照片
    app.twin_portrait_list.setContextMenuPolicy(Qt.CustomContextMenu)
    app.twin_portrait_list.customContextMenuRequested.connect(
        lambda pos: _twin_context_menu(app, pos))
    ab_lay.addWidget(app.twin_portrait_list, 1)

    # 右：预览 + 操作
    right_col = QVBoxLayout()
    right_col.setSpacing(8)
    app.twin_preview = QLabel("未选择")
    app.twin_preview.setFixedSize(120, 120)
    app.twin_preview.setAlignment(Qt.AlignCenter)
    app.twin_preview.setStyleSheet(
        f"QLabel{{background:{THEME['bg']};border:1px solid {THEME['border']};"
        f"border-radius:8px;color:{THEME['dim']};font-size:12px;}}")
    right_col.addWidget(app.twin_preview)
    btn_row = QHBoxLayout()
    add_btn = QPushButton("＋ 添加本人照片")
    add_btn.setFixedHeight(32)
    add_btn.setCursor(Qt.PointingHandCursor)
    add_btn.setStyleSheet(_btn_style())
    add_btn.clicked.connect(lambda: _twin_add_portrait(app))
    btn_row.addWidget(add_btn)
    del_btn = QPushButton("🗑 删除选中")
    del_btn.setFixedHeight(32)
    del_btn.setCursor(Qt.PointingHandCursor)
    del_btn.setStyleSheet(
        f"QPushButton{{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5;"
        f"border-radius:8px;padding:0 14px;font-size:13px;}}"
        f"QPushButton:hover{{background:#fecaca;}}")
    del_btn.clicked.connect(lambda: _twin_delete_portrait(app))
    btn_row.addWidget(del_btn)
    right_col.addLayout(btn_row)
    ab_lay.addLayout(right_col)
    lay.addWidget(avatar_box)

    app.twin_selected_portrait = None
    _refresh_twin_portraits(app)

    # ---------------- 衣橱 / 场景 + 口播台词 ----------------
    mid = QHBoxLayout()
    mid.setSpacing(16)

    # 左：衣橱/场景描述
    ward = QVBoxLayout()
    ward.setSpacing(6)
    wlab = QLabel("衣橱 / 场景描述（可选）")
    wlab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
    ward.addWidget(wlab)
    app.twin_scene = QTextEdit()
    app.twin_scene.setFixedHeight(96)
    app.twin_scene.setPlaceholderText(
        "例如：穿白色衬衫，面对镜头微笑（勾选下方「保持原图背景」时，这里只写服装/动作，背景不会被改）")
    app.twin_scene.setStyleSheet(_edit_style())
    ward.addWidget(app.twin_scene)
    mid.addLayout(ward, 1)

    # 右：口播台词
    dia = QVBoxLayout()
    dia.setSpacing(6)
    dlab = QLabel("口播台词（中文，必填）")
    dlab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
    dia.addWidget(dlab)
    app.twin_dialogue = QTextEdit()
    app.twin_dialogue.setFixedHeight(96)
    app.twin_dialogue.setPlaceholderText("例如：大家好，我是Agent。今天跟大家聊聊……")
    app.twin_dialogue.setStyleSheet(_edit_style())
    dia.addWidget(app.twin_dialogue)
    mid.addLayout(dia, 1)

    lay.addLayout(mid)

    # ---------------- 选项行 ----------------
    opt = QHBoxLayout()
    opt.setSpacing(12)

    dur_lab = QLabel("时长")
    dur_lab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
    opt.addWidget(dur_lab)
    app.twin_duration = QSpinBox()
    # 新版 agnes-video-2.5-flash 时长合法范围 4~12 秒（旧版 3~16s）
    app.twin_duration.setRange(4, 12)
    app.twin_duration.setValue(8)
    app.twin_duration.setSuffix(" 秒")
    app.twin_duration.setFixedHeight(34)
    app.twin_duration.setStyleSheet(_combo_style())
    opt.addWidget(app.twin_duration)

    res_lab = QLabel("分辨率")
    res_lab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
    opt.addWidget(res_lab)
    app.twin_resolution = QComboBox()
    for label, val in RES_PRESETS:
        app.twin_resolution.addItem(label, val)
    app.twin_resolution.setCurrentIndex(2)  # 默认竖屏 768×1152
    app.twin_resolution.setFixedHeight(34)
    app.twin_resolution.setStyleSheet(_combo_style())
    opt.addWidget(app.twin_resolution, 1)

    # 保持原图背景：默认开。关掉才允许模型按「场景描述」另造背景。
    app.twin_keep_bg = QCheckBox("保持原图背景")
    app.twin_keep_bg.setChecked(True)
    app.twin_keep_bg.setToolTip(
        "勾选后：背景 / 房间 / 陈设 / 光线一律沿用参考图，只有人在说话。\n"
        "不勾选：模型会按「衣橱 / 场景描述」重新生成背景。")
    app.twin_keep_bg.setStyleSheet(
        f"QCheckBox{{color:{THEME['text']};font-size:13px;spacing:6px;}}"
        f"QCheckBox::indicator{{width:16px;height:16px;border-radius:4px;"
        f"border:1px solid {THEME['border']};background:{THEME['card']};}}"
        f"QCheckBox::indicator:checked{{background:{THEME['accent']};"
        f"border:1px solid {THEME['accent']};}}")
    opt.addWidget(app.twin_keep_bg)

    gen_btn = QPushButton("🎬 生成分身口播视频")
    gen_btn.setFixedHeight(36)
    gen_btn.setCursor(Qt.PointingHandCursor)
    gen_btn.setStyleSheet(_btn_accent_style())
    gen_btn.clicked.connect(lambda: _twin_generate(app))
    opt.addWidget(gen_btn)
    lay.addLayout(opt)

    # ---------------- 增强选项行 ----------------
    opt2 = QHBoxLayout()
    opt2.setSpacing(14)

    app.twin_ai_mark = QCheckBox("🏷 烧录 AI 标识")
    app.twin_ai_mark.setChecked(True)
    app.twin_ai_mark.setToolTip(
        "在成片右下角烧常驻「AI 生成」角标。\n"
        "数字人内容不标注 AI 标识属违规，**建议始终保持开启**。")

    app.twin_qc = QCheckBox("🔍 VLM 质检")
    app.twin_qc.setChecked(True)
    app.twin_qc.setToolTip(
        "每段生成后用 DeepSeek 视觉模型审查：是否仍是本人、有无脸崩或肢体畸形，\n"
        "不通过自动重生成（每段最多重试 2 次）。\n"
        "⚠️ 调用用户已付费订阅的 DeepSeek，会产生少量费用；关掉则只靠参考图锁定。")

    app.twin_dual_frame = QCheckBox("🔒 首尾双帧锁定")
    app.twin_dual_frame.setChecked(True)
    app.twin_dual_frame.setToolTip(
        "首尾帧都用本人参考图，双端锁定身份，压住中段漂移。\n"
        "关闭则只用首帧（模型自由度更高，但长片段漂移风险上升）。")

    for c in (app.twin_ai_mark, app.twin_qc, app.twin_dual_frame):
        c.setStyleSheet(_chk_style())
        opt2.addWidget(c)
    opt2.addStretch(1)
    lay.addLayout(opt2)

    app.twin_status = QLabel("")
    app.twin_status.setStyleSheet(f"color:{THEME['dim']};font-size:12px;")
    lay.addWidget(app.twin_status)

    # ---------------- 结果列表 ----------------
    app.twin_paths = []
    app.twin_result = QListWidget()
    app.twin_result.setStyleSheet(
        f"QListWidget{{background:{THEME['card']};border:1px solid {THEME['border']};"
        f"border-radius:10px;padding:8px;font-size:13px;color:{THEME['text']};}}")
    app.twin_result.itemDoubleClicked.connect(lambda it: _twin_open_result(app, it))
    lay.addWidget(app.twin_result, 1)

    # 若尚未添加任何本人照片，给个引导
    if not app.twin_selected_portrait:
        app.twin_status.setText("提示：请先点「＋ 添加本人照片」选一张正面清晰照作为分身参考图。")


# ---------------- 样式小工具 ----------------
def _btn_style():
    return (f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
            f"border:1px solid {THEME['border']};border-radius:8px;padding:0 14px;font-size:13px;}}"
            f"QPushButton:hover{{background:{THEME['blue_hover']};}}")


def _btn_accent_style():
    return (f"QPushButton{{background:{THEME['accent']};color:#FFFFFF;border:none;"
            f"border-radius:8px;padding:0 18px;font-size:14px;font-weight:500;}}"
            f"QPushButton:hover{{background:{THEME['accent_hover']};}}")


def _edit_style():
    return (f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:10px;padding:10px 12px;font-size:13px;color:{THEME['text']};}}"
            f"QTextEdit:focus{{border:1px solid {THEME['accent']};}}")


def _combo_style():
    return (f"QComboBox{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:8px;padding:0 10px;font-size:13px;color:{THEME['text']};}}"
            f"QComboBox::drop-down{{border:none;}}")


def _chk_style():
    return (f"QCheckBox{{color:{THEME['text']};font-size:13px;spacing:6px;}}"
            f"QCheckBox::indicator{{width:16px;height:16px;border-radius:4px;"
            f"border:1px solid {THEME['border']};background:{THEME['card']};}}"
            f"QCheckBox::indicator:checked{{background:{THEME['accent']};"
            f"border:1px solid {THEME['accent']};}}")


# ---------------- 事件处理 ----------------
def _refresh_twin_portraits(app):
    lw = app.twin_portrait_list
    lw.clear()
    d = _avatar_dir()
    files = sorted(f for f in os.listdir(d)
                   if f.lower().endswith(AVATAR_EXTS))
    if not files:
        return
    for fn in files:
        path = os.path.join(d, fn)
        pm = QPixmap(path)
        if pm.isNull():
            continue
        pm = pm.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        item = QListWidgetItem(QIcon(pm), "")
        item.setData(Qt.UserRole, path)
        item.setToolTip(fn)
        lw.addItem(item)
    # 默认选中第一张
    lw.setCurrentRow(0)
    _on_twin_portrait_picked(app, lw.item(0))


def _on_twin_portrait_picked(app, item):
    if not item:
        return
    path = item.data(Qt.UserRole)
    app.twin_selected_portrait = path
    pm = QPixmap(path)
    if not pm.isNull():
        pm = pm.scaled(116, 116, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        app.twin_preview.setPixmap(pm)
        app.twin_preview.setText("")


def _twin_add_portrait(app):
    path, _ = QFileDialog.getOpenFileName(
        app, "选择本人照片", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
    if not path:
        return
    d = _avatar_dir()
    base = os.path.basename(path)
    dest = os.path.join(d, base)
    # 避免重名覆盖
    if os.path.exists(dest):
        stem, ext = os.path.splitext(base)
        i = 1
        while os.path.exists(os.path.join(d, f"{stem}_{i}{ext}")):
            i += 1
        dest = os.path.join(d, f"{stem}_{i}{ext}")
    try:
        shutil.copyfile(path, dest)
    except Exception as e:
        app.twin_status.setText(f"复制失败：{e}")
        return
    _refresh_twin_portraits(app)
    app.twin_status.setText(f"已添加本人照片：{os.path.basename(dest)}")


def _twin_delete_portrait(app):
    """删除选中的本人照片（磁盘+列表刷新）。"""
    lw = app.twin_portrait_list
    item = lw.currentItem()
    if not item:
        app.twin_status.setText("请先在左侧缩略图列表中选中要删除的照片。")
        return
    path = item.data(Qt.UserRole)
    if not path or not os.path.isfile(path):
        app.twin_status.setText("照片文件不存在，可能已被手动删除。")
        _refresh_twin_portraits(app)
        return
    fn = os.path.basename(path)
    try:
        os.remove(path)
        app.twin_status.setText(f"已删除照片：{fn}")
    except Exception as e:
        app.twin_status.setText(f"删除失败：{e}")
        return
    # 清空选中状态和预览
    app.twin_selected_portrait = None
    app.twin_preview.setPixmap(QPixmap())
    app.twin_preview.setText("未选择")
    _refresh_twin_portraits(app)


def _twin_context_menu(app, pos):
    """右键菜单。"""
    lw = app.twin_portrait_list
    item = lw.itemAt(pos)
    if not item:
        return
    # 右键同时选中该项
    lw.setCurrentItem(item)
    menu = QMenu(lw)
    del_act = QAction("🗑 删除此照片", lw)
    del_act.triggered.connect(lambda: _twin_delete_portrait(app))
    menu.addAction(del_act)
    menu.exec(lw.mapToGlobal(pos))


def _build_twin_prompt(scene, dialogue, keep_bg=True):
    """组装数字人分身的视频 prompt（抽出来为了可单测）。

    keep_bg=True 时：绝不凭空编造背景，并追加 [BACKGROUND LOCK] 锁死参考图背景。
    keep_bg=False 时：允许模型按 scene 另造背景（旧行为）。
    """
    if scene:
        base_scene = scene
    elif keep_bg:
        # 没填场景时**绝不能**写 "warm indoor lighting" —— 那等于指示模型换个
        # 温暖室内背景，参考图的真实背景就被丢掉了（2026-08-30 用户实测反馈）。
        base_scene = ("The person stands in the SAME place and SAME background as the reference "
                      "image, facing the camera, natural and realistic.")
    else:
        base_scene = ("A real person, facing the camera, warm indoor lighting, natural and "
                      "realistic.")

    # 锁定类指令放在 prompt **最末尾**——模型对尾部指令遵循度最高。
    locks = []
    if keep_bg:
        locks.append(
            "[BACKGROUND LOCK] The background, room, environment, furniture, objects, colors "
            "and lighting MUST remain EXACTLY the same as in the reference image, in every "
            "single frame. Do NOT change, replace, restyle, redecorate or move the background. "
            "Do NOT relocate the person to a different place. Only the person's mouth, subtle "
            "facial expression and small natural gestures may change while speaking."
        )
    locks.append(
        "[CRITICAL FACE LOCK] The person in this video MUST be the EXACT same individual as "
        "the reference image — identical face, facial features, skin tone, hairstyle, glasses, "
        "and overall appearance. Do NOT change the person's identity or face in any frame. "
        "Maintain perfect consistency from first frame to last frame."
    )
    locks.append(
        "[CAMERA LOCK] STATIC camera. Absolutely NO camera movement: no zoom, no push-in, no "
        "pull-out, no pan, no tilt, no dolly, no tracking. The camera position, distance and "
        "framing are completely fixed and locked for the entire clip."
    )
    # 反「僵尸/人体模型」：机位锁死 ≠ 人也要冻住。
    # 早期版本写了「头肩每帧位置完全一致」，结果模型输出一个几乎不动的静止假人。
    # 真人说话全程都有微动作，停顿/静音时尤为明显（LongCat-Video-Avatar 的
    # Disentangled Unconditional Guidance 专门解决这一点）。这里必须显式声明：
    # 相机不动，但人是活的。
    locks.append(
        "[MICRO-MOTION — AVOID A FROZEN MANNEQUIN] The CAMERA is locked, but the PERSON must "
        "stay ALIVE and natural throughout the entire clip. Continuously and subtly, including "
        "during pauses and silent moments between sentences, the person: blinks naturally, "
        "breathes (subtle chest and shoulder rise and fall), makes small natural head nods and "
        "slight head tilts, shifts gaze, relaxes and re-engages facial expression, and uses "
        "small restrained hand gestures while speaking. NEVER freeze the person into a still "
        "mannequin or a static portrait — a real human being is never completely motionless. "
        "All motion must be subtle, continuous and lifelike; never jerky, exaggerated or "
        "dance-like. The person's overall position and scale in frame stay stable."
    )
    lock_text = "\n\n".join(locks)
    return f"{tools_mod._build_video_prompt(base_scene, dialogue)}\n\n{lock_text}"


# ---------------- 长口播分段（纯逻辑，可单测） ----------------
# 中文口播语速经验值：约 4.5 字/秒（Agnes 视频模型实测偏稳的朗读速度）。
# 单段时长上限 12 秒（agnes-video-2.5-flash 硬上限），下限 4 秒。
CHARS_PER_SEC = 4.5
SEG_MIN_SEC = 4
SEG_MAX_SEC = 12

# 断句标点：优先在这些后面切，避免把一句话腰斩
_SENT_END = "。！？!?；;\n"
_SOFT_END = "，,、：:）)》」』"


def split_dialogue(text, max_sec=SEG_MAX_SEC, min_sec=SEG_MIN_SEC,
                   chars_per_sec=CHARS_PER_SEC):
    """把长口播台词切成多段，每段对应 Agnes 单次可生成的时长。

    返回 [(段文本, 秒数), ...]。已按语义断句，尽量不在句子中间腰斩。
    单段装得下就只返回一段（不无谓拆分）。
    """
    text = (text or "").strip()
    if not text:
        return []
    max_chars = int(max_sec * chars_per_sec)
    min_chars = int(min_sec * chars_per_sec)

    # 1) 先按句末标点断成「句子」
    sentences = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SENT_END:
            if buf.strip():
                sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    if not sentences:
        return []

    # 2) 单句就超限 -> 按软标点再切；仍超限则硬切
    refined = []
    for s in sentences:
        if len(s) <= max_chars:
            refined.append(s)
            continue
        part = ""
        for ch in s:
            part += ch
            if len(part) >= max_chars and ch in _SOFT_END:
                refined.append(part)
                part = ""
        if part:
            # 没有软标点兜底就按最大容量硬切（宁可断在一个字后，也不超限）
            while len(part) > max_chars:
                refined.append(part[:max_chars])
                part = part[max_chars:]
            if part:
                refined.append(part)

    # 3) 贪心合并：尽量把相邻短句凑满一段，减少段数（段数越多声音漂移越明显）
    segs = []
    cur = ""
    for s in refined:
        if not cur:
            cur = s
        elif len(cur) + len(s) <= max_chars:
            cur += s
        else:
            segs.append(cur)
            cur = s
    if cur:
        segs.append(cur)

    # 4) 字数 -> 秒数（钳制到 API 合法区间）
    out = []
    for s in segs:
        sec = int(round(len(s) / chars_per_sec))
        sec = max(min_sec, min(max_sec, sec))
        out.append((s, sec))
    # 极短尾巴（不足 min_sec 对应的字数）并进上一段，避免出现 4 秒碎片
    if len(out) >= 2 and len(out[-1][0]) < min_chars * 0.6:
        prev_text, prev_sec = out[-2]
        tail_text, _ = out[-1]
        merged = prev_text + tail_text
        out[-2] = (merged, max(min_sec, min(max_sec,
                                            int(round(len(merged) / chars_per_sec)))))
        out.pop()
    return out


# 声音漂移缓解：每段都写死同一套音色描述（用户已确认接受漂移，尽力而为）
VOICE_LOCK = (
    "[VOICE LOCK] The speaking voice MUST remain IDENTICAL across the whole clip and across "
    "all segments: the SAME adult male voice, same timbre, same pitch, same speaking pace, "
    "same volume and same accent. Clear standard Mandarin pronunciation. Do NOT change the "
    "voice, do NOT switch speakers, do NOT add background music."
)


# ---------------- ffmpeg 辅助（AI 标识 / 抽末帧 / 拼接） ----------------
# 四条命令均于 2026-08-30 在本机 ffmpeg 8.1 实机验证通过（见 probe_ffmpeg_twin.py）。


def _ffmpeg():
    """找 ffmpeg：优先复用 video_pipeline（含冻结环境捆绑 ffmpeg 的查找逻辑）。"""
    try:
        from video_pipeline import find_ffmpeg
        p = find_ffmpeg()
        if p:
            return p
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _cn_font():
    """找一个可用的中文字体（drawtext 渲染中文必需，缺字体中文会变方块）。"""
    for c in (r"C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
              r"C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑粗体
              r"C:/Windows/Fonts/simhei.ttf",   # 黑体
              r"C:/Windows/Fonts/simsun.ttc"):  # 宋体
        if os.path.isfile(c):
            return c
    return None


def burn_ai_mark(src, dst, text="AI 生成", ffmpeg=None, log=None):
    """右下角烧常驻「AI 生成」角标（合规硬性要求）。成功返回 True。

    失败不抛异常——标识烧不上也要让用户拿到片子，但要明确告警。
    ⚠️ Windows 坑：fontfile 路径里的冒号必须转义成 \\:，否则 ffmpeg 解析失败。
    """
    ffmpeg = ffmpeg or _ffmpeg()
    if not ffmpeg:
        if log:
            log("⚠️ 未找到 ffmpeg，AI 标识未能烧录（请手动添加后再发布）")
        return False
    font = _cn_font()
    if not font:
        if log:
            log("⚠️ 未找到中文字体，AI 标识未能烧录（请手动添加后再发布）")
        return False
    fp = font.replace(":", "\\:")
    vf = (f"drawtext=fontfile='{fp}':text='{text}':"
          f"fontsize=34:fontcolor=white@0.80:"
          f"box=1:boxcolor=black@0.35:boxborderw=8:"
          f"x=w-tw-28:y=h-th-28")
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", src, "-vf", vf,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", dst],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300)
        if r.returncode == 0 and os.path.isfile(dst):
            return True
        if log:
            log(f"⚠️ AI 标识烧录失败（rc={r.returncode}）：{(r.stderr or '')[-200:]}")
        return False
    except Exception as e:
        if log:
            log(f"⚠️ AI 标识烧录异常：{e}")
        return False


def extract_last_frame(video, out_png, ffmpeg=None, log=None):
    """抽视频末帧（给下一段做首帧接力，保证脸不跳变）。成功返回 True。"""
    ffmpeg = ffmpeg or _ffmpeg()
    if not ffmpeg:
        return False
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-sseof", "-0.15", "-i", video,
             "-frames:v", "1", "-q:v", "2", out_png],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
        return r.returncode == 0 and os.path.isfile(out_png)
    except Exception as e:
        if log:
            log(f"⚠️ 抽末帧异常：{e}")
        return False


def extract_probe_frame(video, out_png, ffmpeg=None, log=None):
    """抽中间一帧用于 VLM 质检（抽末帧有时正好闭眼/侧头，中间帧更能代表全片）。"""
    ffmpeg = ffmpeg or _ffmpeg()
    if not ffmpeg:
        return False
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-ss", "1.0", "-i", video,
             "-frames:v", "1", "-q:v", "2", out_png],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
        ok = r.returncode == 0 and os.path.isfile(out_png)
        # 视频不足 1 秒时 -ss 1.0 抽不到，退回抽第 0 秒
        if not ok:
            r = subprocess.run(
                [ffmpeg, "-y", "-i", video, "-frames:v", "1", "-q:v", "2", out_png],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120)
            ok = r.returncode == 0 and os.path.isfile(out_png)
        return ok
    except Exception as e:
        if log:
            log(f"⚠️ 抽质检帧异常：{e}")
        return False


def fit_image_to_aspect(src, dst, target_w, target_h, ffmpeg=None, log=None):
    """把参考图**居中裁剪**（不拉伸变形）到目标画幅。成功返回 dst。

    ⚠️ 2026-08-30 实测发现的硬事实：Agnes 在 keyframe 模式下会**跟随首帧图的
    比例**输出，完全忽略 aspect_ratio 参数。实测参考图 1408x768(1.833) →
    输出 1280x704(1.818)，而 UI 选的是 768x1152(0.667 竖屏)。
    也就是说：**想出竖屏，首帧图本身就得是竖的**。

    故这里用 scale(覆盖)+crop(居中) 预处理参考图，原图不动。
    数字人参考图一般是正面半身照，人物居中，居中裁剪是安全的。
    """
    ffmpeg = ffmpeg or _ffmpeg()
    if not ffmpeg:
        return None
    try:
        vf = (f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
              f"crop={target_w}:{target_h}")
        r = subprocess.run(
            [ffmpeg, "-y", "-i", src, "-vf", vf, "-q:v", "2", dst],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
        if r.returncode == 0 and os.path.isfile(dst):
            return dst
        if log:
            log(f"  ⚠️ 参考图画幅预处理失败：{(r.stderr or '')[-160:]}")
        return None
    except Exception as e:
        if log:
            log(f"  ⚠️ 参考图画幅预处理异常：{e}")
        return None


def parse_resolution(res):
    """'768x1152' -> (768, 1152)；解析失败返回 (0, 0)。"""
    try:
        w, h = str(res).lower().split("x", 1)
        return int(w), int(h)
    except Exception:
        return 0, 0


def concat_videos(paths, out, ffmpeg=None, log=None):
    """拼接多段。先试 -c copy（快且无损），失败再重编码。"""
    ffmpeg = ffmpeg or _ffmpeg()
    if not ffmpeg:
        return False
    lst = out + ".list.txt"
    try:
        with open(lst, "w", encoding="utf-8") as f:
            for p in paths:
                f.write("file '%s'\n" % p.replace("\\", "/").replace("'", "'\\''"))
        r = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-c", "copy", out],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600)
        if r.returncode == 0 and os.path.isfile(out):
            return True
        if log:
            log("  段间参数不一致，改用重编码拼接（稍慢但必成）…")
        r = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", out],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=900)
        return r.returncode == 0 and os.path.isfile(out)
    except Exception as e:
        if log:
            log(f"⚠️ 拼接异常：{e}")
        return False
    finally:
        try:
            if os.path.isfile(lst):
                os.remove(lst)
        except Exception:
            pass


class TwinGenThread(QThread):
    """数字人口播生成线程：逐段生成 -> 段间尾帧接力 -> 拼接 -> 烧 AI 标识。

    设计原则：**任何一步失败都不整体崩**。能出几段出几段，最后如实汇报，
    绝不静默吞掉错误让用户以为成功了。
    """

    log = Signal(str)
    progress = Signal(int, int)    # (已完成段数, 总段数)
    done = Signal(object)          # 成片绝对路径，或错误字符串

    def __init__(self, cfg, app_dir, portrait, segs, scene, keep_bg,
                 resolution, dual_frame=True, ai_mark=True, qc=True,
                 max_qc_retry=2, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.app_dir = app_dir
        self.portrait = portrait
        self.segs = segs
        self.scene = scene
        self.keep_bg = keep_bg
        self.resolution = resolution
        self.dual_frame = dual_frame
        self.ai_mark = ai_mark
        self.qc = qc
        self.max_qc_retry = max_qc_retry
        self._cancel = False
        self.qc_notes = []         # 每段质检诊断，供 UI 展示

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            self._work()
        except Exception as e:
            self.done.emit(f"异常：{e}")

    # ---- 内部 ----
    def _work(self):
        ff = _ffmpeg()
        portrait_uri = self.portrait       # 本地路径即可，tool_video_gen 内部会转 data URI
        products = getattr(tools_mod, "PRODUCTS_DIR", None) or "products"
        out_dir = os.path.join(self.app_dir, products)
        os.makedirs(out_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        tmp_dir = os.path.join(out_dir, f"_twin_seg_{stamp}")
        os.makedirs(tmp_dir, exist_ok=True)

        # 参考图预处理：按目标画幅居中裁剪。
        # Agnes 在 keyframe 模式下跟随首帧比例、忽略 aspect_ratio，这一步是必需的，
        # 否则选了竖屏却因为参考图是横拍而出横屏（实测踩过）。
        tw, th = parse_resolution(self.resolution)
        if tw > 0 and th > 0:
            fitted = os.path.join(tmp_dir, "portrait_fit.png")
            if fit_image_to_aspect(self.portrait, fitted, tw, th,
                                   ffmpeg=ff, log=self.log.emit):
                portrait_uri = fitted
                self.log.emit(f"参考图已居中裁剪到 {tw}x{th}"
                              f"（Agnes 跟随首帧比例，此步必需）")
            else:
                self.log.emit(f"⚠️ 参考图未能按 {tw}x{th} 预处理，"
                              f"成片比例可能跟随原图而非所选画幅")

        seg_paths = []
        prev_tail = None
        total = len(self.segs)

        for i, (text, sec) in enumerate(self.segs):
            if self._cancel:
                self.log.emit("已取消。")
                break

            # 首帧：第 1 段用本人参考图；之后用上一段末帧（尾帧接力，脸不跳变）
            first_frame = portrait_uri if i == 0 else (prev_tail or portrait_uri)
            # 尾帧：双端锁定时每段都把末尾拉回本人参考图，压住中段漂移
            last_frame = portrait_uri if self.dual_frame else None

            prompt = _build_twin_prompt(self.scene, text, self.keep_bg) \
                + "\n\n" + VOICE_LOCK

            self.log.emit(f"▶ 第 {i+1}/{total} 段（{len(text)}字 / {sec}秒）生成中…")
            seg_path, note = self._gen_one(
                i, prompt, sec, first_frame, last_frame, tmp_dir, ff)
            self.qc_notes.append(note)

            if not seg_path:
                if not seg_paths:
                    self.done.emit("第 1 段生成失败，未产出任何片段。")
                    return
                self.log.emit(f"⚠️ 第 {i+1} 段失败，用已生成的 {len(seg_paths)} 段继续。")
                break

            seg_paths.append(seg_path)
            self.progress.emit(len(seg_paths), total)

            # 抽末帧给下一段接力
            if i < total - 1:
                tail = os.path.join(tmp_dir, f"tail_{i}.png")
                if extract_last_frame(seg_path, tail, ffmpeg=ff, log=self.log.emit):
                    prev_tail = tail
                else:
                    self.log.emit("  ⚠️ 抽末帧失败，下一段改用本人参考图作首帧")
                    prev_tail = None

        if not seg_paths:
            self.done.emit("没有任何片段生成成功。")
            return

        # ---- 拼接 ----
        if len(seg_paths) == 1:
            final = seg_paths[0]
            self.log.emit("单段成片，无需拼接。")
        else:
            self.log.emit(f"🔗 拼接 {len(seg_paths)} 段…")
            final = os.path.join(out_dir, f"twin_merged_{stamp}.mp4")
            if not concat_videos(seg_paths, final, ffmpeg=ff, log=self.log.emit):
                self.done.emit("拼接失败，片段仍在临时目录：" + tmp_dir)
                return

        # ---- AI 标识（合规红线）----
        if self.ai_mark:
            self.log.emit("🏷 烧录 AI 标识…")
            marked = os.path.join(out_dir, f"twin_ai_{stamp}.mp4")
            if burn_ai_mark(final, marked, ffmpeg=ff, log=self.log.emit):
                final = marked
                self.log.emit("✅ AI 标识已烧录（右下角）")
            else:
                self.log.emit("⚠️ AI 标识烧录失败——**发布前请手动添加**，否则违规。")

        # 清理分段临时文件（保留成片）
        try:
            for p in seg_paths:
                if os.path.isfile(p) and os.path.abspath(p) != os.path.abspath(final):
                    os.remove(p)
            if os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

        self.done.emit(final)

    def _gen_one(self, i, prompt, sec, first_frame, last_frame, tmp_dir, ff):
        """生成单段（带 VLM 质检重试）。返回 (路径 or None, 质检诊断文本)。"""
        last_note = ""
        seg_path = None
        for attempt in range(self.max_qc_retry + 1):
            if self._cancel:
                return seg_path, last_note
            res = tools_mod.tool_video_gen(
                self.cfg, self.app_dir, prompt, sec, None,
                resolution=self.resolution,
                first_frame=first_frame, last_frame=last_frame, dialogue=None)
            if isinstance(res, str):
                self.log.emit(f"  ❌ 生成失败：{res}")
                return seg_path, last_note
            rel, _kind, _name = res
            seg_path = os.path.join(self.app_dir, rel)

            if not self.qc:
                return seg_path, ""

            probe = os.path.join(tmp_dir, f"probe_{i}_{attempt}.png")
            if not extract_probe_frame(seg_path, probe, ffmpeg=ff, log=self.log.emit):
                self.log.emit("  ⚠️ 抽质检帧失败，放行。")
                return seg_path, ""
            passed, note = vq.review_identity(
                self.cfg, self.portrait, probe, log=self.log.emit)
            last_note = note
            if passed:
                self.log.emit("  ✅ 质检通过（仍是本人，无畸变）")
                return seg_path, note
            if attempt >= self.max_qc_retry:
                self.log.emit("  ⚠️ 质检仍未通过，保留最后一次结果（请人工过目）："
                              + (note[:120] if note else ""))
                return seg_path, note
            self.log.emit(f"  ⚠️ 质检未通过，重生成（第 {attempt+2} 次）…")
        return seg_path, last_note


def _twin_generate(app):
    if not app.twin_selected_portrait:
        app.twin_status.setText("请先选择 / 添加一张本人照片作为参考图。")
        return
    dialogue = app.twin_dialogue.toPlainText().strip()
    if not dialogue:
        app.twin_status.setText("请填写口播台词（中文）。")
        return
    scene = app.twin_scene.toPlainText().strip()
    res = app.twin_resolution.currentData() or "768x1152"
    dur = app.twin_duration.value()
    _kb = getattr(app, "twin_keep_bg", None)
    keep_bg = _kb.isChecked() if _kb is not None else True

    # 长口播分段：按用户设定的单段时长切（突破单次 12 秒上限）
    segs = split_dialogue(dialogue, max_sec=dur)
    if not segs:
        app.twin_status.setText("台词解析为空，请检查输入。")
        return

    def _cb(name, default=True):
        w = getattr(app, name, None)
        return w.isChecked() if w is not None else default

    ai_mark = _cb("twin_ai_mark", True)
    qc = _cb("twin_qc", True)
    dual_frame = _cb("twin_dual_frame", True)

    total_sec = sum(s for _t, s in segs)
    tip = f"共 {len(segs)} 段 / 约 {total_sec} 秒"
    if len(segs) > 1:
        tip += "（段间尾帧接力，声音可能有细微差异）"
    app.twin_status.setText(f"提交任务中…{tip}（可能需数分钟，请勿关闭窗口）")

    app.twin_thread = TwinGenThread(
        app.cfg, APP_DIR, app.twin_selected_portrait, segs, scene, keep_bg, res,
        dual_frame=dual_frame, ai_mark=ai_mark, qc=qc)
    app.twin_thread.log.connect(lambda m: app.twin_status.setText(m))
    app.twin_thread.progress.connect(
        lambda a, b: app.twin_status.setText(f"已完成 {a}/{b} 段…"))
    app.twin_thread.done.connect(lambda r: _twin_on_result(app, r))
    app.twin_thread.start()


def _twin_on_result(app, res):
    if isinstance(res, str):
        # 字符串可能是成片绝对路径，也可能是错误文本
        if os.path.isfile(res):
            name = os.path.basename(res)
            rel = os.path.relpath(res, APP_DIR)
            kind = "video"
            app.twin_paths.append(res)
            app.twin_result.addItem(name)
            app.twin_status.setText(f"已生成：{name}")
            try:
                app.store.active().deliverables.append(
                    {"rel": rel, "kind": kind, "name": name, "desc": rel})
                app.store.save()
                app._refresh_deliverables()
            except Exception:
                pass
        else:
            app.twin_status.setText(res)
        return
    rel, kind, name = res
    app.twin_status.setText(f"已生成：{name}")
    app.twin_paths.append(os.path.join(APP_DIR, rel))
    app.twin_result.addItem(name)
    # 同步进交付物面板（与其他工作台一致）
    try:
        app.store.active().deliverables.append(
            {"rel": rel, "kind": kind, "name": name, "desc": rel})
        app.store.save()
        app._refresh_deliverables()
    except Exception:
        pass


def _twin_open_result(app, item):
    idx = app.twin_result.row(item)
    if 0 <= idx < len(app.twin_paths):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(app.twin_paths[idx]))
