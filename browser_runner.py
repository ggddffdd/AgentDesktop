# -*- coding: utf-8 -*-
"""AgentDesktop — 浏览器自动化执行器（由 browser_control_tools 通过 subprocess 调用）

职责：启动带反检测的 Chromium（或 CDP 接管用户真实浏览器），执行
open/click/fill/read 操作，把结果以 JSON 输出到 stdout（截图路径单独返回）。

登录态通过持久化 profile（~/.playwright_profile）跨会话保留；CDP 模式则继承
用户真实浏览器的全部登录态与已开标签页。

v4.105 修复（知乎发布失败根因）：
- CDP 模式（接管用户真实浏览器）下，fill/click/read **复用当前已打开的页面**
  （context.pages[0]），不再 page.goto 重新加载，避免反复刷新清空已填内容、
  破坏多步编辑会话的连续性。仅 open 动作导航一次。
- fill 兼容 contenteditable 富文本编辑器（知乎标题/正文都是 contenteditable
  div，Playwright 的 fill() 对其无效）：检测到 contenteditable 时改用
  click() 聚焦 + keyboard.insert_text() 写入，触发框架 input 事件。
- selector 解析更鲁棒：无 css=/text=/xpath= 前缀时，像 CSS 选择器（含 . # [ > 等）
  按 CSS 处理，否则按可见文本；避免把 ".titleInput" 当文本去找而超时。

运行环境：系统 Python 3.12（已装 playwright + chromium）。
"""
import sys
import os
import json
import time
import argparse
import tempfile


def _looks_like_css(sel):
    """无前缀时粗略判断 selector 是否像 CSS 选择器。"""
    if not sel:
        return False
    # 含典型 CSS 符号
    if any(ch in sel for ch in (".", "#", "[", ">", "+", "~", ":")):
        return True
    # 首词是常见 HTML 标签
    head = sel.strip().split()[0] if sel.strip() else ""
    return head in (
        "div", "span", "input", "button", "a", "p", "textarea", "select",
        "form", "ul", "li", "table", "section", "article", "header",
        "footer", "nav", "h1", "h2", "h3", "h4", "h5", "h6", "label",
        "img", "iframe", "body", "html", "main", "code", "pre", "strong",
        "em", "i", "b",
    )


def _locate(page, sel):
    """返回 (locator, kind)。支持 css=/text=/xpath= 前缀；无前缀智能判断。"""
    if sel.startswith("css="):
        return page.locator(sel[4:]).first, "css"
    if sel.startswith("xpath="):
        return page.locator("xpath=" + sel[6:]).first, "xpath"
    if sel.startswith("text="):
        return page.get_by_text(sel[5:]).first, "text"
    if _looks_like_css(sel):
        return page.locator(sel).first, "css"
    return page.get_by_text(sel).first, "text"


def _is_contenteditable(loc):
    try:
        return bool(loc.evaluate(
            "el => !!(el && el.getAttribute && el.getAttribute('contenteditable') === 'true') "
            "|| (el && el.isContentEditable)"))
    except Exception:
        return False


def _fill_el(page, loc, text):
    """兼容 <input>/<textarea> 与 contenteditable 富文本编辑器。"""
    if _is_contenteditable(loc):
        # 富文本：聚焦后清空再一次性输入，触发框架 input 事件
        loc.click()
        try:
            loc.press("Control+a")
        except Exception:
            pass
        try:
            page.keyboard.press("Delete")
        except Exception:
            pass
        page.keyboard.insert_text(text)
    else:
        loc.fill(text)


def _shot_path(action):
    return os.path.join(tempfile.gettempdir(), f"xc_browser_{action}_{int(time.time() * 1000)}.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", required=True, choices=["open", "click", "fill", "read"])
    ap.add_argument("--url", default="")
    ap.add_argument("--selector", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--profile", default=os.path.join(os.path.expanduser("~"), ".playwright_profile"))
    ap.add_argument("--headless", default="1")
    ap.add_argument("--timeout", default="20000")
    # CDP 接管真实浏览器（用户以 --remote-debugging-port 启动的 Edge/Chrome）
    ap.add_argument("--cdp", default="",
                    help="CDP websocket 地址，如 http://127.0.0.1:9222 ；"
                         "提供则连接用户真实浏览器（带登录态），不另起 Playwright 实例")
    args = ap.parse_args()

    out = {"ok": False}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        out["error"] = f"playwright 未安装：{e}"
        print(json.dumps(out, ensure_ascii=False))
        return

    headless = args.headless != "0"
    timeout = int(args.timeout)
    os.makedirs(args.profile, exist_ok=True)

    try:
        with sync_playwright() as p:
            if args.cdp:
                # CDP 接管：连接用户真实浏览器，继承其全部登录态与已开标签页
                browser = p.chromium.connect_over_cdp(args.cdp)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.pages[0] if context.pages else context.new_page()
                close_ctx = False  # 不关闭用户的浏览器
                cdp_mode = True
            else:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=args.profile,
                    headless=headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-infobars",
                    ],
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    viewport={"width": 1366, "height": 768},
                )
                # 反检测：抹掉 navigator.webdriver 标记
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                page = context.new_page()
                close_ctx = True
                cdp_mode = False
            page.set_default_timeout(timeout)

            # v4.105 导航策略：
            # - open 总是导航到目标 url（建立编辑页）
            # - CDP 模式下 fill/click/read：复用当前已打开页面，**不再 goto**，保持会话连续
            #   （仅当页面仍是空白页且给了 url 时，补一次导航作为兜底）
            # - 非 CDP 模式：每次都 goto（独立浏览器，无跨进程 page 复用）
            if args.action == "open":
                if args.url:
                    page.goto(args.url, wait_until="load", timeout=timeout)
                    page.wait_for_timeout(800)
            elif args.action in ("click", "fill", "read"):
                if not cdp_mode and args.url:
                    page.goto(args.url, wait_until="load", timeout=timeout)
                    page.wait_for_timeout(800)
                elif cdp_mode and args.url and page.url in ("about:blank", "chrome://newtab/", ""):
                    page.goto(args.url, wait_until="load", timeout=timeout)
                    page.wait_for_timeout(800)

            if args.action == "open":
                shot = _shot_path("open")
                page.screenshot(path=shot, full_page=False)
                out = {"ok": True, "action": "open", "url": page.url,
                       "title": page.title(), "screenshot": shot}

            elif args.action == "click":
                loc, kind = _locate(page, args.selector)
                loc.click()
                page.wait_for_timeout(500)
                shot = _shot_path("click")
                page.screenshot(path=shot)
                out = {"ok": True, "action": "click", "url": page.url,
                       "title": page.title(), "screenshot": shot}

            elif args.action == "fill":
                loc, kind = _locate(page, args.selector)
                _fill_el(page, loc, args.text)
                page.wait_for_timeout(400)
                shot = _shot_path("fill")
                page.screenshot(path=shot)
                out = {"ok": True, "action": "fill", "url": page.url,
                       "title": page.title(), "screenshot": shot}

            elif args.action == "read":
                if args.selector:
                    loc, kind = _locate(page, args.selector)
                    text = loc.inner_text()
                else:
                    # v4.108 H-07 修复：page.inner_text() 的 selector 是必填参数，
                    # 整页读取必须显式传 "body"，否则 TypeError 直接崩。
                    text = page.inner_text("body")
                out = {"ok": True, "action": "read", "url": page.url,
                       "title": page.title(), "text": text[:8000]}

            if close_ctx:
                context.close()
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    out["mode"] = "cdp" if args.cdp else "playwright"
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    # v4.108 H-09：无论父进程是否注入 PYTHONIOENCODING，出口都固定 UTF-8，
    # 保证中文 JSON 结果不被系统 GBK 编码污染（父进程按 UTF-8 读取）。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
