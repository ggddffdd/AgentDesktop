# -*- coding: utf-8 -*-
"""v4.79：首次启动新手引导向导。

设计要点：
- 纯 PySide6，无额外依赖；主题色由 main.py 传入的 THEME 字典决定（避免 import ui 造成循环依赖）。
- 5 步：欢迎 → 怎么聊 → 记忆与加密 → 技能市场 → 快捷键&完成。
- 退出（「开始使用」「跳过」/关窗）按「不再显示此引导」勾选框落盘：勾选才永久不再弹
  （审计修复 G2：原为无条件 onboarded=True，勾选项形同虚设）。
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QCheckBox, QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

import config

# (大标题, 正文) —— 正文用 \n 换行
STEPS = [
    ("欢迎，我是小臭玩AI",
     "你的本地 AI 助手：聊天、写稿、生图生视频、跑技能，一个窗口全搞定。\n"
     "下面用 30 秒带你过一遍核心功能。"),
    ("怎么聊",
     "在底部输入框直接打字发送；可以把文档 / 图片拖进去当附件。\n"
     "回复是流式实时出现的——边想边看，不干等。"),
    ("记忆与加密",
     "它有个「第二大脑」（长期记忆），会记住你说过的重要事，越聊越懂你。\n"
     "设置里可以给记忆加密、设密码，隐私更稳。"),
    ("技能市场",
     "标题栏的市场按钮、或设置里的「技能市场」，能发现并一键安装新能力（免费）。\n"
     "想要什么功能，先去市场逛逛。"),
    ("快捷键 & 完成",
     "随时按 Ctrl+Alt+X 把窗口唤出来 / 收起来，不用再去点托盘。\n"
     "下面的选项勾上，以后就不再弹这个引导啦。"),
]


class OnboardingWizard(QDialog):
    def __init__(self, cfg, theme, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme = theme or {}
        self.idx = 0
        self.setWindowTitle("新手引导")
        self.setFixedSize(480, 400)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._build_ui()
        self._show_step(0)

    def _c(self, key, fallback):
        return self.theme.get(key, fallback)

    def _build_ui(self):
        bg = self.theme["bg"]
        card = self.theme["card"]
        border = self.theme["border"]
        text = self.theme["text"]
        dim = self.theme["dim"]
        accent = self.theme["accent"]
        accent_hover = self.theme["accent_hover"]

        self.setStyleSheet(f"QDialog{{background:{bg};}}")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 20)
        root.setSpacing(12)

        # 标题
        self.title_lbl = QLabel()
        self.title_lbl.setStyleSheet(f"font-size:{self._c('font_title_xl', '20px')};font-weight:600;color:{text};")
        self.title_lbl.setWordWrap(True)
        root.addWidget(self.title_lbl)

        # 正文
        self.body_lbl = QLabel()
        self.body_lbl.setStyleSheet(
            f"font-size:{self._c('font_body', '13px')};color:{dim};line-height:1.6;")
        self.body_lbl.setWordWrap(True)
        root.addWidget(self.body_lbl)

        root.addStretch(1)

        # 完成页的勾选项
        self.no_more = QCheckBox("不再显示此引导")
        self.no_more.setStyleSheet(f"QCheckBox{{color:{text};font-size:13px;}}")
        self.no_more.setVisible(False)
        root.addWidget(self.no_more)

        # 步骤指示点
        self.dots = QHBoxLayout()
        self.dots.setSpacing(8)
        self.dot_widgets = []
        for _ in STEPS:
            d = QLabel("●")
            d.setStyleSheet(f"color:{border};font-size:{self._c('font_micro', '11px')};")
            self.dots.addWidget(d)
            self.dot_widgets.append(d)
        root.addLayout(self.dots)

        # 底部按钮
        nav = QHBoxLayout()
        nav.setSpacing(12)
        self.skip_btn = QPushButton("跳过")
        self.skip_btn.setFixedHeight(36)
        self.skip_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{dim};border:none;"
            f"font-size:13px;padding:0 8px;}}"
            f"QPushButton:hover{{color:{text};}}")
        self.skip_btn.clicked.connect(self._finish)
        nav.addWidget(self.skip_btn)

        nav.addStretch(1)

        self.back_btn = QPushButton("上一步")
        self.back_btn.setFixedHeight(36)
        self.back_btn.setStyleSheet(
            f"QPushButton{{background:{card};color:{text};border:1px solid {border};"
            f"border-radius:8px;padding:0 16px;font-size:13px;}}"
            f"QPushButton:hover{{border-color:{accent};}}")
        self.back_btn.clicked.connect(self._back)
        nav.addWidget(self.back_btn)

        self.next_btn = QPushButton("下一步")
        self.next_btn.setFixedHeight(36)
        self.next_btn.setDefault(True)
        self.next_btn.setStyleSheet(
            f"QPushButton{{background:{accent};color:white;border:none;"
            f"border-radius:8px;padding:0 20px;font-size:13px;font-weight:500;}}"
            f"QPushButton:hover{{background:{accent_hover};}}")
        self.next_btn.clicked.connect(self._next)
        nav.addWidget(self.next_btn)

        root.addLayout(nav)

    def _show_step(self, i):
        self.idx = max(0, min(i, len(STEPS) - 1))
        title, body = STEPS[self.idx]
        self.title_lbl.setText(title)
        self.body_lbl.setText(body)
        # 指示点高亮
        for k, d in enumerate(self.dot_widgets):
            d.setStyleSheet(
                f"color:{'%s' % (self.theme['accent'] if k == self.idx else self.theme['border'])};"
                f"font-size:{self._c('font_micro', '11px')};")
        # 按钮态
        self.back_btn.setVisible(self.idx > 0)
        self.no_more.setVisible(self.idx == len(STEPS) - 1)
        self.next_btn.setText("开始使用" if self.idx == len(STEPS) - 1 else "下一步")

    def _next(self):
        if self.idx >= len(STEPS) - 1:
            self._finish()
        else:
            self._show_step(self.idx + 1)

    def _back(self):
        self._show_step(self.idx - 1)

    def _finish(self):
        # 审计修复 G2：「不再显示此引导」勾选框原是装饰件——_finish 无条件写
        # onboarded=True，用户不勾也永远不再弹。现在按其意愿落盘：勾选=不再弹，
        # 未勾=下次启动仍显示引导。
        try:
            self.cfg["onboarded"] = bool(self.no_more.isChecked())
            config.save_config(self.cfg)
        except Exception as e:
            # 写回失败不应阻塞（下次仍会弹，可接受）
            print("onboarding save failed:", e)
        # closeEvent 已调过一次时防止 QDialog.close() 回调重入导致 accept/reject 双弹
        if not getattr(self, "_finishing", False):
            self._finishing = True
            self.accept()

    def closeEvent(self, event):
        # 用户点 X 关闭也视为看过，避免反复弹
        self._finish()
        event.accept()
