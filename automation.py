# -*- coding: utf-8 -*-
"""小臭玩AI — 自动化任务模块（定时提醒 / 定时执行 Agent 任务）。

职责：
1. 任务数据模型与 JSON 持久化（~/Documents/小臭玩AI/automation_tasks.json）；
2. 调度判断：一次性 / 每天 / 每周 / 间隔重复；
3. 供面板（automation_panel）与主窗口调度器（ui.py 的 QTimer tick）复用。

纯数据/逻辑模块，不依赖 Qt（便于单测与冒烟）。
"""

import os
import json
import time
import uuid
import logging
from datetime import datetime, timedelta

from config import USER_DATA_DIR

log = logging.getLogger("dsdesktop")

TASKS_PATH = os.path.join(USER_DATA_DIR, "automation_tasks.json")

# ---- 调度类型 ----
SCHED_ONCE = "once"          # 一次性：at_date + at_time
SCHED_DAILY = "daily"        # 每天：at_time
SCHED_WEEKLY = "weekly"      # 每周：weekday(0=周一..6=周日) + at_time
SCHED_INTERVAL = "interval"  # 间隔：interval_minutes

# ---- 动作类型 ----
ACT_REMIND = "remind"        # 到点弹窗提醒
ACT_RUN = "run"              # 到点把 message 作为指令交给 Agent 执行

SCHEDULE_TYPES = [SCHED_ONCE, SCHED_DAILY, SCHED_WEEKLY, SCHED_INTERVAL]
ACTIONS = [ACT_REMIND, ACT_RUN]

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

SCHEDULE_LABELS = {
    SCHED_ONCE: "一次性",
    SCHED_DAILY: "每天",
    SCHED_WEEKLY: "每周",
    SCHED_INTERVAL: "间隔重复",
}
ACTION_LABELS = {
    ACT_REMIND: "定时提醒",
    ACT_RUN: "执行任务",
}


def new_task(name, action, message, schedule_type, at_time="09:00",
             at_date="", weekday=0, interval_minutes=60, enabled=True):
    """构造一个任务字典（新建用）。"""
    return {
        "id": uuid.uuid4().hex[:12],
        "name": (name or "").strip() or "未命名任务",
        "action": action if action in ACTIONS else ACT_REMIND,
        "message": message or "",
        "schedule_type": schedule_type if schedule_type in SCHEDULE_TYPES else SCHED_DAILY,
        "at_time": at_time or "09:00",
        "at_date": at_date or "",
        "weekday": int(weekday) % 7,
        "interval_minutes": max(1, int(interval_minutes or 60)),
        "enabled": bool(enabled),
        "last_run": 0.0,
        "created": time.time(),
    }


class AutomationStore:
    """自动化任务持久化存储。"""

    def __init__(self, path=None):
        self.path = path or TASKS_PATH
        self.tasks = []
        self._load()

    def _load(self):
        self.tasks = []  # 先清空，避免重复 reload 时 append 导致任务翻倍
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("tasks", []) if isinstance(data, dict) else data
            for t in raw:
                if isinstance(t, dict) and t.get("id"):
                    t.setdefault("last_run", 0.0)
                    t.setdefault("enabled", True)
                    self.tasks.append(t)
        except Exception as e:
            log.error("加载 automation_tasks.json 失败: %s", e)

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"tasks": self.tasks}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            log.error("保存 automation_tasks.json 失败: %s", e)

    def add(self, task):
        self.tasks.append(task)
        self.save()

    def update(self, task):
        for i, t in enumerate(self.tasks):
            if t.get("id") == task.get("id"):
                self.tasks[i] = task
                break
        self.save()

    def delete(self, task_id):
        self.tasks = [t for t in self.tasks if t.get("id") != task_id]
        self.save()

    def set_enabled(self, task_id, enabled):
        for t in self.tasks:
            if t.get("id") == task_id:
                t["enabled"] = bool(enabled)
                break
        self.save()

    def get(self, task_id):
        for t in self.tasks:
            if t.get("id") == task_id:
                return t
        return None

    def list_all(self):
        return list(self.tasks)

    def list_enabled(self):
        return [t for t in self.tasks if t.get("enabled", True)]


# ---- 调度判断（纯函数） ----

def _parse_hm(s):
    try:
        h, m = str(s).split(":")[:2]
        return int(h), int(m)
    except Exception:
        return 0, 0


def is_due(task, now=None):
    """判断任务此刻是否到期。now 为 datetime，缺省用当前时间。"""
    now = now or datetime.now()
    st = task.get("schedule_type", SCHED_DAILY)
    last = float(task.get("last_run", 0.0) or 0.0)
    now_ts = now.timestamp()
    hh, mm = _parse_hm(task.get("at_time", "09:00"))

    if st == SCHED_ONCE:
        if last > 0:
            return False
        try:
            iso = f"{task.get('at_date', '')} {task.get('at_time', '09:00')}".strip()
            dt = datetime.fromisoformat(iso)
        except Exception:
            return False
        return now_ts >= dt.timestamp()

    if st == SCHED_DAILY:
        if last > 0 and datetime.fromtimestamp(last).date() == now.date():
            return False
        return (now.hour, now.minute) >= (hh, mm)

    if st == SCHED_WEEKLY:
        if now.weekday() != int(task.get("weekday", 0)) % 7:
            return False
        if last > 0 and datetime.fromtimestamp(last).date() == now.date():
            return False
        return (now.hour, now.minute) >= (hh, mm)

    if st == SCHED_INTERVAL:
        interval = max(1, int(task.get("interval_minutes", 60)))
        if last <= 0:
            created = float(task.get("created", now_ts) or now_ts)
            return now_ts >= created + interval * 60
        return now_ts >= last + interval * 60

    return False


def mark_fired(task, now=None):
    """触发后回写 last_run；once 类型同时置 disabled。返回是否持久化字段有变化。"""
    now = now or datetime.now()
    task["last_run"] = now.timestamp()
    if task.get("schedule_type") == SCHED_ONCE:
        task["enabled"] = False


def schedule_summary(task):
    """调度方式的简短中文描述。"""
    st = task.get("schedule_type", SCHED_DAILY)
    at_time = task.get("at_time", "09:00")
    if st == SCHED_ONCE:
        return f"一次性 · {task.get('at_date', '')} {at_time}"
    if st == SCHED_DAILY:
        return f"每天 {at_time}"
    if st == SCHED_WEEKLY:
        return f"每周{WEEKDAY_NAMES[int(task.get('weekday', 0)) % 7]} {at_time}"
    if st == SCHED_INTERVAL:
        return f"每 {int(task.get('interval_minutes', 60))} 分钟"
    return at_time


def next_run_text(task, now=None):
    """下次触发时间的人类可读描述。"""
    now = now or datetime.now()
    st = task.get("schedule_type", SCHED_DAILY)
    at_time = task.get("at_time", "09:00")
    hh, mm = _parse_hm(at_time)

    if st == SCHED_ONCE:
        return f"{task.get('at_date', '')} {at_time}"
    if st == SCHED_DAILY:
        if (now.hour, now.minute) < (hh, mm):
            return f"今天 {at_time}"
        return f"明天 {at_time}"
    if st == SCHED_WEEKLY:
        wd = int(task.get("weekday", 0)) % 7
        if now.weekday() == wd and (now.hour, now.minute) < (hh, mm):
            return f"今天 {at_time}"
        days_ahead = (wd - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        d = now.date() + timedelta(days=days_ahead)
        return f"{d.strftime('%m-%d')} {at_time}"
    if st == SCHED_INTERVAL:
        interval = max(1, int(task.get("interval_minutes", 60)))
        last = float(task.get("last_run", 0.0) or 0.0)
        base = last if last > 0 else float(task.get("created", now.timestamp()) or now.timestamp())
        nxt = datetime.fromtimestamp(base + interval * 60)
        return f"下次 {nxt.strftime('%m-%d %H:%M')}"
    return at_time
