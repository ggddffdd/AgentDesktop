# -*- coding: utf-8 -*-
"""v4.297.0：治「做任务时每跳一个过程气泡就把页面拽回对话页」+「自动化任务中途停」。

背景（2026-11-12 大哥实机，两件事同源：后台自动化任务在跑）：
① 点在任何模板/工作流/设置页，Agent 每产生一条过程气泡就被拽回对话页；
② 「每日 AI 新闻抓取」12:57:49 被记成「中途停了，这次不算完成」。

查实的根因（不是猜）：
① `ui.py:_render_messages()` 开头把「渲染消息」和「切主栈页面」绑死：
   只要 session.messages 非空就 setCurrentIndex(1)。worker 的 render 信号
   → _render_throttled → _render_messages，每次气泡/工具卡/流式 delta 都走一遍。
   另有 `_fire_automation_run` 启动时的 `_switch_nav(0)` 再抢一次。
② 不是超时，是 **token 预算熔断**：sessions.json 的 auto_75d5b6a48a85 末条
   assistant 写着「🛑 已触达 token 预算上限（804168/800000）」。该专属会话
   已堆 382 条消息，每轮把整份历史回灌、每步再重发一遍 → 单任务累计撞线。
   `_resumable_reason` 里明明记着 token_budget，UI 却把一切可续停止笼统说成
   「中途停了」，真因被盖住。

按大哥选的 A 档，三处都做减法/修假信息，不加模块、不动信号、不改落盘：
① 切页决策抽成 `ui_focus.page_after_render` 纯函数，后台渲染一律不切页；
   只在用户主动动作（首屏/发送/新建会话…）显式 follow_nav=True 才切。
② `automation.failure_reason` 接 stop_reason，把真因说人话。
③ `automation.history_for_agent` 给自动化专属会话一个**薄的投喂视图**：
   本轮指令 + 上一轮最终答复，几百条工具记录不进模型上下文；落盘原样保留。

判据五组：A 切页纯函数 / B 失败真因 / C 回灌裁剪 / D 源码接线 / E 不变量。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_sbx_focus297_")
os.environ.setdefault("XC_USER_DATA_DIR", os.path.join(_SBX, "userdata"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s %s" % (name, extra))


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def fn_seg(src, name):
    """取 `    def name(` 到下一个同级 def 之间的源码（不 import Qt 也能查接线）。"""
    key = "    def %s(" % name
    i = src.find(key)
    if i < 0:
        return ""
    j = src.find("\n    def ", i + len(key))
    return src[i:j if j >= 0 else len(src)]


import automation  # noqa: E402
import ui_focus  # noqa: E402

UI = read("ui.py")
MIX = read("ui_session_mixin.py")
AUTO = read("automation.py")
FOCUS = read("ui_focus.py")

PREFIX = "【自动化任务】"
GEN = "✅ 上次抓到 8 条，已写入 ai_news_latest.md"
SUMMARY = "\n\n── 本轮执行小结 ──\n做了什么：真实调用了 web_fetch"


def _hist(*items):
    return [dict(x) for x in items]


# ============================================================
# A 组：切页决策纯函数（旧源码没有 → 逐条登记红，不短路）
# ============================================================
_pg = getattr(ui_focus, "page_after_render", None)
check("A1 ui_focus 暴露 page_after_render 纯函数", callable(_pg))
_A_REST = (
    "A2 后台渲染默认不切页：有内容 + 停在模板页",
    "A3 后台渲染默认不切页：有内容 + 停在首页",
    "A4 后台渲染默认不切页：空会话 + 停在对话页",
    "A5 后台渲染默认不切页：流式输出中也不切",
    "A6 显式 follow_nav=True 才按老规矩回对话页",
    "A7 显式 follow_nav=True 空会话才回首页",
    "A8 已在对话页 / 已在首页时不重复切",
    "A9 纯函数不读盘不写盘不发信号",
)
if callable(_pg):
    check(_A_REST[0], _pg(True, 5) is None, repr(_pg(True, 5)))
    check(_A_REST[1], _pg(True, 0) is None, repr(_pg(True, 0)))
    check(_A_REST[2], _pg(False, 1) is None, repr(_pg(False, 1)))
    check(_A_REST[3], _pg(True, 9, False) is None, repr(_pg(True, 9, False)))
    check(_A_REST[4], _pg(True, 5, True) == 1, repr(_pg(True, 5, True)))
    check(_A_REST[5], _pg(False, 1, True) == 0, repr(_pg(False, 1, True)))
    check(_A_REST[6], _pg(True, 1, True) is None and _pg(False, 0, True) is None)
    _names = set(_pg.__code__.co_names or ())
    check(_A_REST[7], not ({"open", "setCurrentIndex", "emit", "save"} & _names),
          sorted(_names))
else:
    for _n in _A_REST:
        check(_n, False, "page_after_render 不存在")

# 非空谓词：把默认改成 True（回退旧行为）A2~A5 必红
check("A10 默认参数确实为 False（改回 True 时 A 组会红）",
      callable(_pg) and "follow_nav=False" in FOCUS,
      "ui_focus.py 里没有 follow_nav=False 的默认声明")


# ============================================================
# B 组：自动化失败原因说真话（旧签名只有 2 参 → 红）
# ============================================================
def _reason(reply, stopped, reason=""):
    try:
        return automation.failure_reason(reply, stopped, reason)
    except TypeError:
        return None


check("B1 failure_reason 支持 stop_reason 第三参",
      "stop_reason" in AUTO, "automation.py 没接原因参数")
check("B2 token_budget 不再说成『中途停了』",
      _reason("", True, "token_budget") == "token 预算用尽了，这次不算完成",
      repr(_reason("", True, "token_budget")))
check("B3 timeout 说超时", _reason("", True, "timeout") == "跑超时了，这次不算完成",
      repr(_reason("", True, "timeout")))
check("B4 total_budget 说总时长", _reason("", True, "total_budget") ==
      "总时长到上限了，这次不算完成", repr(_reason("", True, "total_budget")))
check("B5 model_error 说模型出错", _reason("", True, "model_error") ==
      "模型调用出错了，这次不算完成", repr(_reason("", True, "model_error")))
check("B6 max_steps 说步数用完", _reason("", True, "max_steps") ==
      "步数用完了，这次不算完成", repr(_reason("", True, "max_steps")))
check("B7 未知原因回退旧文案（fail-closed，不编新词）",
      _reason("", True, "weird_new_reason") == "中途停了，这次不算完成",
      repr(_reason("", True, "weird_new_reason")))
check("B8 没带原因（用户手动停）仍是旧文案",
      _reason("", True) == "中途停了，这次不算完成", repr(_reason("", True)))
check("B9 未停止的三种旧判定一字不改",
      automation.failure_reason("") == "没有给出结果"
      and automation.failure_reason("请开启 Agent 模式后才能干活") ==
      "没有真正做完，还在等你切换干活方式"
      and automation.failure_reason("随便写了点东西") == "没有产出可用结果")
check("B10 文案映射是常量表（不靠 if 堆字面量难维护）",
      "STOP_REASON_TEXT" in AUTO or "FAIL_REASON_TEXT" in AUTO,
      "没有集中映射表")


# ============================================================
# C 组：自动化专属会话的薄投喂视图（旧源码没有 → 逐条登记红）
# ============================================================
_h = getattr(automation, "history_for_agent", None)
check("C1 automation 暴露 history_for_agent 纯函数", callable(_h))
_C_REST = (
    "C2 只保留本轮指令（没有历史时不塞空壳）",
    "C3 带上上一轮最终答复，Agent 仍看得见上次干了什么",
    "C4 几百条 tool / tool_log 一条都不进模型上下文",
    "C5 空 assistant / 系统执行小结不当成上一轮结论",
    "C6 多轮任务只带最后一轮指令，不跨轮堆历史",
    "C7 入参不被修改（落盘数据零删除）",
    "C8 坏档 None / 空 prefix / 无本轮指令都安全返回空",
    "C9 纯函数不读盘、不写盘、不改 session",
    "C10 上一轮结论挑真正的答复（跳过空气泡与执行小结）",
)
if callable(_h):
    _one = _hist({"role": "user", "content": PREFIX + "抓今天的 AI 新闻"})
    _r = _h(_one, PREFIX)
    check(_C_REST[0], len(_r) == 1 and _r[0]["content"].startswith(PREFIX), repr(_r))

    _many = _hist(
        {"role": "user", "content": PREFIX + "上一轮任务"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "工具结果一大坨" * 20},
        {"role": "tool_log", "content": "None"},
        {"role": "assistant", "content": GEN},
        {"role": "user", "content": PREFIX + "本轮任务"},
    )
    _r = _h(_many, PREFIX)
    check(_C_REST[1], any(m.get("content") == GEN for m in _r), repr(_r))
    check(_C_REST[2], all(m.get("role") not in ("tool", "tool_log") for m in _r),
          repr([m.get("role") for m in _r]))
    check(_C_REST[4], sum(1 for m in _r
                          if str(m.get("content") or "").startswith(PREFIX)) == 1,
          repr([str(m.get("content"))[:24] for m in _r]))

    _junk = _hist(
        {"role": "user", "content": PREFIX + "上一轮"},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": SUMMARY},
        {"role": "user", "content": PREFIX + "本轮"},
    )
    _rj = _h(_junk, PREFIX)
    check(_C_REST[3], len(_rj) == 1 and str(_rj[0].get("content")) == PREFIX + "本轮",
          repr([str(m.get("content"))[:30] for m in _rj]))

    _before = repr(_many)
    _h(_many, PREFIX)
    check(_C_REST[5], repr(_many) == _before, "入参被就地改过")
    check(_C_REST[6], _h(None, PREFIX) == [] and _h(_many, "") == []
          and _h(_hist({"role": "user", "content": "普通聊天"}), PREFIX) == [])
    _names = set(_h.__code__.co_names or ())
    check(_C_REST[7], not ({"open", "save", "remove", "clear"} & _names),
          sorted(_names))

    _mixed = _hist(
        {"role": "user", "content": PREFIX + "第一轮"},
        {"role": "assistant", "content": "第一轮结论"},
        {"role": "user", "content": PREFIX + "第二轮"},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": SUMMARY},
        {"role": "user", "content": PREFIX + "第三轮"},
    )
    _r = _h(_mixed, PREFIX)
    check(_C_REST[8],
          len(_r) == 2 and str(_r[0].get("content")) == "第一轮结论"
          and str(_r[1].get("content")) == PREFIX + "第三轮",
          repr([str(m.get("content"))[:30] for m in _r]))
else:
    for _n in _C_REST:
        check(_n, False, "history_for_agent 不存在")


# ============================================================
# D 组：源码接线（三处都得真的接上，不许只写个没人调的函数）
# ============================================================
_render = fn_seg(UI, "_render_messages")
check("D1 _render_messages 默认 follow_nav=False",
      "def _render_messages(self, force_bottom=False, follow_nav=False)" in _render,
      _render[:160].replace("\n", " | "))
check("D2 切页改走纯函数，函数体不再自己判 index",
      "ui_focus.page_after_render(" in _render
      and "if has_content and self.main_stack.currentIndex()" not in _render)
check("D3 只有算出目标页才 setCurrentIndex",
      "_target is not None" in _render and _render.count("setCurrentIndex(") == 1,
      "count=%d" % _render.count("setCurrentIndex("))
_tick = fn_seg(UI, "_on_render_tick")
check("D4 后台节流渲染不传 follow_nav（每气泡不抢页的关键）",
      "self._render_messages(self._pending_render_force)" in _tick
      and "follow_nav" not in _tick, _tick[:200].replace("\n", " | "))
_throttle = fn_seg(UI, "_render_throttled")
check("D5 _render_throttled 不偷偷打开 follow_nav", "follow_nav=True" not in _throttle)
_flush = fn_seg(UI, "_flush_render")
check("D6 _flush_render 不偷偷打开 follow_nav", "follow_nav=True" not in _flush)

_fire = fn_seg(UI, "_fire_automation_run")
check("D7 自动化任务启动不再主动 _switch_nav(0) 抢页",
      "self._switch_nav(0)" not in _fire, "fire 里还留着 _switch_nav(0)")
check("D8 自动化启动的渲染是后台渲染",
      "self._render_messages(force_bottom=True)" in _fire
      and "follow_nav=True" not in _fire)
_restore = fn_seg(UI, "_restore_session_after_automation")
check("D9 自动化收尾切回会话也不抢页",
      "follow_nav=True" not in _restore
      and "self._render_messages(force_bottom=True)" in _restore)
check("D10 首屏与用户主动发送仍显式切页（旧体验不丢）",
      "self._render_messages(force_bottom=True, follow_nav=True)" in UI
      and "self._render_messages(follow_nav=True)" in UI)
check("D11 新建/关闭会话仍按老规矩切页（零视觉变化）",
      MIX.count("_render_messages(force_bottom=True, follow_nav=True)") >= 3,
      "count=%d" % MIX.count("_render_messages(force_bottom=True, follow_nav=True)"))

_done = fn_seg(UI, "_on_agent_done")
_finish = fn_seg(UI, "_finish_automation_run")
check("D12 _on_agent_done 把 worker 的真因往下传",
      "_resumable_reason" in _done and "stop_reason" in _done,
      _done[-260:].replace("\n", " | "))
check("D13 _finish_automation_run 接 stop_reason 并传给 failure_reason",
      "stop_reason" in _finish and "failure_reason(reply, stopped=stopped, stop_reason=stop_reason)"
      in _finish.replace("\n", ""), _finish[-400:].replace("\n", " | "))
check("D14 接手不算完成的判定没被顺手放宽",
      "reply_is_real_success(reply, stopped=stopped, tools_ran=tools_ran)" in _finish)

_run = fn_seg(UI, "_agent_run")
check("D15 _agent_run 对自动化会话走薄投喂视图",
      "automation.history_for_agent(" in _run
      and _run.find("automation.history_for_agent(") < _run.find("_build_api_history("),
      "没接上或顺序反了")
check("D16 普通对话会话不走裁剪（别把用户聊天历史砍了）",
      "SESSION_SID_PREFIX" in _run and "_auto_task_active" in _run)
check("D17 受限工具分支与 full_tools 逃生口没被碰坏",
      "automation.filter_tools_safe(" in _run and 'not _auto_t.get("full_tools")' in _run)


# ============================================================
# E 组：不变量 / 回归保护
# ============================================================
check("E1 切会话/点技能/导演引导这些主动切页点仍在",
      "self.main_stack.setCurrentIndex(1)" in fn_seg(UI, "_switch_session")
      and "self.main_stack.setCurrentIndex(1)" in fn_seg(UI, "_on_tool_pick")
      and "self.main_stack.setCurrentIndex(1)" in fn_seg(UI, "_render_director_redirect"))
check("E2 worker render 信号仍接渲染（没把功能一起删了）", "w.render.connect(" in UI)
check("E3 停了就是没做完（reply_is_real_success 不放宽）",
      automation.reply_is_real_success("我做完了", stopped=True, tools_ran=True) is False
      and automation.reply_is_real_success("", stopped=False, tools_ran=False) is False)
check("E4 会话落盘格式一个字段没动（不删用户数据）",
      "def history_for_agent(" in AUTO and ".clear()" not in AUTO
      and "messages.pop" not in AUTO and "sessions.pop" not in AUTO)
check("E5 三个新能力都在纯逻辑模块里（不新建 UI 模块、不动信号签名）",
      "def page_after_render(" in FOCUS
      and "def history_for_agent(" in AUTO
      and "def failure_reason(" in AUTO
      and "board_update" not in FOCUS)


if __name__ == "__main__":
    print("")
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
