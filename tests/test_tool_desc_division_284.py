# -*- coding: utf-8 -*-
"""v4.283.2 判据：易混工具的分工说明必须在位（防「名字像就判重复」式误砍复燃）。

背景：2026-10-16 小臭自检报告把 schedule/create_automation(remind)、
web_fetch/browser_read、search_memory/rag_search 三对判为「重复，二选一砍」。
大哥侧源码实证：三对都不是等价重复，而是分层/分流关系——
  · schedule = 轻量一次性（不落盘） vs create_automation = 落盘持久重复日程；
  · web_fetch = 免浏览器快路径（优先） vs browser_read = JS-heavy/交互场景（失败路由）；
  · search_memory = 用户偏好/记忆 vs rag_search = 本地文档知识库。
正确做法是把分工写进工具 description 让模型自己会选，而不是砍工具。
本判据守护这些分工字样在位——未来改描述时丢掉分工指引即红。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("PASS: %s" % name)
    else:
        _f += 1
        print("FAIL: %s | %s" % (name, detail))


from config import TOOL_DEFS  # noqa: E402

desc = {}
for d in TOOL_DEFS:
    f = d.get("function", d)
    desc[f.get("name")] = f.get("description", "") or ""

# D1 schedule ↔ create_automation：轻量/持久分层
check("D1 schedule 标注轻量一次性不落盘", "轻量一次性" in desc.get("schedule", ""),
      desc.get("schedule", "")[:80])
check("D1b schedule 指向重复日程去向 create_automation",
      "create_automation" in desc.get("schedule", ""), "")
check("D1c create_automation 标注落盘持久化",
      "落盘持久化" in desc.get("create_automation", ""), "")
check("D1d create_automation 反向指向轻量提醒 schedule",
      "schedule" in desc.get("create_automation", ""), "")

# D2 web_fetch ↔ browser_read：快路径/失败路由
check("D2 web_fetch 标注免浏览器快路径优先",
      "免浏览器快路径" in desc.get("web_fetch", ""), "")
check("D2b web_fetch 失败时路由到 browser_read",
      "browser_read" in desc.get("web_fetch", ""), "")
check("D2c browser_read 仍在册（不许按『二选一』误砍）",
      "browser_read" in desc, "browser_read 不在 TOOL_DEFS")

# D3 search_memory ↔ rag_search：记忆/知识库分工
check("D3 search_memory 标注分工", "分工" in desc.get("search_memory", ""), "")
check("D3b search_memory 指向 rag_search", "rag_search" in desc.get("search_memory", ""), "")
check("D3c rag_search 标注分工", "分工" in desc.get("rag_search", ""), "")
check("D3d rag_search 指向 search_memory", "search_memory" in desc.get("rag_search", ""), "")

# D4 误砍防护：三对工具必须同时在册（一个都不能少）
for pair in (("schedule", "create_automation"),
             ("web_fetch", "browser_read"),
             ("search_memory", "rag_search")):
    miss = [n for n in pair if n not in desc]
    check("D4 %s 成对在册（防误砍）" % "/".join(pair), not miss, "缺失 %r" % miss)

print("TOOL_DESC_DIVISION PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
