# -*- coding: utf-8 -*-
"""v4.227 P2-1：不可信内容边界（判定 + 包装 + 防伪造闭合）。

v4.222 建了这套边界，但有两个洞，本轮补上：

**洞一：清单是 8 项枚举，新工具不会自动被覆盖。**
`_UNTRUSTED_TOOLS` 是写死的 frozenset。83 个已注册工具里，
`browser_read`（读网页正文）、`legion_*`（子代理产出，里面可能转述网页）、
`webhook_events`（外部 POST 进来的载荷）、`clipboard_read`、`app_get_text`
（读别的应用的界面文字）全都不在清单里 —— 每一个都是外部可控内容。
枚举的失效方式很安静：将来再加 10 个浏览器工具，没人会记得回来补这个集合。

修法：**规则驱动**，两层 ——
  · `_UNTRUSTED_PREFIXES`：按工具名前缀。浏览器/军团是成族新增的，前缀一劳永逸。
  · `_UNTRUSTED_EXACT`：不成族的零散工具，逐个点名并注明为什么它产出不可信内容。
判定真源只有 `is_untrusted_tool()` 一个函数；包装一律经它，不允许别处再抄一份清单。

**洞二：固定标签可被内容自己闭合（越狱）。**
标签形如 `<untrusted_tool_output ...>...</untrusted_tool_output>`。
如果被读的网页正文里**自己写了** `</untrusted_tool_output>`，
后面那段就落在边界之外，模型会当成可信上下文 —— 攻击者等于自己给自己解除了边界。
（这不是理论问题：网页正文完全由攻击者控制。）

修法：**包装前中和内容里的伪标签**。所有 `<untrusted` / `</untrusted`
开头的标签在内容内部一律把 `<` 转成 `&lt;`，变成惰性文本，
不再能被解析成边界标记。同时闭合标签那一路也要管 ——
`</untrusted skill>` 同理，`wrap_skill_prompt` 一并走同一条中和。

**刻意保留的边界（不是漏项）**：
  · `director_*`（11 个）—— 改的是用户自己的剧本/分镜，来源是用户不是外部。
  · `db_insert/update/delete`、`clipboard_write` —— 写操作，返回值是本工具自己的回执。
  · `sys_info`/`process_list`/`window_list` 等 —— 返回系统事实，攻击者写不进去。
「登记了却从不改变结论」是失效的开始，所以 `is_untrusted_tool` 既是判定真源，
也是唯一需要维护的地方 —— 判据直接钉它。
"""
import re

# ============================================================
# 一、判定：谁产出的是不可信内容
# ============================================================

# 按前缀。成族新增的工具（浏览器、军团子代理）靠这一层自动覆盖。
_UNTRUSTED_PREFIXES = (
    "browser_",     # 浏览器族：正文/标题/输入框内容全来自网页
    "legion_",      # 军团子代理产出：里面可能转述它读到的网页
)

# 逐个点名。每条都要能答出「攻击者从哪控制得了这段内容」。
_UNTRUSTED_EXACT = frozenset({
    # —— 网络/检索 ——
    "web_fetch", "web_search", "search", "browse",
    "rag_search",
    "download_file",     # 下载来的文件内容

    # —— 本地文件（用户或第三方写的都算）——
    "read_file", "read_file_text",

    # —— 剪贴板：来源完全不可知（网页复制、别的应用写入）——
    "clipboard_read",

    # —— 别的应用的界面文字：那个应用可能正显示着攻击者的网页 ——
    "app_get_text", "app_list_controls",

    # —— 数据库存的内容：入库路径多，来源不可追 ——
    "db_query",

    # —— 外部 webhook 载荷：任何人 POST 进来的都是攻击面 ——
    "webhook_events",
})


def is_untrusted_tool(name):
    """该工具的产出是否是不可信外部内容（判定真源，全项目只此一处）。"""
    n = str(name or "").strip()
    if not n:
        return False
    if any(n.startswith(p) for p in _UNTRUSTED_PREFIXES):
        return True
    return n in _UNTRUSTED_EXACT


def untrusted_tool_report():
    """返回 (已判定不可信的工具名, 判定依据)。给判据与排障用。"""
    out = []
    for n in sorted(_UNTRUSTED_EXACT):
        out.append((n, "exact"))
    for p in _UNTRUSTED_PREFIXES:
        out.append((p + "*", "prefix"))
    return out


# ============================================================
# 二、中和：不让内容自己闭合边界
# ============================================================

# 内容里任何 `<untrusted` / `</untrusted` 开头的标签一律钝化。
# 只吃这两个前缀 —— 不碰内容里其它正常 HTML（比如网页正文里的 <div>）。
#
# 收紧的边界（v4.227 判据 B3 逼出来的）：`<untrusted` 之后必须紧跟
# 一个**不可能属于普通英文单词**的分隔符。
#
# 这里踩过一次坑：两个真实标签是 `<untrusted_tool_output>` 与
# `<untrusted skill=...>` —— 前者 `untrusted` 后面紧跟的是**下划线** `_`。
# 所以分隔符集合必须含 `_`，否则正则收紧过头，真标签反而 neutralize 不到
# （判据 A6/A7/B2 会一起红）。而 `<untrustedx>` 的 `x` 是字母，不在集合里，
# 仍被正确放过 —— 这才是要的「精确到不误伤普通词」。
_NEUTRALIZE_RE = re.compile(
    r"<(\s*/?\s*untrusted(?=[\s/>\"'_]|\Z))", re.IGNORECASE)


def neutralize_forged_tags(text):
    """把内容里伪造的边界标签钝化，返回 (处理后的文本, 命中次数)。

    `<untrusted` → `&lt;untrusted`：模型看到的是一段惰性文本，
    不是边界标记，也就没法把自己的边界提前关掉。
    命中次数返回给调用方做留痕 —— 「有内容在试图越狱」这件事不该只有包装器知道。
    """
    if text is None:
        return "", 0
    if not isinstance(text, str):
        text = str(text)
    n = 0

    def _rep(m):
        nonlocal n
        n += 1
        return "&lt;" + m.group(1)

    return _NEUTRALIZE_RE.sub(_rep, text), n


# ============================================================
# 三、包装
# ============================================================

_TOOL_OPEN = '<untrusted_tool_output source="%s"%s>'
_TOOL_CLOSE = "</untrusted_tool_output>"
_SKILL_OPEN = '<untrusted skill="%s">'
_SKILL_CLOSE = "</untrusted skill>"


def wrap_untrusted(content, source, evidence_id=None):
    """把不可信来源内容包进显式边界，模型须当作数据而非指令。

    包装前先中和内容里的伪标签（见 `neutralize_forged_tags`）——
    不中和的话，攻击者控制的正文可以自己写闭合标签越狱。
    """
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = str(content)
    content, forged = neutralize_forged_tags(content)
    if forged:
        try:
            import logging
            logging.getLogger("dsdesktop").warning(
                "不可信内容(%s)内含 %d 处伪造边界标签，已中和", source, forged)
        except Exception:
            pass
    cid = (' evidence_id="%s"' % evidence_id) if evidence_id else ""
    return (_TOOL_OPEN % (source, cid)) + "\n" + content + "\n" + _TOOL_CLOSE


def wrap_tool_content(name, content, evidence_id=None):
    """不可信工具产出包边界；可信工具原样返回。

    一律经 `is_untrusted_tool` 判定 —— 不在本模块外另抄清单。
    """
    if is_untrusted_tool(name):
        return wrap_untrusted(content, name, evidence_id)
    return content


# 旧名保留：v4.222 起 agent.py / agent_result_mixin.py 都按这个名字调用。
_wrap_tool_content = wrap_tool_content


def wrap_skill_prompt_text(text, name):
    """技能指令包进不可信边界（与工具产出同一条中和逻辑）。

    技能文件同样是外部可写的（用户装了别人写的技能），照样能伪造闭合标签。
    """
    if not text:
        return text
    text, forged = neutralize_forged_tags(text)
    if forged:
        try:
            import logging
            logging.getLogger("dsdesktop").warning(
                "技能(%s)内含 %d 处伪造边界标签，已中和", name, forged)
        except Exception:
            pass
    return (_SKILL_OPEN % name) + "\n" + text + "\n" + _SKILL_CLOSE
