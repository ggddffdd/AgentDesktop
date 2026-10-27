# -*- coding: utf-8 -*-
"""v4.289.4：并发工具停止不丢结果 + 工具去重按执行状态记账。

背景（2026-10-27 核实）：
  · `_exec_tool_calls` 给 `_run_concurrent` 传的是 `list(zip(tc, decs))`，而
    stop 后补占位段按 `tc.get("id")` 遍历元组 → AttributeError，停止并发批次必崩。
  · `as_completed` 循环里 `if stop: continue` 跳过了已启动 future 的回收与记账，
    工具已执行却只留「未完成执行」占位，账本/ledger/交付物全丢。
  · 去重签名在**执行前**登记，失败调用也留表 → 下轮同参重试被当死循环拦掉；
    `run_workflow` 在签名逻辑之前 return，签名从不登记 → 同参数工作流可反复启动。

纯标准库，不联网；日志/数据改道临时目录。
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_294_exec_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")
os.environ["XC_LEGION_DIR"] = os.path.join(_SBX, "legion")

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


class Sig:
    def __init__(self):
        self.ev = []

    def emit(self, *a, **k):
        self.ev.append(a)


def make_worker():
    w = agent.AgentWorker.__new__(agent.AgentWorker)
    w.messages = []
    for n in ("tool_started", "tool_finished", "tool_log", "status", "render",
              "deliverable_added", "stream_begin", "stream_chunk", "stream_commit",
              "done", "confirm_action", "schedule_reminder"):
        setattr(w, n, Sig())
    w._ctx_sid = "s1"
    w._stop_requested = False
    w._app_dir = _SBX
    w._tstate = None
    w._guard_blocked = False
    w._resume_done_hashes = set()
    w._recent_tool_sigs = []
    w._remember_calls = 0
    w._deliverables = []
    w._exec_ledger = []
    w.task_id = "t294"
    w._emit_status = lambda s: None
    w._next_tool_index = lambda: 1
    w._record_exec_ledger = lambda *a, **k: None
    return w


def tc(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class Dec:
    allowed = True
    needs_user = False
    reason = ""


class Engine:
    def decide(self, *a, **k):
        return Dec()


class MW:
    cfg = {}
    permission_engine = Engine()


# ---------------------------------------------------------------- C 组
print("=" * 66)
print("C 组：并发执行中用户停止")
print("=" * 66)

_real_exec = tools.exec_tool


def _install_exec(behaviour):
    """behaviour(name) -> str，返回该工具的 result_str。"""
    def _fake(cfg, app_dir, name, args, **kw):
        _text = behaviour(name)
        return (_text, [], None)
    tools.exec_tool = _fake


# C0：stop 前置（一个都没提交）——不得抛异常，且每个调用都有「未执行」回执
w0 = make_worker()
w0._stop_requested = True
w0._handle_tool_result = agent.AgentWorker._handle_tool_result.__get__(w0, agent.AgentWorker)
_install_exec(lambda n: "不该被执行")
_err = None
try:
    w0._exec_tool_calls([tc("x1", "web_search", {"query": "a"}),
                         tc("x2", "web_fetch", {"url": "https://e.com"})], MW(), str(ROOT))
except Exception as e:  # noqa: BLE001
    _err = e
tools.exec_tool = _real_exec
_ids = {m.get("tool_call_id") for m in w0.messages if m.get("role") == "tool"}
check("C1 stop 前置时执行批次不抛异常", _err is None, repr(_err))
check("C2 未提交的调用都有回执（避免下轮 API 400）", _ids == {"x1", "x2"}, _ids)
_cont = "".join(str(m.get("content")) for m in w0.messages if m.get("role") == "tool")
check("C3 未启动的调用明确写「未执行」，不谎称已完成",
      "未执行" in _cont and "已完成" not in _cont, _cont[:120])

# C4：执行中停止——已启动的结果必须被回收
w1 = make_worker()
w1._handle_tool_result = agent.AgentWorker._handle_tool_result.__get__(w1, agent.AgentWorker)
_started = threading.Event()
_done_names = []


def _beh(name):
    _done_names.append(name)
    _started.set()
    time.sleep(0.3)


_install_exec(_beh)


def _fire():
    _started.wait(3)
    time.sleep(0.05)  # 等主线程把批次全部提交，避免测试自身的竞态
    w1._stop_requested = True


threading.Thread(target=_fire, daemon=True).start()
_err1 = None
try:
    w1._exec_tool_calls([tc("y1", "web_search", {"query": "b"}),
                         tc("y2", "web_fetch", {"url": "https://f.com"})], MW(), str(ROOT))
except Exception as e:  # noqa: BLE001
    _err1 = e
tools.exec_tool = _real_exec

check("C4 执行中停止不抛异常（原为 tuple.get AttributeError）", _err1 is None, repr(_err1))
_cont1 = [str(m.get("content")) for m in w1.messages if m.get("role") == "tool"]
check("C5 已执行的工具数 = 实际拿到的回执数",
      len(_cont1) == len(_done_names) == 2, f"回执{len(_cont1)} 执行{len(_done_names)}")
check("C6 已执行工具的真实结果被回收（不是「未完成执行」占位）",
      len(_cont1) == 2 and all("未完成执行" not in c for c in _cont1), _cont1[:2])
_ids1 = {m.get("tool_call_id") for m in w1.messages if m.get("role") == "tool"}
check("C7 停止后仍保证每个 tool_call 有回执", _ids1 == {"y1", "y2"}, _ids1)

# C8：停止时已启动的调用要记账（ledger/账本），供恢复判定
w2 = make_worker()
_recorded = []
w2._handle_tool_result = (lambda _tc, name, fn, rs, dl, sc, tool_result=None:
                          _recorded.append(name))
w2._stop_requested = False
_started2 = threading.Event()


def _beh2(name):
    _started2.set()
    time.sleep(0.3)


_install_exec(_beh2)


def _fire2():
    _started2.wait(3)
    time.sleep(0.05)
    w2._stop_requested = True


threading.Thread(target=_fire2, daemon=True).start()
w2._exec_tool_calls([tc("z1", "web_search", {"query": "c"})], MW(), str(ROOT))
tools.exec_tool = _real_exec
check("C8 停止时已启动的调用仍走结算入口（记账不丢）",
      _recorded == ["web_search"], _recorded)


# ---------------------------------------------------------------- D 组
print()
print("=" * 66)
print("D 组：重复调用签名按执行状态登记")
print("=" * 66)

import evidence  # noqa: E402
evidence.register = lambda *a, **k: None  # 不落证据库，避免测试污染

w3 = make_worker()
w3._handle_tool_result = agent.AgentWorker._handle_tool_result.__get__(
    w3, agent.AgentWorker)
_batch = [tc("d1", "write_file", {"path": "out.txt", "content": "v1"})]
_install_exec(lambda n: "写入失败：磁盘只读")
w3._exec_tool_calls(_batch, MW(), str(ROOT))
tools.exec_tool = _real_exec
check("D1 失败调用不登记成功签名", w3._recent_tool_sigs == [], w3._recent_tool_sigs)

w3.messages = []
w3._guard_blocked = False
w3.tool_finished.ev = []
_install_exec(lambda n: "写入失败：磁盘只读")
w3._exec_tool_calls(_batch, MW(), str(ROOT))
tools.exec_tool = _real_exec
_blk = any("已重复执行且被拦截" in str(m.get("content"))
           for m in w3.messages if m.get("role") == "tool")
check("D2 失败后同参重试真的被执行（不被当死循环拦掉）",
      (not w3._guard_blocked) and (not _blk) and len(w3.tool_finished.ev) == 1,
      "blocked=%s 拦截回执=%s 完成信号=%d"
      % (w3._guard_blocked, _blk, len(w3.tool_finished.ev)))

# D3/D4：成功后才登记，重复才拦
w4 = make_worker()
w4._handle_tool_result = agent.AgentWorker._handle_tool_result.__get__(
    w4, agent.AgentWorker)
_batch4 = [tc("e1", "write_file", {"path": "ok.txt", "content": "v2"})]
_install_exec(lambda n: "已写入 ok.txt")
w4._exec_tool_calls(_batch4, MW(), str(ROOT))
tools.exec_tool = _real_exec
check("D3 成功调用登记签名", len(w4._recent_tool_sigs) == 1, w4._recent_tool_sigs)

w4.messages = []
w4._guard_blocked = False
w4.tool_finished.ev = []
_install_exec(lambda n: "已写入 ok.txt")
w4._exec_tool_calls(_batch4, MW(), str(ROOT))
tools.exec_tool = _real_exec
check("D4 成功后同参重复仍被护栏拦（防死循环能力未削弱）",
      w4._guard_blocked and len(w4.tool_finished.ev) == 0,
      "blocked=%s 完成信号=%d" % (w4._guard_blocked, len(w4.tool_finished.ev)))

# D5：键序不同视为同一签名
w5 = make_worker()
w5._handle_tool_result = agent.AgentWorker._handle_tool_result.__get__(
    w5, agent.AgentWorker)
_install_exec(lambda n: "已写入 k.txt")
w5._exec_tool_calls([tc("k1", "write_file", {"path": "k.txt", "content": "z"})],
                    MW(), str(ROOT))
tool_finished_5 = len(w5.tool_finished.ev)
tools.exec_tool = _real_exec
w5.messages = []
w5._guard_blocked = False
w5._exec_tool_calls([tc("k2", "write_file", {"content": "z", "path": "k.txt"})],
                    MW(), str(ROOT))
check("D5 键序不同也判为同参（护栏签名与批内去重同口径）",
      w5._guard_blocked and len(w5.tool_finished.ev) == tool_finished_5,
      "blocked=%s 完成信号新增=%d"
      % (w5._guard_blocked, len(w5.tool_finished.ev) - tool_finished_5))

# D6/D7：run_workflow 也要进签名表
w6 = make_worker()
w6._handle_tool_result = agent.AgentWorker._handle_tool_result.__get__(
    w6, agent.AgentWorker)
w6._run_workflow_guarded = lambda t, task, mw, a: "工作流输出"
w6._workflow_incomplete = None
_wf = [tc("w1", "run_workflow", {"type": "research_write", "task": "同一任务"})]
w6._exec_tool_calls(_wf, MW(), str(ROOT))
check("D6 run_workflow 成功后登记签名", len(w6._recent_tool_sigs) == 1,
      w6._recent_tool_sigs)
w6.messages = []
w6._guard_blocked = False
w6.tool_finished.ev = []
w6._exec_tool_calls(_wf, MW(), str(ROOT))
check("D7 同参数工作流第二次被拦（不再重复起任务图）",
      w6._guard_blocked and len(w6.tool_finished.ev) == 0,
      "blocked=%s 完成信号=%d" % (w6._guard_blocked, len(w6.tool_finished.ev)))

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
