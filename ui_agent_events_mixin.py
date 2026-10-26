# -*- coding: utf-8 -*-
"""ui_agent_events_mixin.py —— ChatWindow 的 Agent 事件渲染 mixin。

v4.289.2 从 ui.py 抽出「工具完成」事件处理，守住 ui.py < 10000 行红线。
抽出的代码保持原行为，仅增加 worker 身份门控：旧 worker 迟到事件直接丢弃。
"""
import html as html_mod

import ui_focus
from PySide6.QtWidgets import QSystemTrayIcon


def worker_event_is_current(host, worker=None):
    """事件所属 worker 是否仍是当前任务；None 保持老调用口径。"""
    return worker is None or worker is getattr(host, "_agent_worker", None)


class ChatAgentEventsMixin:
    def _on_tool_finished(self, data, worker=None):
        if not worker_event_is_current(self, worker):
            return
        name = data.get("name", "")
        display_name = self._TOOL_NAME_MAP.get(name, name)
        result_preview = html_mod.escape(str(data.get("result_preview", ""))[:100])
        agent_index = data.get("index", 0)

        rec = self._live_tools.get(agent_index)
        if rec is not None and rec.get("name") == name:
            ui_seq = rec["ui_seq"]
            rec["status"] = "done"
            rec["result"] = result_preview
        else:
            # 找不到 started（历史/直发 finished）：兜底追加一张 done 卡，不丢信息
            self._live_seq += 1
            ui_seq = self._live_seq
            self._live_tools[agent_index] = {
                "ui_seq": ui_seq, "name": name, "display_name": display_name,
                "args_preview": "", "status": "done", "result": result_preview,
            }
        self._tool_progress_note(display_name, running=False)
        # 原位替换 running 卡 → done 卡。默认不刷卡，只更新输入框上方的进度条。
        if self._show_tool_details() and getattr(self, "chat_view", None) is not None:
            self.chat_view.replace_live(
                f"live-tool-{ui_seq}",
                self._tool_card_html(ui_seq, display_name, done=True,
                                     result_preview=result_preview))
        _args = (rec or {}).get("args", "")
        for _p in ui_focus.extract_artifact_paths(
                name, _args, data.get("result_preview", "")):
            self._append_live_artifact_chip(_p)

        # v4.84：模型刚提交了一个待审核技能，刷新徽标并提示
        if name == "create_skill":
            self._refresh_skill_review_label()
            try:
                import skill_review
                n = skill_review.count_pending(self.cfg)
                if n > 0 and getattr(self, "tray_app", None):
                    self.tray_app.tray.showMessage(
                        "技能审核", f"新技能已提交到审核队列，共 {n} 个待你通过。",
                        QSystemTrayIcon.Information, 6000)
            except Exception:
                pass
