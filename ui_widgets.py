"""ui_widgets.py —— 通用 UI 构件层（v4.216.0 从 ui.py 拆出）

内容：小工具函数（_brief_err/_session_preview/图标与头像工厂）、几何钳制
（clamp_dialog_to_screen/clamp_popup_to_screen/resource_path）、基础对话框族
（ThemedDialog/Confirm/Rename/Info/MultiLineInput/SessionManagerDialog）、
_NoWheelCombo、_EdgeResizeFilter、TaskStatusStrip、RES_PRESETS、_NAV_ICONS。

零行为变化：代码逐行搬移；ui.py 顶部 re-export 全部名字，外部
`from ui import X` 与 ui 模块内引用均不受影响。
"""
from PySide6.QtCore import (QAbstractNativeEventFilter, QByteArray, QPoint, QRect, Qt, Signal)
from PySide6.QtGui import (QIcon, QPainter, QPixmap)
from PySide6.QtSvg import (QSvgRenderer)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget)
import ctypes
import os
import re
import sys
import task_status
from config import (APP_DIR)
from datetime import (datetime)
from empty_state import (empty_state)
from pathlib import (Path)
from theme_qss import (btn_dialog, label_body, label_micro, label_second, label_title, scroll_transparent)
from theme_tokens import THEME  # v4.216.0：THEME 唯一真源

_WM_NCHITTEST = 0x0084
_HTCLIENT = 1
_HTCAPTION = 2
_HTLEFT = 10
_HTRIGHT = 11
_HTTOP = 12
_HTTOPLEFT = 13
_HTTOPRIGHT = 14
_HT_BOTTOM = 15
_HT_BOTTOMLEFT = 16
_HT_BOTTOMRIGHT = 17
_EDGE_MARGIN = 6  # 边缘命中热区像素
# v4.114：Feather 风格线性 SVG 图标（24×24 viewBox, stroke 2, round caps）
# 用于侧栏导航，替代 emoji——矢量渲染基线天然齐平，选中态可换色
_NAV_ICONS = {
    "对话":   '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "编排":   '<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="20"/><line x1="21" y1="20" x2="21" y2="13"/><polyline points="21 16 21 13 18 13"/><line x1="4" y1="4" x2="9" y2="4"/><polyline points="11 8 11 5 8 5"/>',
    "军团":   '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "生图":   '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
    "生视频": '<polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>',
    "数字人": '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "导演台": '<rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/>',
    "工具":   '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "任务":   '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    # v4.115：第二轮去 emoji 化补充图标（欢迎页/顶栏钮/交付 tag 复用）
    "编辑":   '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
    "代码":   '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    "设计":   '<path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/>',
    "删除":   '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
    "清单":   '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    "文档":   '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>',
    "附件":   '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
    "刷新":   '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    "语音":   '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>',
    "静音":   '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>',
    "技能":   '<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
    "市场":   '<path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/>',
    "导出":   '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    "设置":   '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
}
def _brief_err(exc, limit=160):
    """把异常压成一行短文本（v4.205.0：专供 toast 的 detail）。

    toast 卡片最宽 420px，detail 是 QLabel —— **不可选中、不可复制**。
    长堆栈放上去既读不完也抄不走，所以超过 limit 一律截断。
    需要完整信息的错误（要复制堆栈给开发者的）照旧走 QMessageBox / 状态条，
    不走 toast。
    """
    s = " ".join(str(exc).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"
def _session_preview(s, maxlen=46):
    """最近对话列表用：取会话里最后一条有文本的消息，压成一行摘要。

    会话列表原来只有「标题 + 时间」。标题是 AI 生成的概括（"文件差异核对"这种），
    隔了两天回来看，一排标题长得几乎一样——**标题只能定位"哪个会话"，
    摘要才能定位"说到哪儿了"**。

    Session.messages 规范是 dict 列表 {"role","content"}，历史版本可能混进带
    .content 的对象，两种都兜一下。没有可展示文本时返回空串，由调用方决定占位。
    """
    msgs = getattr(s, "messages", None) or []
    for m in reversed(msgs):
        c = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        if not isinstance(c, str) or not c.strip():
            continue
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        # 单行预览里 markdown 记号全是噪声：加粗/标题/代码/表格竖线统统去掉，
        # 换行压成空格（否则预览会因为源文本换行而顶出第二行）
        t = " ".join(c.split())
        t = re.sub(r"[*`>#|]", "", t).strip().lstrip("-").strip()
        if len(t) > maxlen:
            t = t[:maxlen - 1] + "…"
        return ("我：" if role == "user" else "") + t
    return ""
def _nav_icon_pixmap(icon_name, color, size=18, device_ratio=2.0):
    """渲染导航 SVG 图标为 QPixmap（描边线性风格，2x 超采样抗锯齿）。"""
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer  # QtSvg 模块（QtGui 里没有）
    body = _NAV_ICONS.get(icon_name)
    if not body:
        return QPixmap()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    px = QPixmap(int(size * device_ratio), int(size * device_ratio))
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRect(0, 0, px.width(), px.height()))
    p.end()
    return px
def _make_avatar_label(size=34):
    """v4.114：头像徽标——深蓝渐变底 + 白色人形线性图标，替代文字圆。"""
    avatar = QLabel()
    avatar.setFixedSize(size, size)
    avatar.setAlignment(Qt.AlignCenter)
    avatar.setStyleSheet(
        f"QLabel{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        f"stop:0:{THEME['tpl_avatar_grad1']},stop:1:{THEME['tpl_avatar_grad2']});border-radius:{size // 2}px;}}")
    fg = _nav_icon_pixmap("数字人", THEME["white"], int(size * 0.52))
    avatar.setPixmap(fg)
    return avatar
def _nav_icon_qicon(icon_name, color, size=16):
    """v4.115：SVG 图标转 QIcon（按钮 setIcon 用），复用 _nav_icon_pixmap 渲染。"""
    return QIcon(_nav_icon_pixmap(icon_name, color, size))
# v4.115：技能 id → _NAV_ICONS 图标名映射（显示层用，emoji 数据字段保留兼容旧技能）
SKILL_ICON_MAP = {
    "xiaohongshu": "设计", "gzh": "文档", "novel": "文档", "shortvideo": "生视频",
    "translate": "对话", "summarize": "清单", "rewrite": "编辑", "weekly": "清单",
    "officecli": "文档", "pycode": "代码", "data": "代码", "batchfile": "附件",
    "cmd": "工具",
}
SKILL_ICON_FALLBACK = "技能"
def _skill_icon_name(sk):
    """技能 → _NAV_ICONS 图标名：先按 id 映射，再按名字精确匹配，最后兜底。"""
    n = str(sk.get("id", "") or sk.get("name", ""))
    if n in SKILL_ICON_MAP:
        return SKILL_ICON_MAP[n]
    name = str(sk.get("name", "") or sk.get("display_name", "") or "")
    if name in _NAV_ICONS:
        return name
    return SKILL_ICON_FALLBACK
class _NoWheelCombo(QComboBox):
    """v4.148.8：滚轮不切档的 QComboBox（防「模型被静默切走」）。

    为什么必须加：QComboBox **默认响应鼠标滚轮** —— 光标滑过就静默切换档位，并触发
    currentTextChanged / currentIndexChanged；而模型下拉的处理器会**一路写进 config.json**。
    实测（2026-09-14）：大哥的 `model` 被切成「免费网关 free-api-gw」
    （`zhipu` + `http://127.0.0.1:8000/v1`）——该档指向**本地网关进程**，服务没起时模型
    直接不可用；而它恰好是档位列表的**最后一项**，往下滚到底就切到它，极易误触、且零提示。

    策略：**未获得焦点时忽略滚轮**（`ignore()` 让事件冒泡，页面继续滚动），
    想用滚轮换档就先点一下让它聚焦。

    v4.149.0 补充（别把希望全押在这一个类上）：滚轮只是**两个触发源之一**。
    真正的洞在**信号选错**——档位下拉原先连的是 `currentTextChanged`，
    它对「滚轮滑过 / 键盘上下 / 打字跳到某字母 / 任何程序化 setCurrentIndex」都会触发，
    而处理器会把 base_url+model+api_key **整套写进 config.json**；再加上启动时
    「匹配不到档位 → 强制切到列表第一项并落盘」的 `for...else` 兜底 ——
    两条路都能在**用户毫无意图**的情况下换掉主模型。故 v4.149.0 同时收口：
    只认 `activated`（用户真选）+ 兜底不再落盘（见 `_on_model_change` / `_init_settings_model_combo`）。
    """

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)
def resource_path(rel):
    """定位资源：兼容 PyInstaller onefile/onedir 与开发时。"""
    rel = rel.replace("/", os.sep)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = os.path.join(meipass, rel)
        if os.path.exists(p):
            return p
    exe_dir = os.path.dirname(sys.executable)
    candidates = [
        os.path.join(exe_dir, rel),
        os.path.join(exe_dir, "_internal", rel),
    ]
    dev_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(dev_dir, rel))
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]
def clamp_dialog_to_screen(dlg, want_w=None, want_h=None, margin=24,
                           center_on_parent=True):
    """v4.180.0：把弹窗钳制进屏幕可用区，**保证底部按钮永远可见可点**。

    病根：主程序里十多个弹窗用硬编码 `resize(w, h)` 打开（如 RoleEditor
    写死 820 高）。笔记本 + 系统缩放（125%/150%）下可用高度可能只有
    600~880px，Qt 从屏幕顶部摆 → 底部按钮沉到屏幕外，鼠标够不着
    （大哥实测「授权弹窗超出屏幕点不到」）。

    本函数做三件事：
      1. 钳制尺寸：宽高都不超过 `可用区 - 2*margin`，并设 maximumHeight
         防止子控件把弹窗重新撑破；
      2. 居中摆放：在**可用区**内居中，而不是让 Qt 从顶部摆；
      3. 只在父窗口明显小于屏幕时，才按父窗口对齐（避免父窗口很小又把
         弹窗挤到屏幕外）。

    注意：只调尺寸与位置，不改任何弹窗自身的布局与业务。
    """
    from PySide6.QtWidgets import QApplication
    scr = None
    try:
        scr = dlg.screen() or QApplication.primaryScreen()
    except Exception:
        try:
            scr = QApplication.primaryScreen()
        except Exception:
            scr = None

    if scr is None:
        # 连屏幕都拿不到（极端 headless）：退化成原样，绝不因此崩。
        if want_w and want_h:
            dlg.resize(want_w, want_h)
        return dlg

    avail = scr.availableGeometry()
    max_w = max(320, avail.width() - 2 * margin)
    max_h = max(240, avail.height() - 2 * margin)

    # 目标尺寸：显式传入优先，否则用当前 sizeHint（Qt 打算开多大）
    tw = want_w if want_w else dlg.sizeHint().width()
    th = want_h if want_h else dlg.sizeHint().height()
    # 尊重弹窗自己声明的最小尺寸，但最小也不许超过可用区
    tw = max(tw, min(dlg.minimumWidth(), max_w))
    th = max(th, min(dlg.minimumHeight(), max_h))
    tw = min(tw, max_w)
    th = min(th, max_h)

    # 关键：设上限，防子控件（长报告 QTextEdit 等）把弹窗重新撑高
    dlg.setMaximumHeight(max_h)
    dlg.setMaximumWidth(max_w)
    dlg.resize(tw, th)

    # 摆位：优先在可用区内居中；若指定按父窗口，则对齐父窗口中心但钳进可用区
    x = avail.x() + (avail.width() - tw) // 2
    y = avail.y() + (avail.height() - th) // 2
    if center_on_parent and dlg.parent() is not None:
        try:
            p = dlg.parent().geometry()
            # 父窗口足够大（占屏 60%+）时按父窗口居中，观感更贴
            if p.height() > avail.height() * 0.6 and p.width() > avail.width() * 0.6:
                x = p.x() + (p.width() - tw) // 2
                y = p.y() + (p.height() - th) // 2
        except Exception:
            pass
    # 最后再钳一次，确保整块都在可用区内（含标题栏预留 32px 余量）
    x = min(max(x, avail.x()), avail.x() + avail.width() - tw)
    y = min(max(y, avail.y()), avail.y() + avail.height() - th)
    dlg.move(x, y)
    return dlg
def clamp_popup_to_screen(popup, pos, margin=16):
    """v4.181.1：把 `Qt.Popup` 内嵌弹层钳进屏幕可用区（QDialog 版本见上方）。

    与 `clamp_dialog_to_screen` 的分工：那个管 QDialog（居中摆放），这个管
    从按钮旁弹出的 Popup —— **必须先尽量贴着触发点，放不下才让位**，
    不能一上来就居中，否则弹层会突然跳到屏幕中间、失去「从按钮弹出」的观感。

    做三件事：
      1. 限高：高度不超过 `可用区 - 2*margin`，并设 maximum 防内容再撑破
         （内容靠 `_popup_base` 那层 QScrollArea 滚动，不会丢）；
      2. 水平：右边越界就整块往左推；
      3. 垂直：优先保持锚点 y；下方放不下就整体上移，仍放不下才贴顶。

    返回实际落点 QPoint。拿不到屏幕（headless）时原样返回，绝不因钳制失败而崩。
    """
    from PySide6.QtWidgets import QApplication
    try:
        scr = popup.screen() or QApplication.primaryScreen()
    except Exception:
        try:
            scr = QApplication.primaryScreen()
        except Exception:
            scr = None
    if scr is None:
        popup.move(pos)
        return pos

    avail = scr.availableGeometry()
    max_h = max(200, avail.height() - 2 * margin)
    max_w = max(200, avail.width() - 2 * margin)
    popup.setMaximumHeight(max_h)
    popup.setMaximumWidth(max_w)
    # adjustSize 只认 sizeHint，不会自动套 maximum —— 这里手动再夹一次
    popup.adjustSize()
    w = min(popup.width(), max_w)
    h = min(popup.height(), max_h)
    popup.resize(w, h)

    x, y = pos.x(), pos.y()
    if x + w > avail.x() + avail.width():
        x = avail.x() + avail.width() - w
    x = max(x, avail.x())
    if y + h > avail.y() + avail.height():
        y = avail.y() + avail.height() - h
    y = max(y, avail.y())
    popup.move(x, y)
    return QPoint(x, y)
class ThemedDialog(QDialog):
    """深色、无边框、置顶、自动居中的模态对话框基类。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setStyleSheet(
            f"QDialog{{background:{THEME['card']};border:1px solid {THEME['border_highlight']};"
            f"border-radius:10px;}}")
        self._build()

    def _build(self):
        pass

    def showEvent(self, e):
        super().showEvent(e)
        # v4.180.0：先按父窗口居中，再整体钳进屏幕可用区 —— 父窗口大于屏幕
        # 或弹窗本身偏高时，旧逻辑会把底部按钮顶到屏幕外。
        if self.parent():
            p = self.parent().geometry()
            self.move(p.x() + (p.width() - self.width()) // 2,
                      p.y() + (p.height() - self.height()) // 2)
        try:
            clamp_dialog_to_screen(self, center_on_parent=False)
        except Exception:
            pass

    @staticmethod
    def _btn_style(bg, fg):
        # v4.182.0 收编：真源在 theme_qss.btn_dialog()（方法内惰性导入防循环依赖）
        from theme_qss import btn_dialog
        return btn_dialog(bg, fg)
class ConfirmDialog(ThemedDialog):
    def _build(self):
        from theme_qss import label_second, label_title
        self._result = False
        self._trusted = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)
        self._title = QLabel("")
        self._title.setStyleSheet(label_title())
        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(f"color:{THEME['dim']};font-size:13px;line-height:1.6;")
        lay.addWidget(self._title)
        lay.addWidget(self._detail)
        self._trust = QCheckBox("本次会话信任（不再逐个确认）")
        self._trust.setStyleSheet(label_second())
        lay.addWidget(self._trust)
        row = QHBoxLayout()
        row.addStretch(1)
        no = QPushButton("取消")
        no.setFixedHeight(34)
        no.setStyleSheet(self._btn_style(THEME["elev"], THEME["dim"]))
        no.clicked.connect(self.reject)
        yes = QPushButton("允许")
        yes.setFixedHeight(34)
        yes.setStyleSheet(self._btn_style(THEME["accent"], THEME["white"]))
        yes.clicked.connect(self._on_yes)
        row.addWidget(no)
        row.addWidget(yes)
        lay.addLayout(row)
        self.adjustSize()

    def set_text(self, title, detail):
        self._title.setText(title)
        self._detail.setText(detail)

    def _on_yes(self):
        self._result = True
        self._trusted = self._trust.isChecked()
        self.accept()

    def result(self):
        return self._result

    def trusted(self):
        return self._trusted
class RenameDialog(ThemedDialog):
    def _build(self):
        from theme_qss import label_title
        self._text = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)
        t = QLabel("重命名对话")
        t.setStyleSheet(label_title())
        lay.addWidget(t)
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("例如：小说《死亡倒计时》大纲")
        self._edit.setStyleSheet(
            f"QLineEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:8px;padding:8px 12px;font-size:13px;color:{THEME['text']};}}"
            f"QLineEdit:focus{{border:1px solid {THEME['accent']};}}")
        lay.addWidget(self._edit)
        row = QHBoxLayout()
        row.addStretch(1)
        no = QPushButton("取消")
        no.setFixedHeight(34)
        no.setStyleSheet(self._btn_style(THEME["elev"], THEME["dim"]))
        no.clicked.connect(self.reject)
        yes = QPushButton("保存")
        yes.setFixedHeight(34)
        yes.setStyleSheet(self._btn_style(THEME["accent"], THEME["white"]))
        yes.clicked.connect(self._on_ok)
        row.addWidget(no)
        row.addWidget(yes)
        lay.addLayout(row)
        self._edit.returnPressed.connect(self._on_ok)
        self.setMinimumWidth(360)
        self.adjustSize()

    def _on_ok(self):
        self._text = self._edit.text().strip()
        self.accept()

    def text_value(self):
        return self._text
class InfoDialog(ThemedDialog):
    def _build(self):
        from theme_qss import label_title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)
        self._title = QLabel("")
        self._title.setStyleSheet(label_title())
        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(f"color:{THEME['dim']};font-size:13px;line-height:1.6;")
        lay.addWidget(self._title)
        lay.addWidget(self._detail)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("好的")
        ok.setFixedHeight(34)
        ok.setStyleSheet(self._btn_style(THEME["accent"], THEME["white"]))
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)
        self.adjustSize()

    def set_text(self, title, detail):
        self._title.setText(title)
        self._detail.setText(detail)
class MultiLineInput(QTextEdit):
    """多行输入框：Enter / Ctrl+Enter 发送，Shift+Enter 换行；随内容自动增高；支持粘贴图片。"""
    sendRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(38)
        self.textChanged.connect(self.adjust_height)
        self.image_files = []

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if e.modifiers() & Qt.ShiftModifier:
                # Shift+Enter: 换行
                super().keyPressEvent(e)
            else:
                # Enter: 发送
                self.sendRequested.emit()
                e.accept()
        else:
            super().keyPressEvent(e)

    def adjust_height(self):
        new_h = int(self.document().size().height()) + 16
        self.setFixedHeight(max(38, min(new_h, 160)))

    def insertFromMimeData(self, source):
        if source.hasImage():
            image = source.imageData()
            if image and not image.isNull():
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                tmp_dir = Path(APP_DIR) / "temp" / "images"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                img_path = tmp_dir / f"paste_{ts}.png"
                image.save(str(img_path))
                self.image_files.append(str(img_path))
                # Show placeholder text so user knows image was captured
                cursor = self.textCursor()
                cursor.insertText("[\u56fe\u7247\u5df2\u7c98\u8d34 " + str(len(self.image_files)) + "]")
                self.textChanged.emit()
        else:
            super().insertFromMimeData(source)

    def get_images(self):
        return self.image_files

    def clear_images(self):
        self.image_files = []
class SessionManagerDialog(QDialog):
    """v4.79：会话管理——置顶 / 分组 / 批量删除 / 筛选。"""

    def __init__(self, parent):
        from theme_qss import scroll_transparent
        from theme_qss import label_body
        super().__init__(parent)
        self.parent = parent
        self.store = parent.store
        self.setWindowTitle("会话管理")
        self.setMinimumSize(540, 560)
        self.setStyleSheet(f"QDialog{{background:{THEME['bg']};}}")

        self._row_sids = []
        self._checks = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        # ---- 搜索 + 分组筛选 ----
        top = QHBoxLayout()
        top.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索会话标题…")
        self.search.setFixedHeight(34)
        self.search.setStyleSheet(
            f"QLineEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:8px;padding:0 12px;font-size:13px;color:{THEME['text']};}}"
            f"QLineEdit:focus{{border:1px solid {THEME['accent']};}}")
        self.search.textChanged.connect(self._refresh)
        top.addWidget(self.search, 1)

        self.folder_filter = _NoWheelCombo()
        self.folder_filter.setFixedHeight(34)
        self.folder_filter.setMinimumWidth(140)
        self.folder_filter.setStyleSheet(
            f"QComboBox{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:8px;padding:0 8px;font-size:13px;color:{THEME['text']};}}"
            f"QComboBox:focus{{border:1px solid {THEME['accent']};}}")
        self.folder_filter.currentTextChanged.connect(self._refresh)
        top.addWidget(self.folder_filter)
        lay.addLayout(top)

        # ---- 列表 ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(scroll_transparent())
        self.list_widget = QWidget()
        self.list_lay = QVBoxLayout(self.list_widget)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(8)
        self.scroll.setWidget(self.list_widget)
        lay.addWidget(self.scroll, 1)

        # ---- 底部：全选 / 批量删除 / 完成 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.sel_all = QCheckBox("全选")
        self.sel_all.setStyleSheet(f"QCheckBox{{{label_body()}}}")
        self.sel_all.stateChanged.connect(self._toggle_all)
        bottom.addWidget(self.sel_all)
        bottom.addStretch(1)
        bulk = QPushButton("批量删除")
        bulk.setFixedHeight(34)
        bulk.setStyleSheet(
            f"QPushButton{{background:{THEME['card']};color:{THEME['danger']};"
            f"border:1px solid {THEME['border']};border-radius:8px;padding:0 12px;font-size:13px;}}"
            f"QPushButton:hover{{border-color:{THEME['danger']};background:{THEME['danger_hover_red']};}}")
        bulk.clicked.connect(self._bulk_delete)
        bottom.addWidget(bulk)
        done = QPushButton("完成")
        done.setDefault(True)
        done.setFixedHeight(34)
        done.setStyleSheet(
            f"QPushButton{{background:{THEME['accent']};color:white;border:none;"
            f"border-radius:8px;padding:0 16px;font-size:13px;font-weight:500;}}"
            f"QPushButton:hover{{background:{THEME['accent_hover']};}}")
        done.clicked.connect(self.accept)
        bottom.addWidget(done)
        lay.addLayout(bottom)

        self._refresh(initial=True)

    # ------------------------------------------------------------------
    def _refresh(self, initial=False, preserve_folder=True):
        if initial:
            cur = self.folder_filter.currentText()
            self.folder_filter.blockSignals(True)
            self.folder_filter.clear()
            self.folder_filter.addItem("全部会话")
            for f in self.store.list_folders():
                self.folder_filter.addItem(f)
            # 还原选择
            idx = self.folder_filter.findText(cur) if preserve_folder and cur else -1
            if idx >= 0:
                self.folder_filter.setCurrentIndex(idx)
            self.folder_filter.blockSignals(False)

        # 清旧行
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._row_sids = []
        self._checks = []

        folder_sel = self.folder_filter.currentText()
        folder_key = "" if folder_sel in ("全部会话", "") else folder_sel
        items = self.store.all_sorted(query=self.search.text(), folder=folder_key)

        if not items:
            # v4.207.0：接empty_state 的 compact 形态（行内单行，给窄侧栏）。
            # 同时修一个文案说谎的问题：all_sorted 返回空有两种原因 ——
            # 「搜的词没匹配上」和「一个会话都没有」，原来两种共用同一句
            # 「没有匹配的会话」，新用户第一次打开（搜索框是空的）会懵。
            # 所以按**有没有搜索词**分两句：空搜索词 = 还没开始；有关键词 = 没搜到。
            # 注意：这个对话框里**没有新建按钮**（只有搜索框 + 分组筛选），
            # 所以空态不能指引"点上方新建"—— 会指向不存在的控件。
            q = (self.search.text() or "").strip()
            if q:
                self.list_lay.addWidget(empty_state(
                    f"没有匹配「{q}」的会话", compact=True, icon="对话"))
            else:
                self.list_lay.addWidget(empty_state(
                    "还没有会话", hint="在主界面发起对话后会出现在这里",
                    compact=True, icon="对话"))
            return

        for s in items:
            self._build_row(s)

    def _build_row(self, s):
        row = QWidget()
        row.setStyleSheet(
            f"background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:10px;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 8, 8, 8)
        rl.setSpacing(8)

        chk = QCheckBox()
        # v4.210.3：18→32 命中区。实测（fusion，subElementRect）无论控件多大，
        # 指标恒为 14×14、只在控件内垂直居中 —— 故放大只增点击面积，
        # 勾选框本身的尺寸与视觉位置不变。18px 同时破了 UI_QA §8 的 24px 硬底线。
        chk.setFixedSize(32, 32)
        rl.addWidget(chk)
        self._checks.append(chk)

        pin = QPushButton("⚑" if s.pinned else "☆")
        # v4.210.3：30→32 命中区（UI_QA §8；透明底 + 16px 字形 → 观感不变）。
        pin.setFixedSize(32, 32)
        pin.setToolTip("置顶" if not s.pinned else "取消置顶")
        pin.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;font-size:16px;"
            f"color:{THEME['pinned_color'] if s.pinned else THEME['placeholder']};border-radius:6px;}}"
            f"QPushButton:hover{{background:{THEME['sidebar_hover']};}}")
        pin.clicked.connect(lambda _, sid=s.sid: self._pin_toggle(sid))
        rl.addWidget(pin)

        title = QPushButton(s.title or "新会话")
        title.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;text-align:left;"
            f"font-size:13px;color:{THEME['text']};padding:0 4px;}}"
            f"QPushButton:hover{{color:{THEME['accent']};}}")
        title.setToolTip("点击打开此会话")
        title.clicked.connect(lambda _, sid=s.sid: self._switch(sid))
        rl.addWidget(title, 1)

        fcombo = _NoWheelCombo()
        fcombo.setFixedHeight(28)
        fcombo.setMinimumWidth(96)
        fcombo.setEditable(True)
        fcombo.setStyleSheet(
            f"QComboBox{{background:{THEME['bg']};border:1px solid {THEME['border']};"
            f"border-radius:6px;padding:0 8px;font-size:12px;color:{THEME['text']};}}")
        fcombo.addItem("未分组")
        for f in self.store.list_folders():
            if f != s.folder:
                fcombo.addItem(f)
        fcombo.setCurrentText(s.folder or "未分组")
        fcombo.currentTextChanged.connect(lambda txt, sid=s.sid: self._folder_changed(sid, txt))
        rl.addWidget(fcombo)

        delb = QPushButton("×")
        # v4.210.3：26→32 命中区（UI_QA §8）。
        delb.setFixedSize(32, 32)
        delb.setStyleSheet(
            f"QPushButton{{background:transparent;color:{THEME['placeholder']};"
            f"border:none;font-size:16px;font-weight:600;border-radius:6px;}}"
            f"QPushButton:hover{{color:{THEME['danger']};background:{THEME['sidebar_hover']};}}")
        delb.setToolTip("删除会话")
        delb.clicked.connect(lambda _, sid=s.sid, t=s.title: self._del_one(sid, t))
        rl.addWidget(delb)

        self._row_sids.append(s.sid)
        self.list_lay.addWidget(row)

    def _pin_toggle(self, sid):
        s = self.store.sessions.get(sid)
        if not s:
            return
        self.store.set_pinned(sid, not s.pinned)
        self._refresh()

    def _folder_changed(self, sid, text):
        self.store.set_folder(sid, text)
        # 分组筛选状态下改名可能使该行消失，刷新即可
        self._refresh()

    def _switch(self, sid):
        self.parent._switch_session(sid)
        self.parent._refresh_session_combo()
        self.parent._refresh_recent_on_welcome()
        self.accept()

    def _del_one(self, sid, title):
        if QMessageBox.question(
                self, "删除会话",
                f"确定删除会话「{title or '新会话'}」？此操作不可撤销。",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.store.remove(sid)
        self.parent._refresh_session_combo()
        self.parent._refresh_recent_on_welcome()
        self._refresh(initial=True)

    def _toggle_all(self, state):
        for c in self._checks:
            c.setChecked(bool(state))

    def _bulk_delete(self):
        sids = [sid for sid, c in zip(self._row_sids, self._checks) if c.isChecked()]
        if not sids:
            QMessageBox.information(self, "提示", "请先勾选要删除的会话。")
            return
        if QMessageBox.question(
                self, "批量删除",
                f"确定删除选中的 {len(sids)} 个会话？此操作不可撤销。",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        n = self.store.remove_many(sids)
        self.parent._refresh_session_combo()
        self.parent._refresh_recent_on_welcome()
        self.sel_all.setChecked(False)
        self._refresh(initial=True)
class _EdgeResizeFilter(QAbstractNativeEventFilter):
    """用 QAbstractNativeEventFilter 给无边框窗口加边缘缩放（避免 nativeEvent 签名冲突）。

    PySide6 冻结模式下 QMainWindow.nativeEvent 虚方法签名与 C++ 不一致
    （expected 2, got 3），覆写必然崩溃。改用 installNativeEventFilter + Filter
    子类，其 nativeEventFilter 固定只收 (eventType, message) 两个参数，无此问题。
    """

    def __init__(self, window):
        super().__init__()
        self._win = window

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            try:
                msg = ctypes.cast(message, ctypes.POINTER(ctypes.wintypes.MSG)).contents
                if msg.message == _WM_NCHITTEST and not self._win.isMaximized():
                    # v4.108 H-11：lParam 的屏幕坐标是【物理像素】，而 win.x()/width()
                    # 是【逻辑坐标】——高分屏（125%/150% 缩放）直接相减导致热区偏移、
                    # 边缘缩放失灵。先按 devicePixelRatio 把物理坐标归一为逻辑坐标。
                    dpr = float(self._win.devicePixelRatioF() or 1.0)
                    # v4.125 M-12：lParam 低/高 16 位是有符号 short——副屏在主屏
                    # 左侧时 x 为负（如 -100 → 0xFF9C），不做符号扩展会被当成
                    # 65436 的巨大正数，8 个边缘判定全不命中（H-11 残留）。
                    _x16 = msg.lParam & 0xFFFF
                    _y16 = (msg.lParam >> 16) & 0xFFFF
                    x = ((_x16 - 65536 if _x16 >= 32768 else _x16)) / dpr
                    y = ((_y16 - 65536 if _y16 >= 32768 else _y16)) / dpr
                    wx = x - self._win.x()
                    wy = y - self._win.y()
                    w = self._win.width()
                    h = self._win.height()
                    m = _EDGE_MARGIN
                    if wx <= m and wy <= m:
                        return True, ctypes.wintypes.LPARAM(_HTTOPLEFT)
                    if wx >= w - m and wy <= m:
                        return True, ctypes.wintypes.LPARAM(_HTTOPRIGHT)
                    if wx <= m and wy >= h - m:
                        return True, ctypes.wintypes.LPARAM(_HT_BOTTOMLEFT)
                    if wx >= w - m and wy >= h - m:
                        return True, ctypes.wintypes.LPARAM(_HT_BOTTOMRIGHT)
                    if wx <= m:
                        return True, ctypes.wintypes.LPARAM(_HTLEFT)
                    if wx >= w - m:
                        return True, ctypes.wintypes.LPARAM(_HTRIGHT)
                    if wy <= m:
                        return True, ctypes.wintypes.LPARAM(_HTTOP)
                    if wy >= h - m:
                        return True, ctypes.wintypes.LPARAM(_HT_BOTTOM)
            except Exception:
                pass
        return False, None
# 分辨率预设（v4.188 P2-7 归一：生视频页 / 数字人面板 / 导演台面板共用此唯一来源。
# 此前同一张表在 ui.py 内联 + digital_twin_panel.py + director_panel.py 各存一份，
# 三处漂移就会出现「这页有的选项那页没有」的静默不一致——风格表 v4.151 踩过同款坑）。
# ⚠️ 有效性边界（2026-08-17 实测 + tools.tool_video_gen 现状）：
#   · Agnes 视频内核（2.5-flash）固定 size="720P"，**生成分辨率不随此选择变**；
#   · 此选择实际生效的位置：数字人面板的参考图居中裁剪画幅（fit_image_to_aspect）、
#     导演台的合成画布（ffmpeg 层 width/height）。
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
class TaskStatusStrip(QWidget):
    """状态栏内的任务状态条（可观测性）。

    把「模型调用 / 网页抓取 / 文件解析 / 视频生成」等任务统一显示成
    已接收 / 处理中 / 完成 / 失败原因；对标记为可重试的失败给出「重试」入口。
    此前这些信息只落在日志里，用户感知是「没反应」。

    跨线程安全：任务状态可能从 HTTP 线程（浏览器桥接回调）或工作线程变更，
    所以订阅回调只负责 emit 信号，由 Qt 的 queued 连接投递到 GUI 线程渲染，
    绝不在非 GUI 线程直接碰控件。
    """

    _changed = Signal(object)   # 任意线程 emit → GUI 线程 _render

    def __init__(self, parent=None):
        from theme_qss import label_second
        super().__init__(parent)
        self._latest_retryable = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.icon = QLabel("")
        self.icon.setStyleSheet(f"color:{THEME['accent']};font-size:{THEME['font_micro']};")
        lay.addWidget(self.icon)

        self.text = QLabel("")
        self.text.setStyleSheet(label_second())
        lay.addWidget(self.text)

        self.retry_btn = QPushButton("重试")
        # v4.210.3：18→32 命中区（UI_QA §8 硬底线 24px）。文字/字号不变，只抬高。
        self.retry_btn.setFixedHeight(32)
        self.retry_btn.setCursor(Qt.PointingHandCursor)
        self.retry_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;padding:0 4px;"
            f"color:{THEME['accent']};font-size:{THEME['font_micro']};}}"
            f"QPushButton:hover{{color:{THEME['accent_hover']};}}")
        self.retry_btn.clicked.connect(self._on_retry)
        self.retry_btn.hide()
        lay.addWidget(self.retry_btn)

        self.clear_btn = QPushButton("清除")
        # v4.210.3：18→32 命中区（同 retry_btn，UI_QA §8）。
        self.clear_btn.setFixedHeight(32)
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;padding:0 4px;"
            f"color:{THEME['faint']};font-size:{THEME['font_micro']};}}"
            f"QPushButton:hover{{color:{THEME['dim']};}}")
        self.clear_btn.clicked.connect(self._on_clear)
        self.clear_btn.hide()
        lay.addWidget(self.clear_btn)

        self._changed.connect(self._render)
        try:
            task_status.subscribe(self._on_change)
            # 控件销毁时退订：否则状态总线会一直持有已销毁控件的回调引用
            self.destroyed.connect(self._on_destroyed)
        except Exception:
            pass

    def _on_destroyed(self, *_):
        try:
            task_status.unsubscribe(self._on_change)
        except Exception:
            pass

    # ---- 状态变更入口（可能来自任意线程）----
    def _on_change(self, snap):
        try:
            self._changed.emit(snap)
        except Exception:
            pass

    def refresh(self):
        """立即按当前状态渲染一次（初始化用，不经过信号）。"""
        try:
            self._render(task_status.snapshot())
        except Exception:
            pass

    # ---- 渲染（GUI 线程）----
    def _render(self, snap):
        from theme_qss import label_micro, label_second
        try:
            active = (snap or {}).get("active") or []
            recent = (snap or {}).get("recent") or []

            if active:
                t = active[0]
                more = f" +{len(active) - 1}" if len(active) > 1 else ""
                detail = f" · {t.get('detail')}" if t.get("detail") else ""
                self.icon.setText("●")
                self.icon.setStyleSheet(f"color:{THEME['accent']};font-size:{THEME['font_micro']};")
                self.text.setStyleSheet(label_second())
                self.text.setText(
                    f"{t.get('kind_label', '任务')} · {t.get('state_label', '')}{more}{detail}")
                self.retry_btn.hide()
                self.clear_btn.hide()
                self._latest_retryable = None
                return

            failed = [r for r in recent if r.get("state") == task_status.STATE_FAILED]
            if failed:
                t = failed[0]
                msg = f"{t.get('kind_label', '任务')} 失败"
                if t.get("detail"):
                    msg += f"：{t['detail']}"
                self.icon.setText("⚠")
                self.icon.setStyleSheet(f"color:{THEME['danger']};font-size:{THEME['font_micro']};")
                self.text.setStyleSheet(f"color:{THEME['danger']};font-size:{THEME['font_micro']};")
                self.text.setText(msg)
                self._latest_retryable = t if t.get("retryable") else None
                self.retry_btn.setVisible(bool(self._latest_retryable))
                self.clear_btn.show()
                return

            if recent:
                t = recent[0]
                self.icon.setText("✓")
                self.icon.setStyleSheet(f"color:{THEME['ok']};font-size:{THEME['font_micro']};")
                self.text.setStyleSheet(label_micro())
                self.text.setText(f"{t.get('kind_label', '任务')} {t.get('state_label', '完成')}")
                self._latest_retryable = None
                self.retry_btn.hide()
                self.clear_btn.show()
                return

            # 无任何任务：留白，不占视觉
            self.icon.setText("")
            self.text.setText("")
            self.retry_btn.hide()
            self.clear_btn.hide()
            self._latest_retryable = None
        except Exception:
            pass

    # ---- 交互 ----
    def _on_retry(self):
        t = self._latest_retryable
        if not t:
            return
        try:
            ok = task_status.retry(t.get("id", ""))
        except Exception:
            ok = False
        if not ok:
            # 诚实反馈：没有注册重试钩子时不假装已重试
            self.text.setText("该任务无法自动重试，请手动重发")

    def _on_clear(self):
        try:
            task_status.clear_finished()
        except Exception:
            pass
