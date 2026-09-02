# -*- coding: utf-8 -*-
"""v4.107 导演台底部常驻对话条——独立会话，与主对话模块零交集。

隔离清单（对应 AgentWorker(isolated=True, force_complex=True)）：
1. 不回写主会话历史（agent._sync_to_session 直接 return）
2. 不写长期记忆（agent._auto_remember 跳过）
3. 不落盘步骤轨迹（StepTracer enabled=False）、不写任务级经验
4. 独立 history：存到项目目录 director_chat.json，跟随项目可回溯；换项目自动换一份
5. 受限工具集：只有 5 个 director_*（不含 delete_file / run_command / write_file 等高危工具）
6. 独立渲染：写进导演台自己的显示区，不碰主聊天框、不进主 session

渲染刻意用纯文本 QTextEdit 而非 WebView：对话条只是指令通道，不需要 Markdown，
且避免再拉一个 QtWebEngine 进程（现有 5 个已够）。
"""

import os
import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                               QPushButton)

# 只给导演台的 5 个工具——这是"不给高危工具"的白名单，新增导演工具必须在此登记，
# 否则 Agent 看不到它（fail-closed，宁可少给不多给）。
DIRECTOR_TOOL_NAMES = (
    "director_status",
    "director_revise_clip",
    "director_revise_keyframe",
    "director_revise_character",
    "director_merge",
)

DIRECTOR_SYS = """你是这个视频项目的导演助理，只处理导演台的事，不干别的。

【能做的事】只有这 5 件：
- director_status：查项目进度（有哪些人物/分镜、关键帧和片段生成到哪了）
- director_revise_clip：重生成第 N 镜的视频
- director_revise_keyframe：重生成第 N 镜的关键帧（静帧）
- director_revise_character：重生成第 N 个人物的三视图
- director_merge：把所有片段合成成片

【铁律】
1. 分镜号、人物序号都从 1 开始数，工具参数也是从 1 开始。
2. 用户没说清改哪个（第几镜 / 哪个人物）→ 先 director_status 查清楚，禁止猜。
3. 一次只做一件事。改完用一句话说明改了什么，禁止长篇复述和客套话。
4. 工具返回什么就是什么，禁止编造结果、禁止假装已经调用过工具。
5. 用户只是问进度或闲聊 → 只调 director_status，不要动手改东西。
6. 修改意见要原样传进工具的 note 参数，不要自己缩写成"优化画面"这种空话。

【当前项目状态】
{state}
"""

_TOOL_CN = {
    "director_status": "查进度",
    "director_revise_clip": "重生成分镜",
    "director_revise_keyframe": "重生成关键帧",
    "director_revise_character": "重生成三视图",
    "director_merge": "合成成片",
}

_MAX_HISTORY = 40      # 只保留最近 40 条（user+assistant），防上下文无限膨胀
_MAX_REPLAY = 12       # 重载项目时最多回填显示最近 12 条


class DirectorChatBar(QWidget):
    """导演台底部常驻对话条：上方小对话记录 + 下方输入框 + 发送/停止。"""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._worker = None
        self._streaming = False
        self._proj_dir = None      # 当前绑定的项目目录（换项目即换历史）
        self.history = []
        self._build_ui()
        self._load_history()

    # ---------- UI ----------
    def _build_ui(self):
        from ui import THEME
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 10)
        lay.setSpacing(6)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(110)
        self.log.setPlaceholderText(
            "在这里指挥导演台：例如「第3镜的关键帧改成夜晚」「主角换成短发」「合成成片」")
        self.log.setStyleSheet(
            f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:10px;padding:8px 10px;font-size:12px;color:{THEME['text']};}}")
        lay.addWidget(self.log)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QTextEdit()
        self.input.setFixedHeight(48)
        self.input.setPlaceholderText("用大白话下指令，Enter 发送 / Shift+Enter 换行")
        self.input.setStyleSheet(
            f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
            f"border-radius:10px;padding:8px 10px;font-size:13px;color:{THEME['text']};}}")
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
            f"QPushButton{{background:{THEME['accent']};color:#fff;border:none;"
            f"border-radius:10px;padding:0 18px;font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{background:{THEME['accent_hover']};}}"
            f"QPushButton:disabled{{background:{THEME['border']};color:{THEME['faint']};}}")
        self.stop_btn.setStyleSheet(
            f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
            f"border:1px solid {THEME['border']};border-radius:10px;padding:0 18px;"
            f"font-size:13px;}}")
        self.clear_btn.setStyleSheet(
            f"QPushButton{{background:{THEME['card']};color:{THEME['faint']};"
            f"border:1px solid {THEME['border']};border-radius:10px;padding:0 14px;"
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
        self.history.append({"role": "user", "content": text})
        self._trim_history()
        self._save_history()
        self._start_worker()

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

    # ---------- 信号槽 ----------
    def _on_status(self, text):
        # Agent 心跳状态只显示最新一条，避免刷屏（工具执行期间状态变化频繁）
        if text:
            self._set_hint(text)

    def _on_tool_started(self, data):
        name = data.get("name", "")
        self._append("系统", f"⏳ {_TOOL_CN.get(name, name)}…")

    def _on_tool_finished(self, data):
        ok = data.get("success")
        name = data.get("name", "")
        if not ok:
            self._append("系统", f"❌ {_TOOL_CN.get(name, name)}失败")

    def _on_chunk(self, d):
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
        self._set_hint("")

    def _on_finished(self):
        self._worker = None
        self._set_busy(False)

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
        self._proj_dir = pd
        self.history = []
        self.log.clear()
        self._load_history()

    def _state_brief(self):
        """抓一份项目状态摘要塞进 system prompt，让模型不必每次都先查一遍。"""
        try:
            from director_panel import _agent_status
            st = _agent_status(self.app)
        except Exception as e:
            return f"（状态读取失败：{e}）"
        if not st.get("active"):
            return "当前没有进行中的项目（导演台还没开拍）。如实告诉用户先去开拍。"
        lines = [f"阶段 step={st.get('step')}，{'忙碌中' if st.get('busy') else '空闲'}"]
        for c in st.get("characters") or []:
            lines.append(f"人物{c['i']}：{c['name']}（三视图 {c['views_ok']}/3）")
        for s in st.get("shots") or []:
            lines.append(f"镜{s['i']}：{s['zh']}｜关键帧：{s['keyframe']}｜片段：{s['clip']}")
        if st.get("final"):
            lines.append(f"成片已生成：{st['final']}")
        return "\n".join(lines)
