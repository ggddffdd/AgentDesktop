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

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QLineEdit,
    QSpinBox, QComboBox, QListWidget, QListWidgetItem, QFileDialog, QSizePolicy,
    QGroupBox, QFrame, QMenu,
)
from PySide6.QtGui import QPixmap, QIcon, QAction
from PySide6.QtCore import Qt, QSize

from ui import THEME, _GenThread
from config import APP_DIR
import tools as tools_mod


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


def _avatar_dir():
    d = os.path.join(APP_DIR, "avatars")
    os.makedirs(d, exist_ok=True)
    return d


def build_twin_panel(app):
    page = app.twin_page
    lay = QVBoxLayout(page)
    lay.setContentsMargins(32, 24, 32, 24)
    lay.setSpacing(16)

    head = QLabel("数字人分身 · 我自己")
    head.setStyleSheet(f"font-size:20px;font-weight:700;color:{THEME['text']};")
    lay.addWidget(head)
    sub = QLabel("本人形象 + 口播台词，一键生成「数字人分身」口播视频（Agnes 直连，免费）。"
                 "先添加一张本人照片作为参考图，再写口播台词即可。")
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
    app.twin_scene.setPlaceholderText("例如：坐在书桌前，穿白色衬衫，温暖室内光，背后是书架")
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
    app.twin_dialogue.setPlaceholderText("例如：大家好，我是Agent。今天跟大家聊聊……（用中文说）")
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
    app.twin_duration.setRange(3, 16)
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

    gen_btn = QPushButton("🎬 生成分身口播视频")
    gen_btn.setFixedHeight(36)
    gen_btn.setCursor(Qt.PointingHandCursor)
    gen_btn.setStyleSheet(_btn_accent_style())
    gen_btn.clicked.connect(lambda: _twin_generate(app))
    opt.addWidget(gen_btn)
    lay.addLayout(opt)

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

    # 场景描述作为主体 prompt（放在最前面，设定画面基调）
    base_scene = (scene if scene else
                  "A real person, facing the camera, warm indoor lighting, natural and realistic.")

    data_uri = app._file_to_datauri(app.twin_selected_portrait)
    if not data_uri:
        app.twin_status.setText("参考图读取失败，请重新添加。")
        return

    # 自己组装完整 prompt（不依赖 tool_video_gen 内部的 dialogue 拼接），
    # 把人脸锁定 + 镜头锁定指令放在 prompt **最末尾**——模型对尾部指令遵循度最高。
    if dialogue:
        prompt = (
            f"{base_scene}\n\n"
            f"The character speaks in Chinese (用中文说): \"{dialogue}\". "
            f"NO English speech. Natural lip-synced mouth movement, clear spoken Mandarin voice.\n\n"
            f"[CRITICAL FACE LOCK] The person in this video MUST be the EXACT same individual "
            f"as the reference image — identical face, facial features, skin tone, hairstyle, "
            f"glasses, and overall appearance. Do NOT change the person's identity or face "
            f"in any frame. Maintain perfect consistency from first frame to last frame.\n\n"
            f"[CAMERA LOCK] STATIC camera. Absolutely NO camera movement: no zoom, no push-in, "
            f"no pull-out, no pan, no tilt, no dolly, no tracking. The camera position and "
            f"framing are completely fixed and locked. The subject's head and shoulders stay "
            f"in the exact same position in every single frame."
        )
    else:
        prompt = (
            f"{base_scene}\n\n"
            f"[CRITICAL FACE LOCK] The person in this video MUST be the EXACT same individual "
            f"as the reference image — identical face, facial features, skin tone, hairstyle, "
            f"glasses, and overall appearance. Do NOT change the person's identity or face.\n\n"
            f"[CAMERA LOCK] STATIC camera. Absolutely NO camera movement: no zoom, no push-in, "
            f"no pull-out, no pan, no tilt, no dolly, no tracking. The camera position and "
            f"framing are completely fixed and locked. The subject's head and shoulders stay "
            f"in the exact same position in every single frame."
        )

    app.twin_status.setText("提交任务中…（可能需数分钟，请勿关闭窗口）")
    # dialogue 留空（已手动拼入 prompt），first_frame 传入确保首帧=参考图
    app.twin_thread = _GenThread(
        tools_mod.tool_video_gen, app.cfg, APP_DIR, prompt, dur, None,
        resolution=res, first_frame=data_uri, dialogue=None)
    app.twin_thread.result.connect(lambda r: _twin_on_result(app, r))
    app.twin_thread.start()


def _twin_on_result(app, res):
    if isinstance(res, str):
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
