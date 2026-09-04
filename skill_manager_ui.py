"""
插件市场/可视化技能管理模块 v1.1（v4.67 大修）
PySide6 UI 面板管理技能安装/卸载/排序/开关。

v4.67 改进：
- 勾选框即时生效（去掉冗余的「启用/禁用」按钮，勾上即存盘）
- 详情面板修复：选中即显示名称/描述/来源/分类/状态/路径/装入时间
- 搜索框（按名/描述实时过滤）+ 分类下拉 + 状态下拉
- 「导入技能」按钮（选文件夹复制到用户 skills 目录）
- 「打开目录」按钮（直接打开用户 skills 目录）
- 状态视觉区分（启用绿字✅ / 禁用灰字⚪）
- 默认全启用显示（enabled_skills 为空 = 全部启用，向后兼容）

使用方式：
    from skill_manager_ui import SkillManagerWindow
    window = SkillManagerWindow(cfg)
    window.show()
"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QMessageBox, QGroupBox, QApplication, QLineEdit, QComboBox, QFileDialog
)
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QFont, QColor, QDesktopServices
import os
import shutil
import logging
from datetime import datetime
from pathlib import Path
import config
from skill_loader import scan_skills

log = logging.getLogger(__name__)

# 视觉配色
_COLOR_ON = QColor("#1b7a3d")    # 启用：绿
_COLOR_OFF = QColor("#9aa0a6")   # 禁用：灰


class SkillLoaderThread(QThread):
    """异步加载技能列表，避免阻塞 UI"""
    skills_loaded = Signal(list)

    def __init__(self):
        super().__init__()

    def run(self):
        """扫描所有技能目录，复用 skill_loader 的双格式（.py / SKILL.md）解析。

        跨多目录（内置/打包 skills、用户目录、自定义 skills_dir）合并去重，
        与对话侧 load_dynamic_skills() 共用同一套扫描逻辑，保持一致。
        每个技能附加 source（内置/我的技能/自定义）与 category 标签。
        """
        skills = []
        seen = set()
        try:
            for d in config.get_skill_scan_dirs():
                if "小臭玩AI" in d and "Documents" in d:
                    source = "我的技能"
                elif os.path.basename(d.rstrip("/\\")) == "skills" and "小臭玩AI" not in d:
                    source = "内置"
                else:
                    source = "自定义"
                for sk in scan_skills(d):
                    if sk.name in seen:
                        continue
                    seen.add(sk.name)
                    skills.append({
                        "name": sk.name,
                        "path": sk.file_path,
                        "description": sk.description or sk.name,
                        "emoji": sk.emoji or "📦",
                        "category": sk.category or "未分类",
                        "source": source,
                    })
        except Exception as e:
            log.warning("技能扫描失败: %s", e)
        self.skills_loaded.emit(skills)


class SkillManagerWindow(QMainWindow):
    def __init__(self, cfg=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        # enabled_skills 为空表示「未配置」→ 全部启用（向后兼容）
        self.enabled_skills = list(self.cfg.get("enabled_skills", []) or [])
        self.all_skills = []          # 扫描得到的完整技能列表
        self._suppress = False        # 加载/重建期屏蔽 itemChanged 防递归
        self.init_ui()
        self.load_skills_async()

    # ---------- 启用判定（与对话侧 load_dynamic_skills 同一规则）----------
    def _is_enabled(self, name):
        if not self.enabled_skills:
            return True
        return name in self.enabled_skills

    def init_ui(self):
        """初始化 UI"""
        self.setWindowTitle("技能管理器 - 小臭玩 AI")
        self.setMinimumSize(900, 700)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 标题栏
        title_layout = QHBoxLayout()
        title = QLabel("🔌 技能管理器")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        title_layout.addWidget(title)
        title_layout.addStretch()

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.load_skills_async)
        title_layout.addWidget(refresh_btn)

        main_layout.addLayout(title_layout)

        # 统计信息
        self.stats_label = QLabel("加载中...")
        self.stats_label.setStyleSheet("color: gray; font-size: 12px;")
        main_layout.addWidget(self.stats_label)

        # 筛选栏：搜索 + 分类 + 状态 + 导入 + 打开目录
        filter_layout = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 搜索技能名 / 描述…")
        self.search_box.textChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.search_box, 3)

        self.cat_combo = QComboBox()
        self.cat_combo.addItem("全部分类")
        self.cat_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.cat_combo, 1)

        self.status_combo = QComboBox()
        self.status_combo.addItems(["全部", "仅已启用", "仅未启用"])
        self.status_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.status_combo, 1)

        import_btn = QPushButton("导入技能")
        import_btn.clicked.connect(self.import_skill)
        filter_layout.addWidget(import_btn)

        open_dir_btn = QPushButton("打开目录")
        open_dir_btn.clicked.connect(self.open_dir)
        filter_layout.addWidget(open_dir_btn)

        main_layout.addLayout(filter_layout)

        # 技能列表（左右分栏）
        list_layout = QHBoxLayout()

        left_panel = QGroupBox("已安装技能")
        left_layout = QVBoxLayout(left_panel)

        self.skill_list = QListWidget()
        self.skill_list.currentItemChanged.connect(self._on_current_changed)
        self.skill_list.itemChanged.connect(self.on_item_changed)
        left_layout.addWidget(self.skill_list)

        right_panel = QGroupBox("技能详情")
        right_layout = QVBoxLayout(right_panel)

        self.detail_name = QLabel("未选择技能")
        self.detail_name.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        right_layout.addWidget(self.detail_name)

        self.detail_desc = QLabel("")
        self.detail_desc.setStyleSheet("font-size: 11px; color: #333;")
        self.detail_desc.setWordWrap(True)
        right_layout.addWidget(self.detail_desc)

        self.detail_meta = QLabel("")
        self.detail_meta.setStyleSheet("font-size: 10px; color: #888;")
        self.detail_meta.setWordWrap(True)
        right_layout.addWidget(self.detail_meta)

        right_layout.addStretch()

        list_layout.addWidget(left_panel, 2)
        list_layout.addWidget(right_panel, 1)

        main_layout.addLayout(list_layout)

        # 底部按钮组：仅保留卸载（勾选即时生效，启用/禁用按钮已移除）
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_uninstall = QPushButton("卸载")
        self.btn_uninstall.clicked.connect(self.uninstall_selected_skill)
        self.btn_uninstall.setEnabled(False)
        button_layout.addWidget(self.btn_uninstall)

        main_layout.addLayout(button_layout)

        # 状态栏
        self.status_bar = QLabel("就绪")
        self.status_bar.setStyleSheet("color: gray; font-size: 11px; border-top: 1px solid #eee; padding: 5px;")
        main_layout.addWidget(self.status_bar)

    # ---------- 加载 ----------
    def load_skills_async(self):
        """异步加载技能列表"""
        self.skill_list.clear()
        self.stats_label.setText("加载中...")
        self.loader_thread = SkillLoaderThread()
        self.loader_thread.skills_loaded.connect(self._on_skills_loaded)
        self.loader_thread.start()

    def _on_skills_loaded(self, skills):
        """技能加载完成回调"""
        self.all_skills = skills

        # 刷新分类下拉（保留当前选择）
        cur_cat = self.cat_combo.currentText()
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        self.cat_combo.addItem("全部分类")
        for c in sorted({s["category"] for s in skills}):
            self.cat_combo.addItem(c)
        if cur_cat in [self.cat_combo.itemText(i) for i in range(self.cat_combo.count())]:
            self.cat_combo.setCurrentText(cur_cat)
        self.cat_combo.blockSignals(False)

        self._apply_filter()

    # ---------- 过滤 + 重建列表 ----------
    def _apply_filter(self):
        """根据搜索框 / 分类 / 状态过滤并重建列表"""
        self._suppress = True
        self.skill_list.clear()

        text = self.search_box.text().strip().lower()
        cat = self.cat_combo.currentText()
        status = self.status_combo.currentText()

        for sk in self.all_skills:
            if text and text not in (sk["name"] + " " + sk["description"]).lower():
                continue
            if cat != "全部分类" and sk["category"] != cat:
                continue
            if status == "仅已启用" and not self._is_enabled(sk["name"]):
                continue
            if status == "仅未启用" and self._is_enabled(sk["name"]):
                continue

            item = QListWidgetItem()
            enabled = self._is_enabled(sk["name"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            item.setData(Qt.UserRole, sk)
            self._decorate_item(item, enabled)
            self.skill_list.addItem(item)

        self._suppress = False

        # 默认选中第一项并刷新详情
        if self.skill_list.count() > 0:
            self.skill_list.setCurrentRow(0)
        else:
            self._clear_detail()

        self._update_stats()

    def _decorate_item(self, item, enabled):
        """设置列表项文本与颜色（启用绿/禁用灰）"""
        sk = item.data(Qt.UserRole)
        icon = "✅" if enabled else "⚪"
        item.setText(f"{icon} {sk['emoji']} {sk['name']}")
        item.setForeground(_COLOR_ON if enabled else _COLOR_OFF)

    def _update_stats(self):
        total = len(self.all_skills)
        on = sum(1 for s in self.all_skills if self._is_enabled(s["name"]))
        self.stats_label.setText(f"共 {total} 个技能，已启用 {on} 个")
        self.status_bar.setText(f"已加载 {total} 个技能")

    # ---------- 选中 / 详情 ----------
    def _on_current_changed(self, current, previous):
        if current is not None:
            sk = current.data(Qt.UserRole)
            self._show_detail(sk, self._is_enabled(sk["name"]))
        else:
            self._clear_detail()

    def _show_detail(self, skill, enabled):
        if not skill:
            self._clear_detail()
            return
        self.detail_name.setText(f"{skill['emoji']} {skill['name']}")

        desc = skill.get("description", "无描述")
        self.detail_desc.setText(desc)

        # 装入时间（目录/文件的 mtime）
        mtime = ""
        try:
            t = os.path.getmtime(skill["path"])
            mtime = datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

        status_txt = "✅ 已启用" if enabled else "⚪ 已禁用"
        meta = (
            f"状态：{status_txt}\n"
            f"来源：{skill.get('source', '未知')}\n"
            f"分类：{skill.get('category', '未分类')}\n"
            f"装入时间：{mtime or '未知'}\n"
            f"路径：{skill['path']}"
        )
        self.detail_meta.setText(meta)

        self.btn_uninstall.setEnabled(True)

    def _clear_detail(self):
        self.detail_name.setText("未选择技能")
        self.detail_desc.setText("")
        self.detail_meta.setText("")
        self.btn_uninstall.setEnabled(False)

    # ---------- 勾选即时生效 ----------
    def on_item_changed(self, item):
        """勾选/取消勾选立即生效并存盘（去掉了启用/禁用按钮）"""
        if self._suppress:
            return
        skill = item.data(Qt.UserRole)
        if not skill:
            return

        name = skill["name"]
        checked = item.checkState() == Qt.Checked
        currently = self._is_enabled(name)
        if checked == currently:
            return  # 无变化（含装饰触发的递归），跳过

        # 首次改动：把白名单初始化为「当前全部」，再应用本次操作
        if not self.enabled_skills:
            self.enabled_skills = [s["name"] for s in self.all_skills]

        if checked:
            if name not in self.enabled_skills:
                self.enabled_skills.append(name)
        else:
            if name in self.enabled_skills:
                self.enabled_skills.remove(name)

        self._save_config()
        self._update_stats()

        # 刷新该项视觉（屏蔽本次装饰触发的 itemChanged）
        self._suppress = True
        self._decorate_item(item, checked)
        self._suppress = False

        # 若当前选中项即此项，刷新详情状态
        if self.skill_list.currentItem() is item:
            self._show_detail(skill, checked)

    # ---------- 存盘 ----------
    def _save_config(self):
        """保存配置（落盘 config.json，启用/禁用状态重启不丢失）"""
        self.cfg["enabled_skills"] = self.enabled_skills
        try:
            config.save_config(self.cfg)
        except Exception as e:
            self.status_bar.setText(f"保存失败: {e}")
            return
        self.status_bar.setText(f"已保存配置（{len(self.enabled_skills)} 个技能启用）")

    # ---------- 导入 / 打开目录 ----------
    def _user_skills_dir(self):
        d = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "skills")
        os.makedirs(d, exist_ok=True)
        return d

    def import_skill(self):
        """选择本地技能文件夹，复制到用户 skills 目录"""
        src = QFileDialog.getExistingDirectory(self, "选择技能文件夹（内含 SKILL.md 或 .py）")
        if not src:
            return
        user_dir = self._user_skills_dir()
        name = os.path.basename(src.rstrip("/\\"))
        dst = os.path.join(user_dir, name)
        if os.path.exists(dst):
            QMessageBox.warning(self, "提示", f"目标已存在：\n{dst}\n\n请先删除或重命名后再导入。")
            return
        try:
            shutil.copytree(src, dst)
            self.status_bar.setText(f"已导入技能到 {dst}")
            self.load_skills_async()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入失败：{e}")

    def open_dir(self):
        """在资源管理器打开用户 skills 目录"""
        user_dir = self._user_skills_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(user_dir))

    # ---------- 卸载 ----------
    def uninstall_selected_skill(self):
        """卸载选中的技能"""
        current = self.skill_list.currentItem()
        if not current:
            return

        skill_info = current.data(Qt.UserRole)
        skill_name = skill_info["name"]

        reply = QMessageBox.question(
            self, "确认卸载",
            f"确定要卸载技能 '{skill_name}' 吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            skill_path = Path(skill_info["path"])
            if skill_path.exists():
                try:
                    shutil.rmtree(skill_path)

                    # 从内存列表与启用列表移除
                    self.all_skills = [s for s in self.all_skills if s["name"] != skill_name]
                    if skill_name in self.enabled_skills:
                        self.enabled_skills.remove(skill_name)
                        self._save_config()

                    self.status_bar.setText(f"已卸载 {skill_name}")
                    self._apply_filter()  # 重建列表（保留筛选状态）

                except Exception as e:
                    QMessageBox.critical(self, "错误", f"卸载失败: {e}")
            else:
                QMessageBox.warning(self, "警告", f"技能目录不存在: {skill_path}")


# 模块级缓存，防止 SkillManagerWindow 被 GC 回收导致崩溃
_open_mgr_windows = {}


def open_skill_manager(cfg):
    """打开技能管理器窗口（供 ui.py / main.py 调用）
    同一时刻只保持一个实例，再次点击时激活已有窗口。
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    # 如果已有窗口且可见，直接激活
    for win in list(_open_mgr_windows.values()):
        if win.isVisible():
            win.raise_()
            win.activateWindow()
            return win

    win = SkillManagerWindow(cfg)
    win.show()
    win.raise_()
    win.activateWindow()

    # 持有一个引用防止 GC；窗口销毁时自动清理
    wid = id(win)
    _open_mgr_windows[wid] = win
    win.destroyed.connect(lambda obj, w=wid: _open_mgr_windows.pop(w, None))

    return win


def test_ui():
    """独立运行测试"""
    app = QApplication([])
    window = SkillManagerWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    test_ui()
