# -*- coding: utf-8 -*-
"""v4.280.0 判据：定时任务正文否定浏览器时，物理摘掉 browser_* 工具。

事故延续（bd1c4be 之后）：bd1c4be 已让「无需浏览器」不再被路由成打开网页、
定时任务也不因多功能澄清停住；但模型在无人值守时仍可能无视「无需浏览器」直接
调 browser_open —— 主人反馈「自动化任务又乱调浏览器，还完不成任务」。

根因补强：在工具集层面兜底。任务正文显式否定浏览器（无需浏览器 / 不用浏览器 /
纯 HTTP / requests 直连）时，把 browser_open / browser_navigate / browser_click /
browser_fill / browser_read 全部从无人值守可用清单摘掉，自动化根本没有开浏览器的手段。
fail-open：未命中否定词 → 不动（保留浏览器能力，不误伤真要开网页的定时任务）。

本组钉住：
  N1  trending 任务（无需浏览器 + 纯 HTTP + requests 直连）→ 所有 browser_* 摘掉；
  N2  同时保留 web_fetch / write_file（落盘 + HTTP 仍补回）；
  N3  未否定浏览器的任务（如「打开网页截图」）→ browser_open 保留；
  N4  任务没写任何否定词 → 不误伤（browser 工具全集仍在）；
  N5  源码契约：filter_tools_safe 真的串了 _strip_negated_browser_tools。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s%s" % (name, ("  <%s>" % detail) if detail else ""))


print("=" * 64)
print("v4.280.0 定时任务否定浏览器 → 物理摘掉 browser_* 工具")
print("=" * 64)

# 主人这次没跑起来的 trending 任务正文（含多重否定信号）。
OWNER = (
    "【自动化任务】抓取今天最新的 GitHub trending，整理成 8 条以内要点简报"
    "（每条：仓库名 + 一句话说明 + Star数/语言/来源），"
    "写入 ~/Documents/小臭玩AI/产物/github_trending_latest.md，"
    "并在回复里展示简报内容。\n"
    "【通道策略】T0 首选 requests 直连 https://github.com/trending?since=daily "
    "（纯 HTTP，无需浏览器，避免 Playwright 导航中断）；Star 补全用 GitHub API；"
    "不足 8 条按实际条数，禁止编造。"
)

# 真要开网页的定时任务（不应被误伤）。
WANT_BROWSER = (
    "【自动化任务】打开网页 https://example.com 截图，保存到 ~/Documents/截图.png"
)

# 普通提醒任务（无否定词、无浏览器意图）。
REMIND = "【自动化任务】每天 9 点提醒我喝水"


def _catalog():
    def T(n):
        return {"type": "function", "function": {"name": n}}
    return [T(n) for n in (
        "web_fetch", "web_search", "read_file",
        "browser_open", "browser_navigate", "browser_click",
        "browser_fill", "browser_read",
        "write_file", "db_insert", "schedule", "run_python", "run_command",
    )]


try:
    import automation

    print("\n--- N1 trending 任务：所有 browser_* 摘掉 ---")
    kept = [t["function"]["name"]
            for t in automation.filter_tools_safe(_catalog(), {"id": "t", "message": OWNER})]
    browser_left = [n for n in kept if n.startswith("browser_")]
    check("N1 无 browser_* 工具残留", not browser_left, repr(browser_left))
    check("N1b write_file 仍在（落盘已声明）", "write_file" in kept, repr(kept))
    check("N1c web_fetch 仍在（HTTP 已声明）", "web_fetch" in kept, repr(kept))
    check("N1d 仍不补回危险工具",
          not ({"db_insert", "schedule", "run_python", "run_command"} & set(kept)),
          repr(kept))

    print("\n--- N3 真要开网页的任务：browser_open 保留 ---")
    kept_b = [t["function"]["name"]
              for t in automation.filter_tools_safe(_catalog(), {"id": "b", "message": WANT_BROWSER})]
    # 基础 risk 分级只放过 browser_open / browser_read（navigate/click/fill 风险更高
    # 已被基础过滤滤掉）；这里只验证「过基础过滤的浏览器工具没被否定摘除法误伤」。
    check("N3 打开网页任务保留 browser_open",
          "browser_open" in kept_b, repr(kept_b))
    check("N3b 过基础过滤的 browser_read 也在",
          "browser_read" in kept_b, repr(kept_b))

    print("\n--- N4 普通任务（无否定词）：不误伤 ---")
    kept_r = [t["function"]["name"]
              for t in automation.filter_tools_safe(_catalog(), {"id": "r", "message": REMIND})]
    check("N4 过基础过滤的浏览器工具保留",
          {"browser_open", "browser_read"} <= set(kept_r),
          repr(kept_r))
    check("N4b 未声明落盘 → write_file 仍滤掉",
          "write_file" not in kept_r, repr(kept_r))

    print("\n--- N5 源码契约：filter_tools_safe 串了摘浏览器逻辑 ---")
    src = open(os.path.join(ROOT, "automation.py"), encoding="utf-8").read()
    check("N5 定义了 _NO_BROWSER_KW 否定词表", "_NO_BROWSER_KW" in src)
    check("N5b 定义了 _strip_negated_browser_tools",
          "_strip_negated_browser_tools" in src)
    check("N5c filter_tools_safe 调用了摘浏览器",
          "_strip_negated_browser_tools(\n        _restore_declared_task_tools" in src
          or "_strip_negated_browser_tools(_restore_declared_task_tools" in src)
except Exception as e:
    check("N 套件可运行", False, "%s: %s" % (type(e).__name__, e))


print("=" * 64)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 64)
sys.exit(1 if FAIL else 0)
