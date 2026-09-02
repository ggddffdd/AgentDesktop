# -*- coding: utf-8 -*-
"""自动化任务面板（小臭内嵌工作台）。

build_automation_panel(app): 在 app.automation_page 上构建 UI。
- 任务列表：启用开关 / 名称 / 动作徽章 / 调度摘要 / 下次运行 / 编辑 / 删除。
- 新建/编辑对话框：名称 / 动作类型 / 内容 / 调度方式（一次性·每天·每周·间隔）。
- 数据存于 app.automation_store（automation.AutomationStore）；到点触发由 ui.py 的 QTimer 调度器负责。
"""

import functools
import logging

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QTextEdit,
    QComboBox, QCheckBox, QScrollArea, QWidget, QFrame, QDialog,
    QDateEdit, QTimeEdit, QSpinBox, QMessageBox, QSizePolicy,
)
from PySide6.QtCore import Qt, QDate, QTime

from ui import THEME
import automation as auto

log = logging.getLogger("dsdesktop")


def _safe(fn):
    @functools.wraps(fn)
    def _w(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            log.error("automation_panel 异常: %s", e)
    return _w


# ================= 新建 / 编辑任务对话框 =================

class TaskEditDialog(QDialog):
    def __init__(self, parent=None, task=None):
        super().__init__(parent)
        self.task = task  # None=新建，否则编辑
        self.setWindowTitle("编辑自动化任务" if task else "新建自动化任务")
        self.setMinimumWidth(480)
        self._build()
        if task:
            self._load(task)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        # 名称
        lay.addWidget(self._lbl("任务名称"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("如：每日热点简报")
        self.name_edit.setFixedHeight(34)
        self.name_edit.setStyleSheet(_INPUT_QSS)
        lay.addWidget(self.name_edit)

        # 动作类型
        lay.addWidget(self._lbl("到点执行的动作"))
        self.action_combo = QComboBox()
        self.action_combo.setFixedHeight(34)
        self.action_combo.addItem("🔔 定时提醒（弹窗）", auto.ACT_REMIND)
        self.action_combo.addItem("🤖 执行任务（交给 Agent 跑）", auto.ACT_RUN)
        self.action_combo.setStyleSheet(_INPUT_QSS)
        self.action_combo.currentIndexChanged.connect(self._on_action_changed)
        lay.addWidget(self.action_combo)

        # 内容
        self.content_lbl = self._lbl("提醒内容")
        lay.addWidget(self.content_lbl)
        self.content_edit = QTextEdit()
        self.content_edit.setFixedHeight(90)
        self.content_edit.setStyleSheet(_INPUT_QSS)
        lay.addWidget(self.content_edit)

        # 调度方式
        lay.addWidget(self._lbl("调度方式"))
        self.sched_combo = QComboBox()
        self.sched_combo.setFixedHeight(34)
        for st in auto.SCHEDULE_TYPES:
            self.sched_combo.addItem(auto.SCHEDULE_LABELS[st], st)
        self.sched_combo.setStyleSheet(_INPUT_QSS)
        self.sched_combo.currentIndexChanged.connect(self._on_sched_changed)
        lay.addWidget(self.sched_combo)

        # 调度参数区
        self.once_row = self._make_once_row()
        self.daily_row = self._make_time_row("daily_time")
        self.weekly_row = self._make_weekly_row()
        self.interval_row = self._make_interval_row()
        lay.addWidget(self.once_row)
        lay.addWidget(self.daily_row)
        lay.addWidget(self.weekly_row)
        lay.addWidget(self.interval_row)

        # 启用
        self.enabled_chk = QCheckBox("创建后立即启用")
        self.enabled_chk.setChecked(True)
        self.enabled_chk.setStyleSheet(f"QCheckBox{{color:{THEME['text']};font-size:13px;}}")
        lay.addWidget(self.enabled_chk)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setFixedSize(88, 34)
        cancel.setStyleSheet(_BTN_QSS)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存")
        ok.setFixedSize(88, 34)
        ok.setStyleSheet(_PRIMARY_QSS)
        ok.clicked.connect(self._on_save)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)

        self._on_sched_changed()

    def _lbl(self, text):
        l = QLabel(text)
        l.setStyleSheet(f"color:{THEME['dim']};font-size:12px;font-weight:600;")
        return l

    def _make_once_row(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("日期"))
        self.once_date = QDateEdit()
        self.once_date.setCalendarPopup(True)
        self.once_date.setDisplayFormat("yyyy-MM-dd")
        self.once_date.setDate(QDate.currentDate())
        self.once_date.setStyleSheet(_INPUT_QSS)
        lay.addWidget(self.once_date, 1)
        lay.addWidget(QLabel("时间"))
        self.once_time = self._time_edit()
        lay.addWidget(self.once_time, 1)
        return w

    def _make_time_row(self, name):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("每天时间"))
        self.daily_time = self._time_edit()
        lay.addWidget(self.daily_time, 1)
        lay.addStretch(1)
        return w

    def _make_weekly_row(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("星期"))
        self.weekday_combo = QComboBox()
        for i, n in enumerate(auto.WEEKDAY_NAMES):
            self.weekday_combo.addItem(n, i)
        self.weekday_combo.setStyleSheet(_INPUT_QSS)
        lay.addWidget(self.weekday_combo, 1)
        lay.addWidget(QLabel("时间"))
        self.weekly_time = self._time_edit()
        lay.addWidget(self.weekly_time, 1)
        return w

    def _make_interval_row(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("每隔"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 24 * 60)
        self.interval_spin.setValue(60)
        self.interval_spin.setStyleSheet(_INPUT_QSS)
        lay.addWidget(self.interval_spin, 1)
        lay.addWidget(QLabel("分钟"))
        lay.addStretch(1)
        return w

    def _time_edit(self):
        te = QTimeEdit()
        te.setDisplayFormat("HH:mm")
        te.setTime(QTime(9, 0))
        te.setStyleSheet(_INPUT_QSS)
        return te

    def _on_action_changed(self):
        is_run = self.action_combo.currentData() == auto.ACT_RUN
        self.content_lbl.setText("执行指令（到点自动发给 Agent）" if is_run else "提醒内容")
        self.content_edit.setPlaceholderText(
            "如：抓取今天全网 AI 热点并总结成 200 字简报" if is_run
            else "如：该发公众号了")
        self.content_edit.setFixedHeight(110 if is_run else 90)

    def _on_sched_changed(self):
        st = self.sched_combo.currentData()
        self.once_row.setVisible(st == auto.SCHED_ONCE)
        self.daily_row.setVisible(st == auto.SCHED_DAILY)
        self.weekly_row.setVisible(st == auto.SCHED_WEEKLY)
        self.interval_row.setVisible(st == auto.SCHED_INTERVAL)

    def _load(self, task):
        self.name_edit.setText(task.get("name", ""))
        idx = self.action_combo.findData(task.get("action", auto.ACT_REMIND))
        self.action_combo.setCurrentIndex(max(0, idx))
        self.content_edit.setPlainText(task.get("message", ""))
        idx = self.sched_combo.findData(task.get("schedule_type", auto.SCHED_DAILY))
        self.sched_combo.setCurrentIndex(max(0, idx))
        self.enabled_chk.setChecked(bool(task.get("enabled", True)))

        at_time = task.get("at_time", "09:00")
        hh, mm = auto._parse_hm(at_time)
        t = QTime(hh, mm)
        self.once_time.setTime(t)
        self.daily_time.setTime(t)
        self.weekly_time.setTime(t)
        self.weekday_combo.setCurrentIndex(int(task.get("weekday", 0)) % 7)
        self.interval_spin.setValue(int(task.get("interval_minutes", 60)))
        if task.get("at_date"):
            try:
                self.once_date.setDate(QDate.fromString(task["at_date"], "yyyy-MM-dd"))
            except Exception:
                pass
        self._on_action_changed()
        self._on_sched_changed()

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请填写任务名称")
            return
        action = self.action_combo.currentData()
        message = self.content_edit.toPlainText().strip()
        if not message:
            QMessageBox.warning(self, "提示", "请填写提醒内容或执行指令")
            return
        st = self.sched_combo.currentData()
        at_time = self.daily_time.time().toString("HH:mm")

        task = {
            "id": self.task.get("id") if self.task else None,
            "name": name,
            "action": action,
            "message": message,
            "schedule_type": st,
            "enabled": self.enabled_chk.isChecked(),
            "at_time": self.daily_time.time().toString("HH:mm"),
            "at_date": self.once_date.date().toString("yyyy-MM-dd"),
            "weekday": self.weekday_combo.currentData(),
            "interval_minutes": self.interval_spin.value(),
        }
        if st == auto.SCHED_ONCE:
            task["at_time"] = self.once_time.time().toString("HH:mm")
        elif st == auto.SCHED_WEEKLY:
            task["at_time"] = self.weekly_time.time().toString("HH:mm")
        task["last_run"] = self.task.get("last_run", 0.0) if self.task else 0.0
        task["created"] = self.task.get("created") if self.task else None
        self._result = task
        self.accept()


# ================= 面板 =================

def build_automation_panel(app):
    page = app.automation_page
    # 清空旧布局（若有）
    if page.layout() is not None:
        while page.layout().count():
            item = page.layout().takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    lay = QVBoxLayout(page)
    lay.setContentsMargins(32, 24, 32, 24)
    lay.setSpacing(16)

    head = QLabel("⏰ 自动化任务")
    head.setStyleSheet(f"font-size:20px;font-weight:700;color:{THEME['text']};")
    lay.addWidget(head)

    sub = QLabel("定时提醒 / 定时执行任务。到点后：提醒会弹窗，执行任务会自动交给 Agent 在后台跑（需 App 保持运行）。")
    sub.setWordWrap(True)
    sub.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    lay.addWidget(sub)

    # 新建按钮
    top_row = QHBoxLayout()
    new_btn = QPushButton("＋ 新建任务")
    new_btn.setFixedHeight(36)
    new_btn.setCursor(Qt.PointingHandCursor)
    new_btn.setStyleSheet(_PRIMARY_QSS)
    new_btn.clicked.connect(lambda: _open_edit(app, None))
    top_row.addWidget(new_btn)
    top_row.addStretch(1)
    lay.addLayout(top_row)

    # 列表滚动区
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
    container = QWidget()
    container.setStyleSheet("background:transparent;")
    list_lay = QVBoxLayout(container)
    list_lay.setContentsMargins(0, 0, 0, 0)
    list_lay.setSpacing(8)
    list_lay.addStretch(1)
    scroll.setWidget(container)
    lay.addWidget(scroll, 1)

    # 底部状态
    status = QLabel("")
    status.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    lay.addWidget(status)

    app.auto_status = status
    app.auto_list_lay = list_lay

    def refresh():
        _refresh_list(app)

    app._refresh_automation_list = refresh
    refresh()


def _open_edit(app, task):
    dlg = TaskEditDialog(app, task)
    if dlg.exec() == QDialog.Accepted:
        data = dlg._result
        st = app.automation_store
        if task is None:
            t = auto.new_task(
                data["name"], data["action"], data["message"], data["schedule_type"],
                at_time=data["at_time"], at_date=data["at_date"],
                weekday=data["weekday"], interval_minutes=data["interval_minutes"],
                enabled=data["enabled"])
            st.add(t)
        else:
            merged = dict(task)
            merged.update({
                "name": data["name"], "action": data["action"], "message": data["message"],
                "schedule_type": data["schedule_type"], "enabled": data["enabled"],
                "at_time": data["at_time"], "at_date": data["at_date"],
                "weekday": data["weekday"], "interval_minutes": data["interval_minutes"],
            })
            st.update(merged)
        app._refresh_automation_list()
        app.auto_status.setText("已保存 ✓")


def _refresh_list(app):
    st = app.automation_store
    lay = app.auto_list_lay
    # 清空（保留末尾 stretch）
    while lay.count() > 1:
        item = lay.takeAt(0)
        w = item.widget()
        if w:
            w.deleteLater()

    tasks = st.list_all()
    if not tasks:
        empty = QLabel("还没有任务。点击上方「＋ 新建任务」创建第一个。")
        empty.setStyleSheet(f"color:{THEME['dim']};font-size:13px;padding:24px;")
        empty.setAlignment(Qt.AlignCenter)
        lay.insertWidget(0, empty)
        return

    for t in tasks:
        card = _make_card(app, t)
        lay.insertWidget(lay.count() - 1, card)


def _make_card(app, task):
    card = QFrame()
    card.setStyleSheet(
        f"QFrame{{background:{THEME['card']};border:1px solid {THEME['border']};border-radius:12px;}}")
    v = QVBoxLayout(card)
    v.setContentsMargins(16, 12, 16, 12)
    v.setSpacing(6)

    # 第一行：启用 + 名称 + 徽章 + 编辑/删除
    row1 = QHBoxLayout()
    chk = QCheckBox()
    chk.setChecked(bool(task.get("enabled", True)))
    chk.setStyleSheet(f"QCheckBox{{color:{THEME['text']};}}")
    chk.toggled.connect(lambda on, tid=task["id"]: _toggle(app, tid, on))
    row1.addWidget(chk)

    name = QLabel(task.get("name", "未命名"))
    name.setStyleSheet(f"font-size:14px;font-weight:600;color:{THEME['text']};")
    row1.addWidget(name)

    action = task.get("action", auto.ACT_REMIND)
    badge = QLabel(auto.ACTION_LABELS.get(action, action))
    badge.setStyleSheet(
        f"background:{_badge_bg(action)};color:#FFFFFF;border-radius:9px;"
        f"padding:2px 10px;font-size:11px;font-weight:600;")
    row1.addWidget(badge)
    row1.addStretch(1)

    edit_btn = QPushButton("编辑")
    edit_btn.setFixedSize(56, 26)
    edit_btn.setCursor(Qt.PointingHandCursor)
    edit_btn.setStyleSheet(_SMALL_BTN_QSS)
    edit_btn.clicked.connect(lambda _, t=task: _open_edit(app, t))
    row1.addWidget(edit_btn)

    del_btn = QPushButton("删除")
    del_btn.setFixedSize(56, 26)
    del_btn.setCursor(Qt.PointingHandCursor)
    del_btn.setStyleSheet(_DANGER_QSS)
    del_btn.clicked.connect(lambda _, tid=task["id"]: _delete(app, tid))
    row1.addWidget(del_btn)
    v.addLayout(row1)

    # 第二行：调度摘要 + 下次运行
    row2 = QHBoxLayout()
    sched = QLabel("🗓 " + auto.schedule_summary(task))
    sched.setStyleSheet(f"font-size:12px;color:{THEME['dim']};")
    row2.addWidget(sched)
    row2.addStretch(1)
    if task.get("enabled", True):
        nxt = QLabel("⏱ " + auto.next_run_text(task))
        nxt.setStyleSheet(f"font-size:12px;color:{THEME['accent']};")
        row2.addWidget(nxt)
    else:
        off = QLabel("已停用")
        off.setStyleSheet(f"font-size:12px;color:{THEME['danger']};")
        row2.addWidget(off)
    v.addLayout(row2)

    return card


def _badge_bg(action):
    return THEME["accent"] if action == auto.ACT_RUN else "#8A6FE8"


def _toggle(app, task_id, on):
    app.automation_store.set_enabled(task_id, on)
    app._refresh_automation_list()
    app.auto_status.setText("已启用" if on else "已停用")


def _delete(app, task_id):
    t = app.automation_store.get(task_id)
    if not t:
        return
    r = QMessageBox.question(app, "删除任务", f"确定删除「{t.get('name','')}」？",
                             QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    if r != QMessageBox.Yes:
        return
    app.automation_store.delete(task_id)
    app._refresh_automation_list()
    app.auto_status.setText("已删除")


# ================= 样式 =================

_INPUT_QSS = (
    f"QLineEdit,QComboBox,QDateEdit,QTimeEdit,QSpinBox,QTextEdit{{"
    f"background:{THEME['panel2']};border:1px solid {THEME['border']};border-radius:8px;"
    f"color:{THEME['text']};padding:4px 10px;font-size:13px;}}"
    f"QLineEdit:focus,QComboBox:focus,QDateEdit:focus,QTimeEdit:focus,QSpinBox:focus,"
    f"QTextEdit:focus{{border-color:{THEME['accent']};}}"
)

_BTN_QSS = (
    f"QPushButton{{background:{THEME['panel2']};color:{THEME['text']};border:1px solid {THEME['border']};"
    f"border-radius:8px;font-size:13px;}}"
    f"QPushButton:hover{{background:{THEME['blue_hover']};}}"
)

_PRIMARY_QSS = (
    f"QPushButton{{background:{THEME['accent']};color:#FFFFFF;border:none;border-radius:8px;"
    f"padding:0 18px;font-size:13px;font-weight:500;}}"
    f"QPushButton:hover{{background:{THEME['accent_hover']};}}"
)

_SMALL_BTN_QSS = (
    f"QPushButton{{background:transparent;color:{THEME['dim']};border:1px solid {THEME['border']};"
    f"border-radius:7px;font-size:12px;}}"
    f"QPushButton:hover{{background:{THEME['panel2']};color:{THEME['text']};}}"
)

_DANGER_QSS = (
    f"QPushButton{{background:transparent;color:{THEME['danger']};border:1px solid {THEME['border']};"
    f"border-radius:7px;font-size:12px;}}"
    f"QPushButton:hover{{background:{THEME['danger']};color:#FFFFFF;}}"
)
