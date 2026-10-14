# -*- coding: utf-8 -*-
"""v4.272.0：聊天页 setHtml 不再被导航白名单当成 data: 拒绝。

v4.265.0 起 navigation_allowed 拒绝一切 data:。Qt WebEngine 的
QWebEngineView.setHtml 会发起主框架 data:text/html 导航（不是用户点击），
于是聊天页自己永远 loadFinished(False)，整页空白。

判据是行为级的：
  · 非点击的主框架 data:text/html（含 charset）放行
  · 点击的 data: 链接、子框架、其它 data: / javascript: 仍拒绝
  · navigation_allowed 本身仍然拒绝 data:（白名单不改松）
  · 聊天页 acceptNavigationRequest 真的调用这个判定，且不在 if False 死分支里
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import webengine_policy as wp  # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def _call_name(node):
    func = getattr(node, "func", None)
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_named_call(node, name):
    return isinstance(node, ast.Call) and _call_name(node) == name


def _handler_calls_live(src, method, name):
    """method 里对 name 的调用必须是某条 if 的测试表达式本身（或 not 它）。

    `if False and name(...)` / `if False: name(...)` 都算没接到，避免静态串恒真。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != method:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.If):
                continue
            test = inner.test
            if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                test = test.operand
            if _is_named_call(test, name):
                return True
        return False
    return False


def main():
    print("=== 1) setHtml 主框架放行，点击和子框架仍拒绝 ===")
    allow = getattr(wp, "allow_sethtml_navigation", None)
    check("allow_sethtml_navigation 可调用", callable(allow))
    qt_html = "data:text/html;charset=UTF-8,%3Chtml%3E%3Cbody%3Ehi%3C/body%3E%3C/html%3E"
    if not callable(allow):
        print(f"\n汇总：PASS={_p} FAIL={_f}")
        return 1

    check("setHtml 形态（charset）主框架非点击放行",
          allow(qt_html, True, False) is True)
    check("data:text/html, 主框架非点击放行",
          allow("data:text/html,<p>hi</p>", True, False) is True)
    check("大小写不敏感",
          allow("DATA:text/HTML;charset=utf-8,x", True, False) is True)
    check("前后空白仍认 setHtml",
          allow("  data:text/html,x", True, False) is True)

    check("点击的 data:text/html 不放行",
          allow("data:text/html,<script>alert(1)</script>", True, True) is False)
    check("子框架 data:text/html 不放行",
          allow(qt_html, False, False) is False)
    check("子框架且点击也不放行",
          allow(qt_html, False, True) is False)
    check("data:text/javascript 不放行",
          allow("data:text/javascript,alert(1)", True, False) is False)
    check("data:image/svg+xml 不放行",
          allow("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>",
                True, False) is False)
    check("裸 data:, 不放行", allow("data:,hello", True, False) is False)
    check("javascript: 不走 setHtml 特例",
          allow("javascript:alert(1)", True, False) is False)
    check("空 URL 不放行", allow("", True, False) is False)
    check("None 不放行", allow(None, True, False) is False)

    print("\n=== 2) 白名单本身仍拒绝 data:，页面判定把两段接上 ===")
    check("navigation_allowed 仍拒绝 data:text/html",
          wp.navigation_allowed("data:text/html,hi") is False)
    check("navigation_allowed 仍拒绝 data:text/javascript",
          wp.navigation_allowed("data:text/javascript,alert(1)") is False)
    check("navigation_allowed 仍拒绝 javascript:",
          wp.navigation_allowed("javascript:alert(1)") is False)
    check("未列入的 https 仍拒绝",
          wp.navigation_allowed("https://evil.example/a", allow_hosts=()) is False)

    decide = getattr(wp, "decide_chat_whitelist", None)
    check("decide_chat_whitelist 可调用", callable(decide))
    if not callable(decide):
        print(f"\n汇总：PASS={_p} FAIL={_f}")
        return 1

    check("页面判定放行 setHtml",
          decide(qt_html, True, False) is True, qt_html[:48])
    check("页面判定拒绝点击的 data:",
          decide("data:text/html,<a>", True, True) is False)
    check("页面判定拒绝子框架 data:",
          decide(qt_html, False, False) is False)
    check("页面判定拒绝 data:image",
          decide("data:image/png;base64,aaaa", True, False) is False)
    check("页面判定拒绝外站",
          decide("https://evil.example/a", True, False, allow_hosts=()) is False)
    check("页面判定仍放行显式域",
          decide("https://cdn.example/a", True, False,
                 allow_hosts=["cdn.example"]) is True)
    check("页面判定仍放行 about:blank",
          decide("about:blank", True, False) is True)
    check("页面判定仍拒绝 javascript:",
          decide("javascript:alert(1)", True, False) is False)

    # 反向：特例如果恒真，外站会被放行。这里钉住「特例为假时仍走白名单」。
    check("非 data 的主框架非点击不靠特例放行",
          allow("https://evil.example/a", True, False) is False)

    print("\n=== 3) 聊天页真的在导航入口调用该判定 ===")
    chat = _read("chat_web.py")
    check("acceptNavigationRequest 有效调用 decide_chat_whitelist",
          _handler_calls_live(chat, "acceptNavigationRequest",
                              "decide_chat_whitelist"))
    check("异常仍按拒绝", "按拒绝处理" in chat)

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
