# -*- coding: utf-8 -*-
"""v4.236.0 自动记忆外移扰动：反向照妖镜（改坏必红）。

搬运型改动的真事故不是「搬错」，而是搬完之后**闸门被顺手删掉**或
**本体变成两份**。所以三个 case 全部打在这两类静默退化上：

  M1 删「本轮」闸门（agent_memory_mixin.py）：去掉 `_seq > 0` 判断
     → 判据「历史工具消息（_seq=0）不算本轮 → 不触发」必须翻红
       （v4.169 修过的原坑：只看历史导致闲聊也被提炼成"事实"）
  M2 删隔离闸门（agent_memory_mixin.py）：去掉 `_isolated` 早退
     → 判据「隔离会话（导演台闲聊）不写长期记忆」必须翻红
  M3 断对外名字（agent.py）：import 里去掉 AUTO_REMEMBER_PROMPT
     → 判据「agent.AUTO_REMEMBER_PROMPT 仍可取」必须翻红
       （tests/test_memory_gate_196 就靠这个名字）

M1/M2 是**行为级**变异：判据用假 self 直调 mixin，靠「到底有没有去调 LLM」
来判断，不依赖字符串匹配，所以删掉闸门骗不过去。

沿用「直接变异 + _perturb_guard 还原」模式。原串从真实字节取（assert 唯一）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
JUDGE = os.path.join(ROOT, "tests", "test_agent_memory_mixin_236.py")

F_MIXIN = os.path.join(ROOT, "agent_memory_mixin.py")
F_AGENT = os.path.join(ROOT, "agent.py")

# ---- M1：删「本轮」闸门 ----
OLD_M1 = """        has_tool_msg = any(
            isinstance(msg, dict) and msg.get("role") == "tool"
            and isinstance(msg.get("_seq"), int) and msg["_seq"] > 0
            for msg in self.messages
        )"""
NEW_M1 = """        has_tool_msg = any(
            isinstance(msg, dict) and msg.get("role") == "tool"
            for msg in self.messages
        )"""

# ---- M2：删隔离会话闸门 ----
OLD_M2 = """        if getattr(self, "_isolated", False):
            return
"""
NEW_M2 = ""

# ---- M3：断对外名字（只 import 类，不再暴露提示词）----
OLD_M3 = "from agent_memory_mixin import AgentMemoryMixin, AUTO_REMEMBER_PROMPT"
NEW_M3 = "from agent_memory_mixin import AgentMemoryMixin"

CASES = [
    ("M1 删『本轮』闸门（_seq > 0）", F_MIXIN, OLD_M1, NEW_M1,
     ["历史工具消息（_seq=0）不算本轮"]),
    ("M2 删隔离会话闸门（_isolated 早退）", F_MIXIN, OLD_M2, NEW_M2,
     ["隔离会话（导演台闲聊）不写长期记忆"]),
    ("M3 agent.py 不再暴露 AUTO_REMEMBER_PROMPT", F_AGENT, OLD_M3, NEW_M3,
     ["agent.AUTO_REMEMBER_PROMPT 仍可取"]),
]


def run_judge():
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([F_MIXIN, F_AGENT])  # 三重还原 + 残留预检
    orig = {}
    for f in (F_MIXIN, F_AGENT):
        with open(f, "r", encoding="utf-8") as fh:
            orig[f] = fh.read()

    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            src = orig[target]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            assert src.count(old) == 1, \
                "old 串出现 %d 次，非唯一：%s" % (src.count(old), name)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(src.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and any(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(src)
    finally:
        for f, s in orig.items():
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(s)  # 最终兜底还原
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
