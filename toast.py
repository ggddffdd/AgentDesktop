# -*- coding: utf-8 -*-
"""toast.py —— 轻量非模态提示（v4.204.0 新增）

设计见 DESIGN.md §11。补的是现有反馈体系缺的中间那档：
QMessageBox 太重（33 处全模态）、托盘提示在窗口外、TaskStatusStrip 只管长任务。

三条硬约束
----------
1. **线程安全**：后台线程（HTTP 回调、工作线程）完成任务后最需要弹提示，
   直接碰控件会崩。所以模块级 `toast()` 只 emit 信号，由 GUI 线程渲染 ——
   与 `ui.TaskStatusStrip` 同一套模式。
2. **未注册 host 时降级写日志，不抛异常**：panel 是子控件拿不到主窗口引用，
   离屏测试环境下也不该因为没注册就崩。
3. **样式全走 token**：颜色/圆角/字号/间距一律来自 theme_qss，本文件不写 hex。

用法
----
    from toast import toast, register_toast_host

    # 主窗口 __init__ 里注册一次（parent=承载浮层的控件）
    register_toast_host(self.chat_col, avoid=self.input_area)

    # 任意线程
    toast("已保存到云端", kind="success")
    toast("导出失败：磁盘空间不足", kind="error", detail=str(e))
"""
import logging
import time
import weakref

from PySide6.QtCore import QEvent, Qt, QObject, QPoint, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import ui_motion as motion   # 系统「减弱动效」偏好（v4.210.2）

_log = logging.getLogger(__name__)

# ---- 行为常量（DESIGN.md §11.2 / §11.4 / §11.5）----
# 0 = 不自动消失。error 给关闭按钮 —— 失败信息通常要读完甚至复制，
# 2 秒就消失等于没提示。
DEFAULT_MS = {
    "success": 2500,
    "info": 2500,
    "warn": 4000,
    "error": 0,
}
MAX_VISIBLE = 3           # 最多同时 3 条
MERGE_WINDOW = 2.0        # 秒：同 kind+文案在此窗口内合并
MIN_W, MAX_W = 280, 420   # 卡片宽度区间
FADE_IN_MS, FADE_OUT_MS = 120, 160
STACK_GAP = 8
EDGE_MARGIN = 24          # 距 host 右/下边缘
AVOID_GAP = 12            # 距被避让控件上边缘


def _theme():
    """惰性取 theme_qss（它 from ui import THEME，顶层 import 会循环依赖）。"""
    import theme_qss
    return theme_qss


class _ToastCard(QFrame):
    """单条提示卡片。左竖条 + 图标点 + 主文案（+ 可选副标题）+ 关闭按钮。"""

    def __init__(self, kind, msg, detail, duration_ms, on_close):
        tq = _theme()
        super().__init__()
        self._kind = kind
        self._duration_ms = duration_ms
        self._on_close = on_close
        self._hover = False
        self._closing = False

        self.setStyleSheet(tq.toast_card())
        # 宽度自适应内容（再由 layer 统一夹到 [280,420]），高度按内容
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧语义竖条（3px）。外面套一层 V 布局给上下留白，免得顶到圆角。
        bar_wrap = QWidget()
        bar_lay = QVBoxLayout(bar_wrap)
        bar_lay.setContentsMargins(0, 6, 0, 6)
        bar = QWidget()
        bar.setFixedWidth(3)
        bar.setStyleSheet(tq.toast_bar(kind))
        bar_lay.addWidget(bar)
        root.addWidget(bar_wrap)

        # 内容区（内边距 12px 16px —— 走间距 token）
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(tq.S["lg"], tq.S["md"], tq.S["lg"], tq.S["md"])
        bl.setSpacing(tq.S["sm"])

        dot = QLabel("●")
        dot.setStyleSheet(tq.toast_dot(kind))
        bl.addWidget(dot, 0, Qt.AlignTop)

        texts = QWidget()
        tl = QVBoxLayout(texts)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        main = QLabel(msg)
        main.setWordWrap(True)
        main.setMaximumWidth(MAX_W - 24 - 3 - 12 - 24)
        main.setStyleSheet(tq.label_body())
        tl.addWidget(main)
        if detail:
            sub = QLabel(str(detail))
            sub.setWordWrap(True)
            sub.setMaximumWidth(MAX_W - 24 - 3 - 12 - 24)
            sub.setStyleSheet(tq.label_second())
            tl.addWidget(sub)
        bl.addWidget(texts, 1)

        self._close_btn = QPushButton("✕")
        # v4.210.3：16→32。关按钮是「✕」方图标，16px 命中区连鼠标都难点；
        # 底透明、字号 token 不变，故放大只增点击面积、不改观感（UI_QA §8）。
        self._close_btn.setFixedSize(32, 32)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setStyleSheet(tq.toast_close_btn())
        self._close_btn.clicked.connect(self.dismiss)
        # error 常显（必须能自己关掉），其余只在悬停时出现
        self._close_btn.setVisible(kind == "error")
        bl.addWidget(self._close_btn, 0, Qt.AlignTop)
        root.addWidget(body, 1)

        # 淡入淡出：项目无 QPropertyAnimation（0 处），用 QTimer 分步改 opacity。
        # v4.210.2：时长先过 ui_motion.scale_ms —— 系统关了动画就归零、瞬间到位。
        self._eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._eff)
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._anim_tick)
        self._anim = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        if duration_ms > 0:
            self._timer.start(duration_ms)
        self._fade(0.0, 1.0, FADE_IN_MS)

    # ---- 动效 ----
    def _fade(self, a, b, ms, done=None):
        """淡入/淡出。系统要求「减弱动效」时 ms 归零 → 直接落到终值，不逐帧渐变。

        归零分支**必须异步回调**（QTimer.singleShot(0)）而不能同步调 done：
        dismiss() 是从计时器 timeout 或关闭按钮 clicked 里进来的，
        同步回调会变成「在自己的信号处理里把卡片 setParent(None)+deleteLater()」，
        调用顺序与渐变动画那条路径（回调发生在计时器 tick 里）不一致。
        0ms 只是「下一轮事件循环」，观感同样是瞬间，但时序与原路径完全同构。
        """
        ms = motion.scale_ms(ms)
        if ms <= 0:
            self._anim_timer.stop()
            self._anim = None
            self._eff.setOpacity(b)
            if done:
                QTimer.singleShot(0, done)
            return
        steps = max(1, ms // 20)
        self._anim = {"i": 0, "n": steps, "a": a, "b": b, "done": done}
        self._eff.setOpacity(a)
        self._anim_timer.start(20)

    def _anim_tick(self):
        an = self._anim
        if not an:
            self._anim_timer.stop()
            return
        an["i"] += 1
        i, n = an["i"], an["n"]
        self._eff.setOpacity(an["a"] + (an["b"] - an["a"]) * i / n)
        if i >= n:
            self._anim_timer.stop()
            self._anim = None
            done = an["done"]
            if done:
                done()

    # ---- 生命周期 ----
    def dismiss(self):
        """关闭（淡出后回调移除）。重复调用安全。"""
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self._fade(self._eff.opacity(), 0.0, FADE_OUT_MS, self._finish)

    def _finish(self):
        cb = self._on_close
        self._on_close = None
        if cb:
            cb(self)

    def restart_timer(self):
        """合并命中时重置计时（同内容 2s 内不新增，只把已有的重置）。"""
        if self._duration_ms > 0 and not self._closing:
            self._timer.start(self._duration_ms)

    # ---- 悬停暂停 ----
    def enterEvent(self, e):
        super().enterEvent(e)
        self._hover = True
        if self._closing:
            return
        # 移出时是「重新计时」不是续算，所以这里不需要记剩余时间
        self._timer.stop()
        self._close_btn.setVisible(True)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hover = False
        if self._closing:
            return
        if self._duration_ms > 0:
            self._timer.start(self._duration_ms)
        if self._kind != "error":
            self._close_btn.setVisible(False)


class _ToastLayer(QWidget):
    """承载卡片的透明浮层，挂在 host 上、手动定位（不进 host 的布局）。

    尺寸始终等于卡片堆叠的实际矩形 —— 浮层空白区域越小，吃掉的点击越少。
    """

    def __init__(self, host, avoid=None):
        super().__init__(host)
        self._avoid = weakref.ref(avoid) if avoid is not None else None
        self._cards = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(STACK_GAP)
        self.setWindowFlags(Qt.Widget)
        self.hide()
        # 宿主尺寸变化不会自动传到子 widget，得自己监听 —— 否则窗口一改大小，
        # toast 就停在旧位置（右下角会飘到中间）。
        host.installEventFilter(self)

    def eventFilter(self, obj, e):
        if obj is self.parent() and e.type() == QEvent.Resize:
            self._reposition()
        return super().eventFilter(obj, e)

    def add(self, card):
        self._cards.append(card)
        self.layout().addWidget(card)
        # 第 4 条到来：丢弃最旧（不排队 —— 排队会让提示延迟到不相关时才出现）
        while len(self._cards) > MAX_VISIBLE:
            self._drop(self._cards[0])
        self._relayout()

    def _drop(self, card):
        if card in self._cards:
            self._cards.remove(card)
        self.layout().removeWidget(card)
        card.setParent(None)
        card.deleteLater()

    def remove(self, card):
        self._drop(card)
        self._relayout()

    def _relayout(self):
        if not self._cards:
            self.hide()
            self.resize(0, 0)
            return
        tq = _theme()
        # 统一宽度：取所有卡片自适应的最大值，再夹到 [280, 420]
        try:
            w = max(c.sizeHint().width() for c in self._cards)
        except Exception:
            w = MIN_W
        w = max(MIN_W, min(MAX_W, w))
        h = 0
        for c in self._cards:
            c.setFixedWidth(w)          # 先定宽，高度才是换行后的真实高度
            h += max(c.sizeHint().height(), 1)
        h += STACK_GAP * (len(self._cards) - 1)
        self.setFixedSize(w, h)
        self.show()
        self.raise_()
        self._reposition()

    def _reposition(self):
        host = self.parent()
        if host is None:
            return
        r = host.rect()
        x = max(0, r.width() - self.width() - EDGE_MARGIN)
        y = r.height() - self.height() - EDGE_MARGIN
        av = self._avoid() if self._avoid else None
        if av is not None and av.isVisible():
            # 底部是聊天输入区：浮在它上边缘之上，避免遮住输入框光标
            top = av.mapTo(host, QPoint(0, 0)).y()
            y = min(y, top - AVOID_GAP - self.height())
        self.move(max(0, x), max(EDGE_MARGIN // 2, y))

    def resizeEvent(self, e):
        # 只重定位、不重排：_relayout 里会 setFixedSize，再进 resizeEvent 会递归
        super().resizeEvent(e)
        self._reposition()

    def __len__(self):
        return len(self._cards)

    def cards(self):
        return list(self._cards)


class _ToastManager(QObject):
    """全局单例：持 host 的 weakref，把任意线程的请求排队到 GUI 线程。"""

    _requested = Signal(object)   # 任意线程 emit → GUI 线程 _on_requested

    def __init__(self):
        super().__init__()
        # 显式 Queued：即使从 GUI 线程调用也走队列，行为一致
        self._requested.connect(self._on_requested, Qt.QueuedConnection)
        self._layer = None
        self._host_ref = None      # weakref：主窗口关闭后 layer 随之销毁，
        self._recent = {}          # 直接访问 _layer 会摸到已删的 C++ 对象

    # ---- 注册 ----
    def register(self, host, avoid=None):
        if host is None:
            return None
        old = self.alive_layer()
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        self._host_ref = weakref.ref(host)
        self._layer = _ToastLayer(host, avoid=avoid)
        self._recent.clear()
        return self._layer

    def unregister(self):
        layer = self.alive_layer()
        if layer is not None:
            host = layer.parent()
            if host is not None:
                host.removeEventFilter(layer)
            layer.setParent(None)
            layer.deleteLater()
        self._layer = None
        self._host_ref = None
        self._recent.clear()

    def alive_layer(self):
        """host 还在才返回图层 —— 窗口已关时返回 None，走降级分支不崩。"""
        if self._layer is None or self._host_ref is None:
            return None
        return self._layer if self._host_ref() is not None else None

    # ---- 请求（GUI 线程）----
    def _on_requested(self, payload):
        # 兜底：本方法跑在 Qt 事件处理里，异常不会传到调用方
        # （只会变成 stderr 上的裸 traceback，甚至让事件循环失控）。
        # 「提示弹不出来」这种事绝不该把应用拖垮 —— 一律降级写日志。
        try:
            self._render(payload)
        except Exception:                    # noqa: BLE001
            _log.exception("[toast] 渲染失败，已降级：%s", payload.get("msg"))

    def _render(self, payload):
        kind = payload["kind"]
        msg = payload["msg"]
        detail = payload.get("detail")
        duration = payload.get("duration_ms")
        layer = self.alive_layer()
        if layer is None:
            # 未注册 / 窗口已关 / 离屏环境 → 降级写日志，不抛异常
            _log.info("[toast:%s] %s%s", kind, msg, f" | {detail}" if detail else "")
            return
            # 未注册（panel 子控件 / 离屏环境）→ 降级写日志，不抛异常
            _log.info("[toast:%s] %s%s", kind, msg, f" | {detail}" if detail else "")
            return
        key = (kind, msg, detail)
        now = time.monotonic()
        prev = self._recent.get(key)
        if prev is not None:
            card, ts = prev
            if now - ts <= MERGE_WINDOW and card in layer.cards():
                card.restart_timer()          # 2s 内同内容：只重置计时
                self._recent[key] = (card, now)
                return
        duration_ms = DEFAULT_MS[kind] if duration is None else int(duration)
        card = _ToastCard(kind, msg, detail, duration_ms,
                          on_close=lambda c: self._forget(layer, key, c))
        self._recent[key] = (card, now)
        layer.add(card)

    def _forget(self, layer, key, card):
        if self._recent.get(key, (None,))[0] is card:
            self._recent.pop(key, None)
        layer.remove(card)


_MGR = _ToastManager()


# ============================================================
# 公开 API
# ============================================================
def register_toast_host(host, avoid=None):
    """注册承载浮层的控件（主窗口里调一次）。

    host  承载浮层的容器（右下角定位的参照物）。
    avoid 需要避开的控件（如聊天输入区）—— 浮层会停在它上边缘之上，
          避免遮住输入框光标。传 None 则只按距底 24px 定位。
    """
    return _MGR.register(host, avoid=avoid)


def unregister_toast_host():
    _MGR.unregister()


def toast(msg, kind="info", detail=None, duration_ms=None):
    """弹一条提示。任意线程可调用（内部 emit → GUI 线程渲染）。

    kind: success / info / warn / error
    detail: 可选副标题（如失败原因）
    duration_ms: 覆盖默认时长；0 表示不自动消失
    """
    if kind not in DEFAULT_MS:
        raise ValueError(f"未知 kind: {kind!r}（可选 {sorted(DEFAULT_MS)}）")
    _MGR._requested.emit({
        "kind": kind, "msg": str(msg),
        "detail": None if detail is None else str(detail),
        "duration_ms": duration_ms,
    })


def visible_count():
    """当前界面上的条数（测试与自检用）。"""
    layer = _MGR.alive_layer()
    return len(layer) if layer is not None else 0
