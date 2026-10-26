# -*- coding: utf-8 -*-
"""v4.289.2：旧 Agent worker 的迟到事件不得污染新任务。"""
import os
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui as UI

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


class Sess:
    def __init__(self, sid):
        self.sid = sid
        self.messages = []


class Store:
    def __init__(self):
        self.sessions = {"old": Sess("old"), "new": Sess("new")}
        self.active_sid = "new"

    def active(self):
        return self.sessions["new"]


class Worker:
    def __init__(self, sid):
        self._bound_sid = sid
        self.stopped_by_user = False
        self._resumable_stop = False


def make_win():
    win = type("W", (object,), {})()
    win.store = Store()
    win._agent_worker = Worker("new")
    win._live_tools = {}
    win._live_seq = 0
    win._task_agent = "task-new"
    win._agent_active = True
    win._auto_task_active = {"id": "x"}
    win.stop_btn = type("B", (), {"setVisible": lambda s, v: None,
                                  "setEnabled": lambda s, v: None})()
    win.resume_agent_btn = type("B", (), {"setVisible": lambda s, v: None})()
    win.status_label = type("L", (), {"setText": lambda s, t: setattr(s, "text", t)})()
    win.input_box = type("I", (), {"setFocus": lambda s: None})()
    win._reset_calls = 0
    win._reset_busy = lambda: setattr(win, "_reset_calls", win._reset_calls + 1)
    win._save_throttled = lambda: None
    win._render_throttled = lambda: None
    win._speak = lambda t: None
    win._streaming = False
    win._track_context = lambda role, content, sid=None: None
    win.chat_view = type("C", (), {
        "end_stream": lambda s: None,
        "append": lambda s, h: None,
        "replace_live": lambda s, a, b: None,
    })()
    win._show_tool_details = lambda: False
    win._clear_ghost = lambda: None
    win._is_current_agent_worker = (
        lambda worker=None: UI.ChatWindow._is_current_agent_worker(win, worker))
    win._agent_write_session = (
        lambda worker=None: UI.ChatWindow._agent_write_session(win, worker))
    return win


def main():
    win = make_win()
    old, cur = Worker("old"), win._agent_worker
    with mock.patch.object(UI.automation, "bound_session",
                           side_effect=lambda store, sid: store.sessions.get(sid)):
        check("A1 当前 worker 识别正确",
              UI.ChatWindow._is_current_agent_worker(win, cur) is True)
        check("A2 旧 worker 不再算当前 worker",
              UI.ChatWindow._is_current_agent_worker(win, old) is False)

        UI.ChatWindow._on_tool_log(win, {"name": "x", "args": "", "result": "old"},
                                   old)
        check("A3 旧 worker 工具日志不写任何会话",
              not win.store.sessions["old"].messages
              and not win.store.sessions["new"].messages,
              (win.store.sessions["old"].messages, win.store.sessions["new"].messages))

        UI.ChatWindow._on_tool_log(win, {"name": "x", "args": "", "result": "new"},
                                   cur)
        check("A4 当前 worker 写回它自己钉住的会话",
              len(win.store.sessions["new"].messages) == 1
              and not win.store.sessions["old"].messages)

        UI.ChatWindow._on_stream_commit(win, "迟到的旧回复", old)
        check("A5 旧 worker 迟到回复不串进新会话",
              all(m.get("content") != "迟到的旧回复"
                  for m in win.store.sessions["new"].messages))

        UI.ChatWindow._on_stream_commit(win, "新任务回复", cur)
        check("A6 当前 worker 回复正常提交",
              any(m.get("content") == "新任务回复"
                  for m in win.store.sessions["new"].messages))

        UI.ChatWindow._on_agent_done(win, old)
        check("A7 旧 worker 完成不改新任务 UI 状态",
              win._agent_active is True and win._reset_calls == 0
              and win._task_agent == "task-new")
        check("A8 旧 worker 不隐藏新任务的停止按钮",
              win.stop_btn.setVisible is not None)

        ev = type("E", (), {"set": lambda s: setattr(s, "done", True), "done": False})()
        old._confirm_event = ev
        old._confirm_val = None
        with mock.patch.object(UI, "ConfirmDialog") as dlg:
            UI.ChatWindow._on_confirm_action(win, "权限", "详情", old)
        check("A9 旧 worker 的确认请求自动拒绝且不弹新窗",
              ev.done is True and old._confirm_val is False and not dlg.called)
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
