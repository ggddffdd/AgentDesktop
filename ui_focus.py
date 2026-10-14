# -*- coding: utf-8 -*-
"""专注对话的纯逻辑（v4.264.0）。

侧栏分组、人话三档、工具进度文案、产物芯片、确认白话都放这里，
方便不拉起 Qt 就能测。控件接线在 ui_focus_host.FocusChromeMixin，
不改 PermissionEngine.decide 的规则顺序。
"""
import os
import re
from urllib.parse import quote

# 配置键：缺省即关。开了才把工具卡刷进聊天气泡。
SHOW_TOOL_DETAILS_KEY = "show_tool_details_in_chat"

# 顶栏「⋯」里的项（会话管理留在顶栏，不进这里）。
CHAT_OVERFLOW_LABELS = (
    "朗读回复",
    "技能管理",
    "技能市场",
    "工作流",
    "导出对话",
)

# 欢迎卡标题 → 仍可深链的导航名。文件对账没有专用页，不在表里。
WELCOME_PAGE_BY_TITLE = {
    "生成配图": "生图",
    "数字人视频": "数字人",
    "军团编排": "军团",
}

PERSONA_CHAT = "chat"
PERSONA_ASSIST = "assist"
PERSONA_OWN = "own"

# (值, 按钮文案, 一句说明)
PERSONA_CHOICES = (
    (PERSONA_CHAT, "只聊天", "不调用工具，只回答。"),
    (PERSONA_ASSIST, "助手做事（推荐）", "需要时才动手，危险操作会先问你。"),
    (PERSONA_OWN, "尽量自己做", "能做的尽量自己做；装技能、删数据仍会问。"),
)

_ARTIFACT_NAME_HINTS = ("write_file", "image_gen", "screenshot", "save_file",
                        "image", "write", "file", "video")
_PATH_RE = re.compile(
    r"(?P<p>"
    r"(?:[A-Za-z]:[\\/]|/)[^\s\"'<>|*?\n]{1,240}"
    r"\.(?:png|jpe?g|webp|gif|mp4|mov|webm|txt|md|docx|pdf|py|json|html|csv|xlsx|wav|zip)"
    r"|[\w./\\-]{1,240}"
    r"\.(?:png|jpe?g|webp|gif|mp4|mov|webm|txt|md|docx|pdf|json|html|csv|xlsx)"
    r")",
    re.I,
)


def show_tool_details(cfg):
    """在对话里显示工具细节。缺键、空配置都算关。"""
    if not isinstance(cfg, dict):
        return False
    return bool(cfg.get(SHOW_TOOL_DETAILS_KEY, False))


def state_from_persona(tier):
    """人话档 → (agent_mode, permission_mode)。不认识的档回落到推荐档。"""
    if tier == PERSONA_CHAT:
        return False, "discuss"
    if tier == PERSONA_OWN:
        return True, "auto"
    return False, "interactive"


def persona_from_state(agent_mode, permission_mode):
    """现有两个开关 → 人话档。对不上三档时返回空串（界面展示「高级」）。"""
    agent_on = bool(agent_mode)
    mode = permission_mode or ""
    if mode == "discuss" and not agent_on:
        return PERSONA_CHAT
    if mode == "auto" and agent_on:
        return PERSONA_OWN
    if mode == "interactive" and not agent_on:
        return PERSONA_ASSIST
    return ""


def persona_label(tier):
    for key, label, _hint in PERSONA_CHOICES:
        if key == tier:
            return label
    return "高级"


def persona_status(tier):
    label = persona_label(tier)
    for key, _label, hint in PERSONA_CHOICES:
        if key == tier:
            return "模式：%s · %s" % (label, hint)
    return "模式：高级（五档仍生效）"


def progress_caption(running_name, done, total):
    """输入框上方单行。running_name 为空表示这一轮没有进行中的工具。"""
    done = int(done or 0)
    total = int(total or 0)
    if running_name:
        cur = done + 1
        denom = max(total, cur)
        return "正在：%s (%d/%d)" % (running_name, cur, denom)
    if total > 0 and done >= total:
        return "做完了 · %d 步" % done
    if total > 0:
        return "正在：处理中 (%d/%d)" % (max(done, 1), total)
    return ""


def extract_artifact_paths(name, args, result):
    """从工具参数/结果里抽出看起来像文件的路径。非产物类工具返回空。"""
    name = str(name or "")
    if name and not any(h in name for h in _ARTIFACT_NAME_HINTS):
        return []
    blob_parts = []
    if isinstance(args, dict):
        for key in ("path", "file_path", "output", "save_path", "filename"):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                blob_parts.append(val.strip())
    elif args:
        blob_parts.append(str(args))
    if result:
        blob_parts.append(str(result))
    found = []
    for blob in blob_parts:
        for match in _PATH_RE.finditer(blob):
            path = match.group("p").rstrip(".,;，。)")
            if path and path not in found:
                found.append(path)
            if len(found) >= 4:
                return found
    return found


def chip_html(paths):
    """聊天气泡末尾的「打开 / 在文件夹显示」。href 走 app://，由聊天页拦截。"""
    items = [p for p in (paths or []) if p]
    if not items:
        return ""
    parts = ['<div class="actions artifact-chips">']
    for path in items[:4]:
        enc = quote(str(path), safe="")
        base = os.path.basename(str(path)) or str(path)
        # 标签里只放文件名，避免把整段路径塞进气泡
        safe = (base.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))
        parts.append(
            '<a href="app://openfile/%s">打开 %s</a>'
            '<a href="app://reveal/%s">在文件夹显示</a>' % (enc, safe, enc)
        )
    parts.append("</div>")
    return "".join(parts)


def confirm_copy(title, detail, force=False):
    """确认框白话。force 时写明「少问也挡不住这一步」。"""
    title = (title or "请确认这一步").strip()
    detail = (detail or "").strip()
    if force:
        note = "这一步不可跳过：即使本会话已经少问，仍要你点一次允许。"
    else:
        note = "允许后才会做。勾选「少问」后，输入区会出现徽章，点徽章可以收回。"
    if note not in detail:
        detail = (detail + "\n\n" + note).strip()
    return title, detail


def resolve_artifact(path, roots):
    """把相对路径对着若干根目录试一遍。找不到也返回一个绝对路径，方便报错。"""
    raw = os.path.expanduser(str(path or "").strip())
    if not raw:
        return ""
    if os.path.isabs(raw) and os.path.exists(raw):
        return os.path.abspath(raw)
    for root in roots or []:
        if not root:
            continue
        cand = raw if os.path.isabs(raw) else os.path.join(root, raw)
        if os.path.exists(cand):
            return os.path.abspath(cand)
    if os.path.isabs(raw):
        return os.path.abspath(raw)
    base = next((r for r in (roots or []) if r), os.getcwd())
    return os.path.abspath(os.path.join(base, raw))


def reveal_target(path):
    """(要打开的文件夹, Windows 下用来 /select 的文件或 None)。"""
    path = os.path.abspath(path)
    if os.path.isdir(path):
        return path, None
    folder = os.path.dirname(path) or path
    return folder, path
