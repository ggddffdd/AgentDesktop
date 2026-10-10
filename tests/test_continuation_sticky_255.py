# -*- coding: utf-8 -*-
"""v4.255.0 判据：续接指令 × 会话粘性（B 包）

judge-first：本文件先于实现编写，预期红。改完源码后转绿。

背景（实测证据见 _dev_history/_probe_sticky_255.py，21 条语料 13 条不符）
-----------------------------------------------------------------------
「再来一个」「再详细点」「换一个风格」这类**续接指令**，本身不含任何动作词，
被判 unknown + needs_action=False。后果（agent.py 三处真实读点）：

  · 946 `force_required = step==1 and self._needs_action` → **首步不强制**；
  · 1183 nudge 门槛同样看 `self._needs_action` → **空转也不催**；
  · 结果：模型"光说不做"两道防线同时失效，用户看到的是「好的，我来」然后没下文。

「继续」只是碰巧躺在 `agent_text._ACTION_KEYWORDS` 里才勉强能用 —— 靠词表
蒙对，换个说法（「再来一个」）立刻失效。词表枚举补不完。

本包的修法（B 方案）：**接事实，不猜词**。
`route_judge.has_recent_tool`（v4.111 回放已证明的事实判据）此前只喂回放、
agent 意图层完全没接 —— 把它接进 `intent.classify`：

    弱意图（unknown / action 但未认定要动手）+ 命中续接闭集 + 最近确有工具
    → needs_action = True

三条闸缺一不可，各配反向用例：

  S3 没有工具事实的会话说「再来一个」→ **不许**凭空派活（事实判据是主角，
     不是装饰：去掉它，本包退化成"又补一批词"，V3 变哑弹）。
  S4 否定 / 疑问 / 讨论 / 主观评价语境不接管（那些路径各自有 early-return 语义）。
  S7/S8 长文本（任务说明书里常写「失败了再试一次」）、主观感受句不接管。

纪律（与项目同源）：
  · 行为级断言：全部走真实函数返回值，不查源码字符串（静态串判据恒真，P9/P33）。
  · 只加不减：只在「判据没看出要干活」时**放宽** needs_action，不收紧任何既有结论。
  · fail-open：messages 为 None / 垃圾 / 模块缺失 → 不接管，退回改动前行为。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import intent  # noqa: E402
import agent_text  # noqa: E402

PASS = 0
FAIL = 0


def ck(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, extra))


# ---------------------------------------------------------------- 语料
# HOT：会话刚调过工具（任务进行中）—— 真实 agent 回灌结构
HOT = [
    {"role": "user", "content": "帮我写篇公众号文章"},
    {"role": "assistant", "content": "", "tool_calls": [{"name": "write_file"}]},
    {"role": "tool", "name": "write_file", "content": "ok"},
    {"role": "assistant", "content": "已经写好了，存到桌面了。"},
]
# COLD：纯聊天，从未调过工具
COLD = [
    {"role": "user", "content": "今天累死了"},
    {"role": "assistant", "content": "辛苦了，早点休息。"},
    {"role": "user", "content": "是啊"},
    {"role": "assistant", "content": "要不要先喝口水？"},
]

# HOT 下应被续接接管（needs_action=True）
HOT_YES = ("再来一个", "再详细点", "再来一遍", "再来一张", "换一个风格",
           "不够详细，再加点", "还差个封面", "按这个再来一份",
           "再深入一点", "换个音乐", "再多来两个", "继续", "重新做")
# 任何情况下都不该被续接接管
NEVER = ("好的", "谢谢", "为什么这么慢", "别再搜了", "我觉得不够详细",
         "刚才那个方案再讨论讨论", "太简略了，展开说说")
# ⚠ 刻意不收「之前那个再生成是不是有问题」：它**现状就是** needs_action=True
# （裸「生成」命中 _ACTION_KEYWORDS），与本包无关 —— 拿它当反例会造出一条
# 改完源码也永远红的假判据（踩过 A 包 V7 哑弹的同一类坑）。


def _dc_safe(t, m):
    """函数还没实现时返回哨兵（不等于 False/True），判据照常翻红而不是崩掉。"""
    if _dc is None:
        return "NO_FUNC"
    try:
        return _dc(t, m)
    except Exception as e:  # noqa: BLE001
        return "EXC:%r" % (e,)


def clf(text, msgs):
    try:
        return intent.classify(text, None, msgs)
    except TypeError:
        return intent.classify(text, None)


print("=" * 74)
print("v4.255.0 续接指令 × 会话粘性判据（B 包）")
print("=" * 74)

# ---------------------------------------------------------------- S1 纯函数
print("\n[S1] 纯函数 detect_continuation 存在且自身闭合")
_dc = getattr(intent, "detect_continuation", None)
ck(_dc is not None, "S1 intent.detect_continuation 存在")
if _dc is not None:
    ck(_dc("再来一个", HOT) is True, "S1 续接词 + 有工具 → True",
       "→ %r" % _dc("再来一个", HOT))
    ck(_dc("再来一个", COLD) is False, "S1 续接词 + 无工具 → False（事实判据）",
       "→ %r" % _dc("再来一个", COLD))
    ck(_dc("今天天气不错", HOT) is False, "S1 无续接词 → False（不靠粘性乱派活）",
       "→ %r" % _dc("今天天气不错", HOT))
    ck(_dc("再来一个", None) is False, "S1 messages=None → False（fail-open）")
    ck(_dc("", HOT) is False, "S1 空串 → False")
    ck(_dc("再来一个", "这不是列表") is False, "S1 垃圾 messages → 不崩且 False")

# ---------------------------------------------------------------- S2 正例
print("\n[S2] HOT 会话下续接指令必须 needs_action=True（首步强制 + nudge 才开）")
for txt in HOT_YES:
    _it = clf(txt, HOT)
    ck(bool(_it) and _it.needs_action is True,
       "S2 续接接管：%s" % txt,
       "→ needs_action=%r kind=%r" % (None if _it is None else _it.needs_action,
                                      None if _it is None else _it.kind))
_it = clf("再来一个", HOT)
ck(bool(_it) and getattr(_it, "continuation", False) is True,
   "S2 continuation 标记置位（可观测）",
   "→ %r" % getattr(_it, "continuation", "字段不存在"))

# ---------------------------------------------------------------- S3 事实闸
print("\n[S3] COLD 会话（没调过工具）绝不能凭空派活 —— 事实判据是真主角")
for txt in ("再来一个", "再详细点", "换一个风格", "再来一张"):
    _it = clf(txt, COLD)
    ck(bool(_it) and _it.needs_action is False,
       "S3 无工具事实不接管：%s" % txt,
       "→ %r" % (None if _it is None else _it.needs_action))
# 门槛：窗口内【只有】工具离得很远（超出 k）也不算
_FAR = [
    {"role": "tool", "name": "write_file", "content": "ok"},
    {"role": "assistant", "content": "写好了。"},
    {"role": "user", "content": "好的"},
    {"role": "assistant", "content": "还有别的吗？"},
    {"role": "user", "content": "没了"},
    {"role": "assistant", "content": "好。"},
    {"role": "user", "content": "嗯"},
]
ck(_dc_safe("再来一个", _FAR) is False, "S3 工具调用已衰出窗口 → 粘性自然消失",
   "→ %r" % _dc_safe("再来一个", _FAR))

# ---------------------------------------------------------------- S4 反向
print("\n[S4] 否定 / 疑问 / 讨论 / 主观评价语境不被续接接管")
for txt in NEVER:
    _it = clf(txt, HOT)
    ck(bool(_it) and _it.needs_action is False,
       "S4 不接管：%s" % txt,
       "→ needs_action=%r kind=%r" % (None if _it is None else _it.needs_action,
                                      None if _it is None else _it.kind))
ck(clf("别再搜了", HOT).kind == intent.KIND_NEGATED,
   "S4 「别再搜了」仍是 negated（否定优先于续接）",
   "→ %r" % clf("别再搜了", HOT).kind)

# ---------------------------------------------------------------- S5 事实口径
print("\n[S5] route_judge.has_recent_tool_any 认全部真实口径（单一真源）")
try:
    import route_judge
    _any = getattr(route_judge, "has_recent_tool_any", None)
    ck(_any is not None, "S5 route_judge.has_recent_tool_any 存在")
    if _any is not None:
        ck(_any([{"role": "tool", "content": "ok"}]) is True,
           "S5 认 agent 内存口径 role=tool")
        ck(_any([{"role": "tool_log", "content": "ok"}]) is True,
           "S5 认 UI 落盘口径 role=tool_log")
        ck(_any([{"role": "function", "content": "ok"}]) is True,
           "S5 认 function 口径")
        ck(_any([{"role": "assistant", "tool_calls": [{"name": "x"}]}]) is True,
           "S5 认 assistant 的 tool_calls（回执尚未回灌时）")
        ck(_any([{"role": "user", "content": "hi"}]) is False,
           "S5 无工具 → False（非空谓词）")
        ck(_any([{"role": "tool", "content": "ok"}], 0) is False,
           "S5 k=0 → False（闭掉粘性）")
        ck(_any(None) is False, "S5 messages=None → False（fail-open）")
    # 旧函数不得被改动语义（回放基准）
    ck(route_judge.has_recent_tool([{"role": "tool_log", "content": "x"}]) is False,
       "S5R 回放基准 has_recent_tool 语义不变（仍只认 tool）")
    ck(route_judge.has_recent_tool([{"role": "tool", "content": "x"}]) is True,
       "S5R 回放基准 has_recent_tool 仍认 tool")
except Exception as e:  # noqa: BLE001
    ck(False, "S5 route_judge 导入", repr(e))

# ---------------------------------------------------------------- S6 长度闸
print("\n[S6] 长文本（自动化任务说明书常写「失败再试」）不被当续接")
_long = ("每天 09:00 抓 GitHub 热榜，抓失败了就再试一次，重试三次还不行就记日志，"
         "然后重新生成一份简报存到桌面，注意别上浏览器")
ck(len(_long) > 40, "S6 语料确实超长（前置）", "len=%d" % len(_long))
ck(_dc_safe(_long, HOT) is False, "S6 长文本不接管", "→ %r" % _dc_safe(_long, HOT))
_short = "再来一个"
ck(len(_short) <= 40 and _dc_safe(_short, HOT) is True,
   "S6 对照：同义短句仍接管", "→ %r" % _dc_safe(_short, HOT))

# ---------------------------------------------------------------- S7 主观闸
print("\n[S7] 主观评价 / 假设语气不算续接")
for txt in ("我觉得不够详细", "感觉再详细点会更好", "如果再生成一个会不会更好",
            "为什么这么慢"):
    ck(_dc_safe(txt, HOT) is False, "S7 主观/疑问不接管：%s" % txt,
       "→ %r" % _dc_safe(txt, HOT))

# ---------------------------------------------------------------- S8 兼容
print("\n[S8] 旧两参调用（不传 messages）行为完全不变 —— 只加不减")
_old = intent.classify("再来一个", None)
ck(_old.needs_action is False, "S8 不传 messages → needs_action 保持 False",
   "→ %r" % _old.needs_action)
ck(getattr(_old, "continuation", False) is False,
   "S8 不传 messages → continuation=False（默认关闭）")
ck(intent.classify("帮我搜一下昆明天气", None).force_tool == "web_search",
   "S8R 既有强制路由不回归")
ck(agent_text._detect_action_intent(
    [{"role": "user", "content": "对自己进行一次自检，只看不改"}]) is True,
   "S8R v4.254 no_stall_186 回归项仍在")

# ---------------------------------------------------------------- S9 接线
print("\n[S9] agent.py 必须把会话喂给 classify（不接线＝判据白写）")
try:
    import inspect
    import agent as _agent_mod
    _src = inspect.getsource(_agent_mod)
    _calls = [ln.strip() for ln in _src.splitlines()
              if "intent.classify(" in ln and not ln.strip().startswith("#")]
    ck(len(_calls) >= 1, "S9 找得到 classify 调用点", "→ %r" % _calls)
    _with_msgs = [c for c in _calls if "messages" in c]
    # ⚠ 必须**每一处**都带会话，不能只要求"至少一处"：
    # 765 行（__init__，决定 self._needs_action → 首步强制 + nudge）与
    # 961 行（run 内，决定 step-1 的 _ft）是两个独立调用点，只接一个
    # 就仍有路径拿不到粘性。第一版写成 >=1，扰动 V7 打掉 765 行后
    # 961 行还在 → 判据照样绿，成了哑弹。
    # 若将来确有「不需要粘性」的调用点，请在此显式豁免并写明理由。
    ck(len(_calls) >= 1 and len(_with_msgs) == len(_calls),
       "S9 每一处 classify 调用都传入 messages（漏一处＝该路径无粘性）",
       "→ 缺 %r" % [c for c in _calls if "messages" not in c])
except Exception as e:  # noqa: BLE001
    ck(False, "S9 agent 模块导入/源码读取", repr(e))

print("\n" + "=" * 74)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
