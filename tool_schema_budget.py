# -*- coding: utf-8 -*-
"""工具 schema 预算：默认只发固定的核心段，其余用到再接到后面。

DeepSeek 按请求开头做缓存。核心段的内容和顺序必须每次一样，
按需补上的工具只能接在这段后面，不能插进中间。

全量目录才裁。导演、军团、自动化传进来的本来就是小清单，原样全发。
工具结果不参与点名：sys_info 会把整份名单吐回来，扫它等于下一轮又全发。
"""

import json

import prompt_cache

REQUEST_TOOLS_NAME = "request_tools"

# 按代码里的实际调用挑的 25 个：聊天、搜索、读写文件、记忆、跑代码、生图。
# 顺序就是名字序，和 order_tool_schemas 对这 25 个的结果一致。不要改顺序。
CORE_TOOL_NAMES = (
    "browser_open",
    "browser_read",
    "chart_gen",
    "create_automation",
    "db_insert",
    "db_query",
    "image_gen",
    "list_automation",
    "log_query",
    "rag_index",
    "rag_search",
    "read_file",
    "read_xlsx_json",
    "remember",
    "run_command",
    "run_python",
    "schedule",
    "screenshot",
    "search_memory",
    "sys_info",
    "use_skill",
    "video_gen",
    "web_fetch",
    "web_search",
    "write_file",
)

_CORE_SET = frozenset(CORE_TOOL_NAMES)

# 这几族同时出现，才像「把整份目录发出去」。
# 自动化的受限清单有导演和军团，但没有鼠标和 webhook，不会被裁。
_FAMILY_PREFIXES = ("mouse_", "director_", "legion_", "webhook_", "app_")
_BUDGET_MIN_FAMILIES = 4
_BUDGET_MIN_TOOLS = 60

# 关键词只扫用户正文。值是前缀（以 _ 结尾）或确切工具名。
_FAMILY_ROUTES = (
    (("导演", "分镜", "关键帧", "成片", "运镜"), ("director_",)),
    (("军团", "任务板", "成员产出"), ("legion_",)),
    (("webhook", "回调地址", "回调接口"), ("webhook_",)),
    (("鼠标", "滚动鼠标"), ("mouse_",)),
    (("键盘输入", "按键"), ("keyboard_",)),
    (("剪贴板", "剪切板"), ("clipboard_",)),
    (("窗口列表", "有哪些窗口", "聚焦窗口", "切换窗口"), ("window_",)),
    (("打开应用", "启动应用", "启动软件", "关闭应用", "关闭软件", "关掉软件",
      "点击控件", "点击按钮", "输入文本", "控件树", "窗口最大化", "窗口最小化"),
     ("app_",)),
    (("网页点击", "浏览器点击", "浏览器里点", "填表", "填写表单"), ("browser_",)),
    (("装技能", "安装技能", "搜索技能", "技能市场"), ("skill_",)),
    (("创建技能", "新建技能"), ("create_skill",)),
    (("删除定时任务", "删掉定时任务", "取消定时任务", "删除自动化"),
     ("delete_automation",)),
    (("更新数据库", "修改记录", "删除记录", "删掉记录"), ("db_",)),
    (("工作流", "子代理"), ("run_workflow",)),
    (("压缩上下文", "上下文摘要"), ("context_",)),
    (("分析图片", "看图", "识别图片"), ("analyze_image",)),
    (("结束进程", "杀掉进程", "终止进程"), ("process_kill",)),
    (("进程列表", "有哪些进程"), ("process_list",)),
    (("启动进程",), ("process_start",)),
)

_REQUEST_DESCRIPTION = (
    "当前列表里没有你需要的工具时，先调用它要来完整定义，下一轮再真正调用那个工具。"
    "names 填工具名，和系统提示里的工具概览一致，可以多个。"
    "不要用它完成任务本身，也不要编造还没下发的工具参数。"
)


def _tool_name(tool):
    if not isinstance(tool, dict):
        return ""
    fn = tool.get("function")
    if isinstance(fn, dict) and fn.get("name"):
        return str(fn.get("name"))
    if tool.get("name"):
        return str(tool.get("name"))
    return ""


def catalog_names(tools):
    names = []
    seen = set()
    for tool in tools or []:
        name = _tool_name(tool)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _wants_full_schema(cfg):
    if not isinstance(cfg, dict):
        return False
    value = cfg.get("full_tool_schema", False)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _looks_like_full_catalog(names):
    if len(names) < _BUDGET_MIN_TOOLS:
        return False
    families = 0
    for prefix in _FAMILY_PREFIXES:
        for name in names:
            if name.startswith(prefix):
                families += 1
                break
    return families >= _BUDGET_MIN_FAMILIES


def _index(tools):
    by_name = {}
    for tool in tools or []:
        name = _tool_name(tool)
        if name and name not in by_name:
            by_name[name] = tool
    return by_name


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits = []
        for part in content:
            if isinstance(part, str):
                bits.append(part)
            elif isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                bits.append(str(part.get("text") or ""))
        return "\n".join(bits)
    return ""


def _has_image(content):
    if not isinstance(content, list):
        return False
    for part in content:
        if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
            return True
    return False


def _message_parts(messages):
    user_chunks = []
    want_image = False
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if _has_image(content):
            want_image = True
        role = message.get("role")
        if role == "user":
            text = _content_text(content)
            if text:
                user_chunks.append(text)
    return user_chunks, want_image


def _mentions(text, name):
    if not text or not name:
        return False
    start = 0
    while True:
        index = text.find(name, start)
        if index < 0:
            return False
        before = text[index - 1] if index else ""
        after_at = index + len(name)
        after = text[after_at] if after_at < len(text) else ""
        before_ok = (not before) or not (before.isalnum() or before == "_")
        after_ok = (not after) or not (after.isalnum() or after == "_")
        if before_ok and after_ok:
            return True
        start = after_at
    return False


def _intent_route_names(text):
    try:
        import intent
        return [name for name, _kw in intent._matched_routes(text or "")]
    except Exception:
        return []


def _family_names(text, catalog):
    if not text:
        return []
    low = text.lower()
    found = []
    for keys, targets in _FAMILY_ROUTES:
        if not any(key.lower() in low for key in keys):
            continue
        for target in targets:
            if target.endswith("_"):
                for name in catalog:
                    if name.startswith(target):
                        found.append(name)
            elif target in catalog:
                found.append(target)
    return found


def _literal_names(text, catalog):
    if not text:
        return []
    return [name for name in catalog if _mentions(text, name)]


def _names_from_args(args):
    if not isinstance(args, dict):
        return []
    raw = args.get("names")
    if raw is None:
        raw = args.get("tools")
    if raw is None and isinstance(args.get("name"), str):
        raw = [args.get("name")]
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace("，", ",").split(",")]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def _requested_from_messages(messages):
    found = []
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            if not isinstance(fn, dict) or fn.get("name") != REQUEST_TOOLS_NAME:
                continue
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except Exception:
                    args = {}
            if isinstance(args, dict):
                found.extend(_names_from_args(args))
    return found


def _extra_names(catalog, texts, requested, force_tool, want_image):
    user_blob = "\n".join(t for t in (texts or []) if t)
    found = set()
    found.update(_intent_route_names(user_blob))
    found.update(_family_names(user_blob, catalog))
    found.update(_literal_names(user_blob, catalog))
    for name in requested or ():
        found.add(name)
    if force_tool:
        found.add(force_tool)
    if want_image:
        found.add("analyze_image")
    return sorted(
        name for name in found
        if name in catalog and name not in _CORE_SET and name != REQUEST_TOOLS_NAME
    )


def _request_schema():
    return {
        "type": "function",
        "function": {
            "name": REQUEST_TOOLS_NAME,
            "description": _REQUEST_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要加入的工具名",
                    }
                },
                "required": ["names"],
            },
        },
    }


def select_tool_schemas(tools, cfg=None, texts=(), requested=(),
                        force_tool=None, want_image=False):
    """决定这一轮发给模型的工具 schema。

    full_tool_schema 为真，或这份清单不像全量目录时，整表按名字排好返回。
    否则：核心段（固定顺序）+ request_tools + 按需工具（按名字排）。
    """
    if not isinstance(tools, list):
        return tools
    indexed = catalog_names(tools)
    if _wants_full_schema(cfg) or not _looks_like_full_catalog(indexed):
        return prompt_cache.order_tool_schemas(tools)
    by_name = _index(tools)
    core = [by_name[name] for name in CORE_TOOL_NAMES if name in by_name]
    extras = _extra_names(set(indexed), texts, requested, force_tool, want_image)
    out = list(core)
    out.append(_request_schema())
    for name in extras:
        out.append(by_name[name])
    return out


def select_for_call(tools, cfg, messages, force_tool=None):
    """从这一轮消息里看出要补哪些工具。系统提示和工具结果不参与。"""
    texts, want_image = _message_parts(messages)
    requested = _requested_from_messages(messages)
    return select_tool_schemas(
        tools, cfg=cfg, texts=texts, requested=requested,
        force_tool=force_tool or None, want_image=want_image)


def request_tools_result(args, catalog_names_):
    """给模型的回执：下一轮会带上哪些 schema。不执行业务。"""
    catalog = set(catalog_names_ or [])
    wanted = _names_from_args(args)
    if not wanted:
        return "没有给出工具名。请在 names 里填写工具概览中的名字，下一轮再调用。"
    known = []
    unknown = []
    seen = set()
    for name in wanted:
        if name in seen:
            continue
        seen.add(name)
        if name in catalog:
            known.append(name)
        else:
            unknown.append(name)
    parts = []
    if known:
        parts.append(
            "下一轮会带上这些工具的完整定义：%s。请到时再调用，不要在本轮编造参数。"
            % "、".join(known))
    if unknown:
        parts.append("这些名字不在当前可用工具里：%s。" % "、".join(unknown))
    return "".join(parts)


def split_schema_requests(tool_calls, catalog_names_):
    """把 request_tools 从一批调用里拆出来。返回 (其余调用, [(id, 回执), ...])。"""
    kept = []
    replies = []
    for call in tool_calls or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict) or fn.get("name") != REQUEST_TOOLS_NAME:
            kept.append(call)
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}
        text = request_tools_result(args, catalog_names_)
        tc_id = call.get("id", "") if isinstance(call, dict) else ""
        replies.append((tc_id, text))
    return kept, replies
