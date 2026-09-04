# -*- coding: utf-8 -*-
"""小臭玩AI — 浏览器控制工具（受控：对话触发 + 执行前确认 + 日志可见）

实际浏览器操作交给 browser_runner.py（Playwright），本模块通过 subprocess 调
系统 Python 执行，规避冻结 exe 打包 Playwright 的复杂度。所有工具返回
(result_str, deliverables, schedule)，永不抛异常。

注册方式：BROWSER_CONTROL_TOOL_DEFS 声明式 schema → config.py 聚合进 TOOL_DEFS
→ tools.py 的 exec_tool() 路由分发。
"""

import os
import sys
import json
import subprocess
import time
import urllib.request
import urllib.parse

# 系统 Python（运行 Playwright 的执行体）。顺序：配置 > 通用名兜底。
# 注：Windows 常见安装路径（如 %LOCALAPPDATA%\Programs\Python\Python3XX\python.exe）
# 由下方 _find_python() 动态探测，不在源码硬编码。
_DEFAULT_PY = [
    "python3",
    "python",
]


def _resource_path(name):
    """定位 browser_runner.py：开发态同目录，冻结态在 exe 所在目录或 _internal 下。

    重要：冻结态下本模块 __file__ 路径里的中文（小臭玩AI）可能被编码损坏（如变 СAI），
    因此**不能**依赖 __file__ 推断目录。优先用 sys.executable 所在目录——Windows 会返回
    正确的 Unicode 路径，绝不会乱码。
    """
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, name))
        candidates.append(os.path.join(exe_dir, "_internal", name))
        mp = getattr(sys, "_MEIPASS", None)
        if mp:
            candidates.append(os.path.join(mp, name))
            candidates.append(os.path.join(mp, "_internal", name))
    # 开发态：__file__ 同目录（源码树中文路径完好）
    base = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(base, name))
    candidates.append(os.path.join(base, "_internal", name))
    for c in candidates:
        if os.path.exists(c):
            return c
    # 全找不到：返回最可能是路径（exe 目录），让报错信息准确
    return candidates[0] if candidates else name


def _find_python(cfg):
    cand = (cfg or {}).get("browser_python")
    if cand and os.path.exists(cand):
        return cand
    # 动态探测 Windows 常见 Python 安装位置（不硬编码用户名）
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        import glob as _glob
        for pat in (
            os.path.join(local, "Programs", "Python", "Python3*", "python.exe"),
            os.path.join(local, "Programs", "Python", "Python3*", "scripts", "python.exe"),
        ):
            hits = sorted(_glob.glob(pat), reverse=True)
            if hits:
                return hits[0]
    for p in _DEFAULT_PY:
        if os.path.exists(p):
            return p
    return "python"


# ---------- CDP 自动拉起（v4.103：无需手动点快捷方式）----------
_EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_edge():
    for c in _EDGE_CANDIDATES:
        if os.path.isfile(c):
            return c
    return ""


def _cdp_ready(cdp_url, timeout=1.0):
    try:
        urllib.request.urlopen(cdp_url.rstrip("/") + "/json/version", timeout=timeout)
        return True
    except Exception:
        return False


def _ensure_cdp(cdp, app_dir=None):
    """确保 CDP 调试端口可用；不可用则自动拉起带调试端口的 Edge。返回 (ok, msg)。

    关键：用**专属 profile 目录**启动 Edge（独立单例锁），永远不和用户真实 Edge 的
    默认 profile 抢锁，因此调试端口必定能起来，自动接管稳定可用。
    """
    if _cdp_ready(cdp):
        return True, ""
    edge = _find_edge()
    if not edge:
        return False, ("未找到 Edge 可执行文件，无法自动启动调试浏览器。"
                       "请确认已安装 Microsoft Edge。")
    port = urllib.parse.urlparse(cdp).port or 9222
    # 专属 profile：放在 app_dir 下，避免与默认 Edge 单例冲突导致调试端口起不来
    if app_dir:
        profile = os.path.join(app_dir, "cdp_edge_profile")
    else:
        profile = os.path.expandvars(r"%LOCALAPPDATA%/小臭玩AI/cdp_edge_profile")
    try:
        os.makedirs(profile, exist_ok=True)
    except Exception:
        profile = os.path.join(os.path.expanduser("~"), "cdp_edge_profile")
        os.makedirs(profile, exist_ok=True)
    args = [
        edge,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
    ]
    try:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, **kwargs,
        )
    except Exception as e:
        return False, f"自动启动 Edge 失败：{e}"
    for _ in range(60):  # 最多约 18 秒，Edge 冷启动较慢
        if _cdp_ready(cdp):
            return True, ""
        time.sleep(0.3)
    return False, ("自动启动 Edge 后调试端口仍未就绪。请稍后重试，或检查 Edge 是否被安全"
                   "软件拦截启动。小臭使用独立 profile 接管浏览器，不会干扰你的真实 Edge。")


def _run_runner(action, url, selector="", text="", cfg=None, headless="1", app_dir=None):
    """调用 browser_runner.py，返回解析后的 JSON dict。"""
    # v4.34 lazyload：检查 Playwright 可用性，不可用返回友好提示而非 subprocess 报错
    try:
        import playwright  # noqa: F401
    except ImportError:
        return {"ok": False, "error": "Playwright 未安装。浏览器自动化需先运行：pip install playwright && python -m playwright install chromium"}
    py = _find_python(cfg)
    runner = _resource_path("browser_runner.py")
    cdp = (cfg or {}).get("browser_cdp", "") if cfg else ""
    cmd = [
        py, runner,
        "--action", action,
        "--url", url or "",
        "--selector", selector or "",
        "--text", text or "",
        "--headless", headless,
    ]
    if cdp:
        ok, msg = _ensure_cdp(cdp, app_dir=app_dir)
        if not ok:
            return {"ok": False, "error": msg}
        cmd += ["--cdp", cdp]
    try:
        # v4.108 H-09：显式注入 UTF-8 stdout，避免子进程按系统 GBK 编码输出中文
        # 导致父进程 UTF-8 解码乱码/丢字（errors="ignore" 只丢字符不报错，静默坏数据）。
        _run_env = dict(os.environ)
        _run_env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=90,
            encoding="utf-8", errors="ignore", env=_run_env,
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
        # 返回标准化 (rel, kind, name) 三元组；rel 用绝对路径，
        # _on_deliverable_open 的 os.path.join(APP_DIR, rel) 对绝对路径原样返回可正确打开。
        return [(shot, "image", os.path.basename(shot))]
    return []


# ---------- 工具实现（统一签名 cfg, app_dir, args）----------

def tool_browser_open(cfg, app_dir, args):
    url = args.get("url", "")
    if not url:
        return ("失败：browser_open 需要 url", [], None)
    out = _run_runner("open", url, cfg=cfg, app_dir=app_dir)
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
    out = _run_runner("click", url, selector=sel, cfg=cfg, app_dir=app_dir)
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
    out = _run_runner("fill", url, selector=sel, text=text, cfg=cfg, app_dir=app_dir)
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
    out = _run_runner("read", url, selector=sel, cfg=cfg, app_dir=app_dir)
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
            "description": "打开网页并截图。若已配置 browser_cdp（如用户以 --remote-debugging-port=9222 启动的真实 Edge 且端口已就绪），则接管该浏览器当前页面（带登录态）；否则自动拉起一个独立 profile 的 Edge，不干扰真实 Edge。用于查看网页、留档截图。**操作网页内容请用本系列工具（browser_open/click/fill/read）；不要用 app_* 工具——app_* 针对原生桌面程序（如 Excel），读不到浏览器网页内容。多步操作（填表/发布）请先 browser_open 打开页面，再 browser_fill/browser_click 直接操作当前页面（接管模式下不会重新刷新页面、不会清空已填内容）。**",
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
            "description": "点击网页指定元素并返回点击后截图。selector 支持 css=、text=、xpath= 前缀；无前缀时含 . # [ > 等按 CSS 处理，否则按可见文本。接管浏览器（CDP）模式下直接点击当前已打开页面的元素，不要重复 browser_open（重复打开会刷新清空已填内容）。",
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
            "description": "在指定输入框/编辑器填入文本并返回截图，用于自动填表、发布文章。selector 同 browser_click（支持 css=/text=/xpath= 前缀，无前缀智能判断）。已兼容知乎等 contenteditable 富文本编辑器（标题/正文均为 contenteditable）。接管浏览器（CDP）模式下直接填当前页面，不要重复 browser_open（重复打开会刷新清空已填内容）。",
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
            "description": "提取网页文本（或指定元素文本）返回，用于抓取网页文字内容做分析。selector 同 browser_click（支持 css=/text=/xpath= 前缀，无前缀智能判断）。接管浏览器（CDP）模式下直接读取当前页面，不要重复 browser_open。",
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
