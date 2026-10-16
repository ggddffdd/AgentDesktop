# -*- coding: utf-8 -*-
"""v4.283.0 防「模型无指令自己加戏」验收判据（judge-first）。

事故还原（2026-10-16 大哥抓包，sid 20260904230501729「这7天真实充实啊」）：
  msg138 用户「H吗.」      → kind=unknown、guard 不拦 → 带全量工具+150条历史放模型发挥
  msg139 模型自行宣布「我理解你要的是 30 秒口播稿……现在就写，落盘给你」→ 调 write_file（用户取消）
  msg140 用户「今天的自动化任务做得很顺畅。」
  msg141 模型编造「没写文件 ✅」总结（定时任务实际写盘成功，EV#262/265 在案）
  msg142 write_file 口播稿 → 用户取消
  msg146 用户「我没有叫你写口播啊」→ 旧判据 kind=action、needs_action=True（否决漏拦）
  msg147 模型**再次**调 write_file 同路径 → 用户再次取消

三处修复（v4.283.0）：
  A. intent_guard.is_presence_check：寒暄/存在性呼叫（在吗/H吗./睡了吗/hi…）
     → blocks_tool_call=True，层 3 强制 tool_choice="none"，模型只能纯文字待命；
  B. intent_guard._NEG_DENIAL_OF_AUTH：否认授意（我没有叫你/没让你/谁让你…）
     → is_negation 一票否决，classify kind=negated、needs_action=False、force_tool=None；
  C. config.AGENT_SYS_APPEND 汇报纪律：禁止声称「没写文件/没执行」除非本会话有取消/失败证据，
     看不到别的会话（含定时任务会话）就明说，禁止编造；落盘不擅自另写副本。

本套件不 import ui（不需要 Qt），只读源码文本做结构性断言 + 纯函数行为断言。
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
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s%s" % (name, ("  —— " + detail) if detail else ""))


def main():
    import intent
    import intent_guard as ig
    import config

    # ===== A. 寒暄/存在性呼叫：禁止拿工具 =====
    presence_cases = ["在吗。", "H吗.", "H吗", "在吗", "在？", "睡了吗", "在么",
                      "在不在", "hi", "hello", "在吗？", "在么?"]
    for c in presence_cases:
        check("A1 寒暄被拦（%s）" % c,
              ig.is_presence_check(c) and ig.blocks_tool_call(c),
              "is_presence=%s block=%s" % (ig.is_presence_check(c),
                                            ig.blocks_tool_call(c)))
        check("A2 寒暄拦因是 presence（%s）" % c,
              "presence" in ig.why_blocked(c), "why=%s" % ig.why_blocked(c))

    # 反向：带真指令的句子不许误拦（「在吗」出现在长句/带任务动词里不是寒暄）
    non_presence = ["在吗，帮我写个稿", "帮我写个稿", "在吗？帮我查一下今天的新闻",
                    "写", "跑", "在吗在吗，写个周报"]
    for c in non_presence:
        check("A3 真指令不误拦（%s）" % c,
              not ig.is_presence_check(c) and not ig.blocks_tool_call(c),
              "presence=%s block=%s" % (ig.is_presence_check(c),
                                        ig.blocks_tool_call(c)))

    # ===== B. 否认授意：一票否决 =====
    denial_cases = ["我没有叫你写口播啊", "我没让你写文件", "没叫你", "谁叫你写这个的",
                    "我可没让你写", "我没让"]
    for c in denial_cases:
        check("B1 否认授意判否定（%s）" % c, ig.is_negation(c))
        r = intent.classify(c, None, None)
        check("B2 否认授意→negated 且无动作（%s）" % c,
              r.kind == intent.KIND_NEGATED and not r.needs_action
              and r.force_tool is None,
              "kind=%s needs_action=%s force_tool=%s"
              % (r.kind, r.needs_action, r.force_tool))
        check("B3 否认授意禁工具（%s）" % c, ig.blocks_tool_call(c))

    # 反向：约束句式（任务祈使后的「没让你」）不是喊停——任务不许瘫
    constraint = "抓取新闻，没让你用浏览器"
    check("B4 约束句式不误杀（%s）" % constraint,
          not ig.is_negation(constraint),
          "is_negation=True（约束被误判成喊停，任务会瘫）")
    r = intent.classify(constraint, None, None)
    check("B5 约束句式保留动作意图", r.kind != intent.KIND_NEGATED,
          "kind=%s" % r.kind)
    # 长任务说明书里的否认授字样例不误杀（长度闸 >40 字）
    long_task = ("整理成简报写入文件，每条标题加一句话摘要和来源，"
                 "我没让你编造日期或来源，必须如实标注")
    check("B6 长任务说明书不误杀（%d 字）" % len(long_task),
          not ig.is_negation(long_task), "is_negation=True")

    # ===== 原案复现：消息序逐轮断言 =====
    # msg138「H吗.」→ 禁工具（加戏入口关闭）
    check("C1 原案 msg138「H吗.」禁工具", ig.blocks_tool_call("H吗."))
    # msg146「我没有叫你写口播啊」→ 禁工具 + negated（不能再发起写盘）
    r = intent.classify("我没有叫你写口播啊", None, None)
    check("C2 原案 msg146 禁工具且 negated",
          ig.blocks_tool_call("我没有叫你写口播啊")
          and r.kind == intent.KIND_NEGATED)
    # msg140「今天的自动化任务做得很顺畅。」→ 本就拦（praise），确认仍在
    check("C3 原案 msg140 仍被 praise 拦",
          ig.blocks_tool_call("今天的自动化任务做得很顺畅。")
          and "praise" in ig.why_blocked("今天的自动化任务做得很顺畅。"))

    # ===== C. 汇报纪律提示词在位 =====
    sys_append = config.AGENT_SYS_APPEND
    check("D1 汇报纪律已写入 AGENT_SYS_APPEND",
          "v4.283.0" in sys_append and "汇报纪律" in sys_append)
    check("D2 禁止编造执行事实", "禁止凭印象编造" in sys_append)
    check("D3 落盘不擅自另写副本", "不要擅自另写副本" in sys_append)

    # ===== 结构性护栏：判据入口接线在位（防被后人摘除） =====
    guard_src = open(os.path.join(ROOT, "intent_guard.py"), encoding="utf-8").read()
    check("E1 is_presence_check 已接入 _reasons",
          'if is_presence_check(t):' in guard_src
          and 'out.append("presence")' in guard_src)
    check("E2 否认授义词表已接入 _neg_hits",
          "_NEG_DENIAL_OF_AUTH" in guard_src
          and "for k in _NEG_DENIAL_OF_AUTH:" in guard_src)

    print("NO_SELF_ACTION PASS=%d FAIL=%d" % (_p, _f))
    sys.exit(0 if _f == 0 else 1)


if __name__ == "__main__":
    main()
