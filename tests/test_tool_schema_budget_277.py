# -*- coding: utf-8 -*-
"""v4.277.0 工具 schema 预算。

行为级：
  · 全量目录默认只发固定的 25 个核心工具，顺序不跟传入顺序走
  · 按需工具接在核心和 request_tools 之后，核心前缀字节不变
  · full_tool_schema 恢复整表，且不插入 request_tools
  · 点名、关键词、request_tools 都能补上目录里的工具；目录外的不会凭空出现
  · 工具结果（例如 sys_info 吐出的整份名单）不会把下一轮又撑满
  · 不像全量目录的清单（导演、自动化受限集）原样全发
"""
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault(
    "XC_USER_DATA_DIR", os.path.join(os.path.dirname(__file__), "_schema_budget_tmp"))
os.environ.setdefault(
    "XC_LOG_DIR", os.path.join(os.environ["XC_USER_DATA_DIR"], "logs"))

import prompt_cache
import tool_defs
import tool_schema_budget as budget

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, extra))


def _name(tool):
    fn = tool.get("function") if isinstance(tool, dict) else None
    if isinstance(fn, dict):
        return fn.get("name") or ""
    return ""


def _names(tools):
    return [_name(t) for t in tools or []]


def _dump(tools):
    return json.dumps(tools, ensure_ascii=False)


def _estimate_tokens(text):
    """JSON 标点各算 1，汉字算 1，连续英文约 4 字 1 个。用来比相对减少量。"""
    tok = 0
    run = 0

    def flush():
        nonlocal tok, run
        if run:
            tok += (run + 3) // 4
            run = 0

    for ch in text or "":
        if ord(ch) > 0x2E80:
            flush()
            tok += 1
        elif ch.isalnum() or ch == "_":
            run += 1
        else:
            flush()
            if not ch.isspace():
                tok += 1
    flush()
    return tok


TOOLS = list(tool_defs.TOOL_DEFS)


def _request_msg(names):
    return [
        {"role": "user", "content": "继续"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_more",
                "type": "function",
                "function": {
                    "name": "request_tools",
                    "arguments": json.dumps({"names": list(names)}, ensure_ascii=False),
                },
            }],
        },
    ]


def test_core_stable():
    print("=== 核心集稳定 ===")
    check("核心正好 25 个且按名字排死",
          len(budget.CORE_TOOL_NAMES) == 25
          and budget.CORE_TOOL_NAMES == tuple(sorted(budget.CORE_TOOL_NAMES))
          and len(set(budget.CORE_TOOL_NAMES)) == 25)
    have = set(_names(TOOLS))
    check("核心工具都在目录里", set(budget.CORE_TOOL_NAMES) <= have,
          str(sorted(set(budget.CORE_TOOL_NAMES) - have)))
    left = budget.select_tool_schemas(TOOLS, cfg={})
    right = budget.select_tool_schemas(list(reversed(TOOLS)), cfg={})
    check("乱序两次得到同一份列表", left == right)
    names = _names(left)
    check("默认顺序与核心常量一致",
          tuple(names[:25]) == budget.CORE_TOOL_NAMES, str(names[:25]))
    check("核心后面紧跟索要入口",
          len(names) > 25 and names[25] == budget.REQUEST_TOOLS_NAME)
    check("默认不含目录外的工具",
          set(names) <= (set(budget.CORE_TOOL_NAMES) | {budget.REQUEST_TOOLS_NAME}))
    missing_write = [t for t in TOOLS if _name(t) != "write_file"]
    kept = _names(budget.select_tool_schemas(missing_write, cfg={}))
    expect = tuple(n for n in budget.CORE_TOOL_NAMES if n != "write_file")
    check("缺一个核心工具时其余顺序不变", tuple(kept[:24]) == expect, str(kept[:24]))


def test_on_demand_appended():
    print("=== 按需工具接在后面 ===")
    base = budget.select_tool_schemas(TOOLS, cfg={})
    mailed = budget.select_tool_schemas(TOOLS, cfg={}, texts=["请发邮件给 a@b.c"])
    base_names = _names(base)
    mail_names = _names(mailed)
    check("发邮件会补上 send_email", mail_names[-1] == "send_email", str(mail_names[-3:]))
    check("按需工具排在核心和索要入口后面",
          mail_names[:26] == base_names[:26]
          and mail_names[25] == budget.REQUEST_TOOLS_NAME)
    base_dump = _dump(base)
    mail_dump = _dump(mailed)
    check("核心前缀字节不变",
          mail_dump.startswith(base_dump[:-1] + ","),
          "base %d mail %d" % (len(base_dump), len(mail_dump)))
    both = budget.select_tool_schemas(
        TOOLS, cfg={}, texts=["发邮件，另外结束进程"])
    tail = _names(both)[26:]
    check("两个按需工具按名字排", tail == ["process_kill", "send_email"], str(tail))
    asked = budget.select_for_call(TOOLS, {}, _request_msg(["clean_recycle_bin"]))
    asked_names = _names(asked)
    check("request_tools 会补上点名的工具",
          asked_names[:26] == base_names[:26]
          and asked_names[-1] == "clean_recycle_bin",
          str(asked_names[-3:]))
    forced = budget.select_for_call(
        TOOLS, {}, [{"role": "user", "content": "你好"}], "app_launch")
    check("强制工具不在核心里也会补上", _names(forced)[-1] == "app_launch")
    desk = budget.select_tool_schemas(TOOLS, cfg={}, texts=["看一下导演台现在到哪一步"])
    check("导演关键词会补上 director_status", "director_status" in _names(desk))
    check("没提到导演时不带 director_status", "director_status" not in base_names)


def test_switch_restores_full():
    print("=== 开关恢复全量 ===")
    full = budget.select_tool_schemas(TOOLS, cfg={"full_tool_schema": True})
    ordered = prompt_cache.order_tool_schemas(list(TOOLS))
    check("full_tool_schema 恢复全量且不含索要入口",
          full == ordered
          and len(full) == len(TOOLS)
          and budget.REQUEST_TOOLS_NAME not in _names(full))
    flipped = budget.select_tool_schemas(
        list(reversed(TOOLS)), cfg={"full_tool_schema": "true"})
    check("开关打开时乱序仍按名字排", flipped == ordered)
    off = budget.select_tool_schemas(TOOLS, cfg={"full_tool_schema": "false"})
    check("字符串 false 仍走核心集",
          _names(off)[:25] == list(budget.CORE_TOOL_NAMES))
    check("缺省配置走核心集",
          _names(budget.select_tool_schemas(TOOLS, cfg={}))[:1]
          == [budget.CORE_TOOL_NAMES[0]])


def test_reachable_and_closed():
    print("=== 目录里的都能到，目录外的不到 ===")
    catalog = _names(TOOLS)
    default_names = set(_names(budget.select_tool_schemas(TOOLS, cfg={})))
    missed = []
    for name in catalog:
        if name in budget.CORE_TOOL_NAMES:
            if name not in default_names:
                missed.append(name)
            continue
        via_text = set(_names(budget.select_tool_schemas(
            TOOLS, cfg={}, texts=["请使用 %s 完成这一步" % name])))
        via_ask = set(_names(budget.select_for_call(
            TOOLS, {}, _request_msg([name]))))
        if name not in via_text or name not in via_ask:
            missed.append(name)
    check("每个目录内工具要么在核心里，要么点名和索要都能补上",
          not missed, ",".join(missed[:12]))
    tiny = [t for t in TOOLS if _name(t) in ("read_file", "write_file")]
    tiny_out = budget.select_tool_schemas(tiny, cfg={}, texts=["请发邮件"])
    check("不在目录里的工具不会凭空出现",
          "send_email" not in _names(tiny_out)
          and budget.REQUEST_TOOLS_NAME not in _names(tiny_out))
    blob = " ".join(catalog)
    leaked = budget.select_for_call(TOOLS, {}, [
        {"role": "user", "content": "继续"},
        {"role": "tool", "content": blob},
    ])
    quiet = budget.select_for_call(TOOLS, {}, [{"role": "user", "content": "继续"}])
    check("工具结果里的名单不会把 schema 又撑满", _names(leaked) == _names(quiet))
    sys_only = budget.select_for_call(TOOLS, {}, [
        {"role": "system", "content": "发邮件用 send_email，导演用 director_status"},
        {"role": "user", "content": "你好"},
    ])
    check("系统提示里的点名不会把工具全带上",
          "send_email" not in _names(sys_only)
          and "director_status" not in _names(sys_only))
    cut = [t for t in TOOLS
           if not _name(t).startswith("mouse_")
           and not _name(t).startswith("webhook_")]
    cut_out = budget.select_tool_schemas(cut, cfg={})
    check("不像全量目录时原样全发",
          _names(cut_out) == _names(prompt_cache.order_tool_schemas(cut))
          and budget.REQUEST_TOOLS_NAME not in _names(cut_out),
          "len %d vs %d" % (len(cut_out), len(cut)))
    try:
        import automation
        safe = automation.filter_tools_safe(TOOLS)
    except Exception as exc:
        safe = None
        check("自动化受限清单可取", False, str(exc))
    if safe is not None:
        safe_out = budget.select_tool_schemas(safe, cfg={})
        check("自动化受限清单不裁工具",
              _names(safe_out) == _names(prompt_cache.order_tool_schemas(safe))
              and budget.REQUEST_TOOLS_NAME not in _names(safe_out),
              "len %d vs %d" % (len(safe_out), len(safe)))
    director = [t for t in TOOLS if _name(t).startswith("director_")]
    director_out = budget.select_tool_schemas(director, cfg={})
    check("导演专用清单不裁工具",
          _names(director_out) == _names(prompt_cache.order_tool_schemas(director))
          and len(director_out) == len(director) > 0)


def test_request_reply_and_wiring():
    print("=== 索要工具的回执与接线 ===")
    kept, replies = budget.split_schema_requests([
        {"id": "a", "function": {
            "name": "request_tools",
            "arguments": json.dumps({"names": ["send_email", "no_such_tool"]}),
        }},
        {"id": "b", "function": {"name": "web_search", "arguments": "{}"}},
    ], ["send_email", "web_search"])
    check("索要和真正的调用拆开",
          len(kept) == 1 and _name_of_call(kept[0]) == "web_search" and len(replies) == 1)
    text = replies[0][1]
    check("回执点名下一轮会带上的工具",
          replies[0][0] == "a" and "send_email" in text and "no_such_tool" in text)
    empty_kept, empty_replies = budget.split_schema_requests([
        {"id": "c", "function": {"name": "request_tools", "arguments": "{}"}},
    ], ["web_search"])
    check("没给名字时说明要补名字",
          empty_kept == [] and empty_replies and "没有给出工具名" in empty_replies[0][1])

    ui = open(os.path.join(ROOT, "ui.py"), encoding="utf-8-sig").read()
    agent = open(os.path.join(ROOT, "agent.py"), encoding="utf-8-sig").read()
    cfg = open(os.path.join(ROOT, "config.py"), encoding="utf-8-sig").read()
    check("_agent_call 走 select_for_call", "select_for_call(" in ui)
    call = _method_source(ui, "_agent_call")
    pos_ctx = call.find("with_dynamic_context(")
    pos_sel = call.find("select_for_call(")
    check("先拼当轮背景再选工具", pos_ctx >= 0 and pos_sel > pos_ctx,
          "ctx %d sel %d" % (pos_ctx, pos_sel))
    tree = ast.parse(agent)
    peel_in_exec = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_exec_tool_calls":
            seg = ast.get_source_segment(agent, node) or ""
            peel_in_exec = "_peel_schema_requests(" in seg
            break
    check("执行前先剥掉索要工具", peel_in_exec)
    check("配置开关默认关闭",
          '"full_tool_schema": False' in cfg or "'full_tool_schema': False" in cfg)
    spec = open(os.path.join(ROOT, "小臭玩AI.spec"), encoding="utf-8-sig").read()
    check("进包清单带上预算模块", "tool_schema_budget" in spec)


def _method_source(src, name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def _name_of_call(tc):
    fn = tc.get("function") if isinstance(tc, dict) else None
    return (fn or {}).get("name", "")


def test_token_reduction():
    print("=== schema token 减少量 ===")
    full = prompt_cache.order_tool_schemas(list(TOOLS))
    core = budget.select_tool_schemas(TOOLS, cfg={})
    full_dump = _dump(full)
    core_dump = _dump(core)
    full_tok = _estimate_tokens(full_dump)
    core_tok = _estimate_tokens(core_dump)
    saved = full_tok - core_tok
    pct = (100.0 * saved / full_tok) if full_tok else 0.0
    print("SCHEMA_TOKENS full=%d core=%d saved=%d pct=%.1f chars_full=%d chars_core=%d"
          % (full_tok, core_tok, saved, pct, len(full_dump), len(core_dump)))
    check("默认 schema 明显短于全量", saved > 0 and pct >= 40.0, "pct=%.1f" % pct)
    check("默认条数少于全量", len(core) < len(full), "%d vs %d" % (len(core), len(full)))


def main():
    test_core_stable()
    test_on_demand_appended()
    test_switch_restores_full()
    test_reachable_and_closed()
    test_request_reply_and_wiring()
    test_token_reduction()
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
