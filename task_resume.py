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
import tempfile
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


def _atomic_write(path, data):
    """v4.108 M-15：tmp + os.replace 原子写。

    原实现 open("w")+json.dump 非原子——写入途中崩溃/强杀会留下截断的损坏文件，
    而 load 端 except: pass 静默吞掉，等于检查点静默失效。tmp 文件写完再 os.replace
    是同一文件系统内的原子重命名，任何时刻读到的都是完整旧文件或完整新文件。
    """
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise


def save_checkpoint(cfg, state):
    """写入/覆盖一个检查点。state 至少含 task_id；其余字段自由。"""
    try:
        d = _dir(cfg)
        os.makedirs(d, exist_ok=True)
        state = dict(state)
        state["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        _atomic_write(_path(cfg, state["task_id"]), state)
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
    """列出未完成（status 非 done/completed）的检查点，按最后活跃时间倒序。返回带 _task_id 的列表。

    v4.101：引入 status 字段区分 running / paused / done。
    - running：任务进行中（含崩溃/强杀残留）。
    - paused：用户主动暂停/取消，保留检查点供「继续」入口复用。
    - done / completed：已结束，不列为可恢复项（兼容旧 done 字段）。
    """
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
            if data.get("status") in ("done", "completed"):
                continue
            if data.get("done"):  # 兼容旧字段
                continue
            data["_task_id"] = fn[:-5]
            out.append(data)
        except Exception:
            continue
    out.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return out


def mark_done(cfg, task_id):
    """干净退出：删除检查点（正常完成不留恢复项）。"""
    try:
        p = _path(cfg, task_id)
        if os.path.exists(p):
            os.remove(p)
            return True
    except Exception:
        pass
    return False


def mark_paused(cfg, task_id):
    """主动暂停/用户取消：保留检查点，仅把状态改为 paused（供「继续」入口复用）。

    相比 mark_done（删除文件），paused 保留断点状态，下次扫描可提示用户继续。
    """
    try:
        p = _path(cfg, task_id)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["status"] = "paused"
            data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            _atomic_write(p, data)  # v4.108 M-15：原子写
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
            _atomic_write(p, data)  # v4.108 M-15：原子写
            return True
    except Exception:
        pass
    return False
