# -*- coding: utf-8 -*-
"""empty_state.py —— 空状态占位组件（v4.206.0，DESIGN.md §12）

为什么需要：项目里的空状态散在 7 处、4 种形态（清点见 DESIGN §12.1）——
有的只是一行居中灰字（"还没有任务"），有的是徽章+主副文案+按钮
（会话空态，v4.112 做的）。用户看到的是同一个产品里两种说法。

三条硬约束（与 toast 同款口径）
--------------------------------
1. **样式全走 theme_qss**：本文件不写 hex、不写字号、不写圆角。
2. **不依赖主窗口**：清单里 #6 #7 在独立面板窗口（automation / legion）里，
   组件必须自洽 —— 不去拿 app / store / 全局状态。
3. **视觉零变化优先**：会话空态那段代码（v4.112）**已经是对的**，
   本组件是把它**抽出来复用**，不是重新设计 —— 默认参数与那段代码逐项等价。
   这条是本组件能不能落地的关键：如果它需要"新设计"才能成立，就说明
   抽错了抽象，应该回去把会话空态改对，而不是让组件迁就现状。

用法
----
    from empty_state import empty_state

    # 完整形态：徽章 + 主文案 + 副文案 + 行动按钮
    empty_state("还没有对话", hint="点击下方开始你的第一条对话",
                action="新建对话", on_action=self._new_session, icon="对话")

    # 只要主副文案（无按钮、无图标）
    empty_state("暂无待审核技能", hint="模型自动创建的技能会出现在这里")

    # compact 形态（v4.207.0）：行内单行，给窄容器用
    empty_state("没有匹配的会话", compact=True)

设计取舍
--------
- **v4.206.0 不实现 compact**：§12.2 里写过 compact=True（列表内单行），
  但当时的三个调用点全是"面板正中大空态"。**先不做，等真有第二个
  调用点再加** —— 现在写就是死代码（YAGNI），而死代码会被误当成"已覆盖"。
  （v4.207.0 补上：#3 会话搜索无结果就是那个调用点，见下。）
- **图标走现有 SVG 表**：`_NAV_ICONS` 已有 23 个（对话/任务/技能/清单…），
  三个调用点正好各有一个对应的，不新增图形资产。
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)

# ---- 形态常量（会话空态 v4.112 原值，抽到这里只此一份）----
BADGE_SIZE = 44      # 圆形徽章直径
ICON_SIZE = 20       # 徽章里的线性图标边长
ACTION_W, ACTION_H = 116, 32   # 行动按钮尺寸（116 是文案长度定的）
MARGIN_V = (0, 8, 0, 8)        # 容器上下留白
SPACING = 8                    # 徽章/主/副/按钮之间的间距

# ---- compact 形态常量（v4.207.0，为 #3 会话搜索无结果而加）----
# 为什么图标 18 而不是复用徽章里的 20：compact 走的是**无底色圆**的画法，
# 视觉重量比"浅蓝圆底里的实心图标"轻，尺寸跟着降 2px 才不显得抢。
COMPACT_ICON_SIZE = 18
COMPACT_H = 32         # 整行高，与原 compact 位点的 32px 一致（视觉零变化）
COMPACT_MARGIN_V = (8, 8, 8, 8)   # 上下留白，比完整形态的 (0,8,0,8) 更紧
COMPACT_SPACING = 6    # 图标与文案间距


def empty_state(title, hint=None, action=None, on_action=None, icon=None,
                icon_color=None, compact=False):
    """构造一个空状态占位块。

    title   主文案（必填，一句话）
    hint    副文案（可空）：解释"为什么空 + 下一步能做什么"
    action  行动按钮文案（可空）：给了才渲染按钮
    on_action 按钮回调（可空，但**给了 action 就该给**，否则按钮点了没反应）
    icon    现有 SVG 图标名（`_NAV_ICONS` 的键，如 "对话"）；空则不画徽章
    icon_color  图标颜色键（默认 accent）—— 徽章底色恒为 card_blue_bg
    compact True 走行内单行形态（v4.207.0）：无徽章底色圆、横向排布、
             文字左对齐不居中。给窄容器（侧栏列表、卡片内行）用。

    返回 QWidget（background:transparent），直接塞进任何布局。
    """
    if compact:
        return _compact_state(title, hint, icon, icon_color)
    return _full_state(title, hint, action, on_action, icon, icon_color)


def _compact_state(title, hint, icon, icon_color):
    """compact 形态：**行内单行**，给窄容器用（v4.207.0，为 #3 而生）。

    与完整形态的四处差别，都是被容器逼出来的，不是为了好看：

    1. **不画徽章底色圆**。完整形态的 44px 圆底在 200px 宽的侧栏里
       会吃掉 1/4 宽度，把文案挤成两三行。
    2. **横排而非竖排**。竖排在单行场景下会把容器高度顶到 3 倍。
    3. **左对齐而非居中**。行内元素居中会看起来像"浮在中间"，
       而列表区的空态应该跟正文一样贴左。
    4. **不渲染行动按钮**。行内 32px 高塞不下 32px 高的按钮；
       硬塞会把这一行顶变形。需要引导动作的场景请用完整形态。
    """
    from theme_qss import empty_hint_compact, empty_title_compact

    card = QWidget()
    card.setStyleSheet("background:transparent;")
    card.setFixedHeight(COMPACT_H)
    lay = QHBoxLayout(card)
    lay.setContentsMargins(*COMPACT_MARGIN_V)
    lay.setSpacing(COMPACT_SPACING)
    lay.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    if icon:
        try:
            import ui
            color = ui.THEME[icon_color or "placeholder"]
            pm = ui._nav_icon_pixmap(icon, color, COMPACT_ICON_SIZE)
            if pm.isNull():
                raise ValueError(f"图标名不在 _NAV_ICONS 里: {icon!r}")
            ico = QLabel()
            ico.setFixedSize(COMPACT_ICON_SIZE, COMPACT_ICON_SIZE)
            ico.setAlignment(Qt.AlignCenter)
            ico.setPixmap(pm)
            ico.setStyleSheet("background:transparent;")
            lay.addWidget(ico, 0, Qt.AlignVCenter)
        except Exception:                    # noqa: BLE001
            log.warning("[empty_state] compact 图标渲染失败，降级为纯文字：%s", icon,
                        exc_info=True)

    main = QLabel(title)
    main.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    main.setStyleSheet(empty_title_compact())
    lay.addWidget(main, 1)

    if hint:
        sub = QLabel(hint)
        sub.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # v4.209.2：原先这里只写了 background:transparent —— 既没有 color 也没有
        # 字号，Qt 回退到系统默认（实测是纯黑 9pt），而同排主文案是 dim 色 12px，
        # 副文案反而更抢眼，层级倒置。改为走 token：compact 副文案取 12px / faint，
        # 与主文案（12px / dim）同级字号、**靠明度**分层级，两形态同值。
        # （注释里刻意不写颜色十六进制、也不写字号属性名：
        #   206/207 的 B1/B2 判据会扫全文件，把注释里的字面量当成代码里还在用裸值，
        #   见 L190。）
        sub.setStyleSheet(empty_hint_compact())
        lay.addWidget(sub, 0)

    return card


def _full_state(title, hint, action, on_action, icon, icon_color):
    """完整形态：徽章 + 主文案 + 副文案 + 行动按钮，竖排居中。"""
    from theme_qss import empty_action_btn, empty_badge, empty_hint, empty_title

    card = QWidget()
    card.setStyleSheet("background:transparent;")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(*MARGIN_V)
    lay.setSpacing(SPACING)
    lay.setAlignment(Qt.AlignHCenter)

    if icon:
        # 延迟 import ui：ui.py 会在模块级 import 本文件，顶层 import ui 会循环
        try:
            import ui
            color = ui.THEME[icon_color or "accent"]
            pm = ui._nav_icon_pixmap(icon, color, ICON_SIZE)
            # 图标名拼错时 _nav_icon_pixmap 返回**空 QPixmap 而不抛异常** ——
            # 直接 setPixmap 会留下一个 44×44 的浅色空圆点，看起来像"图标加载失败"。
            # 所以必须显式判空：空就退化成纯文字。
            if pm.isNull():
                raise ValueError(f"图标名不在 _NAV_ICONS 里: {icon!r}")
            badge = QLabel()
            badge.setFixedSize(BADGE_SIZE, BADGE_SIZE)
            badge.setAlignment(Qt.AlignCenter)
            badge.setPixmap(pm)
            badge.setStyleSheet(empty_badge())
            lay.addWidget(badge, 0, Qt.AlignHCenter)
        except Exception:                    # noqa: BLE001
            # 图标画不出来不该让整块空态消失 —— 降级成"只有文字"
            log.warning("[empty_state] 图标渲染失败，降级为纯文字：%s", icon,
                        exc_info=True)

    main = QLabel(title)
    main.setAlignment(Qt.AlignCenter)
    main.setWordWrap(True)
    main.setStyleSheet(empty_title())
    lay.addWidget(main)

    if hint:
        sub = QLabel(hint)
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        sub.setStyleSheet(empty_hint())
        lay.addWidget(sub)

    if action:
        btn = QPushButton(action)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(ACTION_W, ACTION_H)
        btn.setStyleSheet(empty_action_btn())
        if on_action is not None:
            btn.clicked.connect(on_action)
        else:
            # 给了按钮却不给回调 = 点了没反应，比没有按钮更糟
            log.warning("[empty_state] action=%r 没有 on_action，按钮将是死的", action)
            btn.setEnabled(False)
        lay.addWidget(btn, 0, Qt.AlignHCenter)

    return card
