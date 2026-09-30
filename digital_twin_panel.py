"""数字人分身面板（小臭内嵌工作台）。

承接桌面端「数字人分身 / 我自己」Agent 的核心能力，做成小臭里的一个真嵌入面板：
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
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QLineEdit,
    QSpinBox, QComboBox, QListWidget, QListWidgetItem, QFileDialog, QSizePolicy,
    QGroupBox, QFrame, QMenu, QCheckBox,
)
from PySide6.QtGui import QPixmap, QIcon, QAction
from PySide6.QtCore import Qt, QSize, QThread, Signal

from ui import THEME, _GenThread, RES_PRESETS
from config import APP_DIR
import tools as tools_mod
import vision_qc as vq


AVATAR_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# 低于此体积视为程序内置图标（如 26x26 的 avatar_user.png），不是本人照片，
# 迁移旧照片时跳过，免得把占位图标当成形象搬进用户目录。
MIN_PHOTO_BYTES = 10 * 1024


def _avatar_dir():
    """本人形象目录（用户数据，必须落在 Documents 下，不能落程序目录）。

    ⚠️ 2026-08-30 修正：原先是 APP_DIR/avatars，而 APP_DIR 在冻结环境就是
    dist/小臭玩AI/ —— 重打包时整个 dist 会被搬走重建，用户照片（还是本人
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
    head.setStyleSheet(f"font-size:{THEME['font_title_xl']};font-weight:700;color:{THEME['text']};")
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
        f"border-radius:10px;padding:12px 12px;font-size:13px;color:{THEME['text']};"
        f"margin-top:8px;}} QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 8px;}}")
    ab_lay = QHBoxLayout(avatar_box)
    ab_lay.setSpacing(12)

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
        f"border-radius:8px;padding:8px;}}"
        f"QListWidget::item{{border-radius:6px;padding:4px;}}"
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
    del_btn = QPushButton("删除选中")
    del_btn.setFixedHeight(32)
    del_btn.setCursor(Qt.PointingHandCursor)
    del_btn.setStyleSheet(
        f"QPushButton{{background:{THEME["danger_bg2"]};color:{THEME["danger_text_dark"]};border:1px solid {THEME["danger_border"]};"
        f"border-radius:8px;padding:0 12px;font-size:13px;}}"
        f"QPushButton:hover{{background:{THEME["danger_hover_bg"]};}}")
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
    ward.setSpacing(8)
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
    dia.setSpacing(8)
    dlab = QLabel("口播台词（中文，必填）")
    dlab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
    dia.addWidget(dlab)
    app.twin_dialogue = QTextEdit()
    app.twin_dialogue.setFixedHeight(96)
    app.twin_dialogue.setPlaceholderText("例如：大家好，我是小臭。今天跟大家聊聊……")
    app.twin_dialogue.setStyleSheet(_edit_style())
    dia.addWidget(app.twin_dialogue)
    # 实时分段预览：让「长口播会被自动拆成多段」当场可见。
    # 起因（2026-09-17 用户反馈）：下方「时长」下拉最高只有 12 秒，被误读成
    # 「整条视频最多 12 秒、只能 1 镜」，于是以为长口播做不了。用这行把真实
    # 段数 / 总时长算出来给他看，误会当场消除。
    app.twin_seg_preview = QLabel("")
    app.twin_seg_preview.setWordWrap(True)
    app.twin_seg_preview.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    dia.addWidget(app.twin_seg_preview)
    app.twin_dialogue.textChanged.connect(lambda: _update_twin_seg_preview(app))
    mid.addLayout(dia, 1)

    lay.addLayout(mid)

    # ---------------- 选项行 ----------------
    opt = QHBoxLayout()
    opt.setSpacing(12)

    dur_lab = QLabel("每段时长")
    dur_lab.setStyleSheet(f"font-size:13px;color:{THEME['text']};")
    dur_lab.setToolTip(
        "Agnes 单次生成上限 12 秒——这是「每一段」的时长，不是整条视频的长度。\n"
        "长口播会按这个秒数**自动切成多段**，逐段生成后再拼成一整条长视频，\n"
        "所以整条视频的总时长不受 12 秒限制（口播框下方会实时显示将切成几段 / 共多少秒）。")
    opt.addWidget(dur_lab)
    app.twin_duration = QSpinBox()
    # 新版 agnes-video-2.5-flash 单段时长合法范围 4~12 秒（旧版 3~16s）
    app.twin_duration.setRange(4, 12)
    app.twin_duration.setValue(8)
    app.twin_duration.setSuffix(" 秒")
    app.twin_duration.setToolTip(dur_lab.toolTip())
    app.twin_duration.valueChanged.connect(lambda _v: _update_twin_seg_preview(app))
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
        f"QCheckBox::indicator{{width:16px;height:16px;border-radius:6px;"
        f"border:1px solid {THEME['border']};background:{THEME['card']};}}"
        f"QCheckBox::indicator:checked{{background:{THEME['accent']};"
        f"border:1px solid {THEME['accent']};}}")
    opt.addWidget(app.twin_keep_bg)

    gen_btn = QPushButton("生成分身口播视频")
    gen_btn.setFixedHeight(36)
    gen_btn.setCursor(Qt.PointingHandCursor)
    gen_btn.setStyleSheet(_btn_accent_style())
    gen_btn.clicked.connect(lambda: _twin_generate(app))
    opt.addWidget(gen_btn)
    app.twin_gen_btn = gen_btn  # v4.186.0（P1-8）：运行期可由状态回调禁用/恢复

    # v4.186.0（P1-8）：停止按钮。原实现 TwinGenThread.cancel() 是死代码（零调用），
    # 长任务（多段×分钟级）没有停止入口，用户只能烧额度等自然结束。
    stop_btn = QPushButton("⏹ 停止")
    stop_btn.setFixedHeight(36)
    stop_btn.setCursor(Qt.PointingHandCursor)
    stop_btn.setEnabled(False)  # 仅生成中可用
    stop_btn.setToolTip(
        "停止本次生成：已完成的段保留（断点续跑可接着补），\n"
        "正在生成的一段会在几秒内中断轮询（不再烧额度）。")
    stop_btn.setStyleSheet(
        "QPushButton{{background:{bg};color:{fg};border:1px solid {bd};"
        "border-radius:8px;padding:0 16px;font-size:13px;}}"
        "QPushButton:hover{{background:{hov};}}"
        "QPushButton:disabled{{color:{dis};border:1px solid {bd2};}}".format(
            bg=THEME["danger_bg2"], fg=THEME["danger_text_dark"],
            bd=THEME["danger_border"], hov=THEME["danger_hover_bg"],
            dis=THEME["faint"], bd2=THEME["border"]))
    stop_btn.clicked.connect(lambda: _twin_stop(app))
    opt.addWidget(stop_btn)
    app.twin_stop_btn = stop_btn
    lay.addLayout(opt)

    # ---------------- 增强选项行 ----------------
    opt2 = QHBoxLayout()
    opt2.setSpacing(12)

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
        "⚠️ 调用大哥已付费订阅的 DeepSeek，会产生少量费用；关掉则只靠参考图锁定。")

    # 形象/背景锁定（reference 模式）：默认开 —— 背景不跳 + 可并发
    app.twin_ref_lock = QCheckBox("🔒 形象/背景锁定")
    app.twin_ref_lock.setChecked(True)
    app.twin_ref_lock.setToolTip(
        "默认**勾选**（推荐）：每一段都以本人照片作**同一张参考图**生成。\n"
        "· 背景与形象全程锚定同一张图，段与段之间不会各说各话 → **背景不跳**；\n"
        "· 各段互相独立 → **可并发出片，速度更快**。\n"
        "关掉则退回旧模式（首尾帧 + 上一段末帧接力）：误差会逐段累积、\n"
        "背景容易漂移，且只能一段一段串行生成。")

    # 断点续跑开关（默认自动续跑；勾上则无视上次进度全部重生成）
    app.twin_force_redo = QCheckBox("♻ 全部重新生成")
    app.twin_force_redo.setChecked(False)
    app.twin_force_redo.setToolTip(
        "默认**不勾**：同一份口播稿若上次有段落失败/中断，本次会自动**跳过已完成\n"
        "的段**（断点续跑）——差一段不必整条重跑，不重复消耗生成额度。\n"
        "勾上：无视上次进度，所有段全部重新生成。")

    for c in (app.twin_ai_mark, app.twin_qc, app.twin_ref_lock,
              app.twin_force_redo):
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
# v4.182.0 收编：样式函数改为 theme_qss 中心层兼容壳（旧名保留，调用点零改动）。
def _btn_style():
    from theme_qss import btn_outline
    return btn_outline()


def _btn_accent_style():
    # 强调按钮 14px（分场景归档决策）
    from theme_qss import btn_primary, F, W
    return btn_primary(font_size=F["input"], weight=W["medium"])


def _edit_style():
    from theme_qss import edit_style
    return edit_style()


def _combo_style():
    from theme_qss import combo_style
    return combo_style()


def _chk_style():
    # 数字人版复选框带圆角指示器（比 theme_qss.chk_style 视觉多态），保留本地实现
    return (f"QCheckBox{{color:{THEME['text']};font-size:13px;spacing:6px;}}"
            f"QCheckBox::indicator{{width:16px;height:16px;border-radius:6px;"
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
    del_act = QAction("删除此照片", lw)
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


def _update_twin_seg_preview(app):
    """实时显示口播将被切成几段、预计总时长。

    专治「下拉只有 12 秒 → 以为整条只有 12 秒 / 只能 1 镜」的误读：
    把 split_dialogue 的真实结果（段数 + 总秒数）直接摊在口播框下面。
    """
    w = getattr(app, "twin_seg_preview", None)
    if w is None:
        return
    dlg = getattr(app, "twin_dialogue", None)
    text = dlg.toPlainText().strip() if dlg is not None else ""
    if not text:
        w.setText("")
        return
    dw = getattr(app, "twin_duration", None)
    dur = dw.value() if dw is not None else SEG_MAX_SEC
    segs = split_dialogue(text, max_sec=dur)
    if not segs:
        w.setText("")
        return
    n = len(segs)
    total = sum(s for _t, s in segs)
    if n <= 1:
        w.setText(f"共 1 段 · 约 {total} 秒（单段上限 {SEG_MAX_SEC} 秒）")
    else:
        w.setText(f"长口播 → 自动切成 {n} 段，逐段生成后拼接 · "
                  f"预计总时长 ≈ {total} 秒（整条不受 {SEG_MAX_SEC} 秒限制）")


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


# ---------------- 断点续跑（借鉴「葱头玩AI」微课分镜，2026-09-28）----------------
# 问题：一次生成 5 段，第 3 段失败 → 重跑要**全部重新生成**，白烧 4 段的额度与时间。
# 做法：把「同一批输入」映射到同一个任务目录，每段成功即落盘 + 记账；
#       重跑时已完成段直接复用，只补缺失的段。
# 关键取舍：
#   · 指纹只含**决定段内容**的输入（口播稿 / 参考图 / 场景 / 画幅 / 单段时长 /
#     保持背景 / 双帧锁定），**不含 qc 与 ai_mark** —— 那两个是后处理，
#     改了不该让历史段作废。
#   · 段复用还要求**台词 hash 一致**，挡住「指纹没变但文本被改」这类边角。
#   · 任务**全部成功即清理**（不残留）；**有缺段才保留**供下次续跑。
#   · state.json 原子写（os.replace）；复用前校验文件真实存在且非空。

def _twin_fingerprint(dialogue, portrait, scene, resolution, dur,
                      keep_bg, dual_frame, ref_mode=True):
    """同一批输入 → 同一个任务目录（断点续跑的基础）。"""
    try:
        st = os.stat(portrait)
        p_img = f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        p_img = "?"
    payload = json.dumps(
        {"d": dialogue or "", "p": p_img, "s": scene or "",
         "r": resolution or "", "t": int(dur or 0),
         "b": bool(keep_bg), "f": bool(dual_frame), "m": bool(ref_mode)},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _twin_job_dir(out_dir, fp):
    return os.path.join(out_dir, "_twin_jobs", fp)


def _twin_text_sha1(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _load_job_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_job_state(path, state):
    """原子写：先写 .tmp 再 os.replace，不留半截 JSON。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def _job_seg_ok(state, idx, text, seg_file):
    """该段能否复用：state 标记 ok + 台词 hash 一致 + 文件存在且非空。"""
    rec = (state.get("segs") or {}).get(str(idx))
    if not rec or not rec.get("ok"):
        return False
    if rec.get("sha1") != _twin_text_sha1(text):
        return False
    try:
        return os.path.isfile(seg_file) and os.path.getsize(seg_file) > 0
    except OSError:
        return False


def _job_done_count(state, segs, tmp_dir):
    """已完成的段数（生成前提示用户「将跳过 N 段」）。"""
    n = 0
    for i, (t, _s) in enumerate(segs, 1):
        if _job_seg_ok(state, i, t, os.path.join(tmp_dir, "seg_%03d.mp4" % i)):
            n += 1
    return n


class TwinGenThread(QThread):
    """数字人口播生成线程：逐段生成 -> 段间尾帧接力 -> 拼接 -> 烧 AI 标识。

    设计原则：**任何一步失败都不整体崩**。能出几段出几段，最后如实汇报，
    绝不静默吞掉错误让用户以为成功了。

    借鉴「葱头玩AI」微课分镜的抗失败模式（2026-09-28），四处关键：
      ① **单段失败不再中断后续段**（原实现 `break` → 从失败段起后面全丢，
         用户看到的现象就是"差一大段它就合成了"）；
      ② **生成失败也要重试**（原来只有"质检不过"才重试，"生成失败"立刻放弃；
         而视频生成失败多为瞬时——限流/队列拥堵/超时，重试常能过）；
      ③ **缺段必须显式说出来**（成片照常出，但状态栏与日志都标明缺哪几段）；
      ④ **断点续跑**（`resume` + `job_dir`）：同一批输入的已完成段落盘记账，
         重跑时直接复用，**只补缺失的段**，不重复烧额度（差一段不必整条重跑）。

    v4.179.0 —— **背景锁定 + 并发出片**（同一处机制的两个收益）：
      内核按参数自动推导模式（`video-agent/core/agnes.py:266-274`）——
        `images` → **reference**；只给 `first/last_frame` → **keyframe**。
      旧实现只传首尾帧 → 走 keyframe：每段以**上一段的末帧**为首帧，
      误差逐段累积 → **背景逐段漂移，拼接处"跳"**（大哥反馈的现象）。
      `ref_mode=True`（默认）改为 **reference 模式**：每段都以**同一张本人参考图**
      为参考 → 背景/形象锚定一致、不跳；且段与段**互相独立、无需尾帧接力**
      → 正好**可以并发出片**（`workers`，默认 3）。
      `ref_mode=False` 保留旧的 keyframe + 尾帧接力（串行），作为回退。
    """

    log = Signal(str)
    progress = Signal(int, int)    # (已完成段数, 总段数)
    done = Signal(object)          # 成片绝对路径，或错误字符串

    def __init__(self, cfg, app_dir, portrait, segs, scene, keep_bg,
                 resolution, dual_frame=True, ai_mark=True, qc=True,
                 max_qc_retry=2, max_gen_retry=2, gen_retry_delay=3.0,
                 resume=True, job_dir=None,
                 ref_mode=True, workers=3, parent=None):
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
        self.max_gen_retry = max_gen_retry        # 生成失败（瞬时错误）重试次数
        self.gen_retry_delay = gen_retry_delay    # 重试基础退避（秒，线性递增）
        self.resume = resume                      # 断点续跑：复用同任务已完成的段
        self.job_dir = job_dir                    # 固定任务目录（None=用时间戳临时目录）
        self.ref_mode = ref_mode                  # True=reference 模式（背景锁定 + 可并发）
        self.workers = max(1, int(workers or 1))  # 并发路数（ref_mode 下才生效）
        self._cancel = False
        # v4.186.0（P1-8）：内核级取消令牌。原实现 cancel() 只置 _cancel 标志，
        # 只能在「段与段之间」生效——正在生成的一段（Agnes 轮询+下载，分钟级）
        # 完全无法中断，用户只能烧着额度等超时。token 传入 tool_video_gen 后
        # 内核轮询/下载循环会实时检查并抛 CancelledError。
        from cancel_token import CancellationToken
        self._ct = CancellationToken(name="twin_gen")
        self.qc_notes = []         # 每段质检诊断，供 UI 展示
        self.failed_segs = []      # [(段号, 原因)]：成片缺段时**必须**让 UI 说出来
        self.reused = 0            # 本轮复用的段数（供 UI 汇报"省了几段"）
        # v4.188 P3：task_id 记账锁——并发模式下 on_submit 在 worker 线程触发，
        # 与主线程 _record_seg 的记账/落盘串行化（state 是共享 dict）
        self._task_lock = threading.Lock()

    def cancel(self):
        self._cancel = True
        try:
            self._ct.cancel(reason="user_stop", stage="twin_gen")
        except Exception:
            pass

    def _sleep(self, sec):
        """可取消的等待（重试退避用）。返回 True 表示等待期间被取消。"""
        end = time.time() + max(0.0, float(sec))
        while time.time() < end:
            if self._cancel:
                return True
            time.sleep(0.2)
        return False

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
        # 任务目录：断点续跑时用调用方给的固定目录（按输入指纹命名），
        # 否则退回时间戳临时目录（旧行为）。
        tmp_dir = self.job_dir or os.path.join(out_dir, f"_twin_seg_{stamp}")
        os.makedirs(tmp_dir, exist_ok=True)
        state_path = os.path.join(tmp_dir, "state.json")
        state = _load_job_state(state_path) if (self.resume and self.job_dir) else {}

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

        seg_map = {}              # 段号(0基) -> 段文件路径（最后按序拼）
        total = len(self.segs)

        # ---- 阶段 1：断点续跑复用（顺序、秒级，先做掉）----
        todo = []                 # [(i, text, sec, seg_file)]
        for i, (text, sec) in enumerate(self.segs):
            if self._cancel:
                break
            seg_file = os.path.join(tmp_dir, "seg_%03d.mp4" % (i + 1))
            if self.resume and _job_seg_ok(state, i + 1, text, seg_file):
                self.reused += 1
                self.log.emit(f"⏭ 第 {i+1}/{total} 段沿用上次结果（已完成，跳过生成）")
                seg_map[i] = seg_file
                self.progress.emit(len(seg_map), total)
                continue
            todo.append((i, text, sec, seg_file))

        # ---- 阶段 2：生成 ----
        # reference 模式：每段都锚定**同一张**本人参考图 → 段间互相独立 → 可并发出片；
        # keyframe 模式：下一段要拿上一段末帧当首帧（尾帧接力）→ 只能串行。
        if self.ref_mode and len(todo) > 1 and self.workers > 1:
            self._gen_parallel(todo, tmp_dir, ff, state, state_path, seg_map,
                               portrait_uri, total)
        else:
            self._gen_serial(todo, tmp_dir, ff, state, state_path, seg_map,
                             portrait_uri, total)

        # 并发完成的顺序不可控 → 必须按段号重排，否则拼接顺序会乱
        seg_paths = [seg_map[k] for k in sorted(seg_map)]

        if not seg_paths:
            detail = "、".join(f"第 {n} 段（{why}）" for n, why in self.failed_segs[:5])
            self.done.emit("所有片段均生成失败，未产出视频。"
                           + (f"\n失败明细：{detail}" if detail else ""))
            return

        # ---- 拼接 ----
        if len(seg_paths) == 1:
            # 单段：把段文件**移出任务目录**再当"成片"。
            # ⚠️ 不能直接 `final = seg_paths[0]` —— 它位于 tmp_dir 内，
            # 收尾清目录时会把成片一起删掉（旧实现就有这个 bug：关掉 AI 标识
            # + 短口播（单段）→ 成片消失，界面反而把它当错误文本显示）。
            final = os.path.join(out_dir, f"twin_single_{stamp}.mp4")
            try:
                shutil.move(seg_paths[0], final)
                seg_paths[0] = final
                self.log.emit("单段成片，无需拼接。")
            except Exception as e:
                final = seg_paths[0]
                self.log.emit(f"单段成片（移入输出目录失败，保留在原处）：{e}")
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

        # ---- 缺段必须显式说出来（成片能出，但口播内容不完整）----
        if self.reused:
            self.log.emit(f"♻️ 本次复用了 {self.reused}/{total} 段"
                          f"（断点续跑，未重复生成）")
        if self.failed_segs:
            miss = "、".join(f"第 {n} 段" for n, _ in self.failed_segs)
            self.log.emit(f"⚠️ 成片缺少 {len(self.failed_segs)}/{total} 段（{miss}）"
                          f"——**口播内容不完整**，建议重跑补齐。")

        # ---- 收尾 ----
        # · 全部成功 → 清掉分段文件与任务目录（不残留、不占空间）
        # · 有缺段   → **保留任务目录**（段文件 + state.json），供下次断点续跑
        try:
            if self.failed_segs:
                self.log.emit("📌 已保留任务缓存（含已完成的段与进度）——"
                              "下次点「生成」会自动跳过已完成的段，只补缺的")
            else:
                for p in seg_paths:
                    if os.path.isfile(p) and os.path.abspath(p) != os.path.abspath(final):
                        os.remove(p)
                if os.path.isdir(tmp_dir) and not os.path.abspath(final).startswith(
                        os.path.abspath(tmp_dir) + os.sep):
                    shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

        self.done.emit(final)

    # ---- 阶段 2：两条生成路径 ----
    def _archive_seg(self, seg_path, seg_file):
        """把生成结果落到固定名（断点续跑要按固定名找回来）；失败则退回原路径。"""
        try:
            if os.path.abspath(seg_path) != os.path.abspath(seg_file):
                shutil.move(seg_path, seg_file)
            return seg_file
        except Exception as e:
            self.log.emit(f"  ⚠️ 段文件归档失败（不影响本次拼接）：{e}")
            return seg_path

    def _record_seg(self, state, state_path, idx, text, seg_path):
        """记账（原子写）：这段下次可直接复用、不必重新生成。"""
        if not self.job_dir:
            return
        state.setdefault("segs", {})[str(idx)] = {
            "ok": True, "sha1": _twin_text_sha1(text),
            "file": os.path.basename(seg_path),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _save_job_state(state_path, state)

    def _record_task(self, state, state_path, idx, task_id):
        """登记远端 task_id（v4.188 P3）。

        tool_video_gen 的 on_submit 在「提交成功」即刻回调（v4.168.0 加的能力，
        导演台已用上，twin 一直没接）。落盘进 state["tasks"] 后，关软件/断网
        都能凭它对账——这段远端到底提交过没有，不重复提交、不重复扣费。
        并发模式下回调发生在 worker 线程，加 _task_lock 与主线程记账串行化；
        _gen_one 重试会多次提交，同一 idx 覆盖为最后一次（对账以最新为准）。
        """
        if not self.job_dir or not task_id:
            return
        try:
            with self._task_lock:
                state.setdefault("tasks", {})[str(idx)] = {
                    "task_id": str(task_id),
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
                _save_job_state(state_path, state)
            self.log.emit(f"  [第{idx}段] 远端任务已提交并登记"
                          f"（task_id={str(task_id)[:24]}）")
        except Exception:
            pass

    def _gen_serial(self, todo, tmp_dir, ff, state, state_path, seg_map,
                    portrait_uri, total):
        """串行逐段 + 尾帧接力（keyframe 模式；reference 模式也可用，只是慢）。"""
        prev_tail = None
        for (i, text, sec, seg_file) in todo:
            if self._cancel:
                self.log.emit("已取消。")
                break
            if self.ref_mode:
                # reference：每段都锚定同一张参考图（首尾帧不用 —— 三模式互斥）
                images, first_frame, last_frame = [portrait_uri], None, None
            else:
                # keyframe：第 1 段用参考图，之后用上一段末帧（尾帧接力）
                images = None
                first_frame = portrait_uri if i == 0 else (prev_tail or portrait_uri)
                last_frame = portrait_uri if self.dual_frame else None
            prompt = _build_twin_prompt(self.scene, text, self.keep_bg) \
                + "\n\n" + VOICE_LOCK

            self.log.emit(f"▶ 第 {i+1}/{total} 段（{len(text)}字 / {sec}秒）生成中…")
            # v4.188 P3：提交即登记远端 task_id（对账防重复提交/扣费）
            _on_sub = (lambda tid, _i=i: self._record_task(
                state, state_path, _i + 1, tid)) if self.job_dir else None
            seg_path, note = self._gen_one(
                i, prompt, sec, first_frame, last_frame, tmp_dir, ff, images=images,
                on_submit=_on_sub)
            self.qc_notes.append(note)

            if not seg_path:
                # 单段失败**不中断后续段**（原实现 break → 从失败段起后面全丢）
                self.failed_segs.append((i + 1, "多次重试后仍生成失败"))
                self.log.emit(f"⚠️ 第 {i+1}/{total} 段失败 → 跳过，继续生成后面的段"
                              f"（本轮已缺 {len(self.failed_segs)} 段）")
                continue

            seg_path = self._archive_seg(seg_path, seg_file)
            seg_map[i] = seg_path
            self.progress.emit(len(seg_map), total)
            self._record_seg(state, state_path, i + 1, text, seg_path)

            # 只有 keyframe 才需要抽末帧（reference 模式下段间本就互相独立）
            if (not self.ref_mode) and i < total - 1:
                tail = os.path.join(tmp_dir, f"tail_{i}.png")
                if extract_last_frame(seg_path, tail, ffmpeg=ff, log=self.log.emit):
                    prev_tail = tail
                else:
                    self.log.emit("  ⚠️ 抽末帧失败，下一段改用本人参考图作首帧")
                    prev_tail = None

    def _gen_parallel(self, todo, tmp_dir, ff, state, state_path, seg_map,
                      portrait_uri, total):
        """并发出片（reference 模式专属：每段都锚定同一张参考图，段间无依赖）。

        保守并发（`workers` 默认 3）—— 免费队列并发过高会全线限流，反而更慢。
        落盘/记账/进度统一切回本线程做，避免多线程改同一份 state。
        """
        n = max(1, min(self.workers, len(todo)))
        self.log.emit(f"🚀 并发出片：待生成 {len(todo)} 段 / 并发 {n} 路"
                      f"（参考图模式：每段锚定同一张图，段间独立）")

        def _job(item):
            i, text, sec, seg_file = item
            prompt = _build_twin_prompt(self.scene, text, self.keep_bg) \
                + "\n\n" + VOICE_LOCK
            # v4.188 P3：并发模式同样登记 task_id（_record_task 内部加锁，
            # 与主线程 as_completed 循环里的 _record_seg 串行化）
            _on_sub = (lambda tid, _i=i: self._record_task(
                state, state_path, _i + 1, tid)) if self.job_dir else None
            seg_path, note = self._gen_one(
                i, prompt, sec, None, None, tmp_dir, ff, images=[portrait_uri],
                on_submit=_on_sub)
            return i, text, seg_file, seg_path, note

        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(_job, it) for it in todo]
            for fut in as_completed(futs):
                try:
                    i, text, seg_file, seg_path, note = fut.result()
                except Exception as e:            # 保险：job 内已各自捕获
                    self.log.emit(f"  ⚠️ 某段线程异常：{e}")
                    continue
                self.qc_notes.append(note)

                if not seg_path:
                    self.failed_segs.append((i + 1, "多次重试后仍生成失败"))
                    self.log.emit(f"⚠️ 第 {i+1}/{total} 段失败 → 跳过"
                                  f"（本轮已缺 {len(self.failed_segs)} 段）")
                    continue

                seg_path = self._archive_seg(seg_path, seg_file)
                seg_map[i] = seg_path
                self.progress.emit(len(seg_map), total)
                self._record_seg(state, state_path, i + 1, text, seg_path)

    def _gen_one(self, i, prompt, sec, first_frame, last_frame, tmp_dir, ff,
                 images=None, on_submit=None):
        """生成单段：**生成失败**与**质检不过**各自独立重试，互不占额度。

        返回 (路径 or None, 质检诊断文本)。
        - 生成失败多为瞬时（限流 / 免费队列拥堵 / 超时）→ 退避后重试
          `max_gen_retry` 次；用尽且无兜底则返回 None（调用方跳过该段，不拖垮整条）；
        - 质检不过 → 重生成 `max_qc_retry` 次；用尽保留最后一次并提示人工过目；
        - 两者叠加时（质检不过 → 重生成 → 又生成失败）：**宁可留一个有瑕疵的段，
          也不让它整段消失**，故返回最近一次生成成功的结果。
        on_submit: v4.188 P3 —— 提交成功即回调远端 task_id（登记进 state 对账）。
        """
        last_note = ""
        last_ok = None     # 最近一次生成成功的结果（质检不过也留着兜底）
        gen_try = 0        # 生成失败已重试次数
        qc_try = 0         # 质检不过已重生成次数
        while True:
            if self._cancel:
                return last_ok, last_note
            # images 非空 → 内核走 reference（每段锚定同一张参考图，背景/形象一致）；
            # 此时 first/last_frame 必须为 None —— 三模式互斥，内核优先 images。
            # v4.186.0（P1-8）：传入取消令牌，轮询/下载循环可被实时中断。
            try:
                res = tools_mod.tool_video_gen(
                    self.cfg, self.app_dir, prompt, sec, None,
                    resolution=self.resolution,
                    images=images,
                    first_frame=first_frame, last_frame=last_frame, dialogue=None,
                    cancel_token=self._ct,
                    on_submit=on_submit)
            except Exception as _e:
                # 取消（CancelledError）→ 优雅停：已完成段保留，不报「异常」吓人
                if self._cancel or type(_e).__name__ == "CancelledError":
                    self.log.emit(f"  [第{i+1}段] ⏹ 已停止（用户请求），已完成段保留")
                    return last_ok, last_note
                raise
            if isinstance(res, str):
                gen_try += 1
                if gen_try > self.max_gen_retry:
                    self.log.emit(f"  [第{i+1}段] ❌ 生成失败（已重试 {gen_try-1} 次）：{res}")
                    if last_ok:
                        self.log.emit(f"  [第{i+1}段] ↳ 保留上一次生成结果（质检未过，请人工过目）")
                    return last_ok, last_note
                wait = self.gen_retry_delay * gen_try        # 线性退避
                self.log.emit(f"  [第{i+1}段] ⚠️ 生成失败（{str(res)[:80]}），"
                              f"{wait:.0f}s 后重试（{gen_try}/{self.max_gen_retry}）…")
                if self._sleep(wait):
                    return last_ok, last_note
                continue

            rel, _kind, _name = res
            last_ok = os.path.join(self.app_dir, rel)

            if not self.qc:
                return last_ok, ""

            probe = os.path.join(tmp_dir, f"probe_{i}_{gen_try}_{qc_try}.png")
            if not extract_probe_frame(last_ok, probe, ffmpeg=ff, log=self.log.emit):
                self.log.emit(f"  [第{i+1}段] ⚠️ 抽质检帧失败，放行。")
                return last_ok, ""
            passed, note = vq.review_identity(
                self.cfg, self.portrait, probe, log=self.log.emit)
            last_note = note
            if passed:
                self.log.emit(f"  [第{i+1}段] ✅ 质检通过（仍是本人，无畸变）")
                return last_ok, note
            qc_try += 1
            if qc_try > self.max_qc_retry:
                self.log.emit(f"  [第{i+1}段] ⚠️ 质检仍未通过，保留最后一次结果（请人工过目）："
                              + (note[:120] if note else ""))
                return last_ok, note
            self.log.emit(f"  [第{i+1}段] ⚠️ 质检未通过，重生成（第 {qc_try+1} 次）…")


def _twin_generate(app):
    # 审计修复 B5：生成需数分钟，期间再点「生成」会替换仍在运行的 twin_thread，
    # 旧 QThread 被 GC → 「Destroyed while thread is still running」整进程崩溃。
    _th = getattr(app, "twin_thread", None)
    if _th is not None and _th.isRunning():
        app.twin_status.setText("上一次生成仍在进行，请等待完成。")
        return
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
    ref_lock = _cb("twin_ref_lock", True)
    resume = not _cb("twin_force_redo", False)

    # 断点续跑：按输入指纹定位任务目录，并查上次进度
    # （指纹含 ref_mode —— 换了生成模式，旧段不该被复用）
    fp = _twin_fingerprint(dialogue, app.twin_selected_portrait, scene, res, dur,
                           keep_bg, True, ref_lock)
    products = getattr(tools_mod, "PRODUCTS_DIR", None) or "products"
    job_dir = _twin_job_dir(os.path.join(APP_DIR, products), fp)
    done_n = _job_done_count(
        _load_job_state(os.path.join(job_dir, "state.json")), segs, job_dir) \
        if resume else 0

    total_sec = sum(s for _t, s in segs)
    tip = f"共 {len(segs)} 段 / 约 {total_sec} 秒"
    if len(segs) > 1:
        tip += ("（参考图模式：背景锁定 + 并发出片）" if ref_lock
                else "（首尾帧接力模式：只能串行）")
    if done_n:
        tip += (f"｜♻️ 检测到上次进度：已完成 {done_n} 段，本次跳过"
                f"（只补剩 {len(segs) - done_n} 段）")
    app.twin_status.setText(f"提交任务中…{tip}（可能需数分钟，请勿关闭窗口）")

    app.twin_thread = TwinGenThread(
        app.cfg, APP_DIR, app.twin_selected_portrait, segs, scene, keep_bg, res,
        dual_frame=True, ai_mark=ai_mark, qc=qc,
        resume=resume, job_dir=(job_dir if resume else None),
        ref_mode=ref_lock)
    app.twin_thread.log.connect(lambda m: app.twin_status.setText(m))
    app.twin_thread.progress.connect(
        lambda a, b: app.twin_status.setText(f"已完成 {a}/{b} 段…"))
    app.twin_thread.done.connect(lambda r: _twin_on_result(app, r))
    app.twin_thread.start()
    # v4.186.0（P1-8）：生成中点亮停止按钮
    _sb = getattr(app, "twin_stop_btn", None)
    if _sb is not None:
        _sb.setEnabled(True)


def _twin_stop(app):
    """v4.186.0（P1-8）：停止当前生成任务。

    TwinGenThread.cancel() 此前是死代码（零调用）。现在：
    · _cancel 标志 → 段与段之间生效；
    · CancellationToken → 内核轮询/下载循环实时中断（正在生成的一段几秒内停）。
    """
    _th = getattr(app, "twin_thread", None)
    if _th is None or not _th.isRunning():
        return
    _th.cancel()
    app.twin_status.setText("⏹ 正在停止：已完成段保留（断点续跑可接着补），"
                            "正在生成的一段将在几秒内中断…")
    _sb = getattr(app, "twin_stop_btn", None)
    if _sb is not None:
        _sb.setEnabled(False)


def _twin_on_result(app, res):
    # v4.186.0（P1-8）：任务结束（含被停止）后复位停止按钮
    _sb = getattr(app, "twin_stop_btn", None)
    if _sb is not None:
        _sb.setEnabled(False)
    if isinstance(res, str):
        # 字符串可能是成片绝对路径，也可能是错误文本
        if os.path.isfile(res):
            name = os.path.basename(res)
            rel = os.path.relpath(res, APP_DIR)
            kind = "video"
            app.twin_paths.append(res)
            app.twin_result.addItem(name)
            # 缺段必须显式提示 —— 否则用户以为拿到的是完整口播（v4.178.0）
            _th = getattr(app, "twin_thread", None)
            _miss = getattr(_th, "failed_segs", None) or []
            _reused = getattr(_th, "reused", 0) or 0
            if _miss:
                _names = "、".join(f"第 {n} 段" for n, _ in _miss)
                app.twin_status.setText(
                    f"⚠️ 已生成：{name} —— 但缺少 {len(_miss)} 段（{_names}），"
                    f"口播内容不完整，建议重跑补齐")
            elif _reused:
                app.twin_status.setText(
                    f"已生成：{name}（♻️ 复用了上次 {_reused} 段，未重复生成）")
            else:
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
