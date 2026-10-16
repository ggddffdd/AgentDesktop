# -*- coding: utf-8 -*-
"""v4.284.0 P1a 判据：第三方转述族禁工具（judge-first）。

彻查实证（2026-10-16，25 例实测）
--------------------------------
「我朋友说你能写稿」→ intent.classify 判 kind=action、needs_action=True
——用户在**转述别人怎么说**，不是在给自己下指令。放到 Agent 里带全量
工具放模型自由发挥，就是 v4.283.0「H吗.」的同款事故条件（模型自行宣布
「现在就写」并调 write_file）。与否认授意族（v4.283.0）同属「指令不是
用户下的」危险模式：那句是否认，这句是转述。

修复（intent_guard.is_third_party_report）
------------------------------------------
  ① 闭集转述标记：「X说」人称/来源闭集 + 「听说你/据说你」听说族；
  ② has_imperative 一票反否决——「我朋友说你能写稿，帮我写一个」里
     「帮我」是用户自己的真指令，必须放行（与 praise/learning/howto/ref
     同一纪律：那几族也全指望这一个放行口）。
接入 `_reasons` → 同时作用于层 1/2（is_non_action_message：UI 不自动进
Agent、Agent 不置 _needs_action）与层 3（blocks_tool_call：强制
tool_choice="none"，模型只能纯文字回应）。

取舍（写入代码注释）：误拦只退化为纯文字回复（模型仍可文字作答/口头
开写），漏拦会让 app 继续自作主张落盘——故标记一律闭集、放行口一律
has_imperative，不开「转述后用户祈使」的复杂切分（开集坑，本项目已
栽过五次）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s%s" % (name, ("  —— " + detail) if detail else ""))


def main():
    import intent
    import intent_guard as ig

    # ===== A. 转述句：禁工具（彻查 25 例里的漏网族）=====
    block_cases = [
        "我朋友说你能写稿",
        "我朋友说你能写稿，是真的吗",
        "有人说你能写文案",
        "我同事说小臭玩AI很厉害",
        "群里说你能做视频",
        "别人说你会剪辑",
        "我老婆说你能写周报",
        "听说你写稿很快",
        "据说你剪辑很强",
        "传闻你写稿很强",
        "我同学说你能写小说",
        "我领导说你能做报表",
        "粉丝说你会写标题",
    ]
    for c in block_cases:
        check("A1 转述被拦（%s）" % c,
              ig.is_third_party_report(c) and ig.blocks_tool_call(c),
              "third_party=%s block=%s" % (ig.is_third_party_report(c),
                                           ig.blocks_tool_call(c)))
    check("A2 拦因是 third_party（首个案例）",
          "third_party" in ig.why_blocked("我朋友说你能写稿"),
          "why=%s" % ig.why_blocked("我朋友说你能写稿"))

    # ===== B. 转述 + 用户自己的真指令：放行（has_imperative 反否决）=====
    allow_cases = [
        "我朋友说你能写稿，帮我写一个",
        "我朋友说你能写稿，然后帮我写一个",
        "我朋友说你能写稿，继续",
        "听说你写稿很快，请写",
        "有人说你能写稿，帮我生成一版",
        "别人说你会剪辑，帮我做一条",
        "我老婆说你能写周报，写一段",
    ]
    for c in allow_cases:
        check("B1 转述+真指令放行（%s）" % c,
              not ig.is_third_party_report(c) and not ig.blocks_tool_call(c),
              "third_party=%s why=%s" % (ig.is_third_party_report(c),
                                         ig.why_blocked(c)))

    # ===== C. 普通指令/问句不受影响（不误扩族）=====
    # 注意：不进本列表的两种——「视频怎么生成的？」本就被 howto 族拦、
    # 「在吗」本就被 presence 族拦（v4.283.0），那是各自家族的既有正确行为。
    neutral = [
        "帮我写个稿", "写一段周报", "今天的新闻帮我整理一下",
        "任务进度如何", "在吗，帮我写个稿",
        "我朋友过生日，帮我写个祝福语",       # 「我朋友」出现但不是转述 AI 能力
        "请大家阅读以下通知",                  # 「大家」但不是「大家说」
    ]
    for c in neutral:
        check("C1 普通句不拦（%s）" % c, not ig.blocks_tool_call(c),
              "why=%s" % ig.why_blocked(c))
    check("C2 别的家族的既有拦截不受影响（howto）",
          ig.blocks_tool_call("视频怎么生成的？"))
    check("C3 别的家族的既有拦截不受影响（presence）",
          ig.blocks_tool_call("在吗"))

    # ===== D. 路由层联动：kind / needs_action 真的变了 =====
    # 这是 v4.283.0 事故链的第一环：转述句曾被判 action 自动进 Agent
    it = intent.classify("我朋友说你能写稿")
    check("D1 转述句不再 needs_action", not it.needs_action,
          "kind=%s needs_action=%s" % (it.kind, it.needs_action))
    # D2：转述前缀不改变真指令的路由结论——与去掉前缀的基线完全一致
    # （「帮我写一个」基线即 kind=content/needs_action=False：纯文字写作
    #  本来就不需要工具，这不是本族该管的事）
    it2 = intent.classify("我朋友说你能写稿，帮我写一个")
    it_base = intent.classify("帮我写一个")
    check("D2 转述+真指令路由等同基线",
          it2.kind == it_base.kind and it2.needs_action == it_base.needs_action
          and it2.force_tool == it_base.force_tool,
          "got kind=%s na=%s force=%s vs base kind=%s na=%s force=%s"
          % (it2.kind, it2.needs_action, it2.force_tool,
             it_base.kind, it_base.needs_action, it_base.force_tool))
    it3 = intent.classify("我朋友说你能写稿，帮我搜一下昆明天气")
    check("D3 转述+要工具的指令仍 needs_action（真功能不被关掉）",
          it3.needs_action and it3.force_tool == "web_search",
          "kind=%s needs_action=%s force=%s" % (it3.kind, it3.needs_action,
                                                it3.force_tool))

    # ===== E. 两层判据同口（is_non_action_message == blocks_tool_call）=====
    for c in block_cases + allow_cases:
        check("E1 两层一致（%s）" % c[:18],
              ig.is_non_action_message(c) == ig.blocks_tool_call(c))

    # ===== F. 结构护栏：接线在位（防被后人摘除）=====
    src = open(os.path.join(ROOT, "intent_guard.py"), encoding="utf-8").read()
    check("F1 is_third_party_report 已定义", "def is_third_party_report(text):" in src)
    check("F2 已接入 _reasons",
          "if is_third_party_report(t):" in src
          and 'out.append("third_party")' in src)
    check("F3 has_imperative 反否决在位",
          "if has_imperative(text):" in src
          and "return False           # 主句里有用户自己的真指令 → 放行" in src)

    print("THIRD_PARTY PASS=%d FAIL=%d" % (_p, _f))
    sys.exit(0 if _f == 0 else 1)


if __name__ == "__main__":
    main()
