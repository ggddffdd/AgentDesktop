# -*- coding: utf-8 -*-
"""工作流模板管理器（v4.68 新增）

把常用的多步任务固化成模板，面板里点某一步的「▶ 执行」即可把该步提示词
作为用户消息发到当前聊天会话（可选强制走 Agent 多工具执行）。

数据存到 ~/Documents/小臭玩AI/workflow_templates.json（独立文件，不污染 config.json）。
面板打开方式：工具栏「⚙️ 工作流」按钮 / 托盘菜单 / 快捷键 Ctrl+Alt+W。
"""
import os
import json
import uuid
import config as cfg_mod_wf
import logging
from PySide6.QtWidgets import (
    QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QLineEdit, QPushButton, QLabel, QTextEdit, QComboBox,
    QMessageBox, QFileDialog, QDialogButtonBox, QFormLayout, QCheckBox,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices

log = logging.getLogger("workflow_manager")

WF_DIR = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI")
WF_PATH = os.path.join(WF_DIR, "workflow_templates.json")

CATEGORIES = ["内容创作", "视频创作", "小说创作", "营销运营", "日常助手", "其他"]


# ============ 持久化 ============
def load_templates():
    try:
        if not os.path.exists(WF_PATH):
            data = default_templates()
            save_templates(data["templates"])
            return data["templates"]
        with open(WF_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        tpls = data.get("templates", [])
        if not isinstance(tpls, list):
            tpls = []
        return tpls
    except Exception as e:
        log.warning("读取工作流模板失败: %s", e)
        return []


def save_templates(templates):
    try:
        os.makedirs(WF_DIR, exist_ok=True)
        with open(WF_PATH, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "templates": templates}, f,
                      ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log.warning("保存工作流模板失败: %s", e)
        return False


def default_templates():
    return {"version": 1, "templates": [
        {"id": str(uuid.uuid4()), "name": "公众号养生文", "emoji": "📝",
         "category": "内容创作", "description": "确定主题 → 写800字正文 → 配图提示词",
         "steps": [
            {"title": "选今日养生主题", "prompt": "帮我选一个今天适合的公众号养生主题，侧重中老年日常保健，避免敏感医疗建议，给3个候选并推荐1个", "force_agent": False},
            {"title": "写800字正文", "prompt": "按推荐主题写一篇800字左右养生科普正文，飞书文档式排版（小标题+要点），语气平和可信，文末加AI生成内容标注", "force_agent": False},
            {"title": "配图提示词", "prompt": "为这篇养生文生成3个配图提示词（中式插画风、温暖治愈），直接给提示词", "force_agent": False},
         ]},
        {"id": str(uuid.uuid4()), "name": "中式恐怖短视频", "emoji": "👻",
         "category": "视频创作", "description": "30秒脚本分镜 → 配图提示词 → 配音旁白 → 生图",
         "steps": [
            {"title": "30秒脚本分镜", "prompt": "写一个30秒中式恐怖短视频脚本，分5个镜头，每镜含画面描述+旁白，结尾留钩子", "force_agent": False},
            {"title": "配图提示词", "prompt": "把上面5个分镜各写成一条生图提示词（中式恐怖、暗调、民俗元素），直接给提示词列表", "force_agent": False},
            {"title": "配音旁白稿", "prompt": "把脚本旁白整理成一段可直接配音的文稿（约80字），口语化、有氛围", "force_agent": False},
            {"title": "生成分镜图（Agent）", "prompt": "用 image 工具为这5个分镜各生成一张中式恐怖风格配图，尺寸9:16", "force_agent": True},
         ]},
        {"id": str(uuid.uuid4()), "name": "小说三问", "emoji": "📖",
         "category": "小说创作", "description": "选题三问验证 → 写首章带钩子 → 钩子密度检查",
         "steps": [
            {"title": "选题三问验证", "prompt": "用小说选题三问（写给谁/爽点在哪/前3章钩子）验证我今天这个灵感，指出风险", "force_agent": False},
            {"title": "写首章500字", "prompt": "按验证后的方向写第一章约500字，第一人称'我'，每500字一个钩子", "force_agent": False},
            {"title": "钩子密度检查", "prompt": "检查上面章节的钩子密度，标出可以加钩子的位置并给改写建议", "force_agent": False},
         ]},
    ]}


# ============ 步骤编辑器 ============
class StepEditor(QDialog):
    def __init__(self, step=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑步骤" if step else "添加步骤")
        self.resize(480, 320)
        step = step or {"title": "", "prompt": "", "force_agent": False}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(step.get("title", ""))
        self.title_edit.setPlaceholderText("例如：选今日养生主题")
        self.prompt_edit = QTextEdit(step.get("prompt", ""))
        self.prompt_edit.setPlaceholderText("发给小臭的提示词（支持换行）")
        self.force_chk = QCheckBox("强制走 Agent（多工具执行，如生图/联网）")
        self.force_chk.setChecked(bool(step.get("force_agent", False)))
        form.addRow("步骤标题", self.title_edit)
        form.addRow("提示词", self.prompt_edit)
        layout.addLayout(form)
        layout.addWidget(self.force_chk)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_step(self):
        return {
            "title": self.title_edit.text().strip(),
            "prompt": self.prompt_edit.toPlainText().strip(),
            "force_agent": self.force_chk.isChecked(),
        }


# ============ 模板编辑器 ============
class TemplateEditor(QDialog):
    def __init__(self, template=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑工作流模板" if template else "新建工作流模板")
        self.resize(560, 540)
        tpl = template or {"id": str(uuid.uuid4()), "name": "", "emoji": "⚙️",
                           "category": CATEGORIES[0], "description": "", "steps": []}
        self._tpl = dict(tpl)
        self._steps = [dict(s) for s in tpl.get("steps", [])]

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(tpl.get("name", ""))
        self.name_edit.setPlaceholderText("例如：公众号养生文")
        self.emoji_edit = QLineEdit(tpl.get("emoji", "⚙️"))
        self.emoji_edit.setFixedWidth(48)
        self.cat_combo = QComboBox()
        self.cat_combo.addItems(CATEGORIES)
        if tpl.get("category") in CATEGORIES:
            self.cat_combo.setCurrentText(tpl["category"])
        self.desc_edit = QLineEdit(tpl.get("description", ""))
        self.desc_edit.setPlaceholderText("一句话说明流程，如：选主题→写正文→配图")
        form.addRow("名称", self.name_edit)
        hb = QHBoxLayout()
        hb.addWidget(QLabel("图标"))
        hb.addWidget(self.emoji_edit)
        hb.addWidget(QLabel("分类"))
        hb.addWidget(self.cat_combo)
        layout.addLayout(form)
        layout.addLayout(hb)
        layout.addWidget(QLabel("简介"))
        layout.addWidget(self.desc_edit)

        layout.addWidget(QLabel("步骤（按顺序执行）"))
        self.step_list = QListWidget()
        self._fill_steps()
        layout.addWidget(self.step_list, 1)

        step_btn = QHBoxLayout()
        b_add = QPushButton("➕ 添加步骤")
        b_edit = QPushButton("✏️ 编辑")
        b_del = QPushButton("🗑 删除")
        b_up = QPushButton("↑ 上移")
        b_down = QPushButton("↓ 下移")
        b_add.clicked.connect(self._add_step)
        b_edit.clicked.connect(self._edit_step)
        b_del.clicked.connect(self._del_step)
        b_up.clicked.connect(self._up_step)
        b_down.clicked.connect(self._down_step)
        step_btn.addWidget(b_add)
        step_btn.addWidget(b_edit)
        step_btn.addWidget(b_del)
        step_btn.addWidget(b_up)
        step_btn.addWidget(b_down)
        layout.addLayout(step_btn)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _fill_steps(self):
        self.step_list.clear()
        for i, s in enumerate(self._steps, 1):
            tag = "  [Agent]" if s.get("force_agent") else ""
            self.step_list.addItem(f"{i}. {s.get('title', '')}{tag}")

    def _add_step(self):
        dlg = StepEditor(None, self)
        if dlg.exec() == QDialog.Accepted:
            st = dlg.get_step()
            if st["title"] or st["prompt"]:
                self._steps.append(st)
                self._fill_steps()

    def _edit_step(self):
        row = self.step_list.currentRow()
        if row < 0:
            return
        dlg = StepEditor(self._steps[row], self)
        if dlg.exec() == QDialog.Accepted:
            st = dlg.get_step()
            if st["title"] or st["prompt"]:
                self._steps[row] = st
                self._fill_steps()

    def _del_step(self):
        row = self.step_list.currentRow()
        if row < 0:
            return
        self._steps.pop(row)
        self._fill_steps()

    def _up_step(self):
        row = self.step_list.currentRow()
        if row > 0:
            self._steps[row - 1], self._steps[row] = self._steps[row], self._steps[row - 1]
            self._fill_steps()
            self.step_list.setCurrentRow(row - 1)

    def _down_step(self):
        row = self.step_list.currentRow()
        if 0 <= row < len(self._steps) - 1:
            self._steps[row + 1], self._steps[row] = self._steps[row], self._steps[row + 1]
            self._fill_steps()
            self.step_list.setCurrentRow(row + 1)

    def _on_ok(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写模板名称")
            return
        self._tpl["name"] = self.name_edit.text().strip()
        self._tpl["emoji"] = self.emoji_edit.text().strip() or "⚙️"
        self._tpl["category"] = self.cat_combo.currentText()
        self._tpl["description"] = self.desc_edit.text().strip()
        self._tpl["steps"] = [dict(s) for s in self._steps]
        self.accept()

    def get_template(self):
        return self._tpl


# ============ 主面板 ============
class WorkflowManagerWindow(QWidget):
    def __init__(self, cfg, main_window=None):
        super().__init__()
        self.cfg = cfg or {}
        self.main_window = main_window
        self._templates = load_templates()
        self._current_id = None
        self.setWindowTitle("⚙️ 工作流模板")
        self.resize(820, 560)
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        root = QVBoxLayout(self)
        # 顶部：搜索 + 分类
        top = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 搜索模板名称 / 描述…")
        self.search_edit.textChanged.connect(self._apply_filter)
        self.cat_combo = QComboBox()
        self.cat_combo.addItem("全部分类")
        self.cat_combo.addItems(CATEGORIES)
        self.cat_combo.currentTextChanged.connect(self._apply_filter)
        top.addWidget(self.search_edit, 1)
        top.addWidget(self.cat_combo)
        root.addLayout(top)

        # 主体：列表 + 详情
        body = QHBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.setFixedWidth(280)
        self.list_widget.currentItemChanged.connect(self._on_select)
        body.addWidget(self.list_widget)

        self.detail_area = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_area)
        self.detail_layout.setContentsMargins(12, 12, 12, 12)
        body.addWidget(self.detail_area, 1)
        root.addLayout(body)

        # 底部按钮栏
        bar = QHBoxLayout()
        b_new = QPushButton("➕ 新建")
        b_edit = QPushButton("✏️ 编辑")
        b_del = QPushButton("🗑 删除")
        b_imp = QPushButton("📥 导入")
        b_exp = QPushButton("📤 导出")
        b_dir = QPushButton("📂 打开目录")
        b_prod = QPushButton("📂 打开产物")
        b_new.clicked.connect(self._new_template)
        b_edit.clicked.connect(self._edit_template)
        b_del.clicked.connect(self._delete_template)
        b_imp.clicked.connect(self._import)
        b_exp.clicked.connect(self._export)
        b_dir.clicked.connect(self._open_dir)
        b_prod.clicked.connect(self._open_products)
        for b in (b_new, b_edit, b_del, b_imp, b_exp, b_dir, b_prod):
            bar.addWidget(b)
        root.addLayout(bar)

    def _refresh_list(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for t in self._templates:
            item = QListWidgetItem(f"{t.get('emoji', '⚙️')} {t.get('name', '未命名')}")
            item.setData(Qt.UserRole, t.get("id"))
            item.setToolTip(f"分类：{t.get('category', '')}｜{t.get('description', '')}")
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        self._apply_filter()
        if self.list_widget.count() and self.list_widget.currentRow() < 0:
            self.list_widget.setCurrentRow(0)

    def _apply_filter(self):
        text = self.search_edit.text().strip().lower()
        cat = self.cat_combo.currentText()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            tid = item.data(Qt.UserRole)
            t = self._find(tid)
            if not t:
                item.setHidden(True)
                continue
            hit_text = (text in t.get("name", "").lower()) or (text in t.get("description", "").lower())
            hit_cat = (cat == "全部分类") or (t.get("category") == cat)
            item.setHidden(not (hit_text and hit_cat))
        cur = self.list_widget.currentItem()
        if cur is None or cur.isHidden():
            for i in range(self.list_widget.count()):
                if not self.list_widget.item(i).isHidden():
                    self.list_widget.setCurrentRow(i)
                    break

    def _find(self, tid):
        for t in self._templates:
            if t.get("id") == tid:
                return t
        return None

    def _on_select(self, cur, prev):
        if cur is None:
            return
        tid = cur.data(Qt.UserRole)
        self._current_id = tid
        self._show_detail(self._find(tid))

    def _show_detail(self, t):
        self._clear_layout(self.detail_layout)
        if not t:
            self.detail_layout.addWidget(QLabel("（未选择模板）"))
            return
        title = QLabel(f"<h3 style='margin:0'>{t.get('emoji', '⚙️')} {t.get('name', '未命名')}</h3>")
        self.detail_layout.addWidget(title)
        meta = QLabel(f"<span style='color:#888'>分类：{t.get('category', '')}｜步骤数：{len(t.get('steps', []))}</span>")
        self.detail_layout.addWidget(meta)
        if t.get("description"):
            desc = QLabel(t["description"])
            desc.setWordWrap(True)
            self.detail_layout.addWidget(desc)
        self.detail_layout.addWidget(QLabel("<b>步骤（点 ▶ 执行，发到当前会话）</b>"))
        for i, s in enumerate(t.get("steps", []), 1):
            row = QHBoxLayout()
            tag = "  <span style='color:#c0392b'>[Agent]</span>" if s.get("force_agent") else ""
            label = QLabel(f"{i}. {s.get('title', '')}{tag}")
            label.setWordWrap(True)
            run_btn = QPushButton("▶ 执行")
            run_btn.setFixedWidth(72)
            run_btn.clicked.connect(lambda _checked=False, step=s: self._run_step(step))
            if s.get("_done"):
                run_btn.setText("✓ 已执行")
                run_btn.setEnabled(False)
            row.addWidget(label, 1)
            row.addWidget(run_btn)
            self.detail_layout.addLayout(row)
        self.detail_layout.addStretch(1)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            else:
                sub = item.layout()
                if sub:
                    self._clear_layout(sub)

    def _run_step(self, step):
        if self.main_window is None or not hasattr(self.main_window, "send_user_prompt"):
            QMessageBox.warning(self, "提示", "未能关联到主窗口，请通过工具栏/托盘/快捷键重新打开本面板")
            return
        prompt = (step.get("prompt") or "").strip()
        if not prompt:
            QMessageBox.information(self, "提示", "这一步没有提示词")
            return
        self.main_window.send_user_prompt(prompt, force_agent=bool(step.get("force_agent", False)))
        step["_done"] = True
        self._show_detail(self._find(self._current_id))
        QMessageBox.information(self, "已发送", f"已把「{step.get('title', '')}」发到当前会话，去聊天窗口看结果～")

    def _new_template(self):
        dlg = TemplateEditor(None, self)
        if dlg.exec() == QDialog.Accepted:
            self._templates.append(dlg.get_template())
            save_templates(self._templates)
            self._refresh_list()

    def _edit_template(self):
        t = self._find(self._current_id)
        if not t:
            QMessageBox.information(self, "提示", "请先选择一个模板")
            return
        dlg = TemplateEditor(t, self)
        if dlg.exec() == QDialog.Accepted:
            updated = dlg.get_template()
            for i, x in enumerate(self._templates):
                if x.get("id") == updated.get("id"):
                    self._templates[i] = updated
                    break
            save_templates(self._templates)
            self._refresh_list()

    def _delete_template(self):
        t = self._find(self._current_id)
        if not t:
            return
        if QMessageBox.question(self, "删除", f"确定删除模板「{t.get('name', '')}」？") == QMessageBox.Yes:
            self._templates = [x for x in self._templates if x.get("id") != t.get("id")]
            save_templates(self._templates)
            self._refresh_list()

    def _export(self):
        t = self._find(self._current_id)
        if not t:
            QMessageBox.information(self, "提示", "请先选择一个模板")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出模板", f"{t.get('name', 'template')}.json", "JSON (*.json)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(t, f, ensure_ascii=False, indent=2)
                QMessageBox.information(self, "已导出", f"已导出到：{path}")
            except Exception as e:
                QMessageBox.warning(self, "导出失败", str(e))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入模板", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"读取文件出错：{e}")
            return
        if isinstance(data, dict) and "steps" in data:
            tpl = data
        elif isinstance(data, list):
            tpl = {"id": str(uuid.uuid4()), "name": "导入模板", "emoji": "⚙️",
                   "category": "其他", "description": "", "steps": data}
        else:
            QMessageBox.warning(self, "导入失败", "文件格式不对")
            return
        tpl["id"] = str(uuid.uuid4())
        tpl.setdefault("name", "导入模板")
        tpl.setdefault("emoji", "⚙️")
        tpl.setdefault("category", "其他")
        tpl.setdefault("description", "")
        tpl.setdefault("steps", [])
        self._templates.append(tpl)
        save_templates(self._templates)
        self._refresh_list()
        QMessageBox.information(self, "已导入", f"已导入模板「{tpl.get('name', '')}」")

    def _open_dir(self):
        try:
            os.makedirs(WF_DIR, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(WF_DIR))
        except Exception as e:
            QMessageBox.warning(self, "打开失败", str(e))

    def _open_products(self):
        """一键打开统一产物目录（~/Documents/小臭玩AI/产物），
        图片在「图片」、截图在「截图」、视频在「视频」子目录。"""
        d = getattr(cfg_mod_wf, "PRODUCTS_DIR", None) or os.path.join(
            os.path.expanduser("~"), "Documents", "小臭玩AI", "产物")
        try:
            os.makedirs(d, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))
        except Exception as e:
            QMessageBox.warning(self, "打开失败", str(e))

    def closeEvent(self, event):
        _open_wf_windows.pop(id(self), None)
        super().closeEvent(event)


# ============ 打开入口 ============
_open_wf_windows = {}


def open_workflow_manager(cfg, main_window=None):
    """打开工作流模板窗口（供 ui.py / main.py 调用）
    同一时刻只保持一个实例，再次触发时激活已有窗口。
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    for win in list(_open_wf_windows.values()):
        if win.isVisible():
            win.raise_()
            win.activateWindow()
            return win
    win = WorkflowManagerWindow(cfg, main_window)
    win.show()
    win.raise_()
    win.activateWindow()
    _open_wf_windows[id(win)] = win
    return win
