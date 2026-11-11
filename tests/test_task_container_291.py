# -*- coding: utf-8 -*-
"""v4.291.0 任务容器验收：过程消息不再与正式回答混排。

对应大哥的第二轮指摘 + 补充纠偏：
  工具细节已默认关闭，主因是「工具前后的助手话、校验提示、机器小结、
  最终答复仍以普通消息平铺」。本套件守住：
    A 纯函数（拆分 / 分组 / 转义 / 徽章 / 假 window 容错）
    B 分组渲染（user 气泡 → 折叠任务卡 → 最终答复 + critical 提示）
    C 聊天页 JS/CSS（归组、卡头、DOM 裁剪、折叠内搜索）
    D ui.py 接线（生命周期、排队、统一转义、行数红线）
    E Agent 补充说明注入通道
"""
import ast
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import chat_web
import ui_task_container_mixin as m

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK]   " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + ("  <- " + str(detail) if detail else ""))


def read(fn):
    with open(os.path.join(ROOT, fn), encoding="utf-8") as fh:
        return fh.read()


def fn_src(src, name):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n) or ""
    return ""


UI_SRC = read("ui.py")
TC_SRC = read("ui_task_container_mixin.py")
CW_SRC = read("chat_web.py")
AG_SRC = read("agent.py")
AUDIT_SRC = read("ui_audit_mixin.py")

print("=== A 纯函数 ===")
check("A1 esc 转义标签与引号",
      "&lt;script&gt;" in m.esc('<script>alert("x")</script>')
      and "&quot;" in m.esc('"'))
check("A2 esc 截断不破转义",
      m.esc("<" * 20, 5).startswith("&lt;" * 5))
check("A3 系统通知整条进记录、无正文",
      m.split_leading_notice("\n\n⏰ 本轮执行超时（600秒），已自动停止。")
      == ("⏰ 本轮执行超时（600秒），已自动停止。", ""))
check("A4 机器小结（\\n\\n 开头）归执行记录",
      m.is_system_notice("\n\n<机器小结文本>")
      and m.split_leading_notice("\n\n小结")[0] == "小结")
_wf = ("⚠ 任务图未完整达成：2 个节点未成功（失败 1 / 跳过 1）\n"
       "以下只是部分产出，未覆盖的环节必须说明，不得当作已全部完成：\n正文结论")
_n, _b = m.split_leading_notice(_wf)
check("A5 任务图未完整前缀从正式回答里剥离",
      _n.startswith("⚠ 任务图未完整达成") and _b == "正文结论")
check("A6 普通回答不被误判",
      m.split_leading_notice("这是正常回答") == ("", "这是正常回答")
      and not m.is_system_notice("这是正常回答"))
check("A7 group_segments 按连续 task_id 切段",
      m.group_segments([{"role": "user"}, {"task_id": "t1", "role": "assistant"},
                        {"task_id": "t1", "role": "tool_log"}, {}])
      == [(None, [0]), ("t1", [1, 2]), (None, [3])])
check("A8 is_formal 只认 user 与 task_final",
      m.is_formal({"role": "user"}) and m.is_formal({"task_final": True})
      and not m.is_formal({"role": "audit_warn"}))
check("A9 stamp 假 window 安全（无活动任务原样返回）",
      m.stamp_task_message(types.SimpleNamespace(), {"role": "assistant"})
      == {"role": "assistant"})
_h = types.SimpleNamespace(_active_task_id="t1", _task_meta={"t1": {}},
                           chat_view=None)
check("A10 stamp 有活动任务才盖章",
      m.stamp_task_message(_h, {"role": "tool_log"}, "tool")
      == {"role": "tool_log", "task_id": "t1", "log_kind": "tool"})
check("A11 set_task_phase 假 window 不抛异常",
      m.set_task_phase(types.SimpleNamespace(), "正在 联网搜索") is False)
check("A12 sync_task_head 假 window 不抛异常",
      m.sync_task_head(types.SimpleNamespace(_active_task_id="t1")) is False)
check("A13 route badge 自动切换文案直观",
      "自动切换" in m.route_badge_copy("task", auto=True)
      and m.route_badge_copy("chat") == "普通回答")
check("A14 DOM 上限与全量重绘同源",
      m.MAX_DOM_NODES == config.MAX_RENDERED_MSGS)

_head = m.task_head_payload({"label": "把报告写成文件", "state": "进行中",
                            "phase": "正在 联网搜索"}, 4)
_box = m.task_box_html("t1", _head, "<div class='task-row'>x</div>")
check("A15 任务卡默认折叠（无 open 属性）",
      "<details class=\"task-box\"" in _box and "open" not in _box.split(">")[0])
check("A16 卡头显示阶段与记录数",
      "正在 联网搜索" in _box and "4 条执行记录" in _box
      and "展开记录" in _box)
check("A17 卡头标题转义（不撑坏 DOM）",
      "&lt;img" in m.task_box_html(
          "t1", m.task_head_payload({"label": "<img src=x>", "state": "进行中"}), ""))
_row = m.task_log_row_html(
    {"role": "tool_log", "name": "<script>", "args": "", "result": "<b>ok</b>"},
    7, "t1", "{}")
check("A18 工具名/结果统一转义 + 顶层带 data-task + 搜索锚点",
      "&lt;script&gt;" in _row and "&lt;b&gt;ok&lt;/b&gt;" in _row
      and 'data-task="t1"' in _row and 'id="msg_7"' in _row)
check("A19 critical 才在回答旁突出，info 级不出提示",
      m.answer_warn_html("t1", 0) == ""
      and "app://taskbox/t1" in m.answer_warn_html("t1", 2, "疑似编造"))
check("A20 warn_is_critical 只看 critical 级",
      m.warn_is_critical({"role": "audit_warn", "warn_severity": "critical"})
      and not m.warn_is_critical({"role": "audit_warn", "warn_severity": "info"}))
check("A21 失败/暂停任务默认展开，成功任务仍折叠",
      " open>" in m.task_box_html("t1", m.task_head_payload({"state": "失败"}), "")
      and " open>" not in m.task_box_html(
          "t1", m.task_head_payload({"state": "已完成"}), ""))

print("\n=== B 分组渲染（真方法 + 轻量宿主）===")


class Host:
    AUDIT_WARN_BG = "rgba(255,180,0,0.12)"
    AUDIT_WARN_BORDER = "#B06000"
    _TOOL_NAME_MAP = {"web_search": "联网搜索", "write_file": "写入文件"}
    _search_query = ""
    _task_meta = {}
    _active_task_id = ""

    _build_parts = m.TaskContainerMixin._build_parts
    _fmt_single_message = m.TaskContainerMixin._fmt_single_message
    _fmt_plain_message = m.TaskContainerMixin._fmt_plain_message
    _fmt_bubble = m.TaskContainerMixin._fmt_bubble
    _wrap_msg = m.TaskContainerMixin._wrap_msg
    _hl = m.TaskContainerMixin._hl
    _task_head_payload = m.TaskContainerMixin._task_head_payload

    def _show_tool_details(self):
        return False


def mk_msgs():
    return [
        {"role": "assistant", "content": "上一轮的普通回答"},
        {"role": "user", "content": "把报告写成文件", "task_id": "t1"},
        {"role": "assistant", "content": "我先搜一下资料", "task_id": "t1"},
        {"role": "tool_log", "name": "web_search", "args": "",
         "result": "找到 3 条", "task_id": "t1", "log_kind": "tool"},
        {"role": "assistant", "content": "⏰ 本轮超时", "task_id": "t1",
         "log_kind": "notice"},
        {"role": "audit_warn", "content": "引用的版本号没找到，疑似编造",
         "task_id": "t1", "warn_severity": "critical", "log_kind": "warn"},
        {"role": "assistant", "content": "报告已写好", "task_id": "t1",
         "task_final": True},
    ]


host = Host()
host._task_meta = {"t1": {"label": "把报告写成文件", "state": "已完成",
                          "phase": "", "count": 4}}
parts = host._build_parts(mk_msgs())
all_html = "".join(parts)
_box_pos = all_html.find('id="taskbox_t1"')
_final_pos = all_html.find('data-md="报告已写好"')
_user_pos = all_html.find('把报告写成文件')
check("B1 顺序：提问 → 任务卡 → 最终答复",
      _user_pos >= 0 and _box_pos > _user_pos and _final_pos > _box_pos,
      (_user_pos, _box_pos, _final_pos))
check("B2 过程助手话/系统通知/工具只在卡内出现一次",
      all_html.count("我先搜一下资料") == 1
      and all_html.count("⏰ 本轮超时") == 1
      and all_html.count("找到 3 条") == 1)
check("B2b critical 警示卡内一条 + 回答旁提示一条（分级生效）",
      all_html.count("引用的版本号没找到") == 2)
check("B3 最终答复仍是正式 Markdown 气泡",
      'data-md="报告已写好"' in all_html and 'class="msg-row ai"' in all_html)
check("B4 critical 警示在回答旁突出显示",
      'class="answer-warn"' in all_html
      and all_html.find('class="answer-warn"') < _final_pos)
check("B5 历史普通消息不受影响",
      'data-md="上一轮的普通回答"' in all_html)
_plain = "".join(host._build_parts([{"role": "user", "content": "你好"},
                                    {"role": "assistant", "content": "你好呀"}]))
check("B6 无 task_id 的普通对话零容器",
      "task-box" not in _plain and 'data-md="你好呀"' in _plain)
check("B7 增量入口把过程消息渲染成可归组条目",
      'data-task="t1"' in host._fmt_single_message(mk_msgs()[2], 2)
      and 'data-task=' not in (host._fmt_single_message(mk_msgs()[6], 6) or ""))
check("B8 tool 断点数据不渲染、不进记录",
      host._fmt_single_message({"role": "tool", "task_id": "t1",
                                "content": "x"}, 3) == "")

print("\n=== C 聊天页 JS/CSS ===")
_html = chat_web._build_html({"bg": "#FFFFFF", "card": "#FFFFFF",
                              "border": "#E5E7EB"})
check("C1 JS 有任务卡归组与卡头更新入口",
      "window.jsSetTaskHead" in _html and "data-task" in _html
      and "makeTaskBox" in _html)
check("C2 增量追加做 DOM 裁剪且阈值来自 config",
      "function trimHistory()" in _html
      and ("var DOM_LIMIT = %d;" % config.MAX_RENDERED_MSGS) in _html
      and "trimHistory();" in _html)
check("C3 折叠提示节点与全量重建同源复用",
      "class=\"trim-note\" data-hidden=" in UI_SRC
      and "querySelector('.trim-note')" in _html)
check("C4 回答旁警示可点击展开执行记录",
      "app://taskbox/" in _html and "box.open = true" in _html)
check("C5 折叠区里的搜索命中会先展开容器",
      "el.closest('details.task-box')" in _html)
check("C6 任务卡 CSS 三态与低层级执行记录",
      ".task-box" in _html and ".task-log" in _html
      and 'data-task-state="已完成"' in _html)
check("C7 ChatWebView 暴露 set_task_head",
      "def set_task_head" in CW_SRC
      and fn_src(CW_SRC, "set_task_head").find("jsSetTaskHead") >= 0)

print("\n=== D ui.py 接线 ===")
_bases = re.search(r"class ChatWindow\(([^)]*)\):", UI_SRC)
check("D1 ChatWindow 继承 TaskContainerMixin",
      _bases and "TaskContainerMixin" in _bases.group(1))
check("D2 ui.py 仍守住 10000 行红线",
      len(UI_SRC.splitlines()) < 10000, len(UI_SRC.splitlines()))
_commit = fn_src(UI_SRC, "_on_stream_commit")
check("D3 流式提交拆分系统通知 / 正式答复",
      "split_leading_notice" in _commit and "stamp_task_message" in _commit)
check("D4 tool_log / audit_warn 都盖章进本轮",
      "stamp_task_message(self," in fn_src(UI_SRC, "_on_tool_log")
      and "_audit_warn_record" in AUDIT_SRC)
check("D5 工具卡唯一转义点（名称/参数/结果）",
      fn_src(UI_SRC, "_tool_card_html").count("html_mod.escape") >= 3)
check("D6 调用方不再自己 escape（避免双重转义）",
      "html_mod.escape" not in fn_src(read("ui_agent_events_mixin.py"),
                                      "_on_tool_finished"))
check("D7 执行中追加消息先交排队/补充条接管",
      "_handle_busy_send" in fn_src(UI_SRC, "send")
      and "_build_pending_bar" in UI_SRC)
check("D8 任务生命周期三点接线齐",
      "_start_task_round" in UI_SRC and "_end_task_round" in UI_SRC
      and "_drain_queued_send" in UI_SRC)
check("D9 模式徽章 + 自动切换反馈（不只靠状态栏）",
      "_build_route_badge" in UI_SRC and "set_route_badge(self, \"task\", auto="
      in UI_SRC)
check("D10 进包清单显式登记新模块",
      "'ui_task_container_mixin'" in read("小臭玩AI.spec"))

print("\n=== E Agent 补充注入 + 排队 ===")
import agent as AG
_w = AG.AgentWorker.__new__(AG.AgentWorker)
_w._pending_notes = []
check("E1 inject_note 受理非空、拒绝空文本",
      _w.inject_note("顺便把图也生成了") is True
      and _w.inject_note("   ") is False
      and _w._pending_notes == ["顺便把图也生成了"])
_w.inject_note("x" * 3000)
check("E2 注入文本有长度上限", len(_w._pending_notes[-1]) == 2000,
      len(_w._pending_notes[-1]))
check("E3 主循环每步开头 drain（整体换 list 防丢）",
      "_pending = getattr(self, \"_pending_notes\", None)" in AG_SRC
      and "self._pending_notes = []" in AG_SRC
      and '【用户在任务执行中补充】' in AG_SRC)
check("E4 补充消息用 role=user 且不标 _internal（模型看得见、不回写气泡）",
      '"role": "user"' in AG_SRC.split("_pending = getattr")[1][:800])


class QHost:
    def __init__(self):
        self._agent_active = True
        self._busy = False
        self._queued_sends = []
        self._pending_send_text = ""
        self.status_label = types.SimpleNamespace(text="", setText=lambda t: None)
        self.input_box = types.SimpleNamespace(
            toPlainText=lambda: "", clear=lambda: None,
            setPlainText=lambda t: None)
        self._pending_bar = types.SimpleNamespace(setVisible=lambda v: None,
                                                  isVisible=lambda: False)
        self._pending_label = types.SimpleNamespace(setText=lambda t: None)

    _on_queue_send = m.TaskContainerMixin._on_queue_send
    _on_supplement_send = m.TaskContainerMixin._on_supplement_send
    _hide_pending_bar = m.TaskContainerMixin._hide_pending_bar
    _drain_queued_send = m.TaskContainerMixin._drain_queued_send
    _send_queued_text = m.TaskContainerMixin._send_queued_text
    _set_task_phase = m.TaskContainerMixin._set_task_phase
    _refresh_tool_progress = lambda self: None
    _save_throttled = lambda self: None
    _on_input_changed = lambda self: None
    send = lambda self: None


q = QHost()
q._pending_send_text = "下一条指令"
q._on_queue_send()
check("E5 排队后消息进队列、输入框清空、不丢",
      q._queued_sends == ["下一条指令"] and q._pending_send_text == "")
q._tool_prog_state = {"total": 0, "done": 0, "running": "", "log": []}
q._agent_worker = types.SimpleNamespace(
    isRunning=lambda: True, inject_note=lambda t: True)
q._pending_send_text = "补充：顺便导出 PDF"
q._on_supplement_send()
check("E6 补充当前任务走注入、不进排队队列",
      q._queued_sends == ["下一条指令"] and "补充" in q._tool_prog_state["log"][0])
q._agent_worker = None
q._pending_send_text = "注入不可用时不能丢"
q._on_supplement_send()
check("E7 注入通道不可用自动降级为排队（绝不静默丢消息）",
      q._queued_sends == ["下一条指令", "注入不可用时不能丢"])

print("\nPASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
