# -*- coding: utf-8 -*-
"""v4.285.0 判据：批内同名同参工具调用去重（固化 v4.120 既有防线）。

背景（彻查「工具调用准确性」P2 修正）：v4.120 起 _run_serial /
_run_concurrent 已有 `_seen_sigs` 批内去重，但 tests/ 零覆盖——
「改坏了没人知道」。本判据把这条防线钉住：
  1. 行为级：双发同名同参 tool_calls，handler 只执行一次；
  2. 被去重的调用仍补占位 tool 回执（否则下轮 API 400 会话带毒）；
  3. 不同参数的调用不受影响（不误伤）；
  4. 结构护栏：_seen_sigs 去重逻辑在位，防后人当冗余删掉。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s  <%s>" % (name, detail))


print("=" * 64)
print("v4.285.0 批内同名同参去重（固化 v4.120 防线）")
print("=" * 64)


class _Sess(object):
    messages = []


class _Store(object):
    def active(self):
        return _Sess()


class _Dec(object):
    allowed = True
    needs_user = False
    reason = ""
    rule = "test"


class _Engine(object):
    def decide(self, name, args=None, explicit_intent=True, task_risk=None):
        return _Dec()


class _MW(object):
    def __init__(self):
        self.cfg = {}
        self.store = _Store()
        self.permission_engine = _Engine()
        self.app_dir = ROOT


print("\n--- 注册计数探针工具 ---")
import tools  # noqa: E402

_calls = {"n": 0}


@tools.register_tool("_dedup_probe_285", risk="read")
def _probe(cfg, app_dir, args, progress=None):
    _calls["n"] += 1
    return "探针结果-%d" % _calls["n"]


print("\n--- A. 行为级：双发同名同参只执行一次 ---")
try:
    from agent import AgentWorker

    _mw = _MW()
    w = AgentWorker(_mw, [{"role": "user", "content": "x"}], [], isolated=True)
    w._bound_sid = None
    _receipts = []
    w._handle_tool_result = (
        lambda tc, name, fn, result_str, deliverables, schedule,
        tool_result=None:
        _receipts.append((tc.get("id"), name, str(result_str))))
    tcs = [
        {"id": "d1", "type": "function",
         "function": {"name": "_dedup_probe_285",
                      "arguments": "{\"q\":\"昆明天气\"}"}},
        {"id": "d2", "type": "function",
         "function": {"name": "_dedup_probe_285",
                      "arguments": "{\"q\":\"昆明天气\"}"}},   # 同参双发
        {"id": "d3", "type": "function",
         "function": {"name": "_dedup_probe_285",
                      "arguments": "{\"q\":\"北京天气\"}"}},   # 不同参
    ]
    w._run_serial(tcs, _mw, ROOT)
    check("A1 handler 只执行 2 次（unique 条数）", _calls["n"] == 2,
          "n=%d" % _calls["n"])
    check("A2 三条调用都有 tool 回执（防下轮 400）",
          len(_receipts) == 3, "receipts=%r" % (_receipts,))
    check("A3 被去重的调用回执含去重说明",
          any("去重" in r[2] for r in _receipts), repr(_receipts))
    check("A4 不同参的调用没被误伤",
          any("探针结果" in r[2] for r in _receipts), repr(_receipts))
except Exception as e:
    import traceback
    traceback.print_exc()
    check("A0 行为级段执行不抛异常", False, repr(e))

print("\n--- B. 空批次不炸 ---")
try:
    w._run_serial([], _mw, ROOT)
    check("B1 空批次安全返回", True)
except Exception as e:
    check("B1 空批次安全返回", False, repr(e))

print("\n--- C. 结构护栏：去重逻辑在位 ---")
_src = open(os.path.join(ROOT, "agent.py"), encoding="utf-8").read()
check("C1 _run_serial 有 _seen_sigs 去重",
      "_seen_sigs = set()" in _src)
check("C2 去重命中走占位回执（_handle_tool_result）",
      "已自动去重" in _src and "_handle_tool_result" in _src)

print()
print("=" * 64)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 64)
sys.exit(1 if FAIL else 0)
