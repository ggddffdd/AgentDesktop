# -*- coding: utf-8 -*-
"""v4.107 导演台底部常驻对话条——独立会话，与主对话模块零交集。

隔离清单（对应 AgentWorker(isolated=True, force_complex=True)）：
1. 不回写主会话历史（agent._sync_to_session 直接 return）
2. 不写长期记忆（agent._auto_remember 跳过）
3. 不落盘步骤轨迹（StepTracer enabled=False）、不写任务级经验
4. 独立 history：存到项目目录 director_chat.json，跟随项目可回溯；换项目自动换一份
5. 受限工具集：只有 director_* 白名单（不含 delete_file / run_command / write_file 等高危工具）
6. 独立渲染：写进导演台自己的显示区，不碰主聊天框、不进主 session

渲染刻意用纯文本 QTextEdit 而非 WebView：对话条只是指令通道，不需要 Markdown，
且避免再拉一个 QtWebEngine 进程（现有 5 个已够）。

v4.150「对话化」增强（对标 Pavo 导演台：改哪张图、改哪个镜、怎么改全在对话框完成）：
7. 顶部常驻状态行「第 N/7 步 · 名称 · ⏸ 待你确认」——一眼知道现在停在哪、等谁点头
8. 对话区 110→200px，可「⤢」展开到 340px（看懂长回复时用）
9. prefill()：面板按钮/卡片把意见模板预填进来并聚焦，替代原先的 QInputDialog 弹窗
10. 工具成功后**回缩略图**（改完就地看到新图，不必自己翻页），并自动切到对应步骤页
"""

import os
import json

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QTextCursor, QImage, QTextDocument
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                               QPushButton, QLabel)

# 只给导演台的 11 个工具——这是"不给高危工具"的白名单，新增导演工具必须在此登记，
# 否则 Agent 看不到它（fail-closed，宁可少给不多给）。
# v4.150：从 5 个补到 11 个——原先 rollback / gen_clues / revise_clue 三个已在
# director_agent_tools 里实现却不在白名单，对话里说「回滚」「锁一下道具」会被拒；
# 再加本轮新增的 revise_story / revise_shots / confirm，覆盖「每一步都能用对话改」。
DIRECTOR_TOOL_NAMES = (
    "director_status",
    "director_revise_story",
    "director_revise_shots",
    "director_revise_clip",
    "director_revise_keyframe",
    "director_revise_character",
    "director_revise_clue",
    "director_gen_clues",
    "director_rollback",
    "director_confirm",
    "director_merge",
)

DIRECTOR_SYS = """你是这个视频项目的导演助理，只处理导演台的事，不干别的。

【能做的事】只有这 11 件（都只能动当前打开的这个项目，不能碰别的文件）：
- director_status：查项目进度（有哪些人物/分镜，关键帧和片段生成到哪了）
- director_revise_story：按意见重写剧本（纯文本，不花钱）
- director_revise_shots：按意见重排/重拆 分镜（纯文本，不花钱）
- director_revise_keyframe：重生成**某一镜**的关键帧静帧（idx=镜号；idx="all" 才是整批）
- director_revise_clip：重生成**某一镜**的视频片段（idx=镜号）
- director_revise_character：重生成**某一个角色**的三视图（idx=角色号）
- director_revise_clue：重生成某一件道具/场景资产的参考图（idx=序号）
- director_gen_clues：抽取并生成关键道具/场景资产（跨镜一致性）
- director_rollback：把某镜片段/关键帧、某角色、某道具回滚到上一版
- director_confirm：采用当前步骤的产物并推进到下一步（用户说「采用/确定/继续」时用）
- director_merge：把所有片段合成成片

【铁律】
1. 分镜号、人物序号、道具序号都从 1 开始数，工具参数也是从 1 开始。
2. 用户没说清改哪个（第几镜 / 哪个人物 / 哪件道具）→ 先 director_status 查清楚，禁止猜。
3. 一次只做一件事。改完用一句话说明改了什么，禁止长篇复述和客套话。
4. 工具返回什么就是什么，禁止编造结果、禁止假装已经调用过工具。
   工具返回 ok=false → 如实告诉用户失败原因，绝不说「已完成」。
5. 用户只是问进度或闲聊 → 只调 director_status，不要动手改东西。
6. 修改意见要原样传进工具的 note 参数，不要自己缩写成"优化画面"这种空话。
7. 用户发来的是**带前缀的引导文本**（面板按钮预填的），按前缀理解意图：
   「重写剧本：xx」→ director_revise_story(note=xx)
   「重排分镜：xx」→ director_revise_shots(note=xx)
   「重新生成全部关键帧：xx」→ director_revise_keyframe(idx="all", note=xx)
   「重新生成人物：xx」→ director_revise_character(idx="all", note=xx)（整批按钮）；
                            若前缀后点名了角色（如「只改小明」）→ 改成对应 idx，别整批。
   「重新抽取道具/场景资产：xx」→ director_gen_clues(note=xx)
   「镜3关键帧：xx」→ director_revise_keyframe(idx=3, note=xx)
   「镜3视频：xx」→ director_revise_clip(idx=3, note=xx)
   「角色2（小明）：xx」→ director_revise_character(idx=2, note=xx)
   「道具1（木剑）：xx」→ director_revise_clue(idx=1, note=xx)
   前缀后面**空的**（用户没补意见）→ 按「原样重试」处理，note 留空。
8. 用户明确要求整批（"全部""所有关键帧"）才用 idx="all"；否则一律点名到具体镜/角色。
   拿不准就先用 director_status 看清楚有几镜、几个角色，再问用户要改哪个。
   ⚠️ 整批会重生成每一镜，耗时与费用都高——**宁可多问一句，也不要替用户决定整批**。

【当前项目状态】
{state}
"""

_TOOL_CN = {
    "director_status": "查进度",
    "director_revise_story": "重写剧本",
    "director_revise_shots": "重排分镜",
    "director_revise_clip": "重生成分镜",
    "director_revise_keyframe": "重生成关键帧",
    "director_revise_character": "重生成三视图",
    "director_revise_clue": "重生成道具图",
    "director_gen_clues": "抽取道具/场景资产",
    "director_rollback": "版本回滚",
    "director_confirm": "采用当前步",
    "director_merge": "合成成片",
}

_MAX_HISTORY = 40      # 只保留最近 40 条（user+assistant），防上下文无限膨胀
_MAX_REPLAY = 12       # 重载项目时最多回填显示最近 12 条


class DirectorChatBar(QWidget):
    """导演台底部常驻对话条：步骤状态行 + 对话记录 + 输入框 + 发送/停止。"""

    # v4.150：对话区两档高度（收起/展开）
    H_COLLAPSED = 200
    H_EXPANDED = 340

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._worker = None
        self._streaming = False
        self._stream_pos = 0
        self._proj_dir = None      # 当前绑定的项目目录（换项目即换历史）
        self._expanded = False
        self.history = []
        self._build_ui()
        self._load_history()
        self.refresh_status()

    # ---------- UI ----------
    def _build_ui(self):
        from ui import THEME
        self._theme = THEME
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 10)
        lay.setSpacing(8)

        # v4.150 状态行：常驻显示「第 N/7 步 · 名称 · ⏸ 待你确认」，
        # 让「现在停在哪、是不是在等我点头」一眼可见（原先只能从日志猜）。
        stat_row = QHBoxLayout()
        stat_row.setSpacing(8)
        self.step_label = QLabel("尚未开拍")
        self.step_label.setStyleSheet(
            f"color:{THEME['text']};font-size:12px;font-weight:600;")
        stat_row.addWidget(self.step_label)
        stat_row.addStretch(1)
        self.expand_btn = QPushButton("⤢ 展开")
        self.expand_btn.setFixedHeight(24)
        self.expand_btn.setCursor(Qt.PointingHandCursor)
        self.expand_btn.setToolTip("展开/收起对话区（看懂长回复时用；不影响任何操作）")
        self.expand_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{THEME['faint']};border:none;"
            f"font-size:12px;padding:0 8px;}}"
            f"QPushButton:hover{{color:{THEME['text']};}}")
        self.expand_btn.clicked.connect(self._toggle_expand)
        stat_row.addWidget(self.expand_btn)
        lay.addLayout(stat_row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(self.H_COLLAPSED)
        self.log.setPlaceholderText(
            "在这里指挥导演台：例如「第3镜的关键帧改成夜晚」「主角换成短发」「合成成片」")
        self.log.setStyleSheet(
            f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:10px;padding:8px 12px;font-size:12px;color:{THEME['text']};}}")
        lay.addWidget(self.log)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QTextEdit()
        self.input.setFixedHeight(48)
        self.input.setPlaceholderText("用大白话下指令，Enter 发送 / Shift+Enter 换行")
        self.input.setStyleSheet(
            f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:10px;padding:8px 12px;font-size:13px;color:{THEME['text']};}}")
        self.input.installEventFilter(self)
        row.addWidget(self.input, 1)

        self.clear_btn = QPushButton("清空")
        self.send_btn = QPushButton("发送")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setVisible(False)
        for b in (self.clear_btn, self.send_btn, self.stop_btn):
            b.setFixedHeight(48)
            b.setCursor(Qt.PointingHandCursor)
        self.send_btn.setStyleSheet(
            f"QPushButton{{background:{THEME['accent']};color:white;border:none;"
            f"border-radius:10px;padding:0 16px;font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{background:{THEME['accent_hover']};}}"
            f"QPushButton:disabled{{background:{THEME['border']};color:{THEME['faint']};}}")
        self.stop_btn.setStyleSheet(
            f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
            f"border:1px solid {THEME['border']};border-radius:10px;padding:0 16px;"
            f"font-size:13px;}}")
        self.clear_btn.setStyleSheet(
            f"QPushButton{{background:{THEME['card']};color:{THEME['faint']};"
            f"border:1px solid {THEME['border']};border-radius:10px;padding:0 12px;"
            f"font-size:13px;}}"
            f"QPushButton:hover{{color:{THEME['text']};}}")
        self.clear_btn.setToolTip("清空当前项目的对话历史（不动已生成的剧本/三视图/分镜/成片）")
        self.clear_btn.clicked.connect(self._clear_chat)
        self.send_btn.clicked.connect(self.send)
        self.stop_btn.clicked.connect(self._on_stop)
        row.addWidget(self.clear_btn)
        row.addWidget(self.send_btn)
        row.addWidget(self.stop_btn)
        lay.addLayout(row)

    def eventFilter(self, obj, ev):
        # QTextEdit 默认 Enter 换行；对话条里 Enter 发送更符合直觉，Shift+Enter 才换行。
        if obj is self.input and ev.type() == ev.Type.KeyPress:
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if ev.modifiers() & Qt.ShiftModifier:
                    return False
                self.send()
                return True
        return super().eventFilter(obj, ev)

    # ---------- 发送 ----------
    def send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        if self._worker is not None:
            self._append("系统", "上一条指令还在跑，等它完成再发。")
            return
        self.input.clear()
        self._append("你", text)
        # v4.108 H-15：先做可执行性预检，被拒的指令不写 history（不落库、不下轮可见）——
        # 否则下一轮模型看到"用户提过这个需求"，会把它当已确认任务直接执行。
        _reject = self._exec_precheck()
        if _reject:
            self._append("系统", _reject)
            return
        self.history.append({"role": "user", "content": text})
        self._trim_history()
        self._save_history()
        self._start_worker()

    def _exec_precheck(self):
        """指令执行前的可执行性预检：返回 None=可执行，str=拒绝原因。"""
        import config
        try:
            all_tools = config.get_all_tools(self.app.cfg)
        except Exception:
            all_tools = []
        tools = [t for t in all_tools
                 if (t.get("function") or {}).get("name") in DIRECTOR_TOOL_NAMES]
        if not tools:
            return "导演工具未加载，无法执行指令（重启程序可恢复）。"
        if getattr(self.app, "director_busy", False):
            return "导演台正在生成中，等它跑完再下指令。"
        return None

    def _start_worker(self):
        from agent import AgentWorker
        import config
        try:
            all_tools = config.get_all_tools(self.app.cfg)
        except Exception:
            all_tools = []
        tools = [t for t in all_tools
                 if (t.get("function") or {}).get("name") in DIRECTOR_TOOL_NAMES]
        if not tools:
            self._append("系统", "导演工具未加载，无法执行指令（重启程序可恢复）。")
            return
        # 导演台正在生成（点开始导演/逐镜生成中）时，先别塞指令，避免和主流程抢状态
        if getattr(self.app, "director_busy", False):
            self._append("系统", "导演台正在生成中，等它跑完再下指令。")
            return
        msgs = [{"role": "system",
                 "content": DIRECTOR_SYS.format(state=self._state_brief())}]
        msgs += [dict(m) for m in self.history]
        w = AgentWorker(self.app, msgs, tools, [],
                        isolated=True, force_complex=True)
        self._worker = w
        w.stream_chunk.connect(self._on_chunk)
        w.stream_commit.connect(self._on_commit)
        w.status.connect(self._on_status)
        w.tool_started.connect(self._on_tool_started)
        w.tool_finished.connect(self._on_tool_finished)
        w.done.connect(self._on_done)
        w.finished.connect(self._on_finished)
        # 成片登记到交付物区（agent 隔离模式唯一保留主线程副作用，符合设计意图）
        try:
            w.deliverable_added.connect(self.app._on_deliverable_added)
        except Exception:
            pass
        self._streaming = False
        self._set_busy(True)
        w.start()

    def _on_stop(self):
        if self._worker is not None:
            self._worker.request_stop()
            self._append("系统", "已请求停止。")

    def _clear_chat(self):
        """清空当前项目的对话历史（内存 + 显示 + 持久化），不动项目产物。"""
        if self._worker is not None:
            self._append("系统", "正在生成中，先点「停止」再清空。")
            return
        self.history = []
        self.log.clear()
        self._save_history()      # 写空历史，重开/换项目回来也是干净的
        self.input.clear()
        self.input.setFocus()

    # ---------- v4.150 对话化增强 ----------
    def _toggle_expand(self):
        self._expanded = not self._expanded
        self.log.setFixedHeight(self.H_EXPANDED if self._expanded else self.H_COLLAPSED)
        self.expand_btn.setText("⤡ 收起" if self._expanded else "⤢ 展开")

    def prefill(self, text):
        """把意见模板预填进输入框、光标置末尾并聚焦（替代原先的 QInputDialog 弹窗）。

        只预填不发送：用户补完意见自己按 Enter，避免「点了按钮就默默开始烧钱」。
        生成中（busy）时不预填，免得打了一半被 readonly 吞掉。
        """
        try:
            if self._worker is not None or self.input.isReadOnly():
                self._append("系统", "正在生成中，等这一波跑完再改。")
                return False
            self.input.setPlainText(text or "")
            cur = self.input.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            self.input.setTextCursor(cur)
            self.input.setFocus()
            return True
        except Exception:
            return False

    def refresh_status(self):
        """刷新顶部状态行：第 N/7 步 · 名称 · ⏸ 待你确认 / ⏳ 生成中。"""
        try:
            from director_panel import _agent_status, STEP_LABELS as _SL
        except Exception:
            return
        try:
            st = _agent_status(self.app)
        except Exception:
            st = {}
        if not st.get("active"):
            self.step_label.setText("尚未开拍 · 填好主题点「开始导演」")
            self.step_label.setStyleSheet(
                f"color:{self._theme['faint']};font-size:12px;")
            return
        step = int(st.get("step") or 0)
        name = st.get("step_label") or "?"
        total = len(_SL) - 1        # 7 项里「主题」是表单，实际工作步 1~6
        shown = min(step, total)
        busy = bool(st.get("busy"))
        if busy:
            txt = f"第 {shown}/{total} 步 · {name} · ⏳ 生成中…"
            color = self._theme["accent"]
        elif step >= 6:
            txt = f"第 {total}/{total} 步 · {name} · 可合成成片"
            color = self._theme["faint"]
        else:
            txt = f"第 {shown}/{total} 步 · {name} · ⏸ 待你确认"
            color = self._theme["live_green"]
        self.step_label.setText(txt)
        self.step_label.setStyleSheet(f"color:{color};font-size:12px;font-weight:600;")

    def _state_brief(self):
        """抓一份项目状态摘要塞进 system prompt，让模型不必每次都先查一遍。"""
        try:
            from director_panel import _agent_status
            st = _agent_status(self.app)
        except Exception as e:
            return f"（状态读取失败：{e}）"
        if not st.get("active"):
            return "当前没有进行中的项目（导演台还没开拍）。如实告诉用户先去开拍。"
        # v4.127：先给一行总量口径（角色 X 件 / 关键帧 N/M / 视频未开始），
        # 否则人物、分镜阶段查进度会被答成「什么数据都没有」。
        lines = [st.get("summary") or
                 f"阶段 step={st.get('step')}，{'忙碌中' if st.get('busy') else '空闲'}"]
        # v4.150：显式写出「现在停在第几步、等不等确认」，模型才能正确响应「采用/下一步」。
        _step = int(st.get("step") or 0)
        if st.get("busy"):
            lines.append(f"⏳ 当前正在生成（第 {_step} 步 {st.get('step_label')}），"
                         f"此时任何修改指令都会被拒，如实告知用户稍等。")
        elif 1 <= _step <= 5:
            lines.append(f"⏸ 当前停在第 {_step}/6 步（{st.get('step_label')}），"
                         f"正等用户确认。用户说「采用/确定/继续」→ 调 director_confirm。")
        elif _step >= 6:
            lines.append("已进入合成步骤，用户要出成片请调 director_merge。")
        for c in st.get("characters") or []:
            lines.append(f"人物{c['i']}：{c['name']}（三视图 {c['views_ok']}/3）")
        for c in st.get("clues") or []:
            lines.append(f"道具/资产{c['i']}：{c['name']}（参考图 {c['image']}）")
        for s in st.get("shots") or []:
            lines.append(f"镜{s['i']}：{s['zh']}｜关键帧：{s['keyframe']}｜片段：{s['clip']}")
        if st.get("final"):
            lines.append(f"成片已生成：{st['final']}")
        return "\n".join(lines)

    # ---------- 信号槽 ----------
    # v4.145 修复②：所有槽首行校验 sender() 必须是「当前 worker」，丢弃换项目后
    # 迟到的陈旧事件（否则会把上一项目的助手回复写进新项目历史 / 误清当前 worker）。
    def _on_status(self, text):
        if self.sender() is not self._worker:
            return
        # Agent 心跳状态只显示最新一条，避免刷屏（工具执行期间状态变化频繁）
        if text:
            self._set_hint(text)
        self.refresh_status()

    def _on_tool_started(self, data):
        if self.sender() is not self._worker:
            return
        name = data.get("name", "")
        self._append("系统", f"⏳ {_TOOL_CN.get(name, name)}…")

    def _on_tool_finished(self, data):
        if self.sender() is not self._worker:
            return
        ok = data.get("success")
        name = data.get("name", "")
        if not ok:
            self._append("系统", f"❌ {_TOOL_CN.get(name, name)}失败")
            self.refresh_status()
            return
        if name == "director_status":
            self.refresh_status()
            return
        # v4.150：改完就地回一张缩略图——省掉「说完还得自己翻回上面那页找新图」。
        self._append_artifact()
        self.refresh_status()

    def _append_artifact(self):
        """把刚改动的那件产物在对话里回一张缩略图 + 一句说明（只读，失败静默）。"""
        try:
            from director_panel import artifact_thumb
            path, label = artifact_thumb(self.app)
        except Exception:
            return
        if not (path and label):
            return
        self._insert(f"🖼 {label} 已更新：\n")
        self._insert_image(path)
        self._set_hint("")

    def _insert_image(self, path):
        """在只读 QTextEdit 里插图：QTextEdit 不认 file:// 自动加载，必须先
        addResource 注册，再用 insertHtml 引用同一 URL（这是 Qt 的既定用法）。"""
        try:
            img = QImage(str(path))
            if img.isNull():
                return
            if img.width() > 170:
                img = img.scaledToWidth(170, Qt.SmoothTransformation)
            url = QUrl.fromLocalFile(os.path.abspath(str(path)))
            doc = self.log.document()
            _rt = getattr(QTextDocument, "ResourceType", QTextDocument)
            doc.addResource(_rt.ImageResource, url, img)
            cur = self.log.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            cur.insertHtml(f'<img src="{url.toString()}" width="{img.width()}">')
            cur.insertText("\n")
            cur.movePosition(QTextCursor.MoveOperation.End)
            self.log.setTextCursor(cur)
        except Exception:
            pass

    def _on_chunk(self, d):
        if self.sender() is not self._worker:
            return
        if not d:
            return
        if not self._streaming:
            self._streaming = True
            self._insert("🎬 导演：")
            # 记录流式片段起点：_insert 已把光标滚到末尾，起点即「导演：」标签之后
            self._stream_pos = self.log.textCursor().position()
        # stream_chunk 语义是「累积文本」（与主聊天 jsStream 的替换语义一致），
        # 而 QTextEdit 的 insertPlainText 是「追加」——若直接追加，累积文本会被
        # 一遍遍重复拼接（用户实证「镜镜3镜3关键镜3关键帧…」灾难）。这里手动做
        # 「替换」：选中 [起点, 末尾] 旧片段，用最新累积文本整体覆盖。
        cur = self.log.textCursor()
        cur.beginEditBlock()
        cur.setPosition(self._stream_pos)
        cur.movePosition(QTextCursor.MoveOperation.End,
                         QTextCursor.MoveMode.KeepAnchor)
        cur.insertText(d)
        cur.endEditBlock()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.log.setTextCursor(cur)

    def _on_commit(self, text):
        if self.sender() is not self._worker:
            return
        _t = (text or "").strip()
        if self._streaming:
            self._insert("\n")
            self._streaming = False
        elif _t:
            # 兜底：模型没走流式 chunk 直接 commit（超时/收敛/熔断等提示消息），
            # 此时 _streaming 为 False，若不补渲染会只进历史不上屏。
            self._insert(f"🎬 导演：{_t}\n")
        if _t:
            self.history.append({"role": "assistant", "content": _t})
            self._trim_history()
            self._save_history()
        self._set_hint("")

    def _on_done(self):
        if self.sender() is not self._worker:
            return
        self._set_hint("")

    def _on_finished(self):
        if self.sender() is not self._worker:
            return
        self._worker = None
        self._set_busy(False)
        self.refresh_status()

    def _set_busy(self, busy):
        self.send_btn.setEnabled(not busy)
        self.clear_btn.setEnabled(not busy)
        self.stop_btn.setVisible(busy)
        self.input.setReadOnly(busy)
        if not busy:
            self.input.setFocus()

    def _set_hint(self, text):
        # 状态走 placeholder，不占对话区版面
        try:
            self.input.setPlaceholderText(
                text or "用大白话下指令，Enter 发送 / Shift+Enter 换行")
        except Exception:
            pass

    # ---------- 渲染 ----------
    def _append(self, who, text):
        self._insert(f"{who}：{text}\n")

    def _insert(self, s):
        self.log.insertPlainText(s)
        c = self.log.textCursor()
        c.movePosition(c.MoveOperation.End)
        self.log.setTextCursor(c)

    # ---------- 历史（跟随项目） ----------
    def _chat_path(self):
        p = getattr(self.app, "director_pipeline", None)
        pd = getattr(p, "project_dir", None) if p is not None else None
        if not pd:
            return None
        return os.path.join(pd, "director_chat.json")

    def _save_history(self):
        path = self._chat_path()
        if not path:
            return
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"history": self.history}, f, ensure_ascii=False, indent=2)
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
                if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)
            ]
        except Exception:
            self.history = []
        for m in self.history[-_MAX_REPLAY:]:
            self._append("你" if m["role"] == "user" else "导演", m["content"])

    def _trim_history(self):
        if len(self.history) > _MAX_HISTORY:
            self.history = self.history[-_MAX_HISTORY:]

    def reload_for_project(self):
        """换项目（新建 pipeline / 载入续跑任务）后调用：切到该项目的对话历史。

        项目目录没变则不动作，避免误清空。
        """
        p = getattr(self.app, "director_pipeline", None)
        pd = getattr(p, "project_dir", None) if p is not None else None
        if pd == self._proj_dir:
            return
        # v4.145 修复②：换项目前先停掉在跑的旧 worker——否则旧 worker 迟到的
        # stream_commit 会把上一项目的助手回复写进新项目的 director_chat.json
        # （跨项目历史静默损坏），且 _on_finished 误清当前 worker 引用。
        if self._worker is not None:
            try:
                self._worker.request_stop()
            except Exception:
                pass
            self._disconnect_worker(self._worker)
            self._worker = None
            self._streaming = False
            self._set_busy(False)
        self._proj_dir = pd
        self.history = []
        self.log.clear()
        self._load_history()
        self.refresh_status()

    def _disconnect_worker(self, w):
        """断开一个 worker 的全部信号，丢弃其迟到事件（v4.145 修复②）。"""
        if w is None:
            return
        pairs = [
            (w.stream_chunk, self._on_chunk),
            (w.stream_commit, self._on_commit),
            (w.status, self._on_status),
            (w.tool_started, self._on_tool_started),
            (w.tool_finished, self._on_tool_finished),
            (w.done, self._on_done),
            (w.finished, self._on_finished),
        ]
        try:
            pairs.append((w.deliverable_added, self.app._on_deliverable_added))
        except Exception:
            pass
        for sig, slot in pairs:
            try:
                sig.disconnect(slot)
            except Exception:
                pass
