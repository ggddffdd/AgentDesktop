# -*- coding: utf-8 -*-
"""v4.317.0 判据：自动化任务「没产出 + 卡死」双止损。

背景（2026-11-21 两次 AppHang 现场）：
  - verify 阶段连续 8 步「核验未过」，但 v4.298 早停要求「未满足项一字不变」，
    签名每步都在变 → 早停永不触发 → 任务空转到耗尽预算判失败（没产出）。
  - 自动化收尾 _finish_automation_run → _notify_task_done → QMediaPlayer.play()
    在主线程同步触发，WER 两次都载入音频/蓝牙栈 → AppHang 强线索。
  - auto 专属会话膨胀到 678/636 条，落盘/审计/渲染全量扫。

三处止损（本判据）：
  ① agent_text.verify_fail_streak —— 连续「核验未过」计数纯函数，不管签名变不变，
     只要连续 N 步 verified=False 且核验记录非空就累计；agent.py 用它做早停。
  ② automation.should_speak_on_finish —— 收尾播报守门：无头自动化场景不发语音
     （QMediaPlayer.play 主线程同步 = 卡死嫌疑），但普通对话朗读不受影响。
  ③ session.trim_auto_session —— auto 专属会话消息数上限（保留头部目标 + 尾部
     最近消息，砍中间），防 678 条无限膨胀；普通会话一条不删。

判据分两层：
  A 层 —— 纯函数行为（边界/幂等/只动 auto 不动普通）。
  B 层 —— 源码契约（agent.py / ui.py / session.py 接线，语义锚定防注释假绿）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print("  [PASS] %s" % name)
        _p += 1
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        _f += 1


def read_src(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


AGENT_SRC = read_src(os.path.join(ROOT, "agent.py"))
LOOP_MIXIN_SRC = read_src(os.path.join(ROOT, "agent_loop_mixin.py"))
UI_SRC = read_src(os.path.join(ROOT, "ui.py"))
SESSION_SRC = read_src(os.path.join(ROOT, "session.py"))
AGENT_TEXT_PATH = os.path.join(ROOT, "agent_text.py")
AUTOMATION_PATH = os.path.join(ROOT, "automation.py")
SESSION_PATH = os.path.join(ROOT, "session.py")

try:
    import agent_text as at
except Exception as e:
    at = None
    print("import agent_text ERR", e)
try:
    import automation as auto
except Exception as e:
    auto = None
    print("import automation ERR", e)
try:
    import session as sess
except Exception as e:
    sess = None
    print("import session ERR", e)


# ================================================================ A 层：行为
print("=== A 层：纯函数行为 ===")

# ---------- ① verify_fail_streak ----------
# 签名：verify_fail_streak(prev_streak, verified, has_report) -> int
# 语义：verified=True 或有新核验进展 → 归零；否则（核验未过且记录非空）累计。
#       关键：不看「未满足项内容是否变化」——签名每步变也要累计（v4.298 的坑）。
if at is not None and hasattr(at, "verify_fail_streak"):
    fn = at.verify_fail_streak
    check("A1  首步核验未过 → streak=1", fn(0, False, True) == 1)
    check("A2  连续未过 → streak 累加", fn(3, False, True) == 4)
    check("A3  verified=True → 归零", fn(5, True, True) == 0)
    check("A4  无核验记录 → 归零（没东西可核不算空转）", fn(5, False, False) == 0)
    check("A5  未过但中途达标再未过 → 重新计数",
          fn(fn(2, True, True), False, True) == 1)
    check("A6  MAX_VERIFY_FAIL_STREAK 存在且为合理正整数 4-12",
          isinstance(getattr(at, "MAX_VERIFY_FAIL_STREAK", None), int)
          and 4 <= at.MAX_VERIFY_FAIL_STREAK <= 12,
          "val=%r" % getattr(at, "MAX_VERIFY_FAIL_STREAK", None))
else:
    for n in ["A1  首步核验未过 → streak=1", "A2  连续未过 → streak 累加",
              "A3  verified=True → 归零", "A4  无核验记录 → 归零",
              "A5  达标后再未过 → 重新计数", "A6  MAX_VERIFY_FAIL_STREAK 合理"]:
        check(n, False, "agent_text.verify_fail_streak 不存在")

# ---------- ② should_speak_on_finish ----------
# 签名：should_speak_on_finish(speech_enabled, degraded=False, is_automation=False) -> bool
# 语义：自动化收尾播报一律 False（主线程 QMediaPlayer.play 卡死嫌疑）；
#       普通对话朗读按 speech_enabled 且未 degraded 决定。
if auto is not None and hasattr(auto, "should_speak_on_finish"):
    fn2 = auto.should_speak_on_finish
    check("A7  自动化收尾 → 不播报（即使语音开）",
          fn2(True, False, True) is False)
    check("A8  自动化收尾 + degraded → 仍不播报",
          fn2(True, True, True) is False)
    check("A9  普通对话语音开 → 朗读", fn2(True, False, False) is True)
    check("A10 普通对话语音关 → 不朗读", fn2(False, False, False) is False)
    check("A11 普通对话 degraded → 不朗读", fn2(True, True, False) is False)
else:
    for n in ["A7  自动化收尾不播报", "A8  自动化+degraded 不播报",
              "A9  普通对话开→朗读", "A10 普通对话关→不读", "A11 degraded→不读"]:
        check(n, False, "automation.should_speak_on_finish 不存在")

# ---------- ③ trim_auto_session ----------
# 签名：trim_auto_session(sid, messages, max_msgs=None, prefix="auto_") -> (list, int)
# 语义：只裁 auto_ 前缀会话；保留头部（首条目标）+ 尾部最近消息，砍中间；
#       返回 (裁后列表, 删除条数)。普通会话原样返回、删除 0。
if sess is not None and hasattr(sess, "trim_auto_session"):
    fn3 = sess.trim_auto_session
    cap = getattr(sess, "MAX_AUTO_SESSION_MSGS", None)
    check("A12 MAX_AUTO_SESSION_MSGS 存在且 50-1000",
          isinstance(cap, int) and 50 <= cap <= 1000, "val=%r" % cap)
    msgs = [{"role": "user", "content": "目标"}]
    msgs += [{"role": "assistant", "content": "m%d" % i} for i in range(20)]
    msgs += [{"role": "assistant", "content": "末尾"}]
    out, removed = fn3("auto_abc", msgs, max_msgs=5)
    check("A13 auto 会话超限被裁", len(out) == 5 and removed == 17,
          "out=%d removed=%d" % (len(out), removed))
    check("A14 保留头部目标", out and out[0].get("content") == "目标")
    check("A15 保留尾部最近消息", out and out[-1].get("content") == "末尾")
    out0, rm0 = fn3("auto_abc", msgs[:3], max_msgs=5)
    check("A16 未超限不裁（幂等）", len(out0) == 3 and rm0 == 0,
          "out=%d removed=%d" % (len(out0), rm0))
    outn, rmn = fn3("20261114_normal", msgs, max_msgs=3)
    check("A17 普通会话一条不删", len(outn) == len(msgs) and rmn == 0,
          "out=%d removed=%d" % (len(outn), rmn))
    out2, rm2 = fn3("auto_x", [], max_msgs=5)
    check("A18 空消息不崩", out2 == [] and rm2 == 0)
else:
    for n in ["A12 MAX_AUTO_SESSION_MSGS 合理", "A13 auto 会话超限被裁",
              "A14 保留头部目标", "A15 保留尾部最近",
              "A16 未超限幂等", "A17 普通会话不删", "A18 空消息不崩"]:
        check(n, False, "session.trim_auto_session 不存在")


# ================================================================ B 层：源码契约
print("=== B 层：源码契约（语义锚定）===")

# ① agent.py 必须用新计数器做早停（而非只靠一字不变签名）
# 锚定真实调用：verify_fail_streak( 出现在 _stall_limit 判定之前同一函数体内
# 语义锚定：count=1 的字面量会被「别处的定义/注释」满足（v4.316 假绿坑），
# 必须锚到实际调用行。
check("B1  agent_loop_mixin 在计数赋值行调用 verify_fail_streak",
      re.search(r"_verify_fail_streak\s*=\s*(?:agent_text|_at)\.verify_fail_streak\s*\(",
                LOOP_MIXIN_SRC) is not None)
check("B2  agent_loop_mixin 早停条件含新计数器（改坏必红）",
      re.search(r"if[^\n]*(_fail_exceeded|\bnot[^\n]*_fail_exceeded)", LOOP_MIXIN_SRC) is not None)
check("B2b agent.py 只留一行调用（拆分红线 <2400）",
      re.search(r"if\s+self\._verify_stall_check\s*\(", AGENT_SRC) is not None
      and AGENT_SRC.count("\n") < 2400,
      "lines=%d" % (AGENT_SRC.count("\n") + 1))
# 原 v4.298 一字不变判据必须保留（改判据不删判据）
check("B3  v4.298 原一字不变早停判据仍在",
      "MAX_VERIFY_STALL_STEPS" in AGENT_SRC)

# ② ui.py：_notify_task_done 必须用 should_speak_on_finish 守门
check("B4  ui.py 收尾播报守门是真实 if 条件（改坏必红）",
      re.search(r"if\s+not\s+automation\.should_speak_on_finish\s*\(", UI_SRC) is not None)
# 语义锚定：守门调用必须在 _notify_task_done 函数体内（不能只在别处）
m = re.search(r"def _notify_task_done\b(?:(?!\n    def ).)*", UI_SRC, re.S)
check("B5  守门在 _notify_task_done 函数体内",
      m is not None and "should_speak_on_finish" in (m.group(0) if m else ""))

# ③ session.py：save() 前必须裁 auto 会话
check("B6  session.py 在 save 内调用 trim_auto_session 赋值行",
      re.search(r"=\s*trim_auto_session\(_sid", SESSION_SRC) is not None)
# 语义锚定：调用在 save 方法体内
m2 = re.search(r"    def save\(self\):(?:(?!\n    def ).)*", SESSION_SRC, re.S)
check("B7  裁剪在 save() 方法体内",
      m2 is not None and "trim_auto_session" in (m2.group(0) if m2 else ""))

print("汇总：PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
