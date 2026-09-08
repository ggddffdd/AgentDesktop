# -*- coding: utf-8 -*-
"""Agent 军团面板 v4.124.6

把「编排页」从单一小说流水线扩展成**可自定义团队的多项目军团**：

- 左栏：项目列表（多项目并存）+「+ 添加团队」**v4.124.6：🟡 中断项目标 + 续跑入口**
- 右栏：当前项目的波次编排（波内并行、波间串行）+ 成员增删改移
- 底部：给军团下任务 → 启动 → 实时日志 + 产出

v4.124.6 改动：断点续传 UI 入口（项目主面板 / 续跑按钮 / 弹窗选「续/重/取」）。
        详见 LegionWorker.resume_from 字段 + legion.has_checkpoint()。
"""
import copy
import logging

from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QListWidget,
    QListWidgetItem, QLineEdit, QPushButton, QLabel, QTextEdit, QComboBox,
    QMessageBox, QGroupBox, QScrollArea, QDialogButtonBox, QAbstractItemView,
    QSizePolicy, QCheckBox, QSpinBox, QInputDialog,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QBrush, QColor

import legion
from legion_worker import LegionWorker

log = logging.getLogger("legion")


def _add_skill_section_header(lst: QListWidget, text: str):
    """在 QListWidget 加一行不可勾选的分组标题（用于技能挂载的两段展示）。"""
    it = QListWidgetItem(text)
    # 不可选中 + 不可点：用户点上去没反应，视觉上像 header
    flags = it.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled
    it.setFlags(flags)
    it.setForeground(QBrush(QColor("#666")))
    # 禁用样式加重一点：灰底
    it.setBackground(QBrush(QColor("#f3f3f3")))
    lst.addItem(it)
    return it


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
        # v4.123：分类（角色库 30 个后按组显示，自动组队也按组筛）
        self.c_category = QComboBox()
        self.c_category.addItems(legion.ROLE_CATEGORIES)
        self.c_category.setCurrentText(r.get("category") or "通用")
        self.c_category.setToolTip("角色分组，决定在选择器里归到哪一组")
        row_name = QHBoxLayout()
        row_name.addWidget(self.e_emoji)
        row_name.addWidget(self.e_name, 1)
        row_name.addWidget(QLabel("分组"))
        row_name.addWidget(self.c_category)

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

        # ---- ⑧ 挂载技能（v4.121.3 新增；v4.121.4 拆分两段；v4.121.5 归档元素带快照名）----
        # 运行时扫 skills 目录，不持久化。新建/改 SKILL.md 后重开编辑器立即可见。
        # 已挂载但被卸载的 slug 从成员 archived_skills 读，分两段展示：
        #   - 段 1：✓ 当前可挂载（已安装技能）
        #   - 段 2：⚠ 已下架但本项目仍挂着（灰显、可取消勾选=主动移除）
        # v4.121.5：archived_skills 元素从裸 slug 升级为 {slug, name, emoji} 字典，
        # UI 直接用存档里的 name/emoji 显示，不再用 _load_skill_prompt 拿（路径已无）。
        self._all_skills = legion.scan_available_skills()
        self.e_skills = QListWidget()
        self.e_skills.setSelectionMode(QAbstractItemView.MultiSelection)
        mounted = set(r.get("skills") or [])
        # 规范化 archived_skills：旧数据是 slug 字符串，新数据是 dict
        archived_raw = r.get("archived_skills") or []
        archived_norm = []
        for e in archived_raw:
            n = legion._normalize_archived_entry(e)
            if n:
                archived_norm.append(n)
        # 已安装技能段
        _add_skill_section_header(self.e_skills, "✓ 当前可挂载")
        for sk in self._all_skills:
            slug = sk.get("slug", "")
            name = sk.get("name") or slug
            emoji = sk.get("emoji", "")
            label = f"{emoji} {name}".strip() if emoji else name
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, ("installed", slug))
            tip = sk.get("description", "") or ""
            if tip:
                tip = tip[:120] + ("…" if len(tip) > 120 else "")
                it.setToolTip(f"slug: {slug}\n{tip}")
            else:
                it.setToolTip(f"slug: {slug}")
            self.e_skills.addItem(it)
            if slug in mounted:
                it.setSelected(True)
        # 已下架技能段（仅当成员 archived_skills 非空时才出现）
        if archived_norm:
            _add_skill_section_header(self.e_skills, "⚠ 已下架但本项目仍挂着")
            for entry in archived_norm:
                slug = entry.get("slug", "")
                # 显示名解析优先级：v4.121.5 存档快照 → 运行时扫（重装回来）→ slug
                name = entry.get("name") or slug
                emoji = entry.get("emoji") or ""
                if name == slug or not emoji:
                    info = legion._load_skill_prompt(slug)
                    if info:
                        name = info.get("name") or name
                        emoji = emoji or (info.get("emoji") or "")
                # 真正拿不到就用 ⚠ 前缀（极端情况：旧数据 + 未重装）
                if name == slug and not emoji:
                    name = f"⚠ {slug}"
                label = f"{emoji} {name}".strip()
                it = QListWidgetItem(label)
                # v4.121.5：payload 改为整个 dict，get_role 时整存回去名字不丢
                it.setData(Qt.UserRole, ("archived", entry))
                it.setForeground(QBrush(QColor("#a0a0a0")))
                it.setToolTip(
                    f"slug: {slug}\n"
                    f"状态：技能已下架（skills/{slug}/SKILL.md 不存在或读不动）\n"
                    f"默认勾选 = 保留在 archived_skills；取消勾选 = 主动移除")
                self.e_skills.addItem(it)
                # 默认勾选：用户原始意图是"留着"
                it.setSelected(True)
        self.e_skills.setFixedHeight(180)
        self.e_skills.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.e_skills.setToolTip(
            "可多选。技能 = 方法论 / 工作流，挂载后会拼进该角色的 system prompt 末尾。"
            "目录：~/Documents/小臭玩AI/skills/<slug>/SKILL.md，新建或修改后重开本对话框即生效。"
            "已下架段：保留 = 该技能回到 active 后会自动复活；取消勾选 = 主动移除。")

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
            category=self.c_category.currentText().strip(),
        )
        # 挂载技能：v4.121.4 起按两段分拣
        # - ('installed', slug) 勾选 → 进 skills（运行时拼入 prompt）
        # - ('archived', entry)  勾选 → 保留在 archived_skills（运行时忽略）
        #   v4.121.5 起 entry 是 {slug, name, emoji} 字典，整存回去避免名字丢失
        # - ('archived', entry)  未勾 → 主动移除（不写回任何字段）
        new_skills, new_archived = [], []
        for it in self.e_skills.selectedItems():
            payload = it.data(Qt.UserRole)
            if not (isinstance(payload, tuple) and len(payload) == 2):
                continue
            kind, value = payload
            if kind == "installed":
                if isinstance(value, str) and value.strip():
                    new_skills.append(value)
            elif kind == "archived":
                # 兼容旧数据 / 极端情况：value 可能不是 dict
                if isinstance(value, dict):
                    slug = value.get("slug", "")
                    if isinstance(slug, str) and slug.strip():
                        new_archived.append(value)
                elif isinstance(value, str) and value.strip():
                    new_archived.append({"slug": value, "name": value, "emoji": ""})
        role["skills"] = new_skills
        role["archived_skills"] = new_archived
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
        # v4.123：角色库扩到 30 个，按分组显示（借用技能区的分组标题样式）
        _last_cat = None
        for r in legion._sorted_by_category(library):
            cat = r.get("category") or "通用"
            if cat != _last_cat:
                _add_skill_section_header(self.lst, "── %s ──" % cat)
                _last_cat = cat
            label = ("%s %s" % (r.get('emoji', ''), r.get('name', ''))).strip()
            it = QListWidgetItem("    " + label)
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
        # 分组标题行没有 UserRole，点它不算选择
        if not cur or not cur.data(Qt.UserRole):
            QMessageBox.information(self, "未选择", "在分组下面的角色里选一个。")
            return
        self._picked = cur.data(Qt.UserRole)
        self.accept()

    def _new(self):
        self._picked = "__new__"
        self.accept()

    def picked_id(self):
        return self._picked


# ============ 自动组队（v4.123）：跟 PM 对话，让它帮你拉人 ============
class TeamBuildWorker(QThread):
    """后台跑一次「项目经理」节点，拿到 JSON 组队方案原文。

    不进工具链（tools 为空），只要一次纯文本回复，所以 max_turns=2 足够。
    """

    done = Signal(str)
    failed = Signal(str)

    def __init__(self, mw, need, lib, data=None, parent=None):
        super().__init__(parent)
        self.mw = mw
        self.need = need
        self.lib = lib
        self.data = data if isinstance(data, dict) else None

    def run(self):
        try:
            from agent_node import AgentNode
            # v4.124：提示词里带上历史班子（组织记忆）和技能清单，
            # PM 看得到库里有什么，才谈得上「差什么」。
            prompt = legion.build_team_prompt(self.need, self.lib, data=self.data)
            agent = AgentNode("team_build", prompt, tools=set(),
                              mw=self.mw, max_turns=2)
            st = agent.run({"task": self.need, "query": self.need})
            text = (st.get("team_build_output") or "").strip()
            if not text:
                self.failed.emit("项目经理没给出方案（返回为空），换个说法再试一次。")
                return
            self.done.emit(text)
        except Exception as e:
            log.error("自动组队失败: %s", e, exc_info=True)
            self.failed.emit("组队失败：%s" % e)


class TeamBuildDialog(QDialog):
    """提需求 → PM 出方案 → **你审理解、审名单、定缺角缺技能** → 批准才入职。

    v4.124：组队方案本身是一道闸。PM 只是「拟方案」，没有任何角色在他出方案的
    那一刻入职；只有用户点「批准组建」才真正写进项目。审批顺序刻意把
    **PM 对你需求的理解**放在最上面——理解错了，名单排得再漂亮也没意义。
    """

    def __init__(self, mw, project, library, data=None, parent=None):
        super().__init__(parent)
        self.mw = mw
        self.project = project
        self.library = library or []
        self.data = data if isinstance(data, dict) else {}
        self.plan = None
        self.worker = None
        self.advice = ""          # 打回意见，重来时会一起喂给 PM
        self.setWindowTitle("🧙 组队方案审批（第一道闸）")
        self.resize(760, 700)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("用一句话说清你要干什么（越具体，组队越准）："))
        self.ed = QTextEdit()
        self.ed.setPlaceholderText(
            "例：我要做一条抖音带货短视频，先选品看竞品，再写脚本，最后有人把关质量")
        self.ed.setFixedHeight(58)
        lay.addWidget(self.ed)

        row = QHBoxLayout()
        self.b_go = QPushButton("🧙 让 PM 出方案")
        self.b_go.clicked.connect(self._build)
        self.b_adopt = QPushButton("➕ 采纳勾选的缺角草案")
        self.b_adopt.setEnabled(False)
        self.b_adopt.clicked.connect(self._adopt_drafts)
        self.b_skill = QPushButton("🔎 去 GitHub 找缺的技能")
        self.b_skill.setEnabled(False)
        self.b_skill.clicked.connect(self._find_skill)
        row.addWidget(self.b_go)
        row.addWidget(self.b_adopt)
        row.addWidget(self.b_skill)
        row.addStretch(1)
        lay.addLayout(row)

        self.status = QLabel("")
        lay.addWidget(self.status)
        lay.addWidget(QLabel("PM 的方案（**你批准前不会有任何角色入职**）："))
        self.out = QTextEdit()
        self.out.setReadOnly(True)
        lay.addWidget(self.out, 1)

        # ---- 缺角区：角色库里没有的角色，PM 起草了定义草案等你点头 ----
        self.gap_box = QGroupBox("缺角（角色库里没有，PM 起草了定义草案）")
        gl = QVBoxLayout(self.gap_box)
        self.gap_list = QListWidget()
        self.gap_list.setMaximumHeight(110)
        gl.addWidget(self.gap_list)
        self.gap_box.setVisible(False)
        lay.addWidget(self.gap_box)

        # ---- 缺技能区：技能库里没有，PM 必须请示，用户点头才去 GitHub ----
        self.skill_box = QGroupBox("缺技能（PM 请示：技能库里没有，要不要去 GitHub 找）")
        sl = QVBoxLayout(self.skill_box)
        self.skill_list = QListWidget()
        self.skill_list.setMaximumHeight(100)
        sl.addWidget(self.skill_list)
        self.skill_box.setVisible(False)
        lay.addWidget(self.skill_box)

        # ---- 审批三按钮 ----
        brow = QHBoxLayout()
        self.b_approve = QPushButton("✅ 批准组建（写入项目）")
        self.b_approve.setEnabled(False)
        self.b_approve.clicked.connect(self._approve)
        self.b_reject = QPushButton("↩ 打回重来（说清哪里不对）")
        self.b_reject.setEnabled(False)
        self.b_reject.clicked.connect(self._reject_plan)
        b_close = QPushButton("❌ 终止")
        b_close.clicked.connect(self.reject)
        brow.addWidget(self.b_approve)
        brow.addWidget(self.b_reject)
        brow.addStretch(1)
        brow.addWidget(b_close)
        lay.addLayout(brow)

    # ---- 内部 ----
    def _build(self):
        need = self.ed.toPlainText().strip()
        if len(need) < 6:
            QMessageBox.information(self, "说清楚点",
                                    "至少写 6 个字，PM 才知道你要干什么。")
            return
        # 打回重来：把意见原样带进去，让 PM 照着改
        if self.advice.strip():
            need = need + "\n\n【上一版被打回，用户意见如下，必须照改】\n" + self.advice.strip()
        self.b_go.setEnabled(False)
        self.b_approve.setEnabled(False)
        self.b_reject.setEnabled(False)
        self.status.setText("⏳ 项目经理正在拟方案…")
        self.out.setPlainText("")
        self.plan = None
        self.worker = TeamBuildWorker(self.mw, need, self.library, self.data)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.start()

    def _on_done(self, text):
        self.b_go.setEnabled(True)
        plan = legion.parse_team_plan(text)
        if not plan:
            self.status.setText("⚠️ PM 没按 JSON 格式输出，下面是原文。可点「让 PM 出方案」再来一次。")
            self.out.setPlainText(text)
            self._clear_gates()
            return
        self.plan = plan
        self.out.setPlainText(self._fmt(plan))
        self._fill_gaps(plan)
        self.b_approve.setEnabled(True)
        self.b_reject.setEnabled(True)
        self.status.setText("⏸ 等你审批：先看懂没懂你的需求，再看名单。批准后才会写入项目。")

    def _on_fail(self, msg):
        self.b_go.setEnabled(True)
        self.status.setText("❌ " + msg)

    def _clear_gates(self):
        self.b_approve.setEnabled(False)
        self.b_reject.setEnabled(False)
        self.b_adopt.setEnabled(False)
        self.b_skill.setEnabled(False)
        self.gap_box.setVisible(False)
        self.skill_box.setVisible(False)
        self.gap_list.clear()
        self.skill_list.clear()

    def _fmt(self, plan):
        """方案排版：**理解置顶** —— 用户审批时第一眼看的就是这个。"""
        lines = []
        und = (plan.get("understanding") or "").strip()
        lines.append("① PM 对你需求的理解（先看这个，理解错了后面全白搭）")
        lines.append("   " + (und or "（PM 没写理解 —— 建议打回重来）"))
        lines.append("")
        lines.append("② 阵容编排")
        if plan.get("name"):
            lines.append("   【%s %s】%s" % (plan.get("emoji", ""), plan.get("name"),
                                             plan.get("description") or ""))
        for i, w in enumerate(plan.get("waves") or [], 1):
            lines.append("   第 %d 波（并行）：" % i)
            for m in (w.get("members") or []):
                nm = m.get("name", "?")
                em = ""
                for r in self.library:
                    if r.get("name") == nm:
                        em = r.get("emoji", "")
                        break
                seg = "     · %s %s" % (em, nm)
                if m.get("why"):
                    seg += " —— %s" % m["why"]
                if m.get("skills"):
                    seg += "\n         技能：%s" % "、".join(m["skills"])
                lines.append(seg)
        if plan.get("reason"):
            lines.append("\n③ 组队理由：%s" % plan["reason"])
        return "\n".join(lines)

    def _fill_gaps(self, plan):
        """把缺角/缺技能填进下面的勾选区——这两块都是**要你点头**的事。"""
        self.gap_list.clear()
        self.skill_list.clear()
        gaps = plan.get("missing_roles") or []
        for g in gaps:
            it = QListWidgetItem("%s —— %s" % (g.get("name", "?"),
                                                g.get("why") or "（未说明原因）"))
            it.setData(Qt.UserRole, g)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.gap_list.addItem(it)
        self.gap_box.setVisible(bool(gaps))
        self.b_adopt.setEnabled(bool(gaps))

        ms = plan.get("missing_skills") or []
        for s in ms:
            who = s.get("for_role") or "未指定角色"
            txt = "%s（给 %s 用）—— %s" % (s.get("name", "?"), who, s.get("why") or "")
            it = QListWidgetItem(txt)
            it.setData(Qt.UserRole, s)
            self.skill_list.addItem(it)
        self.skill_box.setVisible(bool(ms))
        self.b_skill.setEnabled(bool(ms))
        if ms and not self.skill_list.currentItem():
            self.skill_list.setCurrentRow(0)

    def _adopt_drafts(self):
        """用户勾选后，把 PM 起草的缺角定义正式收进角色库（滚雪球）。"""
        picked = []
        for i in range(self.gap_list.count()):
            it = self.gap_list.item(i)
            if it.checkState() == Qt.Checked:
                picked.append(it.data(Qt.UserRole))
        if not picked:
            QMessageBox.information(self, "没勾", "先勾选要采纳的缺角草案。")
            return
        n, msg = legion.adopt_missing_roles(self.data, picked)
        if n:
            self.library = self.data.get("role_library") or self.library
            legion.save_legion(self.data)
        QMessageBox.information(self, "缺角入库", msg)
        if n:
            # 已入库的角色从缺角区移除，方案里对应的成员下次就能匹配上
            adopted = {str(p.get("name") or "") for p in picked}
            for i in range(self.gap_list.count() - 1, -1, -1):
                it = self.gap_list.item(i)
                d = it.data(Qt.UserRole) or {}
                if d.get("name") in adopted and it.checkState() == Qt.Checked:
                    self.gap_list.takeItem(i)
            self.gap_box.setVisible(self.gap_list.count() > 0)
            self.b_adopt.setEnabled(self.gap_list.count() > 0)

    def _find_skill(self):
        """PM 请示的缺技能 → 用户点头 → 才联网去 GitHub 找；装完顺手挂到角色上。"""
        cur = self.skill_list.currentItem()
        d = cur.data(Qt.UserRole) if cur else {}
        name = (d or {}).get("name") or ""
        who = (d or {}).get("for_role") or ""
        dlg = SkillInstallDialog(query=name, mw=self.mw, parent=self)
        dlg.exec()
        slug = getattr(dlg, "installed_slug", None)
        if not slug or not self.plan:
            return
        # 装完直接挂上，省得用户再跑一轮组队
        target = None
        for w in (self.plan.get("waves") or []):
            for m in (w.get("members") or []):
                if who and m.get("name") == who:
                    target = m
                    break
            if target:
                break
        if not target:
            return
        r = QMessageBox.question(
            self, "挂上技能", "把「%s」挂给 %s？" % (slug, target.get("name")),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            return
        sk = target.get("skills") or []
        if slug not in sk:
            sk.append(slug)
        target["skills"] = sk
        self.out.setPlainText(self._fmt(self.plan))
        self.status.setText("✅ 已把「%s」挂给 %s，批准后就生效。" % (slug, target.get("name")))

    def _reject_plan(self):
        """打回重来：把意见记下来，连同原需求再喂给 PM。"""
        txt, ok = QInputDialog.getMultiLineText(
            self, "打回方案", "告诉 PM 哪里不对（会原样带进下一轮）：",
            self.advice)
        if not ok:
            return
        self.advice = (txt or "").strip()
        if not self.advice:
            QMessageBox.information(self, "写点什么", "不写意见 PM 不知道改哪儿。")
            return
        self._clear_gates()
        self._build()

    def _approve(self):
        """✅ 批准 —— 这一下之前，没有任何角色入职。"""
        if not self.plan:
            return
        need = self.ed.toPlainText().strip()
        # 未勾选入库的缺角提示一下，但不拦（用户可以自己后面补）
        remain = self.gap_list.count()
        if remain:
            r = QMessageBox.question(
                self, "还有缺角没补",
                "方案里有 %d 个角色库还没有的角色（未采纳草案）。\n"
                "批准后会跳过这些角色，确定继续吗？" % remain,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        self.project, rep = legion.apply_team_plan(
            self.project, self.plan, self.library)
        # 班子留档：这次什么需求配了什么阵容，下次同类需求 PM 翻旧账
        try:
            rec, _ = legion.save_team_recipe(self.data, need, self.plan)
            if rec:
                self.project["recipe_id"] = rec.get("id")
        except Exception as e:
            log.warning("班子留档失败: %s", e)
        # 宪法第二章：组队是第一道闸，批准动作必须留审计痕（可抽查）
        try:
            names = []
            for w in (self.plan.get("waves") or []):
                names += [m.get("name", "") for m in (w.get("members") or [])]
            legion.record_auth(
                self.project.get("id", ""), self.project.get("name", ""),
                0, "team_plan",
                "PM:%s" % (self.plan.get("name") or ""),
                "approve", by="user",
                reason="批准组队方案：%s（%d 人）" % ("、".join(names)[:120], len(names)))
        except Exception as e:
            log.warning("组队审计写入失败: %s", e)
        QMessageBox.information(self, "已批准，班子入职", rep)
        self.accept()


# ============ GitHub 技能安装（v4.124）============
class GHWorker(QThread):
    """联网任务统一走后台线程，避免卡住界面。

    只在用户明确点头后才启动——PM 无权自己去 GitHub 装东西。
    """

    done = Signal(object, str)      # (payload, err)

    def __init__(self, mode, parent=None, **kw):
        super().__init__(parent)
        self.mode = mode
        self.kw = kw

    def run(self):
        payload, err = None, "未知任务"
        try:
            if self.mode == "search":
                payload, err = legion.github_search_skill_repos(
                    self.kw.get("query", ""), limit=10)
            elif self.mode == "list":
                payload, err = legion.github_list_skill_files(
                    self.kw.get("repo", ""), self.kw.get("branch"))
            elif self.mode == "preview":
                payload, err = legion.github_fetch_raw(
                    self.kw.get("repo", ""), self.kw.get("branch"),
                    self.kw.get("path", ""))
            elif self.mode == "install":
                ok, msg = legion.install_skill_from_github(
                    self.kw.get("repo", ""), self.kw.get("path", ""),
                    branch=self.kw.get("branch"), slug=self.kw.get("slug"))
                payload, err = {"ok": ok, "msg": msg}, None
        except Exception as e:
            payload, err = None, "%s" % e
        self.done.emit(payload, err)


class _AuditWorker(QThread):
    """v4.124.1：让 PM 调 LLM 给候选出结构化安全审查报告（后台跑，不卡界面）。"""

    done = Signal(list, str)      # (evaluations, err)
    failed = Signal(str)

    def __init__(self, mw, prompt, n_candidates, parent=None):
        super().__init__(parent)
        self.mw = mw
        self.prompt = prompt
        self.n = int(n_candidates or 0)

    def run(self):
        try:
            from agent_node import AgentNode
            agent = AgentNode("skill_audit", self.prompt, tools=set(),
                              mw=self.mw, max_turns=2)
            st = agent.run({"task": "审查", "query": "审查候选技能仓库"})
            text = (st.get("skill_audit_output") or "").strip()
            if not text:
                self.failed.emit("PM 没返回审查结果。")
                return
            evs = legion.parse_candidate_evaluation(text, self.n)
            if not evs:
                self.failed.emit("PM 审查结果不是标准 JSON。")
                return
            self.done.emit(evs, "")
        except Exception as e:
            log.error("安全审查失败: %s", e, exc_info=True)
            self.failed.emit("审查失败：%s" % e)


class SkillInstallDialog(QDialog):
    """搜 GitHub → PM 安全审查（结构化报告）→ 你批 → 挑 SKILL.md → 装进本地。

    v4.124.1 关键升级：装之前必须有抓手——
    每候选都带 ⭐ stars / 📅 updated / 📜 License / archived 状态，
    点「🛡 让 PM 安全审查」会让 PM 给每个候选打 recommend + reasons + risks + danger，
    把一堆链接变成**结构化评估表**，你批完才让装。
    不推 / 缺 license / 已归档的会在装时二次确认。
    """

    def __init__(self, query="", mw=None, parent=None):
        super().__init__(parent)
        self.mw = mw
        self.setWindowTitle("🛡 从 GitHub 装技能（先审查后装）")
        self.resize(820, 760)
        self.worker = None
        self.audit_worker = None
        self.repo = None          # 当前选中的 owner/repo
        self.branch = None
        self.installed_slug = None
        self.candidates = []      # 当前列表展示的所有候选（结构化）
        self.evaluations = {}     # full_name -> {recommend, score, reasons, risks, danger}

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "PM 找完后给你结构化报告（⭐/📅/📜/安全等级），你批了才装。"
            "建议搜完先点「🛡 安全审查」。"))

        row = QHBoxLayout()
        self.ed_q = QLineEdit(query or "")
        self.ed_q.setPlaceholderText("搜什么？例：seo audit / short video script")
        self.b_go = QPushButton("🔎 搜 GitHub")
        self.b_go.clicked.connect(self._search)
        self.b_audit = QPushButton("🛡 让 PM 安全审查")
        self.b_audit.setEnabled(False)
        self.b_audit.setToolTip("搜到候选后启用；让 PM 给每个候选打安全等级和推荐度")
        self.b_audit.clicked.connect(self._audit)
        row.addWidget(self.ed_q, 1)
        row.addWidget(self.b_go)
        row.addWidget(self.b_audit)
        lay.addLayout(row)

        self.status = QLabel("")
        lay.addWidget(self.status)

        lay.addWidget(QLabel("① 候选仓库（按 star 排序，审查后会有 ✅/⚠️/❌ 标签）："))
        self.lst_repo = QListWidget()
        self.lst_repo.setMaximumHeight(180)
        self.lst_repo.itemSelectionChanged.connect(self._on_repo)
        lay.addWidget(self.lst_repo)

        lay.addWidget(QLabel("② PM 的安全审查报告（选仓库看详情；没审查时点上方「🛡 安全审查」）："))
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(120)
        self.report.setPlaceholderText("（未审查）")
        lay.addWidget(self.report)

        lay.addWidget(QLabel("③ 仓库里的 SKILL.md："))
        self.lst_file = QListWidget()
        self.lst_file.setMaximumHeight(120)
        self.lst_file.itemSelectionChanged.connect(self._on_file)
        lay.addWidget(self.lst_file)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("安装为 slug："))
        self.ed_slug = QLineEdit("")
        self.ed_slug.setPlaceholderText("技能目录名，只能字母数字和 -")
        row2.addWidget(self.ed_slug, 1)
        self.b_inst = QPushButton("⬇ 安装到技能库")
        self.b_inst.setEnabled(False)
        self.b_inst.clicked.connect(self._install)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.reject)
        row2.addWidget(self.b_inst)
        row2.addWidget(b_close)
        lay.addLayout(row2)

        lay.addWidget(QLabel("④ 内容预览（前 600 字）："))
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(120)
        lay.addWidget(self.preview)

    # ---- 内部 ----
    def _busy(self, on, text=""):
        self.b_go.setEnabled(not on)
        self.b_inst.setEnabled(not on and bool(self.lst_file.currentItem()))
        self.status.setText(text)

    def _search(self):
        q = self.ed_q.text().strip()
        if not q:
            QMessageBox.information(self, "填点东西", "先写个搜索词。")
            return
        self._busy(True, "⏳ 正在搜 GitHub…")
        self.lst_repo.clear()
        self.lst_file.clear()
        self.preview.clear()
        self.report.clear()
        self.evaluations = {}
        self.candidates = []
        self._run("search", query=q)

    def _audit(self):
        """让 PM 调 LLM 给每个候选出结构化评估（安全等级/推荐度/风险点）。"""
        if not self.candidates:
            QMessageBox.information(self, "还没搜", "先搜出候选，再点安全审查。")
            return
        if not self.mw:
            QMessageBox.warning(self, "没法审查",
                                "缺少主程序上下文（mw），无法调 PM。请把对话框从军团面板打开。")
            return
        from agent_node import AgentNode
        prompt = legion.evaluate_skill_candidates_prompt(self.ed_q.text().strip(),
                                                        self.candidates)
        self.b_audit.setEnabled(False)
        self.status.setText("⏳ PM 正在安全审查 %d 个候选…" % len(self.candidates))
        self.audit_worker = _AuditWorker(self.mw, prompt, len(self.candidates), self)
        self.audit_worker.done.connect(self._on_audit)
        self.audit_worker.failed.connect(self._on_audit_fail)
        self.audit_worker.start()

    def _on_repo(self):
        it = self.lst_repo.currentItem()
        if not it:
            return
        d = it.data(Qt.UserRole) or {}
        self.repo = d.get("full_name")
        self.branch = d.get("branch")
        self.lst_file.clear()
        self.preview.clear()
        self._busy(True, "⏳ 正在列 %s 里的 SKILL.md…" % self.repo)
        self._run("list", repo=self.repo, branch=self.branch)

    def _on_file(self):
        it = self.lst_file.currentItem()
        if not it:
            return
        d = it.data(Qt.UserRole) or {}
        self.ed_slug.setText(d.get("slug") or "")
        self.b_inst.setEnabled(True)
        self._busy(True, "⏳ 正在拉内容预览…")
        self._run("preview", repo=self.repo, branch=self.branch, path=d.get("path"))

    def _install(self):
        it = self.lst_file.currentItem()
        if not it:
            return
        d = it.data(Qt.UserRole) or {}
        slug = self.ed_slug.text().strip()
        if not slug:
            QMessageBox.information(self, "给个名字", "填一下安装后的 slug（技能目录名）。")
            return
        self._busy(True, "⏳ 正在安装…")
        self._run("install", repo=self.repo, branch=self.branch,
                  path=d.get("path"), slug=slug)

    def _run(self, mode, **kw):
        self.worker = GHWorker(mode, self, **kw)
        self.worker.done.connect(self._on_result)
        self.worker.start()

    def _on_result(self, payload, err):
        mode = self.worker.mode if self.worker else ""
        if err:
            self._busy(False, "❌ " + err)
            return
        if mode == "search":
            repos = payload or []
            if not repos:
                self._busy(False, "没搜到，换个词试试（GitHub 未认证接口每小时限 60 次）。")
                return
            self.candidates = repos
            for r in repos:
                self._add_repo_item(r)
            self._busy(False, "✅ 找到 %d 个仓库。**建议先点「🛡 让 PM 安全审查」再挑。**"
                       % len(repos))
            self.b_audit.setEnabled(True)
        elif mode == "list":
            files = payload or []
            if not files:
                self._busy(False, "这个仓库里没找到 SKILL.md。")
                return
            for f in files:
                it = QListWidgetItem("%s  （%s）" % (f.get("path"), f.get("slug")))
                it.setData(Qt.UserRole, f)
                self.lst_file.addItem(it)
            self._busy(False, "✅ %d 个 SKILL.md，选一个安装。" % len(files))
        elif mode == "preview":
            self.preview.setPlainText((payload or "")[:600])
            self._busy(False, "✅ 看一眼内容对不对，再决定装不装。")
        elif mode == "install":
            ok = (payload or {}).get("ok")
            msg = (payload or {}).get("msg") or ""
            self._busy(False, msg)
            if ok:
                self.installed_slug = slug
                QMessageBox.information(
                    self, "装好了",
                    msg + "\n\n关掉这个窗口后，可以把技能直接挂给需要的角色。")

    # ---- 结构化候选 + 审查报告 ----
    _TAG = {
        "强推": ("✅ 强推", "#1a7f37"),
        "推":   ("✅ 推荐", "#1a7f37"),
        "慎":   ("⚠️ 慎装", "#9a6700"),
        "不推": ("❌ 不推", "#b91c1c"),
    }

    def _add_repo_item(self, r):
        """结构化展示一个候选：⭐/📅/📜/archived + 评估标签。"""
        lic = legion._safe_license_text(r)
        badge = ""
        ev = self.evaluations.get(r.get("full_name"))
        if ev:
            tag, color = self._TAG.get(ev["recommend"], ("", ""))
            badge = "  %s(%d)" % (tag, ev.get("score") or 0)
        archived = "  🗄 已归档" if r.get("archived") else ""
        danger = ""
        if ev and ev.get("danger"):
            danger = "  🚩 %s" % ev["danger"]
        # 跨平台中文：全角符号便于辨识
        txt = "⭐%s  %s  📅%s  📜%s%s%s%s\n    %s" % (
            r.get("stars", 0), r.get("full_name", ""),
            r.get("updated", ""), lic, archived, danger, badge,
            r.get("description", "（无描述）"))
        it = QListWidgetItem(txt)
        it.setData(Qt.UserRole, r)
        if ev:
            # 用背景色标安全等级（一眼看得到）
            color = self._TAG.get(ev["recommend"], ("", None))[1]
            if color:
                from PySide6.QtGui import QBrush
                it.setBackground(QBrush(QColor(color if ev["recommend"] != "不推" else "#fee2e2")))
                it.setForeground(QBrush(QColor("#ffffff" if ev["recommend"] in ("强推", "推") else "#1a1a1a")))
        self.lst_repo.addItem(it)

    def _on_audit(self, evals, err):
        self.b_audit.setEnabled(True)
        if err:
            self.status.setText("❌ 审查失败：%s" % err)
            return
        for e in evals:
            idx = e.get("idx")
            if 0 <= (idx or -1) < len(self.candidates):
                self.evaluations[self.candidates[idx]["full_name"]] = e
        # 重新渲染列表（带评估色标）
        cur_full = self.lst_repo.currentItem().data(Qt.UserRole).get("full_name") \
            if self.lst_repo.currentItem() else None
        self.lst_repo.blockSignals(True)
        self.lst_repo.clear()
        for r in self.candidates:
            self._add_repo_item(r)
        # 恢复选中
        for i in range(self.lst_repo.count()):
            d = self.lst_repo.item(i).data(Qt.UserRole) or {}
            if d.get("full_name") == cur_full:
                self.lst_repo.setCurrentRow(i)
                break
        self.lst_repo.blockSignals(False)
        # 报告摘要
        recs = [e for e in evals if e["recommend"] in ("强推", "推")]
        bads = [e for e in evals if e["recommend"] == "不推" or e.get("danger")]
        msg = "✅ PM 审查完成：%d 推荐 / %d 慎或不推。" % (len(recs), len(bads))
        self.status.setText(msg)
        self._show_report_for_current()

    def _on_audit_fail(self, msg):
        self.b_audit.setEnabled(True)
        self.status.setText("❌ 审查失败：%s" % msg)

    def _show_report_for_current(self):
        it = self.lst_repo.currentItem()
        if not it:
            self.report.setPlainText("")
            return
        d = it.data(Qt.UserRole) or {}
        ev = self.evaluations.get(d.get("full_name"))
        if not ev:
            self.report.setPlainText(
                "（未审查）\n\n如果想看结构化评估，点「🛡 让 PM 安全审查」。\n"
                "**注意**：不审查直接装=你自担风险。")
            return
        rec_tag = self._TAG.get(ev["recommend"], ("", ""))[0]
        lines = ["【%s  %s  ⭐ %d/10】" % (rec_tag, d.get("full_name", ""), ev.get("score") or 0)]
        if ev.get("danger"):
            lines.append("🚩 红旗：%s" % ev["danger"])
        if ev.get("reasons"):
            lines.append("\n✅ 推荐理由：")
            for x in ev["reasons"]:
                lines.append("  · " + x)
        if ev.get("risks"):
            lines.append("\n⚠️ 风险点：")
            for x in ev["risks"]:
                lines.append("  · " + x)
        lic = d.get("license") or "—"
        lines.append("\n📋 仓库信息：%s  📜 %s  📅 %s  archived=%s  open_issues=%d" % (
            d.get("url", ""), lic, d.get("updated", ""),
            d.get("archived"), d.get("open_issues") or 0))
        self.report.setPlainText("\n".join(lines))

    def _on_repo(self):
        it = self.lst_repo.currentItem()
        if not it:
            return
        d = it.data(Qt.UserRole) or {}
        self.repo = d.get("full_name")
        self.branch = d.get("branch")
        self.lst_file.clear()
        self.preview.clear()
        self._show_report_for_current()   # 切仓库时同步刷新审查报告
        self._busy(True, "⏳ 正在列 %s 里的 SKILL.md…" % self.repo)
        self._run("list", repo=self.repo, branch=self.branch)

    def _install(self):
        it = self.lst_file.currentItem()
        if not it:
            return
        d = it.data(Qt.UserRole) or {}
        slug = self.ed_slug.text().strip()
        if not slug:
            QMessageBox.information(self, "给个名字", "填一下安装后的 slug（技能目录名）。")
            return
        # v4.124.1：审查结果=不推 或 有 danger 时必须二次确认
        repo_d = None
        for r in self.candidates:
            if r.get("full_name") == self.repo:
                repo_d = r
                break
        ev = self.evaluations.get(self.repo) if self.repo else None
        risk_msg = []
        if repo_d and (repo_d.get("license") in ("", "—") or not repo_d.get("license")):
            risk_msg.append("· ⚠️ 这个仓库**没有明确 License**，装下来不受版权保护")
        if repo_d and repo_d.get("archived"):
            risk_msg.append("· 🗄 仓库已被作者**归档**，不会再维护")
        if ev and ev.get("recommend") == "不推":
            risk_msg.append("· ❌ PM 评「不推」：" + (ev.get("reasons") or ["（无）"])[0])
        if ev and ev.get("danger") and ev["recommend"] != "不推":
            risk_msg.append("· 🚩 红旗：" + ev["danger"])
        if risk_msg:
            r = QMessageBox.warning(
                self, "⚠️ 风险确认",
                "审查提示以下风险，**确定要装吗？**\n\n" + "\n".join(risk_msg),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                self._busy(False, "已取消安装。")
                return
        self._busy(True, "⏳ 正在安装…")
        self._run("install", repo=self.repo, branch=self.branch,
                  path=d.get("path"), slug=slug)


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
        # v4.124.1 修复：改「调度与授权」控件时，valueChanged 是在信号的派发栈上
        # 触发 _rebuild_waves()，而重建会销毁 sender（gate_box 整棵树），
        # 导致 "Internal C++ object already deleted" 段错误。
        # 改为用 QTimer.singleShot(0) 把重建推到事件循环下一轮，
        # 让 valueChanged 完整返回、Qt 不再访问 sender，再重建。
        self._gate_rebuild_pending = False
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
        # v4.123：先让 PM 组队，再手工微调，比从空白搭省事
        b_team = QPushButton("🧙 PM 出方案（我审批）")
        b_team.setToolTip("PM 只拟方案，你批准后角色才入职")
        b_team.setToolTip("说一句你要干什么，项目经理自动拉人排波次、配技能")
        b_team.clicked.connect(self._auto_team)
        b_add = QPushButton("+ 添加团队")
        b_add.clicked.connect(self._add_project)
        b_edit = QPushButton("项目信息")
        b_edit.clicked.connect(self._edit_project)
        b_dup = QPushButton("复制项目")
        b_dup.clicked.connect(self._dup_project)
        b_del = QPushButton("删除项目")
        b_del.clicked.connect(self._del_project)
        b_skill = QPushButton("🔧 装技能（GitHub）")
        b_skill.setToolTip("技能库里缺方法论时，从 GitHub 找 SKILL.md 装上")
        b_skill.clicked.connect(self._install_skill)
        for b in (b_team, b_add, b_edit, b_dup, b_del, b_skill):
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
        # v4.124.6：续跑按钮，默认隐藏（_refresh_head 按 cur_project_id 是否带 ckpt 切可见）
        self.resume_btn = QPushButton("⏵ 续跑")
        self.resume_btn.setVisible(False)
        self.resume_btn.clicked.connect(self._on_resume_clicked)
        # 用一个状态变量记录「续跑弹窗选了哪种模式」：None / "from_scratch" / "resume"
        self._pending_resume = None
        rrow.addWidget(self.resume_btn)
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

    def _resolve_skill_name(self, slug) -> str:
        """slug → "emoji name"，进程内缓存（避免每次刷新都全扫 skills 目录）。

        v4.121.5 起也能接受 {slug, name, emoji} 字典元素（archived_skills 用）：
        - 字典里的 name/emoji 是归档时的快照，优先用；磁盘重装回来后仍稳定显示原名
        - 兜底：缓存里没有时再走 _load_skill_prompt 拿最新
        """
        # v4.121.5: archived 元素是 dict，直接读快照
        snap_emoji = ""
        snap_name = ""
        if isinstance(slug, dict):
            snap_name = slug.get("name") or ""
            snap_emoji = slug.get("emoji") or ""
            slug = slug.get("slug", "")
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
        cached = self._skill_cache[slug]
        # 如果字典里有快照而缓存里没 emoji（说明磁盘已无此 skill），优先用快照
        if snap_name and (cached.startswith("⚠") or " " not in cached.strip()):
            return f"{snap_emoji} {snap_name}".strip()
        return cached

    # ---- 项目列表 ----
    def _refresh_projects(self, select_id=None):
        target = select_id or self.cur_project_id
        self.proj_list.blockSignals(True)
        self.proj_list.clear()
        for p in self.data.get("projects", []):
            ck = legion.has_checkpoint(p.get("id", ""))
            if ck:
                # v4.124.6：有 checkpoint → 项目后加 🟡 + 提示文字
                tag = f"  🟡 续(第{ck.get('last_completed_wave', 0)+1}波)"
            else:
                tag = ""
            it = QListWidgetItem(f"{p.get('emoji', '')} {p.get('name', '未命名')}".strip() + tag)
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
        # v4.124.6：续跑按钮的可见性（依赖 cur_project_id 是否有 ckpt）
        has_ck = bool(p and legion.has_checkpoint(p.get("id", "")))
        if hasattr(self, "resume_btn"):
            self.resume_btn.setVisible(bool(has_ck))
            if has_ck:
                ck = legion.has_checkpoint(p["id"])
                self.resume_btn.setText(
                    f"⏵ 续跑(第{ck.get('last_completed_wave', 0)+1}波)")
                self.resume_btn.setToolTip(
                    f"上次中断于：{ck.get('saved_at_human', '')}\n"
                    f"已完成 {ck.get('last_completed_wave', 0)+1} 波\n"
                    "点击会弹窗让你选：续/重/取")
        if not p:
            self.head.setText("尚未选择项目")
            self.sub.setText("点「+ 添加团队」新建一个")
            return
        ck_tag = ""
        if has_ck:
            ck = legion.has_checkpoint(p["id"])
            ck_tag = (f"  ·  💾 上次中断：第 {ck.get('last_completed_wave', 0)+1} 波完成 "
                      f"({ck.get('saved_at_human', '')})")
        self.head.setText(f"{p.get('emoji', '')} {p.get('name', '')}".strip())
        self.sub.setText(f"{p.get('category', '')} · {legion.project_summary(p)}"
                         f"{(' · ' + p['description']) if p.get('description') else ''}{ck_tag}")

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

    def _auto_team(self):
        """🧙 让项目经理按需求自动组队（v4.123）。

        没项目就先建一个空的；应用后覆盖该项目的波次编排。
        """
        p = self._cur_project()
        created = False
        if not p:
            p = legion.new_project(name="新军团", emoji="🧙")
            self.data.setdefault("projects", []).append(p)
            created = True
        dlg = TeamBuildDialog(self.mw, p, self.data.get("role_library") or [],
                              self.data, self)
        if dlg.exec() != QDialog.Accepted:
            # 只为组队而建的空项目，取消了就撤掉，不留垃圾
            if created:
                self.data["projects"] = [
                    x for x in self.data.get("projects", []) if x is not p]
            return
        legion.save_legion(self.data)
        self._refresh_projects(select_id=p.get("id"))
        self._rebuild_waves()

    def _install_skill(self):
        """独立入口：不组队也能补技能（PM 请示之外，你自己想装也行）。"""
        dlg = SkillInstallDialog(mw=self.mw, parent=self)
        dlg.exec()

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

        # ---- 调度与授权（v4.123：宪法第二章 —— 授权权在你手里，PM 只有建议权）----
        gate_box = QGroupBox("调度与授权")
        gv = QVBoxLayout(gate_box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("模式"))
        mode_combo = QComboBox()
        mode_combo.addItem("关闭（一路跑完，不验收）", "off")
        mode_combo.addItem("顾问模式（PM 出建议，按建议走）", "advisory")
        mode_combo.addItem("人手把关（每波暂停等你批准）", "human")
        mode_combo.setToolTip(
            "宪法第二章：重要节点必须你授权，项目经理**没有放行权**，只有建议权。\n"
            "· 关闭：不要调度验收，一口气跑完（最省）。\n"
            "· 顾问模式：PM 出《执行计划》并逐波验收，按它的建议执行（老行为）。\n"
            "· 人手把关（推荐）：每波跑完弹窗问你 —— 放行 / 打回重做 / 终止。\n"
            "  等你点头才继续；超时按**不授权**处理，停在原地并保留产出。")
        cur_mode = (p.get("gate_mode")
                    or ("advisory" if p.get("gate_enabled") else "off"))
        mode_combo.setCurrentIndex(max(0, mode_combo.findData(cur_mode)))
        row1.addWidget(mode_combo)

        row1.addWidget(QLabel("重跑上限"))
        gate_spin = QSpinBox()
        gate_spin.setRange(0, 3)
        gate_spin.setSuffix(" 次")
        gate_spin.setFixedWidth(90)
        try:
            gate_spin.setValue(max(0, int(p.get("gate_max_retry") or 0)))
        except (TypeError, ValueError):
            gate_spin.setValue(1)
        gate_spin.setToolTip("顾问模式下 FAIL 最多自动重跑几次；用尽则标红放行。\n"
                             "人手把关模式下以你的决定为准。")
        row1.addWidget(gate_spin)
        row1.addStretch(1)
        gv.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("自动放行：同类批准满"))
        ap_spin = QSpinBox()
        ap_spin.setRange(0, 9)
        ap_spin.setFixedWidth(60)
        try:
            ap_spin.setValue(max(0, int(p.get("auto_pass_after") or 0)))
        except (TypeError, ValueError):
            ap_spin.setValue(0)
        ap_spin.setToolTip(
            "0 = **永不自动放行**（默认，最合宪）：每波都弹窗问你。\n"
            "设成 N：同一波次+同一批成员+同一任务（指纹相同）你亲手批准满 N 次后，\n"
            "第 N+1 次起可在围栏内自动放行 —— 换人/换波次/换任务，指纹立刻变，\n"
            "必须重新回到人手。")
        row2.addWidget(ap_spin)
        row2.addWidget(QLabel("次后自动放行，最多"))
        ap_max = QSpinBox()
        ap_max.setRange(0, 99)
        ap_max.setFixedWidth(60)
        try:
            ap_max.setValue(max(0, int(p.get("auto_pass_max") or 3)))
        except (TypeError, ValueError):
            ap_max.setValue(3)
        ap_max.setToolTip("自动放行累计上限，用尽即收回人手（次数围栏）。")
        row2.addWidget(ap_max)
        row2.addWidget(QLabel("次"))

        b_revoke = QPushButton("↩ 收回全部信任")
        b_revoke.setToolTip(
            "一键清空所有「同类自动放行」的累积信任（并记进审计日志）。\n"
            "发现 PM 放错一次，点这里立刻收回 —— 信任是累积的，也必须可撤销。")
        b_revoke.clicked.connect(self._on_revoke_trust)
        row2.addWidget(b_revoke)

        b_auth = QPushButton("🔍 授权记录")
        b_auth.setToolTip("查看每一次放行是谁批的（你 / 围栏自动 / 超时未授权）以及依据。")
        b_auth.clicked.connect(self._on_view_auth)
        row2.addWidget(b_auth)
        row2.addStretch(1)
        gv.addLayout(row2)

        # 先 setCurrentIndex/setValue 再 connect：避免重建 UI 时触发一次无谓的写盘
        mode_combo.currentIndexChanged.connect(
            lambda _i: self._on_gate_changed(mode=mode_combo.currentData()))
        gate_spin.valueChanged.connect(
            lambda v: self._on_gate_changed(retry=int(v)))
        ap_spin.valueChanged.connect(
            lambda v: self._on_gate_changed(auto_after=int(v)))
        ap_max.valueChanged.connect(
            lambda v: self._on_gate_changed(auto_max=int(v)))
        self.waves_lay.addWidget(gate_box)

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
                archived = m.get("archived_skills") or []
                # 工具 + 技能 + 已下架 三段拼接展示（v4.121.4 加已下架段）
                tool_txt = ("工具：" + "、".join(tools)) if tools else "不用工具 · 纯输出"
                skill_txt = ""
                if skills:
                    names = [self._resolve_skill_name(s) for s in skills]
                    skill_txt = " | 技能：" + "、".join(n for n in names if n)
                if archived:
                    # _resolve_skill_name 对不存在的 slug 会前缀 ⚠，正好做视觉提示
                    names = [self._resolve_skill_name(s) for s in archived]
                    skill_txt += " | 已下架：" + "、".join(n for n in names if n)
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

    def _on_gate_changed(self, enabled=None, retry=None, mode=None,
                         auto_after=None, auto_max=None):
        """「调度与授权」设置变更（v4.123）。未传的项（None）表示不改。

        改完重建波次区让控件可用性跟随状态。
        （rebuild 里 setCurrentIndex/setValue 都在 connect 之前，不会递归触发本方法。）
        """
        p = self._cur_project()
        if not p:
            return
        if mode is not None:
            p["gate_mode"] = str(mode)
            # 与旧字段保持同步：非 off 即启用闸门
            p["gate_enabled"] = (str(mode) != "off")
            if str(mode) != "off" and not legion.pm_role(self.data):
                QMessageBox.information(
                    self, "缺少项目经理",
                    "角色库里没有「项目经理」角色，调度验收不会生效。\n"
                    "重启程序会自动补上该预置角色；或到角色库手动新建一个同名角色。")
        if enabled is not None:
            p["gate_enabled"] = bool(enabled)
        if retry is not None:
            try:
                p["gate_max_retry"] = max(0, int(retry))
            except (TypeError, ValueError):
                pass
        if auto_after is not None:
            try:
                p["auto_pass_after"] = max(0, int(auto_after))
            except (TypeError, ValueError):
                pass
        if auto_max is not None:
            try:
                p["auto_pass_max"] = max(0, int(auto_max))
            except (TypeError, ValueError):
                pass
        legion.save_legion(self.data)
        # v4.124.1 修复：不再在 valueChanged 派发栈上同步重建（会销毁 sender），
        # 推到下一轮事件循环，并防抖（用户连续点箭头/输入时只重建一次）。
        self._schedule_waves_rebuild()

    def _schedule_waves_rebuild(self):
        """延迟重建波次区，避免在控件的 valueChanged 派发栈上销毁 sender。"""
        if self._gate_rebuild_pending:
            return
        self._gate_rebuild_pending = True
        QTimer.singleShot(0, self._do_waves_rebuild)

    def _do_waves_rebuild(self):
        self._gate_rebuild_pending = False
        try:
            self._rebuild_waves()
        except Exception as e:
            log.warning("重建波次失败: %s", e)

    # ---- 授权：一键收回信任 / 查看审计 ----
    def _on_revoke_trust(self):
        n = legion.revoke_trust()
        QMessageBox.information(
            self, "已收回",
            f"已清空 {n} 条「同类自动放行」的信任记录，并记进授权审计日志。\n\n"
            f"此后每波都会重新等你亲手批准 —— 信任是累积的，也是可撤销的。")

    def _on_view_auth(self):
        recs = legion.read_auth(60)
        if not recs:
            QMessageBox.information(self, "授权记录",
                                    "还没有任何授权记录。跑一次带验收的军团后这里就有。")
            return
        by_txt = {"user": "👤 你批的", "auto": "🔓 围栏自动", "timeout": "⏱ 超时未授权",
                  "advisory": "📋 顾问模式（按建议执行，非授权）"}
        lines = []
        for r in recs:
            lines.append(
                f"{r.get('time','')} · {r.get('project_name','')} 第{r.get('wave')}波 · "
                f"{by_txt.get(r.get('by'), r.get('by'))}\n"
                f"   决策：{r.get('decision')} ｜ PM 建议：{r.get('pm_verdict') or '-'}\n"
                f"   依据：{r.get('reason') or '-'}（指纹 {r.get('fingerprint')}）")
        dlg = QDialog(self)
        dlg.setWindowTitle("授权审计记录（最近 %d 条）" % len(recs))
        dlg.resize(720, 480)
        lay = QVBoxLayout(dlg)
        tv = QTextEdit()
        tv.setReadOnly(True)
        tv.setPlainText("\n\n".join(lines))
        lay.addWidget(tv)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

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
        role = dlg.get_role()
        # 宪法第二章配套：调度器不当运动员 —— 项目经理不占波次执行位
        if legion.is_pm_role(role):
            QMessageBox.information(
                self, "项目经理不进波次",
                "「项目经理」是全局调度台：它跨波调度、验收并**催你授权**，\n"
                "不占某一波的执行位 —— 否则既浪费一个位置，又让它给自己那一波当裁判。\n\n"
                "开启「调度与授权」后，它会自动参与每一波（不消耗成员位）。")
            return
        p["waves"][wi].setdefault("members", []).append(role)
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
        """点 ▶ 启动军团 按钮：默认走"从头重跑"模式。

        v4.124.6：若 self._pending_resume == "resume"（_on_resume_clicked 设置），
        则传 resume_from / ckpt_run_id 给 LegionWorker → 跳到 ckpt 之后的波次。
        """
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
        # 传 self.data：让验收用上用户在角色库里定制过的「项目经理」
        # v4.124.6：续跑模式 → 带 resume_from + ckpt_run_id
        kwargs = {"legion_data": self.data}
        if self._pending_resume == "resume":
            ck = legion.has_checkpoint(p["id"])
            if ck:
                kwargs["resume_from"] = ck.get("last_completed_wave", 0) + 1
                kwargs["ckpt_run_id"] = ck.get("run_id", "")
        self.worker = LegionWorker(self.mw, p, task, **kwargs)
        self.worker.log_line.connect(self._on_log)
        self.worker.done.connect(self._on_done)
        self.worker.finished.connect(self._on_finished)
        # 宪法第二章：人手授权闸门（PM 只有建议权，批不批在这里由你定）
        self.worker.auth_request.connect(self._on_auth_request)
        self.worker.start()
        # 续跑模式用完即清
        self._pending_resume = None

    def _on_resume_clicked(self):
        """v4.124.6：点 ⏵ 续跑 按钮 → 弹「续/重/取」让大哥选。

        默认选中「从头重跑」防误点（毕竟点错了整个项目从头跑，最贵）；
        选「从上次续跑」会设 _pending_resume="resume"，下一次点 ▶ 启动军团 即走续跑路径；
        选「取消」则什么都不做。
        """
        p = self._cur_project()
        if not p:
            return
        ck = legion.has_checkpoint(p.get("id", ""))
        if not ck:
            QMessageBox.information(self, "没有 checkpoint",
                                    "这个项目没有存档可续跑。")
            return
        # 自定义 QMessageBox 让默认按钮 = "从头重跑"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("续跑 vs 从头重跑")
        box.setText(
            f"项目「{p.get('name', '')}」有存档：\n"
            f"  • 已完成：{ck.get('last_completed_wave', 0)+1} 波\n"
            f"  • 中断时间：{ck.get('saved_at_human', '')}\n"
            f"  • run_id：{ck.get('run_id', '')[:12]}…\n\n"
            "你要：")
        btn_resume = box.addButton("⏵ 从上次续跑", QMessageBox.AcceptRole)
        btn_rescratch = box.addButton("🔄 从头重跑（推荐默认）", QMessageBox.DestructiveRole)
        btn_cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(btn_rescratch)   # 默认选中「重跑」防误点
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_resume:
            self._pending_resume = "resume"
            self._log_line("📌 已选「从上次续跑」——点「▶ 启动军团」即从第 "
                           f"{ck.get('last_completed_wave', 0)+2} 波启动")
        elif clicked is btn_rescratch:
            self._pending_resume = "from_scratch"
            # 用户接下来点 ▶ 启动军团 即按 _run_legion 默认走（不带 resume_from）
            self._log_line("📌 已选「从头重跑」——点「▶ 启动军团」即清存档从头跑")
        else:
            self._pending_resume = None

    def _on_auth_request(self, title, detail):
        """波次授权弹窗：放行 / 打回重做 / 终止（宪法第二章）。

        子线程 emit → Qt 自动排队到主线程，这里是主线程，可安全弹模态框。
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("⏸ 等待你授权 · 宪法第二章")
        box.setText(title)
        box.setInformativeText(
            "项目经理已给出建议（详情展开）。它是**建议**，不是放行。\n"
            "放不放行由你点头 —— 这是重要节点的授权闸。")
        box.setDetailedText(detail)
        b_pass = box.addButton("✅ 放行下一波", QMessageBox.AcceptRole)
        b_reject = box.addButton("↩︎ 打回重做", QMessageBox.DestructiveRole)
        b_abort = box.addButton("⛔ 终止军团", QMessageBox.RejectRole)
        box.setDefaultButton(b_pass)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_pass:
            val = "pass"
        elif clicked is b_reject:
            val = "reject"
        else:
            val = "abort"
        try:
            if self.worker is not None:
                self.worker.set_auth_result(val)
        except Exception:
            pass

    def _on_log(self, text):
        self.log_view.insertPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_done(self, text):
        if text:
            self.log_view.append("\n" + "=" * 30 + " 军团产出 " + "=" * 30 + "\n")
            self.log_view.append(text)
        # v4.124：跑完给班子档案记一笔战绩——这个阵容到底行不行，
        # 下次同类需求 PM 翻旧账时会优先推荐跑通过的。
        try:
            p = self._cur_project()
            rid = (p or {}).get("recipe_id")
            if rid:
                v = legion.parse_verdict(text or "")
                legion.mark_recipe_result(self.data, rid, ok=bool(v.get("pass")))
                legion.save_legion(self.data)
        except Exception as e:
            log.warning("回填班子战绩失败: %s", e)

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
