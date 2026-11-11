# -*- coding: utf-8 -*-
"""ui_task_container_mixin.py —— 一轮 Agent 任务的「可折叠任务容器」（v4.291.0）

背景（2026-11-11 大哥的第二轮指摘 + 补充纠偏）：
  工具卡片其实已经默认关闭、输入框上方也已有单行进度，所以「工具卡太多」
  **不是**默认状态下视觉混乱的主因。真正的病是：

    工具前后的助手说明、超时/预算/失败通知、机器小结、自动核验警示、
    以及最终答复，全都以普通气泡形式混排在同一条时间线里。

  用户看到的不是「一轮任务」，而是十几条平级消息，正式回答被过程噪音淹没。

本模块做的事（不新增消息 role，只在 dict 上打标记，历史会话天然兼容）：
  · 一轮 Agent 任务 = 一个 task_id；段内 user 提问 + 最终答复仍是正式气泡；
  · 段内其余消息（过程助手话、系统通知、机器小结、核验提示、工具记录）
    收进聊天流里一枚默认折叠的 <details> 任务卡，展开才看「执行记录」；
  · 卡头显示状态 / 当前阶段 / 正在做什么 / 记录条数；
  · 只有 critical 级核验警示才在最终答复旁突出一行，其余留在执行记录里；
  · 执行中追加消息不再被一句「稍候再发」吞掉：可排队发送，或补充当前任务；
  · 工具卡名称/参数/结果在这里统一转义，杜绝结果文本撑坏 DOM。

纯函数集中在模块级（无 Qt 依赖，可单测）；Qt 侧接线放 TaskContainerMixin。
气泡格式化族（_fmt_bubble / _hl / _wrap_msg / _extract_text /
_fmt_single_message / _build_parts）从 ui.py 整体搬来，净减 ui.py 行数，
守住 ui.py < 10000 的规模红线。
"""
import html as html_mod
import json
import re
import uuid

import ui_focus
from theme_tokens import THEME

# ---- 消息标记（附加字段，不动原有 role/content，历史消息无字段即走旧渲染）----
TASK_KEY = "task_id"          # 所属任务轮次
FINAL_KEY = "task_final"      # 本轮最终答复（只有它能留在正式气泡层）
KIND_KEY = "log_kind"         # notice / tool / warn / note / progress
SEVERITY_KEY = "warn_severity"  # critical = 确实影响结论，回答旁要突出

# 与 config.MAX_RENDERED_MSGS 同口径：增量追加也不能让 DOM 无限长
try:
    from config import MAX_RENDERED_MSGS as MAX_DOM_NODES
except Exception:                      # pragma: no cover（冻结环境兜底）
    MAX_DOM_NODES = 300

# agent._finalize_workflow 会把「未完整达成」前缀拼进返回文本开头；
# 这里拆成执行记录条目，剩下的正文才配当正式答复。
_LEAD_WF_WARN = re.compile(r"^(⚠️?\s*任务图未完整达成[^\n]*\n[^\n]*\n)")


def esc(s, limit=0):
    """统一 HTML 转义。工具名/参数/结果/警告正文一律过这里。"""
    t = str(s if s is not None else "")
    if limit and len(t) > limit:
        t = t[:limit] + "…"
    return html_mod.escape(t, quote=True)


def is_system_notice(text):
    """agent 侧「系统生成」的文本：\n\n 开头的通知/机器小结、任务图未完整前缀。"""
    t = str(text or "")
    return t.startswith("\n\n") or bool(_LEAD_WF_WARN.match(t))


def split_leading_notice(text):
    """→ (应进执行记录的系统文本, 可能是正式答复的正文)。

    · "\\n\\n⚠️ 模型调用失败…" / "\\n\\n<机器小结>" → 整条进记录，无正文；
    · "⚠ 任务图未完整达成…\\n以下只是部分产出…\\n<正文>" → 拆前两行进记录；
    · 其余正常回答 → 全部当正文。
    """
    t = str(text or "")
    if not t:
        return "", ""
    if t.startswith("\n\n"):
        return t.strip(), ""
    m = _LEAD_WF_WARN.match(t)
    if m:
        return m.group(1).strip(), t[m.end():].strip()
    return "", t


def stamp_task_message(host, rec, kind=None):
    """模块级盖章：不依赖 host 是否继承了 mixin（审计/隔离单测常用假 window）。

    原地改 dict 并返回；没有活动任务时原样返回，普通对话与历史消息零影响。
    """
    tid = getattr(host, "_active_task_id", "")
    if tid:
        rec[TASK_KEY] = tid
        if kind:
            rec[KIND_KEY] = kind
    return rec


def is_formal(m):
    """段内只有 user 提问与带 task_final 的最终答复留在正式气泡层。"""
    return m.get("role") == "user" or bool(m.get(FINAL_KEY))


def group_segments(msgs):
    """按连续相同 task_id 切段 → [(tid|None, [下标...]), ...]。"""
    segs, cur_tid, cur = [], None, []
    for i, m in enumerate(msgs):
        tid = (m.get(TASK_KEY) if isinstance(m, dict) else None) or None
        if tid != cur_tid:
            if cur:
                segs.append((cur_tid, cur))
            cur_tid, cur = tid, []
        cur.append(i)
    if cur:
        segs.append((cur_tid, cur))
    return segs


def warn_is_critical(m):
    return (m.get("role") == "audit_warn"
            and m.get(SEVERITY_KEY) == "critical")


def log_kind_of(m):
    role = m.get("role")
    if role == "audit_warn":
        return "critical" if warn_is_critical(m) else "warn"
    if role == "tool_log":
        return "tool"
    if role == "task_note":
        return "note"
    kind = m.get(KIND_KEY)
    if kind:
        return kind
    return "notice" if is_system_notice(m.get("content", "")) else "progress"


def brief_label(text, limit=32):
    t = " ".join(str(text or "").split())
    return (t[:limit] + "…") if len(t) > limit else (t or "执行任务")


def task_head_payload(meta, count=0):
    meta = meta or {}
    return {
        "label": brief_label(meta.get("label", "")),
        "state": meta.get("state") or "已完成",
        "phase": meta.get("phase") or "",
        "count": int(count or 0),
    }


def task_box_html(tid, head, rows):
    """聊天流里的任务卡。<details> 不带 open = 默认只露一行简短进度。"""
    state = head.get("state") or "已完成"
    icon = ("…" if state == "进行中"
            else "!" if state in ("失败", "已暂停") else "✓")
    phase = head.get("phase") or ""
    summary_phase = esc((phase + " · ") if phase else "")
    # 成功任务默认只露一行；失败/暂停直接展开，失败原因不该被藏在折叠里
    open_attr = " open" if state in ("失败", "已暂停") else ""
    return (
        f'<details class="task-box" id="taskbox_{esc(tid)}" '
        f'data-task-state="{esc(state)}"{open_attr}>'
        '<summary class="task-head">'
        f'<span class="task-state">{icon}</span>'
        f'<span class="task-title">{esc(head.get("label") or "执行任务")}</span>'
        f'<span class="task-phase">{summary_phase}{int(head.get("count") or 0)} 条执行记录</span>'
        '<span class="task-toggle">展开记录</span>'
        '</summary>'
        f'<div class="task-log">{rows}</div>'
        '</details>'
    )


def task_log_row_html(m, idx, tid, head_json, tool_names=None, chips_html=None):
    """一条执行记录。所有外部文本过 esc()，顶层带 data-task 供增量归组。"""
    kind = log_kind_of(m)
    role = m.get("role")
    chips = ""
    if role == "tool_log":
        name = str(m.get("name") or "工具")
        label = (tool_names or {}).get(name, name)
        result = str(m.get("result") or "")
        body = esc(label, 40) + ("：" + esc(result, 160) if result else "")
        chips = chips_html if chips_html is not None else ui_focus.chip_html(
            ui_focus.extract_artifact_paths(name, m.get("args", ""), result))
        tag = "工具"
    elif role == "audit_warn":
        body = esc(m.get("content"), 500)
        tag = "核验"
    elif role == "task_note":
        body = esc(m.get("content"), 400)
        tag = "补充"
    else:
        body = esc(m.get("content"), 600).replace("\n", "<br>")
        tag = "系统" if kind == "notice" else "过程"
    anchor = "" if idx is None else f'<a name="msg_{idx}"></a>'
    id_attr = "" if idx is None else f'id="msg_{idx}" '
    esc_head = esc(head_json, 2000)
    return (
        f'<div class="task-row tr-{kind}" {id_attr}data-task="{esc(tid)}" '
        f'data-task-head="{esc_head}">{anchor}'
        f'<span class="tr-tag">{tag}</span>'
        f'<span class="tr-text">{body}</span>{chips}</div>'
    )


def answer_warn_html(tid, critical_count, sample=""):
    """确实影响结论的核验警示 → 回答旁突出一行（点击跳执行记录）。"""
    if not critical_count:
        return ""
    detail = esc(sample, 90)
    tail = ("　" + detail) if detail else ""
    return (
        f'<div class="answer-warn" id="answer-warn-{esc(tid)}">'
        f'注意：本回答有 {int(critical_count)} 条自动核验提示可能影响结论{tail}'
        f'<a href="app://taskbox/{esc(tid)}">查看执行记录</a></div>'
    )


def route_badge_copy(mode, auto=False, preview=False):
    """输入区模式徽章文案：明确「这条消息将怎么处理」。"""
    if mode == "task":
        return ("执行任务 · 已自动切换" if auto
                else "执行任务" if not preview
                else "这条会当成执行任务")
    return "普通回答"


def sync_task_head(host):
    """把卡头状态推给页面；容器不存在时先创建（模块级，假 window 安全）。"""
    tid = (getattr(host, "_active_task_id", "")
           or getattr(host, "_last_task_id", ""))
    cv = getattr(host, "chat_view", None)
    if not tid or cv is None or not hasattr(cv, "set_task_head"):
        return False
    try:
        meta = getattr(host, "_task_meta", None) or {}
        payload = task_head_payload(meta.get(tid, {}),
                                    meta.get(tid, {}).get("count", 0))
        cv.set_task_head(tid, json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False


def set_task_phase(host, phase, running_name=""):
    """任务卡头「正在做什么」。没有活动任务时静默跳过。"""
    tid = getattr(host, "_active_task_id", "")
    if not tid:
        return False
    meta = getattr(host, "_task_meta", None) or {}
    if tid in meta:
        meta[tid]["phase"] = brief_label(phase, 48)
        meta[tid]["running"] = running_name
        sync_task_head(host)
    return True


def set_route_badge(host, mode, auto=False, preview=False):
    """输入区徽章。控件未建好/假 window 时静默跳过，绝不阻断发送。"""
    lbl = getattr(host, "_route_badge", None)
    if lbl is None:
        return False
    try:
        from theme_tokens import THEME as _T
        lbl.setText(route_badge_copy(mode, auto=auto, preview=preview))
        if mode == "task":
            lbl.setStyleSheet(
                f"QLabel{{background:{_T['card_blue_bg']};color:{_T['accent']};"
                f"border:1px solid {_T['accent']};border-radius:12px;"
                f"font-size:{_T['font_micro']};padding:3px 10px;}}")
        else:
            lbl.setStyleSheet(
                f"QLabel{{background:{_T['panel2']};color:{_T['dim']};"
                f"border:1px solid {_T['border']};border-radius:12px;"
                f"font-size:{_T['font_micro']};padding:3px 10px;}}")
        return True
    except Exception:
        return False


class TaskContainerMixin:
    # ==================== 气泡格式化族（从 ui.py 搬来，行为不变） ====================
    def _fmt_bubble(self, role, text, idx=None):
        """v4.104：flex 布局气泡；assistant 正文走 data-md 由 marked.js 渲染。"""
        if role == "user":
            esc_text = html_mod.escape(str(text)).replace("\n", "<br>")
            esc_text = re.sub(
                r'(https?://[^\s\u4e00-\u9fff，。、；：！？（）()【】<>"]+)',
                r'<a href="\1" target="_blank">\1</a>', esc_text)
            if not esc_text.strip():
                esc_text = "…"
            actions = ""
            if idx is not None:
                actions = '<div class="actions"><a href="app://copy/%d">复制</a></div>' % idx
            return (
                '<div class="msg-row user">'
                '<div class="col"><div class="bubble">{esc}</div>{actions}</div>'
                '</div>'
            ).format(esc=esc_text, actions=actions)
        raw = text if (text and str(text).strip()) else "…"
        md_attr = html_mod.escape(str(raw), quote=True)
        actions = ""
        if idx is not None:
            actions = (
                '<div class="actions">'
                f'<a href="app://copy/{idx}">复制</a>'
                f'<a href="app://regen/{idx}">重新生成</a>'
                f'<a href="app://edit/{idx}">改写问题</a>'
                '<span class="sep">|</span>'
                f'<a href="app://fb_up/{idx}">▲</a>'
                f'<a href="app://fb_down/{idx}">▼</a>'
                '</div>'
            )
        return (
            '<div class="msg-row ai">'
            '<div class="col">'
            f'<div class="bubble"><div class="md" data-md="{md_attr}"></div></div>'
            f'{actions}'
            '</div></div>'
        )

    def _hl(self, escaped):
        """对话内搜索高亮：只在标签外可见文本上包 <mark>。"""
        q = getattr(self, "_search_query", "")
        if not q:
            return escaped
        try:
            qe = re.escape(html_mod.escape(q))
        except Exception:
            return escaped
        if not qe:
            return escaped
        parts = re.split(r'(<[^>]+>)', escaped)
        for i in range(0, len(parts), 2):
            parts[i] = re.sub(
                qe,
                f'<span style="background-color:{THEME["tpl_hl_bg"]};color:#000;'
                r'border-radius:6px;padding:0 4px;">\g<0></span>',
                parts[i], flags=re.IGNORECASE)
        return "".join(parts)

    def _wrap_msg(self, bubble, idx):
        if idx is None:
            return bubble
        return f'<div id="msg_{idx}"><a name="msg_{idx}"></a>{bubble}</div>'

    def _extract_text(self, m):
        c = m.get("content", "")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "".join(p.get("text", "") for p in c
                           if isinstance(p, dict) and p.get("type") == "text")
        return ""

    def _fmt_plain_message(self, m, idx=None):
        """原 _fmt_single_message 的逻辑（不带分组），供正式消息/历史消息复用。"""
        if m.get("_internal"):
            return ""
        if m.get("role") == "tool":
            return ""
        if m.get("role") == "audit_warn":
            bubble = ('<div class="tool-wrap">'
                      f'<div style="background:{self.AUDIT_WARN_BG};'
                      f'border-left:3px solid {self.AUDIT_WARN_BORDER};border-radius:8px;'
                      'padding:8px 12px;font-size:12px;">'
                      + html_mod.escape(str(m.get("content", ""))) + "</div></div>")
            return self._wrap_msg(bubble, idx)
        if m.get("role") == "tool_log":
            name = m.get("name", "")
            result = m.get("result", "")
            chips = ui_focus.chip_html(ui_focus.extract_artifact_paths(
                name, m.get("args", ""), result))
            import os
            if name == "image_gen" and re.search(r"\.(png|jpe?g|webp|gif)$", result, re.I):
                from config import APP_DIR
                img_abs = os.path.join(APP_DIR, result).replace("/", os.sep)
                if os.path.isfile(img_abs):
                    bubble = (
                        '<div class="tool-wrap">'
                        f'<img src="file:///{img_abs.replace(os.sep, "/")}" '
                        f'style="max-width:320px;border-radius:10px;"/>'
                        f'<div style="font-size:{THEME["font_micro"]};color:{THEME["faint"]};margin-top:4px;">'
                        f'{html_mod.escape(result)}</div>{chips}</div>'
                    )
                    return self._wrap_msg(bubble, idx)
            if not self._show_tool_details():
                if chips:
                    return self._wrap_msg(
                        f'<div class="tool-wrap artifact-chips">{chips}</div>', idx)
                return ""
            import tools as tools_mod
            card = tools_mod.card_html(name, m.get("args", ""), result)
            bubble = f'<div class="tool-wrap">{card}{chips}</div>'
            return self._wrap_msg(bubble, idx)
        else:
            raw = m.get("content", "")
            if isinstance(raw, list):
                mm_parts = []
                for part in raw:
                    if isinstance(part, dict) and part.get("type") == "text":
                        t = part.get("text", "")
                        if t:
                            mm_parts.append(html_mod.escape(t).replace("\n", "<br>"))
                    elif isinstance(part, dict) and part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url:
                            mm_parts.append(
                                '<br><img src="' + html_mod.escape(url) + '" '
                                'style="max-width:300px;border-radius:10px;margin:8px 0;"><br>'
                            )
                inner = "".join(mm_parts) if mm_parts else "[图片消息]"
                side = "user" if m.get("role") == "user" else "ai"
                bubble = (
                    f'<div class="msg-row {side}">'
                    '<div class="col">'
                    f'<div class="bubble">{inner}</div></div>'
                    '</div>'
                )
                return self._wrap_msg(bubble, idx)
            if (m.get("role") == "assistant"
                    and not raw
                    and not m.get("tool_calls")):
                return ""
            return self._wrap_msg(self._fmt_bubble(m.get("role"), raw, idx), idx)

    def _fmt_single_message(self, m, idx=None):
        """增量渲染入口：带 task_id 且非最终答复 → 折进任务容器的执行记录。"""
        tid = (m.get(TASK_KEY) if isinstance(m, dict) else None) or None
        if tid and not is_formal(m) and m.get("role") != "tool":
            head = json.dumps(self._task_head_payload(tid), ensure_ascii=False)
            # 产物芯片由 chip_html 统一生成，折叠区里也能点开文件。
            chips = ui_focus.chip_html(ui_focus.extract_artifact_paths(
                m.get("name", ""), m.get("args", ""), m.get("result", "")))
            return task_log_row_html(m, idx, tid, head,
                                     getattr(self, "_TOOL_NAME_MAP", None),
                                     chips_html=chips)
        return self._fmt_plain_message(m, idx)

    def _build_parts(self, msgs, start_idx=0):
        """全量重建：按 task_id 分组，过程消息收进折叠任务卡。"""
        parts = []
        for tid, idxs in group_segments(msgs):
            if not tid:
                for i in idxs:
                    bubble = self._fmt_plain_message(msgs[i], i + start_idx)
                    if bubble:
                        parts.append(bubble)
                continue
            logs = [i for i in idxs
                    if not is_formal(msgs[i]) and msgs[i].get("role") != "tool"]
            critical = [msgs[i] for i in logs if warn_is_critical(msgs[i])]
            head = self._task_head_payload(tid, len(logs))
            head_json = json.dumps(head, ensure_ascii=False)
            rows = "".join(
                task_log_row_html(msgs[i], i + start_idx, tid, head_json,
                                  getattr(self, "_TOOL_NAME_MAP", None))
                for i in logs)
            box = task_box_html(tid, head, rows) if logs else ""
            box_done = False
            for i in idxs:
                m = msgs[i]
                # 容器恒定在最终答复之前：user 提问是正式层，日志不能挤到答复后面。
                if box and not box_done and (not is_formal(m)
                                            or m.get("role") != "user"):
                    box_done = True
                    parts.append(box)
                if is_formal(m):
                    bubble = self._fmt_plain_message(m, i + start_idx)
                    if bubble:
                        if m.get(FINAL_KEY) and critical:
                            parts.append(answer_warn_html(
                                tid, len(critical),
                                str(critical[0].get("content", ""))))
                        parts.append(bubble)
            if box and not box_done:
                parts.append(box)
        return parts

    # ==================== 任务轮次生命周期 ====================
    def _task_head_payload(self, tid, count=None):
        meta = getattr(self, "_task_meta", None) or {}
        if count is None:
            count = meta.get(tid, {}).get("count", 0)
        return task_head_payload(meta.get(tid, {}), count)

    def _start_task_round(self, session, label=""):
        """在 agent worker 启动前给本轮 user 消息盖章并生成任务卡。"""
        tid = "task-" + uuid.uuid4().hex[:10]
        self._active_task_id = tid
        meta = getattr(self, "_task_meta", None)
        if meta is None:
            meta = self._task_meta = {}
        meta[tid] = {"label": brief_label(label), "state": "进行中",
                     "phase": "准备中", "count": 0}
        try:
            msgs = session.messages
            for m in reversed(msgs):
                if m.get("role") == "user" and not m.get("_internal"):
                    m[TASK_KEY] = tid
                    break
        except Exception:
            pass
        self._sync_task_head()
        return tid

    def _stamp_task_message(self, rec, kind=None):
        """本轮产生的所有会话消息统一盖章（无 tid 时原样返回，历史/普通对话不受影响）。"""
        return stamp_task_message(self, rec, kind)

    def _set_task_phase(self, phase, running_name=""):
        return set_task_phase(self, phase, running_name)

    def _sync_task_head(self):
        """把卡头状态推给页面：容器不存在时先创建，运行中持续更新阶段。"""
        return sync_task_head(self)

    def _end_task_round(self, session=None, ok=True, reason=""):
        """任务收口：挑出最终答复、更新卡头状态、触发一次全量重建。"""
        tid = getattr(self, "_active_task_id", "")
        if not tid:
            return
        meta = getattr(self, "_task_meta", None) or {}
        state = ("已完成" if ok else "已暂停" if "暂停" in (reason or "")
                 else "失败")
        if tid in meta:
            meta[tid]["state"] = state
            meta[tid]["phase"] = brief_label(reason, 48) if reason else state
        self._last_task_id = tid
        self._active_task_id = ""
        try:
            sess = session if session is not None else self.store.active()
            msgs = sess.messages
            cands = [m for m in msgs
                     if m.get(TASK_KEY) == tid
                     and m.get("role") == "assistant"
                     and m.get(KIND_KEY) != "notice"
                     and str(m.get("content") or "").strip()]
            if cands:
                cands[-1][FINAL_KEY] = True
            meta_tid = meta.get(tid)
            if meta_tid is not None:
                meta_tid["count"] = sum(
                    1 for m in msgs if m.get(TASK_KEY) == tid
                    and not is_formal(m) and m.get("role") != "tool")
        except Exception:
            pass
        self._sync_task_head()
        # 最终答复要脱离折叠区浮到正式气泡层 → 必须重摆一次 DOM。
        # 此刻 _agent_active 已 False，live 工具卡无需恢复，重建是安全的。
        self._rendered_msg_count = 0

    # ==================== 输入区：模式徽章 ====================
    def _build_route_badge(self, row):
        from PySide6.QtWidgets import QLabel
        self._route_badge = QLabel(route_badge_copy("chat"))
        self._route_badge.setStyleSheet(
            f"QLabel{{background:{THEME['panel2']};color:{THEME['dim']};"
            f"border:1px solid {THEME['border']};border-radius:12px;"
            f"font-size:{THEME['font_micro']};padding:3px 10px;}}")
        self._route_badge.setToolTip(
            "普通回答 = 直接聊天，不调用工具；执行任务 = 会调工具、有任务卡和执行记录")
        row.addWidget(self._route_badge, 0)

    def _set_route_badge(self, mode, auto=False, preview=False):
        return set_route_badge(self, mode, auto=auto, preview=preview)

    # ==================== 执行中追加消息 ====================
    def _build_pending_bar(self, layout):
        """长任务期间的三选一条：排队发送 / 补充当前任务 / 取消。"""
        from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel,
                                       QPushButton)
        from PySide6.QtCore import Qt
        self._pending_bar = QFrame()
        self._pending_bar.setVisible(False)
        self._pending_bar.setStyleSheet(
            f"QFrame{{background:{THEME['card_blue_bg']};"
            f"border:1px solid {THEME['accent']};border-radius:10px;}}")
        lay = QHBoxLayout(self._pending_bar)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(8)
        self._pending_label = QLabel("当前任务还在跑，这条消息要：")
        self._pending_label.setStyleSheet(
            f"QLabel{{background:transparent;color:{THEME['text']};"
            f"font-size:{THEME['font_micro']};}}")
        lay.addWidget(self._pending_label, 1)
        btn_style = (
            f"QPushButton{{background:transparent;color:{THEME['accent']};"
            f"border:1px solid {THEME['accent']};border-radius:12px;"
            f"font-size:{THEME['font_micro']};padding:4px 10px;}}"
            f"QPushButton:hover{{background:{THEME['card']};}}")
        for text, slot in (("排队发送", self._on_queue_send),
                           ("补充当前任务", self._on_supplement_send),
                           ("取消", self._on_cancel_pending)):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(btn_style)
            b.clicked.connect(slot)
            lay.addWidget(b, 0)
        layout.addWidget(self._pending_bar)

    def _handle_busy_send(self):
        """send() 在 _busy 时先问这里。返回 True = 已接管，send 直接返回。"""
        if not getattr(self, "_agent_active", False):
            return False
        text = self.input_box.toPlainText().strip()
        if not text:
            return False
        bar = getattr(self, "_pending_bar", None)
        if bar is None:
            return False
        self._pending_send_text = text
        self._pending_label.setText(
            "当前任务还在跑，「%s」要：" % brief_label(text, 22))
        bar.setVisible(True)
        self.status_label.setText("这条消息先放一放：排队发送，或补进当前任务")
        return True

    def _hide_pending_bar(self, clear_input=False):
        bar = getattr(self, "_pending_bar", None)
        if bar is not None:
            bar.setVisible(False)
        self._pending_send_text = ""
        if clear_input:
            self.input_box.clear()
            self._on_input_changed()

    def _on_queue_send(self):
        text = getattr(self, "_pending_send_text", "") or ""
        if not text:
            self._hide_pending_bar()
            return
        queue = getattr(self, "_queued_sends", None)
        if queue is None:
            queue = self._queued_sends = []
        queue.append(text)
        self._hide_pending_bar(clear_input=True)
        self.status_label.setText(
            f"已排队 {len(queue)} 条，当前任务结束后自动发送")
        self._save_throttled()

    def _on_supplement_send(self):
        text = getattr(self, "_pending_send_text", "") or ""
        if not text:
            self._hide_pending_bar()
            return
        worker = getattr(self, "_agent_worker", None)
        injected = False
        try:
            running = getattr(worker, "isRunning", lambda: True)()
            if (worker is not None and running
                    and hasattr(worker, "inject_note")):
                injected = bool(worker.inject_note(text))
        except Exception:
            injected = False
        self._hide_pending_bar(clear_input=True)
        if injected:
            self._set_task_phase("已收到补充：" + brief_label(text, 28))
            st = getattr(self, "_tool_prog_state", None)
            if isinstance(st, dict):
                st.setdefault("log", []).append("＋ 补充：" + brief_label(text, 40))
                st["log"] = st["log"][-8:]
                self._refresh_tool_progress()
            self.status_label.setText("补充已加入当前任务，下一步生效")
        else:
            # 注入通道不可用 → 退化为排队，绝不静默丢消息
            queue = getattr(self, "_queued_sends", None)
            if queue is None:
                queue = self._queued_sends = []
            queue.append(text)
            self.status_label.setText("当前任务无法补充，已改为排队发送")

    def _on_cancel_pending(self):
        self._hide_pending_bar()
        self.status_label.setText("已取消，消息仍留在输入框")

    def _drain_queued_send(self):
        """任务结束后把排队消息送出去（单条、延迟一拍，避免重入 send）。"""
        queue = getattr(self, "_queued_sends", None) or []
        if not queue or getattr(self, "_busy", False):
            return
        text = queue.pop(0)
        if not text:
            return
        from PySide6.QtCore import QTimer
        QTimer.singleShot(120, lambda t=text: self._send_queued_text(t))

    def _send_queued_text(self, text):
        if getattr(self, "_busy", False):
            # 又忙起来了 → 放回队首，等下一次收口
            self._queued_sends.insert(0, text)
            return
        self.input_box.setPlainText(text)
        self.send()
        self.status_label.setText("已发送排队消息")


__all__ = [
    "TaskContainerMixin", "esc", "is_system_notice", "split_leading_notice",
    "is_formal", "group_segments", "task_box_html", "task_log_row_html",
    "answer_warn_html", "route_badge_copy", "MAX_DOM_NODES",
]
