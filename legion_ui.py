# -*- coding: utf-8 -*-
"""Agent 军团面板 v4.121

把「编排页」从单一小说流水线扩展成**可自定义团队的多项目军团**：

- 左栏：项目列表（多项目并存）+「+ 添加团队」
- 右栏：当前项目的波次编排（波内并行、波间串行）+ 成员增删改移
- 底部：给军团下任务 → 启动 → 实时日志 + 产出

成员定义沿用 openclaw-multi-agent-team 的结构化角色 prompt 思路（7 要素），
由 legion.build_role_prompt() 拼成 system prompt，交给 agent_node.AgentNode 执行。
"""
import copy
import logging

from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QListWidget,
    QListWidgetItem, QLineEdit, QPushButton, QLabel, QTextEdit, QComboBox,
    QMessageBox, QGroupBox, QScrollArea, QDialogButtonBox, QAbstractItemView,
    QSizePolicy,
)
from PySide6.QtCore import Qt

import legion
from legion_worker import LegionWorker

log = logging.getLogger("legion")


# ============ 角色编辑器（7 要素）============
class RoleEditor(QDialog):
    """团队成员编辑器：把角色拆成 7 个要素填，避免 prompt 写成一坨糊话。"""

    def __init__(self, role=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("团队成员 · 角色定义")
        self.resize(640, 820)
        r = role or legion.new_role()

        self.e_emoji = QLineEdit(r.get("emoji", ""))
        self.e_emoji.setFixedWidth(64)
        self.e_emoji.setPlaceholderText("🔍")
        self.e_name = QLineEdit(r.get("name", ""))
        row_name = QHBoxLayout()
        row_name.addWidget(self.e_emoji)
        row_name.addWidget(self.e_name, 1)

        self.e_mission = QTextEdit(r.get("mission", ""))
        self.e_mission.setFixedHeight(64)
        self.e_mission.setPlaceholderText("这个角色负责干什么，一句话说清")

        self.e_constraints = QTextEdit(r.get("constraints", ""))
        self.e_constraints.setFixedHeight(52)
        self.e_constraints.setPlaceholderText("不能做什么 / 必须遵守什么")

        self.e_tools = QListWidget()
        self.e_tools.setSelectionMode(QAbstractItemView.MultiSelection)
        for t in legion.TOOL_CANDIDATES:
            it = QListWidgetItem(t)
            self.e_tools.addItem(it)
            if t in (r.get("tools") or []):
                it.setSelected(True)
        self.e_tools.setFixedHeight(108)
        self.e_tools.setToolTip("可多选。留空 = 该角色不使用工具，直接输出文本。")

        self.e_model = QLineEdit(r.get("model", ""))
        self.e_model.setPlaceholderText("留空 = 跟随全局模型配置")

        self.e_output = QTextEdit(r.get("output_format", ""))
        self.e_output.setFixedHeight(52)
        self.e_output.setPlaceholderText("产出长什么样（格式要求）")

        self.e_quality = QTextEdit(r.get("quality", ""))
        self.e_quality.setFixedHeight(52)
        self.e_quality.setPlaceholderText("做到什么程度算合格")

        self.e_selfcheck = QTextEdit(r.get("self_check", ""))
        self.e_selfcheck.setFixedHeight(52)
        self.e_selfcheck.setPlaceholderText("交付前自检哪几项")

        # ---- ⑧ 挂载技能（v4.121.3 新增）----
        # 运行时扫 skills 目录，不持久化。新建/改 SKILL.md 后重开编辑器立即可见。
        self._all_skills = legion.scan_available_skills()
        self.e_skills = QListWidget()
        self.e_skills.setSelectionMode(QAbstractItemView.MultiSelection)
        mounted = set(r.get("skills") or [])
        for sk in self._all_skills:
            slug = sk.get("slug", "")
            name = sk.get("name") or slug
            emoji = sk.get("emoji", "")
            label = f"{emoji} {name}".strip() if emoji else name
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, slug)
            tip = sk.get("description", "") or ""
            if tip:
                tip = tip[:120] + ("…" if len(tip) > 120 else "")
                it.setToolTip(f"slug: {slug}\n{tip}")
            else:
                it.setToolTip(f"slug: {slug}")
            self.e_skills.addItem(it)
            if slug in mounted:
                it.setSelected(True)
        self.e_skills.setFixedHeight(140)
        self.e_skills.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.e_skills.setToolTip(
            "可多选。技能 = 方法论 / 工作流，挂载后会拼进该角色的 system prompt 末尾。"
            "目录：~/Documents/小臭玩AI/skills/<slug>/SKILL.md，新建或修改后重开本对话框即生效。")

        form = QFormLayout()
        form.addRow("图标 + 角色名", row_name)
        form.addRow("① 使命", self.e_mission)
        form.addRow("② 约束", self.e_constraints)
        form.addRow("③ 工具白名单", self.e_tools)
        form.addRow("④ 模型", self.e_model)
        form.addRow("⑤ 输出格式", self.e_output)
        form.addRow("⑥ 质量标准", self.e_quality)
        form.addRow("⑦ 自检", self.e_selfcheck)
        form.addRow("⑧ 挂载技能", self.e_skills)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        # 滚动窗口包住表单：技能列表可能很长
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        host.setLayout(form)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        lay.addWidget(QLabel("说明：只有勾选的工具会注入该角色；勾选的技能会在执行时拼进 system prompt 末尾。"))
        lay.addWidget(btns)

    def _on_ok(self):
        if not self.e_name.text().strip():
            QMessageBox.warning(self, "缺角色名", "给这个成员起个名字，比如「研究员」。")
            return
        self.accept()

    def get_role(self):
        role = legion.new_role(
            name=self.e_name.text().strip(),
            emoji=self.e_emoji.text().strip(),
            mission=self.e_mission.toPlainText().strip(),
            constraints=self.e_constraints.toPlainText().strip(),
            tools=[i.text() for i in self.e_tools.selectedItems()],
            model=self.e_model.text().strip(),
            output_format=self.e_output.toPlainText().strip(),
            quality=self.e_quality.toPlainText().strip(),
            self_check=self.e_selfcheck.toPlainText().strip(),
        )
        # 挂载技能：按 slug 列表存进成员对象（v4.121.3 新增）
        role["skills"] = [i.data(Qt.UserRole) for i in self.e_skills.selectedItems() if i.data(Qt.UserRole)]
        return role


# ============ 成员挑选器 ============
class RolePicker(QDialog):
    """从角色库挑现成成员（复制一份进项目，改库不破项目），或新建空白。"""

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加团队成员")
        self.resize(460, 420)
        self._picked = None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("从角色库选一个（会复制一份进本项目，之后改动互不影响）："))
        self.lst = QListWidget()
        for r in (library or []):
            it = QListWidgetItem(f"{r.get('emoji', '')} {r.get('name', '')}".strip())
            it.setData(Qt.UserRole, r.get("id"))
            self.lst.addItem(it)
        self.lst.itemDoubleClicked.connect(lambda _i: self._pick())
        lay.addWidget(self.lst, 1)

        row = QHBoxLayout()
        b_pick = QPushButton("用选中的")
        b_new = QPushButton("+ 新建空白成员")
        b_cancel = QPushButton("取消")
        b_pick.clicked.connect(self._pick)
        b_new.clicked.connect(self._new)
        b_cancel.clicked.connect(self.reject)
        row.addWidget(b_pick)
        row.addWidget(b_new)
        row.addStretch(1)
        row.addWidget(b_cancel)
        lay.addLayout(row)

    def _pick(self):
        cur = self.lst.currentItem()
        if not cur:
            QMessageBox.information(self, "未选择", "先在列表里选一个角色。")
            return
        self._picked = cur.data(Qt.UserRole)
        self.accept()

    def _new(self):
        self._picked = "__new__"
        self.accept()

    def picked_id(self):
        return self._picked


# ============ 项目信息编辑器 ============
class ProjectEditor(QDialog):
    def __init__(self, project=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("项目信息")
        self.resize(520, 340)
        p = project or legion.new_project()

        self.e_emoji = QLineEdit(p.get("emoji", ""))
        self.e_emoji.setFixedWidth(64)
        self.e_emoji.setPlaceholderText("📝")
        self.e_name = QLineEdit(p.get("name", ""))
        row = QHBoxLayout()
        row.addWidget(self.e_emoji)
        row.addWidget(self.e_name, 1)

        self.e_desc = QTextEdit(p.get("description", ""))
        self.e_desc.setFixedHeight(80)
        self.e_desc.setPlaceholderText("这个项目用来干什么（给自己看的备注）")

        self.e_cat = QComboBox()
        self.e_cat.addItems(legion.CATEGORIES)
        if p.get("category") in legion.CATEGORIES:
            self.e_cat.setCurrentText(p.get("category"))

        form = QFormLayout()
        form.addRow("图标 + 项目名称", row)
        form.addRow("说明", self.e_desc)
        form.addRow("分类", self.e_cat)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)

    def _on_ok(self):
        if not self.e_name.text().strip():
            QMessageBox.warning(self, "缺项目名", "给项目起个名字，比如「公众号养生文」。")
            return
        self.accept()

    def get_data(self):
        return {
            "name": self.e_name.text().strip(),
            "emoji": self.e_emoji.text().strip(),
            "description": self.e_desc.toPlainText().strip(),
            "category": self.e_cat.currentText(),
        }


# ============ 军团主窗口 ============
class LegionWindow(QWidget):
    """多项目军团管理 + 编排 + 执行。"""

    def __init__(self, mw=None, parent=None):
        super().__init__(parent)
        self.mw = mw
        self.data = legion.load_legion()
        self.cur_project_id = None
        self.worker = None
        self.setWindowTitle("Agent 军团 · 自定义团队")
        # 必须是独立窗口：否则会当成父窗口里的子控件，落在左上角盖住编排页，
        # 且没有自己的标题栏/关闭按钮。Qt.Window 让它带标题栏 + 关闭 X。
        self.setWindowFlags(self.windowFlags() | Qt.Window)
        self.resize(1040, 720)
        self._build_ui()
        self._refresh_projects()
        self._center_on_parent()

    def _center_on_parent(self):
        """首次打开时把窗口居中（相对父窗口，无父则居中屏幕）。只执行一次。"""
        try:
            parent = self.parentWidget() if self.parent() else None
            if parent is not None:
                geo = parent.frameGeometry()
                x = geo.x() + (geo.width() - self.width()) // 2
                y = geo.y() + (geo.height() - self.height()) // 2
            else:
                scr = self.screen()
                if scr is None:
                    return
                avail = scr.availableGeometry()
                x = avail.x() + (avail.width() - self.width()) // 2
                y = avail.y() + (avail.height() - self.height()) // 2
            self.move(max(0, x), max(0, y))
        except Exception:
            pass

    # ---- UI 骨架 ----
    def _build_ui(self):
        root = QHBoxLayout(self)

        # 左栏：项目列表
        left = QWidget()
        left.setFixedWidth(250)
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("项目（可并存）"))
        self.proj_list = QListWidget()
        self.proj_list.currentItemChanged.connect(self._on_select_project)
        lv.addWidget(self.proj_list, 1)
        b_add = QPushButton("+ 添加团队")
        b_add.clicked.connect(self._add_project)
        b_edit = QPushButton("项目信息")
        b_edit.clicked.connect(self._edit_project)
        b_dup = QPushButton("复制项目")
        b_dup.clicked.connect(self._dup_project)
        b_del = QPushButton("删除项目")
        b_del.clicked.connect(self._del_project)
        for b in (b_add, b_edit, b_dup, b_del):
            lv.addWidget(b)
        root.addWidget(left)

        # 右栏：波次编排 + 运行
        right = QWidget()
        rv = QVBoxLayout(right)
        self.head = QLabel("尚未选择项目")
        self.head.setStyleSheet("font-size:15px;font-weight:600;")
        self.sub = QLabel("")
        self.sub.setStyleSheet("color:#777;")
        rv.addWidget(self.head)
        rv.addWidget(self.sub)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.waves_host = QWidget()
        self.waves_lay = QVBoxLayout(self.waves_host)
        self.scroll.setWidget(self.waves_host)
        rv.addWidget(self.scroll, 1)

        # 成员行的技能摘要：slug → "emoji name"，按需缓存（一次扫描复用多次）
        self._skill_cache = {}

        row_w = QHBoxLayout()
        b_wave = QPushButton("+ 添加波次")
        b_wave.clicked.connect(self._add_wave)
        row_w.addWidget(b_wave)
        row_w.addStretch(1)
        rv.addLayout(row_w)

        run_box = QGroupBox("启动军团")
        rb = QVBoxLayout(run_box)
        rrow = QHBoxLayout()
        self.task_edit = QLineEdit()
        self.task_edit.setPlaceholderText("给军团下个任务，例如：调研 2026 年储能行业并给出入局建议")
        self.run_btn = QPushButton("▶ 启动军团")
        self.run_btn.clicked.connect(self._run_legion)
        rrow.addWidget(self.task_edit, 1)
        rrow.addWidget(self.run_btn)
        rb.addLayout(rrow)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(160)
        self.log_view.setPlaceholderText("执行日志与产出会显示在这里")
        rb.addWidget(self.log_view)
        rv.addWidget(run_box)

        root.addWidget(right, 1)

    # ---- 工具 ----
    def _clear_layout(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
            elif it.layout() is not None:
                self._clear_layout(it.layout())

    def _cur_project(self):
        return legion.find_project(self.data, self.cur_project_id)

    def _resolve_skill_name(self, slug: str) -> str:
        """slug → "emoji name"，进程内缓存（避免每次刷新都全扫 skills 目录）。"""
        if not slug:
            return ""
        if slug not in self._skill_cache:
            info = legion._load_skill_prompt(slug)
            if info:
                emoji = info.get("emoji", "") or ""
                name = info.get("name") or slug
                self._skill_cache[slug] = f"{emoji} {name}".strip() if emoji else name
            else:
                self._skill_cache[slug] = f"⚠ {slug}"   # 找不到的标记一下
        return self._skill_cache[slug]

    # ---- 项目列表 ----
    def _refresh_projects(self, select_id=None):
        target = select_id or self.cur_project_id
        self.proj_list.blockSignals(True)
        self.proj_list.clear()
        for p in self.data.get("projects", []):
            it = QListWidgetItem(f"{p.get('emoji', '')} {p.get('name', '未命名')}".strip())
            it.setData(Qt.UserRole, p.get("id"))
            self.proj_list.addItem(it)
        self.proj_list.blockSignals(False)

        if target:
            for i in range(self.proj_list.count()):
                if self.proj_list.item(i).data(Qt.UserRole) == target:
                    self.proj_list.setCurrentRow(i)
                    break
        elif self.proj_list.count() > 0:
            self.proj_list.setCurrentRow(0)
        else:
            self.cur_project_id = None
            self._refresh_head()
            self._rebuild_waves()

    def _on_select_project(self, cur, prev):
        if cur is None:
            return
        self.cur_project_id = cur.data(Qt.UserRole)
        self._refresh_head()
        self._rebuild_waves()

    def _refresh_head(self):
        p = self._cur_project()
        if not p:
            self.head.setText("尚未选择项目")
            self.sub.setText("点「+ 添加团队」新建一个")
            return
        self.head.setText(f"{p.get('emoji', '')} {p.get('name', '')}".strip())
        self.sub.setText(f"{p.get('category', '')} · {legion.project_summary(p)}"
                         f"{(' · ' + p['description']) if p.get('description') else ''}")

    # ---- 项目增删改 ----
    def _add_project(self):
        dlg = ProjectEditor(None, self)
        if dlg.exec() != QDialog.Accepted:
            return
        d = dlg.get_data()
        p = legion.new_project(name=d["name"], emoji=d["emoji"],
                               description=d["description"], category=d["category"])
        self.data.setdefault("projects", []).append(p)
        legion.save_legion(self.data)
        self._refresh_projects(select_id=p["id"])

    def _edit_project(self):
        p = self._cur_project()
        if not p:
            QMessageBox.information(self, "未选择项目", "先选一个项目。")
            return
        dlg = ProjectEditor(p, self)
        if dlg.exec() != QDialog.Accepted:
            return
        p.update(dlg.get_data())
        legion.save_legion(self.data)
        self._refresh_projects(select_id=p["id"])

    def _dup_project(self):
        p = self._cur_project()
        if not p:
            QMessageBox.information(self, "未选择项目", "先选一个项目。")
            return
        np = copy.deepcopy(p)
        np["id"] = str(__import__("uuid").uuid4())
        np["name"] = p.get("name", "") + " 副本"
        self.data.setdefault("projects", []).append(np)
        legion.save_legion(self.data)
        self._refresh_projects(select_id=np["id"])

    def _del_project(self):
        p = self._cur_project()
        if not p:
            return
        r = QMessageBox.question(
            self, "删除项目",
            f"确定删除「{p.get('name', '')}」？该项目的团队配置会一起删掉（角色库不受影响）。")
        if r != QMessageBox.Yes:
            return
        self.data["projects"] = [x for x in self.data.get("projects", [])
                                 if x.get("id") != p.get("id")]
        self.cur_project_id = None
        legion.save_legion(self.data)
        self._refresh_projects()

    # ---- 波次编排 ----
    def _rebuild_waves(self):
        self._clear_layout(self.waves_lay)
        p = self._cur_project()
        if not p:
            self.waves_lay.addWidget(QLabel("左侧选一个项目，或点「+ 添加团队」新建。"))
            return

        waves = p.get("waves") or []
        for wi, wave in enumerate(waves):
            box = QGroupBox(f"第 {wi + 1} 波 · 波内并行")
            bl = QVBoxLayout(box)
            members = wave.get("members") or []
            if not members:
                bl.addWidget(QLabel("（本波还没有成员，点下面「+ 添加成员」）"))
            for mi, m in enumerate(members):
                row = QHBoxLayout()
                name_l = QLabel(f"{m.get('emoji', '')} {m.get('name', '')}".strip())
                name_l.setFixedWidth(140)
                tools = m.get("tools") or []
                skills = m.get("skills") or []
                # 工具 + 技能两段拼接展示（v4.121.3 新增技能摘要）
                tool_txt = ("工具：" + "、".join(tools)) if tools else "不用工具 · 纯输出"
                skill_txt = ""
                if skills:
                    names = [self._resolve_skill_name(s) for s in skills]
                    skill_txt = " | 技能：" + "、".join(n for n in names if n)
                info_l = QLabel(tool_txt + skill_txt)
                info_l.setStyleSheet("color:#777;")
                info_l.setWordWrap(True)
                row.addWidget(name_l)
                row.addWidget(info_l, 1)

                b_up = QPushButton("↑")
                b_up.setFixedWidth(32)
                b_up.setToolTip("上移（越过波首则并入上一波）")
                b_up.clicked.connect(
                    lambda _c=False, w=wi, i=mi: self._move_member(w, i, -1))
                b_dn = QPushButton("↓")
                b_dn.setFixedWidth(32)
                b_dn.setToolTip("下移（越过波尾则并入下一波）")
                b_dn.clicked.connect(
                    lambda _c=False, w=wi, i=mi: self._move_member(w, i, 1))
                b_ed = QPushButton("编辑")
                b_ed.clicked.connect(
                    lambda _c=False, w=wi, i=mi: self._edit_member(w, i))
                b_rm = QPushButton("移除")
                b_rm.clicked.connect(
                    lambda _c=False, w=wi, i=mi: self._del_member(w, i))
                for b in (b_up, b_dn, b_ed, b_rm):
                    row.addWidget(b)
                bl.addLayout(row)

            brow = QHBoxLayout()
            b_add = QPushButton("+ 添加成员")
            b_add.clicked.connect(lambda _c=False, w=wi: self._add_member(w))
            b_delw = QPushButton("删除本波")
            b_delw.clicked.connect(lambda _c=False, w=wi: self._del_wave(w))
            brow.addWidget(b_add)
            brow.addWidget(b_delw)
            brow.addStretch(1)
            bl.addLayout(brow)
            self.waves_lay.addWidget(box)

        self.waves_lay.addStretch(1)

    def _add_wave(self):
        p = self._cur_project()
        if not p:
            QMessageBox.information(self, "未选择项目", "先选一个项目。")
            return
        p.setdefault("waves", []).append(legion.new_wave())
        legion.save_legion(self.data)
        self._rebuild_waves()
        self._refresh_head()

    def _del_wave(self, wi):
        p = self._cur_project()
        if not p:
            return
        waves = p.get("waves") or []
        if not (0 <= wi < len(waves)):
            return
        if (waves[wi].get("members") or []):
            r = QMessageBox.question(self, "删除波次",
                                     f"第 {wi + 1} 波还有成员，确定连人一起删？")
            if r != QMessageBox.Yes:
                return
        waves.pop(wi)
        if not waves:
            waves.append(legion.new_wave())
        legion.save_legion(self.data)
        self._rebuild_waves()
        self._refresh_head()

    def _add_member(self, wi):
        p = self._cur_project()
        if not p:
            return
        picker = RolePicker(self.data.get("role_library", []), self)
        if picker.exec() != QDialog.Accepted:
            return
        pid = picker.picked_id()
        if pid == "__new__":
            role = legion.new_role()
        else:
            src = legion.find_role(self.data, pid)
            role = copy.deepcopy(src) if src else legion.new_role()
        dlg = RoleEditor(role, self)
        if dlg.exec() != QDialog.Accepted:
            return
        p["waves"][wi].setdefault("members", []).append(dlg.get_role())
        legion.save_legion(self.data)
        self._rebuild_waves()
        self._refresh_head()

    def _edit_member(self, wi, mi):
        p = self._cur_project()
        if not p:
            return
        try:
            role = p["waves"][wi]["members"][mi]
        except (IndexError, KeyError):
            return
        dlg = RoleEditor(role, self)
        if dlg.exec() != QDialog.Accepted:
            return
        p["waves"][wi]["members"][mi] = dlg.get_role()
        legion.save_legion(self.data)
        self._rebuild_waves()

    def _del_member(self, wi, mi):
        p = self._cur_project()
        if not p:
            return
        try:
            name = p["waves"][wi]["members"][mi].get("name", "该成员")
        except (IndexError, KeyError):
            return
        r = QMessageBox.question(self, "移除成员", f"把「{name}」移出本项目？")
        if r != QMessageBox.Yes:
            return
        p["waves"][wi]["members"].pop(mi)
        legion.save_legion(self.data)
        self._rebuild_waves()
        self._refresh_head()

    def _move_member(self, wi, mi, d):
        """上/下移。同波内换位；越过边界则并入相邻波次。"""
        p = self._cur_project()
        if not p:
            return
        waves = p.get("waves") or []
        if not (0 <= wi < len(waves)):
            return
        members = waves[wi].get("members") or []
        if not (0 <= mi < len(members)):
            return

        member = members.pop(mi)
        tw, ti = wi, mi + d
        if ti < 0:
            tw, ti = wi - 1, None          # 并入上一波末尾
        elif ti > len(members):
            tw, ti = wi + 1, 0             # 并入下一波开头

        if not (0 <= tw < len(waves)):
            members.insert(mi, member)     # 越界，还原
            return
        if ti is None:
            waves[tw].setdefault("members", []).append(member)
        else:
            waves[tw].setdefault("members", []).insert(ti, member)

        legion.save_legion(self.data)
        self._rebuild_waves()
        self._refresh_head()

    # ---- 执行 ----
    def _run_legion(self):
        p = self._cur_project()
        if not p:
            QMessageBox.information(self, "未选择项目", "先在左侧选一个项目。")
            return
        task = self.task_edit.text().strip()
        if not task:
            QMessageBox.information(self, "缺任务", "给军团下个任务再启动。")
            return
        if not legion.wave_members(p):
            QMessageBox.information(self, "团队是空的",
                                    "这个项目还没有成员，先给波次里加人。")
            return
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "军团运行中",
                                    "已有一个军团在执行，等它跑完再启动。")
            return

        self.log_view.clear()
        self.run_btn.setEnabled(False)
        self.run_btn.setText("执行中…")
        self.worker = LegionWorker(self.mw, p, task)
        self.worker.log_line.connect(self._on_log)
        self.worker.done.connect(self._on_done)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_log(self, text):
        self.log_view.insertPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_done(self, text):
        if text:
            self.log_view.append("\n" + "=" * 30 + " 军团产出 " + "=" * 30 + "\n")
            self.log_view.append(text)

    def _on_finished(self):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("▶ 启动军团")

    def closeEvent(self, e):
        if self.worker is not None and self.worker.isRunning():
            r = QMessageBox.question(
                self, "军团运行中",
                "军团仍在执行。关闭窗口不会中断它，产出也不会丢（会写进日志）。确定关闭？")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        e.accept()
