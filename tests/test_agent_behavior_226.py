# -*- coding: utf-8 -*-
"""tests/test_agent_behavior_226.py —— v4.226 真实 Agent **行为级**回归套件

为什么要有这个套件（这是本版本的立项理由之一）
------------------------------------------------
本项目此前的绝大多数判据是**静态探针**：grep 源码里有某个调用点、数某个字符串
出现几次。这类判据抓得住「接线被删」，抓不住：

  * 接上了但**动作没做**（包进 `if False:`、判定恒False、异常被吞）
  * 三段接线都在，但**时序错了**（比如小结发在补做之前，用户先看到半截结论）
  * 多道防线**互相抵消**（A 注入、B 撤回，C 看不到任何东西）
  * 真跑起来才发现的**依赖缺失**（缺信号、缺 checkpoint、缺 Qt 主循环）

所以这里**真跑 `AgentWorker.run()`**：用假 `mw`（`_agent_call` 按剧本返回响应）
驱动完整主循环，断言**可观测行为**——messages 里进了什么指令、stream_commit
发出了什么文本、账本记了什么、阶段走到哪、run 是否正常收尾。

隔离保证
--------
* `_agent_call` 完全被替换 → **不发任何网络请求、不调任何真实模型**；
* 工具执行走假 `_exec_tool_calls` → 不碰磁盘、不起子进程；
* `isolated=True` + 假 store → 不写会话、不写长期记忆、不落 checkpoint；
* 离屏 Qt，worker 同步 `run()`（不起线程）→ 无事件循环依赖、无竞态。

四个场景（覆盖四阶段 + 关键时序）
----------------------------------
  AB-1  双硬要求 → PLAN 注入清单 → EXECUTE 执行 → VERIFY 固化 → SUMMARIZE 出小结
  AB-2  单硬要求 → 不注入清单（零行为变化），仍出小结
  AB-3  收尾闸门放行（未达标补做）→ **不出现小结**（时序铁律：未收尾不发小结）
  AB-4 纯咨询零工具 → 既不注入清单也不出小结（不打扰普通问答）
"""
import os
import sys
import types

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
        print("[FAIL] %s%s" % (name, ("  <%s>" % detail) if detail else ""))


print("=" * 62)
print("v4.226 真实 Agent 行为级回归套件")
print("=" * 62)


# ============================================================
# 假 mw / 假 store / 假权限引擎
# ============================================================
class _Sess(object):
    sid = "test_behavior_226"
    messages = []


class _Store(object):
    def active(self):
        return _Sess()


class _Dec(object):
    allowed = True
    needs_user = False
    reason = ""


class _Engine(object):
    def decide(self, name, args, explicit_intent=True, task_risk=None):
        return _Dec()


class _FakeMw(object):
    """假主窗口：只提供 run() 真会碰到的那些接口。"""

    def __init__(self, script, cfg=None):
        self.script = list(script)      # [(content, tool_calls), ...]
        self.i = 0
        self.calls = []                 # 记录每次 _agent_call 收到的历史长度
        self.cfg = cfg or {}
        self.store = _Store()
        self.permission_engine = _Engine()
        self.app_dir = ROOT

    def _agent_call(self, messages, tool_defs, on_delta=None,
                    force_required=False, force_tool=None, force_complex=False):
        """按剧本返回响应；剧本用尽后返回纯文本（模拟模型收尾）。"""
        self.calls.append({
            "n_msgs": len(messages),
            "force_required": bool(force_required),
            "force_tool": force_tool,
            "last_user": next((m.get("content") for m in reversed(messages)
                               if m.get("role") == "user"), ""),
            "internals": [m.get("content", "") for m in messages
                          if m.get("_internal")],
        })
        if self.i < len(self.script):
            content, tcs = self.script[self.i]
            self.i += 1
        else:
            content, tcs = "（剧本用尽）收尾结论。", []
        if content and on_delta:
            try:
                on_delta(content)
            except Exception:
                pass
        resp = {"content": content, "tool_calls": tcs,
                "reasoning_content": "", "usage": {"total_tokens": 10}}
        return resp


def _mk_worker(script, user_text, cfg=None):
    """造一个真AgentWorker（只隔离外部依赖，不改被测逻辑）。"""
    from agent import AgentWorker

    mw = _FakeMw(script, cfg=cfg)
    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": user_text},
    ]
    w = AgentWorker(mw, messages, [], isolated=True)
    # ---- 隔离外部副作用（真跑但不出网/不落盘）----
    w._sync_to_session = lambda mw_: None
    w._sync_agent_checkpoint = lambda mw_: None
    w._auto_remember = lambda mw_: None
    w._persist_task_memory = lambda mw_, duration_s=0, steps=0: None
    # 工具执行：只记账，不真跑工具（不碰磁盘/不起进程）
    _executed = []

    def _fake_exec(tcs, mw_, app_dir):
        for tc in tcs or []:
            fn = tc.get("function", {})
            _executed.append(fn.get("name", ""))
            # 走真实记账路径（_handle_tool_result 的账本接线在里面）
            try:
                w._handle_tool_result(tc, fn.get("name", ""), fn,
                                      "OK 执行成功", [], None)
            except Exception:
                pass
            try:
                w._tstate_record(task_state_mod, fn.get("name", ""), fn,
                                 "OK 执行成功", [], w._tool_result_looks_failed)
            except Exception:
                pass

    import task_state as task_state_mod
    w._fake_executed = _executed
    w._exec_tool_calls = _fake_exec
    # Qt 信号 → 收集器
    w.render.connect(lambda *a: None)
    return w, mw


import task_state as task_state_mod  # noqa: E402


def _all_text(worker):
    """worker 运行期间 stream_commit 发出的全部文本（拼起来便于断言）。"""
    chunks = []
    try:
        worker.stream_commit.connect(lambda t: chunks.append(str(t)))
    except Exception:
        pass
    return chunks


# ============================================================
# AB-1  四阶段全链路
# ============================================================
print("\n--- AB-1 双硬要求：PLAN→EXECUTE→VERIFY→SUMMARIZE ---")
try:
    _tcs1 = [{"id": "c1", "type": "function",
              "function": {"name": "video_gen", "arguments": "{}"}},
             {"id": "c2", "type": "function",
              "function": {"name": "write_file", "arguments": "{}"}}]
    _w1, _mw1 = _mk_worker(
        [("我先生成视频。", _tcs1),
         ("已生成并保存。", [])],
        "用 video_gen 生成视频，再用 write_file 保存到 D 盘")
    _chunks1 = []
    _w1.stream_commit.connect(lambda t: _chunks1.append(str(t)))
    _w1.run()
    _txt1 = "\n".join(_chunks1)
    _msgs1 = _w1.messages

    # PLAN：清单被注入
    _plan_msgs = [m for m in _msgs1
                  if m.get("_internal") and "任务计划" in str(m.get("content", ""))]
    check("AB-1-1 行为级：PLAN 阶段真的注入了任务清单",
          len(_plan_msgs) == 1, "找到 %d 条" % len(_plan_msgs))
    check("AB-1-2 清单逐条点名了两个硬要求",
          bool(_plan_msgs) and "video_gen" in _plan_msgs[0]["content"]
          and "write_file" in _plan_msgs[0]["content"],
          _plan_msgs[0]["content"][:120] if _plan_msgs else "")
    # EXECUTE：工具真被记账
    check("AB-1-3 行为级：两个工具都真被调用（不是只在清单里写）",
          set(_w1._fake_executed) == {"video_gen", "write_file"},
          str(_w1._fake_executed))
    check("AB-1-4 账本记录了两个工具",
          set(_w1._tstate.used_tools) >= {"video_gen", "write_file"},
          str(_w1._tstate.used_tools))
    # VERIFY：核验记录固化
    check("AB-1-5 行为级：VERIFY 阶段固化了核验记录",
          len(_w1._loop.verification) > 0, str(_w1._loop.verification))
    check("AB-1-6 行为级：全部落实 → verified=True",
          _w1._loop.verified is True, str(_w1._loop.verified))
    # SUMMARIZE：机器小结发出
    check("AB-1-7 行为级：收尾发出了机器小结",
          "本轮执行小结" in _txt1, _txt1[-200:])
    check("AB-1-8 小结列出了真实调用的工具",
          "video_gen" in _txt1 and "做了什么" in _txt1, _txt1[-260:])
    check("AB-1-9 小结显式声明以系统记录为准",
          "以本段为准" in _txt1, _txt1[-160:])
    # 阶段推进轨迹
    _hist = [p for (p, _s) in _w1._loop.history]
    check("AB-1-10 阶段轨迹包含 execute/verify/summarize",
          ("execute" in _hist and "verify" in _hist
           and "summarize" in _hist), str(_hist))
    check("AB-1-11 阶段不回退（末阶段仍是 summarize）",
          _w1._loop.phase == "summarize", _w1._loop.phase)
    # run 正常收尾
    check("AB-1-12 run() 正常收尾（未卡死/未抛异常）",
          _w1._loop.summary_emitted is True)
except Exception as _e:
    import traceback
    traceback.print_exc()
    check("AB-1-0 四阶段全链路真跑得起来", False,
          "%s: %s" % (type(_e).__name__, _e))

# ============================================================
# AB-2  单硬要求：不注入清单（零行为变化），仍出小结
# ============================================================
print("\n--- AB-2 单硬要求：不注入清单 ---")
try:
    _tcs2 = [{"id": "c1", "type": "function",
              "function": {"name": "video_gen", "arguments": "{}"}}]
    _w2, _mw2 = _mk_worker([("生成中。", _tcs2), ("已完成。", [])],
                           "用 video_gen 生成个视频")
    _chunks2 = []
    _w2.stream_commit.connect(lambda t: _chunks2.append(str(t)))
    _w2.run()
    _txt2 = "\n".join(_chunks2)
    check("AB-2-1 单硬要求不注入任务清单（零行为变化）",
          not any(m.get("_internal") and "任务计划" in str(m.get("content", ""))
                  for m in _w2.messages),
          str([m.get("content", "")[:40] for m in _w2.messages
               if m.get("_internal")]))
    check("AB-2-2 单硬要求仍会出机器小结（有事实就有据可查）",
          "本轮执行小结" in _txt2, _txt2[-160:])
    check("AB-2-3 小结里未完成项为空（单要求已满足）",
          "未完成：无" in _txt2, _txt2[-200:])
except Exception as _e:
    import traceback
    traceback.print_exc()
    check("AB-2-0 单硬要求场景真跑得起来", False,
          "%s: %s" % (type(_e).__name__, _e))

# ============================================================
# AB-3  时序铁律：收尾闸门放行（补做）时**不得**发小结
# ============================================================
print("\n--- AB-3 时序：未收尾不发小结 ---")
try:
    # 剧本：第1步调 video_gen，第2步纯文本（此时 write_file 仍缺 → 闸门补做），
    #       第3步（补做轮）调 write_file，第4步收尾纯文本。
    _t3a = [{"id": "c1", "type": "function",
             "function": {"name": "video_gen", "arguments": "{}"}}]
    _t3b = [{"id": "c2", "type": "function",
             "function": {"name": "write_file", "arguments": "{}"}}]
    _w3, _mw3 = _mk_worker(
        [("生成中。", _t3a),
         ("已经生成好了。", []),            # ← 此刻 write_file 缺 → 闸门放行补做
         ("好，现在保存。", _t3b),
         ("已保存完毕。", [])],
        "用 video_gen 生成视频，再用 write_file 保存")
    _chunks3 = []
    _w3.stream_commit.connect(lambda t: _chunks3.append(str(t)))
    _w3.run()
    _txt3 = "\n".join(_chunks3)
    _n_sum = _txt3.count("本轮执行小结")
    check("AB-3-1 时序：补做轮**不发**小结（只在真正收尾时发一次）",
          _n_sum == 1, "小结出现 %d 次" % _n_sum)
    check("AB-3-2 补做确实发生了（write_file 后被调用）",
          "write_file" in _w3._fake_executed, str(_w3._fake_executed))
    check("AB-3-3 补做后账本全达标",
          _w3._tstate.is_complete() is True, str(_w3._tstate.pending()))
    check("AB-3-4 收尾时小结标为未完成：无（补做已生效）",
          "未完成：无" in _txt3, _txt3[-220:])
    # 小结必须在「已保存完毕」之后
    _i_final = _txt3.rfind("已保存完毕")
    _i_sum = _txt3.find("本轮执行小结")
    check("AB-3-5 小结出现在最终结论**之后**（不是半截结论的注解）",
          _i_sum > _i_final, "final=%d summary=%d" % (_i_final, _i_sum))
except Exception as _e:
    import traceback
    traceback.print_exc()
    check("AB-3-0 时序场景真跑得起来", False,
          "%s: %s" % (type(_e).__name__, _e))

# ============================================================
# AB-4  纯咨询零工具：既不注入清单也不出小结
# ============================================================
print("\n--- AB-4 纯咨询不打扰 ---")
try:
    _w4, _mw4 = _mk_worker([("Qwen 完全可以离线用，官方有量化版。", [])],
                           "Qwen 能不能离线用")
    _chunks4 = []
    _w4.stream_commit.connect(lambda t: _chunks4.append(str(t)))
    _w4.run()
    _txt4 = "\n".join(_chunks4)
    check("AB-4-1 纯咨询不注入任务清单",
          not any(m.get("_internal") and "任务计划" in str(m.get("content", ""))
                  for m in _w4.messages))
    check("AB-4-2 纯咨询不出机器小结（不打扰普通问答）",
          "本轮执行小结" not in _txt4, _txt4[-160:])
    check("AB-4-3 纯咨询正常给出答案",
          "离线" in _txt4, _txt4[:120])
except Exception as _e:
    import traceback
    traceback.print_exc()
    check("AB-4-0 纯咨询场景真跑得起来", False,
          "%s: %s" % (type(_e).__name__, _e))

# ============================================================
# AB-6  有未达标项 → verified=False（补 is_verified 那道门的抓手）
# ============================================================
print("\n--- AB-6 未达标不得自称已核验 ---")
try:
    # 只点名 write_file，却只调了 video_gen → 收尾时账本未达标
    _t6 = [{"id": "c1", "type": "function",
            "function": {"name": "video_gen", "arguments": "{}"}}]
    _w6, _mw6 = _mk_worker(
        [("生成中。", _t6),
         ("生成完了。", []),          # write_file 仍缺 → 闸门补做一轮
         ("（剧本用尽，无法保存）", [])],   # 补做后仍缺 → 收尾
        "用 video_gen 生成视频，再用 write_file 保存")
    _ch6 = []
    _w6.stream_commit.connect(lambda t: _ch6.append(str(t)))
    _w6.run()
    _txt6 = "\n".join(_ch6)
    check("AB-6-1 补做后仍未达标 → 账本 pending 非空",
          _w6._tstate.pending() != [], str(_w6._tstate.pending()))
    check("AB-6-2 未达标时 verified=False（不得自称已核验）",
          _w6._loop.verified is False, str(_w6._loop.verified))
    check("AB-6-3 核验记录里点名了缺失项",
          any("write_file" in x for x in _w6._loop.verification),
          str(_w6._loop.verification))
    check("AB-6-4 小结的未完成段如实列出 write_file 未被调用",
          "write_file" in _txt6 and "未被调用" in _txt6, _txt6[-260:])
    check("AB-6-5 小结不得出现「未完成：无」（掩盖未达标）",
          "未完成：无" not in _txt6, _txt6[-260:])
except Exception as _e:
    import traceback
    traceback.print_exc()
    check("AB-6-0 未达标场景真跑得起来", False,
          "%s: %s" % (type(_e).__name__, _e))

# ============================================================
# AB-7  有工具调用但用户没点名 → 不出小结（has_requirements 门的抓手）
# ============================================================
print("\n--- AB-7 没点名不核对 ---")
try:
    _t7 = [{"id": "c1", "type": "function",
            "function": {"name": "web_search", "arguments": "{}"}}]
    _w7, _mw7 = _mk_worker([("搜了一下。", _t7), ("今天新闻如上。", [])],
                           "帮我搜一下今天的新闻")
    _ch7 = []
    _w7.stream_commit.connect(lambda t: _ch7.append(str(t)))
    _w7.run()
    _txt7 = "\n".join(_ch7)
    check("AB-7-1 工具真被调用了（有事实）",
          "web_search" in _w7._fake_executed, str(_w7._fake_executed))
    check("AB-7-2 但用户没点名 → 不出核对小结",
          "本轮执行小结" not in _txt7, _txt7[-200:])
    check("AB-7-3 build_summary 在该场景下**本非空**（是门在拦，不是兜底）",
          bool(_w7._tstate) and len(_w7._tstate.used_tools) > 0,
          str(_w7._tstate.used_tools))
except Exception as _e:
    import traceback
    traceback.print_exc()
    check("AB-7-0 没点名场景真跑得起来", False,
          "%s: %s" % (type(_e).__name__, _e))

# ============================================================
# AB-5  自证：这套套件真的在跑 AgentWorker.run（防空跑假绿）
# ============================================================
print("\n--- AB-5 套件自证 ---")
try:
    _w5, _mw5 = _mk_worker([("hi", [])], "用 video_gen 生成视频")
    _ch5 = []
    _w5.stream_commit.connect(lambda t: _ch5.append(str(t)))
    _w5.run()          # ← 必须真跑，否则下面两条断言是「造完就查」的假绿
    check("AB-5-1 假 mw 的 _agent_call 被真调用过（剧本被消费）",
          _w5.mw.i >= 1, "i=%d" % _w5.mw.i)
    check("AB-5-2 _agent_call 收到的是真 messages（n_msgs>0）",
          _w5.mw.calls and _w5.mw.calls[0]["n_msgs"] > 0,
          str(_w5.mw.calls[:1])[:160])
    check("AB-5-3 工具执行走的是被替换的假执行器（未碰磁盘）",
          isinstance(_w5._fake_executed, list))
    check("AB-5-4 真import 了 AgentWorker（不是空壳）",
          "agent" in sys.modules and hasattr(sys.modules["agent"],
                                             "AgentWorker"))
    # 关键自证：把 _loop_start 换掉，行为必须变 —— 证明这套断言不是恒真
    _w5b, _mw5b = _mk_worker([("hi", [])], "用 video_gen 生成视频")
    _w5b._loop_start = lambda agent_loop, dag=None: False     # 拆掉PLAN 接线
    _ch5b = []
    _w5b.stream_commit.connect(lambda t: _ch5b.append(str(t)))
    _w5b.run()
    check("AB-5-5 自证：拆掉 _loop_start 后机器小结消失（断言非恒真）",
          ("本轮执行小结" in "\n".join(_ch5b)) is False,
          "拆掉接线后小结仍在 = 这套断言抓不到东西")
except Exception as _e:
    check("AB-5-0 自证可跑", False, "%s: %s" % (type(_e).__name__, _e))

print("\n" + "=" * 62)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 62)
sys.exit(1 if FAIL else 0)