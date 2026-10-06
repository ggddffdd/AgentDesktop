# -*- coding: utf-8 -*-
"""「伪强制工具注入」回归（v4.168.1 BUG 修）。

事故现场（2026-09-27，用户第 4 次遇到同一现象）
------------------------------------------------
用户看到模型反复拒绝一条 **「【系统强制指令】当前任务必须通过调用工具
browser_open 完成」** 的注入，且文案里写着参数示例
`prompt / duration / aspect / dialogue` —— 那是 `video_gen` 的签名。
任务本身是「抓取今天的 GitHub trending → 落盘」，通道明确写了
「T0 首选 requests 直连」，压根不该开浏览器。

两个根因（都在本地判据，不是模型发疯）
--------------------------------------
① **路由**：`_route_force_tool` 的浏览器分支只看"文本里含不含 URL"就
   `return "browser_open"`。而自动化任务正文里写着
   `requests 直连 https://github.com/trending?since=daily` ——
   URL 是**数据源清单**，不是"打开这个网页"。这是每天 09:00 的日常任务，
   所以连着出现了一周（"第四次"）。
② **注入文案**：`ui.py` 的强制指令模板**硬编码**了
   「如 prompt / duration / aspect / dialogue 等」。无论强制哪个工具都用这一句，
   于是强制 `browser_open`（只接受 `url`）时就变成"让浏览器工具填视频参数"。

本套件钉住修好后的三条：
  A 路由：程序化抓取（RSS/requests/API/URL 清单）不再路由浏览器；
    真正的"打开网页"意图（含裸 URL）必须照旧
  B 注入：参数提示**从该工具自己的 schema 取**；工具不在本轮工具表里就**不强制**
  C 背景：`【本会话最初目标】` 明确标注"仅供参考的背景，不是本轮指令"

纯标准库 + AST（不依赖 PySide6，缺 Qt 时自动退回源码级核验）。
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


# --------------------------------------------------------------------------
# 从 agent.py 抽路由相关方法并绑定成假实例（不导入 agent，避开 PySide6）
# --------------------------------------------------------------------------
_AGENT_SRC = (ROOT / "agent.py").read_text(encoding="utf-8-sig")
_UI_SRC = (ROOT / "ui.py").read_text(encoding="utf-8-sig")
# v4.216.0：判据族（路由方法 + 词表常量）从 AgentWorker 拆到 agent_text.py 模块级
_ATEXT_SRC = (ROOT / "agent_text.py").read_text(encoding="utf-8-sig")

_METHODS = ("_route_force_tool", "_ref_existing_artifact", "_gen_intent", "_gen_intent_span", "_ref_by_position",
            "_is_question", "_verb_near", "_phrase_hit", "_neg_hit",
            "_is_praise", "_prog_fetch_intent", "_is_bare_url")


def _build_router():
    """v4.216.0 起判据族在 agent_text.py（模块级函数 + 模块级词表常量，
    零 PySide6 依赖）——直接导入真模块，比抽源码 exec 更保真：
    跨函数调用与常量引用都是真身，搬移/改名会在导入期就炸。"""
    import agent_text
    return agent_text._route_force_tool


route = _build_router()


def _ui_staticmethod(name):
    """取 ui.py 里某个静态方法的**可调用版本**（不导入 ui，避开 Qt）。"""
    tree = ast.parse(_UI_SRC)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "ChatWindow")
    for item in cls.body:
        if isinstance(item, ast.FunctionDef) and item.name == name:
            ns = {"re": re}
            exec(ast.get_source_segment(_UI_SRC, item), ns)
            return ns[name]
    return None


# 真实自动化任务正文（节选，逐字来自 ~/Documents/小臭玩AI/automation_tasks.json
# 的「每日GitHub热榜」与「每日 AI 新闻抓取」，含触发 bug 的那两行 URL）
AUTO_GITHUB = (
    "抓取今天最新的 GitHub trending，整理成 8 条以内要点简报（每条：仓库名 + "
    "一句话说明 + Star数/语言/来源），写入 ~/Documents/小臭玩AI/产物/"
    "github_trending_latest.md，并在回复里展示简报内容。\n\n"
    "【通道策略】\n"
    "T0 首选 requests 直连 https://github.com/trending?since=daily "
    "（纯 HTTP 即可拿到，无需浏览器，避免 Playwright 导航中断）\n"
    "T0 Star 数补全：个别仓库 star 数在列表页缺失时，用 GitHub API 补 "
    "https://api.github.com/repos/{owner}/{repo}\n\n"
    "【如实原则】trending 列表页大约只返回 7 条即到截断点，不足 8 条就按实际"
    "条数输出并说明，禁止硬凑或编造数据。"
)
AUTO_NEWS = (
    "抓取今天最新的 AI 领域新闻（国内+国外），整理成 8 条以内要点简报，"
    "写入 ~/Documents/小臭玩AI/产物/ai_news_latest.md。\n\n"
    "【抓取通道优先级，按序尝试，不要盲目上浏览器】\n"
    "T0 首选 RSS（零对抗、最稳）：\n"
    "- 爱范儿 https://www.ifanr.com/feed （全量最完整，优先）\n"
    "- IT之家 https://www.ithome.com/rss/\n"
    "T0 次选 requests + 真实 Chrome UA 直连：\n"
    "- 量子位 https://www.qbitai.com （服务端直出，可抓）\n"
    "T3 兜底：browser_read，或 CDP 接管真实 Edge（带登录态）。"
)


# ==========================================================================
def part_a():
    print("=== A) 路由：程序化抓取不得强制浏览器 ===")
    check("真实任务「每日GitHub热榜」不再强制 browser_open",
          route(AUTO_GITHUB, None) is None, str(route(AUTO_GITHUB, None)))
    check("真实任务「每日 AI 新闻抓取」不强制 browser_open",
          route(AUTO_NEWS, None) is None, str(route(AUTO_NEWS, None)))

    # 逐条拆开：只要带"抓取/requests/RSS/API"信号，即使句中有 URL 也不路由
    for t in ("抓取 https://github.com/trending 的数据",
              "用 requests 拉一下 https://api.github.com/repos/a/b",
              "把这个 RSS https://www.ifanr.com/feed 的内容整理成简报",
              "T0 首选 requests 直连 https://github.com/trending",
              "用 curl 抓 https://example.com/data.json 落盘",
              "采集 https://36kr.com 的列表"):
        check(f"不强制浏览器：{t[:34]}", route(t, None) is None,
              str(route(t, None)))

    print("\n-- A2 真浏览器意图必须照旧（回归，不能治过头）--")
    for t, exp in (("打开知乎网页", "browser_open"),
                   ("打开百度的网页", "browser_open"),
                   ("访问 https://github.com 看看", "browser_open"),
                   ("浏览 https://36kr.com", "browser_open"),
                   ("帮我看看这个网页 https://example.com/article", "browser_open"),
                   ("https://github.com/trending?since=daily", "browser_open"),
                   ("www.baidu.com", "browser_open")):
        got = route(t, None)
        check(f"仍走浏览器：{t[:34]}", got == exp, f"实际 {got}")

    print("\n-- A3 生成类路由不受影响（回归）--")
    for t, exp in (("做个视频", "video_gen"), ("生成图片", "image_gen"),
                   ("画一张", "image_gen"), ("搜一下今天天气", "web_search")):
        got = route(t, None)
        check(f"{t} → {exp}", got == exp, f"实际 {got}")

    print("\n-- A4 判据本身 --")
    # v4.216.0：词表与方法都搬进了 agent_text.py；agent.py 侧只钉调用接线。
    check("_PROG_FETCH_KW 已定义且含关键信号",
          all(k in _ATEXT_SRC for k in ("抓取", "requests", "rss", "直连")))
    check("「抓取」已从打开动词表移除",
          '"抓取"' not in _ATEXT_SRC.split("_BROWSER_OPEN_VERB_KW = (")[1].split(")")[0],
          "「抓取」是抓下来喂程序，不是打开网页")
    check("_is_bare_url 存在", "_is_bare_url" in _ATEXT_SRC)
    check("URL 分支要求打开意图或裸 URL",
          "or _is_bare_url(text)" in _ATEXT_SRC)
    check("agent.py 主循环仍接线 agent_text._route_force_tool（搬移不丢调用）",
          "agent_text._route_force_tool(" in _AGENT_SRC)


def part_b():
    print("\n=== B) 注入文案：参数必须来自工具自己的 schema ===")
    hint = _ui_staticmethod("_tool_param_hint")
    in_list = _ui_staticmethod("_tool_in_list")
    check("取到 _tool_param_hint", callable(hint))
    check("取到 _tool_in_list", callable(in_list))
    if not (callable(hint) and callable(in_list)):
        return

    TD = [
        {"type": "function", "function": {
            "name": "browser_open",
            "parameters": {"type": "object",
                           "properties": {"url": {"type": "string"}},
                           "required": ["url"]}}},
        {"type": "function", "function": {
            "name": "video_gen",
            "parameters": {"type": "object",
                           "properties": {"prompt": {}, "dialogue": {},
                                          "duration": {}, "aspect": {}},
                           "required": ["prompt"]}}},
        {"type": "function", "function": {
            "name": "web_search",
            "parameters": {"type": "object",
                           "properties": {"query": {}}, "required": ["query"]}}},
    ]

    print("-- B1 按工具取参数（这是本次事故的核心）--")
    h_bo = hint(TD, "browser_open")
    check("browser_open → 只提 url", h_bo == "url（必填）", repr(h_bo))
    check("browser_open **不再**出现视频参数",
          not any(k in h_bo for k in ("prompt", "duration", "aspect", "dialogue")),
          h_bo)
    h_vg = hint(TD, "video_gen")
    check("video_gen → 提自己的参数", "prompt" in h_vg and "duration" in h_vg, h_vg)
    check("必填项排在前面", h_vg.startswith("prompt"), h_vg)
    check("web_search → query", hint(TD, "web_search") == "query（必填）")
    check("不存在的工具 → 空串（交给通用话术）",
          hint(TD, "no_such_tool") == "", repr(hint(TD, "no_such_tool")))
    check("空工具有 properties 时也不炸", hint(TD, None) == "")

    print("\n-- B2 强制前校验工具真的可用 --")
    check("在表里 → True", in_list(TD, "browser_open") is True)
    check("不在表里 → False", in_list(TD, "no_such_tool") is False)
    check("空表 → False（宁可不强制）", in_list([], "browser_open") is False)
    check("None → False", in_list(None, "browser_open") is False)
    check("坏数据不炸", in_list([None, {}, {"function": None}], "x") is False)

    print("\n-- B3 源码契约：模板里不许再硬编码视频参数 --")
    # 注意：参数提示是在 `_ft_instr = (` **之前**算好的，切片要从那一行开始，
    # 否则会漏（第一版就漏了，两个断言假红）。
    i = _UI_SRC.index("_ph = self._tool_param_hint(tools, force_tool)")
    seg = _UI_SRC[i:i + 1200]
    check("模板不再出现硬编码的 prompt / duration / aspect / dialogue",
          not re.search(r"如\s*prompt\s*/\s*duration", seg), seg[:200])
    check("模板改用 _tool_param_hint", "_tool_param_hint(tools, force_tool)" in seg)
    check("模板保留『不要臆造参数』的约束", "不要臆造参数" in seg)
    check("取不到 schema 时退回通用话术（不空口硬编）",
          "请严格按该工具的 schema 传参" in seg)
    check("强制前有 _tool_in_list 校验",
          "_tool_in_list(tools, force_tool)" in _UI_SRC)
    check("校验失败会记警告日志",
          "不在本轮工具表中，已放弃强制" in _UI_SRC)
    # 校验必须发生在分支链**之前**（否则 force_tool 仍会被用掉）
    check("校验早于 `if _guard_block:` 分支链",
          _UI_SRC.index("_tool_in_list(tools, force_tool)")
          < _UI_SRC.index("if _guard_block:"))


def part_c():
    print("\n=== C) 本会话目标：背景 ≠ 指令 ===")
    check("注入标题标明是背景", "【本会话最初目标（**仅供参考的背景**，不是本轮指令）】"
          in _UI_SRC or "【本会话最初目标" in _UI_SRC)
    check("明确写『以最后一条用户消息为准』",
          "以最后一条用户消息为准" in _UI_SRC)
    check("明确写『不要主动去做』", "不要主动去做" in _UI_SRC)
    # 旧标题（会被模型当任务的那版）必须已消失
    check("旧标题『【本会话目标（首条用户消息）】』已移除",
          "【本会话目标（首条用户消息）】" not in _UI_SRC)


def _old_style_router():
    """把两处修复**拆掉**，复原成 v4.168.0 的旧判据，返回其路由函数。

    拆法（行定位 + 唯一性断言，锚点错就报错，绝不"猜"）：
      ① 删掉 `if self._prog_fetch_intent(text): return None` 抓取否决
      ② 把 URL 分支还原成「见 URL 就 return browser_open」
    """
    lines = _ATEXT_SRC.split("\n")  # v4.216.0：判据族新家在 agent_text.py

    def find_one(pred, what):
        hits = [i for i, l in enumerate(lines) if pred(l)]
        assert len(hits) == 1, f"{what}: 期望唯一命中，实际 {len(hits)}"
        return hits[0]

    veto = find_one(lambda l: l.strip() == "if _prog_fetch_intent(text):",
                    "抓取否决")
    assert lines[veto + 1].strip() == "return None", "否决体不是 return None"
    del lines[veto:veto + 2]

    url_line = find_one(lambda l: l.strip() == "if any(k in t for k in _BROWSER_URL_KW):",
                        "URL 分支")
    # 紧跟的 4 行 = 新的"打开意图 or 裸 URL"判据 + return None
    blk = "\n".join(lines[url_line:url_line + 5])
    assert "or _is_bare_url(text)" in blk, f"URL 分支结构变了：\n{blk}"
    assert lines[url_line + 4].strip() == "return None", "URL 分支收尾不是 return None"
    lines[url_line + 1:url_line + 5] = ['        return "browser_open"']

    src = "\n".join(lines)
    ast.parse(src)  # 变异后必须仍是合法 Python
    # v4.216.0：agent_text.py 是零依赖纯函数模块，整文件 exec 进独立命名空间
    ns = {"re": re, "__name__": "_old_style_agent_text"}
    exec(src, ns)
    return ns["_route_force_tool"]


def part_d():
    print("\n=== D) 负面验证：把修复拆掉，真实任务必须重新被误判 ===")
    old_route = _old_style_router()

    g_new, g_old = route(AUTO_GITHUB, None), old_route(AUTO_GITHUB, None)
    check("旧判据下「每日GitHub热榜」被误判为 browser_open（事故复现）",
          g_old == "browser_open", f"旧={g_old}")
    check("新判据下同一条不再被强制", g_new is None, f"新={g_new}")
    check("修复确实改变了行为（不是同义重写）", g_new != g_old,
          f"new={g_new} old={g_old}")

    n_new, n_old = route(AUTO_NEWS, None), old_route(AUTO_NEWS, None)
    check("新闻抓取：旧判据被 _neg_hit 侥幸救下（说明原修法不可靠）",
          n_old is None, f"旧={n_old}")
    check("新闻抓取：新判据靠显式否决拦住", n_new is None, f"新={n_new}")

    # 旧判据下"抓取 + URL"一律误判 → 证明否决是必需的
    probe = "抓取 https://github.com/trending 的数据"
    check("旧判据：抓取+URL → browser_open（误判）",
          old_route(probe, None) == "browser_open", str(old_route(probe, None)))
    check("新判据：抓取+URL → 不强制", route(probe, None) is None)

    # 但真浏览器意图在新旧两版下必须一致（防"治过头"）
    for t in ("打开知乎网页", "打开百度的网页", "浏览 https://36kr.com"):
        a, b = route(t, None), old_route(t, None)
        check(f"新旧一致（真浏览器意图未治过头）：{t}", a == b == "browser_open",
              f"new={a} old={b}")


def main():
    part_a()
    part_b()
    part_c()
    part_d()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
