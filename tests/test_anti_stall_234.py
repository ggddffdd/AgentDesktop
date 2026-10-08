# -*- coding: utf-8 -*-
"""v4.234 防空转三闸门修复 judge-first（A/B/C 全覆盖）。

根因（三道闸门在「agnes-3.0-flash + 多步搜索」下叠加失效）：
  A｜agent.py 承诺兜底带死条件 `not self._any_tool_executed`：本轮只要跑过一次工具，
     兜底永久失效 → 截图末轮「搜过一次后说『我直接…再补一次搜』」从缝里漏走。
  B｜agent_text._looks_like_promise 词表过窄 + 短文本限制：截图三句原话 0 命中。
  C｜ui.py _is_reasoning_model 把 agnes-3.x 误判为「拒 required 的推理模型」
     （v4.162 基于 DeepSeek 思考模式 400 的错误类推、从未实测）→ 首步 tool_choice="required"
     整段被跳过 → 模型自由裸奔。

v4.234 探针实测（2026-10-08）：agnes-3.0-flash 在 api.agnes-ai.cn / apihub 下，
auto/required/specific_function 三种 tool_choice 均返回 200 并正常调工具。**根本不 400。**

judge-first：本文件在改源码前应为 FAIL（A 组方法不存在、C 组函数不存在、B 组词表未扩）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import agent as _agent
import agent_text as at

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  ✅ %s" % name)
    else:
        _f += 1
        print("  ❌ %s  %s" % (name, detail))


_promise_fn = getattr(_agent.AgentWorker, "_promise_nudge_should_fire", None)
_reject_fn = getattr(at, "model_rejects_tool_required", None)
MAX_FORCE_RETRIES = getattr(_agent, "MAX_FORCE_RETRIES", 3)


def main():
    # ===== B：_looks_like_promise（纯函数） =====
    # 正向：截图三句真实语料（修复前应为 False → 红）
    check("B1 再补一次搜：命中", at._looks_like_promise("我直接打开浏览器，再补一次搜索："))
    check("B2 直接查：命中", at._looks_like_promise("我把范围放宽，直接查一下剩余节点："))
    check("B3 联网搜索：命中", at._looks_like_promise("我理解你要查的，现在联网搜索相关文档："))
    # 反向：正常回答不得误伤（回归）
    check("B4 排查结论不命中", not at._looks_like_promise("这是排查结论：三项均正常，无需处理。"))
    check("B5 我确认过不命中(回归)", not at._looks_like_promise("我确认过了，没问题。"))
    _long = "结论如下：" + "x" * 420
    check("B6 长文不命中(回归)", not at._looks_like_promise(_long))

    # ===== A：_promise_nudge_should_fire（抽方法，去死条件） =====
    if _promise_fn is None:
        check("A1 已跑工具+空承诺应触发", False, "方法未实现")
        check("A2 已跑工具+正常结论不触发", False, "方法未实现")
        check("A3 未跑工具+空承诺仍触发", False, "方法未实现")
        check("A4 已 nudged 不触发", False, "方法未实现")
        check("A5 达上限不触发", False, "方法未实现")
    else:
        def _mk(any_tool=True, nudged=False, ncount=0):
            w = _agent.AgentWorker.__new__(_agent.AgentWorker)
            w._nudged = nudged
            w._nudge_count = ncount
            w._any_tool_executed = any_tool
            return w

        # A1：已跑过工具 + 空承诺 → 修复后必须触发（原死条件 not _any_tool_executed 挡住）
        w = _mk(any_tool=True)
        check("A1 已跑工具+空承诺应触发", w._promise_nudge_should_fire("我直接打开浏览器，再补一次搜索："))
        # A2：已跑工具 + 正常结论 → 不触发
        w = _mk(any_tool=True)
        check("A2 已跑工具+正常结论不触发",
              not w._promise_nudge_should_fire("这是最终结论，无需再查。"))
        # A3：未跑工具 + 空承诺 → 仍触发（原也能，防回归）
        w = _mk(any_tool=False)
        check("A3 未跑工具+空承诺仍触发", w._promise_nudge_should_fire("我这就去搜索相关内容："))
        # A4：已 nudged → 一轮一闸不触发
        w = _mk(any_tool=True, nudged=True)
        check("A4 已 nudged 不触发", not w._promise_nudge_should_fire("再补一次搜索："))
        # A5：nudge 达上限 → 不触发
        w = _mk(any_tool=True, ncount=MAX_FORCE_RETRIES)
        check("A5 达上限不触发", not w._promise_nudge_should_fire("再补一次搜索："))

    # ===== C：model_rejects_tool_required（纯函数，迁自 ui） =====
    if _reject_fn is None:
        check("C1 agnes-3.0 国内站不拒", False, "函数未实现")
        check("C2 agnes-3.0 apihub不拒", False, "函数未实现")
        check("C3 deepseek官方拒", False, "函数未实现")
        check("C4 agnes-2.5不拒", False, "函数未实现")
        check("C5 deepseek思考特征拒", False, "函数未实现")
    else:
        check("C1 agnes-3.0 国内站不拒",
              not _reject_fn("agnes-3.0-flash", "https://api.agnes-ai.cn/v1"))
        check("C2 agnes-3.0 apihub不拒",
              not _reject_fn("agnes-3.0-flash", "https://apihub.agnes-ai.cn/v1"))
        check("C3 deepseek官方拒",
              _reject_fn("deepseek-flash", "https://api.deepseek.com"))
        check("C4 agnes-2.5不拒",
              not _reject_fn("agnes-2.5-flash", "https://api.agnes-ai.cn/v1"))
        check("C5 deepseek思考特征拒",
              _reject_fn("deepseek-v4-flash-vision-exp", "https://api.deepseek.com"))

    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
