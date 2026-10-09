# -*- coding: utf-8 -*-
"""tests/test_agent_loop_phases_226.py —— v4.226 四阶段循环判据

核心不变量（改坏必红）：
  LP1  阶段推进：PLAN→EXECUTE→VERIFY→SUMMARIZE，**饱和不回退**
      （回退 = 验证完又回去执行 = 本项目最恨的打转模式）
  LP2  非法阶段名忽略（fail-open，不静默改状态）
  LP3  PLAN 门槛：硬要求 **≥2 项**才注入；单要求/零要求一律不注入
      （单要求步骤唯一，列计划纯属浪费一轮 prompt）
  LP4  计划文案必须「不许复述清单当交差」+「做不完要说清哪条」
  LP5  build_plan 只列目标与硬要求，**不臆造步骤**
  LP6  VERIFY 只固化记录、**不注入指令**（补做的唯一真源是 task_state）
  LP7  SUMMARIZE 三 gate：有硬要求 + 有事实 + 非空文本；每轮最多一次
  LP8  build_summary 内容全来自账本事实，且显式声明「以本段为准」
  LP9  agent.py 接线钉子（start/verify/summary 三处 + 位置在 break 前）
  LP10 规模红线：agent.py < 2400 行（接线必须外移到 mixin）
  LP11 接线 fail-open：mixin 抛异常 → 返回「不介入」，不阻断主循环
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

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
print("v4.226 四阶段循环判据")
print("=" * 62)

import agent_loop  # noqa: E402
import task_state  # noqa: E402
from agent_loop_mixin import AgentLoopMixin  # noqa: E402


def _ag_code():
    with open(os.path.join(ROOT, "agent.py"), "rb") as f:
        return f.read().decode("utf-8-sig")


def _code_lines(path):
    """剔除**注释文本**后的代码（防注释里的旧句造成假红）。

    做法：用 tokenize 拿到每个 COMMENT token 的精确 (行, 起列, 止列)，
    只把那几列字符挖掉，**保留该行的代码部分**。
    ⚠️ 不能按「注释所在整行」剔除 —— `import agent_loop  # v4.226 xxx`
    这种「代码 + 行尾注释」是本项目的主力写法，整行删会把真调用点一起删掉，
    判据就会假红（这个坑踩过一次：v4.225 的路由句断言）。
    ⚠️ 也不能按 `#` 字符裸切 —— 会误伤字符串里的 `#`（如 `'#标题'`）。
    """
    import io
    import tokenize
    with open(os.path.join(ROOT, path), "rb") as f:
        src = f.read().decode("utf-8-sig")
    try:
        lines = src.split("\n")
        spans = {}
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            for ln in range(tok.start[0], tok.end[0] + 1):
                spans.setdefault(ln, []).append((tok.start[1], tok.end[1]))
        out = []
        for i, line in enumerate(lines, 1):
            sp = spans.get(i)
            if not sp:
                out.append(line)
                continue
            # 从后往前挖，避免前面的切除影响后面的列号
            for (c0, c1) in sorted(sp, reverse=True):
                line = line[:c0] + line[c1:]
            out.append(line)
        return "\n".join(out)
    except Exception:
        return src


# ============================================================
# LP1  阶段推进：顺序 + 饱和不回退
# ============================================================
print("\n--- LP1 阶段推进 ---")
check("LP1-1 四个阶段常量齐全且有序",
      agent_loop.PHASE_ORDER == ("plan", "execute", "verify", "summarize"),
      str(agent_loop.PHASE_ORDER))
check("LP1-2 PHASE_ALL 含全部四阶段",
      set(agent_loop.PHASE_ALL) == set(agent_loop.PHASE_ORDER))
check("LP1-3 next_phase(plan)=execute", agent_loop.next_phase("plan") == "execute")
check("LP1-4 next_phase(execute)=verify",
      agent_loop.next_phase("execute") == "verify")
check("LP1-5 next_phase(verify)=summarize",
      agent_loop.next_phase("verify") == "summarize")
# 关键反例：末阶段必须饱和，**不许回退到 execute**
check("LP1-6 next_phase(summarize) 饱和不回退（不回 execute）",
      agent_loop.next_phase("summarize") == "summarize",
      agent_loop.next_phase("summarize"))
check("LP1-7 非法阶段 → execute（中枢态，fail-open）",
      agent_loop.next_phase("不存在的阶段") == "execute",
      agent_loop.next_phase("不存在的阶段"))
check("LP1-8 next_phase(None) 不抛",
      agent_loop.next_phase(None) == "execute")

_st = agent_loop.LoopState("目标")
check("LP1-9 LoopState 初始 phase=plan", _st.phase == "plan", _st.phase)
_st.advance()
check("LP1-10 advance 后 = execute", _st.phase == "execute", _st.phase)
_st.advance(); _st.advance(); _st.advance(); _st.advance()
check("LP1-11 连 advance 四次仍饱和在 summarize",
      _st.phase == "summarize", _st.phase)
check("LP1-12 history 记录了转换轨迹",
      ("verify", 0) in _st.history and len(_st.history) >= 4,
      str(_st.history))

# ============================================================
# LP2  非法阶段名忽略（不静默改状态）
# ============================================================
print("\n--- LP2 非法阶段 fail-open ---")
_st2 = agent_loop.LoopState()
_st2.enter("plan", 1)
_st2.enter("胡说八道", 2)
check("LP2-1 非法阶段名被忽略（phase 不变）",
      _st2.phase == "plan", _st2.phase)
check("LP2-2 非法阶段不入 history",
      ("胡说八道", 2) not in _st2.history, str(_st2.history))
_st2.enter("verify", 3)
check("LP2-3 合法阶段照常切换", _st2.phase == "verify", _st2.phase)
# enter 传不可哈希/怪类型不应抛
try:
    _st2.enter(object(), 4)
    check("LP2-4 enter(非字符串) 不抛", True)
except Exception as e:
    check("LP2-4 enter(非字符串) 不抛", False, "%s: %s" % (type(e).__name__, e))

# ============================================================
# LP3  PLAN 门槛：硬要求 ≥2
# ============================================================
print("\n--- LP3 PLAN 门槛 ---")
check("LP3-1 零硬要求 → 不注入计划",
      agent_loop.should_plan(()) is False)
check("LP3-2 单硬要求 → 不注入计划（步骤唯一，不浪费一轮）",
      agent_loop.should_plan(["video_gen"]) is False)
check("LP3-3 双硬要求 → 注入计划",
      agent_loop.should_plan(["video_gen", "write_file"]) is True)
check("LP3-4 三硬要求 → 注入计划",
      agent_loop.should_plan(["a", "b", "c"]) is True)
check("LP3-5 非第一步 → 不注入",
      agent_loop.should_plan(["a", "b"], step=3) is False)
check("LP3-6 门槛常量=2",
      agent_loop.PLAN_MIN_REQUIREMENTS == 2,
      str(agent_loop.PLAN_MIN_REQUIREMENTS))
check("LP3-7 None 输入 → False（不抛）",
      agent_loop.should_plan(None) is False)

# ============================================================
# LP4  计划文案纪律
# ============================================================
print("\n--- LP4 计划文案 ---")
_p4 = agent_loop.build_plan("生成视频并保存", ["video_gen", "write_file"])
_in4 = agent_loop.plan_instruction(_p4)
check("LP4-1 计划含总目标", any("生成视频并保存" in x for x in _p4), str(_p4))
check("LP4-2 计划逐项列出硬要求",
      any("video_gen" in x for x in _p4) and any("write_file" in x for x in _p4),
      str(_p4))
check("LP4-3 文案非空", bool(_in4.strip()))
check("LP4-4 文案带「不要复述清单」反交差约束",
      "不要" in _in4 and ("复述" in _in4 or "念" in _in4), _in4[:80])
check("LP4-5 文案带「做不完要说清哪条」",
      "做不到" in _in4 and "哪一条" in _in4, _in4[:120])
check("LP4-6 空计划 → 空文案（不留空标题）",
      agent_loop.plan_instruction([]) == "")
check("LP4-7 空计划 → build_plan 也可能空（无目标无要求）",
      agent_loop.build_plan("", []) == [])

# ============================================================
# LP5  build_plan 不臆造步骤
# ============================================================
print("\n--- LP5 计划只列事实 ---")
_p5 = agent_loop.build_plan("目标X", ["t1", "t2"])
check("LP5-1 不臆造「第一步/第二步」步骤词",
      not any(("第一步" in x) or ("第二步" in x) for x in _p5), str(_p5))
_p5b = agent_loop.build_plan("", ["t1", "t1", "t2"])
check("LP5-2 每条硬要求只出现一次（去重保序）",
      _p5b.count("必须完成：真实调用工具 t1") == 1
      and _p5b.count("必须完成：真实调用工具 t2") == 1
      and len(_p5b) == 2, str(_p5b))
check("LP5-3 超长目标温和截断",
      len(agent_loop.build_plan("很长的目标" * 60, ["t"])[0]) <= 140,
      str(len(agent_loop.build_plan("很长的目标" * 60, ["t"])[0])))
check("LP5-4 None 目标/要求 → 空列表不抛",
      agent_loop.build_plan(None, None) == [])

# ============================================================
# LP6  VERIFY 只固化记录、不注入
# ============================================================
print("\n--- LP6 VERIFY 只记录 ---")
_ts6 = task_state.TaskState("目标", ["video_gen", "write_file"])
check("LP6-1 空账本（零工具零要求）→ 无核验记录",
      agent_loop.verify_report(task_state.TaskState("", [])) == [])
check("LP6-2 verify_report(None) → []", agent_loop.verify_report(None) == [])
_ts6.record_tool("video_gen", {}, "ok", ok=True, step=1)
_rep6 = agent_loop.verify_report(_ts6)
check("LP6-3 有事实 → 产出记录", bool(_rep6), str(_rep6))
check("LP6-4 记录列出已真实调用的工具",
      any("video_gen" in x for x in _rep6), str(_rep6))
check("LP6-5 记录点名未调用的硬要求",
      any("write_file" in x and "未被调用" in x for x in _rep6), str(_rep6))
check("LP6-6 should_verify(有记录)=True",
      agent_loop.should_verify(_ts6) is True)
check("LP6-7 should_verify(空账本)=False",
      agent_loop.should_verify(task_state.TaskState("", [])) is False)
# 关键：verify_report 是**只读**的 —— 调用前后账本状态必须一模一样
_snap = _ts6.to_dict()
agent_loop.verify_report(_ts6)
agent_loop.should_verify(_ts6)
check("LP6-8 verify_report/should_verify 都不改账本（只读）",
      _ts6.to_dict() == _snap, "%r != %r" % (_snap, _ts6.to_dict()))
# 未落地产物也要被记进核验
import tempfile  # noqa: E402
_ts6b = task_state.TaskState("目标", ["write_file"])
_ts6b.record_tool("write_file", {"path": os.path.join(tempfile.gettempdir(),
                                                        "no_such_226.txt")},
                  "声称成功", ok=True, step=1)
_rep6b = agent_loop.verify_report(_ts6b)
check("LP6-9 声称成功但未落地的产物进核验记录",
      any("未在磁盘上落地" in x for x in _rep6b), str(_rep6b))
# is_verified：达标判定只有一个真源，且必须经账本的 is_complete()
_ts6c = task_state.TaskState("目标", ["video_gen"])
_ts6c.record_tool("video_gen", {}, "ok", ok=True, step=1)
check("LP6-10 is_verified：全部达标 → True",
      agent_loop.is_verified(_ts6c) is True)
check("LP6-11 is_verified：有未调用点名工具 → False",
      agent_loop.is_verified(_ts6) is False)
check("LP6-12 is_verified：空账本 → False（不构成已核验）",
      agent_loop.is_verified(task_state.TaskState("", [])) is False)
check("LP6-13 is_verified(None) → False 不抛",
      agent_loop.is_verified(None) is False)
check("LP6-14 is_verified 与账本 is_complete 同源（无事实时二者区别恰在门槛）",
      agent_loop.is_verified(_ts6c) == _ts6c.is_complete())

# ============================================================
# LP7  SUMMARIZE 三 gate
# ============================================================
print("\n--- LP7 SUMMARIZE 门槛 ---")
_ts7 = task_state.TaskState("目标", ["video_gen", "write_file"])
_ts7.record_tool("video_gen", {}, "ok", ok=True, step=1)
check("LP7-1 有要求+有事实 → 追加小结",
      agent_loop.should_summarize(_ts7) is True)
check("LP7-2 emitted=True → 不重复追加",
      agent_loop.should_summarize(_ts7, emitted=True) is False)
check("LP7-3 无硬要求（纯咨询）→ 不追加",
      agent_loop.should_summarize(task_state.TaskState("", [])) is False)
# ⚠️ 关键用例：**有工具调用但用户没点名** —— 只有 has_requirements 门能拦它。
# 「纯咨询」用例抓不到那道门（纯咨询同时也没事实，被 build_summary 的
# 空串早返回兜住，两道门一起失效也看不出来）。这条是那道门的唯一抓手。
_ts7d = task_state.TaskState("帮我搜一下今天的新闻", [])
_ts7d.record_tool("web_search", {}, "ok", ok=True, step=1)
check("LP7-3b 有工具调用但用户没点名 → 仍不追加小结（点名核对才有意义）",
      agent_loop.should_summarize(_ts7d) is False)
check("LP7-3c 上例中 build_summary 本非空（证明是门在拦，不是兜底在拦）",
      bool(agent_loop.build_summary(_ts7d)),
      repr(agent_loop.build_summary(_ts7d))[:120])
_ts7c = task_state.TaskState("目标", ["video_gen"])
check("LP7-4 有要求但零工具调用 → 不追加（空话小结）",
      agent_loop.should_summarize(_ts7c) is False)
check("LP7-4b 零事实时 build_summary 本身返回空串（兜底真在，非装饰 gate）",
      agent_loop.build_summary(_ts7c) == "",
      repr(agent_loop.build_summary(_ts7c)))
check("LP7-5 should_summarize(None)=False",
      agent_loop.should_summarize(None) is False)

# ============================================================
# LP8  小结内容全部来自账本事实
# ============================================================
print("\n--- LP8 小结内容纪律 ---")
_s8 = agent_loop.build_summary(_ts7)
check("LP8-1 小结非空", bool(_s8.strip()))
check("LP8-2 小结列出真实调用的工具",
      "video_gen" in _s8, _s8[:120])
check("LP8-3 小结含未完成项（write_file 没调）",
      "write_file" in _s8 and "未完成" in _s8, _s8[:200])
check("LP8-4 小结显式声明「以本段为准」（对抗模型幻觉自述）",
      "以本段为准" in _s8, _s8[-120:])
check("LP8-5 空账本 → 空小结（不留空标题）",
      agent_loop.build_summary(task_state.TaskState("", [])) == "")
check("LP8-6 build_summary(None) → 空串不抛",
      agent_loop.build_summary(None) == "")
# 失败工具必须进「未完成」
_ts8b = task_state.TaskState("目标", ["run_python"])
_ts8b.record_tool("run_python", {}, "报错", ok=False, step=1)
_s8b = agent_loop.build_summary(_ts8b)
check("LP8-7 失败工具进未完成段",
      "执行失败" in _s8b, _s8b[:200])
# 已落地产物应进「证据」段
_ts8c = task_state.TaskState("目标", ["write_file"])
_ts8c.record_tool("write_file", {"path": os.path.abspath(__file__)},
                  "ok", ok=True, step=1)
_s8c = agent_loop.build_summary(_ts8c)
check("LP8-8 真实存在的产物进证据段",
      "证据" in _s8c and "已落地" in _s8c, _s8c[:200])
# 未落地产物**不得**进「证据」段 —— 这条才是抓住 landed 存在性过滤的那根钉子
_ts8d = task_state.TaskState("目标", ["write_file"])
_ts8d.record_tool("write_file", {"path": os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "no_such_artifact_226.txt")},
    "声称成功", ok=True, step=1)
_s8d = agent_loop.build_summary(_ts8d)
check("LP8-9 未落地产物不进证据段（claimed≠landed）",
      "no_such_artifact_226.txt" not in _s8d.split("未完成")[0],
      _s8d[:200])
check("LP8-10 未落地产物进未完成段",
      "no_such_artifact_226.txt" in _s8d and "未落地" in _s8d, _s8d[:220])

# ============================================================
# LP9  agent.py 接线钉子
# ============================================================
print("\n--- LP9 agent.py 接线 ---")
_ag = _ag_code()
_agx = _code_lines("agent.py")
check("LP9-1 agent.py import agent_loop",
      "import agent_loop" in _agx)
check("LP9-2 agent.py import AgentLoopMixin",
      "from agent_loop_mixin import AgentLoopMixin" in _agx)
check("LP9-3 AgentWorker 继承 AgentLoopMixin",
      re.search(r"class\s+AgentWorker\s*\([^)]*AgentLoopMixin", _ag) is not None)
check("LP9-4 run() 开头调 _loop_start（相B：透传意图层 DAG）",
      "self._loop_start(agent_loop, getattr(self, \"_intent_dag\", None))" in _agx)
check("LP9-5 工具执行后调 _loop_verify",
      "self._loop_verify(agent_loop)" in _agx)
check("LP9-6 收尾调 _loop_summary",
      "self._loop_summary(agent_loop)" in _agx)
check("LP9-7 每步调 _loop_step",
      "self._loop_step(step)" in _agx)
# 位置钉子：verify 必须在 _exec_tool_calls 之后；summary 必须在 break 之前
_i_exec = _agx.find("self._exec_tool_calls(resp[")
_i_ver = _agx.find("self._loop_verify(agent_loop)")
check("LP9-8 _loop_verify 在工具执行之后",
      _i_exec != -1 and _i_ver != -1 and _i_ver > _i_exec,
      "exec=%d verify=%d" % (_i_exec, _i_ver))
_i_sum = _agx.find("self._loop_summary(agent_loop)")
_i_nudge = _agx.find("self._tstate_nudge_now(task_state, step, self._max_steps)")
_i_brk = _agx.find("\n                break", _i_nudge if _i_nudge > 0 else 0)
check("LP9-9 _loop_summary 在 tstate 闸门之后（闸门放行=未收尾，不发小结）",
      _i_nudge != -1 and _i_sum != -1 and _i_sum > _i_nudge,
      "nudge=%d summary=%d" % (_i_nudge, _i_sum))
check("LP9-10 _loop_summary 在 break 之前",
      _i_brk != -1 and _i_sum != -1 and _i_sum < _i_brk,
      "summary=%d break=%d" % (_i_sum, _i_brk))

# ============================================================
# LP10 规模红线
# ============================================================
print("\n--- LP10 规模红线 ---")
_ag_lines = len(_ag.split("\n"))
check("LP10-1 agent.py 守住拆分红线 < 2400 行",
      _ag_lines < 2400, "当前 %d 行（接线应外移到 agent_loop_mixin）" % _ag_lines)
_lp_lines = len(open(os.path.join(ROOT, "agent_loop.py"), "rb")
                .read().decode("utf-8-sig").split("\n"))
check("LP10-2 agent_loop.py 是独立模块（非塞进 agent.py）",
      _lp_lines > 100, str(_lp_lines))

# ============================================================
# LP11  接线 fail-open（行为级）
# ============================================================
print("\n--- LP11 接线 fail-open ---")
try:

    class _S(AgentLoopMixin):
        def __init__(self, ts, loop=None):
            self.messages = []
            self._tstate = ts
            self._loop = loop
            self._status = []
            self._force_next = False
            self.emitted = []

            class _Sig(object):
                def __init__(self, sink):
                    self._sink = sink

                def emit(self, v):
                    self._sink.append(v)

            self.stream_commit = _Sig(self.emitted)

        def _emit_status(self, t):
            self._status.append(t)

    # ① 双硬要求 → 注入计划，进 EXECUTE
    _s1 = _S(task_state.TaskState("生成并保存", ["video_gen", "write_file"]))
    _got1 = _s1._loop_start(agent_loop)
    check("LP11-1 行为级：双硬要求注入计划并返回 True", _got1 is True)
    check("LP11-2 行为级：messages 里确有计划指令",
          len(_s1.messages) == 1 and "任务计划" in _s1.messages[0]["content"],
          str(_s1.messages[:1]))
    check("LP11-3 行为级：阶段推进到 execute",
          _s1._loop.phase == "execute", _s1._loop.phase)
    check("LP11-4 行为级：plan_injected 置位且计划已存",
          _s1._loop.plan_injected is True and len(_s1._loop.plan) >= 2,
          str(_s1._loop.plan))
    # 重复调 start 不重复注入（幂等）
    _s1b = _S(task_state.TaskState("生成并保存", ["video_gen", "write_file"]))
    _s1b._loop_start(agent_loop)
    _n_before = len(_s1b.messages)
    _s1b.messages = []
    _s1b._loop = agent_loop.LoopState("x")   # 模拟已注入过的状态
    _s1b._loop_start(agent_loop)
    check("LP11-5 重复 _loop_start 不崩（重入安全）", isinstance(_s1b._loop, agent_loop.LoopState))

    # ② 单硬要求 → 不注入
    _s2 = _S(task_state.TaskState("生成视频", ["video_gen"]))
    check("LP11-6 行为级：单硬要求不注入计划",
          _s2._loop_start(agent_loop) is False and _s2.messages == [])
    check("LP11-7 行为级：单硬要求阶段=execute（非卡在 plan）",
          _s2._loop.phase == "execute", _s2._loop.phase)

    # ③ verify：固化记录但不注入任何消息
    _s3 = _S(task_state.TaskState("目标", ["video_gen", "write_file"]))
    _s3._loop_start(agent_loop)
    _s3._tstate.record_tool("video_gen", {}, "ok", ok=True, step=1)
    _msgs_before = len(_s3.messages)
    _got3 = _s3._loop_verify(agent_loop)
    check("LP11-8 行为级：_loop_verify 返回 True", _got3 is True)
    check("LP11-9 行为级：_loop_verify **不注入任何消息**（补做归task_state独管）",
          len(_s3.messages) == _msgs_before,
          "%d -> %d" % (_msgs_before, len(_s3.messages)))
    check("LP11-10 行为级：阶段进 verify 且 verified=False（有未达标）",
          _s3._loop.phase == "verify" and _s3._loop.verified is False,
          "%s verified=%r" % (_s3._loop.phase, _s3._loop.verified))
    # 全部达标 → verified=True
    _s3b = _S(task_state.TaskState("目标", ["video_gen"]))
    _s3b._loop_start(agent_loop)
    _s3b._tstate.record_tool("video_gen", {}, "ok", ok=True, step=1)
    _s3b._loop_verify(agent_loop)
    check("LP11-11 行为级：全部达标时 verified=True",
          _s3b._loop.verified is True, str(_s3b._loop.verified))

    # ④ summary：追加机器小结
    _s4 = _S(task_state.TaskState("目标", ["video_gen", "write_file"]))
    _s4._loop_start(agent_loop)
    _s4._tstate.record_tool("video_gen", {}, "ok", ok=True, step=1)
    _got4 = _s4._loop_summary(agent_loop)
    check("LP11-12 行为级：_loop_summary 追加小结并返回 True", _got4 is True)
    check("LP11-13 行为级：小结经 stream_commit 发出",
          len(_s4.emitted) == 1 and "本轮执行小结" in _s4.emitted[0],
          str(_s4.emitted[:1]))
    check("LP11-14 行为级：阶段进 summarize 且 summary_emitted 置位",
          _s4._loop.phase == "summarize" and _s4._loop.summary_emitted is True)
    # 幂等：第二次不再追加
    _got4b = _s4._loop_summary(agent_loop)
    check("LP11-15 行为级：第二次 _loop_summary 不重复追加",
          _got4b is False and len(_s4.emitted) == 1,
          "ret=%r emitted=%d" % (_got4b, len(_s4.emitted)))

    # ⑤ 无事实 → 不追加
    _s5 = _S(task_state.TaskState("目标", ["video_gen"]))
    _s5._loop_start(agent_loop)
    check("LP11-16 行为级：零工具调用不追加小结",
          _s5._loop_summary(agent_loop) is False and _s5.emitted == [])

    # ⑥ fail-open：_tstate 为 None 全部不崩
    _s6 = _S(None)
    check("LP11-17 行为级：_tstate=None 时 _loop_start 不抛",
          _s6._loop_start(agent_loop) in (True, False))
    check("LP11-18 行为级：_tstate=None 时 _loop_verify 返回 False",
          _s6._loop_verify(agent_loop) is False)
    check("LP11-19 行为级：_tstate=None 时 _loop_summary 返回 False",
          _s6._loop_summary(agent_loop) is False)
    # _loop 为 None（start 失败后）也安全
    _s7 = _S(task_state.TaskState("目标", ["video_gen"]))
    _s7._loop = None
    check("LP11-20 行为级：_loop=None 时 verify/summary 返回 False 不抛",
          _s7._loop_verify(agent_loop) is False
          and _s7._loop_summary(agent_loop) is False)
    # _loop_step 在 None 时安全
    _s7._loop_step(3)
    check("LP11-21 行为级：_loop=None 时 _loop_step 不抛", True)
    # 异常注入：账本方法抛异常 → 接线不崩
    class _Boom(object):
        def __getattr__(self, n):
            raise RuntimeError("boom")

    _s8 = _S(_Boom())
    try:
        _r = (_s8._loop_start(agent_loop), _s8._loop_verify(agent_loop),
              _s8._loop_summary(agent_loop))
        check("LP11-22 行为级：账本方法全抛异常时接线仍不崩", True, str(_r))
    except Exception as e:
        check("LP11-22 行为级：账本方法全抛异常时接线仍不崩", False,
              "%s: %s" % (type(e).__name__, e))
    # _loop_state_dict 可序列化
    _d = _s4._loop_state_dict()
    check("LP11-23 行为级：_loop_state_dict 可序列化且含阶段",
          isinstance(_d, dict) and _d.get("phase") == "summarize", str(_d))
    check("LP11-24 行为级：无 _loop 时 _loop_state_dict 返回 {}",
          _S(task_state.TaskState())._loop_state_dict() == {})
except Exception as _e:
    import traceback
    check("LP11-0 行为级：mixin 方法真可调", False,
          "%s: %s" % (type(_e).__name__, _e))
    traceback.print_exc()

# ============================================================
# LP12  判据自证（防空谓词 / 防注释剔除器失效）
# ============================================================
print("\n--- LP12 判据自证 ---")
# ① 注释剔除器自证：注释文本必须抓不到、真代码必须抓得到
check("LP12-1 剔除注释后，注释文本抓不到（防空谓词）",
      "# v4.226（四阶段循环）" not in _agx)
check("LP12-2 剔除注释后，带行尾注释的真调用点仍抓得到",
      "import agent_loop" in _agx)
check("LP12-3 剔除器对 agent_loop.py 同样有效（保留代码）",
      "def should_plan(" in _code_lines("agent_loop.py"))
check("LP12-4 剔除器对 agent_loop_mixin.py 同样有效（保留代码）",
      "def _loop_start(" in _code_lines("agent_loop_mixin.py"))
# ② 反向钉子：判定必须真在 agent_loop.py，且 mixin 不得**复制**判定
_mx = _code_lines("agent_loop_mixin.py")
check("LP12-5 mixin 不自行判定 should_plan（真源唯一）",
      "should_plan(" not in _mx.replace("agent_loop.should_plan(", "SHOULDPLAN(")
      or _mx.count("agent_loop.should_plan(") >= 1,
      "mixin 只能调 agent_loop.should_plan")
check("LP12-6 mixin 全部判定都经 agent_loop. 前缀（无本地重实现）",
      ("missing_tools()" not in _mx and "missing_artifacts()" not in _mx
       and "PLAN_MIN_REQUIREMENTS" not in _mx and ".is_complete()" not in _mx),
      "mixin 出现账本判定=判据漂移风险")
check("LP12-6b mixin 的 verified 真走 agent_loop.is_verified（单一真源）",
      "agent_loop.is_verified(ts)" in _mx)
_lp = _code_lines("agent_loop.py")
check("LP12-7 should_plan/should_verify/should_summarize 都真在 agent_loop.py",
      all(("def %s(" % f) in _lp
          for f in ("should_plan", "should_verify", "should_summarize")))
# ③ 假动作自证：注释剔除后若整体失效，判据必须翻红（反向验证过滤器会抓坏样本）
# ③ 坏样本自证：剔除器遇到「注释里写了调用点」的坏样本必须抓不到
#（防空谓词 —— 若剔除器哪天退化成「原样返回」，这两条会立刻翻红；
#  样本落到 ROOT 下，让它走与业务判定**完全同一条**代码路径）
_bad = os.path.join(ROOT, "_zz_bad_probe_226.py")
with open(_bad, "w", encoding="utf-8") as _f:
    _f.write("x = 1  # self._loop_summary(agent_loop)\n"
             "y = '带 # 号的字符串'  # 真注释\n")
try:
    _xb = _code_lines("_zz_bad_probe_226.py")
    check("LP12-8 坏样本自证：注释里的调用点抓不到",
          "self._loop_summary(agent_loop)" not in _xb, repr(_xb))
    check("LP12-9 坏样本自证：字符串里的 # 不被误切",
          "带 # 号的字符串" in _xb, repr(_xb))
finally:
    try:
        os.remove(_bad)
    except Exception:
        pass
# 防空负负得正：_ag_code() 若因任何原因读到空/截断内容，LP12-1~9 会**集体假绿**。
# 原判据写死 `len(_ag) > 100000` —— 那个数字是「v4.226.0 当时文件大小」的经验值，
# 却被当成硬编码阈值。此后 P1-2 等轮次把代码**外移到 agent_result_mixin.py** 等模块，
# agent.py 合理地变小（100000 → 98679），于是这条「防假绿」的判据自己先红了，
# 逼着人改判据 —— 恰好违背它自己的目的。
# 现改为**自锚定**：下限用真实文件字节数（外移只会变小，但不会小到离谱），
# 并且额外钉住「与 git HEAD 相比不得缩小超过 30%」，这样：
#   ①真的读空了/读截断了 → 立刻红（原意保住）；
#   ②正常的模块外移 → 不再误报（不再逼人改判据）；
#   ③真把 agent.py 掏空一大半 → 仍会红（防「删功能顺便把判据调松」）。
_AG_FILE = os.path.join(ROOT, "agent.py")
try:
    _ag_bytes = os.path.getsize(_AG_FILE)
except OSError:
    _ag_bytes = 0
check("LP12-10 源码读取非空（防空负负得正）",
      _ag_bytes > 60000, "agent.py 仅 %d 字节" % _ag_bytes)
check("LP12-11 代码文本非空（_ag_code 真读到东西）",
      len(_ag) > 30000, "_ag_code 仅 %d 字符" % len(_ag))
# 自锚定：相对 HEAD 不得缩小超过 30%（HEAD 不可用时跳过，不因无 git 而红）
try:
    import subprocess as _sp
    _head = _sp.run(["git", "show", "HEAD:agent.py"], cwd=ROOT,
                    capture_output=True, timeout=60)
    if _head.returncode == 0 and _head.stdout:
        _hb = len(_head.stdout)
        if _hb > 0:
            check("LP12-12 agent.py 未被掏空（较 HEAD 缩小不超过 30%）",
                  _ag_bytes >= _hb * 0.7,
                  "HEAD=%d 字节, 现=%d 字节" % (_hb, _ag_bytes))
except Exception:
    pass

print("\n" + "=" * 62)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 62)
sys.exit(1 if FAIL else 0)