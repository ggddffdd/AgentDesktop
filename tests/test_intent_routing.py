# -*- coding: utf-8 -*-
"""「对话误调用工具」回归测试 —— 统一非指令判据 + 三层接入。

钉住的 BUG 链（v4.164.0 审查报告《对话误调用工具 BUG 修改方案》）：

    普通对话输入
      → ui._message_needs_agent()      误判为媒体生成需求 → 自动切 Agent
      → agent._detect_action_intent()  认为需要执行动作
      → 模型调用层                      非推理模型被 tool_choice="required" 强制调用
      → video_gen / image_gen 真的跑起来（花钱、还答非所问）

根因不是模型发疯，而是**三套路由判断各修了一部分评价句豁免，
却从未共用同一条判据**。本套件覆盖两层：

  A. 判据本体（intent_guard）——报告 §6 的 8 条用例 + 边界
  B. 接线（源码契约）—— 三层确实都接上了同一条判据，
     且模型调用层的保险**先于** force_tool / required 分支生效
     （推理模型与非推理模型走的是不同分支，保险必须在分支之前，
       否则只会挡住其中一边）。

纯标准库；B 部分用 AST + 源码定位，不导入 ui/agent（避开 PySide6）。
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import intent_guard as ig   # noqa: E402

_p = _f = 0

_UI = os.path.join(ROOT, "ui.py")
_AGENT = os.path.join(ROOT, "agent.py")


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _read(path):
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8-sig")


def _find_one(lines, pred, what):
    """按行定位唯一命中行；命中数 != 1 直接报错（防止锚点漂移后静默失效）。"""
    hits = [i for i, l in enumerate(lines) if pred(l)]
    assert len(hits) == 1, f"{what}: 期望唯一命中，实际 {len(hits)} 处"
    return hits[0]


def _method_span(lines, name):
    """返回类方法 [start, end) 行区间（按缩进判断方法体结束）。"""
    sig = re.compile(rf"^(\s*)def {re.escape(name)}\(")
    starts = [i for i, l in enumerate(lines) if sig.match(l)]
    assert len(starts) >= 1, f"未找到方法 {name}"
    i = starts[0]
    ind = len(lines[i]) - len(lines[i].lstrip())
    j = i + 1
    while j < len(lines):
        l = lines[j]
        if l.strip() and (len(l) - len(l.lstrip())) <= ind:
            break
        j += 1
    return i, j


# ==========================================================================
# A. 判据本体
# ==========================================================================

def part_a():
    print("=== A) 判据本体（intent_guard）===")

    print("\n-- A1 报告 §6 必备用例：评价句必须不触发工具 --")
    for t in ("这个视频生成得真不错",
              "刚才图片做得太惊艳了",
              "刚才那个图片做得很惊艳",
              "视频制作得挺好",
              "Agnes 的视频效果真强",
              "这能力颠覆认知",
              "效果真好"):
        check(f"评价句不进 Agent/不调工具：{t}",
              ig.is_non_action_message(t) and ig.blocks_tool_call(t),
              f"explain={ig.explain(t)}")

    print("\n-- A2 报告 §6 必备用例：真指令必须照常执行 --")
    for t in ("请生成一个视频", "帮我做一张海报", "做个视频", "生成一段视频",
              "帮我做个口播视频", "帮我做一张封面"):
        check(f"真指令不被误伤：{t}",
              not ig.is_non_action_message(t) and not ig.blocks_tool_call(t),
              f"explain={ig.explain(t)}")

    print("\n-- A3 报告 §6：疑问句 / 否定句 --")
    check("疑问句『视频怎么生成的？』不调生成工具",
          ig.blocks_tool_call("视频怎么生成的？"))
    check("疑问句『图片怎么做的』不调生成工具",
          ig.blocks_tool_call("图片怎么做的"))
    check("否定句『别生成视频了』不调生成工具",
          ig.blocks_tool_call("别生成视频了"))
    check("否定句『视频先不做了』不调生成工具",
          ig.blocks_tool_call("视频先不做了"))
    check("否定句『取消生成视频』不调生成工具",
          ig.blocks_tool_call("取消生成视频"))

    print("\n-- A4 评价 + 祈使 = 真指令（不能一刀切） --")
    for t in ("做得不错，继续做一版", "效果真好，再来一版", "生成得不错，改一下封面"):
        check(f"评价后续接指令仍算真指令：{t}",
              not ig.is_non_action_message(t), f"explain={ig.explain(t)}")

    print("\n-- A5 引用 / 质疑语境 --")
    for t in ("你刚才说的生成个视频，是BUG", "分析下生成视频这件事"):
        check(f"引用/质疑语境不调工具：{t}", ig.blocks_tool_call(t),
              f"explain={ig.explain(t)}")
    check("生成 + 后续分析（分隔符）仍是真指令",
          not ig.is_non_action_message("生成个视频，顺便分析下这个题材"))

    print("\n-- A6 否定词误杀防护（歧义词不当地否定） --")
    for t in ("别出心裁做个视频", "特别想做视频", "辨别一下这张图", "识别图片里的文字"):
        check(f"不误判成否定：{t}", not ig.is_negation(t), f"explain={ig.explain(t)}")

    print("\n-- A7 状态追问不参与拦截（Agent 模式要能用 task_status） --")
    check("『任务进度如何』不拦工具", not ig.blocks_tool_call("任务进度如何"),
          f"explain={ig.explain('任务进度如何')}")

    print("\n-- A8 内部注入消息不得被判据拖走（nudge 里带『不要』） --")
    nudge = ("你刚才只描述了计划，却没有调用任何工具——这等于什么都没做，任务失败。"
             "现在必须立即调用真实工具来完成任务：检索资料用 web_search、"
             "写文件用 write_file。禁止再输出承诺性文字。"
             "若任务确实已全部完成，请直接给出最终成果与产物路径，不要再空头承诺。")
    # v4.168.3：长度闸上线后，真实 nudge（168 字）**不再**命中判据。
    # 这是判据变准了（长文里的「不要」本就是约束/句式，不是喊停），不是退化。
    check("真实 nudge 文案（168 字）现在不再命中判据（长度闸生效）",
          not ig.blocks_tool_call(nudge), f"explain={ig.explain(nudge)}")
    # 但 _internal 标记**仍然必需**：nudge 文案随时会改，短文案 + 含「不要」照样命中。
    # 用它来验证机制，而不是赌某条具体文案恰好命中。
    _short_inject = "不要再空头承诺了"
    check("短注入文案仍会命中判据（所以 _internal 跳过机制必须保留）",
          ig.blocks_tool_call(_short_inject), f"explain={ig.explain(_short_inject)}")

    print("\n-- A9 explain() 结构化输出 --")
    e = ig.explain("这个视频生成得真不错")
    check("explain 命中 praise", e.get("praise") is True, f"{e}")
    check("explain 带 imperative 字段", "imperative" in e)
    check("why_blocked 返回原因列表",
          "praise" in ig.why_blocked("这个视频生成得真不错"))


# ==========================================================================
# B. 接线（源码契约）
# ==========================================================================

def part_b():
    print("\n=== B) 三层接入（源码契约）===")

    ui = _read(_UI)
    ag = _read(_AGENT)
    at = _read(os.path.join(ROOT, "agent_text.py"))  # v4.216.0 判据族新家
    ui_lines = ui.splitlines()
    ag_lines = ag.splitlines()
    at_lines = at.splitlines()

    ast.parse(ui)
    ast.parse(ag)
    ast.parse(at)

    print("\n-- B1 第 1 层：UI 自动路由前置于媒体判定 --")
    s, e = _method_span(ui_lines, "_message_needs_agent")
    body = "\n".join(ui_lines[s:e])
    check("_message_needs_agent 调用共享判据",
          "intent_guard" in body and "is_non_action_message" in body)
    # 必须是一条**真语句**，不是被 False/条件短路掉的死代码
    # （历史教训：只断言出现子串，把 `if False and X:` 这种注入也能判绿）
    check("判据调用是真语句（未被短路）",
          any(l.strip() == "if _ig.is_non_action_message(text):"
              for l in ui_lines[s:e]),
          f"方法体内未找到裸判据语句；实际含该子串的行："
          f"{[l.strip() for l in ui_lines[s:e] if 'is_non_action_message' in l]}")
    check("判据调用早于 _is_media_gen_request",
          body.index("intent_guard") < body.index("self._is_media_gen_request("),
          "判据必须前置于媒体生成判定，否则评价句仍会被当生成请求")
    check("_looks_like_praise 已改为共享判据薄包装",
          "is_praise" in "\n".join(
              ui_lines[_method_span(ui_lines, "_looks_like_praise")[0]:
                       _method_span(ui_lines, "_looks_like_praise")[1]]))

    print("\n-- B2 第 2 层：Agent 意图判断前置于生成意图 --")
    s, e = _method_span(at_lines, "_detect_action_intent")  # v4.216.0：已迁 agent_text
    body = "\n".join(at_lines[s:e])
    check("_detect_action_intent 调用共享判据",
          "intent_guard" in body and "is_non_action_message" in body)
    check("判据调用是真语句（未被短路）",
          any(l.strip() == "if _ig.is_non_action_message(text):"
              for l in at_lines[s:e]),
          f"实际含该子串的行："
          f"{[l.strip() for l in at_lines[s:e] if 'is_non_action_message' in l]}")
    check("判据调用早于 _gen_intent",
          body.index("intent_guard") < body.index("_gen_intent("))

    print("\n-- B3 第 3 层：模型调用层最终保险 --")
    s, e = _method_span(ui_lines, "_agent_call") if any(
        l.startswith("    def _agent_call") for l in ui_lines) else (0, len(ui_lines))
    check("_agent_call 存在", s != 0 or "    def _agent_call" in ui,
          "未找到 _agent_call 方法签名")
    check("模型调用层调用 blocks_tool_call",
          "blocks_tool_call" in ui)
    gi = _find_one(ui_lines, lambda l: "blocks_tool_call(" in l,
                   "blocks_tool_call 调用点")
    # v4.186.0（P1-3 修后）：guard 分支带 not force_tool 条件——
    # 已验证在工具表内的强制工具不再被 guard 静默反盖成 none。
    ft = _find_one(ui_lines,
                   lambda l: l.strip() == "if _guard_block and not force_tool:",
                   "if _guard_block 分支")
    check("保险分支在 force_tool 之前",
          gi < ft, f"blocks@{gi+1} 应在 if _guard_block@{ft+1} 之前")
    check("保险分支强制 tool_choice=none",
          ui_lines[ft + 1].strip() == 'body["tool_choice"] = "none"',
          f"实际下一行: {ui_lines[ft+1].strip()!r}")
    order = []
    for i, l in enumerate(ui_lines):
        if l.strip() == "if _guard_block and not force_tool:":
            order.append(("guard", i))
        elif l.strip() == "elif force_tool:":
            order.append(("force_tool", i))
        elif l.strip().startswith("elif (force_required or redo or _tool_intent)"):
            order.append(("required", i))
    check("分支顺序：guard → force_tool → required",
          [k for k, _ in order] == ["guard", "force_tool", "required"],
          f"实际 {[k for k, _ in order]}")
    check("强制 required 一行仍被保留（真指令路径不受影响）",
          'body["tool_choice"] = "required"' in ui)

    print("\n-- B4 内部注入消息带 _internal 标记（不被判据误伤） --")
    n_nudge = len(re.findall(r"self\._AGENT_NUDGE,\s*\n\s*\"_internal\": True",
                             ag))
    check("nudge 注入点都标了 _internal（4 处：主+续跑各 2）", n_nudge == 4, f"实际 {n_nudge}")
    check("伪造工具指令标了 _internal",
          re.search(r'"content": self\._AGENT_FAKE_TOOL_INSTR,\s*\n\s*'
                    r'"_internal": True', ag) is not None)
    s, e = _method_span(ag_lines, "_is_sync_writable")
    check("_internal 消息不回写 session",
          "_internal" in "\n".join(ag_lines[s:e]))

    print("\n-- B5 层 3 取「最后一句」时跳过 _internal --")
    s2 = _find_one(ui_lines,
                   lambda l: "for msg in reversed(messages):" in l
                   and "_REDO_KEYWORDS" in "\n".join(
                       ui_lines[ui_lines.index(l):ui_lines.index(l) + 30]),
                   "redo 扫描循环")
    seg = "\n".join(ui_lines[s2:s2 + 30])
    check("扫描循环跳过 _internal",
          "_internal" in seg, f"实际片段:\n{seg}")
    check("跳过发生在取 last_user 之前（否则会误取内部消息）",
          seg.index("_internal") < seg.index("last_user = msg.get(\"content\""),
          f"实际片段:\n{seg}")

    print("\n-- B6 _internal 不上行给 API --")
    check("body 构造前清洗 _internal",
          re.search(r'if any\(isinstance\(m, dict\) and "_internal" in m '
                    r'for m in messages\):', ui) is not None)


# ==========================================================================
# C. 负面验证（判据一旦退化必须变红）
# ==========================================================================

def part_c():
    print("\n=== C) 负面验证 ===")
    # 模拟「评价句判据被移除」——动补结构必须能独立认出来
    check("动补结构独立可识别",
          bool(ig._PRAISE_COMPLEMENT_RE.search("视频生成得真不错")))
    check("动补结构不误伤祈使句",
          not bool(ig._PRAISE_COMPLEMENT_RE.search("做一张好看的图")))

    # 只要有人把 is_non_action_message 简化成「只查关键词表」，动补句就会漏
    check("关键词表单独不足以覆盖动补句（所以正则必须留着）",
          not any(k in "视频生成得真不错" for k in ("颠覆认知", "真好用", "太强了")))

    # blocks_tool_call 与 is_non_action_message 都必须对同一批评价句生效
    same = all(ig.blocks_tool_call(t) == ig.is_non_action_message(t)
               for t in ("这个视频生成得真不错", "别生成视频了", "视频怎么生成的？",
                         "请生成一个视频", "帮我做一张海报"))
    check("两侧判据在当前用例集上一致", same)


def main():
    part_a()
    part_b()
    part_c()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
