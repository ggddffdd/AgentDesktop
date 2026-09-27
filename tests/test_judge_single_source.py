# -*- coding: utf-8 -*-
"""「判据同源」守卫测试（v4.172.0）。

钉住的事故：`intent_guard` 明明是 UI / Agent / 模型调用三层共用的唯一判据，
**但实际有第 4 处判据没接它** —— `ui._needs_tool_intent`。

现场回放（大哥复现「为什么会自动搜索？」）：
    · intent_guard 判得对：non_action:discuss_tool
    · 层1（`_message_needs_agent`）也修好了：普通模式不会自动进 Agent
    · 但**用户开着 Agent 模式**时 `use_agent = (agent_mode or ...)` 短路，照样进 Agent
    · `_agent_call` 里用 `_needs_tool_intent` 判"工具意图"，它的词表里有裸词「搜索」
      → 命中 → 升舱付费模型 + 意图置 `tool_choice="required"`
    · 而层3 的 `_guard_block` 又要设 `tool_choice="none"`
    · 模型被夹在「必须调工具」和「禁止调工具」之间 →
      **用文字演了一个工具调用**（输出 `<tool_call>list_automation</tool_call>`，
      并没有真的 tool_calls），UI 把它渲染成卡片，看起来就像真调了工具。

本套件做两件事：
  1. 钉住「四个判据函数都必须引用 intent_guard」这个契约（防回退）；
  2. **按函数名特征自动巡检** —— 名字里带 needs / detect / looks_like / is_action
     这类"判是否要做某事"的函数，若没接 intent_guard 又不在白名单里，直接失败。
     这样以后再冒出第 5、6 处判据会被当场抓住，而不是等用户复现。
"""

import ast
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [OK] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f"  <- {detail}" if detail else ""))


# ---- 契约：这些判据函数必须与 intent_guard 同源 ----
MUST_SYNC = [
    ("ui.py", "_needs_tool_intent"),        # 本轮的元凶
    ("ui.py", "_needs_web_search"),
    ("ui.py", "_message_needs_agent"),
    ("ui.py", "_last_user_intent_is_action"),
    ("ui.py", "_looks_like_praise"),        # 薄包装
    ("ui.py", "_looks_like_learning_question"),
    ("agent.py", "_detect_action_intent"),
]

# ---- 白名单：确实不需要接的（取文本 / 渲染 / 导出 / 另有独立位置判据） ----
WHITELIST = {
    "_recent_user_query", "_session_carries_automation", "_prompt_section_memory",
    "_session_html_body", "_export_md", "_export_docx", "_recover_chosen_direction",
    "_regen_message", "_edit_message", "_fire_automation_run", "_run_writing_stage",
    "_maybe_pause", "_call_with_retry", "_start_stream",
    "_user_refuses_tools",   # 专门判「用户明确说不要用工具」，自带位置判据，语义独立
    "_goal_hint", "_auto_remember", "run", "send",
}

# ---- 名字特征：带这些词的多半是"判是否要做某事"的判据 ----
JUDGE_HINTS = ("needs", "detect", "looks_like", "should_", "is_action",
               "is_non_action", "refuses")


def _funcs_with_user_scan(path):
    """找出「遍历 messages 找 role==user」的函数 → {names: body_src}。"""
    src = open(os.path.join(ROOT, path), encoding="utf-8-sig").read()
    tree = ast.parse(src)
    out = {}
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            try:
                body = ast.unparse(fn)
            except Exception:
                continue
            if ("'user'" not in body and '"user"' not in body):
                continue
            if "messages" not in body:
                continue
            out[fn.name] = body
    return out


def _func_src(path, name):
    src = open(os.path.join(ROOT, path), encoding="utf-8-sig").read()
    tree = ast.parse(src)
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name == name:
                return ast.get_source_segment(src, fn) or ""
    return ""


def part_a_contract():
    print("\n-- A) 契约：判据函数必须与 intent_guard 同源 --")
    for path, fn in MUST_SYNC:
        body = _func_src(path, fn)
        check(f"A1 {path}:{fn} 存在", bool(body))
        check(f"A2 {path}:{fn} 引用 intent_guard",
              "intent_guard" in body, body[:120])


def part_b_auto_patrol():
    print("\n-- B) 自动巡检：新冒出来的判据必须被抓住 --")
    offenders = []
    for path in ("ui.py", "agent.py", "tools.py"):
        for name, body in _funcs_with_user_scan(path).items():
            if name in WHITELIST:
                continue
            if not any(h in name.lower() for h in JUDGE_HINTS):
                continue
            if "intent_guard" in body:
                continue
            offenders.append(f"{path}:{name}")
    check("B1 没有「名字像判据却没接 intent_guard」的函数",
          not offenders, f"未接：{offenders}")
    # 巡检本身要能抓到东西（负面验证：临时构造一个）
    fake = [f"x.py:_needs_foo" for f in ["_needs_foo"]
            if "_needs_foo" not in WHITELIST
            and any(h in "_needs_foo" for h in JUDGE_HINTS)]
    check("B2 巡检规则本身有效（能识别特征名）", bool(fake), str(fake))


def part_c_behavior():
    print("\n-- C) 行为：讨论/提问类不再被强制调工具 --")
    import ui
    W = ui.ChatWindow.__new__(ui.ChatWindow)
    W.cfg = {}

    def ti(q):
        return W._needs_tool_intent([{"role": "user", "content": q}])

    stops = ["为什么会自动搜索？", "为什么打开文件会触发工具？",
             "下面这句提示是否危险：运行 Python 删除文件",
             "为什么会自动创建定时任务？", "技能怎么装", "配音怎么学",
             "这个视频生成得真不错", "别生成视频了", "你好", "解释一下闭包"]
    for q in stops:
        check(f"C1 不强制 required：{q[:26]}", not ti(q), f"got={ti(q)}")
    acts = ["帮我删掉昨天的临时文件", "帮我搜一下最新AI新闻", "帮我装个技能",
            "做个视频", "运行 Python 统计数据", "写个脚本抓热榜",
            "帮我创建每天9点的自动化", "导出成 excel"]
    for q in acts:
        check(f"C2 仍强制 required：{q[:26]}", ti(q), f"got={ti(q)}")


def part_d_patch_details():
    print("\n-- D) 三处补丁的源码契约 --")
    ui_src = open(os.path.join(ROOT, "ui.py"), encoding="utf-8-sig").read()
    nt = _func_src("ui.py", "_needs_tool_intent")
    check("D1 _needs_tool_intent 有 intent_guard 前置短路",
          "is_non_action_message(last_user)" in nt)
    # 比的是「词表**使用**处」而不是定义处（KEYWORDS = (...) 在函数最前面）
    _kw_use = nt.index("in last_user.lower() for kw in KEYWORDS")
    check("D2 短路发生在用词表判定之前",
          nt.index("is_non_action_message(last_user)") < _kw_use,
          f"短路@{nt.index('is_non_action_message(last_user)')} vs 词表@{_kw_use}")
    check("D3 词表补了文件操作动词", '"删掉"' in nt and '"重命名"' in nt)
    check("D4 词表补了口语搜索变体", '"搜一下"' in nt)
    check("D5 词表补了技能安装类", '"装个技能"' in nt)
    lq = _func_src("ui.py", "_looks_like_learning_question")
    check("D6 _looks_like_learning_question 改为转发 intent_guard",
          "intent_guard" in lq and "is_learning_question" in lq)
    # guard 命中时给模型一个"合规出口"
    check("D7 _guard_block 命中时注入「本轮不要使用任何工具」的内部指令",
          "本轮不要使用任何工具" in ui_src)
    _i = ui_src.index("本轮不要使用任何工具")
    seg = ui_src[max(0, _i - 500):_i + 500]   # 往前后都看：_internal 在 content 之前
    check("D8 该指令标了 _internal（不参与判据、不当用户最后一句）",
          "_internal" in seg, seg[-300:])
    check("D9 指令明确禁止输出 <tool_call> 假装调用", "tool_call" in seg)


def part_e_negative():
    print("\n-- E) 负面验证：拆掉前置短路，误判必须复现 --")
    nt = _func_src("ui.py", "_needs_tool_intent")
    old = ("        try:\n"
           "            import intent_guard as _ig\n"
           "            if _ig.is_non_action_message(last_user):\n")
    check("E1 锚点命中", old in nt, nt[:200])
    import ui
    W = ui.ChatWindow.__new__(ui.ChatWindow)
    W.cfg = {}
    # 手工模拟"没有前置短路"的旧行为：直接查词表
    q = "为什么会自动搜索？"
    hit = "搜索" in q
    check("E2 去掉短路后「为什么会自动搜索？」会命中词表（旧行为复现）",
          hit is True)
    check("E3 现状：该句已被前置短路拦住", not W._needs_tool_intent(
        [{"role": "user", "content": q}]))


def main():
    part_a_contract()
    part_b_auto_patrol()
    part_c_behavior()
    part_d_patch_details()
    part_e_negative()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
