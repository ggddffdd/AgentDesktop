# -*- coding: utf-8 -*-
"""v4.278.0 错峰调度扰动。

改坏窗口、折扣、最长等待和接线，跑 tests/test_offpeak_schedule_278.py。
SKIP / MISS 都算哑弹。原串从真实文件取，且必须唯一。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_offpeak_schedule_278.py")
F_CFG = os.path.join(ROOT, "config.py")
F_AUTO = os.path.join(ROOT, "automation.py")
F_UI = os.path.join(ROOT, "ui.py")
F_PANEL = os.path.join(ROOT, "automation_panel.py")

OLD_WINDOW = 'DEEPSEEK_PEAK_WINDOWS_BJ = (("09:00", "12:00"), ("14:00", "18:00"))'
NEW_WINDOW = 'DEEPSEEK_PEAK_WINDOWS_BJ = (("09:00", "13:00"), ("14:00", "18:00"))  # 扰动：上午高峰拉长'

OLD_RATIO = "DEEPSEEK_OFFPEAK_PRICE_RATIO = 0.5"
NEW_RATIO = "DEEPSEEK_OFFPEAK_PRICE_RATIO = 1.0  # 扰动：没有半价就不要等"

OLD_DELAY = "DEEPSEEK_OFFPEAK_MAX_DELAY_SEC = 6 * 60 * 60"
NEW_DELAY = "DEEPSEEK_OFFPEAK_MAX_DELAY_SEC = 1 * 60 * 60  # 扰动：最多只等 1 小时"

OLD_WEEKEND = "    if bj.weekday() >= 5:\n"
NEW_WEEKEND = "    if bj.weekday() >= 8:  # 扰动：周末不再算空闲\n"

OLD_HOLIDAY = "    if bj.date().isoformat() in _statutory_holidays():\n"
NEW_HOLIDAY = "    if False and bj.date().isoformat() in _statutory_holidays():  # 扰动：法定节假日仍按工作日\n"

OLD_WAIT = (
    "def _offpeak_wait_exceeded(waited, remain, max_delay_sec):\n"
    "    \"\"\"已经等够久，或者下一段空闲远到会超过上限。\"\"\"\n"
    "    if waited >= max_delay_sec:\n"
    "        return True\n"
    "    return remain > (max_delay_sec - waited)\n"
)
NEW_WAIT = (
    "def _offpeak_wait_exceeded(waited, remain, max_delay_sec):\n"
    "    \"\"\"已经等够久，或者下一段空闲远到会超过上限。\"\"\"\n"
    "    return False  # 扰动：等再久也不放行\n"
)

OLD_DEFAULT = "             full_tools=False, prefer_offpeak=False):"
NEW_DEFAULT = "             full_tools=False, prefer_offpeak=True):  # 扰动：默认就等空闲"

OLD_REMIND_FLAG = "    return action == ACT_RUN and bool(checked)\n"
NEW_REMIND_FLAG = "    return bool(checked)  # 扰动：提醒也能等\n"

OLD_REMIND_DEFER = '    if task.get("action") != ACT_RUN:\n'
NEW_REMIND_DEFER = '    if task.get("action") == "":  # 扰动：提醒也会推迟\n'

OLD_GATE = (
    "    if should_defer_offpeak(task, now):\n"
    "        return False\n"
)
NEW_GATE = (
    "    if False:  # 扰动：高峰也直接跑\n"
    "        return False\n"
)

OLD_TICK = "                if not automation.ready_to_fire(t, now):\n"
NEW_TICK = "                if not automation.is_due(t, now):  # 扰动：高峰也直接跑\n"

OLD_CHAT = (
    "    def send(self):\n"
    "        # v4.102 fix7：_busy 检查提到最前——上一条任务未结束时，直接提示并保留输入，\n"
)
NEW_CHAT = (
    "    def send(self):\n"
    "        automation.should_defer_offpeak({})  # 扰动：聊天也等空闲\n"
    "        # v4.102 fix7：_busy 检查提到最前——上一条任务未结束时，直接提示并保留输入，\n"
)

OLD_PANEL = "        self.offpeak_chk.setChecked(False)\n"
NEW_PANEL = "        self.offpeak_chk.setChecked(True)  # 扰动：面板默认就等\n"

CASES = [
    ("P1 上午高峰拉到 13 点", F_CFG, OLD_WINDOW, NEW_WINDOW,
     ["[FAIL] 周三 12:30 北京时间是空闲"]),
    ("P2 没有半价就不要等", F_CFG, OLD_RATIO, NEW_RATIO,
     ["[FAIL] 周三 10:00 勾了会推迟"]),
    ("P3 最长等待缩成 1 小时", F_CFG, OLD_DELAY, NEW_DELAY,
     ["[FAIL] 下午刚到点会等到 18 点"]),
    ("P4 周末也当高峰", F_AUTO, OLD_WEEKEND, NEW_WEEKEND,
     ["[FAIL] 周六上午不推迟"]),
    ("P5 法定节假日仍按工作日", F_AUTO, OLD_HOLIDAY, NEW_HOLIDAY,
     ["[FAIL] 国庆当天上午不推迟"]),
    ("P6 等再久也不放行", F_AUTO, OLD_WAIT, NEW_WAIT,
     ["[FAIL] 等到第 7 小时就不再推迟", "[FAIL] 下一段空闲超出最长等待就直接跑"]),
    ("P7 新任务默认就等", F_AUTO, OLD_DEFAULT, NEW_DEFAULT,
     ["[FAIL] 新任务默认不等空闲"]),
    ("P8 提醒也能打开选项", F_AUTO, OLD_REMIND_FLAG, NEW_REMIND_FLAG,
     ["[FAIL] 只有执行任务能打开错峰"]),
    ("P9 提醒也会推迟", F_AUTO, OLD_REMIND_DEFER, NEW_REMIND_DEFER,
     ["[FAIL] 提醒即使勾了也不推迟"]),
    ("P10 到期了就不再看空闲", F_AUTO, OLD_GATE, NEW_GATE,
     ["[FAIL] 调度器在到期后才看要不要等"]),
    ("P11 调度器绕开错峰闸门", F_UI, OLD_TICK, NEW_TICK,
     ["[FAIL] 到点调度走错峰闸门"]),
    ("P12 聊天也等空闲", F_UI, OLD_CHAT, NEW_CHAT,
     ["[FAIL] 聊天发送不会等空闲时段"]),
    ("P13 面板默认就等", F_PANEL, OLD_PANEL, NEW_PANEL,
     ["[FAIL] 面板勾选默认关闭"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    watched = [F_CFG, F_AUTO, F_UI, F_PANEL]
    G.arm(watched)
    orig = {}
    for path in watched:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            orig[path] = fh.read()
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            src = orig[target]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            n = src.count(old)
            if n != 1:
                print("SKIP %s: old 串出现 %d 次，非唯一" % (name, n))
                continue
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(src.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and all(token in out for token in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2500:])
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
    finally:
        for path, src in orig.items():
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
