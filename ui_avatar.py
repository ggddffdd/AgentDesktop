# -*- coding: utf-8 -*-
"""ui_avatar.py —— 用户头像（v4.299.0：支持自定义）

头像来源优先级：
  1. USER_DATA_DIR/avatar.png —— 用户自定义图片（点击头像更换，存这里）；
  2. 默认灰色人物占位 SVG —— 没有自定义时的兜底。

两处展示共用：对话页顶栏右侧头像 + 侧栏底部用户区头像。
本模块是叶子模块（只依赖标准库 + PySide6 + config），不 import ui，
被 ui_widgets._make_avatar_label 与 ui.py 的头像点击接线使用。
"""
import os

from PySide6.QtCore import Qt, QByteArray, Signal
from PySide6.QtGui import QPixmap, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel

from theme_tokens import THEME

# 默认占位：浅灰圆底 + 灰色人物剪影（与全局浅色风一致，不再用蓝渐变人形）。
# 颜色一律走 THEME token（border 浅底 / gray2 人物），不裸写 hex（hex 护栏）。
_DEFAULT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    f'<rect width="40" height="40" rx="20" fill="{THEME["border"]}"/>'
    f'<circle cx="20" cy="15" r="6.5" fill="{THEME["gray2"]}"/>'
    f'<path d="M7 35c0-7.5 5.8-12 13-12s13 4.5 13 12z" fill="{THEME["gray2"]}"/></svg>'
)

# 自定义头像落盘尺寸（统一缩放到不超过该边长，避免大图占内存/占盘）。
_SAVE_MAX = 256


def avatar_file():
    """自定义头像的落盘路径（数据目录下，不进入安装目录/源码树）。"""
    import config
    return os.path.join(config.USER_DATA_DIR, "avatar.png")


def has_custom_avatar():
    try:
        return os.path.isfile(avatar_file())
    except Exception:
        return False


def _circle_clip(pix, size):
    """把任意图片缩放到 size 并裁剪成圆形（透明背景），居中显示。"""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    p.setClipPath(path)
    scaled = pix.scaled(size, size, Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation)
    x = (size - scaled.width()) // 2
    y = (size - scaled.height()) // 2
    p.drawPixmap(x, y, scaled)
    p.end()
    return out


def default_avatar_pixmap(size):
    """默认灰色人物占位（SVG 渲染，矢量清晰）。"""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(_DEFAULT_SVG.encode("utf-8")))
    p = QPainter(pix)
    r.render(p)
    p.end()
    return pix


def avatar_pixmap(size):
    """当前应显示的头像：自定义图片（圆形裁剪）优先，否则默认占位。"""
    if has_custom_avatar():
        pix = QPixmap(avatar_file())
        if not pix.isNull():
            return _circle_clip(pix, size)
    return default_avatar_pixmap(size)


def set_custom_avatar(src_path):
    """把用户选中的图片保存为自定义头像。返回 (ok, message)。"""
    pix = QPixmap(src_path)
    if pix.isNull():
        return False, "图片无法读取，换一张试试"
    pix = pix.scaled(_SAVE_MAX, _SAVE_MAX, Qt.KeepAspectRatio,
                     Qt.SmoothTransformation)
    dst = avatar_file()
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if pix.save(dst, "PNG"):
            return True, dst
        return False, "头像保存失败"
    except Exception as e:
        return False, "头像保存失败：%s" % e


def clear_custom_avatar():
    """删除自定义头像，恢复默认占位。返回是否有自定义被清掉。"""
    try:
        if os.path.isfile(avatar_file()):
            os.remove(avatar_file())
            return True
    except Exception:
        pass
    return False


class AvatarLabel(QLabel):
    """可点击的头像标签：左键点击发射 clicked，供「更换头像」接线。"""

    clicked = Signal()

    def __init__(self, size=34, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击更换头像")
        self.refresh()

    def refresh(self):
        self.setPixmap(avatar_pixmap(self._size))
        self.setStyleSheet("background:transparent;border:none;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
