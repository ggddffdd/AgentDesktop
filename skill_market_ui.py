# -*- coding: utf-8 -*-
"""AgentDesktop — 技能市场发现 UI（P2）

独立窗口：让用户「发现并安装」新技能，而不只是管理已装的。

能力：
- 浏览已安装技能（启用状态、来源、卸载用户技能）。
- 发现新技能：
  * 「在线搜索」：实时搜 GitHub（网络不可达时优雅降级，提示用链接安装）。
  * 「从链接安装」：粘贴 SKILL.md / 仓库 / blob / tree 链接，自动走国内镜像拉取+审计+安装。
  * 可编辑清单 skills_catalog.json（用户目录）：把常用技能链接常驻市场，重启不丢。

设计取舍：
- 用户网络境外站常不通，故「在线搜索」是增强项；链接安装走 ghproxy/jsdelivr/gitmirror
  镜像，是国内可达主路径，务必保证好用。
- 安装是网络操作，放 QThread 防 UI 卡死；卸载是本地操作，同步即可。
- 复用已有基建：config.get_skill_scan_dirs / skill_loader.scan_skills /
  skill_installer_tools.tool_skill_install（含安全审计 + 镜像回退）。

使用：
    from skill_market_ui import open_skill_market
    open_skill_market(cfg)
"""
import os
import json
import logging
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QLineEdit, QComboBox, QMessageBox, QFrame,
    QApplication, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QFont, QColor, QDesktopServices

import config
from skill_loader import scan_skills, normalize_skill_name

log = logging.getLogger(__name__)

# 用户数据目录（应用自有数据，非系统个人目录）
USER_SKILLS_DIR = os.path.join(os.path.expanduser("~"), "Documents", "AgentDesktop", "skills")
CATALOG_PATH = os.path.join(os.path.expanduser("~"), "Documents", "AgentDesktop", "skills_catalog.json")

# 配色
_COLOR_CARD = QColor("#ffffff")
_COLOR_ON = QColor("#1b7a3d")
_COLOR_OFF = QColor("#9aa0a6")
_COLOR_ACCENT = QColor("#6c5ce7")
_COLOR_WARN = QColor("#b8860b")


# ---------- 安装线程（防 UI 卡死） ----------
class InstallThread(QThread):
    done = Signal(bool, str)  # (ok, message)

    def __init__(self, cfg, app_dir, url):
        super().__init__()
        self.cfg = cfg
        self.app_dir = app_dir
        self.url = url

    def run(self):
        try:
            import skill_installer_tools
            msg, _, _ = skill_installer_tools.tool_skill_install(
                self.cfg, self.app_dir, {"url": self.url}
            )
            ok = not msg.startswith(("失败", "⛔", "⚠️"))
            self.done.emit(ok, msg)
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, f"安装线程异常：{e}")


class SkillMarketWindow(QMainWindow):
    def __init__(self, cfg=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        self.installed = {}      # norm_name -> info dict
        self.catalog = []        # 用户清单 + 默认清单（未安装的可发现项）
        self.search_results = [] # 在线搜索结果（本次会话有效）
        self._cards = []         # 当前显示的卡片控件
        self._suppress = False
        self.setWindowTitle("技能市场 - Agent玩 AI")
        self.setMinimumSize(860, 660)
        self.resize(980, 720)
        self.init_ui()
        self.reload()

    # ---------- 启用判定（与对话侧同一规则） ----------
    def _is_enabled(self, name):
        enabled = self.cfg.get("enabled_skills", []) or []
        if not enabled:
            return True
        return name in enabled

    # ---------- UI ----------
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        # 标题栏
        title_row = QHBoxLayout()
        title = QLabel("🛍 技能市场")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        title_row.addWidget(title)
        title_row.addStretch(1)

        self.search_online_btn = QPushButton("🔎 在线搜索")
        self.search_online_btn.setFixedHeight(32)
        self.search_online_btn.setToolTip("联网搜 GitHub 社区技能（网络不可达时自动降级）")
        self.search_online_btn.clicked.connect(self._online_search)
        title_row.addWidget(self.search_online_btn)

        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.clicked.connect(self.reload)
        title_row.addWidget(self.refresh_btn)

        root.addLayout(title_row)

        # 说明
        hint = QLabel(
            "发现新技能：① 点「在线搜索」（联网）；② 粘贴 SKILL.md / 仓库链接一键安装（自动走国内镜像，"
            "国内可达）；③ 把常用链接「加入清单」常驻显示。已装技能可在此启用 / 卸载。"
        )
        hint.setStyleSheet("font-size:12px;color:#888;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # 过滤栏：搜索 + 分类
        filter_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 过滤技能名 / 描述…")
        self.search_box.textChanged.connect(self._rebuild_cards)
        filter_row.addWidget(self.search_box, 4)

        self.cat_combo = QComboBox()
        self.cat_combo.addItem("全部分类")
        self.cat_combo.currentTextChanged.connect(self._rebuild_cards)
        filter_row.addWidget(self.cat_combo, 1)
        root.addLayout(filter_row)

        # 从链接安装
        link_row = QHBoxLayout()
        link_row.addWidget(QLabel("从链接安装："))
        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText("粘贴 GitHub 仓库页 / blob / tree / raw SKILL.md 直链")
        link_row.addWidget(self.link_edit, 1)
        self.link_btn = QPushButton("⬇️ 安装")
        self.link_btn.setFixedHeight(32)
        self.link_btn.clicked.connect(self._install_from_link)
        link_row.addWidget(self.link_btn)
        self.add_catalog_btn = QPushButton("➕ 加入清单")
        self.add_catalog_btn.setFixedHeight(32)
        self.add_catalog_btn.setToolTip("把当前链接存到 skills_catalog.json，常驻市场")
        self.add_catalog_btn.clicked.connect(self._add_link_to_catalog)
        link_row.addWidget(self.add_catalog_btn)
        root.addLayout(link_row)

        # 卡片滚动区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{background:#f6f7f9;border:1px solid #e6e8eb;border-radius:10px;}")
        self.cards_inner = QWidget()
        self.cards_lay = QVBoxLayout(self.cards_inner)
        self.cards_lay.setContentsMargins(14, 14, 14, 14)
        self.cards_lay.setSpacing(10)
        self.scroll.setWidget(self.cards_inner)
        root.addWidget(self.scroll, 1)

        # 状态栏
        self.status_bar = QLabel("就绪")
        self.status_bar.setStyleSheet("color:gray;font-size:11px;border-top:1px solid #eee;padding:5px 2px;")
        root.addWidget(self.status_bar)

    # ---------- 数据加载 ----------
    def reload(self):
        self.status_bar.setText("加载中…")
        self._load_installed()
        self._load_catalog()
        self._rebuild_cards()
        total = len(self.installed)
        self.status_bar.setText(f"已安装 {total} 个技能；发现 {len(self.catalog) + len(self.search_results)} 个可装项")

    def _load_installed(self):
        self.installed = {}
        for d in config.get_skill_scan_dirs():
            try:
                for sk in scan_skills(d):
                    norm = normalize_skill_name(sk.name)
                    if norm in self.installed:
                        continue
                    removable = os.path.abspath(sk.file_path).startswith(os.path.abspath(USER_SKILLS_DIR))
                    self.installed[norm] = {
                        "name": sk.name,
                        "emoji": sk.emoji or "📦",
                        "description": sk.description or sk.name,
                        "category": sk.category or "未分类",
                        "path": sk.file_path,
                        "removable": removable,
                        "source": "我的技能" if removable else "内置",
                    }
            except Exception as e:
                log.warning("技能扫描失败 %s: %s", d, e)

    def _load_catalog(self):
        """合并：用户清单(skills_catalog.json) + 内置示例清单，去掉已装的。"""
        items = []
        # 用户清单
        if os.path.exists(CATALOG_PATH):
            try:
                with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    items += data
            except Exception as e:
                log.warning("读取技能清单失败: %s", e)
        # 内置示例（可发现但未安装；无外部链接，纯占位引导，用户可在清单里加真实链接）
        items += DEFAULT_CATALOG
        # 去重（按 name）+ 去掉已安装
        seen = set()
        out = []
        for it in items:
            name = (it.get("name") or "").strip()
            if not name:
                continue
            norm = normalize_skill_name(name)
            if norm in self.installed or norm in seen:
                continue
            seen.add(norm)
            out.append({
                "name": name,
                "emoji": it.get("emoji", "🧩"),
                "description": it.get("description", ""),
                "category": it.get("category", "未分类"),
                "source_url": it.get("source_url") or "",
                "from_catalog": True,
            })
        self.catalog = out

    # ---------- 卡片渲染 ----------
    def _all_entries(self):
        """合并已安装 + 未安装可发现项（清单 + 搜索），供过滤渲染。"""
        entries = []
        for norm, info in self.installed.items():
            e = dict(info)
            e["installed"] = True
            e["norm"] = norm
            entries.append(e)
        for c in self.catalog:
            e = dict(c)
            e["installed"] = False
            e["norm"] = normalize_skill_name(c["name"])
            entries.append(e)
        for s in self.search_results:
            e = dict(s)
            e["installed"] = False
            e["norm"] = normalize_skill_name(s["name"])
            entries.append(e)
        return entries

    def _rebuild_cards(self):
        self._suppress = True
        # 清旧卡片
        while self.cards_lay.count():
            w = self.cards_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._cards = []

        text = self.search_box.text().strip().lower()
        cat = self.cat_combo.currentText()

        entries = self._all_entries()
        # 分类下拉刷新（保留当前选择）
        cur = self.cat_combo.currentText()
        cats = sorted({e.get("category", "未分类") for e in entries})
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        self.cat_combo.addItem("全部分类")
        for c in cats:
            self.cat_combo.addItem(c)
        if cur in [self.cat_combo.itemText(i) for i in range(self.cat_combo.count())]:
            self.cat_combo.setCurrentText(cur)
        self.cat_combo.blockSignals(False)

        shown = 0
        for e in entries:
            if text and text not in (e["name"] + " " + e.get("description", "")).lower():
                continue
            if cat != "全部分类" and e.get("category", "未分类") != cat:
                continue
            card = self._make_card(e)
            self.cards_lay.addWidget(card)
            self._cards.append(card)
            shown += 1

        self.cards_lay.addStretch(1)
        self._suppress = False
        self.status_bar.setText(
            f"显示 {shown} 个；已安装 {len(self.installed)}；可发现 {len(self.catalog) + len(self.search_results)}"
        )

    def _make_card(self, e):
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet(
            "QFrame{background:#ffffff;border:1px solid #e6e8eb;border-radius:10px;}"
            "QFrame:hover{border-color:#6c5ce7;}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        name_lbl = QLabel(f"{e.get('emoji','📦')} {e['name']}")
        name_lbl.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        top.addWidget(name_lbl, 1)

        badge = QLabel(e.get("category", "未分类"))
        badge.setStyleSheet(
            "QLabel{background:#f0eefb;color:#6c5ce7;border-radius:10px;padding:2px 10px;font-size:11px;}"
        )
        top.addWidget(badge)

        # 状态/来源标签
        if e.get("installed"):
            st = QLabel("✅ 已安装" if self._is_enabled(e["name"]) else "⚪ 已禁用")
            st.setStyleSheet(f"QLabel{{color:{'#1b7a3d' if self._is_enabled(e['name']) else '#9aa0a6'};font-size:11px;}}")
        else:
            st = QLabel("🆕 可发现")
            st.setStyleSheet("QLabel{color:#b8860b;font-size:11px;}")
        top.addWidget(st)
        lay.addLayout(top)

        desc = QLabel(e.get("description", "") or "（无描述）")
        desc.setStyleSheet("font-size:12px;color:#444;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # 操作行
        act = QHBoxLayout()
        act.addStretch(1)
        if e.get("installed"):
            if e.get("removable"):
                uninst = QPushButton("🗑 卸载")
                uninst.setFixedHeight(30)
                uninst.setStyleSheet(
                    "QPushButton{background:#fff;color:#c0392b;border:1px solid #e6b0aa;"
                    "border-radius:8px;padding:0 12px;font-size:12px;}"
                    "QPushButton:hover{background:#fdecea;}")
                uninst.clicked.connect(lambda _=False, p=e["path"], n=e["name"]: self._uninstall(p, n))
                act.addWidget(uninst)
        else:
            url = e.get("source_url") or e.get("html_url") or ""
            install_btn = QPushButton("⬇️ 安装")
            install_btn.setFixedHeight(30)
            install_btn.setEnabled(bool(url))
            install_btn.setToolTip(url or "无来源链接（在线搜索结果需用仓库链接安装）")
            install_btn.setStyleSheet(
                "QPushButton{background:#6c5ce7;color:#fff;border:none;border-radius:8px;"
                "padding:0 16px;font-size:12px;font-weight:500;}"
                "QPushButton:hover{background:#5a4bd4;}"
                "QPushButton:disabled{background:#cfc8ef;}")
            install_btn.clicked.connect(lambda _=False, u=url: self._install(u))
            act.addWidget(install_btn)
        lay.addLayout(act)
        return card

    # ---------- 安装 ----------
    def _install(self, url):
        url = (url or "").strip()
        if not url:
            QMessageBox.information(self, "提示", "该技能没有可用的安装链接。\n在线搜索结果请用其仓库链接安装，或从「从链接安装」粘贴。")
            return
        self.status_bar.setText(f"安装中：{url[:60]} …")
        self.link_btn.setEnabled(False)
        self.search_online_btn.setEnabled(False)
        self._thread = InstallThread(self.cfg, config.APP_DIR, url)
        self._thread.done.connect(self._on_installed)
        self._thread.start()

    def _on_installed(self, ok, msg):
        self.link_btn.setEnabled(True)
        self.search_online_btn.setEnabled(True)
        QMessageBox.information(self, "安装结果", msg)
        self.reload()

    def _install_from_link(self):
        url = self.link_edit.text().strip()
        if not url:
            QMessageBox.information(self, "提示", "请先粘贴技能链接（GitHub 仓库页 / blob / tree / raw SKILL.md 直链）。")
            return
        self._install(url)

    def _add_link_to_catalog(self):
        url = self.link_edit.text().strip()
        if not url:
            QMessageBox.information(self, "提示", "请先在输入框粘贴技能链接。")
            return
        name = self.link_edit.text().strip().rstrip("/").split("/")[-1] or "自定义技能"
        item = {
            "name": name,
            "emoji": "🧩",
            "description": f"来自链接：{url}",
            "category": "我的清单",
            "source_url": url,
        }
        try:
            os.makedirs(os.path.dirname(CATALOG_PATH), exist_ok=True)
            data = []
            if os.path.exists(CATALOG_PATH):
                with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, list):
                data = []
            # 去重（同名或同链接）
            data = [d for d in data if d.get("source_url") != url and d.get("name") != name]
            data.append(item)
            with open(CATALOG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.status_bar.setText(f"已加入清单：{name}（重启Agent后仍在）")
            self.reload()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"写入清单失败：{e}")

    # ---------- 在线搜索 ----------
    def _online_search(self):
        q, ok = _get_text(self, "在线搜索技能", "输入方向关键词（如「短视频脚本」「PDF处理」「数据分析」）：")
        if not ok or not q.strip():
            return
        self.status_bar.setText(f"搜索中：{q} …")
        self.search_online_btn.setEnabled(False)
        try:
            import skill_installer_tools
            raw = skill_installer_tools.skill_search(q.strip())
        except Exception as e:  # noqa: BLE001
            raw = f"搜索失败：{e}"
        self.search_online_btn.setEnabled(True)

        # 解析 skill_search 的文本输出为结构化结果
        results = _parse_search_text(raw)
        if not results:
            QMessageBox.information(
                self, "搜索结果",
                f"{raw}\n\n💡 若网络不可达，可直接粘贴技能链接用「从链接安装」一键装（自动走国内镜像）。"
            )
            return
        self.search_results = results
        self.reload()
        self.status_bar.setText(f"在线搜索找到 {len(results)} 个候选，点「安装」即可装入")

    # ---------- 卸载 ----------
    def _uninstall(self, path, name):
        reply = QMessageBox.question(
            self, "确认卸载",
            f"确定卸载技能「{name}」吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            import os
            import shutil
            # path 可能是 技能名/SKILL.md（文件夹内的文件）或 旧式 xxx.py 文件。
            # 统一解析出要删的目标：SKILL.md 结构删整个技能文件夹；.py 删文件本身。
            target = path
            if os.path.isfile(path):
                parent = os.path.dirname(path)
                if os.path.basename(path).lower() == "skill.md" and os.path.dirname(parent) == os.path.abspath(USER_SKILLS_DIR):
                    target = parent
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            elif os.path.exists(target):
                os.remove(target)
            self.status_bar.setText(f"已卸载 {name}")
            self.reload()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"卸载失败：{e}")


# ---------- 工具函数 ----------
def _parse_search_text(raw):
    """把 skill_search 返回的文本解析为卡片 entry 列表。

    成功格式：
      🔍 找到 N 个候选技能仓库（按 stars 排序）：
      1. owner/repo ⭐123
         描述
         https://github.com/owner/repo
    """
    if not raw or raw.startswith(("失败", "搜索失败", "未找到")):
        return []
    results = []
    import re
    blocks = re.split(r"\n\s*\d+\.\s*", raw)
    for b in blocks[1:]:
        lines = [l.strip() for l in b.splitlines() if l.strip()]
        if not lines:
            continue
        first = lines[0]
        m = re.match(r"([^\s⭐]+)\s*⭐?\s*(\d*)", first)
        full_name = m.group(1) if m else first
        stars = m.group(2) if (m and m.group(2)) else ""
        desc = lines[1] if len(lines) > 1 else ""
        html = ""
        for l in lines:
            if l.startswith("http"):
                html = l
                break
        if not html:
            html = f"https://github.com/{full_name}"
        results.append({
            "name": full_name.split("/")[-1] or full_name,
            "emoji": "🌐",
            "description": desc,
            "category": "社区",
            "html_url": html,
            "stars": stars,
            "from_search": True,
        })
    return results


def _get_text(parent, title, label):
    """简单文本输入对话框，返回 (text, ok)。"""
    from PySide6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getText(parent, title, label)
    return text, ok


# 内置示例清单（无外部链接，纯引导；用户在 skills_catalog.json 添加真实可装项）。
# 已装的内置技能会在 installed 中自动出现，无需在此列。
DEFAULT_CATALOG = []


# ---------- 单实例窗口管理 ----------
_open_market_windows = {}


def open_skill_market(cfg):
    """打开技能市场窗口（单实例，再次点击激活已有）。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    for win in list(_open_market_windows.values()):
        if win.isVisible():
            win.raise_()
            win.activateWindow()
            return win
    win = SkillMarketWindow(cfg)
    win.show()
    win.raise_()
    win.activateWindow()
    wid = id(win)
    _open_market_windows[wid] = win
    win.destroyed.connect(lambda obj, w=wid: _open_market_windows.pop(w, None))
    return win


if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    open_skill_market({})
    app.exec()
