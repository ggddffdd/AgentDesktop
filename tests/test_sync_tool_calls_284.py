# -*- coding: utf-8 -*-
"""v4.284.0 P0 判据：工具调用历史回写不丢 tool_calls（judge-first）。

彻查根因（2026-10-16，sessions.json 实证）
------------------------------------------
agent 先 `stream_commit.emit(content)` → ui `_on_stream_commit` 把 assistant
**纯文本**写进 session.messages → agent 侧再 append「同 content + tool_calls」
的完整版 → `_sync_to_session` 旧指纹 `(role, content, tool_call_id)`
**不含 tool_calls 维度** → 带调用版本被判重跳过 → 续跑/「继续」时
`_repair_tool_pairs` 把孤儿 tool 结果整批丢弃 → 模型看不到自己调过什么
（失忆重干 / 重复调工具 / 编造进度）。

实证数字：盘上 **123 条 tool 结果 vs 仅 5 条 assistant.tool_calls**。

修复（agent.py `_sync_to_session` / `_sync_sig`）
------------------------------------------------
  1. 指纹升级四维：`(role, content, tool_call_id, tool_calls 规范化序列化)`；
  2. **同文缺调用时合并而非跳过**：session 侧已有 stream_commit 写的纯文本
     assistant（base 三维相同、缺 tool_calls）→ 把 agent 侧 tool_calls 补进
     那条既有消息（不重复正文，tool 结果有主可配）；
  3. 兜底路径（session 无 _seq）补**边界合并**：反向扫描的 start_idx 落在带
     调用消息之后时，循环遍历不到它，先合并边界消息本身。

本套件为行为级：假 session + `AgentWorker.__new__` 真调 `_sync_to_session`，
并接 `ui_msg._repair_tool_pairs` 断言孤儿真的复活（旧行为此处为 0）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s%s" % (name, ("  —— " + detail) if detail else ""))


TC1 = {"id": "call_1", "type": "function",
       "function": {"name": "web_search", "arguments": "{\"query\": \"昆明天气\"}"}}


class _Sess(object):
    def __init__(self, msgs):
        self.sid = "test_sync_284"
        self.messages = msgs


class _Store(object):
    pass


class _MW(object):
    store = _Store()


def _new_worker(messages, seq_ctr):
    import agent
    w = agent.AgentWorker.__new__(agent.AgentWorker)
    w._isolated = False
    w._bound_sid = "test_sync_284"
    w._seq_ctr = seq_ctr
    w.messages = messages
    return w


def _tool_results(msgs):
    return [m for m in msgs if m.get("role") == "tool"]


def main():
    import agent
    import automation
    from ui_msg import _repair_tool_pairs

    # ===== A. 主路径：同文合并（stream_commit 已写纯文本）=====
    # session 已有 _seq>0（此前同步过，走主路径按序号对齐），且 ui 侧刚把
    # assistant 文本写进去（无 _seq）
    sess = _Sess([
        {"role": "user", "content": "在吗", "_seq": 1},
        {"role": "assistant", "content": "先搜一下"},      # stream_commit 纯文本
    ])
    w = _new_worker([
        {"role": "user", "content": "在吗", "_seq": 1},
        {"role": "assistant", "content": "先搜一下", "_seq": 2, "tool_calls": [dict(TC1)]},
        {"role": "tool", "content": "搜索结果A", "_seq": 3, "tool_call_id": "call_1"},
        {"role": "assistant", "content": "搜完了", "_seq": 4},
    ], 4)
    automation.bound_session = lambda store, sid: sess
    w._sync_to_session(_MW())

    check("A1 主路径不重复追加正文（4 条而非 5 条）", len(sess.messages) == 4,
          "len=%d" % len(sess.messages))
    _asst = sess.messages[1]
    check("A2 主路径同文 assistant 合并进 tool_calls", bool(_asst.get("tool_calls")),
          "tool_calls=%r" % (_asst.get("tool_calls"),))
    check("A3 主路径合并的是同一个调用 id",
          _asst.get("tool_calls") and _asst["tool_calls"][0]["id"] == "call_1")
    check("A4 主路径 tool 结果已回写", len(_tool_results(sess.messages)) == 1)
    _kept = _tool_results(_repair_tool_pairs(sess.messages))
    check("A5 主路径孤儿复活：_repair_tool_pairs 后 tool 结果幸存（旧行为为 0）",
          len(_kept) == 1, "kept=%d" % len(_kept))
    check("A6 主路径合并后 assistant 的 tool_calls 与回写 tool 配对",
          any(tc.get("id") == "call_1" for tc in (_asst.get("tool_calls") or [])))

    # ===== B. 幂等：二次同步不增长、不改写 =====
    _before = [dict(m) for m in sess.messages]
    w._sync_to_session(_MW())
    check("B1 二次同步不新增消息", len(sess.messages) == len(_before),
          "len=%d vs %d" % (len(sess.messages), len(_before)))
    check("B2 二次同步不重复挂 tool_calls",
          len(sess.messages[1].get("tool_calls") or []) == 1)

    # ===== C. 兜底路径：session 无 _seq（首批/旧会话）=====
    sess2 = _Sess([
        {"role": "user", "content": "在吗"},              # 无 _seq
        {"role": "assistant", "content": "先搜一下"},      # 无 _seq，stream_commit 纯文本
    ])
    w2 = _new_worker([
        {"role": "user", "content": "在吗", "_seq": 1},
        {"role": "assistant", "content": "先搜一下", "_seq": 2, "tool_calls": [dict(TC1)]},
        {"role": "tool", "content": "搜索结果A", "_seq": 3, "tool_call_id": "call_1"},
        {"role": "assistant", "content": "搜完了", "_seq": 4},
    ], 4)
    automation.bound_session = lambda store, sid: sess2
    w2._sync_to_session(_MW())
    check("C1 兜底路径 tool 结果回写", len(_tool_results(sess2.messages)) == 1,
          "tools=%d" % len(_tool_results(sess2.messages)))
    check("C2 兜底路径边界消息合并 tool_calls（边界落在带调用消息之后）",
          bool(sess2.messages[1].get("tool_calls")),
          "tool_calls=%r" % (sess2.messages[1].get("tool_calls"),))
    _kept2 = _tool_results(_repair_tool_pairs(sess2.messages))
    check("C3 兜底路径孤儿同样复活", len(_kept2) == 1, "kept=%d" % len(_kept2))

    # ===== D. 纯文本轮不受影响（无 tool_calls 时不合并不复制）=====
    sess3 = _Sess([
        {"role": "user", "content": "在吗", "_seq": 0},
        {"role": "assistant", "content": "在的，说吧"},
    ])
    w3 = _new_worker([
        {"role": "user", "content": "在吗", "_seq": 0},
        {"role": "assistant", "content": "在的，说吧", "_seq": 1},
    ], 1)
    automation.bound_session = lambda store, sid: sess3
    w3._sync_to_session(_MW())
    check("D1 纯文本同内容不重复追加", len(sess3.messages) == 2,
          "len=%d" % len(sess3.messages))
    check("D2 纯文本消息不被误挂 tool_calls",
          not sess3.messages[1].get("tool_calls"))

    # ===== E. _sync_sig 纯函数性质 =====
    sig = agent.AgentWorker._sync_sig
    _a = {"role": "assistant", "content": "先搜一下", "tool_calls": [dict(TC1)]}
    _b = {"role": "assistant", "content": "先搜一下"}
    check("E1 指纹含 tool_calls 维度（有无调用指纹不同）", sig(_a) != sig(_b))
    check("E2 同调用指纹稳定（可比较）", sig(_a) == sig(dict(_a)))
    check("E3 base 三维一致（合并定位前提）", sig(_a)[:3] == sig(_b)[:3])
    check("E4 content None 与缺省同指纹",
          sig({"role": "assistant"}) == sig({"role": "assistant", "content": None}))
    check("E5 畸形 tool_calls 不炸（fail-open 退化为无调用）",
          sig({"role": "assistant", "tool_calls": ["x"]})[3] == "")

    # ===== F. 结构护栏：合并逻辑在位（防被后人简化回跳过）=====
    # v4.284.0：_sync_to_session 已按拆分红线外移到 agent_task_mixin.py
    # （agent.py 2418 > 2400 撞线），锚点随迁——外移是既定拆分模式，
    # 不是把逻辑删了。
    src = open(os.path.join(ROOT, "agent_task_mixin.py"), encoding="utf-8").read()
    check("F1 _sync_sig 四维指纹已定义", "def _sync_sig(m):" in src)
    check("F2 主路径合并分支在位",
          '_tgt = _exist_by_base.get(_sig[:3])' in src
          and '_tgt["tool_calls"] = [dict(tc) for tc in m["tool_calls"]]' in src)
    check("F3 兜底路径边界合并在位",
          "_bm = self.messages[start_idx - 1]" in src
          and '_le["tool_calls"] = [dict(tc) for tc in _bm["tool_calls"]]' in src)
    check("F4 agent.py 守住拆分红线（< 2400 行）",
          len(open(os.path.join(ROOT, "agent.py"), encoding="utf-8")
              .read().split("\n")) < 2400,
          "agent.py %d 行" % len(open(os.path.join(ROOT, "agent.py"),
                                      encoding="utf-8").read().split("\n")))

    print("SYNC_TOOLCALLS PASS=%d FAIL=%d" % (_p, _f))
    sys.exit(0 if _f == 0 else 1)


if __name__ == "__main__":
    main()
