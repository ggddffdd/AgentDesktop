# -*- coding: utf-8 -*-
"""v4.186：Agent「只说不做」空转修复 —— 双向回归。

背景（2026-09-30，大哥实录）：小臭对「对自己进行一次自检，只看不改」连回
「收到。那我按你改完的状态复核一遍」却不调任何工具。

根因（三步排查 + 真机探针实锤，非猜测）：
  ① 意图漏词：`_ACTION_KEYWORDS` 缺诊断/检视动词（自检/排查/诊断/巡检…），
     自检请求一路走到 `_detect_action_intent` 末尾 `return any(k in _ACTION_KEYWORDS)`
     → False。这一个 False 同时关掉 step-1 force_required 与 nudge 两道防线。
  ② 兜底死代码：`_looks_like_promise`（承诺却不行动）定义后**从未被调用**，
     这本是独立于 needs_action 的第三道防线，等于没有。
  ③ （相关）推理模型（DeepSeek thinking）下 force_tool 只注入软指令、不硬发
     tool_choice，可被无视——需运行态确认，属已知限制，不阻塞本套件。

修复：`_ACTION_KEYWORDS` 补诊断动词；主/续跑两处纯文本分支各接一段「承诺兜底」
（`_looks_like_promise` + `_nudged` 一轮一次闸 + `_nudge_count` 上限，防死循环）。

本套件钉三件事（双向，收紧判据必须同时防空转命中与正常误伤）：
  A 诊断类 request → needs_action=True（修复前是 False）
  B 承诺兜底：真空转回复 _looks_like_promise=True；正常回复 =False（0 误伤）
  C 兜底确已接线（不再是死代码）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def main():
    import agent
    import agent_text  # v4.216.0：判据族已从 AgentWorker 拆到 agent_text.py

    def na(t):
        return agent_text._detect_action_intent([{"role": "user", "content": t}])

    def pr(t):
        return agent_text._looks_like_promise(t)

    print("=== A) 诊断/检视类 request → needs_action=True（修复前漏词 = False）===")
    for t in ("对自己进行一次自检，只看不改", "自检一下", "排查最近的ERROR日志",
              "帮我诊断一下这个报错", "巡检系统状态", "盘点可用工具",
              "核验能力清单", "扫描一下异常"):
        check(f"诊断类转 True：{t[:16]}", na(t) is True, f"needs_action={na(t)}")

    print("=== B1) 承诺兜底：真空转回复 → _looks_like_promise=True（截图语料）===")
    for t in ("好的！我来做一次全面自检，看看有哪些潜在问题。",
              "收到。那我检查一下日志和数据库状态",
              "继续自检，先排查最近的错误",
              "我来排查一下最近的 ERROR 日志",
              "继续。我诊断一下数据库连接",
              "我先核验一遍工具是否可用",
              "我来复核一下你的方案"):
        check(f"命中空转：{t[:16]}", pr(t) is True, f"promise={pr(t)}")

    print("=== B2) 正常回复 → _looks_like_promise=False（0 误伤）===")
    for t in ("下一步建议加强巡视力度，下季度重点盯 10kV 线路。",
              "已完成 3 个模块的自检，全部正常。报告已保存到 ~/产物/report.md。",
              "我确认过了，你的方案没问题，可以执行。",
              "这是源码片段：\n```python\nprint(1)\n```\n跑通无报错。",
              "让我看看你的数据再决定。",
              "我来总结今天的成果：完成了 A、B、C 三项。",
              "结论：搜索功能正常，返回 200，以下是详细日志……"):
        check(f"不误伤：{t[:14]}", pr(t) is False, f"promise={pr(t)}")

    print("=== B3) 讨论/评价类 request → needs_action 仍 False（补词不误伤）===")
    for t in ("这个视频生成得真不错", "为什么刚才自动调用了工具",
              "评价下我刚才那张图", "QWen 能不能离线用"):
        check(f"讨论仍 False：{t[:14]}", na(t) is False, f"needs_action={na(t)}")

    print("=== C) 承诺兜底确已接线（不再是死代码）===")
    src = (ROOT / "agent.py").read_text(encoding="utf-8-sig")
    atsrc = (ROOT / "agent_text.py").read_text(encoding="utf-8-sig")  # v4.216.0 新家
    check("_looks_like_promise 有定义", "def _looks_like_promise" in atsrc)
    _uses = src.count("agent_text._looks_like_promise(")
    check("_looks_like_promise 有 ≥2 处调用（主分支+续跑镜像）",
          _uses >= 2, f"调用点={_uses}")
    check("_nudged 一轮一次闸在兜底内", "_nudged = True" in src)
    check("_ACTION_KEYWORDS 已含诊断动词", '"自检"' in atsrc and '"排查"' in atsrc)

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
