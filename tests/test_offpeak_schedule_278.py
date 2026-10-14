# -*- coding: utf-8 -*-
"""v4.278.0 可推迟任务等到 DeepSeek 空闲时段。

官方价目（2026-10-14 核对）：
  https://api-docs.deepseek.com/zh-cn/quick_start/pricing
  空闲价是高峰的一半。北京时间周一至周五（不含法定节假日）
  09:00–12:00、14:00–18:00 为高峰；周末和法定节假日全天都是空闲。

行为：
  · prefer_offpeak 默认关。聊天发送不看这个开关。
  · 只有「执行任务」会等。提醒到点就弹。
  · 高峰里等到下一段空闲；等满配置的上限，或下一段空闲超出上限，就直接跑。
"""
import ast
import os
import shutil
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="offpeak278_")
os.environ["XC_USER_DATA_DIR"] = _TMP
os.environ["XC_WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["XC_LOG_DIR"] = os.path.join(_TMP, "logs")
os.makedirs(os.environ["XC_WORKSPACE_DIR"], exist_ok=True)
os.makedirs(os.environ["XC_LOG_DIR"], exist_ok=True)

import automation  # noqa: E402
import config  # noqa: E402
import tools  # noqa: E402

BJ = ZoneInfo("Asia/Shanghai")
PASS = FAIL = 0
UI_PATH = os.path.join(ROOT, "ui.py")
PANEL_PATH = os.path.join(ROOT, "automation_panel.py")


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, extra))


def bj(y, mo, d, hh, mm, ss=0):
    return datetime(y, mo, d, hh, mm, ss, tzinfo=BJ)


def run_task(at_time="09:00", **kw):
    t = automation.new_task(
        "简报", automation.ACT_RUN, "总结今天", automation.SCHED_DAILY,
        at_time=at_time)
    t["prefer_offpeak"] = True
    t.update(kw)
    return t


def _is_false(node):
    return isinstance(node, ast.Constant) and node.value is False


def _reachable_attr_calls(fn, attr):
    found = []

    class V(ast.NodeVisitor):
        def visit_If(self, node):
            if _is_false(node.test):
                return
            self.generic_visit(node)

        def visit_Call(self, node):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == attr:
                found.append(node)
            self.generic_visit(node)

    V().visit(fn)
    return found


def _defer_returns_false(fn):
    if fn is None:
        return False
    for node in ast.walk(fn):
        if not isinstance(node, ast.If) or _is_false(node.test):
            continue
        test = node.test
        ok_call = (
            isinstance(test, ast.Call)
            and (
                (isinstance(test.func, ast.Name) and test.func.id == "should_defer_offpeak")
                or (isinstance(test.func, ast.Attribute)
                    and test.func.attr == "should_defer_offpeak")))
        if not ok_call:
            continue
        for stmt in node.body:
            if (isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value is False):
                return True
    return False


def test_official_windows():
    print("=== 官方窗口 ===")
    check("空闲价是高峰的一半", config.DEEPSEEK_OFFPEAK_PRICE_RATIO == 0.5,
          config.DEEPSEEK_OFFPEAK_PRICE_RATIO)
    check("高峰写在唯一配置里",
          config.DEEPSEEK_PEAK_WINDOWS_BJ == (("09:00", "12:00"), ("14:00", "18:00")),
          str(config.DEEPSEEK_PEAK_WINDOWS_BJ))
    check("最长等待是 6 小时",
          config.DEEPSEEK_OFFPEAK_MAX_DELAY_SEC == 6 * 60 * 60,
          config.DEEPSEEK_OFFPEAK_MAX_DELAY_SEC)
    wed = dict(y=2026, mo=10, d=14)  # 周三，不是节假日
    check("周三 08:59 北京时间是空闲", not automation.is_deepseek_peak(bj(2026, 10, 14, 8, 59)))
    check("周三 09:00 北京时间是高峰", automation.is_deepseek_peak(bj(2026, 10, 14, 9, 0)))
    check("周三 11:59 北京时间是高峰", automation.is_deepseek_peak(bj(2026, 10, 14, 11, 59)))
    check("周三 12:00 北京时间是空闲", not automation.is_deepseek_peak(bj(2026, 10, 14, 12, 0)))
    check("周三 12:30 北京时间是空闲", not automation.is_deepseek_peak(bj(2026, 10, 14, 12, 30)))
    check("周三 13:59 北京时间是空闲", not automation.is_deepseek_peak(bj(2026, 10, 14, 13, 59)))
    check("周三 14:00 北京时间是高峰", automation.is_deepseek_peak(bj(2026, 10, 14, 14, 0)))
    check("周三 17:59 北京时间是高峰", automation.is_deepseek_peak(bj(2026, 10, 14, 17, 59)))
    check("周三 18:00 北京时间是空闲", not automation.is_deepseek_peak(bj(2026, 10, 14, 18, 0)))
    check("周六上午不在高峰", not automation.is_deepseek_peak(bj(2026, 10, 17, 10, 0)))
    check("周日补班的上午仍是空闲", not automation.is_deepseek_peak(bj(2026, 1, 4, 10, 0)))
    check("国庆当天上午不在高峰", not automation.is_deepseek_peak(bj(2026, 10, 1, 10, 0)))
    check("端午当天上午不在高峰", not automation.is_deepseek_peak(bj(2026, 6, 19, 10, 0)))
    check("除夕上午不在高峰", not automation.is_deepseek_peak(bj(2026, 2, 16, 10, 0)))
    check("国庆调休的周一上午仍是高峰", automation.is_deepseek_peak(bj(2026, 10, 5, 10, 0)))
    check("清明调休的周一上午仍是高峰", automation.is_deepseek_peak(bj(2026, 4, 6, 10, 0)))
    check("元旦调休的周五上午仍是高峰", automation.is_deepseek_peak(bj(2026, 1, 2, 10, 0)))
    # 样本日期的星期锁死，避免把调休日写成节日当天。
    check("样本日期的星期没标错",
          bj(2026, 10, 14, 10, 0).weekday() == 2
          and bj(2026, 10, 1, 10, 0).weekday() == 3
          and bj(2026, 10, 5, 10, 0).weekday() == 0
          and bj(2026, 1, 4, 10, 0).weekday() == 6
          and bj(**wed, hh=10, mm=0).weekday() == 2)


def test_defer_behavior():
    print("=== 等到空闲 / 最长等待 ===")
    now = bj(2026, 10, 14, 10, 0)
    task = run_task()
    check("周三 10:00 勾了会推迟", automation.should_defer_offpeak(task, now))
    check("推迟期间任务仍然算到期", automation.is_due(task, now))
    check("周三 12:30 勾了也不推迟",
          not automation.should_defer_offpeak(task, bj(2026, 10, 14, 12, 30)))
    check("周六上午不推迟",
          not automation.should_defer_offpeak(task, bj(2026, 10, 17, 10, 0)))
    holiday = run_task()
    check("国庆当天上午不推迟",
          not automation.should_defer_offpeak(holiday, bj(2026, 10, 1, 10, 0)))
    plain = run_task()
    plain["prefer_offpeak"] = False
    check("没勾选时高峰也不推迟",
          not automation.should_defer_offpeak(plain, now))
    missing = run_task()
    missing.pop("prefer_offpeak", None)
    check("旧任务没有这个字段也不推迟",
          not automation.should_defer_offpeak(missing, now))
    remind = automation.new_task(
        "喝水", automation.ACT_REMIND, "喝水", automation.SCHED_DAILY, at_time="09:00")
    remind["prefer_offpeak"] = True
    check("提醒即使勾了也不推迟", not automation.should_defer_offpeak(remind, now))
    afternoon = run_task(at_time="14:00")
    check("下午刚到点会等到 18 点",
          automation.should_defer_offpeak(afternoon, bj(2026, 10, 14, 14, 0)))
    late = run_task(at_time="09:00")
    check("等到第 7 小时就不再推迟",
          not automation.should_defer_offpeak(late, bj(2026, 10, 14, 16, 0)))
    tight = run_task(at_time="14:00")
    check("下一段空闲超出最长等待就直接跑",
          not automation.should_defer_offpeak(
              tight, bj(2026, 10, 14, 14, 0), max_delay_sec=2 * 60 * 60))
    fresh = automation.new_task(
        "n", automation.ACT_RUN, "m", automation.SCHED_DAILY)
    check("新任务默认不等空闲", fresh.get("prefer_offpeak") is False)
    check("只有执行任务能打开错峰",
          automation.prefer_offpeak_for(automation.ACT_RUN, True) is True
          and automation.prefer_offpeak_for(automation.ACT_REMIND, True) is False
          and automation.prefer_offpeak_for(automation.ACT_RUN, False) is False)
    once = automation.new_task(
        "一次", automation.ACT_RUN, "跑", automation.SCHED_ONCE,
        at_time="10:00", at_date="2026-10-14", prefer_offpeak=True)
    check("一次性任务高峰会推迟",
          automation.should_defer_offpeak(once, bj(2026, 10, 14, 10, 30)))
    check("一次性任务到了空闲就不再推迟",
          not automation.should_defer_offpeak(once, bj(2026, 10, 14, 12, 0)))
    gap = run_task(at_time="11:00")
    check("快到中午空闲时仍推迟",
          automation.should_defer_offpeak(gap, bj(2026, 10, 14, 11, 50)))
    summary = automation.schedule_summary(task)
    check("调度摘要能看出勾了错峰", "错峰" in summary, summary)
    check("没勾选的摘要不写错峰",
          "错峰" not in automation.schedule_summary(plain))


def test_tool_and_ui():
    print("=== 工具与聊天 ===")
    r = tools._h_create_automation(None, ROOT, {
        "name": "错峰简报",
        "message": "总结今天的新闻",
        "schedule_type": "daily",
        "at_time": "10:00",
        "prefer_offpeak": True,
    })
    store = automation.AutomationStore()
    saved = [t for t in store.list_all() if t.get("name") == "错峰简报"]
    check("工具能建一个等到空闲的执行任务",
          len(saved) == 1 and saved[0].get("prefer_offpeak") is True
          and saved[0].get("action") == automation.ACT_RUN,
          str(r[0]) if r else "")
    tools._h_create_automation(None, ROOT, {
        "name": "普通简报",
        "message": "总结今天的新闻",
        "schedule_type": "daily",
        "at_time": "10:00",
    })
    store._load()
    plain = [t for t in store.list_all() if t.get("name") == "普通简报"]
    check("工具不传这个选项时默认关闭",
          len(plain) == 1 and plain[0].get("prefer_offpeak") is False)
    tools._h_create_automation(None, ROOT, {
        "name": "喝水提醒",
        "message": "喝水",
        "action": "remind",
        "schedule_type": "daily",
        "at_time": "10:00",
        "prefer_offpeak": True,
    })
    store._load()
    remind = [t for t in store.list_all() if t.get("name") == "喝水提醒"]
    check("提醒任务不会被工具改成错峰",
          len(remind) == 1 and remind[0].get("prefer_offpeak") is False)
    listed = tools._h_list_automation(None, ROOT, {})
    check("任务列表能看出谁在等空闲", "错峰" in (listed[0] or ""))

    ui_src = open(UI_PATH, encoding="utf-8").read()
    tree = ast.parse(ui_src)
    bad = []
    tick_src = ""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name == "_on_automation_tick":
            tick_src = ast.get_source_segment(ui_src, node) or ""
        if _reachable_attr_calls(node, "should_defer_offpeak"):
            bad.append(node.name)
        if node.name != "_on_automation_tick" and _reachable_attr_calls(node, "ready_to_fire"):
            bad.append(node.name)
    check("聊天发送不会等空闲时段", not bad, ",".join(bad))
    check("到点调度走错峰闸门", "automation.ready_to_fire(" in tick_src)

    auto_src = open(os.path.join(ROOT, "automation.py"), encoding="utf-8").read()
    auto_tree = ast.parse(auto_src)
    gate = None
    for node in ast.walk(auto_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "ready_to_fire":
            gate = node
            break
    gate_src = ast.get_source_segment(auto_src, gate) if gate is not None else ""
    check("调度器在到期后才看要不要等",
          bool(gate_src)
          and gate_src.find("is_due") < gate_src.find("should_defer_offpeak")
          and _defer_returns_false(gate))

    panel = open(PANEL_PATH, encoding="utf-8").read()
    check("面板勾选默认关闭", "self.offpeak_chk.setChecked(False)" in panel)
    check("面板把选项交给新建任务", "prefer_offpeak=" in panel)


def main():
    try:
        test_official_windows()
        test_defer_behavior()
        test_tool_and_ui()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
