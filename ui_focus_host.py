# -*- coding: utf-8 -*-
"""ui_focus_host.py —— ChatWindow 专注对话控件接线（v4.264.0 从 ui.py 拆出）

人话三档、会话信任徽章、工具进度条、产物打开。纯逻辑在 ui_focus.py。
ChatWindow(FocusChromeMixin, ChatAuditMixin, QMainWindow) 继承接入；
self.cfg / self.permission_engine / self.chat_view 仍由 ChatWindow 提供。
"""
import os
import subprocess
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

import config
import ui_focus
from toast import toast


class FocusChromeMixin:
    def _show_tool_details(self):
        return ui_focus.show_tool_details(getattr(self, "cfg", None))

    def _on_show_tool_details_toggle(self, state):
        self.cfg[ui_focus.SHOW_TOOL_DETAILS_KEY] = bool(state)
        self._save_cfg()
        self.status_label.setText(
            "对话里显示工具细节：开" if state else "对话里显示工具细节：关")

    def _fill_persona_combo(self, combo):
        combo.blockSignals(True)
        combo.clear()
        for key, label, _hint in ui_focus.PERSONA_CHOICES:
            combo.addItem(label, key)
        combo.addItem("高级五档…", "")
        combo.blockSignals(False)

    def _on_persona_picked(self, index):
        combo = self.sender()
        tier = combo.itemData(index) if combo is not None else ""
        if not tier:
            if getattr(self, "_perm_advanced_btn", None) is not None:
                self._perm_advanced_btn.setChecked(True)
            self.status_label.setText("高级五档仍按下面的执行模式生效")
            return
        self._apply_persona(tier)

    def _apply_persona(self, tier):
        """人话档写回现有 agent_mode + permission_mode，不另起一套策略。"""
        agent_on, mode = ui_focus.state_from_persona(tier)
        self.agent_mode = agent_on
        self.cfg["agent_mode"] = agent_on
        self.permission_engine.set_mode(mode)
        self.permission_mode = mode
        self.agent_skip_confirm = (mode == "auto")
        self.cfg["agent_skip_confirm"] = self.agent_skip_confirm
        self.cfg["permission_mode"] = mode
        self._save_cfg()
        if getattr(self, "agent_toggle", None) is not None:
            self.agent_toggle.blockSignals(True)
            self.agent_toggle.setChecked(agent_on)
            self.agent_toggle.blockSignals(False)
        combo = getattr(self, "permission_mode_combo", None)
        if combo is not None:
            combo.blockSignals(True)
            idx = combo.findData(mode)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        self._sync_persona_widgets()
        self.status_label.setText(ui_focus.persona_status(tier))

    def _sync_persona_widgets(self):
        tier = ui_focus.persona_from_state(
            getattr(self, "agent_mode", False),
            getattr(self.permission_engine, "mode", "") if getattr(self, "permission_engine", None) else "")
        for combo in (getattr(self, "persona_combo", None),
                      getattr(self, "settings_persona_combo", None)):
            if combo is None:
                continue
            combo.blockSignals(True)
            idx = combo.findData(tier if tier else "")
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        if not tier and getattr(self, "_perm_advanced_btn", None) is not None:
            self._perm_advanced_btn.setChecked(True)

    def _sync_trust_badge(self):
        trusted = bool(getattr(self, "session_trusted", False))
        badge = getattr(self, "trust_badge_btn", None)
        if badge is not None:
            badge.setVisible(trusted)
        revoke = getattr(self, "trust_revoke_btn", None)
        if revoke is not None:
            revoke.setVisible(trusted)
        lbl = getattr(self, "_trust_settings_lbl", None)
        if lbl is not None:
            lbl.setText(
                "会话信任：本会话正在少问。点徽章或「收回少问」即可恢复逐项询问。"
                if trusted else
                "会话信任：会逐项询问。少问之后，对话输入区的徽章可以收回。")

    def _revoke_session_trust(self):
        self._reset_session_trust()
        try:
            toast("已收回少问，危险操作会再问你", kind="info")
        except Exception:
            pass
        try:
            self.status_label.setText("已收回少问")
        except Exception:
            pass

    def _tool_progress_note(self, display_name, running):
        st = getattr(self, "_tool_prog_state", None)
        if not isinstance(st, dict):
            st = {"total": 0, "done": 0, "running": "", "log": []}
            self._tool_prog_state = st
        if running:
            st["total"] = int(st.get("total") or 0) + 1
            st["running"] = display_name
            st.setdefault("log", []).append("正在 " + display_name)
        else:
            st["done"] = int(st.get("done") or 0) + 1
            st["running"] = ""
            st.setdefault("log", []).append("完成 " + display_name)
        st["log"] = st["log"][-8:]
        self._refresh_tool_progress()

    def _refresh_tool_progress(self):
        bar = getattr(self, "_tool_progress", None)
        if bar is None:
            return
        st = self._tool_prog_state
        cap = ui_focus.progress_caption(st.get("running") or "", st.get("done") or 0,
                                        st.get("total") or 0)
        self._tool_progress_label.setText(cap)
        self._tool_progress_detail.setText("\n".join(st.get("log") or []))
        bar.setVisible(bool(cap))

    def _toggle_tool_progress_detail(self):
        w = getattr(self, "_tool_progress_detail", None)
        if w is not None:
            w.setVisible(not w.isVisible())

    def _clear_tool_progress(self):
        self._tool_prog_state = {"total": 0, "done": 0, "running": "", "log": []}
        bar = getattr(self, "_tool_progress", None)
        if bar is not None:
            bar.setVisible(False)

    def _append_live_artifact_chip(self, rel):
        if not rel or getattr(self, "chat_view", None) is None:
            return
        seen = getattr(self, "_live_chip_paths", None)
        if seen is None:
            self._live_chip_paths = seen = set()
        key = str(rel)
        if key in seen:
            return
        html = ui_focus.chip_html([key])
        if not html:
            return
        seen.add(key)
        try:
            self.chat_view.append(html)
        except Exception:
            seen.discard(key)

    def _artifact_roots(self):
        roots = []
        for candidate in (
                getattr(config, "PRODUCTS_DIR", ""),
                getattr(config, "WORKSPACE_DIR", ""),
                getattr(config, "APP_DIR", ""),
        ):
            if candidate:
                roots.append(candidate)
        return roots

    def _open_artifact(self, rel, reveal=False):
        path = ui_focus.resolve_artifact(rel, self._artifact_roots())
        if not path or not os.path.exists(path):
            self.status_label.setText("文件不存在：%s" % (path or rel))
            return
        if not reveal:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        folder, selected = ui_focus.reveal_target(path)
        if sys.platform.startswith("win") and selected and os.path.isfile(selected):
            try:
                subprocess.Popen(["explorer", "/select,", selected])
                return
            except Exception:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
