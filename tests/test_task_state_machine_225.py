# -*- coding: utf-8 -*-
"""tests/test_task_state_machine_225.py —— v4.225 P2 主 Agent 任务状态机判据

核心不变量（改坏必红）：
  TS1  required_from_text 只认「字面点名」，不认关键词命中
        （「写口播文案」绝不能变成硬要求 video_gen —— 那是逼模型干额外的活）
  TS2  词边界：my_image_genner 不得命中 image_gen
  TS3  记账：record_tool 累积 used/failed/产物，产物按磁盘真实存在性判定
  TS4  判定：missing_tools / missing_artifacts / is_complete / pending
  TS5  四道 gate：should_nudge 任一不满足即False（这是「零行为变化」的保证）
  TS6  nudge 文案必须**说清缺什么**，且带反「空泛交差」约束
  TS7  agent.py 接线钉子（记账点 / 收尾闸门 / 续跑放开）
"""
import os
import sys
import tempfile

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
print("v4.225 P2 任务状态机判据")
print("=" * 62)

import task_state  # noqa: E402


# ============================================================
# TS1  required_from_text 只认字面点名
# ============================================================
print("\n--- TS1 硬要求口径（只认点名） ---")
check("TS1-1 字面点名 video_gen → 硬要求",
      ("video_gen", "video_gen") in task_state.required_from_text("用 video_gen 生成个视频"),
      str(task_state.required_from_text("用 video_gen 生成个视频")))
check("TS1-2 字面点名 web_search → 硬要求",
      bool(task_state.required_from_text("调web_search 查一下最近新闻")))
# 关键反例：关键词命中但用户没点名 → 绝不能成硬要求
check("TS1-3 「帮我写一段口播文案」→ 无硬要求（口播命中 video_gen 词但不算）",
      task_state.required_from_text("帮我写一段口播文案") == [],
      str(task_state.required_from_text("帮我写一段口播文案")))
check("TS1-4 「生成个视频」→ 无硬要求（那是意图不是点名）",
      task_state.required_from_text("生成个视频") == [],
      str(task_state.required_from_text("生成个视频")))
check("TS1-5 「把 Chrome 杀掉」→ 无硬要求",
      task_state.required_from_text("把 Chrome 杀掉") == [],
      str(task_state.required_from_text("把 Chrome 杀掉")))
check("TS1-6 空串 → 空", task_state.required_from_text("") == [])
check("TS1-7 None → 空", task_state.required_from_text(None) == [])
# 多个点名按原文顺序去重
_multi = task_state.required_from_text("先 web_search 查，再用 write_file 存")
check("TS1-8 多个点名按顺序返回",
      [n for n, _w in _multi] == ["web_search", "write_file"], str(_multi))
check("TS1-9 重复点名去重",
      [n for n, _w in task_state.required_from_text("web_search 然后再 web_search")]
      == ["web_search"])


# ============================================================
# TS2  词边界
# ============================================================
print("\n--- TS2 词边界（防子串误伤） ---")
check("TS2-1 my_image_genner 不命中 image_gen",
      task_state.required_from_text("用my_image_genner 那个工具") == [],
      str(task_state.required_from_text("用my_image_genner 那个工具")))
check("TS2-2 image_genner 不命中 image_gen",
      task_state.required_from_text("image_genner 报错") == [],
      str(task_state.required_from_text("image_genner 报错")))
check("TS2-3 xweb_search 不命中 web_search",
      task_state.required_from_text("xweb_search 挂了") == [],
      str(task_state.required_from_text("xweb_search 挂了")))
check("TS2-4 独立成词的 web_search 命中",
      bool(task_state.required_from_text("用 web_search 搜")))


# ============================================================
# TS3  记账
# ============================================================
print("\n--- TS3 记账 ---")
st = task_state.TaskState("用 write_file 存文件", ["write_file"])
st.record_tool("web_search", {"query": "x"}, "结果", ok=True, step=1)
check("TS3-1 记账累积 used_tools", "web_search" in st.used_tools)
check("TS3-2 非硬要求工具不进 required", "web_search" not in st.required_tools)
check("TS3-3 used_tools 不重复", (
    lambda: (st.record_tool("web_search", {}, "", ok=True, step=2),
             st.used_tools.count("web_search") == 1)[1])())

_tmpd = tempfile.mkdtemp(prefix="_ts225_")
_real = os.path.join(_tmpd, "out.txt")
with open(_real, "w", encoding="utf-8") as f:
    f.write("hello")
_st2 = task_state.TaskState("用 write_file 存文件", ["write_file"])
_st2.record_tool("write_file", {"path": _real}, "已写入", ok=True, step=1)
check("TS3-4 write_file 从 args.path 提取产物", len(_st2.artifacts) == 1,
      str(_st2.artifacts))
check("TS3-5 产物存在 → exists=True", _st2.artifacts[0][1] is True,
      str(_st2.artifacts))
check("TS3-6 调过硬要求 → missing_tools 空", _st2.missing_tools() == [],
      str(_st2.missing_tools()))
check("TS3-7 is_complete=True", _st2.is_complete() is True)

_missing = os.path.join(_tmpd, "never_written.txt")
_st3 = task_state.TaskState("用 write_file 存文件", ["write_file"])
_st3.record_tool("write_file", {"path": _missing}, "声称成功", ok=True, step=1)
check("TS3-8 产物不存在 → exists=False", _st3.artifacts[0][1] is False,
      str(_st3.artifacts))
check("TS3-9 missing_artifacts 报出来",
      _st3.missing_artifacts() == [(_missing, "write_file")],
      str(_st3.missing_artifacts()))
check("TS3-10 is_complete=False（声称成功但文件不存在）",
      _st3.is_complete() is False)
check("TS3-11 pending 文案点明路径",
      _missing in _st3.pending()[0], str(_st3.pending()))

# 失败记账
_st4 = task_state.TaskState("用 web_search 查", ["web_search"])
_st4.record_tool("web_search", {}, "报错", ok=False, step=1)
check("TS3-12 失败记进 failed_tools", "web_search" in _st4.failed_tools,
      str(_st4.failed_tools))
check("TS3-13 失败仍算 used（调过了）", _st4.missing_tools() == [],
      str(_st4.missing_tools()))
check("TS3-14 to_dict.status=failed",
      _st4.to_dict()["status"] == "failed", _st4.to_dict()["status"])

# 非产文件工具不登记产物（否则等一个永不出现的文件 → 卡死）
_st5 = task_state.TaskState("用 web_search 查", ["web_search"])
_st5.record_tool("web_search", {"path": _missing}, "结果", ok=True, step=1)
check("TS3-15 非产文件工具不登记产物", _st5.artifacts == [], str(_st5.artifacts))

# note_artifact 外部登记
_st6 = task_state.TaskState()
_st6.note_artifact(_real, "image_gen")
check("TS3-16 note_artifact 登记存在产物",
      _st6.artifacts and _st6.artifacts[0][1] is True, str(_st6.artifacts))

# 产物去重
_st6.note_artifact(_real, "image_gen")
check("TS3-17 产物登记去重", len(_st6.artifacts) == 1, str(_st6.artifacts))

# 坏参数不崩
_st7 = task_state.TaskState()
try:
    _st7.record_tool("", None, None, ok=True, step=0)
    _st7.record_tool("write_file", "not-a-dict", "r", ok=True, step=0)
    _st7.record_tool(None, {"path": 123}, None, ok=True, step=0)
    check("TS3-18 坏参数不崩", True)
except Exception as e:
    check("TS3-18 坏参数不崩", False, repr(e))


# ============================================================
# TS4  判定
# ============================================================
print("\n--- TS4 判定 ---")
_st8 = task_state.TaskState("用 video_gen 生视频", ["video_gen"])
check("TS4-1 没调过 → missing_tools 报出",
      _st8.missing_tools() == ["video_gen"], str(_st8.missing_tools()))
check("TS4-2 pending 非空", bool(_st8.pending()))
check("TS4-3 is_complete=False", _st8.is_complete() is False)
_st8.record_tool("video_gen", {"path": _real}, "ok", ok=True, step=1)
check("TS4-4 调过后 complete", _st8.is_complete() is True)
check("TS4-5 complete 时 pending 空", _st8.pending() == [], str(_st8.pending()))

# 无硬要求 → has_requirements False → 完全不介入
_st9 = task_state.TaskState("随便聊聊")
check("TS4-6 无硬要求 has_requirements=False", _st9.has_requirements() is False)
check("TS4-7 无硬要求 is_complete=True（不阻塞）", _st9.is_complete() is True)
check("TS4-8 无硬要求 summary 非空", bool(_st9.summary()))

# required_tools 去重保序
_st10 = task_state.TaskState("g", ["web_search", "web_search", "write_file", None, ""])
check("TS4-9 required 去重保序剔空",
      _st10.required_tools == ["web_search", "write_file"],
      str(_st10.required_tools))


# ============================================================
# TS5  四道 gate
# ============================================================
print("\n--- TS5 should_nudge 四道 gate ---")
_n = task_state.TaskState("用 video_gen 生视频", ["video_gen"])
check("TS5-1 有硬要求+未达标+有余量+未注入 → True",
      _n.should_nudge(1, 10, already_injected=False) is True)
check("TS5-2 已注入过 → False（只补一轮）",
      _n.should_nudge(1, 10, already_injected=True) is False)
check("TS5-3 步数耗尽 → False（没下一轮可跑）",
      _n.should_nudge(10, 10, already_injected=False) is False)
check("TS5-4 超步数 → False",
      _n.should_nudge(99, 10, already_injected=False) is False)
_ok = task_state.TaskState("用 video_gen 生视频", ["video_gen"])
_ok.record_tool("video_gen", {}, "ok", ok=True, step=1)
check("TS5-5 已达标 → False（零行为变化）",
      _ok.should_nudge(1, 10, already_injected=False) is False)
_noreq = task_state.TaskState("随便聊聊")
check("TS5-6 无硬要求 → False（完全不介入）",
      _noreq.should_nudge(1, 10, already_injected=False) is False)
# mark_nudged 后自身闸生效（already_injected 省略时读自身状态）
_m = task_state.TaskState("用 video_gen 生视频", ["video_gen"])
_m.mark_nudged()
check("TS5-7 mark_nudged 后自身闸生效", _m.should_nudge(1, 10) is False)
check("TS5-8 nudge_instruction 在达标时为空串",
      _ok.nudge_instruction() == "")
check("TS5-9 max_steps=0（不限）→ 仍可 nudge",
      _n.should_nudge(999, 0, already_injected=False) is True)

# 续跑场景：主轮注入过，续跑轮放开后仍只补一轮
_r = task_state.TaskState("用 video_gen 生视频", ["video_gen"])
_r.mark_nudged()
check("TS5-10 续跑轮放开（already_injected=False）后可再补一轮",
      _r.should_nudge(1, 10, already_injected=False) is True)


# ============================================================
# TS6  nudge 文案
# ============================================================
print("\n--- TS6 nudge 文案 ---")
_i = _n.nudge_instruction()
check("TS6-1 文案非空", bool(_i))
check("TS6-2 点名缺哪个工具", "video_gen" in _i, _i)
check("TS6-3 带反空泛交差约束",
      "不要只在文字里声称做过" in _i or "不要输出空泛" in _i, _i)
check("TS6-4 允许客观做不到时说明",
      "做不到" in _i, _i)
_i2 = _st3.nudge_instruction()
check("TS6-5 产物缺失文案带路径", _missing in _i2, _i2)


# ============================================================
# TS7  agent.py 接线
# ============================================================
print("\n--- TS7 接线（两层：agent.py 调用点 + mixin 实现）---")
_ag = open(os.path.join(ROOT, "agent.py"), encoding="utf-8").read()
_ag_code = "\n".join(ln for ln in _ag.split("\n")
                     if not ln.lstrip().startswith("#"))
_mx = open(os.path.join(ROOT, "agent_task_mixin.py"), encoding="utf-8").read()
_mx_code = "\n".join(ln for ln in _mx.split("\n")
                     if not ln.lstrip().startswith("#"))
# v4.226：`_handle_tool_result` 连同记账接线一起搬进了 agent_result_mixin.py
#（拆分红线要求，见 tests/test_split_216.py 的 D2）。接线扫描源随之扩到
# **两个 mixin** —— 判据本身没放宽：仍要求「记账接线真实存在」，
# 只是不再假设它一定写在 agent.py 里。
_rm = open(os.path.join(ROOT, "agent_result_mixin.py"), encoding="utf-8").read()
_rm_code = "\n".join(ln for ln in _rm.split("\n")
                     if not ln.lstrip().startswith("#"))
_wire_code = _ag_code + "\n" + _mx_code + "\n" + _rm_code

# —— 第一层：agent.py 只留调用点（接线搬出是 v4.225 的规模红线要求）——
check("TS7-1 agent.py import task_state", "import task_state" in _ag_code)
check("TS7-1b agent.py import AgentTaskMixin",
      "AgentTaskMixin" in _ag_code)
check("TS7-1c AgentWorker 真的 mixin 了 AgentTaskMixin",
      "class AgentWorker(AgentTaskMixin," in _ag_code,
      _ag_code[_ag_code.find("class AgentWorker"):][:60]
      if "class AgentWorker" in _ag_code else "找不到类声明")
for _tag, _needle in [("TS7-2 run() 里建账本", "self._tstate_init(task_state)"),
                      ("TS7-5 _handle_tool_result 里记账",
                       "self._tstate_record(task_state,"),
                      ("TS7-7 收尾闸门调 should_nudge",
                       "self._tstate_nudge_now(task_state,"),
                      ("TS7-9 主循环同步步号", "self._tstate_step(step)"),
                      ("TS7-10 续跑轮步号延续",
                       "self._tstate_resume_step(self._max_steps, rstep)")]:
    # v4.226：接线分布在 agent.py + 两个 mixin（见 _wire_code 定义处的说明）
    check(_tag, _needle in _wire_code)

# —— 第二层：mixin 里真实现（调用点存在 ≠ 实现存在）——
for _tag, _needle in [
        ("TS7-3 账本用 required_from_text 取硬要求",
         "task_state.required_from_text("),
        ("TS7-4 _tstate_nudged 初始化", "self._tstate_nudged = False"),
        ("TS7-6 工具回传产物也登记", "_ts.note_artifact("),
        ("TS7-8 注入后置 nudged 标记", "self._tstate_nudged = True"),
        ("TS7-12 记账复用 _tool_result_looks_failed",
         "not failed_checker(name, result_str)")]:
    check(_tag, _needle in _mx_code)
check("TS7-7b mixin 收尾闸门真调 should_nudge",
      "_ts.should_nudge(" in _mx_code)
check("TS7-6b agent.py 把失败判据注入给 mixin",
      "self._tool_result_looks_failed)" in _ag_code,
      "记账若不注入既有失败判据就会另立口径 → 两处判据漂移")
check("TS7-13 mixin 定义 6 个接线方法",
      all(("def _tstate_%s(" % m) in _mx_code
          for m in ("init", "record", "step", "resume_step",
                    "resume_reset_nudge", "nudge_now")),
      "接线方法不全 → 某个调用点是死引用")

# —— 第三层：mixin 内部的**动作**（前两层钉的都是「调用点/定义」，
#    若 mixin 方法体被掏空成空壳，前两层照样全绿 —— 必须钉动作本身）——
check("TS7-15 mixin 里真调 _ts.record_tool（记账动作没被掏空）",
      "_ts.record_tool(" in _mx_code,
      "mixin 记账动作消失 → 账本永远空")
check("TS7-16 mixin 里真调 _ts.should_nudge（闸门判定没被掏空）",
      "_ts.should_nudge(" in _mx_code,
      "mixin 闸门判定消失 → 账本形同虚设")
check("TS7-17 mixin 里真调 _ts.nudge_instruction（补做指令没被掏空）",
      "_ts.nudge_instruction()" in _mx_code)
check("TS7-18 mixin 里真调 _ts.mark_nudged（注入后置状态没被掏空）",
      "_ts.mark_nudged()" in _mx_code)
check("TS7-19 mixin 里真调 _ts.note_artifact（产物登记没被掏空）",
      "_ts.note_artifact(" in _mx_code)

# —— 第四层：**行为级**接线验证（源码匹配抓不住 `if False and ...` 这类
#    短路变异 —— 串还在，判据照样全绿）。用真 stub 跑一遍 mixin 方法，
#    看它是否真把指令注入 messages / 真返回 True。这是唯一能抓住
#    「接线被掏空但字面还在」的层次。——
try:
    from agent_task_mixin import AgentTaskMixin

    class _Stub(AgentTaskMixin):
        def __init__(self):
            self.messages = []
            self._tstate_nudged = False
            self._force_next = False
            self._idle_steps = 0
            self._emitted = []

        def _last_user_text(self):
            return "用 video_gen 生成个视频"

        def _emit_status(self, s):
            self._emitted.append(s)

    # ① 建账本：字面点名 video_gen → 应成为硬要求
    _s1 = _Stub()
    _s1._tstate_init(task_state)
    check("TS7-20 行为级：_tstate_init 建出账本且硬要求正确",
          getattr(_s1, "_tstate", None) is not None
          and _s1._tstate.required_tools == ["video_gen"],
          str(getattr(_s1, "_tstate", None)))
    check("TS7-21 行为级：init 后 _tstate_nudged=False",
          _s1._tstate_nudged is False)

    # ② 记账：注入一个失败判据，工具应被记进账本
    _s1._tstate_record(task_state, "web_search", {"q": "x"}, "结果", [],
                       lambda n, r: False)
    check("TS7-22 行为级：_tstate_record 真把工具记进账本",
          "web_search" in _s1._tstate.used_tools,
          str(_s1._tstate.used_tools))
    _s2 = _Stub()
    _s2._tstate_init(task_state)
    _s2._tstate_record(task_state, "video_gen", {}, "报错", [],
                       lambda n, r: True)   # 判定为「失败」
    check("TS7-23 行为级：失败判据被真正采用（记进 failed_tools）",
          "video_gen" in _s2._tstate.failed_tools,
          str(_s2._tstate.failed_tools))

    # ③ 闸门：未调 video_gen → 应注入并返回 True
    _s3 = _Stub()
    _s3._tstate_init(task_state)
    _got = _s3._tstate_nudge_now(task_state, 1, 10)
    check("TS7-24 行为级：未达标时闸门返回 True", _got is True, repr(_got))
    check("TS7-25 行为级：真把补做指令注入 messages",
          len(_s3.messages) == 1
          and "任务未完成检查" in _s3.messages[0]["content"],
          str(_s3.messages)[:120])
    check("TS7-26 行为级：注入后置 _tstate_nudged=True",
          _s3._tstate_nudged is True)
    check("TS7-27 行为级：注入后置 _force_next=True",
          _s3._force_next is True)
    check("TS7-28 行为级：注入的指令带 _internal 标记",
          _s3.messages[0].get("_internal") is True)
    # 第二次调用必须被「本轮注入过」闸拦住
    _s3.messages.append({"role": "user", "content": "占位"})
    _got2 = _s3._tstate_nudge_now(task_state, 2, 10)
    check("TS7-29 行为级：同轮第二次被闸拦住（不重复注入）",
          _got2 is False and len(_s3.messages) == 2,
          "ret=%r msgs=%d" % (_got2, len(_s3.messages)))

    # ④ 已完成 → 完全不介入
    _s4 = _Stub()
    _s4._tstate_init(task_state)
    _s4._tstate_record(task_state, "video_gen", {}, "ok", [],
                       lambda n, r: False)
    _got3 = _s4._tstate_nudge_now(task_state, 1, 10)
    check("TS7-30 行为级：已达标时闸门返回 False 且不注入",
          _got3 is False and _s4.messages == [],
          "ret=%r msgs=%d" % (_got3, len(_s4.messages)))

    # ⑤ 步数耗尽 → 不介入
    _s5 = _Stub()
    _s5._tstate_init(task_state)
    check("TS7-31 行为级：步数耗尽时不注入",
          _s5._tstate_nudge_now(task_state, 10, 10) is False)
    # ⑥ 续跑放开闸后仍只补一轮
    _s5._tstate_resume_reset_nudge()
    _s5._tstate_step(1)
    check("TS7-32 行为级：续跑放开后可补一轮",
          _s5._tstate_nudge_now(task_state, 1, 10) is True)
    check("TS7-33 行为级：续跑补完后再调被拦住",
          _s5._tstate_nudge_now(task_state, 1, 10) is False)
    # ⑦ 步号延续
    _s6 = _Stub()
    _s6._tstate_init(task_state)
    _s6._tstate_resume_step(10, 3)
    check("TS7-34 行为级：续跑步号延续（10+3=13）",
          _s6._tstate.current_step == 13, str(_s6._tstate.current_step))
    # ⑧ _last_user_text（多模态 list content）
    class _M(AgentTaskMixin):
        def __init__(self, msgs):
            self.messages = msgs
    check("TS7-35 行为级：_last_user_text 取字符串 content",
          _M([{"role": "user", "content": "abc"}])._last_user_text() == "abc")
    check("TS7-36 行为级：_last_user_text 取多模态 text part",
          _M([{"role": "user", "content": [
              {"type": "text", "text": "帮我生"},
              {"type": "image_url", "image_url": {"url": "x"}}]}])._last_user_text()
          == "帮我生")
    check("TS7-37 行为级：_last_user_text 无 user 时返回空串",
          _M([{"role": "system", "content": "s"}])._last_user_text() == "")
except Exception as _e:
    import traceback
    check("TS7-20 行为级：mixin 方法真可调", False,
          "%s: %s" % (type(_e).__name__, _e))

# 闸门必须在 break 之前
_gate = _ag_code.find("self._tstate_nudge_now(")
_brk = _ag_code.find("break", _gate)
check("TS7-11 闸门在 break 之前", _gate != -1 and _brk > _gate,
      "gate=%d break=%d" % (_gate, _brk))

# 规模红线：agent.py 净增接线代码会撞 v4.216 立的 2400 行红线
_ag_lines = len(_ag.split("\n"))
check("TS7-14 agent.py 守住拆分红线 < 2400 行", _ag_lines < 2400,
      "当前 %d 行（接线应外移到 agent_task_mixin）" % _ag_lines)


# ============================================================
# 清理临时目录
# ============================================================
try:
    for _f in os.listdir(_tmpd):
        os.remove(os.path.join(_tmpd, _f))
    os.rmdir(_tmpd)
except Exception:
    pass

print("\n" + "=" * 62)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 62)
sys.exit(1 if FAIL else 0)