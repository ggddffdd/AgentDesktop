# -*- coding: utf-8 -*-
"""v4.289.5：工具执行入口收敛（串行/并发/工作流三条路径共用一份实现）。

背景（2026-10-27 GPT 审查第 3 条）：串行路径的「确认后执行」与「免确认执行」
是两份逐字重复的 try/except，并发又自己写第三份，工作流分支另写一套信号发射。
三处兜底文案与返回形状迟早漂移。

本版收敛为：
  · `_invoke_tool`          —— 全仓唯一 `tools.exec_tool` 调用点
  · `_emit_tool_started`    —— 全仓唯一 `tool_started.emit`
  · `_emit_tool_finished`   —— 全仓唯一 `tool_finished.emit`

纯标准库，不联网；日志/数据改道临时目录。
"""
import ast
import json
import os
import sys
import tempfile
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_295_exec_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import agent
import tools

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def _tree(name):
    return ast.parse((ROOT / name).read_text(encoding="utf-8-sig"))


_AG = _tree("agent.py")
_EX = _tree("agent_exec_mixin.py")


def _count_exec_tool(*trees):
    n = 0
    for t in trees:
        for x in ast.walk(t):
            if (isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
                    and x.func.attr == "exec_tool"):
                n += 1
    return n


def _count_emit(*trees):
    out = {}
    for sig in ("tool_started", "tool_finished"):
        n = 0
        for t in trees:
            for x in ast.walk(t):
                if (isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
                        and x.func.attr == "emit"
                        and isinstance(x.func.value, ast.Attribute)
                        and x.func.value.attr == sig):
                    n += 1
        out[sig] = n
    return out


def _find_fn(tree, name):
    for x in ast.walk(tree):
        if isinstance(x, ast.FunctionDef) and x.name == name:
            return x
    return None


def _calls(node, fname):
    n = 0
    for x in ast.walk(node):
        if isinstance(x, ast.Call):
            f = x.func
            nm = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else None)
            if nm == fname:
                n += 1
    return n


print("=" * 66)
print("E 组：执行入口唯一性（AST，不看注释）")
print("=" * 66)

_n_exec = _count_exec_tool(_AG, _EX)
check("E1 tools.exec_tool 全仓只剩 1 个调用点", _n_exec == 1, _n_exec)
_inv = _find_fn(_EX, "_invoke_tool")
check("E2 该唯一调用点在 _invoke_tool 内", _inv is not None and _calls(_inv, "exec_tool") == 1,
      "定义=%s 调用数=%s" % (_inv is not None,
                            _calls(_inv, "exec_tool") if _inv else "N/A"))
_em = _count_emit(_AG, _EX)
check("E3 tool_started.emit 只剩 1 处（唯一发射点）", _em["tool_started"] == 1,
      _em["tool_started"])
check("E4 tool_finished.emit 只剩 1 处（唯一发射点）", _em["tool_finished"] == 1,
      _em["tool_finished"])
_serial = _find_fn(_AG, "_run_serial")
_conc = _find_fn(_EX, "_run_concurrent")
check("E5 串行路径改走 _invoke_tool（不再自己 try/except）",
      _serial is not None and _calls(_serial, "_invoke_tool") == 2,
      _calls(_serial, "_invoke_tool") if _serial else "无")
check("E6 并发路径改走 _invoke_tool",
      _conc is not None and _calls(_conc, "_invoke_tool") == 1,
      _calls(_conc, "_invoke_tool") if _conc else "无")
check("E7 串行路径的 tool_finished 改走统一 helper",
      _serial is not None and _calls(_serial, "_emit_tool_finished") == 1)
check("E8 并发路径的 tool_finished 改走统一 helper",
      _conc is not None and _calls(_conc, "_emit_tool_finished") == 1)


class Sig:
    def __init__(self):
        self.ev = []

    def emit(self, *a, **k):
        self.ev.append(a[0] if a else k)


class MW:
    cfg = {}


def make_worker():
    w = agent.AgentWorker.__new__(agent.AgentWorker)
    w.messages = []
    for n in ("tool_started", "tool_finished", "tool_log", "status", "render",
              "deliverable_added", "stream_begin", "stream_chunk", "stream_commit",
              "done", "confirm_action", "schedule_reminder"):
        setattr(w, n, Sig())
    w._stop_requested = False
    w._ctx_sid = "s1"
    return w


print()
print("=" * 66)
print("F 组：统一入口的行为契约")
print("=" * 66)

w = make_worker()
_orig = tools.exec_tool
try:
    tools.exec_tool = lambda cfg, app_dir, name, args, **kw: ("已执行", [], None)
    rs, dl, sc, tr = w._invoke_tool(MW(), _SBX, "web_search", {"query": "x"}, None)
    check("F1 正常返回四元组（result/deliverables/schedule/tool_result）",
          rs == "已执行" and dl == [] and sc is None and tr == ("已执行", [], None),
          (rs, dl, sc, tr))

    def _boom(cfg, app_dir, name, args, **kw):
        raise RuntimeError("boom-295")

    tools.exec_tool = _boom
    rs2, dl2, sc2, tr2 = w._invoke_tool(MW(), _SBX, "web_search", {"query": "x"}, None)
    check("F2 异常归一：文案带原因、tool_result 为 None（下游退回文案判据）",
          "工具执行崩溃" in rs2 and "boom-295" in rs2 and tr2 is None, rs2)

    # should_stop 必须透传（避免停止后工具继续跑）
    _seen = {}

    def _spy(cfg, app_dir, name, args, should_stop=None, perm_ctx=None):
        _seen["stop"] = callable(should_stop)
        return ("ok", [], None)

    tools.exec_tool = _spy
    w._stop_requested = True
    w._invoke_tool(MW(), _SBX, "web_search", {"query": "y"}, None)
    check("F3 should_stop 仍透传给 exec_tool（停止语义不丢）",
          _seen.get("stop") is True, _seen)
    w._stop_requested = False
finally:
    tools.exec_tool = _orig

w._emit_tool_started("web_search", {"query": "z"}, 7, 2)
_check_started = w.tool_started.ev and w.tool_started.ev[0].get("index") == 7 \
    and w.tool_started.ev[0].get("total") == 2
check("F4 开始信号带 index/total（UI 卡片配对不变）", bool(_check_started),
      w.tool_started.ev)

w._emit_tool_finished("web_search", "x" * 500, 7, 1234, True)
_p = w.tool_finished.ev[-1] if w.tool_finished.ev else {}
check("F5 完成信号 success 取布尔、内容截断 200 字",
      _p.get("success") is True and len(_p.get("result_preview", "")) == 200
      and _p.get("duration_ms") == 1234, _p)
w._emit_tool_finished("web_search", "fail", 8, 5, 0)
_p2 = w.tool_finished.ev[-1] if w.tool_finished.ev else {}
check("F6 success 归一为 bool（不把 0/None 透给 UI）", _p2.get("success") is False, _p2)

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
