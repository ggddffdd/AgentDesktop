# -*- coding: utf-8 -*-
"""AgentDesktop — 浏览器控制工具（受控：对话触发 + 执行前确认 + 日志可见）

实际浏览器操作交给 browser_runner.py（Playwright），本模块通过 subprocess 调
系统 Python 执行，规避冻结 exe 打包 Playwright 的复杂度。所有工具返回
(result_str, deliverables, schedule)，永不抛异常。

注册方式：BROWSER_CONTROL_TOOL_DEFS 声明式 schema → config.py 聚合进 TOOL_DEFS
→ tools.py 的 exec_tool() 路由分发。
"""

import os
import json
import subprocess

# 系统 Python（运行 Playwright 的执行体）。顺序：配置 > 已知路径 > 通用名。
_DEFAULT_PY = [
    r"<PYTHON_EXE>",
    "python3",
    "python",
]


def _resource_path(name):
    """定位 browser_runner.py：开发态同目录，冻结态在 _internal 下。"""
    base = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(base, name)
    if os.path.exists(cand):
        return cand
    cand2 = os.path.join(base, "_internal", name)
    if os.path.exists(cand2):
        return cand2
    return cand


def _find_python(cfg):
    cand = (cfg or {}).get("browser_python")
    if cand and os.path.exists(cand):
        return cand
    for p in _DEFAULT_PY:
        if os.path.exists(p):
            return p
    return "python"


def _run_runner(action, url, selector="", text="", cfg=None, headless="1"):
    """调用 browser_runner.py，返回解析后的 JSON dict。"""
    # v4.34 lazyload：检查 Playwright 可用性，不可用返回友好提示而非 subprocess 报错
    try:
        import playwright  # noqa: F401
    except ImportError:
        return {"ok": False, "error": "Playwright 未安装。浏览器自动化需先运行：pip install playwright && python -m playwright install chromium"}
    py = _find_python(cfg)
    runner = _resource_path("browser_runner.py")
    cmd = [
        py, runner,
        "--action", action,
        "--url", url or "",
        "--selector", selector or "",
        "--text", text or "",
        "--headless", headless,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=90,
            encoding="utf-8", errors="ignore",
        )
    except Exception as e:
        return {"ok": False, "error": f"启动浏览器执行器失败：{e}"}
    # 取 stdout 中最后一个 JSON 行
    out = None
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                out = json.loads(line)
            except Exception:
                out = None
            if out:
                break
    if out is None:
        err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "无输出"
        return {"ok": False, "error": f"浏览器执行器无结果：{err}"}
    return out


def _deliver_shot(out):
    shot = out.get("screenshot", "")
    if shot and os.path.exists(shot):
        return [shot]
    return []


# ---------- 工具实现（统一签名 cfg, app_dir, args）----------

def tool_browser_open(cfg, app_dir, args):
    url = args.get("url", "")
    if not url:
        return ("失败：browser_open 需要 url", [], None)
    out = _run_runner("open", url, cfg=cfg)
    if not out.get("ok"):
        return (f"失败：{out.get('error', '未知错误')}", [], None)
    return (
        f"已打开网页：{out.get('title', '')}（{out.get('url', '')}），截图见下方",
        _deliver_shot(out),
        None,
    )


def tool_browser_click(cfg, app_dir, args):
    url = args.get("url", "")
    sel = args.get("selector", "")
    if not url or not sel:
        return ("失败：browser_click 需要 url 和 selector", [], None)
    out = _run_runner("click", url, selector=sel, cfg=cfg)
    if not out.get("ok"):
        return (f"失败：{out.get('error', '未知错误')}", [], None)
    return (
        f"已点击元素并截图：{out.get('url', '')}",
        _deliver_shot(out),
        None,
    )


def tool_browser_fill(cfg, app_dir, args):
    url = args.get("url", "")
    sel = args.get("selector", "")
    text = args.get("text", "")
    if not url or not sel or text is None:
        return ("失败：browser_fill 需要 url、selector、text", [], None)
    out = _run_runner("fill", url, selector=sel, text=text, cfg=cfg)
    if not out.get("ok"):
        return (f"失败：{out.get('error', '未知错误')}", [], None)
    return (
        f"已填写表单并截图：{out.get('url', '')}",
        _deliver_shot(out),
        None,
    )


def tool_browser_read(cfg, app_dir, args):
    url = args.get("url", "")
    sel = args.get("selector", "")
    if not url:
        return ("失败：browser_read 需要 url", [], None)
    out = _run_runner("read", url, selector=sel, cfg=cfg)
    if not out.get("ok"):
        return (f"失败：{out.get('error', '未知错误')}", [], None)
    txt = out.get("text", "")
    preview = txt[:1500] + ("…" if len(txt) > 1500 else "")
    return (f"已读取网页文本（{len(txt)} 字）：\n{preview}", [], None)


# ---------- 声明式 schema（OpenAI function-calling 格式）----------

BROWSER_CONTROL_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": "用内置反检测浏览器打开一个网页 URL 并截图返回。用于查看网页内容、留档截图。登录态会通过持久化 profile 保留。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的网页地址，如 https://www.baidu.com"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "打开网页并点击指定元素，返回点击后截图。selector 支持 css=、text=、xpath= 前缀，默认按可见文本匹配。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页地址"},
                    "selector": {"type": "string", "description": "要点击的元素，如 text=登录 或 css=#btn"},
                },
                "required": ["url", "selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fill",
            "description": "打开网页并在指定输入框填入文本，返回截图。用于自动填表。selector 同 browser_click。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页地址"},
                    "selector": {"type": "string", "description": "输入框选择器，如 css=#username"},
                    "text": {"type": "string", "description": "要填入的文本"},
                },
                "required": ["url", "selector", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_read",
            "description": "打开网页并提取页面文本（或指定元素文本）返回，用于抓取网页文字内容做分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页地址"},
                    "selector": {"type": "string", "description": "可选，只提取该元素的文本；留空则提取整页文本"},
                },
                "required": ["url"],
            },
        },
    },
]

BROWSER_CONTROL_TOOL_TABLE = {
    "browser_open": tool_browser_open,
    "browser_click": tool_browser_click,
    "browser_fill": tool_browser_fill,
    "browser_read": tool_browser_read,
}
