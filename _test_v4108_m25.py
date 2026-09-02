# -*- coding: utf-8 -*-
"""v4.108 M-25 聊天渲染状态机：定向单元验证（离线）。

覆盖：
1. agent._tool_seq 全局单调（串行/并发/workflow 不再每批从 0 起）
2. ui._on_tool_started/_on_tool_finished：UI 自管唯一 DOM id + 台账配对
   （跨批 index 复用不撞 id、finished 正确 replace 新卡）
3. _on_stream_commit 不再强制全量重建（不重置 _rendered_msg_count → 不清 live 卡 DOM）
4. 全量重建分支带 #stream-bubble id 的流式部件 + live 台账恢复（源码断言）
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ok = True


def check(name, cond):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")


print("== 1. agent 工具序号全局单调 ==")
import agent as agent_mod
try:
    a = agent_mod.AgentWorker.__new__(agent_mod.AgentWorker)
    a._tool_seq = 0
    seqs = [a._next_tool_index() for _ in range(5)]
    check("连续 5 次序号唯一递增", seqs == [1, 2, 3, 4, 5])
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "agent.py"), encoding="utf-8").read()
    check("_run_serial 用 _next_tool_index", "_next_tool_index()" in src)
    check("并发与 workflow 也走全局序号",
          "idx = self._next_tool_index()" in src and "_wf_idx = self._next_tool_index()" in src)
    check("workflow 补 started emit 配对",
          'self.tool_started.emit({' in src and "_wf_idx" in src)
except Exception as e:
    check(f"agent 检查异常: {e}", False)

print("== 2. UI 工具卡台账（裸实例模拟信号流）==")
import ui as ui_mod
try:
    w = ui_mod.ChatWindow.__new__(ui_mod.ChatWindow)

    class _CV:
        def __init__(self):
            self.appended = []
            self.replaced = []

        def append(self, html):
            self.appended.append(html)

        def replace_live(self, id_, html):
            self.replaced.append((id_, html))

    w.chat_view = _CV()
    w._live_tools = {}
    w._live_seq = 0
    # 模拟旧 agent 行为：跨轮 index 复用（0 → 0），UI 应生成不同 DOM id
    w._on_tool_started({"name": "web_search", "args": {"query": "x"}, "index": 0})
    w._on_tool_finished({"name": "web_search", "result_preview": "结果1", "index": 0})
    w._on_tool_started({"name": "write_file", "args": {"path": "a.py"}, "index": 0})
    w._on_tool_finished({"name": "write_file", "result_preview": "已写", "index": 0})
    ids = []
    for h in w.chat_view.appended:
        import re as _re
        m = _re.search(r'id="(live-tool-\d+)"', h)
        ids.append(m.group(1) if m else "?")
    check("两轮 started 生成不同 DOM id（不撞车）",
          len(ids) == 2 and ids[0] != ids[1])
    repl_ids = [r[0] for r in w.chat_view.replaced]
    check("finished 各替换到自己的卡（2 次均命中）",
          len(repl_ids) == 2 and repl_ids[0] == ids[0] and repl_ids[1] == ids[1])
    check("台账状态均为 done", all(
        r.get("status") == "done" for r in w._live_tools.values()))
    # done 卡样式包含 ✓
    check("done 卡样式正确", "✓" in w.chat_view.replaced[-1][1])
except Exception as e:
    check(f"UI 台账异常: {e}", False)

print("== 3. _on_stream_commit 不再强制全量重建 ==")
try:
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ui.py"), encoding="utf-8").read()
    seg = src[src.find("def _on_stream_commit"):src.find("def _on_stream_commit") + 1800]
    check("commit 段不再 _rendered_msg_count = 0",
          "_rendered_msg_count = 0" not in seg.replace(
              "def _on_stream_commit", "", 1) or
          "_rendered_msg_count = len(msgs)" in src)
    check("commit 段保留 _render_throttled", "self._render_throttled()" in seg)
    check("M-25 注释已标注增量方案", "v4.108 M-25" in seg)
except Exception as e:
    check(f"commit 检查异常: {e}", False)

print("== 4. 全量重建：流式部件 id + live 台账恢复 ==")
try:
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ui.py"), encoding="utf-8").read()
    check("全量分支流式部件带 stream-bubble id",
          'id="stream-bubble">' in src and
          "self._fmt_bubble(\"assistant\", self._streaming_text)" in src)
    check("全量分支后按台账恢复 live 卡",
          "render_all(\"\".join(parts))" in src and
          "sorted(self._live_tools.values()" in src)
    check("恢复逻辑只在 agent 运行中触发",
          'getattr(self, "_agent_active", False)' in src)
    # 旧 live-tool index 直拼已不存在
    check("不再用 agent index 直拼 DOM id",
          'id="live-tool-{index}"' not in src)
except Exception as e:
    check(f"全量分支检查异常: {e}", False)

print("== 5. _tool_card_html 统一模板存在 ==")
try:
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ui.py"), encoding="utf-8").read()
    check("有 _tool_card_html 两态模板", "def _tool_card_html" in src and
          "tool-dot running" in src and "tool-dot done" in src)
except Exception as e:
    check(f"模板检查异常: {e}", False)

print()
print("ALL_M25_FIXES_OK" if ok else "SOME_CHECKS_FAILED")
sys.exit(0 if ok else 1)
