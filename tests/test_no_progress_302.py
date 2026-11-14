# -*- coding: utf-8 -*-
"""v4.302.0：堵住「不停重复调用工具」的四个真根因（2026-11-14 实机事故）。

事故复盘（sessions.json / route_log.jsonl / tool_audit.jsonl / debug.log）：
  · 主会话 129 条消息、39 次工具调用尝试，run_command 34 次（25 次真执行、
    9 次被「参数全等」护栏拦截）；
  · 已回执「回收站 0 项」仍继续调工具 13 条；
  · 10:29–11:11 共 87 次模型调用，82 次带 tool_choice=required；
  · 首轮 20 步 596.5 秒后自动续跑，最终 829280/800000 token 预算熔断。

四个根因与对应修复：
  1. 工具意图每步都把 tool_choice 设成 required → 模型没有文本收尾出口
     → `ui_routing.count_prior_tool_results` + `_tool_intent_required`（仅首步）。
  2. 回收站计数把 desktop.ini / $I* 元数据算成删除项 → 假阴性「还有 3 项」
     → `_count_recycle_items` 只数真实删除项。
  3. 防重复护栏只比「工具名 + 参数全等」，改个变量名就绕过
     → `agent_text.outcome_no_progress` + `AgentExecMixin._guard_no_progress`。
  4. 核验停滞早停只在 verified=False 且有未满足项时计数；本次是
     「计划 0 项、核验通过」→ 永不触发
     → `verified_no_progress_sig` + `MAX_NO_PROGRESS_STEPS`。

纯标准库 + 离屏 Qt，不联网、不改真实用户数据目录。
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

_SBX = tempfile.mkdtemp(prefix="xc_302_np_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")
os.environ["XC_LEGION_DIR"] = os.path.join(_SBX, "legion")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import agent
import agent_text
import agent_exec_mixin
import system_control_tools
import ui_routing

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def read(name):
    return (ROOT / name).read_text(encoding="utf-8-sig")


# 旧代码（v4.301.0 及以前）没有这批新 API。用探测式调用让「先红验证」能
# 跑完整套并给出具体红数，而不是在 import/属性访问处直接崩掉。
MISSING = "<<MISSING-302>>"


def call_if(name, *args):
    f = getattr(agent_text, name, None)
    if not callable(f):
        return MISSING
    try:
        return f(*args)
    except Exception:
        return MISSING


def route_if(name, *args):
    f = getattr(ui_routing, name, None)
    if not callable(f):
        return MISSING
    try:
        return f(*args)
    except Exception:
        return MISSING


def guard_if(w, calls, white):
    f = getattr(w, "_guard_no_progress", None)
    if not callable(f):
        return MISSING
    try:
        return f(calls, white)
    except Exception:
        return MISSING


UI = read("ui.py")
AG = read("agent.py")
AT = read("agent_text.py")
AX = read("agent_exec_mixin.py")
SC = read("system_control_tools.py")


def tool_msg(name, content, call_id="c"):
    return {"role": "tool", "tool_call_id": call_id,
            "name": name, "content": content}


def call(name, args, cid="x"):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class Sig:
    def __init__(self):
        self.ev = []

    def emit(self, *a, **k):
        self.ev.append(a)


def make_worker(messages=None):
    w = agent.AgentWorker.__new__(agent.AgentWorker)
    w.messages = list(messages or [])
    for n in ("tool_started", "tool_finished", "tool_log", "status", "render",
              "deliverable_added", "stream_begin", "stream_chunk", "stream_commit",
              "done", "confirm_action", "schedule_reminder"):
        setattr(w, n, Sig())
    w._ctx_sid = "s302"
    w._stop_requested = False
    w._app_dir = _SBX
    w._guard_blocked = False
    w._resume_done_hashes = set()
    w._recent_tool_sigs = []
    w._remember_calls = 0
    w.tool_defs = []
    w._emit_status = lambda s: None
    w._next_tool_index = lambda: 1
    return w


class FakeHost:
    def _looks_like_learning_question(self, s):
        return False

    def _looks_like_praise(self, s):
        return False

    def _is_media_gen_request(self, s):
        return False


print("=" * 66)
print("A 组：count_prior_tool_results —— 只在「本轮还没真调过工具」时强制 required")
print("=" * 66)

check("A1 空消息 → 0", route_if("count_prior_tool_results",[]) == 0)
check("A2 只有用户消息 → 0",
      route_if("count_prior_tool_results",
          [{"role": "user", "content": "查一下"}]) == 0)
check("A3 用户 + 1 条工具回执 → 1",
      route_if("count_prior_tool_results",
          [{"role": "user", "content": "查一下"},
           tool_msg("run_command", "0 项")]) == 1)
check("A4 用户 + 3 条工具回执 → 3",
      route_if("count_prior_tool_results",
          [{"role": "user", "content": "查一下"}] +
          [tool_msg("run_command", "0 项", str(i)) for i in range(3)]) == 3)
check("A5 只数最后一条真实用户消息之后的回执",
      route_if("count_prior_tool_results",
          [{"role": "user", "content": "清空回收站"},
           tool_msg("clean_recycle_bin", "已清空"),
           {"role": "user", "content": "查一下"},
           tool_msg("run_command", "0 项")]) == 1)
check("A6 tool_log 回执同样计入",
      route_if("count_prior_tool_results",
          [{"role": "user", "content": "查一下"},
           {"role": "tool_log", "name": "run_command", "result": "0 项"}]) == 1)
check("A7 _internal 注入消息不构成新指令边界",
      route_if("count_prior_tool_results",
          [{"role": "user", "content": "查一下"},
           tool_msg("run_command", "0 项"),
           {"role": "user", "content": "必须调用工具", "_internal": True},
           tool_msg("run_command", "0 项")]) == 2)
check("A8 非 dict 元素不抛异常",
      route_if("count_prior_tool_results",
          [None, {"role": "user", "content": "查一下"}, "junk",
           tool_msg("run_command", "0 项")]) == 1)

print("\n-- B 组：ui.py 的 required 接线 --")
check("B1 工具意图的 required 收敛成独立变量",
      "_tool_intent_required = (" in UI)
check("B2 收敛条件真读本轮工具回执数",
      "ui_routing.count_prior_tool_results(messages) == 0" in UI)
check("B3 required 分支改用收敛后的变量",
      "elif (force_required or redo or _tool_intent_required)" in UI)
check("B4 主循环明确强制意图 force_required / redo 未被削弱",
      "force_required or redo" in UI and 'body["tool_choice"] = "required"' in UI)
check("B5 升舱路由仍按原始工具意图（未顺带降级模型）",
      "_route_force = _tool_intent or _has_img_call or force_complex" in UI)
_accident_msgs = ([{"role": "user", "content": "查一下"}] +
                  [tool_msg("run_command", "回收站 0 项", str(i)) for i in range(3)])
_prior = route_if("count_prior_tool_results", _accident_msgs)
check("B6 实机事故消息序列：工具意图仍为真，但本轮已有回执 → 不再强制",
      ui_routing._needs_tool_intent(_accident_msgs, FakeHost()) is True
      and isinstance(_prior, int) and _prior >= 1,
      "工具意图或回执计数不符：%r" % (_prior,))
check("B7 用户新指令其后无回执 → 仍应强制 required（防首步演工具）",
      route_if("count_prior_tool_results",
          [tool_msg("run_command", "旧结果"),
           {"role": "user", "content": "查一下"}]) == 0)

print("\n" + "=" * 66)
print("C 组：回收站计数排除系统元数据（假阴性起点）")
print("=" * 66)

_count_src = SC[SC.index("def _count_recycle_items()"):
                SC.index("def _empty_recycle_bin()")]
check("C1 计数源码排除 desktop.ini", '"desktop.ini"' in _count_src)
check("C2 计数源码排除 $I* 元数据", 'startswith("$I")' in _count_src)
check("C3 不再用 total += len(files) 一把全算",
      "total += len(files)" not in _count_src
      and "for f in files" in _count_src)

_SID = "S-1-5-21-TEST"
_BASE = "C:\\$Recycle.Bin\\" + _SID


def _count_with(files):
    with mock.patch.object(system_control_tools, "_current_user_sid",
                           return_value=_SID), \
         mock.patch.object(system_control_tools.os.path, "isdir",
                           side_effect=lambda p: p == _BASE), \
         mock.patch.object(system_control_tools.os, "walk",
                           return_value=[(_BASE, [], list(files))]):
        return system_control_tools._count_recycle_items()


check("C4 行为级：desktop.ini + $I 元数据 + $R 真实项 → 只算 1 项",
      _count_with(["desktop.ini", "$IBVAIBO.py", "$RBVAIBO.py"]) == 1,
      _count_with(["desktop.ini", "$IBVAIBO.py", "$RBVAIBO.py"]))
check("C5 行为级：清空后只剩元数据 → 0 项（实机事故场景）",
      _count_with(["desktop.ini", "$IBVAIBO.py"]) == 0,
      _count_with(["desktop.ini", "$IBVAIBO.py"]))
check("C6 行为级：普通删除项仍照数（不误伤非标准命名）",
      _count_with(["a.txt", "b.txt", "desktop.ini"]) == 2,
      _count_with(["a.txt", "b.txt", "desktop.ini"]))

print("\n" + "=" * 66)
print("D 组：结果无进展纯判据（参数会变，结果不会骗人）")
print("=" * 66)

check("D1 规范化剥离 [EV#3] 证据编号",
      call_if("normalize_tool_outcome","run_command", "[EV#3] 回收站 0 项")
      == ("run_command", "回收站 0 项"))
check("D2 规范化剥离 ⟦EV#7⟧ 证据编号",
      call_if("normalize_tool_outcome","run_command", "⟦EV#7⟧ 回收站 0 项 ⟦EV#7⟧")
      == ("run_command", "回收站 0 项"))
check("D3 规范化剥离时间戳",
      call_if("normalize_tool_outcome",
          "run_command", "2026-11-14 10:29:01 完成；2026-11-14 10:31:02 完成")
      == ("run_command", "<time> 完成；<time> 完成"))
check("D4 规范化折叠空白",
      call_if("normalize_tool_outcome","run_command", " 0 项\n\n  0 项  ")
      == ("run_command", "0 项 0 项"))
check("D5 最近 3 条同工具同结果 → 判定无进展",
      call_if("outcome_no_progress",
          [tool_msg("run_command", "[EV#%d] 回收站 0 项" % i, str(i))
           for i in range(3)]) is True)
check("D6 只有 2 条 → 不判无进展（给正常复查留空间）",
      call_if("outcome_no_progress",
          [tool_msg("run_command", "0 项", str(i)) for i in range(2)]) is False)
check("D7 结果不同 → 不判无进展",
      call_if("outcome_no_progress",
          [tool_msg("run_command", "0 项", "0"),
           tool_msg("run_command", "1 项", "1"),
           tool_msg("run_command", "2 项", "2")]) is False)
check("D8 工具名不同 → 不判无进展",
      call_if("outcome_no_progress",
          [tool_msg("run_command", "0 项", str(i)) for i in range(2)] +
          [tool_msg("web_search", "0 项", "2")]) is False)
check("D9 无 name 的框架「已拦截」回执不计入执行次数",
      call_if("outcome_no_progress",
          [{"role": "tool", "content": "（系统提示：已拦截）"}] +
          [tool_msg("run_command", "0 项", str(i)) for i in range(2)]) is False)
check("D10 实机 25 条「0 项」连发 → 判无进展",
      call_if("outcome_no_progress",
          [tool_msg("run_command", "回收站 0 项", str(i)) for i in range(25)]) is True)

print("\n" + "=" * 66)
print("E 组：护栏行为级（改参数也拦得住）")
print("=" * 66)

_three = [tool_msg("run_command", "回收站 0 项", str(i)) for i in range(3)]
check("E1 3 条同结果 + run_command → 护栏命中",
      guard_if(make_worker(_three),
               [call("run_command", {"cmd": "Get-Item"})],
               agent._IDEMPOTENT_READ) is True)
check("E2 参数改成换行/换变量名仍命中（旧护栏的绕过手法）",
      guard_if(make_worker(_three),
               [call("run_command", {"cmd": "$x = 1\nGet-Item"})],
               agent._IDEMPOTENT_READ) is True)
check("E3 只有 2 条同结果 → 不拦",
      guard_if(make_worker(_three[:2]),
               [call("run_command", {"cmd": "x"})],
               agent._IDEMPOTENT_READ) is False)
check("E4 幂等读工具仍按白名单豁免（轮询/复查合理）",
      guard_if(make_worker([tool_msg("web_search", "无结果", str(i))
                            for i in range(3)]),
               [call("web_search", {"query": "a"})],
               agent._IDEMPOTENT_READ) is False)
check("E5 结果有变化 → 不拦",
      guard_if(make_worker([tool_msg("run_command", "结果 %d" % i, str(i))
                            for i in range(3)]),
               [call("run_command", {"cmd": "x"})],
               agent._IDEMPOTENT_READ) is False)
check("E6 接线在 agent_exec_mixin.py（agent.py 只有一行调用点）",
      "def _guard_no_progress(" in AX
      and AG.count("self._guard_no_progress(tool_calls, _IDEMPOTENT_READ)") == 1)

_real_exec = agent.tools.exec_tool
_exec_hits = []


def _fake_exec(*a, **k):
    _exec_hits.append(a)
    return ("不该被执行", [], None)


agent.tools.exec_tool = _fake_exec
_w = make_worker(_three)
_exec_boom = None
try:
    try:
        _w._exec_tool_calls(
            [call("run_command", {"cmd": "$a = 1; Get-Item"}, "n1")],
            None, _SBX)
    except Exception as e:
        _exec_boom = repr(e)
finally:
    agent.tools.exec_tool = _real_exec
_receipts = [m for m in _w.messages if m.get("role") == "tool"
             and m.get("tool_call_id") == "n1"]
check("E7 端到端：护栏命中后工具一次都没真执行",
      _exec_boom is None and _exec_hits == [],
      "boom=%r hits=%r" % (_exec_boom, _exec_hits))
check("E8 端到端：_guard_blocked 置位，主循环能感知拦截",
      _w._guard_blocked is True)
check("E9 端到端：本批每个调用都有回执（防下轮 API 400）",
      len(_receipts) == 1 and "没有进展" in _receipts[0].get("content", ""),
      _receipts)
check("E10 端到端：工具日志留痕「护栏」",
      any(e and e[0].get("name") == "护栏"
          for e in _w.tool_log.ev), _w.tool_log.ev)

print("\n" + "=" * 66)
print("F 组：核验通过但结果不变也要早停")
print("=" * 66)

_o = [tool_msg("run_command", "回收站 0 项", str(i)) for i in range(3)]
check("F1 verified=False → 不产生新停滞签名（旧路径不受影响）",
      call_if("verified_no_progress_sig",_o, False) == "")
check("F2 verified=True 但无工具回执 → 空签名",
      call_if("verified_no_progress_sig",[], True) == "")
_s1 = call_if("verified_no_progress_sig",_o, True)
check("F3 verified=True 且有回执 → 非空 verified_same 签名",
      _s1.startswith("verified_same:"), _s1)
check("F4 连续两步结果不变 → 签名一致（能被停滞计数器累加）",
      _s1 != MISSING
      and _s1 == call_if("verified_no_progress_sig",
          _o + [tool_msg("run_command", "[EV#9] 回收站 0 项", "3")], True))
check("F5 结果变了 → 签名变化（不该被早停）",
      _s1 != call_if("verified_no_progress_sig",
          _o + [tool_msg("run_command", "回收站 1 项", "3")], True))
check("F6 源码用更短阈值覆盖「核验通过」空转",
      "_stall_limit = (agent_text.MAX_NO_PROGRESS_STEPS if _np_sig" in AG
      and getattr(agent_text, "MAX_NO_PROGRESS_STEPS", MISSING) == 3)
check("F7 源码保留原核验未过停滞路径 MAX_VERIFY_STALL_STEPS",
      "else MAX_VERIFY_STALL_STEPS)" in AG and "MAX_VERIFY_STALL_STEPS = 8" in AG)
check("F8 agent.py 真调用核验通过空转签名（不是只定义不用）",
      "agent_text.verified_no_progress_sig(" in AG)

print("\n" + "=" * 66)
print("G 组：外移纪律与单一真源")
print("=" * 66)
check("G1 agent.py 守住 < 2400 行拆分红线",
      len(AG.split("\n")) < 2400, len(AG.split("\n")))
check("G2 判据只在 agent_text.py 定义一处（不在 agent.py 另抄）",
      "def outcome_no_progress(" in AT and "def outcome_no_progress(" not in AG)
check("G3 护栏白名单仍由 agent.py 单一真源传入",
      "_IDEMPOTENT_READ = frozenset({" in AG
      and "_IDEMPOTENT_READ = frozenset({" not in AX)

print("\n" + "=" * 66)
print("汇总：PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 66)
sys.exit(1 if FAIL else 0)
