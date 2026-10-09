# -*- coding: utf-8 -*-
"""审查报告第二批修复（v4.244.0）验收判据。

覆盖第三方审查报告（2026-10-09）第二批 + 第一批遗留尾巴：
  I-5 澄清话术泄露工具名 → 改用中文 desc
  I-6 intent_dag 裸字键「画」/ 路径词「电脑」「下载」→ 删
  I-4 DISCUSS 分支被 not req 架空 → 讨论优先于候选工具
  I-3 _REF_KW 裸词「什么时候/让你/讨论/评价」→ 加回指锚点
  T-2 并发路径二次 decide 漏 task_risk → 复用批次级 decs
  I-2尾巴 _is_question「如何/怎么」裸词误判标题内容 → 剥离书名号/引号

judge-first：本文件引用的是修复后的行为，实现前必红，实现后转绿。
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_text  # noqa: E402
import intent  # noqa: E402
import intent_dag  # noqa: E402

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, detail))


def test_i5_clarify_desc():
    print("== I-5 澄清话术不再泄露工具名 ==")
    it = intent.classify("帮我把视频和图片都处理一下")
    check("I-5 触发澄清", it.needs_clarification is True, str(it.needs_clarification))
    check("I-5 clarify_reason 不含内部工具名",
          "image_gen" not in it.clarify_reason and "video_gen" not in it.clarify_reason,
          it.clarify_reason)
    check("I-5 clarify_reason 含中文描述",
          ("图片" in it.clarify_reason or "视频" in it.clarify_reason),
          it.clarify_reason)
    check("I-5 clarify_options 仍带工具名（供内部/UI 选择）",
          any(o[0] == "image_gen" for o in it.clarify_options),
          str(it.clarify_options))


def test_i6_dag_no_bare():
    print("== I-6 intent_dag 不再凭空造节点 ==")
    dag1 = intent_dag.decompose_intent(
        "搜索一下最近的动画片信息", intent.classify("搜索一下最近的动画片信息"))
    check("I-6 「动画片」不再造 image_gen 节点",
          dag1 is None or all(n.tool != "image_gen" for n in dag1.nodes),
          str([(n.tool) for n in dag1.nodes] if dag1 else None))
    dag2 = intent_dag.decompose_intent(
        "帮我在电脑上搜一下今天的AI新闻", intent.classify("帮我在电脑上搜一下今天的AI新闻"))
    check("I-6 「电脑」不再造 write_file 节点",
          dag2 is None or all(n.tool != "write_file" for n in dag2.nodes),
          str([(n.tool) for n in dag2.nodes] if dag2 else None))
    dag3 = intent_dag.decompose_intent(
        "生成一张图并存到D盘", intent.classify("生成一张图并存到D盘"))
    check("I-6 真落盘「存到D盘」仍造 write_file",
          dag3 is not None and any(n.tool == "write_file" for n in dag3.nodes),
          str([(n.tool) for n in dag3.nodes] if dag3 else None))


def test_i4_discuss_first():
    print("== I-4 讨论句不再被判 action ==")
    check("I-4 「分析下这个视频」→ discuss",
          intent.classify("分析下这个视频").kind == "discuss",
          intent.classify("分析下这个视频").kind)
    check("I-4 「聊聊刚才那张图」→ discuss（保持）",
          intent.classify("聊聊刚才那张图").kind == "discuss",
          intent.classify("聊聊刚才那张图").kind)
    check("I-4 「帮我生成一张封面图」→ action（保持）",
          intent.classify("帮我生成一张封面图").kind == "action",
          intent.classify("帮我生成一张封面图").kind)


def test_i3_ref_anchor():
    print("== I-3 回指词不再误伤真指令 ==")
    check("I-3 「明天什么时候下雨」仍路由搜索",
          agent_text._route_force_tool("帮我搜一下明天什么时候下雨") == "web_search",
          str(agent_text._route_force_tool("帮我搜一下明天什么时候下雨")))
    check("I-3 「生成图，讨论AI」仍路由图片",
          agent_text._route_force_tool("生成一张图，讨论一下AI的未来") == "image_gen",
          str(agent_text._route_force_tool("生成一张图，讨论一下AI的未来")))
    check("I-3 「做视频，评价方案」不再因「评价」被拦",
          agent_text._is_ref_context("做个视频，评价一下这三个方案哪个好") is False,
          str(agent_text._is_ref_context("做个视频，评价一下这三个方案哪个好")))
    check("I-3 「做视频，评价方案」仍路由视频",
          agent_text._route_force_tool("做个视频，评价一下这三个方案") == "video_gen",
          str(agent_text._route_force_tool("做个视频，评价一下这三个方案")))
    # 回归保护：强引用句仍拦
    check("I-3 「我什么时候让你生成视频了」仍拦",
          agent_text._route_force_tool("我什么时候让你生成视频了") is None,
          str(agent_text._route_force_tool("我什么时候让你生成视频了")))


def test_t2_concurrent_dec():
    print("== T-2 并发路径复用批次级 dec ==")
    import agent
    _rc = inspect.getsource(agent.AgentWorker._run_concurrent)
    check("T-2 worker 内不再二次 decide",
          "_engine.decide(" not in _rc and "engine.decide(" not in _rc)
    _ec = inspect.getsource(agent.AgentWorker._exec_tool_calls)
    check("T-2 批次级 decs 传给并发", "zip(tool_calls, decs)" in _ec)


def test_i2_tail_is_question_strip():
    print("== I-2尾巴 _is_question 剥离书名号/引号 ==")
    check("I-2尾巴 「标题叫《如何用AI赚钱》」不再判疑问",
          agent_text._is_question("帮我生成一个视频，标题叫《如何用AI赚钱》") is False,
          str(agent_text._is_question("帮我生成一个视频，标题叫《如何用AI赚钱》")))
    check("I-2尾巴 「标题叫《如何用AI赚钱》」仍路由视频",
          agent_text._route_force_tool("帮我生成一个视频，标题叫《如何用AI赚钱》") == "video_gen",
          str(agent_text._route_force_tool("帮我生成一个视频，标题叫《如何用AI赚钱》")))
    # 回归保护：真疑问句仍判疑问
    check("I-2尾巴 「如何做视频？」仍疑问",
          agent_text._is_question("如何做视频？") is True,
          str(agent_text._is_question("如何做视频？")))


def main():
    test_i5_clarify_desc()
    test_i6_dag_no_bare()
    test_i4_discuss_first()
    test_i3_ref_anchor()
    test_t2_concurrent_dec()
    test_i2_tail_is_question_strip()
    print("\nREVIEW_FIX_244 PASS=%d FAIL=%d" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
