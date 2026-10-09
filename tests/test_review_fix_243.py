# -*- coding: utf-8 -*-
"""审查报告第一批修复（v4.243.0）验收判据。

覆盖第三方审查报告（2026-10-09）第一批三条 P1 修复：
  I-1 否定判据双实现：agent_text._neg_hit 委托 intent_guard.is_negation
      （含长度闸 + 约束句式闸），消灭「修了 is_negation 但下游绕过」的第二个真源。
  I-2 状态词裸词误伤：_route_force_tool 用共现判据 _is_status_query，恢复真指令强制路由。
  T-1 成败兜底误判：tool_contract._infer_ok 加精确失败前缀，停止「失败记成功」。
      （刻意保留兜底黑名单，不翻白名单 —— 避免 82 个工具大范围回归 + 破坏 SR2 基线。）

judge-first：本文件引用的是修复后的行为，实现前必红，实现后转绿。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_text  # noqa: E402
import intent  # noqa: E402
import tool_contract as tc  # noqa: E402

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


LONG_TASK = ("每日AI新闻抓取任务：从多个RSS源抓取今天的AI新闻，不要盲目上浏览器，"
             "优先RSS，整理成简报写入文件。内容不要编造日期或来源，不要据此判定行业趋势。")


def test_i1_negation_single_source():
    print("== I-1 否定判据单一真源（长任务不再被「不要」一票否决） ==")
    check("I-1 _neg_hit(长任务) 不再误判喊停",
          agent_text._neg_hit(LONG_TASK) is False,
          "got=%s" % agent_text._neg_hit(LONG_TASK))
    check("I-1 classify(长任务) 不再 negated",
          intent.classify(LONG_TASK).kind != "negated",
          "kind=%s" % intent.classify(LONG_TASK).kind)
    check("I-1 classify(长任务) 判 action（这是任务）",
          intent.classify(LONG_TASK).kind == "action",
          "kind=%s" % intent.classify(LONG_TASK).kind)
    check("I-1 _detect_action_intent(长任务) 恢复 True",
          agent_text._detect_action_intent(
              [{"role": "user", "content": LONG_TASK}]) is True,
          "got=%s" % agent_text._detect_action_intent(
              [{"role": "user", "content": LONG_TASK}]))
    # 回归保护：短喊停句仍被拦（委托后不得漏拦喊停）
    check("I-1 短喊停「别生成视频了」仍 negated",
          intent.classify("别生成视频了").kind == "negated",
          "kind=%s" % intent.classify("别生成视频了").kind)
    check("I-1 短喊停「取消生成视频」仍 _neg_hit",
          agent_text._neg_hit("取消生成视频") is True,
          "got=%s" % agent_text._neg_hit("取消生成视频"))
    check("I-1 约束句「抓取新闻，不要盲目上浏览器」不判喊停",
          agent_text._neg_hit("抓取新闻，不要盲目上浏览器") is False,
          "got=%s" % agent_text._neg_hit("抓取新闻，不要盲目上浏览器"))


def test_i2_status_query():
    print("== I-2 状态词裸词不再误伤真指令（_STATUS_KW → _is_status_query） ==")
    check("I-2 文案含「状态」仍路由图片",
          agent_text._route_force_tool("帮我生成一张封面图，文案是『状态拉满』") == "image_gen",
          "got=%s" % agent_text._route_force_tool("帮我生成一张封面图，文案是『状态拉满』"))
    check("I-2 主题含「进度」仍路由图片",
          agent_text._route_force_tool("帮我生成一张海报，主题是项目进度") == "image_gen",
          "got=%s" % agent_text._route_force_tool("帮我生成一张海报，主题是项目进度"))
    # 回归保护：真状态追问仍不强制
    check("I-2 「视频好了吗？」仍不强制",
          agent_text._route_force_tool("视频好了吗？") is None,
          "got=%s" % agent_text._route_force_tool("视频好了吗？"))
    check("I-2 「进度如何？」仍不强制",
          agent_text._route_force_tool("进度如何？") is None,
          "got=%s" % agent_text._route_force_tool("进度如何？"))
    check("I-2 classify「视频好了吗？」仍 status",
          intent.classify("视频好了吗？").kind == "status",
          "kind=%s" % intent.classify("视频好了吗？").kind)


def test_t1_infer_ok():
    print("== T-1 成败兜底不再把失败判成功 ==")
    for s in ["未知工具：foo_bar",
              "MCP 工具 [foo] 调用异常：connection reset",
              "该路径不在允许读取的目录内：D:/x",
              "内容为空",
              "未提供 url 参数",
              "搜索暂不可用",
              "当前没有军团执行记录（这三个工具只在军团运行时有数据）。",
              "本次执行还没有任何成员产出，无法读取。",
              "没有匹配到产出（查询条件 role='x'）"]:
        check("T-1 失败文案判 False：%s" % s[:22], tc._infer_ok(s) is False,
              "got=%s" % tc._infer_ok(s))
    # 正例：成功文案仍 True
    check("T-1 成功文案「已保存到…」仍 True",
          tc._infer_ok("已保存到 D:/x/y.md（128 字）") is True,
          "got=%s" % tc._infer_ok("已保存到 D:/x/y.md（128 字）"))
    check("T-1 成功文案「共找到 8 条结果」仍 True",
          tc._infer_ok("共找到 8 条结果") is True,
          "got=%s" % tc._infer_ok("共找到 8 条结果"))
    # 回归保护：兜底不翻白名单（SR2 基线，中性文案仍走默认成功）
    check("T-1 中性文案兜底仍 True（不翻白名单）",
          tc._infer_ok("随便一句不带成败词的话") is True,
          "got=%s" % tc._infer_ok("随便一句不带成败词的话"))


def main():
    test_i1_negation_single_source()
    test_i2_status_query()
    test_t1_infer_ok()
    print("\nREVIEW_FIX_243 PASS=%d FAIL=%d" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
