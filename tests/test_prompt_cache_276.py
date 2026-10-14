# -*- coding: utf-8 -*-
"""v4.276.0 DeepSeek 前缀缓存。

行为级：
  · 两轮时间、记忆、命中经验不同，稳定前缀字节相同，日期和记忆不在前缀里
  · 当轮背景是历史之后的一条 user 消息，并写明不是用户说的话
  · 较早的历史仍是下一轮请求的前缀
  · 搜索结果留在这条背景里，不插到历史前面
  · 工具 schema 按名字固定排序；工具概览跟着这个顺序
  · 用量日志只含命中、未命中、生成 token 和累计命中率，不回显附带的密钥或提示词
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import prompt_cache

PASS = FAIL = 0
_UI = os.path.join(ROOT, "ui.py")
_SRC = open(_UI, encoding="utf-8-sig").read()
_TREE = ast.parse(_SRC)
_CLS = next(n for n in ast.walk(_TREE)
            if isinstance(n, ast.ClassDef) and n.name == "ChatWindow")


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, extra))


def _method(name):
    for node in _CLS.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(_SRC, node)
    return None


class _Store(object):
    def active(self):
        return self


def _fake_split(today, memory, hits, skill, topic):
    src = _method("_split_system_prompt")
    ns = {
        "AGENT_SYS_APPEND": "\nAGENT",
        "load_dynamic_skills": lambda compact=False: "\nSKILLS",
    }
    if not src:
        return None
    exec(src, ns)

    def session_goal(self, sess):
        return "\n\nGOAL"

    def recent(self):
        return "你好"

    def skill_sec(self):
        return skill

    def overview(self):
        return "\n\nTOOLS"

    def rules(self):
        return "RULES"

    def perm(self):
        return "PERMLIST"

    def harness(self, user_query=""):
        return "HARNESS"

    def products(self):
        return today

    def mem(self):
        return memory

    def hit_block(self, user_query):
        return hits

    def facts(self):
        return "FACTS"

    def is_topic(self, user_query):
        return topic

    cls = type("FakeWin", (), {
        "_split_system_prompt": ns["_split_system_prompt"],
        "_prompt_section_session_goal": session_goal,
        "_recent_user_query": recent,
        "_prompt_section_skill": skill_sec,
        "_build_tool_overview": overview,
        "_prompt_section_rules": rules,
        "_prompt_section_permission": perm,
        "_prompt_section_harness": harness,
        "_prompt_section_products_dir": products,
        "_prompt_section_memory": mem,
        "_harness_hits_block": hit_block,
        "_prompt_section_facts": facts,
        "_is_topic_request": is_topic,
    })
    win = cls()
    win.cfg = {"system_prompt": "你是小臭"}
    win._PERMISSION_RULES = "PERM"
    win._META_SILENCE_RULE = "META"
    win._agent_active = True
    win.store = _Store()
    return win


def test_prefix_across_turns():
    print("=== 两轮时间与记忆不同，稳定前缀相同 ===")
    left = _fake_split(
        "今天日期：2026-10-14", "【用户长期记忆】\n喜欢咖啡",
        "【命中操作经验详情】甲", "", False)
    right = _fake_split(
        "今天日期：2026-10-15", "【用户长期记忆】\n喜欢茶",
        "【命中操作经验详情】乙", "【当前技能：写作】\n写长文", True)
    check("_split_system_prompt 存在", left is not None and right is not None)
    if left is None or right is None:
        return
    s1, d1 = left._split_system_prompt()
    s2, d2 = right._split_system_prompt()
    check("两轮稳定前缀字节相同", s1 == s2 and s1.encode("utf-8") == s2.encode("utf-8"),
          "len %d vs %d" % (len(s1), len(s2)))
    blob = s1
    check("稳定前缀不含日期", "2026-10-14" not in blob and "2026-10-15" not in blob)
    check("稳定前缀不含记忆", "喜欢咖啡" not in blob and "喜欢茶" not in blob)
    check("稳定前缀不含当轮技能", "当前技能" not in blob)
    check("稳定前缀不含命中经验", "命中操作经验" not in blob)
    check("稳定前缀仍有人设和规矩", "你是小臭" in blob and "PERM" in blob and "META" in blob)
    check("第一轮动态带上日期和记忆",
          "2026-10-14" in d1 and "喜欢咖啡" in d1 and "甲" in d1)
    check("第二轮动态带上另一日期、记忆和技能",
          "2026-10-15" in d2 and "喜欢茶" in d2 and "当前技能" in d2 and "乙" in d2)
    check("选题模板不进稳定前缀", "选题标题" not in s2)
    if right._is_topic_request("x"):
        check("选题模板仍在第二轮动态里", "选题标题" in d2, d2[-80:] if d2 else "empty")
    else:
        check("选题模板仍在第二轮动态里", False, "topic flag off")

    hist1 = [{"role": "user", "content": "你好"}]
    hist2 = hist1 + [
        {"role": "assistant", "content": "在的"},
        {"role": "user", "content": "继续"},
    ]
    m1 = prompt_cache.with_dynamic_context(prompt_cache.prefix_messages(s1, hist1), d1)
    m2 = prompt_cache.with_dynamic_context(prompt_cache.prefix_messages(s2, hist2), d2)
    c1 = prompt_cache.cacheable_prefix(m1)
    c2 = prompt_cache.cacheable_prefix(m2)
    check("较早历史仍是下一轮的前缀", c2[:len(c1)] == c1)
    check("动态消息在历史之后",
          m1[-1]["role"] == "user"
          and m1[-2]["role"] == "user" and m1[-2]["content"] == "你好"
          and m2[-2]["content"] == "继续")
    check("动态消息标明不是用户说的话",
          m1[-1]["content"].startswith(prompt_cache.CONTEXT_MARK)
          and "不是用户说的话" in m1[-1]["content"]
          and "喜欢咖啡" in m1[-1]["content"])
    check("动态消息标了内部，不当成用户原话", m1[-1].get("_internal") is True)
    wired = prompt_cache.strip_internal(m1)
    check("发出前去掉内部标记，正文还在",
          "_internal" not in wired[-1] and "喜欢咖啡" in wired[-1]["content"])
    stable_dump = "".join(
        m.get("content", "") for m in c1 if isinstance(m.get("content"), str))
    check("可缓存前缀里没有这两轮的日期和记忆",
          "2026-10-14" not in stable_dump and "喜欢咖啡" not in stable_dump
          and "喜欢茶" not in stable_dump)


def test_search_stays_after_history():
    print("=== 搜索结果留在当轮背景里 ===")
    hist = [{"role": "user", "content": "今天天气"}]
    msgs = prompt_cache.build_chat_messages(
        "STABLE", hist, "今天日期：2026-10-14", "【联网搜索结果】晴")
    check("搜索不另起一条 system",
          sum(1 for m in msgs if m.get("role") == "system") == 1)
    check("用户的话在背景之前",
          msgs[1]["content"] == "今天天气" and msgs[-1]["role"] == "user")
    check("搜索和日期都在最后一条背景里",
          "【联网搜索结果】晴" in msgs[-1]["content"]
          and "2026-10-14" in msgs[-1]["content"]
          and msgs[-1]["content"].startswith(prompt_cache.CONTEXT_MARK))
    again = prompt_cache.build_chat_messages(
        "STABLE",
        hist + [{"role": "assistant", "content": "晴"},
                {"role": "user", "content": "明天呢"}],
        "今天日期：2026-10-15",
        "【联网搜索结果】阴")
    c1 = prompt_cache.cacheable_prefix(msgs)
    c2 = prompt_cache.cacheable_prefix(again)
    check("第二轮仍能接上第一轮去掉背景后的前缀", c2[:len(c1)] == c1)


def test_tool_order():
    print("=== 工具 schema 顺序固定 ===")
    tools = [
        {"type": "function", "function": {"name": "zeta", "description": "z"}},
        {"type": "function", "function": {"name": "alpha", "description": "a"}},
        {"type": "function", "function": {"name": "mid", "description": "m"}},
    ]
    flipped = list(reversed(tools))
    a = prompt_cache.order_tool_schemas(tools)
    b = prompt_cache.order_tool_schemas(flipped)
    names = [t["function"]["name"] for t in a]
    check("乱序两次得到同一顺序", a == b and names == ["alpha", "mid", "zeta"])
    check("不丢掉工具", len(a) == 3 and {t["function"]["name"] for t in a} == {
        "alpha", "mid", "zeta"})
    same = [
        {"type": "function", "function": {"name": "a"}, "id": 1},
        {"type": "function", "function": {"name": "b"}, "id": 2},
        {"type": "function", "function": {"name": "a"}, "id": 3},
    ]
    ids = [t["id"] for t in prompt_cache.order_tool_schemas(same)]
    check("同名保持原来的相对顺序", ids == [1, 3, 2], str(ids))
    check("空表和单件原样返回",
          prompt_cache.order_tool_schemas([]) == []
          and prompt_cache.order_tool_schemas(None) is None
          and prompt_cache.order_tool_schemas(tools[:1]) == tools[:1])

    src = _method("_build_tool_overview")
    check("工具概览方法还在", bool(src))
    if not src:
        return
    ns = {"prompt_cache": prompt_cache, "log": __import__("logging").getLogger("t")}

    class _Cfg(object):
        @staticmethod
        def get_all_tools(cfg):
            return [
                {"type": "function", "function": {
                    "name": "zeta", "description": "最后"}},
                {"type": "function", "function": {
                    "name": "alpha", "description": "最先"}},
            ]

    ns["config"] = _Cfg
    exec(src, ns)
    win = type("W", (), {"_build_tool_overview": ns["_build_tool_overview"]})()
    win.cfg = {}
    text = win._build_tool_overview()
    check("概览按名字排，不跟传入顺序走",
          text.find("alpha") != -1 and text.find("alpha") < text.find("zeta"),
          text)


def test_usage_log():
    print("=== 用量日志与累计命中率 ===")
    prompt_cache.reset_stats()
    planted_key = "planted-key-value"
    planted_prompt = "planted-prompt-body"
    line = prompt_cache.note_and_format({
        "prompt_cache_hit_tokens": 80,
        "prompt_cache_miss_tokens": 20,
        "completion_tokens": 10,
        "api_key": planted_key,
        "prompt": planted_prompt,
    })
    check("日志含三个用量字段",
          line and "prompt_cache_hit_tokens=80" in line
          and "prompt_cache_miss_tokens=20" in line
          and "completion_tokens=10" in line,
          line)
    check("首笔命中率是 0.8000", line and "hit_rate=0.8000" in line, line)
    check("日志不含附带的密钥", line and planted_key not in line, line)
    check("日志不含附带的提示词", line and planted_prompt not in line, line)
    line2 = prompt_cache.note_and_format({
        "prompt_cache_hit_tokens": "0",
        "prompt_cache_miss_tokens": 100,
        "completion_tokens": 5,
    })
    check("第二笔按累计算命中率 0.4000",
          line2 and "hit_rate=0.4000" in line2 and "calls=2" in line2, line2)
    check("没有缓存字段就不记",
          prompt_cache.note_and_format(
              {"prompt_tokens": 3, "completion_tokens": 1, "api_key": planted_key})
          is None)
    check("累计命中率接口与日志一致",
          abs(prompt_cache.running_hit_rate() - 0.4) < 1e-9)
    prompt_cache.set_turn_context("仅这一轮")
    check("线程上能读到当轮背景", prompt_cache.current_turn_context() == "仅这一轮")
    prompt_cache.clear_turn_context()
    check("清掉之后不再附带", prompt_cache.current_turn_context() is None)


def test_wiring():
    print("=== 调用点真的走这套拼装 ===")
    ui = _SRC
    agent = open(os.path.join(ROOT, "agent.py"), encoding="utf-8-sig").read()
    check("对话用 build_chat_messages", "build_chat_messages(" in ui)
    check("不再把搜索插成历史前的 system",
          '{"role": "system", "content": search_context}' not in ui)
    check("Agent 用稳定前缀组消息", "prefix_messages(" in ui)
    check("调用前记下当轮背景", "set_turn_context(" in agent)
    check("发送前附上当轮背景", "with_dynamic_context(" in ui)
    check("发送前记下缓存用量", "note_and_format(" in ui)
    check("请求里的工具走固定排序",
          ui.count("order_tool_schemas(") >= 2)


def main():
    test_prefix_across_turns()
    test_search_stays_after_history()
    test_tool_order()
    test_usage_log()
    test_wiring()
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
