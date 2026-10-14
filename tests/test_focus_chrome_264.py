# -*- coding: utf-8 -*-
"""v4.264.0 专注对话：侧栏收进「更多」、顶栏溢出菜单、人话三档、
工具条默认不刷卡、产物芯片、确认文案可收回。

纯函数走 ui_focus（不拉 Qt）。控件接线在 ui_focus_host.FocusChromeMixin，
ChatWindow 必须继承它；侧栏/顶栏/确认框仍在 ui.py / ui_widgets.py。
只改路径、不断言继承，等于把判据改松了。
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        extra = ("  <- " + str(detail)) if detail else ""
        print("  [FAIL] " + name + extra)


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def _fn_src(src, tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def _const(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        return None
    return None


ui_src = _read("ui.py")
ui_tree = ast.parse(ui_src)
wg_src = _read("ui_widgets.py")
wg_tree = ast.parse(wg_src)
try:
    host_src = _read("ui_focus_host.py")
    host_tree = ast.parse(host_src)
except OSError:
    host_src = ""
    host_tree = ast.parse("")


def _chat_bases():
    for node in ast.walk(ui_tree):
        if isinstance(node, ast.ClassDef) and node.name == "ChatWindow":
            return [ast.get_source_segment(ui_src, b) or "" for b in node.bases]
    return []

import ui_focus as focus  # noqa: E402

print("-- 侧栏：对话露出，其余进更多 --")
groups = _const(ui_tree, "NAV_GROUPS")
more = _const(ui_tree, "NAV_MORE_GROUPS")
nav = _const(ui_tree, "nav_defs")
check("F1 NAV_GROUPS 第一组只含「对话」",
      isinstance(groups, tuple) and groups and groups[0][0] == "对话"
      and tuple(groups[0][1]) == ("对话",),
      groups[0] if groups else None)
check("F2 协作/创作/系统收进 NAV_MORE_GROUPS",
      tuple(more or ()) == ("协作", "创作", "系统"), more)
flat = []
if groups:
    for _g, members in groups:
        flat.extend(members)
nav_labels = [item[0] for item in (nav or []) if item]
check("F3 分组展平仍等于 nav_defs（下标契约）",
      flat == nav_labels, "flat=%s nav=%s" % (flat, nav_labels))
check("F4 深链仍走 _switch_nav",
      "def _open_nav_label" in ui_src and "def _switch_nav" in ui_src)

print("-- 顶栏：技能/市场/工作流/导出进溢出菜单 --")
for _attr in ("skill_mgr_btn", "market_btn", "wf_mgr_btn", "export_btn"):
    check("F5 顶栏不再挂上 %s" % _attr,
          ("hb.addWidget(self.%s)" % _attr) not in ui_src)
overflow = _fn_src(ui_src, ui_tree, "_open_chat_overflow")
check("F6 溢出菜单含五项文案",
      all(label in overflow for label in focus.CHAT_OVERFLOW_LABELS),
      overflow[:80])
check("F7 会话管理仍留在顶栏",
      "hb.addWidget(mgr_btn)" in ui_src and "会话管理" in ui_src)

print("-- 人话三档映射（不改引擎规则，只映射现有两个开关） --")
check("F8 只聊天 → agent 关 + discuss",
      focus.state_from_persona("chat") == (False, "discuss"))
check("F9 助手做事 → agent 开 + interactive（能调工具，危险操作仍问）",
      focus.state_from_persona("assist") == (True, "interactive"))
check("F10 尽量自己做 → agent 开 + auto",
      focus.state_from_persona("own") == (True, "auto"))
check("F11 interactive + agent 开映回「助手做事」",
      focus.persona_from_state(True, "interactive") == "assist")
check("F12 只聊天 / 尽量自己做 往返",
      focus.persona_from_state(False, "discuss") == "chat"
      and focus.persona_from_state(True, "auto") == "own")
check("F13 对不上的组合落成高级（空串，不是瞎猜一档）",
      focus.persona_from_state(True, "plan") == ""
      and focus.persona_from_state(False, "auto") == ""
      and focus.persona_from_state(False, "interactive") == "")
check("F14 不认识的档名回落到推荐档，而不是抛错",
      focus.state_from_persona("nope") == (True, "interactive"))
apply = _fn_src(host_src, host_tree, "_apply_persona")
check("F15 _apply_persona 写回 agent_mode 与 permission_mode",
      "agent_mode" in apply and "set_mode" in apply and "permission_mode" in apply
      and "def _apply_persona" not in ui_src
      and "FocusChromeMixin" in _chat_bases())
send = _fn_src(ui_src, ui_tree, "send")
check("F16 只聊天（discuss）不再因关键词自动进 Agent",
      'permission_mode' in send and '"discuss"' in send)

print("-- 旧档「agent 关 + interactive」只升一次，不再冒充助手做事 --")
_reconcile = getattr(focus, "reconcile_persona_cfg", None)
_legacy = {"agent_mode": False, "permission_mode": "interactive"}
_legacy_ok = False
if _reconcile is not None:
    _legacy_ok = (
        _reconcile(_legacy) is True
        and _legacy.get("agent_mode") is True
        and _legacy.get("permission_mode") == "interactive"
        and focus.persona_from_state(True, "interactive") == "assist")
check("F37 旧档助手做事升到 agent 开 + interactive",
      _legacy_ok, _legacy)
_again = {"agent_mode": False, "permission_mode": "interactive",
          "_mig_v4273_persona_assist": True}
check("F38 升过之后在高级里关掉 Agent 不会被写回去",
      _reconcile is not None and _reconcile(_again) is False
      and _again.get("agent_mode") is False, _again)
_chat_cfg = {"agent_mode": False, "permission_mode": "discuss"}
_chat_ok = False
if _reconcile is not None:
    _chat_ok = (_reconcile(_chat_cfg) is True
                and _chat_cfg.get("agent_mode") is False
                and _chat_cfg.get("permission_mode") == "discuss"
                and _chat_cfg.get("_mig_v4273_persona_assist") is True
                and _reconcile(_chat_cfg) is False)
check("F39 只聊天不被升成助手做事，且只记一次", _chat_ok, _chat_cfg)
_own_cfg = {"agent_mode": True, "permission_mode": "auto"}
_blank = {}
_skip = {"agent_mode": False, "permission_mode": "interactive",
         "agent_skip_confirm": True}
if _reconcile is not None:
    _reconcile(_own_cfg)
    _reconcile(_blank)
    _reconcile(_skip)
check("F40 尽量自己做、缺省、免确认旧档各走各的",
      _reconcile is not None
      and _own_cfg.get("agent_mode") is True
      and _own_cfg.get("permission_mode") == "auto"
      and _blank.get("agent_mode") is True
      and _blank.get("permission_mode") == "interactive"
      and _skip.get("agent_mode") is False
      and _skip.get("permission_mode") == "interactive"
      and _reconcile(None) is False,
      (_own_cfg, _blank, _skip))


def _method_src(src, tree, class_name, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return ast.get_source_segment(src, item) or ""
    return ""


_init = _method_src(ui_src, ui_tree, "ChatWindow", "__init__")
_rec_at = _init.find("reconcile_persona_cfg")
_agent_at = _init.find("self.agent_mode")
check("F41 启动时先对齐旧档再读 agent_mode，并对齐结果落盘",
      _rec_at >= 0 and _agent_at > _rec_at and "_save_cfg" in _init[_rec_at:_agent_at],
      _init[_rec_at:_rec_at + 180] if _rec_at >= 0 else "未调用 reconcile_persona_cfg")
_input_area = _fn_src(ui_src, ui_tree, "_build_input_area")
check("F42 输入区人话档按当前状态对齐，不总停在第一项「只聊天」",
      "_sync_persona_widgets()" in _input_area)

print("-- 设置信息卡「Agent 模式」跟着开关变 --")
_sync = _fn_src(host_src, host_tree, "_sync_persona_widgets")
_refresh = _fn_src(host_src, host_tree, "_refresh_agent_mode_card")
_toggle = _fn_src(ui_src, ui_tree, "_on_agent_toggle")
check("F43 信息卡记住 Agent 值标签，同步和 Agent 开关都会改它",
      'hold="_agent_mode_value"' in ui_src
      and "_refresh_agent_mode_card" in _sync
      and "_agent_mode_value" in _refresh
      and "setText" in _refresh
      and "_refresh_agent_mode_card" in _toggle,
      "refresh=%s" % (_refresh[-160:] if _refresh else "缺失"))


class _CardLabel:
    def __init__(self, text):
        self._t = text

    def setText(self, text):
        self._t = str(text)

    def text(self):
        return self._t


class _Mode:
    def __init__(self, mode):
        self.mode = mode


def _card_host(agent_on, mode, shown):
    from ui_focus_host import FocusChromeMixin
    host = FocusChromeMixin()
    host.agent_mode = agent_on
    host.permission_engine = _Mode(mode)
    host._agent_mode_value = _CardLabel(shown)
    host.persona_combo = None
    host.settings_persona_combo = None
    return host


_card = _card_host(True, "interactive", "关")
_card._sync_persona_widgets()
check("F44 选助手做事后信息卡从「关」变成「开」",
      _card._agent_mode_value.text() == "开"
      and getattr(_card, "persona_combo", None) is None,
      _card._agent_mode_value.text())
_card.agent_mode = False
_card.permission_engine.mode = "discuss"
_card._refresh_agent_mode_card()
check("F45 关掉 Agent 后信息卡回到「关」（不是恒写成开）",
      _card._agent_mode_value.text() == "关",
      _card._agent_mode_value.text())

print("-- 工具条默认不刷卡 --")
check("F17 缺省配置不在对话显示工具细节",
      focus.show_tool_details(None) is False
      and focus.show_tool_details({}) is False
      and focus.show_tool_details({"show_tool_details_in_chat": False}) is False)
check("F18 显式打开才为真（反向：不是恒假）",
      focus.show_tool_details({"show_tool_details_in_chat": True}) is True)
check("F19 进度文案带正在与计数",
      focus.progress_caption("写入文件", 1, 4) == "正在：写入文件 (2/4)")
check("F20 全部完成是一句摘要",
      focus.progress_caption("", 3, 3) == "做完了 · 3 步")
started = _fn_src(ui_src, ui_tree, "_on_tool_started")
check("F21 started 先问开关再决定要不要刷工具卡",
      "_show_tool_details" in started and "chat_view.append" in started)
check("F22 输入区有单行进度条，刷新方法在 mixin",
      "_tool_progress" in ui_src
      and "def _refresh_tool_progress" in host_src
      and "def _refresh_tool_progress" not in ui_src
      and "FocusChromeMixin" in _chat_bases())

print("-- 产物芯片 --")
paths = focus.extract_artifact_paths(
    "write_file", {"path": "notes/今日.md"}, "已写入 notes/今日.md")
check("F23 write_file 能抽出路径", paths == ["notes/今日.md"] or "notes/今日.md" in paths,
      paths)
check("F24 联网搜索不产生产物芯片",
      focus.extract_artifact_paths("web_search", {"q": "x"}, "没有文件") == [])
html = focus.chip_html(["产物/a.png"])
check("F25 芯片含打开与在文件夹显示",
      "打开" in html and "在文件夹显示" in html and "app://openfile/" in html
      and "app://reveal/" in html)
check("F26 空路径没有芯片（反向：不是恒输出按钮）", focus.chip_html([]) == "")
fmt = _fn_src(ui_src, ui_tree, "_fmt_single_message")
check("F27 消息渲染会拼芯片", "chip_html" in fmt or "artifact-chips" in fmt)
check("F28 交付物卡片有打开/在文件夹显示，打开方法在 mixin",
      "在文件夹显示" in ui_src and "def _open_artifact" in host_src
      and "def _open_artifact" not in ui_src
      and "FocusChromeMixin" in _chat_bases())

print("-- 少打断：确认白话 + 成功回执改 toast --")
title, body = focus.confirm_copy("确认写入文件", "路径：a.txt", force=False)
check("F29 普通确认写明可以收回", "收回" in body and title)
_ft, fbody = focus.confirm_copy("确认执行命令", "rm", force=True)
check("F30 硬确认写明少问也挡不住", "不可跳过" in fbody)
cdlg = _fn_src(wg_src, wg_tree, "_build")
# ConfirmDialog._build 与别的 _build 重名，取含信任文案的那一段
trust_hits = [ast.get_source_segment(wg_src, n) or ""
              for n in ast.walk(wg_tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_build"]
trust_src = next((s for s in trust_hits if "信任" in s or "少问" in s), "")
check("F31 确认框勾选写成可收回的少问",
      "少问" in trust_src and "收回" in trust_src, trust_src[:120])
check("F32 确认框能装一批条目",
      "def set_batch" in wg_src)
backup = _fn_src(ui_src, ui_tree, "_run_backup_now")
check("F33 立即备份的成功回执走 toast",
      "toast(" in backup and "QMessageBox.information" not in backup)
upd = _fn_src(ui_src, ui_tree, "_check_update")
check("F34 无更新通道的说明走 toast",
      "toast(" in upd and "QMessageBox.information" not in upd)
perf = _fn_src(ui_src, ui_tree, "_on_perf_done")
check("F35 性能基线长报告仍用信息框（不是一句 toast 塞得下的）",
      "QMessageBox.information" in perf)
spec = _read("小臭玩AI.spec")
check("F36 进包清单登记 ui_focus 与 ui_focus_host",
      "'ui_focus'" in spec and "'ui_focus_host'" in spec)

print()
print("PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
