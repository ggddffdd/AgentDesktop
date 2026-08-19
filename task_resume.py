# -*- coding: utf-8 -*-
"""task_resume：长任务断点续跑 / 心跳（借鉴 Prime-Agent 的 daemon 续跑/reattach 范式）。

设计原则：
- 长任务（小说一条龙编排）跑动中，在【阶段边界】把可恢复状态（累积 messages + 当前阶段号 + 草稿）落到磁盘。
- 心跳：写手循环每轮刷新 updated 时间戳，记录任务最后活跃时间。
- 干净退出（正常完成 / 用户取消）→ 删除检查点；只有【崩溃 / 被强杀】才留下检查点。
- 下次启动扫描到「未完成检查点」→ 提示用户从断点继续（reattach），避免从头重跑浪费。
- 与 app shell 解耦：本模块纯标准库、无 Qt 依赖，可被离线单测直接 import。
"""
import os
import json
import datetime
import uuid


DEFAULT_DIR_NAME = "task_resume"


def _dir(cfg):
    if isinstance(cfg, dict) and cfg.get("task_resume_dir"):
        return cfg["task_resume_dir"]
    base = os.path.join(os.path.expanduser("~"), "Documents", "AgentDesktop")
    return os.path.join(base, DEFAULT_DIR_NAME)


def _path(cfg, task_id):
    return os.path.join(_dir(cfg), f"{task_id}.json")


def new_task_id():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]


def save_checkpoint(cfg, state):
    """写入/覆盖一个检查点。state 至少含 task_id；其余字段自由。"""
    try:
        d = _dir(cfg)
        os.makedirs(d, exist_ok=True)
        state = dict(state)
        state["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        with open(_path(cfg, state["task_id"]), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_checkpoint(cfg, task_id):
    try:
        p = _path(cfg, task_id)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def list_active(cfg):
    """列出未完成（无 done 标记）的检查点，按最后活跃时间倒序。返回带 _task_id 的列表。"""
    d = _dir(cfg)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in os.listdir(d):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("done"):
                continue
            data["_task_id"] = fn[:-5]
            out.append(data)
        except Exception:
            continue
    out.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return out


def mark_done(cfg, task_id):
    """干净退出：删除检查点（正常完成 / 用户取消都不留恢复项）。"""
    try:
        p = _path(cfg, task_id)
        if os.path.exists(p):
            os.remove(p)
            return True
    except Exception:
        pass
    return False


def update_heartbeat(cfg, task_id):
    """轻量心跳：仅刷新 updated 时间戳（不重写全部 state），用于记录最后活跃时间。"""
    try:
        p = _path(cfg, task_id)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except Exception:
        pass
    return False
