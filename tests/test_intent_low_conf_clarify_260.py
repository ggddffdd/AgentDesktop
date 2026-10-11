# -*- coding: utf-8 -*-
"""test_intent_low_conf_clarify_260.py
意图识别护栏（v4.260.x，借鉴业界 LLM-Router 第二判据 + 本项目 fail-closed 哲学）

背景（真实事故复盘）
--------------------
用户说「这种代码做的动画你会做吗」「你会做视频吗」，规则层仅靠关键词（动画/视频）
命中 video_gen，但用户是在**确认能力 / 提问**，并非明确下指令。旧 classify 的
confidence 字段「不参与任何行为决策」（见 intent.py 文件头），于是下游直接调
video_gen 空转（与 tools.py 的 video_gen 熔断是同一类根因）。

护栏（借鉴报告点 1+3）
---------------------
1. 规则层 fail-closed 回退：classify 命中「高风险生成工具(video_gen/image_gen) +
   无显式强制(force=None) + 非指令语境(question/unknown/discuss) + confidence<阈值」
   → 置 needs_clarification，反问确认意图，不直接调。
2. LLM 第二判据架构位：route_second_opinion(text, intent, llm_router) —— 调用方可选
   传入轻量 LLM 二分类细化；未接入时规则层回退已足够防误调（零网络依赖）。

judge-first：本文件先红（改前 classify 不置 clarify）→ 实现后绿。
行为级：只测 classify / route_second_opinion 的返回值，不盯源码字符串。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import intent


def _check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
        return True
    print("  FAIL  %s   %s" % (name, detail))
    return False


def run():
    passed = failed = 0

    # ---------- 护栏 1：规则层低置信高风险回退（should 现在为红→实现后绿）----------
    print("[A] 模糊疑问句命中高风险生成工具 → 须澄清，不直接调")

    c1 = intent.classify("这种代码做的动画你会做吗")
    if _check("A1 动画疑问→needs_clarification",
              c1.needs_clarification,
              "kind=%s req=%s conf=%.2f" % (c1.kind, c1.requested_tools, c1.confidence)):
        passed += 1
    else:
        failed += 1
    if _check("A1b 澄清选项含 生成/只是问问",
              ("confirm_generate", "just_asking") == tuple(
                  (o[0] for o in c1.clarify_options)) if c1.clarify_options else False):
        passed += 1
    else:
        failed += 1

    c2 = intent.classify("这种动画你会做吗")
    if _check("A2 动画疑问(简)→needs_clarification", c2.needs_clarification,
              "kind=%s req=%s" % (c2.kind, c2.requested_tools)):
        passed += 1
    else:
        failed += 1

    c3 = intent.classify("你会做视频吗")
    if _check("A3 视频疑问→needs_clarification", c3.needs_clarification,
              "kind=%s req=%s" % (c3.kind, c3.requested_tools)):
        passed += 1
    else:
        failed += 1

    c4 = intent.classify("你会做图片吗")
    if _check("A4 图片疑问→needs_clarification", c4.needs_clarification,
              "kind=%s req=%s" % (c4.kind, c4.requested_tools)):
        passed += 1
    else:
        failed += 1

    c5 = intent.classify("这种动画你能不能用代码做出来")
    if _check("A5 动画能力确认疑问→needs_clarification", c5.needs_clarification,
              "kind=%s req=%s" % (c5.kind, c5.requested_tools)):
        passed += 1
    else:
        failed += 1

    # ---------- 反向：明确指令绝不被误拦（应为绿，验证只加不减）----------
    print("[B] 明确指令 → 不触发回退（保障零回归）")

    b1 = intent.classify("帮我用代码生成一个动画")
    if _check("B1 显式生成指令→不澄清(force 优先)", not b1.needs_clarification,
              "force=%s clarify=%s" % (b1.force_tool, b1.needs_clarification)):
        passed += 1
    else:
        failed += 1

    b2 = intent.classify("做个视频")
    if _check("B2 裸生成指令→不澄清(force 优先)", not b2.needs_clarification,
              "force=%s" % b2.force_tool):
        passed += 1
    else:
        failed += 1

    b3 = intent.classify("帮我画一张图")
    if _check("B3 显式生图指令→不澄清", not b3.needs_clarification,
              "force=%s" % b3.force_tool):
        passed += 1
    else:
        failed += 1

    # ---------- 护栏 2：LLM 第二判据架构位（route_second_opinion）----------
    print("[C] route_second_opinion（LLM 第二判据可选扩展位）")

    guard_base = intent.classify("这种动画你会做吗")
    if not guard_base.needs_clarification:
        print("  (跳过 C：规则层未触发澄清，先实现护栏 1)")
    else:
        # 每个子测试用独立 fresh intent：route_second_opinion 会 mutate 入参的
        # needs_clarification，共享 base 会污染后续用例（C1 消除澄清后 C2/C3/C4
        # 拿到的已是脏 base）。判据自隔离，不依赖调用顺序。
        _SENT = "这种动画你会做吗"

        # C1: LLM 确认要生成 → 消澄清
        class _MockConfirm(object):
            def __call__(self, text):
                return {"confirm": True}
        b_c1 = intent.classify(_SENT)
        r1 = intent.route_second_opinion(_SENT, b_c1, _MockConfirm())
        if _check("C1 LLM confirm=True→澄清消除", not r1.needs_clarification):
            passed += 1
        else:
            failed += 1

        # C2: LLM 否认 → 保留澄清（fresh intent）
        class _MockDeny(object):
            def __call__(self, text):
                return {"confirm": False}
        b_c2 = intent.classify(_SENT)
        r2 = intent.route_second_opinion(_SENT, b_c2, _MockDeny())
        if _check("C2 LLM confirm=False→保留澄清", r2.needs_clarification):
            passed += 1
        else:
            failed += 1

        # C3: llm_router=None → 不抛、原样返回（fresh intent）
        b_c3 = intent.classify(_SENT)
        try:
            r3 = intent.route_second_opinion(_SENT, b_c3, None)
            if _check("C3 llm_router=None→不崩溃且保留澄清",
                      r3.needs_clarification and r3 is b_c3):
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print("  FAIL  C3 llm_router=None 抛异常: %r" % e)
            failed += 1

        # C4: llm_router 抛异常 → fail-open 不阻断（fresh intent）
        class _MockBoom(object):
            def __call__(self, text):
                raise RuntimeError("net down")
        b_c4 = intent.classify(_SENT)
        try:
            r4 = intent.route_second_opinion(_SENT, b_c4, _MockBoom())
            if _check("C4 llm_router 抛异常→fail-open 保留澄清", r4.needs_clarification):
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print("  FAIL  C4 异常未兜住: %r" % e)
            failed += 1

    print("\nPASS=%d FAIL=%d" % (passed, failed))
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
