# -*- coding: utf-8 -*-
"""AgentDesktop — 浏览器自动化执行器（由 browser_control_tools 通过 subprocess 调用）

职责：启动带反检测的 Chromium，执行 open/click/fill/read 操作，
把结果以 JSON 输出到 stdout（截图路径单独返回）。
登录态通过持久化 profile（~/.playwright_profile）跨会话保留。

运行环境：系统 Python 3.12（已装 playwright + chromium）。
"""
import sys
import os
import json
import time
import argparse
import tempfile


def _parse_selector(sel):
    """支持 css= / text= / xpath= 前缀；否则默认按可见文本匹配。"""
    if sel.startswith("css="):
        return sel[4:], "css"
    if sel.startswith("xpath="):
        return sel[6:], "xpath"
    if sel.startswith("text="):
        return sel[5:], "text"
    return sel, "text"


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
            page.set_default_timeout(timeout)

            if args.action in ("open", "click", "fill", "read") and args.url:
                page.goto(args.url, wait_until="load", timeout=timeout)
                page.wait_for_timeout(800)

            if args.action == "open":
                shot = _shot_path("open")
                page.screenshot(path=shot, full_page=False)
                out = {"ok": True, "action": "open", "url": page.url,
                       "title": page.title(), "screenshot": shot}

            elif args.action == "click":
                sel, kind = _parse_selector(args.selector)
                loc = page.locator(sel).first if kind == "css" else page.get_by_text(sel).first
                loc.click()
                page.wait_for_timeout(500)
                shot = _shot_path("click")
                page.screenshot(path=shot)
                out = {"ok": True, "action": "click", "url": page.url,
                       "title": page.title(), "screenshot": shot}

            elif args.action == "fill":
                sel, kind = _parse_selector(args.selector)
                loc = page.locator(sel).first if kind == "css" else page.get_by_text(sel).first
                loc.fill(args.text)
                page.wait_for_timeout(400)
                shot = _shot_path("fill")
                page.screenshot(path=shot)
                out = {"ok": True, "action": "fill", "url": page.url,
                       "title": page.title(), "screenshot": shot}

            elif args.action == "read":
                if args.selector:
                    sel, kind = _parse_selector(args.selector)
                    text = (page.locator(sel).first.inner_text()
                            if kind == "css" else page.get_by_text(sel).first.inner_text())
                else:
                    text = page.inner_text()
                out = {"ok": True, "action": "read", "url": page.url,
                       "title": page.title(), "text": text[:8000]}

            context.close()
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
