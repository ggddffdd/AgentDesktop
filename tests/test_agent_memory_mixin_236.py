# -*- coding: utf-8 -*-
"""v4.236.0 判据：自动记忆块外移到 AgentMemoryMixin 后，契约与行为都不能变。

背景：agent.py 涨到 **2393 行**，距 `tests/test_split_216.py` 的 D2 红线
（`agent.py < 2400`）只剩 **7 行**余量。按硬纪律 13「处理 = 外移，不抬阈值」，
把尾部 164 行的自动记忆块（提示词 + `_auto_remember` + `_parse_remember_facts`）
搬进 `agent_memory_mixin.py`，主类只留继承（沿用 v4.225/226 抽 mixin 的老套路）。

搬运型改动的真事故**不是搬错**，而是这三类静默退化：

  ① 名字还在，但**本体复制了两份**（提示词日后必然漂移，且改哪份都说不清）；
  ② 方法搬走了，**副作用闸门被顺手删掉** —— 本轮没调工具也会去提炼记忆，
     用户只是闲聊两句，记忆库里却多一条推断出来的"事实"（v4.169 修过的原坑）；
  ③ 对外名字消失 —— `tests/test_memory_gate_196` 走的是
     `agent.AUTO_REMEMBER_PROMPT`，搬走后取不到就直接炸。

所以判据分三组：**契约 / 本体唯一 / 行为闸门**。
行为闸门用假 self 直调 Mixin，**不碰真实记忆库**（测试绝不污染用户记忆）。
"""
import io
import os
import sys
import tokenize
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent as AG  # noqa: E402
from agent_memory_mixin import AgentMemoryMixin  # noqa: E402
from agent_memory_mixin import AUTO_REMEMBER_PROMPT as MIXIN_PROMPT  # noqa: E402

_N_PASS = 0
_N_FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global _N_PASS, _N_FAIL
    if cond:
        _N_PASS += 1
        print(f"  [PASS] {name}")
    else:
        _N_FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


def _strip_comments(src: str) -> str:
    """按 COMMENT 位置把注释挖成空格：列位不变、行号不漂（P11）。"""
    lines = src.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            sl, sc = tok.start
            el, ec = tok.end
            if 1 <= sl <= len(lines):
                ln = lines[sl - 1]
                lines[sl - 1] = ln[:sc] + " " * max(0, ec - sc) + ln[ec:]
    except Exception:
        pass
    return "".join(lines)


AGENT_SRC = (ROOT / "agent.py").read_text(encoding="utf-8", errors="ignore")
AGENT_CODE = _strip_comments(AGENT_SRC)
MIXIN_SRC = (ROOT / "agent_memory_mixin.py").read_text(encoding="utf-8", errors="ignore")
MIXIN_CODE = _strip_comments(MIXIN_SRC)


# ---------- 假 self：只实现 _auto_remember 用到的三个属性/方法 ----------
class _W(AgentMemoryMixin):
    def __init__(self, messages, isolated=False):
        self.messages = messages
        self._isolated = isolated
        self.emitted = []

    def _emit_status(self, s):
        self.emitted.append(s)


class _MW:
    """假 mw：只记录「到底有没有去调 LLM」。返回空内容 → 解析不到条目 → 提前 return，
    因此永远不会走到写记忆库那一步（测试绝不污染用户记忆）。"""

    def __init__(self):
        self.called = 0

    def _agent_call(self, msgs, tools, on_delta=None):
        self.called += 1
        return {"content": ""}


def main():
    print("v4.236.0 自动记忆外移判据（契约 / 本体唯一 / 行为闸门）")

    # ---------- A) 对外契约 ----------
    print("\n-- A) 对外契约：搬走本体，名字必须还在 --")
    check("★ AgentWorker 继承了 AgentMemoryMixin",
          any(c.__name__ == "AgentMemoryMixin" for c in AG.AgentWorker.__mro__),
          f"MRO={[c.__name__ for c in AG.AgentWorker.__mro__]}")
    check("★ agent.AUTO_REMEMBER_PROMPT 仍可取（test_memory_gate_196 靠它）",
          hasattr(AG, "AUTO_REMEMBER_PROMPT"), "对外名字没了 → 依赖它的判据直接炸")
    check("★ agent.AUTO_REMEMBER_PROMPT 与 mixin 里是同一份（不是复制）",
          getattr(AG, "AUTO_REMEMBER_PROMPT", None) is MIXIN_PROMPT,
          "不是同一对象 → 两份并存，日后必漂移")
    check("★ AgentWorker._auto_remember 就是 Mixin 的那个函数（继承而来）",
          getattr(AG.AgentWorker, "_auto_remember", None)
          is AgentMemoryMixin._auto_remember, "方法被另行定义 → 搬了个寂寞")

    # ---------- B) 本体唯一：agent.py 里不得再有 ----------
    print("\n-- B) 本体唯一：不能复制两份 --")
    check("★ agent.py 里不再定义 AUTO_REMEMBER_PROMPT",
          "AUTO_REMEMBER_PROMPT = " not in AGENT_CODE,
          "仍在定义 → 两份并存必漂移")
    check("★ agent.py 里不再定义 _auto_remember",
          "def _auto_remember" not in AGENT_CODE, "没真搬走")
    check("★ agent.py 里不再定义 _parse_remember_facts",
          "def _parse_remember_facts" not in AGENT_CODE, "没真搬走")
    for nm in ("AUTO_REMEMBER_PROMPT = ", "def _auto_remember",
               "def _parse_remember_facts"):
        check(f"★ 本体在 agent_memory_mixin.py（{nm.strip()}）",
              nm in MIXIN_CODE, f"mixin 里找不到 {nm}")
    check("★ agent.py 真的 import 了这个 mixin（接线在位）",
          "from agent_memory_mixin import" in AGENT_CODE, "没接上")

    # ---------- C) 行为闸门（不碰真实记忆库）----------
    print("\n-- C) 行为闸门：搬走后副作用口径必须照旧 --")
    mw = _MW()
    w_idle = _W([{"role": "user", "content": "今天天气不错"}])
    w_idle._auto_remember(mw)
    check("★ 本轮没调工具 → 不去提炼记忆（不产生无令副作用）",
          mw.called == 0, f"仍调了 LLM {mw.called} 次 → 闲聊也会污染记忆库")

    w_tool = _W([{"role": "tool", "content": "x", "_seq": 1}])
    w_tool._auto_remember(mw)
    check("★ 本轮真调过工具 → 会去提炼（闸门不是一刀切死锁）",
          mw.called == 1, f"调用次数={mw.called}")

    before = mw.called
    w_hist = _W([{"role": "tool", "content": "旧的工具消息", "_seq": 0}])
    w_hist._auto_remember(mw)
    check("★ 历史工具消息（_seq=0）不算本轮 → 不触发（v4.169 原坑）",
          mw.called == before, f"被历史消息误触发（{before}→{mw.called}）")

    before = mw.called
    w_iso = _W([{"role": "tool", "content": "x", "_seq": 2}], isolated=True)
    w_iso._auto_remember(mw)
    check("★ 隔离会话（导演台闲聊）不写长期记忆",
          mw.called == before, f"隔离失效（{before}→{mw.called}）")

    # ---------- D) 解析能力照旧 ----------
    print("\n-- D) 解析能力：搬走后仍解析得动各种脏格式 --")
    f = AgentMemoryMixin._parse_remember_facts
    check("★ 空输入返回空列表", f("") == [] and f(None) == [])
    got = f('[{"topic":"t","category":"c","content":"正文"}]')
    check("★ 结构化对象解析（topic/category/content）",
          len(got) == 1 and got[0]["topic"] == "t" and got[0]["content"] == "正文",
          f"got={got}")
    got = f('```json\n[{"content":"带代码块"}]\n```')
    check("★ 代码块包裹也能剥开", len(got) == 1 and got[0]["content"] == "带代码块",
          f"got={got}")
    got = f("- 纯文本一条")
    check("★ 非 JSON 兜底按行解析", len(got) == 1 and got[0]["content"] == "纯文本一条",
          f"got={got}")
    got = f('[{"content":"c","source":"tool","ev":"EV#3","confidence":0.9}]')
    check("★ 准入判据字段透传（source/ev/confidence 一个都不能丢）",
          got and got[0].get("source") == "tool" and got[0].get("ev") == "EV#3"
          and got[0].get("confidence") == 0.9, f"got={got}")

    print("\n" + "=" * 62)
    print(f"汇总：PASS={_N_PASS} FAIL={_N_FAIL}")
    if FAILED:
        for x in FAILED:
            print(f"  × {x}")
    return 1 if _N_FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
