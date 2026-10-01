# -*- coding: utf-8 -*-
"""v4.135.0 军团对话页 —— 原生 QTextEdit 时间线 + 输入框（不内嵌网页）。

设计目标（把过去散在 5 处的决策收敛进一条流）：
- 把「160px 日志框 / 660px 授权弹窗 / 联系 PM 输入框 / 波次编排滚动区 /
  重编计划二次弹窗」收敛进**一条对话时间线 + 一个输入框**。
- PM 主动汇报（每波进展 + 下一步 + 提醒）、建议通过/打回及理由、大哥的决策、
  成果，全部进同一条流，在对话框里说话做决策。
- 渲染走原生 QTextEdit（复用 director_chat 范式），不内嵌网页（避免再拉 QWebEngine）。
- 与引擎零耦合：只 import legion（数据层，只读 LEGION_DIR）拿存档目录；
  不 import legion_worker，所有信号由 LegionWindow 转发进来。

轻量意图解析（parse_intent）：纯规则，不调 LLM。用于在「授权待决」上下文下，
把大哥的一句话（「放行 / 打回 / 终止」）路由成 pass/reject/abort；
非授权上下文则一律当「给 PM 的普通留言」。
"""

import os
import re
import json
import logging
from ui import THEME
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                               QPushButton)

import legion  # 仅取数据层目录 LEGION_DIR；不触碰引擎，保证改 UI 不破壳

log = logging.getLogger(__name__)


# 角色 → 语义色（独立于主题 token；与 director_chat 的灰底范式一致）
# 注意：这里只存「色值」，渲染时的卡片样式由 _render 统一拼。
_ROLE_COLOR = {
    "你":       THEME["role_green"],   # 大哥（绿）
    "项目经理":  THEME["role_pm_blue"],   # PM（蓝）
    "系统":      THEME["role_sys_gray"],   # 系统/日志（灰）
    "授权":      THEME["warn_gold"],   # 授权待决（琥珀）
    "成果":      THEME["role_purple"],   # 成果（紫）
    "波次":      THEME["role_wave_gray"],   # 波次分隔条（深灰）
}
_ROLE_NAMES = set(_ROLE_COLOR.keys())   # 历史回放时的合法 role 校验集

# 系统日志里出现这些 token → 升级为「警示」色（琥珀/红），让问题一眼可见
_WARN_TOKENS = ("⚠️", "🔴", "☠", "❌", "失败", "拦截", "缺口", "异常", "ERROR")

# 波次启动标记：worker 在每波真正开跑时固定打印 `── 第 N 波启动`，
# 重跑时打印 `── 第 N 波 · 重跑第 K 次（…）`。靠这两个固定串精确识别波边界，
# 把后续消息归到「第 N 波」分组，解决「分不清是哪一波」。
_WAVE_RE = re.compile(r"──\s*第\s*(\d+)\s*波\s*启动")
_WAVE_RERUN_RE = re.compile(r"──\s*第\s*(\d+)\s*波\s*·\s*重跑")

_MAX_HISTORY = 400   # 内存里最多留 400 条
_MAX_REPLAY = 40     # 换项目回填显示最近 40 条
_SAVE_EVERY = 8      # 每 8 次落盘一次（避免在每条日志上狂写磁盘）


def _esc(s):
    """HTML 转义，防止日志/产出里的 < > & 破坏富文本渲染。"""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---- 轻量意图解析（纯规则，不调 LLM）----
_ABORT_KW = ("终止", "中止", "停下", "停", "别跑了", "别干了", "算了", "放弃",
             "杀掉", "abort", "stop", "取消执行", "别跑")
_REJECT_KW = ("打回", "驳回", "退回", "重做", "重来", "拒绝", "不行", "reject")
_PASS_KW = ("放行", "通过", "同意", "批准", "可以", "好的", "ok", "OK", "yes",
            "YES", "pass", "approve", "放行下一波", "通过下一波")


def parse_intent(text):
    """把一句话分成授权意图：'abort' / 'reject' / 'pass' / 'message'。

    规则优先级：abort > reject > pass（abort 最危险，误判成本最高，先查）。
    非授权语境下调用方一律按 'message' 处理（路由给 PM）。
    纯函数，可单测。
    """
    if not text or not text.strip():
        return "message"
    low = text.strip().lower()
    for kw in _ABORT_KW:
        if kw.lower() in low:
            return "abort"
    for kw in _REJECT_KW:
        if kw.lower() in low:
            return "reject"
    for kw in _PASS_KW:
        if kw.lower() in low:
            return "pass"
    return "message"


class LegionChatPanel(QWidget):
    """军团对话页：上方时间线 + 下方输入框（Enter 发送 / Shift+Enter 换行）。"""

    def __init__(self, mw=None, parent=None):
        super().__init__(parent)
        self.mw = mw
        self._proj_id = None
        self.history = []
        self._cur_wave = None           # 当前波次分组（None=尚未进波 / 非波内消息）
        self._last_wave_rendered = None # 最近一次渲染所归属的波（用于回放时补分隔条）
        self._sys_buffer = []           # 待合并的普通系统日志（降密缓存）
        self._pending_auth = None       # 授权待决上下文：{title, detail}
        self._send_pm_cb = None         # LegionWindow 注入：把留言投进 worker
        self._auth_cb = None            # LegionWindow 注入：把授权结果送回 worker
        self._auth_dialog_cb = None     # LegionWindow 注入：打开兜底模态弹窗
        self._save_counter = 0
        self._build_ui()
        self._load_history()

    # ---------- UI ----------
    def _build_ui(self):
        from ui import THEME
        # 缓存主题色，供卡片渲染统一取用（回放早于构建时也有兜底默认）
        self._THEME_BORDER = THEME.get("border", "#ddd")
        self._THEME_TEXT = THEME.get("text", "#222")
        self._THEME_FAINT = THEME.get("faint", "#999")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(8)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText(
            "军团进展、项目经理汇报、你的决策、成果都会在这里实时出现。\n"
            "授权待决时，在下面一句话写「放行 / 打回 / 终止」即可。")
        self.log.setStyleSheet(
            f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:10px;padding:12px 12px;font-size:13px;color:{THEME['text']};}}")
        lay.addWidget(self.log, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QTextEdit()
        self.input.setFixedHeight(52)
        self.input.setPlaceholderText(
            "说句话做决策，或给项目经理留言 · Enter 发送 / Shift+Enter 换行")
        self.input.setStyleSheet(
            f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:10px;padding:8px 12px;font-size:13px;color:{THEME['text']};}}")
        self.input.installEventFilter(self)
        row.addWidget(self.input, 1)

        self.clear_btn = QPushButton("清空")
        self.auth_btn = QPushButton("授权弹窗")
        self.stop_btn = QPushButton("⛔ 停止")
        self.send_btn = QPushButton("发送")
        self.skill_btn = QPushButton("🔧 装技能（GitHub）")
        self.skill_btn.setToolTip("技能库缺方法论时，从 GitHub 找 SKILL.md 装上。\n"
                                  "装完会问挂给谁 —— 挂上后下一波 / 打回重跑立即生效。\n"
                                  "（运行中也能装，不必切回编排页）")
        self.auth_btn.setEnabled(False)   # 仅授权待决时可点（兜底入口）
        self.auth_btn.setToolTip("用按钮完成这次授权（与在对话框说话二选一）；"
                                 "宪法第二章保留按钮兜底。")
        # v4.137：停止入口也要有两处（编排页 + 对话页）。波内执行时最想停，
        # 而那时编排页不一定在你眼前；过去只能等 human 模式的授权弹窗。
        self.stop_btn.setVisible(False)   # 仅军团执行时由 LegionWindow 显形
        self.stop_btn.setToolTip(
            "向军团发出停止指令：跑完当前这一步就收尾，产出保留、可续跑。")
        for b in (self.skill_btn, self.clear_btn, self.auth_btn, self.stop_btn,
                  self.send_btn):
            b.setFixedHeight(52)
            b.setCursor(Qt.PointingHandCursor)
        self.send_btn.setStyleSheet(
            f"QPushButton{{background:{THEME['accent']};color:white;border:none;"
            f"border-radius:10px;padding:0 16px;font-size:13px;font-weight:600;}}")
        self.clear_btn.setStyleSheet(
            f"QPushButton{{background:{THEME['card']};color:{THEME['faint']};"
            f"border:1px solid {THEME['border']};border-radius:10px;padding:0 12px;"
            f"font-size:13px;}}")
        self.skill_btn.setStyleSheet(
            f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
            f"border:1px solid {THEME['border']};border-radius:10px;padding:0 12px;"
            f"font-size:13px;}}")
        self.auth_btn.setStyleSheet(
            f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
            f"border:1px solid {THEME['border']};border-radius:10px;padding:0 12px;"
            f"font-size:13px;}}"
            f"QPushButton:disabled{{color:{THEME['faint']};}}")
        self.stop_btn.setStyleSheet(
            f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
            f"border:1px solid {THEME['border']};border-radius:10px;padding:0 12px;"
            f"font-size:13px;}}"
            f"QPushButton:disabled{{color:{THEME['faint']};}}")
        self.clear_btn.clicked.connect(self._clear_chat)
        self.send_btn.clicked.connect(self.send)
        self.auth_btn.clicked.connect(self._on_auth_dialog_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.skill_btn.clicked.connect(self._on_skill_install_clicked)
        row.addWidget(self.skill_btn)
        row.addWidget(self.clear_btn)
        row.addWidget(self.auth_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.send_btn)
        lay.addLayout(row)

    def eventFilter(self, obj, ev):
        # Enter 发送更符合直觉，Shift+Enter 才换行（同 director_chat 范式）。
        if obj is self.input and ev.type() == ev.Type.KeyPress:
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if ev.modifiers() & Qt.ShiftModifier:
                    return False
                self.send()
                return True
        return super().eventFilter(obj, ev)

    # ---------- 回调注入（由 LegionWindow 设，避免双向耦合）----------
    def set_pm_sender(self, cb):
        """cb(text, urgent) —— 把留言投进 worker.send_message。"""
        self._send_pm_cb = cb

    def set_auth_resolver(self, cb):
        """cb(intent, text) —— 把授权结果送回 worker（intent∈pass/reject/abort）。"""
        self._auth_cb = cb

    def set_auth_dialog_opener(self, cb):
        """cb() —— 打开兜底模态授权弹窗。"""
        self._auth_dialog_cb = cb

    def set_stopper(self, cb):
        """cb() —— v4.137 新增：请求停止当前军团（由 LegionWindow 实现）。"""
        self._stop_cb = cb

    def set_skill_helper(self, cb):
        """cb(kind, arg) —— v4.139 P2：技能指令处理器（由 LegionWindow 实现）。

        kind ∈ {"list"（查缺口）, "search"（去 GitHub 找）, "install"（装）}，
        arg 是缺口名/序号。让「缺技能 → GitHub 找 → 装」全程在对话里说完。
        """
        self._skill_cb = cb

    def set_skill_installer(self, cb):
        """cb() —— v4.144：对话页「🔧 装技能（GitHub）」按钮回调（由 LegionWindow 实现）。

        复用编排页同一套 _install_skill：装完选角色挂上、下一波/重跑立即生效，
        运行中也能补，不必切回编排页。
        """
        self._skill_install_cb = cb

    def set_launcher(self, cb):
        """cb(task_text) —— v4.148.1：聊天框直接「启动 <任务>」开团（UI 化繁为简）。"""
        self._launch_cb = cb

    def _on_skill_install_clicked(self):
        """🔧 装技能（GitHub）—— 真实实现在 LegionWindow._install_skill。"""
        cb = getattr(self, "_skill_install_cb", None)
        if not callable(cb):
            self._record("系统", "装技能入口未接线（军团窗口还没准备好）。")
            return
        cb()

    def _handle_skill_intent(self, kind, arg):
        cb = getattr(self, "_skill_cb", None)
        if not callable(cb):
            self._record("系统", "技能入口未接线（军团窗口还没准备好）。")
            return
        try:
            cb(kind, arg)
        except Exception as e:
            self._record("系统", "技能指令执行失败：%s" % e)

    def _on_stop_clicked(self):
        """⛔ 停止（对话页入口）—— 真实实现在 LegionWindow._on_stop_clicked。"""
        cb = getattr(self, "_stop_cb", None)
        if cb is None:
            self.say("系统", "停止入口未接线（军团窗口还没准备好）。")
            return
        cb()

    # ---------- 输入路由 ----------
    def send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        # v4.137：非授权态下的「停止」也必须生效。此前只有授权待决才解析意图，
        # 平时在对话页写「停止」会被原样当成给 PM 的留言 —— 而波内执行阶段
        # 恰恰没有任何按钮能停它，于是「说停不停」。
        # 误判兜底：LegionWindow 那侧还有一道二次确认，认错就点「否」。
        if self._is_stop_command(text):
            self._record("你", text)
            cb = getattr(self, "_stop_cb", None)
            if callable(cb):
                cb()
            else:
                self._record("系统", "停止入口未接线（军团窗口还没准备好）。")
            return
        if self._pending_auth is not None:
            intent = parse_intent(text)
            if intent in ("pass", "reject", "abort"):
                self._record("你", text)
                self._resolve_auth(intent, text)
                return
            # 非决策句：当作给 PM 的随附意见，授权上下文保持挂起
            self._record("你", text)
            self._route_message(text, urgent=False)
            self._record("系统", "（授权还开着，写「放行 / 打回 / 终止」完成这次授权）")
            return
        # v4.139 P2：技能指令（查缺口 / 去 GitHub 找 / 装 X）—— 对话内闭环：
        # 不必等跑完弹模态框、也不必去开组队弹窗点按钮。授权态已在上面的分支
        # 拦掉（授权优先），这里是非授权态的普通对话。
        _sk_kind, _sk_arg = legion.parse_skill_intent(text)
        if _sk_kind:
            self._record("你", text)
            self._handle_skill_intent(_sk_kind, _sk_arg)
            return
        # v4.148.1（UI 化繁为简）：聊天框直接「启动 <任务描述>」开团 ——
        # 只在军团未运行时生效（运行中写「启动」会被当留言转给 PM）。
        _task = self._parse_launch_command(text)
        if _task is not None:
            self._record("你", text)
            cb = getattr(self, "_launch_cb", None)
            if callable(cb):
                cb(_task)
            else:
                self._record("系统", "启动入口未接线（军团窗口还没准备好）。")
            return
        # 普通留言 → PM
        self._record("你", text)
        self._route_message(text, urgent=False)

    @staticmethod
    def _parse_launch_command(text: str):
        """识别「启动军团 …」「开团 …」开团命令。命中返回任务文本；未命中 None。

        刻意只认「启动军团 / 开团」两个明确前缀 —— 「启动」「开跑」这类词
        出现在给 PM 的普通留言里的概率太高（如「启动后把结果发我」），不能抢。
        """
        t = (text or "").strip()
        if not t or len(t) > 60:
            return None
        for kw in ("启动军团", "开团"):
            if t.startswith(kw) and len(t) > len(kw):
                task = t[len(kw):].strip(" :：，,。.")
                return task or None
        return None

    @staticmethod
    def _is_stop_command(text: str) -> bool:
        """一句话是不是明确的「停止」命令（区别于授权槽的 放行/打回/终止）。

        放宽收录是安全的：LegionWindow._on_stop_clicked 还有一道二次确认，
        认错了点「否」即可。反过来说，宁可多问一次，也不能让人停不下来。
        超过 20 字的句子一律不认 —— 那多半是在跟 PM 聊业务。
        """
        t = (text or "").strip().lower().replace(" ", "").replace("　", "")
        if not t or len(t) > 20:
            return False
        return any(k in t for k in ("停止", "终止", "停下", "别跑", "stop", "abort"))

    def _route_message(self, text, urgent):
        if callable(self._send_pm_cb):
            try:
                self._send_pm_cb(text, urgent)
            except Exception:
                pass

    def _resolve_auth(self, intent, text):
        if callable(self._auth_cb):
            try:
                self._auth_cb(intent, text)
            except Exception:
                pass
        self._pending_auth = None
        self.auth_btn.setEnabled(False)
        self._set_auth_hint(off=True)

    def auth_resolved(self):
        """兜底弹窗路径清掉授权待决态（worker 决议已由弹窗自己完成）。"""
        self._pending_auth = None
        self.auth_btn.setEnabled(False)
        self._set_auth_hint(off=True)

    def _on_auth_dialog_clicked(self):
        if self._pending_auth is None:
            return
        if callable(self._auth_dialog_cb):
            try:
                self._auth_dialog_cb()
            except Exception:
                pass

    # ---------- 渲染方法（接收 LegionWindow 转发）----------
    def on_log(self, text):
        """执行日志 → 按「整段」渲染成卡片（不再逐行拆碎，治「密」）。

        - 识别 worker 固定标记 `── 第 N 波启动` / `── 第 N 波 · 重跑` → 插入波次分隔条，
          后续消息归入「第 N 波」分组（治「分不清哪一波」）。
        - 含「项目经理」的整段归 PM（蓝）；`💬 联系项目经理` 回声归大哥自己；
          其余整段归系统（灰）。
        """
        if not text:
            return
        det = self._detect_wave(text)
        if det is not None:
            wn, rerun = det
            self._start_wave(wn, rerun)
            # 把标记行本身从文本里剥掉，避免它再被当成一张普通卡片
            text = _WAVE_RE.sub("", text)
            text = _WAVE_RERUN_RE.sub("", text)
            text = text.replace("──", "")
        chunk = text.strip()
        if not chunk:
            return
        # 整段作为一张卡片渲染（内部换行保留为 <br>），不再逐行拆；
        # 连续的普通系统日志并吞进同一张紧凑卡（治「密」），PM/你/成果等出现时先落卡。
        # 注意优先级：先判「💬 联系项目经理」回声（也含「项目经理」字样），再判普通 PM 行。
        if chunk.startswith("💬 联系项目经理"):
            self._flush_sys()
            self._record("你", chunk)
        elif "项目经理" in chunk:
            self._flush_sys()
            self.on_pm_status(chunk)
        else:
            self._buffer_system(chunk)

    def on_pm_status(self, text):
        """PM 主动汇报 / 请示 / 验收结论 → 蓝色 PM 卡片。"""
        if not text or not text.strip():
            return
        self._record("项目经理", text.strip())

    def begin_auth(self, title, detail):
        """挂起一次授权待决上下文，并把标题/正文渲染进时间线。"""
        self._pending_auth = {"title": title, "detail": detail}
        full = f"⏸ 等待你授权：{title}"
        d = (detail or "").strip()
        if d:
            full += "\n" + d
        self._record("授权", full)
        self._record("系统", "在下面一句话写「放行 / 打回（附改稿要求）/ 终止」即可，"
                            "也可点「授权弹窗」按钮。")
        self.auth_btn.setEnabled(True)
        self._set_auth_hint(off=False)

    def on_replan(self, reply):
        """PM 请求重编计划 → 系统引导（继续/停下），PM 原话走 on_log。"""
        self._record("系统", "📋 项目经理建议重新编计划 —— 在下面写「继续」（按原计划）"
                            "或「停下」（我去重编），也可点「授权弹窗」旁的处理。")

    def on_done(self, text):
        """成果落盘前在对话页给一句话索引（全文走报告文件，不刷屏）。"""
        if not text or not text.strip():
            return
        head = text.strip().split("\n")[0]
        self._record("成果", f"✅ 军团产出完成（共 {len(text)} 字）。首行：{head[:60]}")
        self._record("系统", "完整报告已落盘，可在「编排页 → 上次报告」打开。")

    def say(self, role, text):
        """通用追加（LegionWindow 记决策/系统提示用，如「你放行了下一波」）。"""
        if not text or not text.strip():
            return
        self._record(role, text.strip())

    def _set_auth_hint(self, off):
        try:
            if off:
                self.input.setPlaceholderText(
                    "说句话做决策，或给项目经理留言 · Enter 发送 / Shift+Enter 换行")
            else:
                self.input.setPlaceholderText(
                    "⏸ 授权待决：写「放行 / 打回 / 终止」完成这次授权")
        except Exception:
            pass

    # ---------- 波次识别 ----------
    def _detect_wave(self, text):
        """从日志片段里抠波次号。返回 (wave_no, is_rerun) 或 None。"""
        m = _WAVE_RE.search(text or "")
        if m:
            return int(m.group(1)), False
        m = _WAVE_RERUN_RE.search(text or "")
        if m:
            return int(m.group(1)), True
        return None

    def _is_warn(self, text):
        """单行日志是否含警示 token（失败/拦截/缺口/异常等）。"""
        if not text:
            return False
        for tok in _WARN_TOKENS:
            if tok in text:
                return True
        return False

    def _buffer_system(self, text):
        """系统日志：警示行单独成卡（立即可见），普通行累积进 _sys_buffer，
        由后续非系统消息 / 波次分隔 / 落盘事件统一并成一张紧凑卡，降低密度。"""
        if self._is_warn(text):
            self._flush_sys()
            self._record("系统", text)
        else:
            self._sys_buffer.append(text)

    def _flush_sys(self):
        """把累积的普通系统日志合并成一张紧凑卡渲染并落历史。"""
        if not self._sys_buffer:
            return
        text = "\n".join(self._sys_buffer)
        self._sys_buffer = []
        ts = self._now()
        self._render_card("系统", text, self._cur_wave, ts)
        self._append_history({"role": "系统", "content": text,
                              "wave": self._cur_wave, "ts": ts})

    def _start_wave(self, wave_no, rerun=False):
        """插入一条波次分隔条，并把后续消息归入该波分组。"""
        self._flush_sys()   # 先把上一波残余的系统日志落卡，再开新波分隔
        label = f"第 {wave_no} 波" + (" · 重跑" if rerun else "")
        self._cur_wave = wave_no
        self._render_divider(label, wave_no)
        self._append_history({"role": "波次", "content": label,
                              "wave": wave_no, "ts": self._now()})

    def _now(self):
        try:
            return datetime.now().strftime("%H:%M:%S")
        except Exception:
            return ""

    # ---------- 渲染基础 ----------
    def _record(self, role, text, wave=None):
        """渲染一张消息卡片并落历史。wave 缺省自动沿用当前波分组。"""
        if role != "系统":
            self._flush_sys()   # 非系统消息到来 → 先把累积的系统日志并成一张卡
        if wave is None:
            wave = self._cur_wave
        ts = self._now()
        self._render_card(role, text, wave, ts)
        self._append_history({"role": role, "content": text,
                              "wave": wave, "ts": ts})

    def _append_history(self, entry):
        self.history.append(entry)
        self._trim_history()
        self._save_counter += 1
        if self._save_counter >= _SAVE_EVERY:
            self._save_counter = 0
            self._save_history()

    def _accent_color(self, role, text):
        """卡片左色条颜色：系统日志含警示 token → 琥珀/红，否则取角色色。"""
        base = _ROLE_COLOR.get(role, THEME["role_default_gray"])
        if role == "系统" and text:
            for tok in _WARN_TOKENS:
                if tok in text:
                    return THEME["role_alert_red"]   # 警示红，让失败/拦截/缺口一眼可见
        return base

    def _render_card(self, role, text, wave=None, ts=None):
        """把一条消息渲染成「带角色徽章 + 时间戳 + 左色条的卡片」，卡片间留白。"""
        color = self._accent_color(role, text)
        ts = ts or self._now()
        border_c = getattr(self, "_THEME_BORDER", "#ddd")
        text_c = getattr(self, "_THEME_TEXT", "#222")
        faint_c = getattr(self, "_THEME_FAINT", "#999")
        # 系统日志降级为紧凑样式（小字、淡边），降低密度、突出关键消息
        if role == "系统":
            body_style = ("font-size:12px;color:%s;line-height:1.45;" % _ROLE_COLOR["系统"])
            pad = "5px 9px"
            border = ("border:1px solid %s;border-left:3px solid %s;"
                      % (border_c, color))
        else:
            body_style = "font-size:13px;color:%s;line-height:1.55;" % text_c
            pad = "7px 11px"
            border = ("border:1px solid %s;border-left:4px solid %s;"
                      % (border_c, color))
        body = _esc(text).replace(chr(10), "<br>")
        html = (
            '<div style="margin:0 0 8px 0;padding:%s;background:transparent;'
            '%s;border-radius:8px;">'
            '<div style="margin:0 0 4px 0;font-size:' + THEME['font_micro'] + ';color:%s;">'
            '<span style="font-weight:700;color:%s;">%s</span>'
            % (pad, border, faint_c, color, _esc(role))
        )
        if wave:
            html += ('<span style="color:%s;"> · 第 %d 波</span>'
                     % (faint_c, wave))
        html += ('<span style="float:right;">%s</span></div>'
                 '<div style="%s">%s</div></div>'
                 % (ts, body_style, body))
        self._insert(html)

    def _render_divider(self, label, wave_no):
        """渲染一条居中的波次分隔条，清晰划分波与波。"""
        html = (
            '<div style="margin:12px 0 8px 0;text-align:center;'
            'font-size:12px;font-weight:700;color:%s;letter-spacing:2px;">'
            '─────  %s  ─────</div>'
            % (_ROLE_COLOR["波次"], _esc(label))
        )
        self._insert(html)

    def _insert(self, s):
        self.log.insertHtml(s)
        c = self.log.textCursor()
        c.movePosition(c.MoveOperation.End)
        self.log.setTextCursor(c)

    # ---------- 历史（跟随项目）----------
    def _chat_path(self):
        if not self._proj_id:
            return None
        base = os.path.join(legion.LEGION_DIR, "legion_chat")
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            return None
        return os.path.join(base, f"{self._proj_id}.json")

    def _save_history(self):
        if getattr(self, "_history_load_failed", False):
            # P1-6（v4.186.0 审查）：历史加载失败期间禁止回写。此时
            # self.history 只含本轮新消息、不含磁盘旧记录，写出即用半份
            # 覆盖全量。旧文件原样保住，等下次能正常读取再恢复存档。
            return
        path = self._chat_path()
        if not path:
            return
        try:
            self._flush_sys()   # 落盘前先把残余系统日志并成卡，避免丢失
        except Exception:
            pass
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"history": self.history}, f,
                          ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass  # 存盘失败不影响本轮对话

    def _load_history(self):
        path = self._chat_path()
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.history = [
                m for m in (data.get("history") or [])
                if isinstance(m, dict) and m.get("role") in _ROLE_NAMES
                and isinstance(m.get("content"), str)
            ]
        except json.JSONDecodeError:
            # 真损坏（与"读不到"是两回事）：坏档改名留底，允许清空重建。
            try:
                os.replace(path, path + ".bad."
                           + datetime.now().strftime("%Y%m%d%H%M%S"))
            except Exception:
                pass
            self.history = []
        except Exception as e:
            # P1-6（v4.186.0 审查）：读失败（权限/瞬时锁/IO）≠内容损坏。
            # 原实现一律清空 history → 攒几条后 _save_history 把空历史回写，
            # 原文件被覆盖，军团对话记录永久丢失。改为：置读失败标志 →
            # 禁止回写（见 _save_history），磁盘旧记录原样保住。
            self._history_load_failed = True
            log.warning("军团对话历史读取失败，已禁止回写以防覆盖: %s: %s",
                        path, e)
        # 回放：按存档顺序重建卡片 + 波次分隔条（沿用存档里的 ts / wave）
        self._last_wave_rendered = None
        self._cur_wave = None
        for m in self.history[-_MAX_REPLAY:]:
            role = m["role"]
            content = m["content"]
            wave = m.get("wave")
            ts = m.get("ts")
            if role == "波次":
                self._render_divider(content, wave or 0)
                self._cur_wave = wave
                self._last_wave_rendered = wave
            else:
                # 兜底：显式分隔条缺失但波号变了，自动补一条（旧日志/异常路径）
                if wave is not None and wave != self._last_wave_rendered:
                    self._render_divider("第 %d 波" % wave, wave)
                    self._last_wave_rendered = wave
                self._render_card(role, content, wave, ts)
                self._cur_wave = wave

    def _trim_history(self):
        if len(self.history) > _MAX_HISTORY:
            self.history = self.history[-_MAX_HISTORY:]

    def reload_for_project(self, proj_id):
        """换项目后调用：切到该项目的对话历史。项目没变则不动作。"""
        if proj_id == self._proj_id:
            return
        try:
            self._save_history()
        except Exception:
            pass
        self._proj_id = proj_id
        self.history = []
        self._save_counter = 0
        self._pending_auth = None
        self._cur_wave = None
        self._last_wave_rendered = None
        self._sys_buffer = []
        self.auth_btn.setEnabled(False)
        self._set_auth_hint(off=True)
        self.log.clear()
        self._load_history()

    def _clear_chat(self):
        """清空当前项目的对话历史（内存 + 显示 + 持久化），不动项目产物。"""
        self.history = []
        self._cur_wave = None
        self._last_wave_rendered = None
        self._sys_buffer = []
        self.log.clear()
        self._save_history()
        self.input.clear()
        self.input.setFocus()
