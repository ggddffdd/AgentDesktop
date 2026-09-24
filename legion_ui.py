# -*- coding: utf-8 -*-
"""Agent 军团面板 v4.124.11

把「编排页」从单一小说流水线扩展成**可自定义团队的多项目军团**：

- 左栏：项目列表（多项目并存）+「+ 添加团队」**v4.124.6：🟡 中断项目标 + 续跑入口**
- 右栏：当前项目的波次编排（波内并行、波间串行）+ 成员增删改移
- 底部：给军团下任务 → 启动 → 实时日志 + 产出

v4.124.6 改动：断点续传 UI 入口（项目主面板 / 续跑按钮 / 弹窗选「续/重/取」）。
        详见 LegionWorker.resume_from 字段 + legion.has_checkpoint()。
v4.124.8 改动：联系项目经理对话框（两档：备忘进队列 / 急件本波跑完立即响应），
        每句话进审计账本（legion.record_message），PM 判定需重走流程时弹窗拍板。
v4.124.9 改动：修「点续跑没动静」三根因——补 _log_line 方法（原来调用不存在的方法
        抛 AttributeError 静默失败）、续跑时任务框空自动复用存档任务、续跑按钮文案
        起点波号 +1→+2 对齐日志。
v4.124.10 改动：修「第 3 波 FAIL 却续(第4波)」——每波完成落盘用 last_passed_wave
        （最后 PASS 的波）而非 wi（当前波索引），避免未验收/FAIL 的波被当"完成"、
        续跑跳过它；PASS 波后立即落盘持久化新进度。
v4.124.11 改动：验收链五修（实盘跑通失败的五个根因）——
        ① 空产出不得放行：parse_verdict(has_output=False) 强制打回，PM 写 PASS 也不翻盘；
        ② 续跑 run_id 补登记（legion.ensure_run）—— 旧 run_id 不在内存状态仓导致
           record_log/record_output 全哑火、PM 三件套全瞎（审校读不到产出就是这个坑），
           续跑时同时回填存档里前几波产出；
        ③ 标的锁死：涉及选品/选题的任务，选品波必须收敛唯一标的，否则 FAIL；
        ④ 交付物形态第一道闸：交的不是约定形态（报告/说明/提示词）直接 FAIL；
        ⑤ 读不到产出禁止脑补：PM 与审校必须写 FAIL + 「产出不可读」，禁止复述打回指令。
v4.135.0 改动：军团双页 + 原生对话页（本次）：
        把「所有决策/成果/建议在一个对话页实时交互」落地，右栏拆 QStackedWidget 双页：
        ① 编排页（页0）：原波次编排/成员管理/任务下发，低频配置态，不动；
        ② 对话页（页1）：新建 legion_chat.LegionChatPanel —— 原生 QTextEdit 时间线 +
           输入框（**刻意不内嵌网页**，复用 director_chat 范式，避免再拉 QWebEngine 进程）；
        ③ 运行中日志/成果/PM 汇报/重编请求**同步进对话流**（信号桥接 log_line/done/
           replan_request），PM 每波结束一句话进展 + 下一步 + 提醒；
        ④ 授权去自动挡：auth_request 不再模态 dlg.exec() 挡输入，改为渲染进对话页 + 自动
           切到对话页，大哥在对话框里「放行/打回/终止」做决策；模态按钮弹窗抽成 _open_auth_dialog
           兜底（对话页「授权弹窗」按钮触发，保留宪法第二章人手放行权）；
        ⑤ 轻量意图解析 legion_chat.parse_intent（纯规则不调 LLM）：abort>reject>pass 优先级，
           非授权语境一律 message 路由给 PM；
        ⑥ 历史持久化 LEGION_DIR/legion_chat/<pid>.json 跟随项目，换项目 reload_for_project
           清/载最近 40 条；左栏（项目列表 + 8 按钮）两页共用，所有 self.xxx 引用只改父级不删。
"""
import os
import copy
import json
import logging

from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QListWidget,
    QListWidgetItem, QLineEdit, QPushButton, QLabel, QTextEdit, QComboBox,
    QMessageBox, QGroupBox, QScrollArea, QDialogButtonBox, QAbstractItemView,
    QSizePolicy, QCheckBox, QSpinBox, QInputDialog, QStackedWidget,
    QSplitter, QTextBrowser, QPlainTextEdit,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QBrush, QColor

import legion
from legion_worker import LegionWorker
from legion_chat import LegionChatPanel
from ui import THEME

log = logging.getLogger("legion")


class _NoWheelCombo(QComboBox):
    """v4.149.0：滚轮不切档的 QComboBox（军团编排页用）。

    为什么军团也要一份：**授权闸门（human/advisory/off）** 就在编排页上，它决定
    「每波是否停下来等大哥批准」—— 宪法第二章的红线。而 QComboBox 默认响应滚轮，
    光标滑过就切档；切档处理器 `_on_gate_changed` 会**直接落盘**到 legion.json。
    等于「滚过去一眼」就可能把『人手把关』降级成『关闭（一路跑完）』，零提示零确认。
    与 ui.py 的 `_NoWheelCombo` 同一策略：**未聚焦时忽略滚轮**，想滚轮换档先点它聚焦。
    """

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


def _add_skill_section_header(lst: QListWidget, text: str):
    """在 QListWidget 加一行不可勾选的分组标题（用于技能挂载的两段展示）。"""
    it = QListWidgetItem(text)
    # 不可选中 + 不可点：用户点上去没反应，视觉上像 header
    flags = it.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled
    it.setFlags(flags)
    it.setForeground(QBrush(QColor("#666")))
    # 禁用样式加重一点：灰底
    it.setBackground(QBrush(QColor(THEME["row_gray_bg"])))
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
        # v4.148.2（模型分档）：取证/批量岗用便宜快模型省 token，终稿/决策岗用强模型。
        self.e_model.setPlaceholderText("留空=跟随主模型")
        self.e_model.setToolTip(
            "模型分档（对标 CrewAI per-agent right-sizing）：\n"
            "· 取证/批量岗（研究员、竞品分析师）→ 填便宜快档位（如「Agnes」），省 token；\n"
            "· 终稿/决策岗（写手、项目经理）→ 留空走主模型（DeepSeek）。\n"
            "档位名必须与 config.json 的 model_profiles 里的名字完全一致；\n"
            "填了但该档位没配 api_key，该成员会调用失败 —— 拿不准就留空。")
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
                it.setForeground(QBrush(QColor(THEME["gray2"])))
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
                tools = []
                for r in self.library:
                    if r.get("name") == nm:
                        em = r.get("emoji", "")
                        tools = [t for t in (r.get("tools") or []) if t]
                        break
                seg = "     · %s %s" % (em, nm)
                if m.get("why"):
                    seg += " —— %s" % m["why"]
                # v4.134：把「手脚」摆出来 —— 成员有没有工具，一眼就能看出
                # （无工具的纯生成型成员跑起来只能凭记忆编，组队时就得知道）
                seg += "\n         🔧 工具：%s" % (
                    "、".join(tools) if tools
                    else "（无 —— 一个工具都调不了，开工时需项目经理在【能力配置】里配）")
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
        # v4.134.2：挂载改走数据层唯一落地点 —— 与主窗口「🔧 装技能」共用同一段
        # 逻辑（plan 与 proj 的 waves 结构相同），免得两处各写一份、日后行为漂移。
        _waves, _msg = legion.attach_skill_to_project(
            self.plan, slug, target.get("name"))
        self.out.setPlainText(self._fmt(self.plan))
        self.status.setText(_msg if _waves else ("⚠️ " + _msg))

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

    def _on_file(self):
        it = self.lst_file.currentItem()
        if not it:
            return
        d = it.data(Qt.UserRole) or {}
        self.ed_slug.setText(d.get("slug") or "")
        self.b_inst.setEnabled(True)
        self._busy(True, "⏳ 正在拉内容预览…")
        self._run("preview", repo=self.repo, branch=self.branch, path=d.get("path"))

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
                _tail = "\n\n关掉这个窗口后，可以把技能直接挂给需要的角色。"
                # v4.134.1：静态安全审计给了 P1 提示（或审计器不可用）时抬成警告弹窗
                if "⚠️" in msg:
                    QMessageBox.warning(self, "装好了（有提示）", msg + _tail)
                else:
                    QMessageBox.information(self, "装好了", msg + _tail)

    # ---- 结构化候选 + 审查报告 ----
    _TAG = {
        "强推": ("✅ 强推", THEME["on_green2"]),
        "推":   ("✅ 推荐", THEME["on_green2"]),
        "慎":   ("⚠️ 慎装", THEME["warn_gold2"]),
        "不推": ("❌ 不推", THEME["tag_red"]),
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
                it.setBackground(QBrush(QColor(color if ev["recommend"] != "不推" else THEME["danger_bg2"])))
                it.setForeground(QBrush(QColor(THEME["white"] if ev["recommend"] in ("强推", "推") else THEME["text_dark"])))
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


# ============ 技能缺口候选面板（v4.135 · 闭环）============
class SkillGapCandidateDialog(QDialog):
    """v4.135：技能缺口候选面板（闭合「差技能→去 GitHub 找→批→装→挂」断环）。

    跑批检测到的技能缺口（名字+给谁用+干什么用）自动去 GitHub 找候选仓库，
    列出 ⭐/📅/📜/archived 等结构化信息；点「安装」才装——
    装前过内容级静态安全审计（install_skill_from_github 内部），装完自动挂给对应角色。
    搜索只是「给报告」（大哥设计的机制一步），真正落盘要你点头（宪法第二章）。
    """

    def __init__(self, parent, gaps, mw=None, project=None, data=None):
        super().__init__(parent)
        self.mw = mw
        self.project = project
        self.data = data
        self.gaps = [g for g in (gaps or []) if isinstance(g, dict) and g.get("name")]
        self.setWindowTitle("🛡 技能缺口候选（自动找好，你批了才装）")
        self.resize(760, 580)
        self.gap_lists = {}
        self.gap_status = {}
        self.gap_meta = {}
        self.resolved = set()
        self.worker = None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "本次跑批检测到 %d 个技能库里没有的需求。已自动去 GitHub 找候选"
            "（仅搜索，不安装）。点「安装」才装：装前过内容级安全审计，"
            "装完自动挂给对应角色。" % len(self.gaps)))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)

        self.status = QLabel("")
        lay.addWidget(self.status)

        row = QHBoxLayout()
        self.b_rescan = QPushButton("🔎 重新搜索")
        self.b_rescan.clicked.connect(self._search_all)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        row.addWidget(self.b_rescan)
        row.addStretch(1)
        row.addWidget(b_close)
        lay.addLayout(row)

        self._build_gap_sections()
        # 弹窗画好后再自动搜（避免卡绘制），这就是「去 GitHub 找」自动触发
        QTimer.singleShot(200, self._search_all)

    # ---- 缺口分段 ----
    def _build_gap_sections(self):
        seen = set()
        for g in self.gaps:
            nm = g.get("name")
            if nm in seen:
                continue
            seen.add(nm)
            self.gap_meta[nm] = g
            who = g.get("for_role") or "未指定角色"
            why = g.get("why") or ""
            gb = QGroupBox("🔧 缺口：%s（给 %s 用）" % (nm, who))
            gl = QVBoxLayout(gb)
            if why:
                gl.addWidget(QLabel("用途：%s" % why))
            lw = QListWidget()
            lw.setMaximumHeight(170)
            gl.addWidget(lw)
            st = QLabel("⏳ 正在去 GitHub 找候选…")
            gl.addWidget(st)
            self.body_lay.addWidget(gb)
            self.gap_lists[nm] = lw
            self.gap_status[nm] = st

    def _gap_for(self, nm):
        return (self.gap_meta.get(nm) or {}).get("for_role") or ""

    # ---- 搜索 ----
    def _search_all(self):
        for nm in list(self.gap_lists.keys()):
            self._search_one(nm)

    def _search_one(self, nm):
        st = self.gap_status.get(nm)
        lw = self.gap_lists.get(nm)
        if st is not None:
            st.setText("⏳ 正在搜 GitHub：%s …" % nm)
        if lw is not None:
            lw.clear()
        w = GHWorker("search", self, query=nm, gap_name=nm)
        w.done.connect(lambda pl, err, _w=w: self._on_search_done(pl, err, _w))
        w.start()

    def _on_search_done(self, payload, err, w):
        nm = (w.kw.get("gap_name") if w else None) or None
        lw = self.gap_lists.get(nm) if nm else None
        st = self.gap_status.get(nm) if nm else None
        if lw is None or st is None:
            return
        repos = payload or []
        if err:
            st.setText("❌ " + err)
            return
        if not repos:
            st.setText("ℹ️ GitHub 未搜到相关仓库（可去主窗口「🔧 装技能」换词手动搜）")
            return
        for r in repos:
            self._add_candidate_item(lw, r, nm)
        st.setText("✅ 找到 %d 个候选仓库，点「安装」才装。" % len(repos))

    def _add_candidate_item(self, lw, repo, nm):
        lic = legion._safe_license_text(repo)
        txt = "⭐%s  %s  📅%s  📜%s%s\n    %s" % (
            repo.get("stars", 0), repo.get("full_name", ""),
            repo.get("updated", ""), lic,
            "  🗄 已归档" if repo.get("archived") else "",
            repo.get("description", "（无描述）"))
        item = QListWidgetItem()
        item.setData(Qt.UserRole, repo)
        lw.addItem(item)
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(2, 2, 2, 2)
        hl.addWidget(QLabel(txt), 1)
        btn = QPushButton("⬇ 安装")
        btn.clicked.connect(
            lambda _=False, r=repo, g={"name": nm, "for_role": self._gap_for(nm),
                                       "why": (self.gap_meta.get(nm) or {}).get("why", "")}:
            self._install_candidate(r, g))
        hl.addWidget(btn)
        lw.setItemWidget(item, row)

    # ---- 安装闭环 ----
    def _install_candidate(self, repo, gap):
        full = repo.get("full_name")
        if not full:
            return
        lic = repo.get("license") or "—"
        risk = []
        if not lic or lic == "—":
            risk.append("· ⚠️ 仓库没有明确 License，装下来不受版权保护")
        if repo.get("archived"):
            risk.append("· 🗄 仓库已被归档，不会再维护")
        if risk:
            r = QMessageBox.warning(
                self, "⚠️ 风险确认",
                "以下风险，确定要装吗？\n\n" + "\n".join(risk),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        self.status.setText("⏳ 正在列 %s 里的 SKILL.md…" % full)
        w = GHWorker("list", self, repo=full, branch=repo.get("branch"))
        w.done.connect(
            lambda pl, err, _w=w, _repo=repo, _gap=gap:
            self._on_list_done(pl, err, _w, _repo, _gap))
        w.start()

    def _on_list_done(self, payload, err, w, repo, gap):
        files = payload or []
        if err:
            self.status.setText("❌ 列文件失败：" + err)
            return
        if not files:
            self.status.setText("❌ 这个仓库里没找到 SKILL.md。")
            return
        if len(files) == 1:
            self._do_install(repo, files[0], gap)
            return
        labels = ["%s（%s）" % (f.get("path"), f.get("slug")) for f in files]
        pick, ok = QInputDialog.getItem(
            self, "选 SKILL.md",
            "仓库 %s 里有 %d 个 SKILL.md，装哪个？" % (repo.get("full_name"), len(files)),
            labels, 0, False)
        if not ok or not pick:
            self.status.setText("已取消安装。")
            return
        try:
            idx = labels.index(pick)
        except Exception:
            return
        self._do_install(repo, files[idx], gap)

    def _do_install(self, repo, f, gap):
        slug = f.get("slug") or ""
        if not slug:
            self.status.setText("❌ 推不出 slug，没法装。")
            return
        self.status.setText("⏳ 正在安装 %s …" % slug)
        w = GHWorker("install", self, repo=repo.get("full_name"),
                     path=f.get("path"), branch=repo.get("branch"), slug=slug)
        w.done.connect(
            lambda pl, err, _w=w, _gap=gap, _slug=slug, _repo=repo:
            self._on_install_done(pl, err, _gap, _slug, _repo))
        w.start()

    def _on_install_done(self, payload, err, gap, slug, repo):
        if err:
            self.status.setText("❌ 安装失败：" + err)
            return
        ok = (payload or {}).get("ok")
        msg = (payload or {}).get("msg") or ""
        if not ok:
            self.status.setText("❌ " + msg)
            return
        who = gap.get("for_role")
        attach_msg = ""
        if who and self.project:
            try:
                _waves, _m = legion.attach_skill_to_project(self.project, slug, who)
                attach_msg = "｜" + _m
                if _waves:
                    try:
                        legion.save_legion(self.data)
                        parent = self.parent()
                        if parent is not None and hasattr(parent, "_rebuild_waves"):
                            parent._rebuild_waves()
                    except Exception:
                        pass
            except Exception as e:
                attach_msg = "｜⚠️ 挂接失败：%s" % e
        elif who and not self.project:
            attach_msg = "｜（未选项目，未自动挂；可去「🔧 装技能」手动挂）"
        self.resolved.add(gap.get("name"))
        st = self.gap_status.get(gap.get("name"))
        if st is not None:
            st.setText("✅ 已安装 %s 并挂给 %s。下一波/重跑立即生效。"
                       % (slug, who or "—"))
        self.status.setText("✅ %s%s" % (msg, attach_msg))


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

        # v4.148.2：配方化 —— 任务模板 + 填空变量（inputs）。留空 = 普通项目。
        self.e_tpl = QLineEdit(p.get("task_template", ""))
        self.e_tpl.setPlaceholderText(
            "可选。如：对 {region} 的 {category} 做选品全链路 —— {key} 会被填空替换")
        self.e_tpl.setToolTip(
            "任务模板：团队库点「▶ 启动此团队」时按下面的填空变量渲染成任务。\n"
            "留空 = 普通项目（启动时手动输任务）。")
        self.e_inputs = QPlainTextEdit()
        self.e_inputs.setPlainText(
            json.dumps(p.get("inputs") or [], ensure_ascii=False, indent=2))
        self.e_inputs.setFixedHeight(110)
        self.e_inputs.setPlaceholderText(
            '填空变量（JSON 数组）：[{"key":"region","label":"目标站点",\n'
            '  "default":"马来西亚(MY)","placeholder":"可选提示"}]')

        form = QFormLayout()
        form.addRow("图标 + 项目名称", row)
        form.addRow("说明", self.e_desc)
        form.addRow("分类", self.e_cat)
        form.addRow("任务模板（配方）", self.e_tpl)
        form.addRow("填空变量（配方）", self.e_inputs)

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
        # v4.148.2：inputs 必须是合法 JSON 数组（留空 = 无填空变量）
        if self.e_inputs.toPlainText().strip():
            try:
                val = json.loads(self.e_inputs.toPlainText())
                if not isinstance(val, list):
                    raise ValueError("必须是 JSON 数组")
                for item in val:
                    if not isinstance(item, dict) or not item.get("key"):
                        raise ValueError("每项都要有 key 字段")
            except Exception as e:
                QMessageBox.warning(self, "填空变量格式不对",
                                    f"inputs 需要是 JSON 数组，每项含 key：\n{e}")
                return
        self.accept()

    def get_data(self):
        inputs = []
        if self.e_inputs.toPlainText().strip():
            try:
                inputs = json.loads(self.e_inputs.toPlainText())
            except Exception:
                inputs = []   # _on_ok 已拦截，这里只兜底
        return {
            "name": self.e_name.text().strip(),
            "emoji": self.e_emoji.text().strip(),
            "description": self.e_desc.toPlainText().strip(),
            "category": self.e_cat.currentText(),
            # v4.148.2：配方字段
            "task_template": self.e_tpl.text().strip(),
            "inputs": inputs,
        }


# ============ 配方启动 · 填空对话框（v4.148.2，对标 CrewAI inputs 模板变量）============
class RecipeLaunchDialog(QDialog):
    """按配方的 inputs 弹表单填空，渲染 task_template 成最终任务。"""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"启动配方 · {project.get('name', '')}")
        self.resize(460, 200)
        self._project = project
        form = QFormLayout()
        self._edits = {}
        for spec in (project.get("inputs") or []):
            key = spec.get("key") or ""
            if not key:
                continue
            ed = QLineEdit(str(spec.get("default") or ""))
            ed.setPlaceholderText(str(spec.get("placeholder") or ""))
            self._edits[key] = ed
            form.addRow(spec.get("label") or key, ed)
        if not self._edits:
            self.reject()   # 没有可填项（理论不达，保险）
            return
        hint = QLabel("填完点「启动」—— 任务会按模板自动拼好交给项目经理。")
        hint.setStyleSheet("color:#888;font-size:12px;")
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("🚀 启动")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(hint)
        lay.addWidget(btns)

    def _on_ok(self):
        # 必填校验：有 placeholder 提示且无默认值的，留空不给过（缺失交给 PM 澄清也行，
        # 但既然弹了表单，填全体验更顺）
        for key, ed in self._edits.items():
            if not ed.text().strip():
                QMessageBox.warning(self, "还有没填的", f"「{key}」还没填 —— "
                                    "留空的话请回团队库直接启动（跳过填空）。")
                return
        self.accept()

    def values(self):
        return {k: ed.text().strip() for k, ed in self._edits.items()}


# ============ 军团主窗口 ============
class LegionWindow(QWidget):
    """多项目军团管理 + 编排 + 执行。"""

    def __init__(self, mw=None, parent=None, embedded=False):
        super().__init__(parent)
        self.mw = mw
        # v4.135.0：embedded=True → 作为主窗口内嵌面板（不设 Qt.Window、不自定尺寸/居中，
        # 布局交给主窗口）；False → 保持独立窗口（旧行为，兼容 _open_legion 兜底与冒烟测试）。
        self.embedded = bool(embedded)
        self.data = legion.load_legion()
        self.cur_project_id = None
        self.worker = None
        # v4.137：正在执行的 worker 所属项目 id。用来识别「僵尸任务」——
        # 项目被删掉了但 worker 还在跑，此时光靠 isRunning() 判断会把
        # 整个军团的启动入口永久锁死，必须先能把执行对象认出来。
        self._worker_pid = ""
        # v4.124.1 修复：改「调度与授权」控件时，valueChanged 是在信号的派发栈上
        # 触发 _rebuild_waves()，而重建会销毁 sender（gate_box 整棵树），
        # 导致 "Internal C++ object already deleted" 段错误。
        # 改为用 QTimer.singleShot(0) 把重建推到事件循环下一轮，
        # 让 valueChanged 完整返回、Qt 不再访问 sender，再重建。
        self._gate_rebuild_pending = False
        self.setWindowTitle("Agent 军团 · 自定义团队")
        if not self.embedded:
            # 必须是独立窗口：否则会当成父窗口里的子控件，落在左上角盖住编排页，
            # 且没有自己的标题栏/关闭按钮。Qt.Window 让它带标题栏 + 关闭 X。
            # （内嵌模式反过来——绝不能设 Qt.Window，否则在页面里又弹独立窗。）
            self.setWindowFlags(self.windowFlags() | Qt.Window)
            self.resize(1040, 720)
        self._build_ui()
        self._refresh_projects()
        if not self.embedded:
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
        b_skill.setToolTip("技能库里缺方法论时，从 GitHub 找 SKILL.md 装上。\n"
                           "装完会问挂给谁 —— 挂上后下一波 / 打回重跑立即生效\n"
                           "（项目经理报了差技能的话，名字在执行日志里）")
        b_skill.clicked.connect(self._install_skill)
        # v4.124.14：记忆层必须「看得见、清得掉」——
        # 否则换了个新任务，旧的补录数据/锁定标的还在暗处把方向带跑偏。
        b_manual = QPushButton("📎 补录数据")
        b_manual.setToolTip("管理手工补录文件：挂载到本项目 / 归档（不再被任何任务自动读取）")
        b_manual.clicked.connect(self._manage_manual)
        b_wipe = QPushButton("🧹 清记忆")
        b_wipe.setToolTip("清空本项目的锁定标的 / 否决黑名单 / 项目教训本")
        b_wipe.clicked.connect(self._wipe_memory)
        # v4.124.15：报告入口（跑完的成稿落在 ~/Documents/小臭玩AI/legion_reports/）
        b_report = QPushButton("📄 上次报告")
        b_report.setToolTip("打开最近一次军团的报告文件（落盘位置 legion_reports/）")
        b_report.clicked.connect(self._open_last_report)
        for b in (b_team, b_add, b_edit, b_dup, b_del, b_skill, b_manual, b_wipe,
                  b_report):
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
        # v4.146：任务模板（反复任务一键加载 task_hint，开工前能力审计据此跑）
        self.tpl_btn = QPushButton("📋 任务模板")
        self.tpl_btn.setToolTip("保存/套用/删除任务模板（must_roles/must_capabilities/task_hint）")
        self.tpl_btn.clicked.connect(self._open_task_templates)
        rrow.addWidget(self.tpl_btn)
        # v4.137：常驻「停止」入口。此前唯一的终止按钮藏在 human 模式的授权弹窗里，
        # 波内执行阶段（最耗时、也正是最想停的时候）界面上压根没有出口 ——
        # 表现为「删了团队它还在跑，而且停不下来」。运行时才显示。
        self.stop_btn = QPushButton("⛔ 停止")
        self.stop_btn.setToolTip(
            "向军团发出停止指令：\n"
            "  • 它会跑完正在进行的这一步后收尾，不把成员调用拦腰砍断\n"
            "  • 已产出的东西保留，可续跑\n"
            "（想更硬一点：删除该项目时选「立即停止并删除」）")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        rrow.addWidget(self.stop_btn)
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
        # v4.124.8：联系项目经理 —— 军团运行时把要求传给 PM（两档：备忘 / 急件）
        self.pm_box = QGroupBox("联系项目经理")
        pm_lay = QVBoxLayout(self.pm_box)
        self.pm_msg_edit = QLineEdit()
        self.pm_msg_edit.setPlaceholderText("给项目经理传句话，例如：把选品范围收窄到 3C 类目")
        self.pm_msg_edit.returnPressed.connect(self._on_send_pm)
        pm_row = QHBoxLayout()
        self.pm_urgent_cb = QCheckBox("急件（本波跑完立即响应）")
        self.pm_urgent_cb.setToolTip(
            "默认「备忘」：进队列，下一个波界交给 PM。\n"
            "勾选「急件」：本波跑完后立即打断、PM 就地响应。\n"
            "两种都会进审计账本（与授权同一 run_id 谱系）。")
        self.pm_send_btn = QPushButton("发送")
        self.pm_send_btn.clicked.connect(self._on_send_pm)
        pm_row.addWidget(self.pm_urgent_cb)
        pm_row.addStretch(1)
        pm_row.addWidget(self.pm_send_btn)
        pm_lay.addWidget(self.pm_msg_edit)
        pm_lay.addLayout(pm_row)
        self.pm_box.setEnabled(False)   # 军团运行且开启验收（有 PM）时才启用
        rb.addWidget(self.pm_box)
        rv.addWidget(run_box)

        # v4.148.1（UI 化繁为简，大哥定调）：页面重排为「💬 聊天（默认）/ 👥 角色库 /
        # 🧩 团队库 / ⚙️ 编排（高级，默认隐藏）」。编排页整段代码保留不删 —— 续跑/
        # 波次微调/补录数据等高级操作仍挂在它上面，点 ⚙️ 随时可展开；日常只面对聊天页。
        # 左栏（项目列表 + 按钮）不动 —— 项目即团队的载体，很多回调查它。
        self.chat_panel = LegionChatPanel(mw=self.mw)
        self.chat_panel.set_pm_sender(self._chat_send_pm)
        self.chat_panel.set_auth_resolver(self._chat_resolve_auth)
        self.chat_panel.set_auth_dialog_opener(self._open_auth_dialog)
        self.chat_panel.set_stopper(self._on_stop_clicked)   # v4.137 对话页也能停
        self.chat_panel.set_skill_helper(self._skill_cmd)    # v4.139 对话里查/搜/装技能
        self.chat_panel.set_skill_installer(self._install_skill)  # v4.144 对话页也能装技能
        self.chat_panel.set_launcher(self._chat_launch)      # v4.148.1 聊天框直接「启动 …」

        right_container = QWidget()
        rcv = QVBoxLayout(right_container)
        rcv.setContentsMargins(0, 0, 0, 0)
        rcv.setSpacing(6)

        # 顶部 tab 切换条（聊天 / 角色库 / 团队库 / ⚙️编排）
        tab_row = QHBoxLayout()
        tab_row.setSpacing(6)
        self._tab_chat = QPushButton("💬 聊天")
        self._tab_roles = QPushButton("👥 角色库")
        self._tab_teams = QPushButton("🧩 团队库")
        self._tab_orch = QPushButton("📐 编排")
        for _tb in (self._tab_chat, self._tab_roles, self._tab_teams, self._tab_orch):
            _tb.setCheckable(True)
            _tb.setCursor(Qt.PointingHandCursor)
            _tb.setFixedHeight(34)
        self._tab_chat.setChecked(True)          # 默认进聊天页
        self._tab_chat.clicked.connect(lambda: self._switch_page(0))
        self._tab_roles.clicked.connect(lambda: self._switch_page(1))
        self._tab_teams.clicked.connect(lambda: self._switch_page(2))
        self._tab_orch.clicked.connect(lambda: self._switch_page(3))
        tab_row.addWidget(self._tab_chat)
        tab_row.addWidget(self._tab_roles)
        tab_row.addWidget(self._tab_teams)
        # ⚙️ 切换「编排（高级）」页的显隐：只藏 tab 按钮，不销毁任何控件/信号，
        # 高级操作（续跑、波次微调、补录、清记忆）都还在编排页上，随时可展开。
        self._gear_btn = QPushButton("⚙️")
        self._gear_btn.setCheckable(True)
        self._gear_btn.setFixedHeight(34)
        self._gear_btn.setFixedWidth(44)
        self._gear_btn.setToolTip("展开「编排」高级页：波次微调 / 续跑 / 补录数据 / 清记忆")
        self._gear_btn.setCursor(Qt.PointingHandCursor)
        self._gear_btn.toggled.connect(self._toggle_orch_tab)
        tab_row.addWidget(self._gear_btn)
        tab_row.addStretch(1)
        rcv.addLayout(tab_row)

        # 三张新页
        self._roles_page = self._build_roles_page()
        self._teams_page = self._build_teams_page()

        self._stack = QStackedWidget()
        self._stack.addWidget(self.chat_panel)   # 页0 聊天（默认）
        self._stack.addWidget(self._roles_page)  # 页1 角色库
        self._stack.addWidget(self._teams_page)  # 页2 团队库
        self._stack.addWidget(right)             # 页3 编排（原右栏，高级）
        rcv.addWidget(self._stack, 1)

        self._style_tabs()
        self._toggle_orch_tab(False)             # 默认藏编排 tab
        root.addWidget(right_container, 1)

    def _toggle_orch_tab(self, on):
        """⚙️ 开关：显示/隐藏「编排」高级页 tab。开着时编排序号仍为 3。"""
        self._tab_orch.setVisible(bool(on))
        if not on and self._stack.currentIndex() == 3:
            self._switch_page(0)

    def _switch_page(self, idx):
        """切页并刷新 tab 高亮（0 聊天 / 1 角色库 / 2 团队库 / 3 编排-高级）。"""
        self._stack.setCurrentIndex(idx)
        # 直调（非点击）时也要同步 checked 态 —— 点击路径 Qt 自己会切，
        # 程序路径（如团队库点启动跳聊天页）必须手动对齐。
        self._tab_chat.setChecked(idx == 0)
        self._tab_roles.setChecked(idx == 1)
        self._tab_teams.setChecked(idx == 2)
        self._tab_orch.setChecked(idx == 3)
        if idx == 1:
            self._refresh_roles_page()
        elif idx == 2:
            self._refresh_teams_page()
        self._style_tabs()

    def _style_tabs(self):
        """按选中态给 tab 上色（active=accent / inactive=card）。"""
        from ui import THEME
        active = (f"QPushButton{{background:{THEME['accent']};color:#fff;border:none;"
                  f"border-radius:8px;padding:0 18px;font-size:13px;font-weight:600;}}")
        inactive = (f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
                    f"border:1px solid {THEME['border']};border-radius:8px;padding:0 18px;"
                    f"font-size:13px;}}"
                    f"QPushButton:disabled{{color:{THEME['faint']};}}")
        for _tb in (self._tab_chat, self._tab_roles, self._tab_teams, self._tab_orch):
            _tb.setStyleSheet(active if _tb.isChecked() else inactive)

    # ---- v4.148.1：角色库页 ----
    def _build_roles_page(self):
        """角色库：左边角色清单，右边「员工简历」详情（对标 TeamWork 员工卡）。"""
        from ui import THEME
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("角色库（角色即能力 —— 工具/技能/纪律都在角色卡上，"
                            "PM 不再代配）"))
        self.roles_list = QListWidget()
        self.roles_list.currentItemChanged.connect(self._on_role_selected)
        lv.addWidget(self.roles_list, 1)
        b_role_new = QPushButton("+ 新建角色")
        b_role_new.clicked.connect(self._add_standalone_role)
        lv.addWidget(b_role_new)
        split.addWidget(left)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.role_detail = QTextBrowser()
        self.role_detail.setStyleSheet(
            f"QTextBrowser{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:10px;padding:10px 12px;font-size:13px;color:{THEME['text']};}}")
        rv.addWidget(self.role_detail, 1)
        rb = QHBoxLayout()
        self.role_edit_btn = QPushButton("✏️ 编辑此角色")
        self.role_edit_btn.clicked.connect(self._edit_current_role)
        rb.addWidget(self.role_edit_btn)
        rb.addStretch(1)
        rv.addLayout(rb)
        split.addWidget(right)
        split.setSizes([260, 640])
        lay.addWidget(split)
        return page

    def _current_role_library(self):
        """角色库数据源：默认角色库（已叠加角色成长库 override）。"""
        try:
            return legion.default_role_library()
        except Exception:
            return []

    def _refresh_roles_page(self, keep_sel=True):
        cur = (self.roles_list.currentItem().data(Qt.UserRole)
               if keep_sel and hasattr(self, "roles_list")
               and self.roles_list.currentItem() else None)
        self.roles_list.blockSignals(True)
        self.roles_list.clear()
        lib = self._current_role_library()
        for r in lib:
            tools = r.get("tools") or []
            sk = r.get("skills") or []
            tag = f"（{len(tools)} 工具 · {len(sk)} 技能）" if (tools or sk) else ""
            it = QListWidgetItem(f"{r.get('emoji') or '▫️'} {r.get('name', '?')}  {tag}")
            it.setData(Qt.UserRole, r.get("name"))
            self.roles_list.addItem(it)
        self.roles_list.blockSignals(False)
        _hit = None
        if cur:
            for i in range(self.roles_list.count()):
                if self.roles_list.item(i).data(Qt.UserRole) == cur:
                    _hit = i
                    break
        self.roles_list.setCurrentRow(_hit if _hit is not None else 0)

    def _on_role_selected(self, cur, _prev=None):
        if cur is None:
            return
        name = cur.data(Qt.UserRole)
        role = next((r for r in self._current_role_library()
                     if r.get("name") == name), None)
        if not role:
            return
        tools = "、".join(role.get("tools") or []) or "（无）"
        skills = "、".join(role.get("skills") or []) or "（无）"
        body = (role.get("constraints") or "").replace("&", "&amp;") \
                                               .replace("<", "&lt;")
        body = body.replace("\n", "<br>")
        self.role_detail.setHtml(
            f"<h2 style='margin:4px 0'>{role.get('emoji') or ''} "
            f"{role.get('name', '')} <span style='font-size:13px;color:#888'>"
            f"{role.get('category') or ''}</span></h2>"
            f"<p><b>使命：</b>{role.get('mission', '')}</p>"
            f"<p><b>🧰 工具集：</b>{tools}<br>"
            f"<b>🧩 技能：</b>{skills}</p>"
            f"<p><b>📦 交付形态：</b>{role.get('output_format', '')}<br>"
            f"<b>⭐ 质量线：</b>{role.get('quality', '')}<br>"
            f"<b>🔍 自检：</b>{role.get('self_check', '')}</p>"
            f"<hr><div style='white-space:pre-wrap'>{body}</div>")

    def _edit_current_role(self):
        """编辑当前选中的角色卡（复用 RoleEditor，改完写回角色成长库并刷新）。"""
        cur = self.roles_list.currentItem()
        if not cur:
            return
        name = cur.data(Qt.UserRole)
        role = next((r for r in self._current_role_library()
                     if r.get("name") == name), None)
        if not role:
            return
        dlg = RoleEditor(role=role, parent=self)
        if dlg.exec() != RoleEditor.Accepted:
            return
        new = dlg.get_role()
        # 写进角色成长库（role_library_override.json）—— 与「装技能挂角色」同一落点，
        # 下一次 default_role_library() 就带上改动；本会话立即刷新。
        try:
            ov = legion._cm_json_load(legion.ROLE_OVERRIDE_PATH, {})
            if not isinstance(ov, dict):
                ov = {}
            ov[name] = dict(ov.get(name) or {})
            for k in ("emoji", "mission", "constraints", "tools", "skills",
                      "output_format", "quality", "self_check"):
                if k in new and new[k] is not None:
                    ov[name][k] = new[k]
            legion._cm_json_save(legion.ROLE_OVERRIDE_PATH, ov)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"角色改动未能写入成长库：{e}")
        self._refresh_roles_page(keep_sel=True)

    def _add_standalone_role(self):
        """从角色库直接新建角色（进成长库；入队仍由团队页/PM 拉人完成）。"""
        dlg = RoleEditor(role=legion.new_role(name="新角色"), parent=self)
        if dlg.exec() != RoleEditor.Accepted:
            return
        new = dlg.get_role()
        try:
            ov = legion._cm_json_load(legion.ROLE_OVERRIDE_PATH, {})
            if not isinstance(ov, dict):
                ov = {}
            ov[new.get("name") or "新角色"] = {
                k: new[k] for k in ("emoji", "mission", "constraints", "tools",
                                    "skills", "output_format", "quality",
                                    "self_check") if k in new
            }
            legion._cm_json_save(legion.ROLE_OVERRIDE_PATH, ov)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"新角色未能写入成长库：{e}")
        self._refresh_roles_page(keep_sel=False)

    # ---- v4.148.1：团队库页 ----
    def _build_teams_page(self):
        """团队库：项目即成品配方（角色×波次×口径已冻结），克隆即用。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel("团队库（每个团队 = 角色 × 波次 × 口径的配方；"
                             "点「克隆」改个主题就能开跑）"))
        top.addStretch(1)
        b_new_team = QPushButton("+ 新建团队")
        b_new_team.clicked.connect(self._add_project)
        top.addWidget(b_new_team)
        b_pm_team = QPushButton("🧙 PM 出方案（我审批）")
        b_pm_team.clicked.connect(self._auto_team)
        top.addWidget(b_pm_team)
        # v4.148.2（跑通即存配方）：把当前跑通的项目冻结成命名配方进团队库
        b_save_recipe = QPushButton("💾 存为配方（当前项目）")
        b_save_recipe.setToolTip(
            "把当前选中项目跑通后的 阵容+波次+纪律+数据口径 冻结成配方：\n"
            "可命名、可配填空变量（inputs）和任务模板，之后从团队库一键克隆启动。")
        b_save_recipe.clicked.connect(self._save_as_recipe)
        top.addWidget(b_save_recipe)
        lay.addLayout(top)
        self.teams_list = QListWidget()
        self.teams_list.setSpacing(4)
        self.teams_list.itemDoubleClicked.connect(self._on_team_launch)
        lay.addWidget(self.teams_list, 1)
        brow = QHBoxLayout()
        b_launch = QPushButton("▶ 启动此团队")
        b_launch.clicked.connect(self._on_team_launch)
        b_clone = QPushButton("⧉ 克隆")
        b_clone.clicked.connect(self._on_team_clone)
        b_edit = QPushButton("✏️ 编辑成员/波次")
        b_edit.clicked.connect(self._on_team_edit)
        b_del = QPushButton("🗑 删除")
        b_del.clicked.connect(self._on_team_delete)
        for b in (b_launch, b_clone, b_edit, b_del):
            b.setCursor(Qt.PointingHandCursor)
            brow.addWidget(b)
        brow.addStretch(1)
        lay.addLayout(brow)
        return page

    def _refresh_teams_page(self):
        """团队列表：emoji + 名称 + 成员头像串 + 波次流程线 + 续跑标记。"""
        cur = (self.teams_list.currentItem().data(Qt.UserRole)
               if hasattr(self, "teams_list") and self.teams_list.currentItem()
               else None)
        self.teams_list.blockSignals(True)
        self.teams_list.clear()
        for p in self.data.get("projects", []):
            # v4.148.2（⑤a）：流程线 —— 「🌐研究员+⚔️竞品分析师 → 📊分析师 → ✍️写手」
            flow = []
            for w in (p.get("waves") or []):
                names = [f"{m.get('emoji') or ''}{m.get('name') or '?'}"
                         for m in (w.get("members") or []) if m.get("name")]
                if names:
                    flow.append("+".join(names))
            flow_line = " → ".join(flow) or "（空团队）"
            ck = legion.has_checkpoint(p.get("id", ""))
            tag = ""
            if ck and ck.get("corrupt"):
                tag = "  ⚠️ 存档损坏"
            elif ck:
                tag = f"  🟡 可续跑(第{ck.get('last_completed_wave', 0) + 2}波起)"
            recipe_tag = "  📦配方" if (p.get("inputs") and p.get("task_template")) else ""
            line2 = f"　　{(p.get('description') or '').strip()[:48]}"
            it = QListWidgetItem(
                f"{p.get('emoji') or '🧩'} {p.get('name', '未命名')}{tag}{recipe_tag}\n"
                f"　　🔗 {flow_line}\n"
                f"{line2}")
            it.setData(Qt.UserRole, p.get("id"))
            self.teams_list.addItem(it)
        self.teams_list.blockSignals(False)
        if cur:
            for i in range(self.teams_list.count()):
                if self.teams_list.item(i).data(Qt.UserRole) == cur:
                    self.teams_list.setCurrentRow(i)
                    break
            else:
                self.teams_list.setCurrentRow(0)
        else:
            self.teams_list.setCurrentRow(0)

    def _save_as_recipe(self):
        """v4.148.2（跑通即存配方）：把当前项目冻结成命名配方。

        深拷贝阵容/波次/闸门配置 + 继承 briefing（组织记忆），剥掉运行期状态
        （锁定标的/否决黑名单/补录挂载/存档），配 inputs + task_template 后
        作为一个新项目落盘 —— 团队库里从此多一个可克隆的成品。
        """
        p = self._cur_project()
        if not p:
            QMessageBox.information(self, "未选择项目",
                                    "先在左侧或团队库选中要固化的项目。")
            return
        if not legion.wave_members(p):
            QMessageBox.information(self, "团队是空的", "这个项目还没有成员，没有可固化的阵容。")
            return
        dlg = ProjectEditor(p, self)
        dlg.setWindowTitle("存为配方")
        if dlg.exec() != QDialog.Accepted:
            return
        d = dlg.get_data()
        if not d["name"]:
            return
        np = copy.deepcopy(p)
        np["id"] = str(__import__("uuid").uuid4())
        np["name"] = d["name"]
        np["emoji"] = d["emoji"] or p.get("emoji", "")
        np["description"] = d["description"]
        np["category"] = d["category"]
        # 配方字段来自编辑器（含 inputs / task_template）
        np["inputs"] = d.get("inputs") or []
        np["task_template"] = d.get("task_template") or ""
        # 剥运行期状态：配方是干净的可复用成品，不背旧任务的包袱
        for k in ("locked_target", "dead_directions", "manual_files"):
            np.pop(k, None)
        self.data.setdefault("projects", []).append(np)
        legion.save_legion(self.data)
        self._refresh_projects(select_id=np["id"])
        self._refresh_teams_page()
        QMessageBox.information(
            self, "已存为配方",
            f"「{np['name']}」已进团队库（📦配方）。\n"
            + ("配了填空变量 —— 团队库点「▶ 启动此团队」即可填空开跑。"
               if np["inputs"] else
               "未配填空变量 —— 启动时手动输任务即可。"))

    def _teams_cur_project(self):
        it = self.teams_list.currentItem() if hasattr(self, "teams_list") else None
        if not it:
            return None
        return legion.find_project(self.data, it.data(Qt.UserRole))

    def _on_team_launch(self, *_a):
        """从团队库启动：选中该项目 → 切到聊天页 → 聚焦聊天输入框。

        v4.148.2：项目配了 inputs/task_template（配方）→ 先弹填空表单，
        渲染出任务后直接开跑；普通项目保持原行为（切聊天页聚焦）。
        """
        p = self._teams_cur_project()
        if not p:
            return
        self._on_select_project_by_id(p.get("id"))
        self._switch_page(0)
        # v4.148.2：配方填空启动
        if (p.get("inputs") or []) and (p.get("task_template") or "").strip():
            dlg = RecipeLaunchDialog(p, self)
            if dlg.exec() != QDialog.Accepted:
                return
            task = legion.render_task_template(p, dlg.values())
            if self.worker is not None and self.worker.isRunning():
                self.chat_panel.say("系统", "军团正在跑 —— 等它跑完，或先「⛔ 停止」。")
                return
            self.task_edit.setText(task)
            self._run_legion()
            self.chat_panel.say("系统", f"🚀 配方「{p.get('name')}」已按填空启动。"
                                        "浏览器弹出后记得点 VPN 扩展「连接」。")
            return
        if hasattr(self.chat_panel, "input"):
            self.chat_panel.input.setFocus()

    def _on_team_clone(self):
        """克隆当前选中的团队（复用 _dup_project，完成后刷新团队列表并选中新团）。"""
        p = self._teams_cur_project()
        if not p:
            return
        self.cur_project_id = p.get("id")
        self._dup_project()
        self._refresh_teams_page()

    def _on_team_edit(self):
        p = self._teams_cur_project()
        if not p:
            return
        self._on_select_project_by_id(p.get("id"))
        self._switch_page(3)      # 编辑成员/波次 → 编排（高级）页
        self._gear_btn.setChecked(True)
        self._edit_project()

    def _on_team_delete(self):
        p = self._teams_cur_project()
        if not p:
            return
        self._on_select_project_by_id(p.get("id"))
        self._del_project()
        self._refresh_teams_page()

    def _on_select_project_by_id(self, pid):
        for i in range(self.proj_list.count()):
            if self.proj_list.item(i).data(Qt.UserRole) == pid:
                self.proj_list.setCurrentRow(i)
                break

    # ---- 对话页回调（由 LegionChatPanel 注入）----
    def _chat_launch(self, task_text):
        """v4.148.1：聊天框「启动军团 <任务>」→ 用当前选中团队开跑。"""
        p = self._cur_project()
        if not p:
            self.chat_panel.say("系统", "还没有选中团队 —— 先去「🧩 团队库」选一个"
                                        "（或克隆/新建一个），再回来启动。")
            return
        if not legion.wave_members(p):
            self.chat_panel.say("系统", f"团队「{p.get('name')}」还没有成员 —— "
                                        "去「🧩 团队库 → 编辑成员/波次」加人后再启动。")
            return
        if self.worker is not None and self.worker.isRunning():
            self.chat_panel.say("系统", "军团正在跑 —— 等它跑完，或先「⛔ 停止」。")
            return
        self.task_edit.setText(task_text)
        self._run_legion()
        self.chat_panel.say("系统", f"🚀 已用团队「{p.get('name')}」启动军团，"
                                    "任务已下达。浏览器弹出后记得点 VPN 扩展「连接」。")

    def _chat_send_pm(self, text, urgent=False):
        """对话页输入框 → PM 留言（复用既有 _on_send_pm 的投送逻辑）。"""
        if self.worker is None or not self.worker.isRunning():
            self.log_view.append(f"\n💬 你 → 项目经理：{text}\n（军团未运行，消息未发送）\n")
            return
        if self.worker.send_message(text, urgent=urgent):
            kind = "急件" if urgent else "备忘"
            self.log_view.append(f"\n💬 你 → 项目经理（{kind}）：{text}\n")

    def _chat_resolve_auth(self, intent, text):
        """对话页一句话决策 → 送回 worker（intent∈pass/reject/abort）。"""
        if self.worker is None or not self.worker.isRunning():
            self.log_view.append("\n⚠️ 授权上下文已失效（军团未运行），忽略。\n")
            if hasattr(self, "chat_panel"):
                self.chat_panel.auth_resolved()
            return
        try:
            if intent == "reject" and text:
                self.worker.set_auth_revision(text)
                self.log_view.append(f"\n✍️ 你的改稿要求：{text}\n")
            self.worker.set_auth_result(intent)
            _zh = {"pass": "✅ 放行下一波", "reject": "↩︎ 打回重做",
                   "abort": "⛔ 终止军团"}.get(intent, intent)
            self.log_view.append(f"\n{_zh}\n")
            if hasattr(self, "chat_panel"):
                self.chat_panel.say("你", f"授权决定：{_zh}")
        except Exception as e:
            self.log_view.append(f"\n⚠️ 授权结果发送失败：{e}\n")

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
            if ck and ck.get("corrupt"):
                # v4.125 M-03：存档损坏——明示，不再静默当作"没有"
                tag = "  ⚠️ 存档损坏"
            elif ck:
                # v4.124.6：有 checkpoint → 项目后加 🟡 + 提示文字
                # v4.124.8 修正：续跑起点 = last_completed_wave + 2（+1 会显示成
                # 「上次完成的波」而非「下次要跑的波」，与日志提示差 1 让人困惑）。
                tag = f"  🟡 续(第{ck.get('last_completed_wave', 0)+2}波)"
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
        # v4.135.0：换项目同步切对话页历史（legion_chat/<pid>.json）
        if hasattr(self, "chat_panel"):
            try:
                self.chat_panel.reload_for_project(self.cur_project_id)
            except Exception:
                pass

    def _refresh_head(self):
        p = self._cur_project()
        # v4.124.6：续跑按钮的可见性（依赖 cur_project_id 是否有 ckpt）
        _ck_raw = legion.has_checkpoint(p.get("id", "")) if p else None
        has_ck = bool(_ck_raw) and not _ck_raw.get("corrupt")
        if hasattr(self, "resume_btn"):
            self.resume_btn.setVisible(bool(has_ck))
            if has_ck:
                ck = _ck_raw
                # v4.124.8 修正：续跑起点 = last_completed_wave + 2（与日志「从第 N 波启动」对齐）
                self.resume_btn.setText(
                    f"⏵ 续跑(第{ck.get('last_completed_wave', 0)+2}波)")
                self.resume_btn.setToolTip(
                    f"上次中断于：{ck.get('saved_at_human', '')}\n"
                    f"已完成 {ck.get('last_completed_wave', 0)+1} 波\n"
                    f"续跑从第 {ck.get('last_completed_wave', 0)+2} 波启动\n"
                    "点击会弹窗让你选：续/重/取")
        if not p:
            self.head.setText("尚未选择项目")
            self.sub.setText("点「+ 添加团队」新建一个")
            return
        ck_tag = ""
        if has_ck:
            ck = _ck_raw
            ck_tag = (f"  ·  💾 上次中断：第 {ck.get('last_completed_wave', 0)+1} 波完成 "
                      f"({ck.get('saved_at_human', '')})")
        elif _ck_raw and _ck_raw.get("corrupt"):
            # v4.125 M-03：损坏存档要在界面明示
            ck_tag = "  ·  ⚠️ 存档损坏，无法续跑（重新开跑将从第 1 波开始）"
        # v4.124.14：记忆层状态直接显示在标题栏 —— 「看不见的记忆＝失控的记忆」
        mem_bits = []
        _raw = p.get("locked_target")
        _lt = (_raw.get("text", "") if isinstance(_raw, dict)
               else str(_raw or "")).strip()
        if _lt:
            mem_bits.append(f"🔒{_lt[:18]}")
        _nd = len(p.get("dead_directions") or [])
        if _nd:
            mem_bits.append(f"🚫{_nd}")
        _nm = len(p.get("manual_files") or [])
        if _nm:
            mem_bits.append(f"📎{_nm}")
        mem_tag = ("  ·  " + " ".join(mem_bits)) if mem_bits else ""
        self.head.setText(f"{p.get('emoji', '')} {p.get('name', '')}".strip())
        self.sub.setText(f"{p.get('category', '')} · {legion.project_summary(p)}"
                         f"{(' · ' + p['description']) if p.get('description') else ''}"
                         f"{ck_tag}{mem_tag}")

    # ---- v4.124.14：记忆层管理（补录数据归属 / 清空记忆）----
    def _manage_manual(self):
        """手工补录文件归属管理：挂载到本项目 / 归档（移出自动扫描）。"""
        p = self._cur_project()
        if not p:
            QMessageBox.information(self, "补录数据", "先选一个项目。")
            return
        files = legion.list_manual_files(include_archived=True)
        if not files:
            QMessageBox.information(
                self, "补录数据",
                f"还没发现手工补录文件。\n\n"
                f"把 JSON 放进：{legion.MANUAL_DIR}\n"
                f"文件名以 manual_ 开头（例：manual_competitor_research_xxx.json）")
            return
        attached = set(p.get("manual_files") or [])

        dlg = QDialog(self)
        dlg.setWindowTitle("📎 手工补录数据 · 归属管理")
        dlg.resize(620, 380)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(
            "只有**挂载到本项目**的补录文件才会注入 PM 提示词。\n"
            "未挂载的文件不会被任何任务自动读取 —— 上一份跨境电商的补录不会再把新任务带跑偏。"))
        lst = QListWidget()
        lst.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for name in files:
            mark = "✅ 已挂载" if name in attached else "　未挂载"
            archived = name.startswith("archive/")
            if archived:
                mark = "🗄 已归档"
            lst.addItem(f"{mark} · {name}")
        v.addWidget(lst, 1)

        def _names_from_selected():
            out = []
            for it in lst.selectedItems():
                out.append(it.text().split(" · ", 1)[-1].strip())
            return out

        def _toggle():
            names = _names_from_selected()
            if not names:
                return
            for n in names:
                if n.startswith("archive/"):
                    continue
                legion.attach_manual_file(p.get("id", ""), n, on=(n not in attached))
            self._refresh_head()
            self._manage_manual_refresh = True
            dlg.accept()
            self._manage_manual()

        def _archive():
            names = _names_from_selected()
            if not names:
                return
            if QMessageBox.question(
                    self, "归档补录文件",
                    f"把 {len(names)} 份文件移入 manual_archive/ ？\n"
                    "归档后不再被任何任务自动扫描（文件不删除，可手动放回）。",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            for n in names:
                legion.archive_manual_file(n)
                legion.attach_manual_file(p.get("id", ""), n, on=False)
            self._refresh_head()
            dlg.accept()
            self._manage_manual()

        row = QHBoxLayout()
        b_tog = QPushButton("挂载 / 取消挂载")
        b_tog.clicked.connect(_toggle)
        b_arc = QPushButton("🗄 归档（移出扫描）")
        b_arc.clicked.connect(_archive)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(dlg.accept)
        row.addWidget(b_tog)
        row.addWidget(b_arc)
        row.addStretch(1)
        row.addWidget(b_close)
        v.addLayout(row)
        dlg.exec()

    def _wipe_memory(self):
        """清空本项目的标的记忆 / 否决名单 / 教训本。"""
        p = self._cur_project()
        if not p:
            QMessageBox.information(self, "清空记忆", "先选一个项目。")
            return
        raw = p.get("locked_target")
        lt = (raw.get("text", "") if isinstance(raw, dict) else str(raw or "")).strip()
        n_dead = len(p.get("dead_directions") or [])
        box = QMessageBox(self)
        box.setWindowTitle("🧹 清空本项目的记忆")
        box.setText("要清掉哪一项？（换任务跑偏时，先清「锁定标的」）")
        box.setInformativeText(
            f"当前：🔒 锁定标的＝{lt or '（无）'}\n"
            f"　　　🚫 否决黑名单＝{n_dead} 项\n"
            f"　　　📓 项目教训本＝随项目\n\n"
            "否决黑名单一般不用清（打死的方向到哪都别提）。")
        b_t = box.addButton("清 锁定标的", QMessageBox.AcceptRole)
        b_l = box.addButton("清 教训本", QMessageBox.AcceptRole)
        b_d = box.addButton("清 否决名单", QMessageBox.DestructiveRole)
        b_a = box.addButton("全部清空", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        what = None
        if clicked is b_t:
            what = "target"
        elif clicked is b_l:
            what = "lessons"
        elif clicked is b_d:
            what = "dead"
        elif clicked is b_a:
            what = "all"
        if not what:
            return
        did = legion.clear_target_memory(p.get("id", ""), what)
        self._refresh_projects()
        self._refresh_head()
        QMessageBox.information(self, "已清空", did or "没有可清的内容")

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
        """独立入口：**任何时候**都能补技能，装完直接挂给在编角色（v4.134.2）。

        此前这个入口只装不挂 —— 装完 slug 就丢了。而「挂给角色」的逻辑只写在
        组队弹窗里，开工之后/波间/打回重跑根本走不到那个弹窗，于是「能装但没处挂」，
        等于没补（这正是断口）。现在装完弹角色下拉，挂上立即生效。
        """
        dlg = SkillInstallDialog(mw=self.mw, parent=self)
        dlg.exec()
        slug = getattr(dlg, "installed_slug", None)
        if not slug:
            return
        p = self._cur_project()
        if not p:
            QMessageBox.information(
                self, "已装进技能库",
                "「%s」已装好。\n\n当前没选项目 —— 选一个项目（或先组队）后，"
                "再挂给需要的角色。" % slug)
            return
        roster = legion.project_members(p)
        if not roster:
            QMessageBox.information(
                self, "已装进技能库",
                "「%s」已装好。\n\n项目「%s」还没有在编成员 —— 先组队，"
                "组队时可以直接勾上这个技能。" % (slug, p.get("name", "")))
            return
        labels = ["%s%s（第 %d 波）%s"
                  % (r.get("emoji", ""), r["name"], r["wave"],
                     "｜已挂" if slug in (r.get("skills") or []) else "")
                  for r in roster]
        pick, ok = QInputDialog.getItem(
            self, "挂给谁",
            "把「%s」挂给谁？\n挂上后**下一波 / 打回重跑立即生效**（不用重新组队）。" % slug,
            labels, 0, False)
        if not ok or not pick:
            return
        role_name = roster[labels.index(pick)]["name"]
        _waves, msg = legion.attach_skill_to_project(p, slug, role_name)
        if _waves:
            legion.save_legion(self.data)
            self._rebuild_waves()
        self.log_view.append(msg)
        QMessageBox.information(self, "挂载结果", msg)

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
        pid = p.get("id", "")
        # v4.137 病根修复：过去这里只删数据、不停 worker，于是造出「僵尸任务」——
        # 它继续烧 API、继续写产出；而 _run_legion 用 isRunning() 守卫，
        # 把整个军团的启动入口锁死；终止按钮又只存在于 human 模式的授权弹窗里，
        # 波内执行时没有任何出口 —— 三者叠加＝删了团队却停不下来、新团队也跑不了。
        if self._worker_running(pid):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("该项目正在执行")
            box.setText(
                f"「{p.get('name', '')}」的军团正在执行中。\n\n"
                "只删配置不会让它停下 —— 它会在后台继续烧 API、继续写产出，\n"
                "而且会挡住其它项目启动。\n\n"
                "你要：")
            b_kill = box.addButton("⛔ 立即停止并删除（推荐）", QMessageBox.AcceptRole)
            b_keep = box.addButton("照删，让它在后台跑完", QMessageBox.DestructiveRole)
            b_cancel = box.addButton("取消", QMessageBox.RejectRole)
            box.setDefaultButton(b_cancel)   # 破坏性操作，默认停在「取消」
            box.exec()
            clicked = box.clickedButton()
            if clicked is b_cancel or clicked is None:
                return
            if clicked is b_kill:
                self._detach_worker()
                # 信号已断开，不会再有 finished 回调来复位按钮，这里手动复位
                self._set_running_ui(False)
                self.log_view.append(
                    "\n⛔ 已向正在执行的军团发出停止指令（跑完当前这一步就收尾）。\n")
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
        mode_combo = _NoWheelCombo()
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
        # v4.149.0：currentIndexChanged → activated —— 授权模式只认「用户真选」，
        # 键盘上下/程序化改动都不再落盘（原写法会把「滚过/划过」当成「改配置」）。
        mode_combo.activated.connect(
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
    # v4.137：worker 的生命周期以前散在三处各管一半 —— 启动处只管置位、
    # _on_finished 只管复位、删除项目处压根不管。于是「删了团队还在跑、
    # 而且停不下来、新团队也跑不了」。下面四个小工具把它收拢成一处。

    def _set_running_ui(self, running: bool):
        """统一维护执行态的三个控件：启动按钮 / 停止按钮 / 联系项目经理。

        以前只有 _on_finished 会复位；一旦那条路没走到（worker 被 detach，
        或项目被删导致信号断开），按钮就永远卡在「执行中…」，谁也点不动。
        """
        self.run_btn.setEnabled(not running)
        self.run_btn.setText("执行中…" if running else "▶ 启动军团")
        if hasattr(self, "stop_btn"):
            self.stop_btn.setVisible(running)
            self.stop_btn.setEnabled(True)
            self.stop_btn.setText("⛔ 停止")
        # 对话页那颗同步显隐（两页共用一个 worker，停的入口也该有两处）
        _cp = getattr(self, "chat_panel", None)
        if _cp is not None and hasattr(_cp, "stop_btn"):
            _cp.stop_btn.setVisible(running)
            _cp.stop_btn.setEnabled(True)
        if hasattr(self, "pm_box"):
            self.pm_box.setEnabled(running)

    def _worker_running(self, pid="") -> bool:
        """当前有个在跑的 worker（传 pid 则进一步限定是它所属的项目）。"""
        if self.worker is None or not self.worker.isRunning():
            return False
        if pid and getattr(self, "_worker_pid", "") != pid:
            return False
        return True

    def _detach_worker(self):
        """切断与当前 worker 的一切联系：断信号 → 请求停止 → 丢弃引用。

        关键在「断信号」：worker 是合作式停止，调 abort 后线程不会立刻退出，
        它还会跑一小段；若不断开连接，它的 log/done/finished 会串进新任务的
        执行过程 —— 表现为新任务日志里混着旧日志，或者新任务还没跑完，
        按钮就被旧 worker 的 finished 提前解锁。
        """
        w = getattr(self, "worker", None)
        if w is None:
            return
        for _sig in ("log_line", "done", "finished", "auth_request", "replan_request"):
            _s = getattr(w, _sig, None)
            if _s is None:
                continue
            try:
                _s.disconnect()
            except Exception:
                pass
        try:
            w.abort()
        except Exception:
            pass
        self.worker = None
        self._worker_pid = ""
        # 审计修复 B1：abort 是合作式的，线程还会跑数十秒（当前 LLM 调用）。
        # 丢引用后 PySide6 会在 QThread 仍运行时析构 → qFatal 整进程崩溃。
        # 挂进 graveyard 持有到真正 finished 再释放（信号已断开，不会串进新任务）。
        pool = getattr(self, "_dead_workers", None)
        if pool is None:
            pool = self._dead_workers = []
        pool.append(w)
        try:
            w.finished.connect(lambda _w=w: pool.remove(_w) if _w in pool else None)
        except (RuntimeError, TypeError):
            pass

    # ---- v4.139 P2：对话里的技能指令（查缺口 / 去 GitHub 找 / 装）----
    def _skill_say(self, text):
        """把技能相关回复写进对话流（复用对话页的记录口）。"""
        cp = getattr(self, "chat_panel", None)
        if cp is None:
            return
        try:
            cp._record("系统", text)
        except Exception:
            pass

    def _skill_cmd(self, kind, arg):
        """对话页技能指令统一入口（由 LegionChatPanel.set_skill_helper 注入）。

        kind：list=查当前缺口；search=去 GitHub 找；install=装（序号 / owner/repo / 关键词）。
        「缺口 → 找 → 装」全程在对话里说完，不必等跑完弹窗、也不必点按钮。
        """
        if kind == "list":
            meta = getattr(getattr(self, "worker", None), "last_meta", None) or {}
            gaps = meta.get("skill_gaps") or []
            res = meta.get("skill_candidates") or {}
            if not gaps:
                self._skill_say("当前没检测到技能缺口（PM 没报、成员产出里也没扫到信号）。"
                                "若你知道缺什么，说「去GitHub找 <关键词>」。")
                return
            self._skill_say(legion.skill_candidates_report_text(gaps, res))
            return
        if kind == "search":
            q = (arg or "").strip()
            if not q:
                self._skill_say("要搜什么？说「去GitHub找 <关键词>」。")
                return
            self._skill_say("⏳ 正在去 GitHub 找「%s」…" % q)
            w = GHWorker("search", self, query=q)
            w.done.connect(lambda pl, err, _q=q: self._on_skill_search(pl, err, _q))
            w.start()
            return
        if kind == "install":
            a = (arg or "").strip()
            if a.isdigit():
                idx = int(a) - 1
                cands = getattr(self, "_last_skill_cands", None) or []
                if 0 <= idx < len(cands):
                    self._skill_install(cands[idx])
                else:
                    self._skill_say("没有第 %s 个候选 —— 先说「去GitHub找 <关键词>」，"
                                    "我再按序号装。" % a)
                return
            if "/" in a and " " not in a:
                self._skill_install({"full_name": a})
                return
            self._skill_say("「%s」不是序号也不是 owner/repo —— 我先搜，"
                            "你再按序号说「装第N个」。" % a)
            self._skill_cmd("search", a)
            return

    def _on_skill_search(self, payload, err, q):
        repos = payload or []
        self._last_skill_cands = repos
        if err:
            self._skill_say("❌ GitHub 搜索失败：%s" % err)
            return
        if not repos:
            self._skill_say("GitHub 没搜到「%s」相关仓库 —— 换个关键词再试。" % q)
            return
        lines = ["🔎 「%s」候选（搜到 %d 个）：" % (q, len(repos))]
        for i, r in enumerate(repos[:5]):
            lines.append("  %d) %s ★%s ｜ %s" % (
                i + 1, r.get("full_name", "?"), r.get("stars", 0),
                str(r.get("description") or "")[:60]))
        lines.append("说「装第N个」我就装（装前自动安全审计）。")
        self._skill_say("\n".join(lines))

    def _skill_install(self, repo):
        full = (repo or {}).get("full_name") or ""
        if not full:
            self._skill_say("这个候选没有仓库名，装不了。")
            return
        self._skill_say("⏳ 正在列 %s 里的 SKILL.md…" % full)
        w = GHWorker("list", self, repo=full, branch=(repo or {}).get("branch"))
        w.done.connect(lambda pl, err, _r=repo: self._on_skill_list(pl, err, _r))
        w.start()

    def _on_skill_list(self, files, err, repo):
        if err:
            self._skill_say("❌ 列文件失败：%s" % err)
            return
        files = files or []
        if not files:
            self._skill_say("这个仓库里没找到 SKILL.md。")
            return
        f = files[0]
        if len(files) > 1:
            self._skill_say("仓库里有 %d 个 SKILL.md，先装第一个「%s」"
                            "（想挑别的去主窗口「🔧 装技能」）。"
                            % (len(files), f.get("slug")))
        self._skill_say("⏳ 正在安装 %s（含内容级安全审计）…" % (f.get("slug") or ""))
        w = GHWorker("install", self, repo=(repo or {}).get("full_name"),
                     path=f.get("path"), branch=(repo or {}).get("branch"),
                     slug=f.get("slug"))
        w.done.connect(
            lambda pl, err, _s=f.get("slug"): self._on_skill_installed(pl, err, _s))
        w.start()

    def _on_skill_installed(self, payload, err, slug):
        if err:
            self._skill_say("❌ 安装失败：%s" % err)
            return
        p = payload or {}
        if p.get("ok"):
            self._skill_say("✅ 已装好技能「%s」——%s\n若要让某个角色用上，"
                            "在「编排」页把这个技能挂给它即可。"
                            % (slug, p.get("msg") or ""))
        else:
            self._skill_say("❌ 没装成：%s" % (p.get("msg") or "未知原因"))

    def _on_stop_clicked(self):
        """⛔ 停止：向 worker 发出合作式停止指令（保留已产出、可续跑）。

        v4.137 新增：此前「终止军团」只存在于 human 模式的授权弹窗里，
        波内执行阶段（最耗时、也正是最想停的时候）界面上没有任何出口。
        """
        if not self._worker_running():
            self._set_running_ui(False)
            return
        r = QMessageBox.question(
            self, "停止军团",
            "向军团发出停止指令：\n"
            "  • 它会跑完正在进行的这一步再收尾，不拦腰砍断成员调用\n"
            "  • 已产出的东西都保留，可以续跑\n\n"
            "确定停止？")
        if r != QMessageBox.Yes:
            return
        self.worker.abort()
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("停止中…")
        _cp = getattr(self, "chat_panel", None)
        if _cp is not None and hasattr(_cp, "stop_btn"):
            _cp.stop_btn.setEnabled(False)
        self.log_view.append(
            "\n⛔ 停止指令已发出 —— 当前这一步跑完就收尾，产出不会丢。\n")

    def _ask_kill_zombie(self) -> bool:
        """僵尸任务：worker 还在跑，但它所属的项目已经被删了。

        过去这里只会弹「等它跑完」——可项目都没了，它跑完也只是堆垃圾，
        产出还没地方挂；同时它占着执行名额，别的项目一律启动不了。
        返回 True 表示已清理干净、可以继续启动新的。
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("上一个军团已无处安放")
        box.setText(
            "检测到还在执行的军团，它所属的项目已经被删除了（僵尸任务）。\n\n"
            "它会继续烧 API、继续写产出，产出也没地方挂；\n"
            "同时它还占着执行名额，别的项目一律启动不了。\n\n"
            "你要：")
        b_kill = box.addButton("⛔ 终止它，然后启动这次的", QMessageBox.AcceptRole)
        b_wait = box.addButton("算了，等它自己跑完", QMessageBox.RejectRole)
        box.setDefaultButton(b_kill)
        box.exec()
        if box.clickedButton() is not b_kill:
            return False
        self._detach_worker()
        self._set_running_ui(False)
        self.log_view.append(
            "\n🧹 已终止僵尸任务（项目被删导致的残留执行），现在可以重新开始了。\n")
        return True

    # ---- v4.146：任务模板（反复任务一键加载）----
    def _open_task_templates(self):
        """任务模板管理：保存当前任务为模板 / 套用 / 删除。

        模板存 ~/Documents/小臭玩AI/task_templates.json（legion.save_task_template /
        load_task_template / save_all_task_templates）。套用把 task_hint 填进任务输入框，
        开工前能力审计据此跑；建议角色从当前项目团队抽样记录。
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("任务模板")
        dlg.resize(420, 380)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("已存模板（套用 = 把任务提示填进输入框）："))
        lst = QListWidget()
        tpls = legion.load_task_template() or {}
        for nm in sorted(tpls.keys()):
            lst.addItem(nm)
        lay.addWidget(lst, 1)
        row = QHBoxLayout()
        b_apply = QPushButton("套用")
        b_save = QPushButton("存当前任务为模板")
        b_del = QPushButton("删除")
        row.addWidget(b_apply)
        row.addWidget(b_save)
        row.addWidget(b_del)
        lay.addLayout(row)

        def _project_roles():
            if not self.cur_project_id:
                return []
            for p in (self.data.get("projects") or []):
                if isinstance(p, dict) and p.get("id") == self.cur_project_id:
                    out = []
                    for w in (p.get("waves") or []):
                        for m in (w.get("members") or []):
                            if isinstance(m, dict) and m.get("name"):
                                out.append(m["name"])
                    return out[:12]
            return []

        def _apply():
            it = lst.currentItem()
            if not it:
                QMessageBox.information(dlg, "提示", "先选中一个模板。")
                return
            tpl = tpls.get(it.text()) or {}
            hint = tpl.get("task_hint") or ""
            if hint:
                self.task_edit.setText(hint)
            _more = []
            if tpl.get("must_roles"):
                _more.append("建议角色：" + "、".join(tpl["must_roles"]))
            if tpl.get("must_capabilities"):
                _more.append("建议能力：" + "、".join(tpl["must_capabilities"]))
            if _more:
                self.log_view.append("📋 套用模板「%s」\n  %s"
                                     % (it.text(), "\n  ".join(_more)))
            dlg.accept()

        def _save():
            nm, ok = QInputDialog.getText(dlg, "存为模板", "模板名（如 toutiao_爆文）：")
            nm = (nm or "").strip()
            if not ok or not nm:
                return
            tpl = {
                "task_hint": self.task_edit.text().strip(),
                "must_roles": _project_roles(),
                "must_capabilities": [],
            }
            legion.save_task_template(nm, tpl)
            tpls[nm] = tpl
            lst.addItem(nm)
            self.log_view.append("📋 已存模板「%s」" % nm)

        def _del():
            it = lst.currentItem()
            if not it:
                return
            nm = it.text()
            allt = legion.load_task_template() or {}
            if nm not in allt:
                return
            del allt[nm]
            legion.save_all_task_templates(allt)
            lst.takeItem(lst.row(it))
            self.log_view.append("📋 已删模板「%s」" % nm)

        b_apply.clicked.connect(_apply)
        b_save.clicked.connect(_save)
        b_del.clicked.connect(_del)
        dlg.exec()

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
        # v4.124.8 修复：续跑模式下任务框可能为空（用户中断后没重新填），
        # 从 checkpoint 复用原任务兜底，别让「缺任务」拦住续跑。
        if not task and self._pending_resume == "resume":
            ck = legion.load_checkpoint(p.get("id", ""))
            if ck and (ck.get("task") or "").strip():
                task = ck["task"].strip()
                self.task_edit.setText(task)
        if not task:
            QMessageBox.information(self, "缺任务", "给军团下个任务再启动。")
            return
        if not legion.wave_members(p):
            QMessageBox.information(self, "团队是空的",
                                    "这个项目还没有成员，先给波次里加人。")
            return
        if self.worker is not None and self.worker.isRunning():
            # v4.137：分两种情况。项目还在 → 维持「等它跑完」（现在多了 ⛔ 停止 可选）；
            # 项目已经被删了 → 僵尸任务，必须给「终止它再开跑」的活路，
            # 否则整个军团从此永久卡在「执行中…」，谁也救不回来。
            _wpid = getattr(self, "_worker_pid", "")
            # 认不出归属（_wpid 为空）时按「还在」处理 —— 宁可让人多等一会儿、
            # 或自己点 ⛔ 停止，也不误伤正在跑的正事。
            _still_exists = (not _wpid) or any(
                x.get("id") == _wpid for x in self.data.get("projects", []))
            if _still_exists:
                QMessageBox.information(
                    self, "军团运行中",
                    "已有一个军团在执行，等它跑完再启动。\n"
                    "（不想等：点右边的「⛔ 停止」让它先收尾）")
                return
            if not self._ask_kill_zombie():
                return

        self.log_view.clear()
        self._set_running_ui(True)
        # 传 self.data：让验收用上用户在角色库里定制过的「项目经理」
        # v4.124.6：续跑模式 → 带 resume_from + ckpt_run_id
        kwargs = {"legion_data": self.data}
        if self._pending_resume == "resume":
            ck = legion.has_checkpoint(p["id"])
            if ck and not ck.get("corrupt"):
                kwargs["resume_from"] = ck.get("last_completed_wave", 0) + 1
                kwargs["ckpt_run_id"] = ck.get("run_id", "")
            elif ck:
                # v4.125 M-03：存档损坏——明示后全新开跑，不静默跳波
                self._on_log("⚠️ 检测到损坏的存档：无法续跑，本次从第 1 波全新开始"
                             "（旧产出仍在运行记录与报告中）。\n")
        self.worker = LegionWorker(self.mw, p, task, **kwargs)
        self._worker_pid = p.get("id", "")   # v4.137：认得出跑的是哪个项目
        self.worker.log_line.connect(self._on_log)
        self.worker.log_line.connect(self.chat_panel.on_log)   # v4.135.0 对话页同步
        self.worker.done.connect(self._on_done)
        self.worker.done.connect(self.chat_panel.on_done)       # v4.135.0 成果进对话流
        self.worker.finished.connect(self._on_finished)
        # 宪法第二章：人手授权闸门（PM 只有建议权，批不批在这里由你定）
        self.worker.auth_request.connect(self._on_auth_request)
        # v4.124.8：PM 判定「需重走流程」时的拍板弹窗
        self.worker.replan_request.connect(self._on_replan_request)
        self.worker.replan_request.connect(self.chat_panel.on_replan)  # v4.135.0 对话页同步
        self.worker.start()
        # v4.124.8：启用「联系项目经理」（仅验收开启、有 PM 时才有意义）
        gate_mode = (p.get("gate_mode") or "").strip()
        if not gate_mode:
            gate_mode = "advisory" if p.get("gate_enabled") else "off"
        self.pm_box.setEnabled(gate_mode != "off")
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
        if ck.get("corrupt"):
            # v4.125 M-03：损坏存档——告知而非冒充可续跑
            QMessageBox.warning(
                self, "存档损坏",
                "检测到该项目的存档文件已损坏，无法续跑。\n"
                "旧产出仍在运行记录与军团报告中；重新开跑将从第 1 波全新开始。")
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
            # v4.124.8 修复：续跑自动复用存档里的任务，否则任务框空着、
            # 点「▶ 启动军团」会被 _run_legion 的「缺任务」检查拦住。
            if not self.task_edit.text().strip():
                full = legion.load_checkpoint(p.get("id", ""))
                if full and (full.get("task") or "").strip():
                    self.task_edit.setText(full["task"].strip())
            self._log_line("📌 已选「从上次续跑」——点「▶ 启动军团」即从第 "
                           f"{ck.get('last_completed_wave', 0)+2} 波启动")
        elif clicked is btn_rescratch:
            self._pending_resume = "from_scratch"
            # 用户接下来点 ▶ 启动军团 即按 _run_legion 默认走（不带 resume_from）
            self._log_line("📌 已选「从头重跑」——点「▶ 启动军团」即清存档从头跑")
        else:
            self._pending_resume = None

    def _on_auth_request(self, title, detail):
        """波次授权（宪法第二章）：渲染进对话页 + 自动切到对话页。

        v4.135.0 改动：**不再自动弹模态框**——模态框会挡住对话页输入，
        违背「在对话框里说话做决策」的定调。改为把授权渲染进对话时间线，
        并自动切到对话页；大哥一句话「放行 / 打回 / 终止」即可决策。
        模态按钮弹窗作为兜底（对话页「授权弹窗」按钮触发 _open_auth_dialog）。
        """
        self._auth_title = title
        self._auth_detail = detail
        if hasattr(self, "chat_panel"):
            try:
                self.chat_panel.begin_auth(title, detail)
                self._switch_page(1)
                # v4.145 修复①：把承载军团的顶层页提到前台——用户可能在别的页
                # （导演台/主对话），否则授权被渲染进不可见的对话页，600s 后静默超时、
                # 整轮多波次运行被破坏性中止（人不在必「卡死」）。
                try:
                    mw = getattr(self, "mw", None)
                    if mw is not None and hasattr(mw, "_open_legion"):
                        mw._open_legion()
                except Exception:
                    pass
            except Exception:
                # v4.145 修复①：聊天渲染失败兜底——弹模态授权框，保证一定有可见入口
                # （宪法第二章：授权权必须在大哥手里，但入口不能隐身）。
                try:
                    self._open_auth_dialog()
                except Exception:
                    pass

    def _open_auth_dialog(self):
        """兜底：用按钮打开授权模态弹窗（与对话输入决策二选一）。

        v4.135.0：从 _on_auth_request 抽出的原弹窗逻辑；只在大哥主动点
        「授权弹窗」时打开，不自动挡对话输入。宪法第二章按钮兜底保留。
        """
        title = getattr(self, "_auth_title", "") or "等待你授权"
        detail = getattr(self, "_auth_detail", "") or ""
        dlg = QDialog(self)
        dlg.setWindowTitle("⏸ 等待你授权 · 宪法第二章")
        dlg.setMinimumWidth(660)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"<b>{title}</b>"))
        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText(detail)
        body.setMinimumHeight(300)   # v4.131-C：报告原文较长，加高可滚动
        lay.addWidget(body, 1)

        # v4.131-E：手写的改稿要求（选填）—— 打回时拼在 PM 指令最前面，
        # 优先级最高。过去大哥看到「建议打回」却没地方说「到底要改成啥样」。
        lay.addWidget(QLabel("我的改稿要求（选填）：打回时拼在打回指令最前面，"
                             "优先级高于项目经理的意见"))
        rev_edit = QTextEdit()
        rev_edit.setPlaceholderText(
            "例：数据必须带来源；别写「待核」这种模糊词；结论控制在 3 条以内")
        rev_edit.setFixedHeight(58)
        lay.addWidget(rev_edit)

        # v4.124.13：记忆一笔（选填）
        lay.addWidget(QLabel("记忆一笔（选填）：打回＝打死这个方向（永不再提）；"
                             "放行＝锁定这个标的（后续波只围绕它）"))
        note_edit = QLineEdit()
        note_edit.setPlaceholderText("例：香薰蜡烛 / 逗猫棒 —— 多个方向用顿号分隔")
        lay.addWidget(note_edit)
        cb_remember = QCheckBox("跨项目记住（写进长期教训，下次组队自动提醒相关角色）")
        lay.addWidget(cb_remember)

        btn_row = QHBoxLayout()
        b_pass = QPushButton("✅ 放行下一波")
        b_reject = QPushButton("↩︎ 打回重做")
        b_abort = QPushButton("⛔ 终止军团")
        b_pass.setDefault(True)
        btn_row.addWidget(b_pass)
        btn_row.addWidget(b_reject)
        btn_row.addWidget(b_abort)
        lay.addLayout(btn_row)

        result = {"val": "abort"}

        def _finish(val):
            result["val"] = val
            dlg.accept()

        b_pass.clicked.connect(lambda: _finish("pass"))
        b_reject.clicked.connect(lambda: _finish("reject"))
        b_abort.clicked.connect(lambda: _finish("abort"))
        dlg.exec()

        val = result["val"]
        _rev = rev_edit.toPlainText().strip()
        try:
            if self.worker is not None:
                # v4.131-E：先送改稿要求（worker 端打回时才用），再送记忆与结果
                if _rev and val == "reject":
                    self.worker.set_auth_revision(_rev)
                    self.log_view.append(f"\n✍️ 你的改稿要求：{_rev}\n")
                elif _rev:
                    self.log_view.append(
                        f"\n✍️ 你写了改稿要求但选了放行 —— 只在打回时生效：{_rev}\n")
                # v4.124.13：先送记忆再送结果（worker 端在 set_auth_result 里解阻塞）
                self.worker.set_auth_note(note_edit.text(), cb_remember.isChecked())
                self.worker.set_auth_result(val)
        except Exception:
            pass
        # v4.135.0：同步进对话页（清授权待决态 + 记一句决策）
        if hasattr(self, "chat_panel"):
            try:
                self.chat_panel.auth_resolved()
                _zh = {"pass": "✅ 放行下一波", "reject": "↩︎ 打回重做",
                       "abort": "⛔ 终止军团"}.get(val, val)
                _note = (f"｜改稿：{_rev}" if (_rev and val == "reject") else "")
                self.chat_panel.say("你", f"授权决定（弹窗）：{_zh}{_note}")
            except Exception:
                pass

    def _on_send_pm(self):
        """v4.124.8：把「联系项目经理」输入框里的话投进 PM 收件箱。

        默认备忘（进队列，下一波界交给 PM）；勾「急件」则本波跑完立即响应。
        只入队不打断正在跑的波 —— 原子波不可中途杀成员。
        """
        text = self.pm_msg_edit.text().strip()
        if not text:
            return
        if self.worker is None or not self.worker.isRunning():
            QMessageBox.information(self, "军团未运行",
                                    "军团没在跑，没有项目经理可联系。")
            return
        urgent = self.pm_urgent_cb.isChecked()
        if self.worker.send_message(text, urgent=urgent):
            kind = "急件" if urgent else "备忘"
            self.log_view.append(f"\n💬 你 → 项目经理（{kind}）：{text}\n")
            self.pm_msg_edit.clear()
            self.pm_urgent_cb.setChecked(False)

    def _on_replan_request(self, reply):
        """v4.124.8：PM 判定「这条要求会影响后续波次，需重走流程」→ 让你拍板。

        继续 = 忽略重编建议，按原计划跑（要求已留痕）；
        停下重编 = 中止当前执行（保留已产出、可续跑），你回去改波次再启动。
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("📋 项目经理：需要重新编计划")
        box.setText("项目经理认为你这条要求会影响后续波次，需要重新编计划、重新走审批。")
        box.setInformativeText((reply or "")[:800])
        b_cont = box.addButton("▶ 继续按原计划跑", QMessageBox.AcceptRole)
        b_stop = box.addButton("⏹ 停下，我去重编计划", QMessageBox.DestructiveRole)
        box.setDefaultButton(b_stop)
        box.exec()
        clicked = box.clickedButton()
        try:
            if self.worker is not None:
                self.worker.set_replan_result(clicked is b_cont)
        except Exception:
            pass

    def _on_log(self, text):
        self.log_view.insertPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _log_line(self, text):
        """往执行日志区追加一行本地提示（续跑/重跑选择等，不走 worker 信号）。

        v4.124.8 修复：此前 _on_resume_clicked 调了 self._log_line() 但本类
        根本没这个方法 → 选「从上次续跑」后抛 AttributeError 静默失败，
        用户看不到「还得再点一次启动」的提示，体感就是"点续跑没动静"。
        """
        self.log_view.append(text)

    def _on_done(self, text):
        if text:
            self.log_view.append("\n" + "=" * 30 + " 军团产出 " + "=" * 30 + "\n")
            self.log_view.append(text)
        # v4.124.15：报告落盘 —— 此前成稿只在日志区（内存）里显示一遍，
        # 关掉窗口就什么都没了（「成功跑完了，但是我的报告没有给我」）。
        self._save_and_deliver_report(text)
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
        # v4.135：跑批检测到的技能缺口 → 自动去 GitHub 找候选，弹「你批了才装」面板，
        # 闭合「差技能→找→批→装→挂」断环（此前缺口只写文字、搜索只活在手按钮里）。
        try:
            _gm = getattr(self.worker, "last_meta", None) or {}
            _sk = _gm.get("skill_gaps") or []
            if _sk:
                dlg = SkillGapCandidateDialog(
                    self, _sk, mw=self.mw,
                    project=self._cur_project(), data=self.data)
                dlg.exec()
        except Exception as e:
            log.warning("技能缺口候选面板打开失败: %s", e)

    # ---- v4.124.15：报告落盘 + 交付 ----
    def _save_and_deliver_report(self, text):
        """把军团成稿写成 md 落盘、挂进交付物区，并给「打开」入口。"""
        p = self._cur_project() or {}
        meta = getattr(self.worker, "last_meta", None) or {}
        # 兜底：UI 没收到正文（崩溃/信号丢失）→ 从落盘产出重建，至少把稿捞回来
        if (not text or text.strip() == "（军团未产出内容）") and meta.get("run_id"):
            text = legion.rebuild_output_text(meta.get("run_id", ""))
            if text:
                self.log_view.append("♻️ 产出已从运行记录重建（自动找回，避免白跑）")
        if not text or text.strip() == "（军团未产出内容）":
            self.log_view.append("⚠️ 本次没有可交付的产出（未落盘报告）")
            return
        # v4.125 M-09：worker 线程内已兜底落盘（关窗也不丢）——UI 直接复用，
        # 不再重复写第二份；仅当 worker 落盘失败（如目录被锁）才由 UI 补写。
        path = meta.get("report_path") or ""
        if not path or not os.path.exists(path):
            path = legion.save_report(
                p.get("name", "") or "军团",
                meta.get("task", "") or (self.task_edit.text() if hasattr(self, "task_edit") else ""),
                text,
                run_id=meta.get("run_id", ""),
                n_waves=meta.get("n_waves", 0),
                n_done=meta.get("n_done", 0),
                status=meta.get("status", "done"),
                gate_reports=meta.get("gate_reports"),
                summary=meta.get("summary", ""),
                capability=meta.get("capability_text", ""),
                missing_skills=meta.get("missing_skills_text", ""),
            )
        if not path:
            self.log_view.append("⚠️ 报告落盘失败（内容仍在上方日志里，可手动复制）")
            return
        self._last_report = path
        _has_sum = bool((meta.get("summary") or "").strip())
        self.log_view.append(
            f"\n📄 报告已保存：{path}"
            + ("（含项目经理结项总结）" if _has_sum else "（⚠️ 缺结项总结，仅产出拼接）"))
        # 挂进主窗口交付物区（双击即可打开）
        try:
            mw = getattr(self, "mw", None)
            if mw is not None and hasattr(mw, "_on_deliverable_added"):
                mw._on_deliverable_added(path, "md", os.path.basename(path))
                self.log_view.append("📦 已加入交付物区（主界面右侧，双击可打开）")
        except Exception as e:
            log.warning("登记交付物失败: %s", e)
        # 直接问一句要不要打开 —— 跑完最想要的就是看到它
        try:
            box = QMessageBox(self)
            box.setWindowTitle("军团跑完了")
            box.setText("报告已生成，要现在打开吗？")
            box.setInformativeText(path)
            b_open = box.addButton("📄 打开报告", QMessageBox.AcceptRole)
            box.addButton("稍后", QMessageBox.RejectRole)
            box.setDefaultButton(b_open)
            box.exec()
            if box.clickedButton() is b_open:
                self._open_path(path)
        except Exception:
            pass

    def _open_last_report(self):
        """打开最近一份报告（手动入口，防止弹窗被略过后找不回来）。"""
        path = getattr(self, "_last_report", "") or legion.latest_report()
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "报告", "还没有生成过报告。")
            return
        if not self._open_path(path):
            QMessageBox.information(self, "报告", f"已找到但打不开：\n{path}")

    @staticmethod
    def _open_path(path):
        try:
            os.startfile(path)
            return True
        except Exception:
            try:
                import subprocess
                subprocess.Popen(["notepad", path])
                return True
            except Exception:
                return False

    def _on_finished(self):
        # v4.137：统一由 _set_running_ui 复位（顺带关闭「联系项目经理」入口）
        self._set_running_ui(False)
        self._worker_pid = ""
        # v4.124.7：任务结束（跑完/中断/续跑）后刷新项目列表与续跑入口，
        # 让刚写入的 checkpoint 立即变成可见的 🟡 + ⏵ 续跑按钮。
        # 否则按钮停留在启动前的隐藏状态，用户会以为"没存上"。
        try:
            self._refresh_projects()
            self._refresh_head()
        except Exception as e:
            log.warning("_on_finished 刷新续跑入口失败: %s", e)

    def closeEvent(self, e):
        if self._worker_running():
            r = QMessageBox.question(
                self, "军团运行中",
                "军团仍在执行。关闭窗口不会中断它，产出也不会丢（会写进日志）。\n\n"
                "（想让它停下来：点任务框右边的「⛔ 停止」，别关窗口。）\n\n"
                "确定关闭？")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        e.accept()
