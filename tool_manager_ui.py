"""
可视化工具管理器 v4.111

干的事：把 config.json 的 `enabled_tools` 白名单变成一个能勾选、能看体积、
能看使用次数的界面。

为什么需要它（v4.111 实测）：
  68 个工具定义共 26,699 字符，占每次 API 输入 token 的约 65%；其中 30 个
  从未被用过，却吃掉 42.9% 的体积。Agent 路径每轮全量注入，长会话把这项
  放大几百倍。**功能可以无限堆，但每轮注入量不能跟着涨**，否则"功能怪兽"
  会被自己的成本拖死。

设计原则（与技能管理器 v4.67 一致）：
- 勾选即存盘，没有多余的"应用"按钮
- 白名单为空 = 全部启用（向后兼容，装了不动就等于没变）
- 关掉只是不注入，不是删除，随时能开回来

使用方式：
    from tool_manager_ui import ToolManagerWindow
    window = ToolManagerWindow(cfg)
    window.show()
"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QMessageBox, QGroupBox, QLineEdit, QComboBox, QApplication
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
import json
import os
import logging

import config

log = logging.getLogger(__name__)

# 视觉配色（与技能管理器保持一致）
_COLOR_ON = QColor("#1b7a3d")    # 启用：绿
_COLOR_OFF = QColor("#9aa0a6")   # 禁用：灰
_COLOR_HOT = QColor("#b45309")   # 体积大又没人用：橙

# 体积/token 估算系数，与 tool_budget.py 保持一致
CHARS_PER_TOK = 2.0


def _tool_usage_counts():
    """从真实会话历史里统计每个工具被**调用过多少次**。

    只统计 role == "tool_log" 的消息（结构：{role, name, args, result}）。
    读不到或格式变了就返回空 dict——用不上不算错，界面退化成不显示次数即可。

    ⚠ 口径差异：这里是**调用次数**（同一轮里调 3 次算 3），
    而 tool_budget.py 统计的是**轮次数**（同一轮里调 3 次算 1）。
    两边数字对不上是正常的，别当成 bug 去"修"。
    """
    counts = {}
    path = os.path.join(os.path.expanduser("~"), "Documents",
                        "小臭玩AI", "sessions.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return counts
    sess = data.get("sessions") if isinstance(data, dict) else data
    if not isinstance(sess, list):
        return counts
    for s in sess:
        if not isinstance(s, dict):
            continue
        for m in (s.get("messages") or []):
            if isinstance(m, dict) and m.get("role") == "tool_log":
                n = (m.get("name") or "").strip()
                if n:
                    counts[n] = counts.get(n, 0) + 1
    return counts


class ToolManagerWindow(QMainWindow):
    def __init__(self, cfg=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        # 白名单为空 = 全部启用（向后兼容）
        self.enabled = list(self.cfg.get("enabled_tools", []) or [])
        self.all_tools = []       # [{name, desc, params, chars, used}, ...]
        self.usage = {}
        self._suppress = False    # 重建期屏蔽 itemChanged 防递归
        self.init_ui()
        self.load_tools()

    # ---------- 启用判定（与 config._filter_enabled_tools 同一规则）----------
    def _is_enabled(self, name):
        if not self.enabled:
            return True
        return name in self.enabled

    # ---------- 界面 ----------
    def init_ui(self):
        self.setWindowTitle("工具管理器 - 小臭玩 AI")
        self.setMinimumSize(940, 700)

        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)

        title = QLabel("工具管理器")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        main.addWidget(title)

        tip = QLabel("关闭的工具不会被注入给模型 = 模型看不见它，也就不会调用。"
                     "只是不注入、不是删除，勾回来即可恢复。")
        tip.setStyleSheet("color: #888; font-size: 11px;")
        tip.setWordWrap(True)
        main.addWidget(tip)

        self.stats_label = QLabel("加载中...")
        self.stats_label.setStyleSheet("color: #444; font-size: 12px; padding: 4px 0;")
        main.addWidget(self.stats_label)

        # 筛选栏
        bar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 搜索工具名 / 描述…")
        self.search_box.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search_box, 3)

        self.status_combo = QComboBox()
        self.status_combo.addItems(["全部", "仅已启用", "仅未启用"])
        self.status_combo.currentTextChanged.connect(self._apply_filter)
        bar.addWidget(self.status_combo, 1)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["体积大的在前", "用得多的在前", "按名称"])
        self.sort_combo.currentTextChanged.connect(self._apply_filter)
        bar.addWidget(self.sort_combo, 1)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.load_tools)
        bar.addWidget(refresh_btn)
        main.addLayout(bar)

        # 预设档位
        preset = QHBoxLayout()
        preset.addWidget(QLabel("一键档位："))
        for label, tip_text in (("全开", "全部工具注入（默认，行为零变化）"),
                                ("均衡", "只留历史用到过的工具"),
                                ("精简", "只留高频工具（≥3 次）")):
            b = QPushButton(label)
            b.setToolTip(tip_text)
            b.clicked.connect(lambda _c, k=label: self._apply_preset(k))
            preset.addWidget(b)
        preset.addStretch()
        main.addLayout(preset)

        # 左右分栏
        split = QHBoxLayout()

        left = QGroupBox("工具清单（勾选 = 注入）")
        ll = QVBoxLayout(left)
        self.tool_list = QListWidget()
        self.tool_list.currentItemChanged.connect(self._on_current_changed)
        self.tool_list.itemChanged.connect(self.on_item_changed)
        ll.addWidget(self.tool_list)
        split.addWidget(left, 2)

        right = QGroupBox("工具详情")
        rl = QVBoxLayout(right)
        self.detail_name = QLabel("未选择工具")
        self.detail_name.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        rl.addWidget(self.detail_name)

        self.detail_meta = QLabel("")
        self.detail_meta.setStyleSheet("font-size: 11px; color: #666;")
        self.detail_meta.setWordWrap(True)
        rl.addWidget(self.detail_meta)

        self.detail_desc = QLabel("")
        self.detail_desc.setStyleSheet("font-size: 11px; color: #333;")
        self.detail_desc.setWordWrap(True)
        rl.addWidget(self.detail_desc)
        rl.addStretch()
        split.addWidget(right, 1)

        main.addLayout(split)

        self.status_bar = QLabel("就绪")
        self.status_bar.setStyleSheet(
            "color: gray; font-size: 11px; border-top: 1px solid #eee; padding: 5px;")
        main.addWidget(self.status_bar)

    # ---------- 加载 ----------
    def load_tools(self):
        """读工具定义 + 历史使用次数，重建列表。"""
        self.usage = _tool_usage_counts()
        try:
            # ⚠ 必须拿**全量**工具，不能拿过滤后的：self.cfg 里带着白名单，
            # 直接传进去的话，被关掉的工具根本不会出现在这里 —— 等于永远开不回来。
            probe = dict(self.cfg)
            probe["enabled_tools"] = []
            defs = config.get_all_tools(probe)
        except Exception as e:
            log.warning("读取工具定义失败: %s", e)
            self.status_bar.setText("读取工具定义失败：%s" % e)
            return
        rows = []
        for t in defs:
            fn = (t or {}).get("function") or {}
            name = fn.get("name") or (t or {}).get("name") or ""
            if not name:
                continue
            rows.append({
                "name": name,
                "desc": fn.get("description") or "",
                "params": list(((fn.get("parameters") or {}).get("properties")
                                or {}).keys()),
                "chars": len(json.dumps(t, ensure_ascii=False)),
                "used": self.usage.get(name, 0),
            })
        self.all_tools = rows
        self._apply_filter()
        self.status_bar.setText("已加载 %d 个工具" % len(rows))

    # ---------- 过滤 / 排序 ----------
    def _visible_rows(self):
        kw = (self.search_box.text() or "").strip().lower()
        st = self.status_combo.currentText()
        rows = []
        for r in self.all_tools:
            if kw and kw not in r["name"].lower() and kw not in r["desc"].lower():
                continue
            if st == "仅已启用" and not self._is_enabled(r["name"]):
                continue
            if st == "仅未启用" and self._is_enabled(r["name"]):
                continue
            rows.append(r)
        mode = self.sort_combo.currentText()
        if mode == "体积大的在前":
            rows.sort(key=lambda x: -x["chars"])
        elif mode == "用得多的在前":
            rows.sort(key=lambda x: (-x["used"], -x["chars"]))
        else:
            rows.sort(key=lambda x: x["name"])
        return rows

    def _apply_filter(self):
        self._suppress = True
        self.tool_list.clear()
        for r in self._visible_rows():
            it = QListWidgetItem()
            it.setData(Qt.UserRole, r)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            self._decorate_item(it, self._is_enabled(r["name"]))
            self.tool_list.addItem(it)
        self._suppress = False
        self._update_stats()

    def _decorate_item(self, item, enabled):
        r = item.data(Qt.UserRole)
        used = r["used"]
        usage_txt = ("从未用过" if used == 0 else "用过 %d 次" % used)
        item.setText("%s   ·   %d 字符   ·   %s"
                     % (r["name"], r["chars"], usage_txt))
        item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
        if not enabled:
            item.setForeground(_COLOR_OFF)
        elif used == 0 and r["chars"] >= 400:
            item.setForeground(_COLOR_HOT)   # 体积大又没人用 = 最该关
        else:
            item.setForeground(_COLOR_ON)

    # ---------- 勾选即存盘 ----------
    def on_item_changed(self, item):
        if self._suppress:
            return
        r = item.data(Qt.UserRole)
        if not r:
            return
        name = r["name"]
        checked = item.checkState() == Qt.Checked
        if checked == self._is_enabled(name):
            return

        # 首次改动：把白名单初始化为「当前全部」，再应用本次操作
        if not self.enabled:
            self.enabled = [x["name"] for x in self.all_tools]

        if checked:
            if name not in self.enabled:
                self.enabled.append(name)
        else:
            if name in self.enabled:
                self.enabled.remove(name)

        self._save_config()
        self._update_stats()

        self._suppress = True
        self._decorate_item(item, checked)
        self._suppress = False
        if self.tool_list.currentItem() is item:
            self._show_detail(r, checked)

    # ---------- 预设档位 ----------
    def _apply_preset(self, kind):
        if kind == "全开":
            self.enabled = []
        elif kind == "均衡":
            self.enabled = [r["name"] for r in self.all_tools if r["used"] >= 1]
        elif kind == "精简":
            self.enabled = [r["name"] for r in self.all_tools if r["used"] >= 3]
        else:
            return
        self._save_config()
        self._apply_filter()

    # ---------- 详情 ----------
    def _on_current_changed(self, cur, _prev):
        if cur is None:
            self.detail_name.setText("未选择工具")
            self.detail_meta.setText("")
            self.detail_desc.setText("")
            return
        r = cur.data(Qt.UserRole)
        if r:
            self._show_detail(r, self._is_enabled(r["name"]))

    def _show_detail(self, r, enabled):
        self.detail_name.setText(r["name"])
        self.detail_meta.setText(
            "状态：%s\n体积：%d 字符（≈ %d tok）\n历史使用：%s\n参数：%s"
            % ("已注入" if enabled else "已关闭",
               r["chars"], int(r["chars"] / CHARS_PER_TOK),
               ("从未用过" if r["used"] == 0 else "%d 次" % r["used"]),
               "、".join(r["params"]) or "无"))
        self.detail_desc.setText(r["desc"])

    # ---------- 统计 ----------
    def _update_stats(self):
        total = sum(r["chars"] for r in self.all_tools) or 1
        on = [r for r in self.all_tools if self._is_enabled(r["name"])]
        on_chars = sum(r["chars"] for r in on)
        never = [r for r in self.all_tools if r["used"] == 0]
        never_on = [r for r in never if self._is_enabled(r["name"])]
        self.stats_label.setText(
            "启用 %d / 共 %d 个   注入 %s 字符（≈ %s tok）   相对全量省 %.0f%%   "
            "｜ 仍开着但从没用过的：%d 个（%s 字符）"
            % (len(on), len(self.all_tools),
               format(on_chars, ","),
               format(int(on_chars / CHARS_PER_TOK), ","),
               100.0 * (1 - on_chars / total),
               len(never_on), format(sum(r["chars"] for r in never_on), ",")))

    # ---------- 存盘 ----------
    def _save_config(self):
        self.cfg["enabled_tools"] = self.enabled
        try:
            config.save_config(self.cfg)
        except Exception as e:
            self.status_bar.setText("保存失败: %s" % e)
            return
        self.status_bar.setText(
            "已保存（启用 %d 个%s）"
            % (len(self.enabled) if self.enabled else len(self.all_tools),
               "" if self.enabled else "，空清单=全开"))

    def closeEvent(self, event):
        """关窗时若白名单是「全开」，写空数组而不是 68 个名字。

        省得配置文件里堆一大串名字，将来新增工具还得手动补进去。
        """
        try:
            if self.enabled and len(self.enabled) >= len(self.all_tools):
                self.cfg["enabled_tools"] = []
                config.save_config(self.cfg)
        except Exception:
            pass
        super().closeEvent(event)


# 模块级缓存，防止 ToolManagerWindow 被 GC 回收导致崩溃
_open_tool_windows = {}


def open_tool_manager(cfg):
    """打开工具管理器窗口（供 ui.py / main.py 调用）。

    同一时刻只保持一个实例，再次点击时激活已有窗口。
    """
    app = QApplication.instance()
    if app is None:
        return None
    for win in list(_open_tool_windows.values()):
        if win.isVisible():
            win.raise_()
            win.activateWindow()
            return win

    win = ToolManagerWindow(cfg)
    win.show()
    win.raise_()
    win.activateWindow()

    wid = id(win)
    _open_tool_windows[wid] = win
    win.destroyed.connect(lambda obj, w=wid: _open_tool_windows.pop(w, None))
    return win
