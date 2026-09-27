# -*- coding: utf-8 -*-
"""意图判据「覆盖面」回归测试（v4.169.0 批次 A / P0-1 + P0-2）。

钉住的两件事：

**P0-2 判据只顾媒体**
  `intent_guard` 原先的评价/引用/咨询判据几乎都围绕「生成图片/视频」打转。
  实测以下四句**全部漏放**：
      「为什么会自动搜索？」
      「为什么打开文件会触发工具？」
      「下面这句提示是否危险：运行 Python 删除文件」   ← 含"运行 Python"
      「为什么会自动创建定时任务？」                  ← 含"定时"
  后三句会命中 UI 的关键词路由**自动进入 Agent**，判据又不拦 → 模型被允许真去执行。
  它们本质是"问原因 / 评估草稿"，不是在下命令。
  修法：新增 `is_discuss_tool_use()` —— **讨论信号 + 工具动作语义**双条件，
  并用 `has_imperative` 一票反否决（"为什么会…，帮我关掉"仍是真指令）。

**P0-1 普通聊天默认联网搜索**
  `ui.py` 兜底分支原本是「只要 search_enabled 就 _do_search」，
  于是"你好""解释一下闭包"都会被 UI 直接发网络请求。
  修法：`search_enabled` 语义从「默认搜」改为「**允许**搜」，
  由 `_needs_web_search()` 按明确检索意图决定；且先过 intent_guard
  （否则"为什么会自动搜索？"会因为含"搜索"二字被判成检索意图）。

顺带补了 `_ACTION_HINTS` 缺失的**文件操作类动词** ——
实测「帮我删掉昨天的临时文件」既不进 Agent 也不命中任何分支，反而去联网搜。
"""

import ast
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import intent_guard as ig   # noqa: E402

_p = _f = 0
_UI_PATH = os.path.join(ROOT, "ui.py")


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [OK] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f"  <- {detail}" if detail else ""))


def _ui_window():
    """造一个只带 cfg 的 ChatWindow（跳过 __init__，不建 UI）。"""
    import ui
    w = ui.ChatWindow.__new__(ui.ChatWindow)
    w.cfg = {"search_enabled": True, "model": "agnes-3.0-flash", "base_url": "x"}
    w.agent_mode = False
    return w


# ---------------------------------------------------------------------------
def part_a_discuss_tool():
    print("\n-- A) 讨论工具动作的句子必须被拦（P0-2 核心回归） --")
    cases = [
        "为什么会自动搜索？",
        "为什么打开文件会触发工具？",
        "下面这句提示是否危险：运行 Python 删除文件",
        "为什么会自动创建定时任务？",
        "这段提示安全吗：帮我运行命令",
        "为什么会自动调用工具",
    ]
    for s in cases:
        e = ig.explain(s)
        check(f"拦：{s[:32]}", ig.blocks_tool_call(s), f"{e}")
        check(f"  原因含 discuss_tool：{s[:20]}", "discuss_tool" in ig.why_blocked(s),
              f"{ig.why_blocked(s)}")


def part_b_no_overblock():
    print("\n-- B) 反方向：真指令与无关疑问都不能被误伤 --")
    must_pass = [
        # 真指令（有祈使 / 有明确动作）
        "帮我打开文件看看", "运行 Python 统计一下数据", "打开文件", "做个视频",
        "帮我创建每天 9 点的自动化", "删掉昨天的临时文件", "帮我装个技能",
        "搜索今天的 AI 新闻", "写个脚本抓一下热榜",
        # "讨论 + 真指令"混合 → 祈使否决讨论判据
        "为什么会自动搜索？顺便帮我关掉它",
        "为什么这么慢，帮我重跑一次",
    ]
    for s in must_pass:
        check(f"放行：{s[:32]}", not ig.blocks_tool_call(s),
              f"{ig.explain(s)} why={ig.why_blocked(s)}")
    # 与工具无关的疑问不该被拦（说明判据要"讨论信号 + 工具语义"双条件）
    unrelated = ["为什么会下雨", "今天几号", "你好", "解释一下闭包",
                 "这个函数为什么报错", "为什么会失败"]
    for s in unrelated:
        check(f"放行（非工具语境）：{s[:26]}", not ig.blocks_tool_call(s),
              f"{ig.explain(s)}")


def part_c_install_consult():
    print("\n-- C) 安装/配置类咨询（新增 learning 词） --")
    for s in ["技能怎么装", "如何安装技能", "怎么配置模型", "搜索技能怎么装"]:
        check(f"拦（问怎么装）：{s[:24]}", ig.blocks_tool_call(s), f"{ig.explain(s)}")
    # 真指令不能误伤
    for s in ["帮我装个技能", "安装这个技能", "装技能"]:
        check(f"放行（真让装）：{s[:24]}", not ig.blocks_tool_call(s), f"{ig.explain(s)}")


def part_d_needs_web_search():
    print("\n-- D) 联网搜索按需（P0-1） --")
    w = _ui_window()
    should_search = [
        "帮我搜一下最新AI新闻", "今天天气怎么样", "比特币价格",
        "查一下明天的汇率", "最近的 AI 政策有什么变化", "搜索一下这个技能",
    ]
    for s in should_search:
        check(f"搜：{s[:26]}", w._needs_web_search(s) is True)
    no_search = [
        "你好", "解释一下闭包", "为什么会自动搜索？", "别搜了",
        "这个函数为什么报错", "帮我写个爬虫", "做个视频", "今天心情不错",
        "为什么会自动创建定时任务？",
    ]
    for s in no_search:
        check(f"不搜：{s[:26]}", w._needs_web_search(s) is False,
              f"got={w._needs_web_search(s)}")


def part_e_layer1_routing():
    print("\n-- E) 层1 路由判决（端到端） --")
    w = _ui_window()

    def verdict(s):
        if w._message_needs_agent(s) and not w._message_is_statement_only(s):
            return "agent"
        if w._message_is_statement_only(s):
            return "chat"
        if w._message_is_advice_only(s):
            return "chat"
        if w._message_is_topic_only(s):
            return "chat"
        return "search" if w._needs_web_search(s) else "chat"

    expect = {
        "你好": "chat",
        "解释一下闭包": "chat",
        "为什么会自动搜索？": "chat",
        "为什么会自动创建定时任务？": "chat",
        "为什么打开文件会触发工具？": "chat",
        "今天天气怎么样": "search",
        "帮我搜一下最新AI新闻": "search",
        "帮我删掉昨天的临时文件": "agent",
        "帮我装个技能": "agent",
        "做个视频": "agent",
    }
    for s, exp in expect.items():
        got = verdict(s)
        # agent/chat/search 是三个互斥去向，这里只断言"不该去的别去"
        check(f"「{s[:24]}」→ {exp}", got == exp, f"实际 {got}")


def part_f_source_contract():
    print("\n-- F) 源码契约：接线与唯一来源 --")
    src = open(_UI_PATH, encoding="utf-8-sig").read()
    check("F1 _needs_web_search 已定义", "def _needs_web_search(self, text):" in src)
    # 两个发送入口都要接上
    check("F2 主发送入口接了按需搜索",
          src.count('self.cfg.get("search_enabled", True) and self._needs_web_search(') == 2,
          f"命中 {src.count(chr(39) + 'search_enabled' + chr(39))} 处 search_enabled")
    # F3 行级检查：不能有「无条件搜索」的 elif 分支（写成子串匹配会把修复注释也命中）
    _bare = [l for l in src.splitlines()
             if l.strip().startswith('elif self.cfg.get("search_enabled", True)')
             and "_needs_web_search" not in l]
    check("F3 不存在无条件 _do_search 分支", not _bare, f"残留 {len(_bare)} 行：{_bare[:2]}")
    check("F4 _needs_web_search 先过 intent_guard（防'搜索'二字误触）",
          "_ig.is_non_action_message(text)" in src[
              src.index("def _needs_web_search"):src.index("def _needs_web_search") + 2000])
    check("F5 _ACTION_HINTS 补了文件操作动词", '"删掉"' in src and '"重命名"' in src)
    check("F6 _ACTION_HINTS 补了'装个技能'变体",
          '"装个技能"' in src)
    # 判据口径：讨论工具用 _TOOL_ACTION_RE + _DISCUSS_KW 双条件
    ig_src = open(os.path.join(ROOT, "intent_guard.py"), encoding="utf-8-sig").read()
    check("F7 intent_guard 定义了双条件常量",
          "_DISCUSS_KW" in ig_src and "_TOOL_ACTION_RE" in ig_src)
    check("F8 discuss_tool 已接入 _reasons()",
          'out.append("discuss_tool")' in ig_src)
    check("F9 discuss_tool 有 imperative 反否决（且只看主句 _head）",
          "if has_imperative(_head):" in ig_src[
              ig_src.index("def is_discuss_tool_use"):ig_src.index("def is_discuss_tool_use") + 2000])


def part_g_negative():
    print("\n-- G) 负面验证：拆掉修复必须变红 --")
    ig_src = open(os.path.join(ROOT, "intent_guard.py"), encoding="utf-8-sig").read()
    tree = ast.parse(ig_src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "is_discuss_tool_use")
    body = ast.get_source_segment(ig_src, fn)

    # G1 把 imperative 反否决拆掉 → 混合句被误拦
    p1 = body.replace(
        "    if has_imperative(_head):\n"
        "        return False           # 主句里有明确祈使 → 真指令，不拦\n", "")
    check("G1 锚点命中", p1 != body, "锚点需随判据更新")
    if p1 != body:
        check("G1 拆掉 imperative 反否决 → 「会…，帮我关掉」被误拦（证明反否决必需）",
              ig.is_discuss_tool_use("为什么会自动搜索？帮我关掉它") is False)

    # G2 拆掉"工具语义"这一半条件 → 任何疑问都被拦（"为什么会下雨"也中）
    p2 = body.replace(
        "    if _TOOL_ACTION_RE.search(text):\n        return True\n"
        "    return any(k in text for k in _TOOL_DOMAIN_KW)",
        "    return True")
    check("G2 锚点命中", p2 != body)
    if p2 != body:
        check("G2 只留讨论信号就返回 → 「为什么会下雨」也会被拦（证明双条件必需）",
              ig.is_discuss_tool_use("为什么会下雨") is False
              and "为什么会下雨" in "为什么会下雨")

    # G3 搜索判据里去掉 intent_guard 前置 → "为什么会自动搜索？"又去搜
    ui_src = open(_UI_PATH, encoding="utf-8-sig").read()
    seg = ui_src[ui_src.index("def _needs_web_search"):]
    seg = seg[:seg.index("\n    def ", 10)]
    check("G3 _needs_web_search 确实含 intent_guard 前置",
          "_ig.is_non_action_message(text)" in seg)
    check("G3 去掉后该句会被判成要搜（证明前置必需）",
          any(k in "为什么会自动搜索？" for k in ("搜索",)))


def main():
    part_a_discuss_tool()
    part_b_no_overblock()
    part_c_install_consult()
    part_d_needs_web_search()
    part_e_layer1_routing()
    part_f_source_contract()
    part_g_negative()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
