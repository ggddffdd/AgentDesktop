# -*- coding: utf-8 -*-
"""Agent 军团数据层 v4.121

把「编排页」从单一流水线（小说一条龙）泛化成**可自定义团队角色的多项目军团**。

设计要点：
- **角色库 role_library**：可复用角色定义，每个角色含 7 要素
  （使命 / 约束 / 工具白名单 / 模型 / 输出格式 / 质量标准 / 自检），
  范式参考 Apache-2.0 项目 openclaw-multi-agent-team 的结构化角色 prompt。
- **项目 projects**：多项目并存。每个项目 = 若干**波次 wave**，
  wave 内成员并行、wave 间串行依赖，正好映射到已有的 task_graph.TaskGraph。
- **成员自包含**：项目里的成员是角色快照（从角色库导入时复制一份），
  改角色库不会破坏已有项目。

数据存 ~/Documents/小臭玩AI/legion.json（独立文件，不污染 config.json）。
"""
import os
import re
import json
import uuid
import copy
import time
import logging
import threading
import hashlib

log = logging.getLogger("legion")

LEGION_DIR = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI")
LEGION_PATH = os.path.join(LEGION_DIR, "legion.json")
# 技能 SKILL.md 默认扫描根目录（不持久化进 legion.json，每次现扫）
DEFAULT_SKILLS_DIR = os.path.join(LEGION_DIR, "skills")

# 项目分类（沿用 workflow_manager_ui 的分类习惯，另补军团专属）
CATEGORIES = ["内容创作", "视频创作", "小说创作", "营销运营", "调研分析", "日常助手", "其他"]

# 角色 7 要素字段名（顺序即表单顺序）
ROLE_FIELDS = [
    ("name", "角色名"),
    ("emoji", "图标"),
    ("mission", "使命"),
    ("constraints", "约束"),
    ("tools", "工具白名单"),
    ("model", "模型"),
    ("output_format", "输出格式"),
    ("quality", "质量标准"),
    ("self_check", "自检"),
]

# 常用工具候选（与 agent_node._TOOL_DESC 对齐，另补实际在用的工具名）
TOOL_CANDIDATES = [
    "web_search", "web_fetch", "write_file", "read_file",
    "run_python", "image_gen", "search_memory", "remember",
    # 军团调度三件套（v4.122 新增）：项目经理靠这三个「眼睛」做验收与调度
    "legion_list_outputs", "legion_get_output", "legion_read_log",
]

# 军团查询类工具名集合（判断某角色是不是「调度/验收型」用）
LEGION_QUERY_TOOLS = ("legion_list_outputs", "legion_get_output", "legion_read_log")


# ============ 运行时状态仓（v4.122 新增）============
# 供 legion_list_outputs / legion_get_output / legion_read_log 三个查询工具读取。
# 背景：军团执行日志此前只往 UI 日志框 emit，不落盘、外部读不到 —— 项目经理
# 加进来也是瞎子。这里开一个进程内状态仓，执行器边跑边写，工具按 run_id 读。
# 纯内存，不持久化进 legion.json（避免垃圾堆积）。
_RUNTIME_LOCK = threading.Lock()
_RUNTIME = {
    "current_run_id": None,
    "runs": {},        # run_id -> {"project", "task", "t0", "log": [], "outputs": [], "status"}
}
_MAX_RUNS = 20         # 只保留最近 20 次，防止长会话内存膨胀
_LOG_TAIL_KEEP = 2000  # 单次 run 最多保留 2000 行日志


def start_run(project_name="", task=""):
    """开一次军团执行，返回 run_id。执行器应在开跑前调用。"""
    rid = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    with _RUNTIME_LOCK:
        _RUNTIME["runs"][rid] = {
            "project": project_name or "",
            "task": task or "",
            "t0": time.time(),
            "log": [],
            "outputs": [],   # [(wave_idx, attempt, role_name, text)]
            "status": "running",
        }
        _RUNTIME["current_run_id"] = rid
        # 超量清理：按创建顺序丢最老的
        while len(_RUNTIME["runs"]) > _MAX_RUNS:
            _RUNTIME["runs"].pop(next(iter(_RUNTIME["runs"])))
    return rid


def get_run(run_id=None):
    """取一次执行的状态快照（dict 拷贝）；run_id 为空取当前/最近一次。"""
    with _RUNTIME_LOCK:
        rid = run_id or _RUNTIME.get("current_run_id")
        runs = _RUNTIME["runs"]
        if rid and rid in runs:
            r = runs[rid]
            return {
                "run_id": rid,
                "project": r["project"],
                "task": r["task"],
                "t0": r["t0"],
                "log": list(r["log"]),
                "outputs": list(r["outputs"]),
                "status": r["status"],
            }
        if not runs:
            return None
        rid = list(runs)[-1]
        r = runs[rid]
        return {
            "run_id": rid,
            "project": r["project"],
            "task": r["task"],
            "t0": r["t0"],
            "log": list(r["log"]),
            "outputs": list(r["outputs"]),
            "status": r["status"],
        }


def record_log(run_id, line):
    """追加一行执行日志（同时落盘，便于事后复盘）。"""
    if not run_id:
        return
    with _RUNTIME_LOCK:
        r = _RUNTIME["runs"].get(run_id)
        if r is None:
            return
        r["log"].append(line.rstrip("\n"))
        if len(r["log"]) > _LOG_TAIL_KEEP:
            del r["log"][:-_LOG_TAIL_KEEP]
    _persist_log(run_id, line)


RUNS_DIR = os.path.join(LEGION_DIR, "legion_runs")


def _persist_log(run_id, line):
    """日志落盘到 ~/Documents/小臭玩AI/legion_runs/<run_id>.log（best-effort，失败不影响执行）。"""
    try:
        os.makedirs(RUNS_DIR, exist_ok=True)
        with open(os.path.join(RUNS_DIR, f"{run_id}.log"), "a", encoding="utf-8") as f:
            f.write(line if line.endswith("\n") else line + "\n")
    except Exception:
        pass


def record_output(run_id, wave_idx, attempt, role_name, text):
    """登记一位成员的产出，供验收/调度工具查询。"""
    if not run_id:
        return
    with _RUNTIME_LOCK:
        r = _RUNTIME["runs"].get(run_id)
        if r is None:
            return
        r["outputs"].append({
            "wave": wave_idx, "attempt": attempt,
            "role": role_name, "text": text or "",
            "chars": len(text or ""),
        })


def end_run(run_id, status="done"):
    with _RUNTIME_LOCK:
        r = _RUNTIME["runs"].get(run_id)
        if r is not None:
            r["status"] = status


def reset_runtime():
    """清空状态仓（测试/单调用）。"""
    with _RUNTIME_LOCK:
        _RUNTIME["current_run_id"] = None
        _RUNTIME["runs"].clear()


# ============ 断点续传 checkpoint（v4.124.5）============
# 设计：每波完成后 / 终止前 / 异常前 落盘一次，下次可「从上次波次续跑」」
# 路径：~/Documents/小臭玩AI/legion_checkpoints/<project_id>.json
# 原子写：.tmp → os.replace（防写一半崩坏）
# 安全：magic header + sha256 校验和 → 损坏自动改名 .broken
CHECKPOINT_DIR = os.environ.get("XC_LEGION_CKPT_DIR") or os.path.join(
    LEGION_DIR, "legion_checkpoints")
_CKPT_MAGIC = "XC_LEGION_CKPT_v1"
# v4.124.6：UI 轻量查 ckpt 时只读头 _CKPT_LIGHT_LIMIT 字节（head 字段都靠前）
_CKPT_LIGHT_LIMIT = 2048
_CKPT_LOCK = threading.Lock()


def _checkpoint_path(project_id):
    return os.path.join(CHECKPOINT_DIR, f"{project_id or 'unknown'}.json")


def _waves_fingerprint(waves):
    """对成员阵容取指纹：成员名 + 工具白名单 + 模型的元组列表（顺序敏感）。

    用于续跑时比对阵容是否一致。模型/工具轻微调整 → 仍可续跑但 UI 提示。
    """
    fp = []
    for w in waves or []:
        wave_fp = []
        for r in w:
            wave_fp.append((
                r.get("name", ""),
                tuple(sorted(r.get("tools") or [])),
                r.get("model", ""),
            ))
        fp.append(tuple(wave_fp))
    return fp


def save_checkpoint(project_id, run_id, task, plan_text, parts_by_wave,
                    gate_reports, last_completed_wave, waves):
    """原子写 checkpoint（v4.124.5）。

    三处调用时机（LegionWorker 内部）：
      1. 每波 parts_by_wave 写入后 → last_completed_wave = wi
      2. 终止前 / 异常前 → 保留已完成的 parts_by_wave
    失败不抛异常（best-effort）—— 落盘失败只是失去续跑能力，不应阻断运行。
    """
    if not project_id:
        return False
    try:
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        payload = {
            "magic": _CKPT_MAGIC,
            "project_id": project_id,
            "run_id": run_id or "",
            "task": task or "",
            "plan_text": plan_text or "",
            "parts_by_wave": {str(k): v for k, v in (parts_by_wave or {}).items()},
            "gate_reports": list(gate_reports or []),
            "last_completed_wave": int(last_completed_wave or 0),
            "waves_fingerprint": _waves_fingerprint(waves),
            "saved_at": time.time(),
            "version": 1,
        }
        # checksum 算法与 load 端严格对齐：pop 掉 magic + checksum 后再 sha256。
        # 不在这里 pop，因为下面的 json.dump 还要写完整 dict —— 但要先算校验和。
        to_hash = {k: v for k, v in payload.items() if k not in ("magic", "checksum")}
        raw = json.dumps(to_hash, ensure_ascii=False, sort_keys=True).encode("utf-8")
        payload["checksum"] = hashlib.sha256(raw).hexdigest()
        # 原子写：先写 .tmp → fsync → os.replace
        path = _checkpoint_path(project_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, path)
        return True
    except Exception as e:
        # best-effort：落盘失败不应阻断运行，但 UI 可日志提示
        log.warning("save_checkpoint(%s) 失败：%s", project_id, e)
        return False


def load_checkpoint(project_id):
    """读取 checkpoint。返回 dict 或 None。

    校验流程：magic header → sha256 → JSON 解析。失败 → 改名 .broken + 返回 None。
    """
    if not project_id:
        return None
    path = _checkpoint_path(project_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        # 损坏 → 改名 .broken
        try:
            os.replace(path, path + ".broken")
        except Exception:
            pass
        log.warning("load_checkpoint(%s) JSON 损坏已备份：%s", project_id, e)
        return None
    if not isinstance(data, dict) or data.get("magic") != _CKPT_MAGIC:
        try:
            os.replace(path, path + ".broken")
        except Exception:
            pass
        log.warning("load_checkpoint(%s) magic 不匹配，已备份", project_id)
        return None
    # 校验 sha256：与 save 端严格对齐 —— 排除 magic 和 checksum 后再 sha256
    saved_sum = data.pop("checksum", None)
    data.pop("magic", None)        # 消费 magic，避免回填到 caller 污染字段语义
    to_hash = {k: v for k, v in data.items() if k not in ("magic", "checksum")}
    raw = json.dumps(to_hash, ensure_ascii=False, sort_keys=True).encode("utf-8")
    cur_sum = hashlib.sha256(raw).hexdigest()
    if saved_sum != cur_sum:
        try:
            os.replace(path, path + ".broken")
        except Exception:
            pass
        log.warning("load_checkpoint(%s) checksum 不匹配，已备份", project_id)
        return None
    # parts_by_wave 的 key 还原回 int（落盘时转成 str 省 JSON 坑）
    pbw = data.get("parts_by_wave", {}) or {}
    data["parts_by_wave"] = {int(k): v for k, v in pbw.items() if str(k).isdigit()}
    return data


def clear_checkpoint(project_id):
    """删除 checkpoint（用户选了「从头重跑」时调）。"""
    if not project_id:
        return False
    path = _checkpoint_path(project_id)
    try:
        if os.path.exists(path):
            os.remove(path)
        return True
    except Exception as e:
        log.warning("clear_checkpoint(%s) 失败：%s", project_id, e)
        return False


def list_resumable_projects():
    """扫 CHECKPOINT_DIR 列出所有可续跑项目 ID。"""
    if not os.path.isdir(CHECKPOINT_DIR):
        return []
    out = []
    try:
        for fn in sorted(os.listdir(CHECKPOINT_DIR)):
            if not fn.endswith(".json"):
                continue
            pid = fn[:-5]
            ckpt = load_checkpoint(pid)
            if ckpt:
                out.append({
                    "project_id": pid,
                    "last_completed_wave": ckpt.get("last_completed_wave", 0),
                    "task": ckpt.get("task", "")[:80],
                    "saved_at": ckpt.get("saved_at", 0),
                })
    except Exception as e:
        log.warning("list_resumable_projects 失败：%s", e)
    return out


def has_checkpoint(project_id):
    """v4.124.6：轻量查"项目是否有可续跑 checkpoint"（UI 用，避免每次刷新都 load 全量）。

    返回值：
      - None：没有 / 损坏 / magic 不匹配
      - dict：精简字段 {run_id, last_completed_wave, saved_at, saved_at_human,
                          parts_count, gate_count} —— 够 UI 显示 + LegionWorker 透传
    """
    if not project_id:
        return None
    path = _checkpoint_path(project_id)
    if not os.path.isfile(path):
        return None
    # 故意走轻量读：避免 load_checkpoint 的 magic / sha256 校验，UI 刷得快
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read(_CKPT_LIGHT_LIMIT)
        import json as _json
        obj = _json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict) or obj.get("magic") != _CKPT_MAGIC:
        return None
    saved_at = float(obj.get("saved_at", 0) or 0)
    return {
        "run_id": obj.get("run_id", ""),
        "last_completed_wave": int(obj.get("last_completed_wave", 0) or 0),
        "saved_at": saved_at,
        "saved_at_human": time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.localtime(saved_at)) if saved_at else "",
        "parts_count": len(obj.get("parts_by_wave") or {}),
        "gate_count": len(obj.get("gate_reports") or []),
    }


# ============ 共享任务板（v4.123）============
# 设计原则：项目状态**不挂在某个角色身上**（含项目经理），而是挂在项目自己的任务板上。
# 这样换成员、换 PM、甚至重启程序，任务板都还在 —— 项目才连续。
# 任务板是 PM 的"记忆"，也是执行器写状态的唯一落点。
BOARD_DIR = os.path.join(LEGION_DIR, "legion_board")
_BOARD_LOCK = threading.Lock()
_BOARD_EVENT_KEEP = 200       # 每个项目最多保留的事件条数


def _board_path(project_id):
    return os.path.join(BOARD_DIR, f"{project_id or 'unknown'}.json")


def _atomic_write_json(path, data):
    """原子写：先写 .tmp 再替换，避免中途崩溃留下半个文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def board_load(project_id):
    """读任务板；不存在返回空板。"""
    try:
        with open(_board_path(project_id), "r", encoding="utf-8") as f:
            b = json.load(f)
        if isinstance(b, dict):
            b.setdefault("nodes", {})
            b.setdefault("events", [])
            return b
    except Exception:
        pass
    return {"project_id": project_id, "nodes": {}, "events": [], "updated": 0}


def board_get(project_id):
    """给工具用的只读快照。"""
    return board_load(project_id)


def board_update(project_id, node_key, project_name="", **fields):
    """更新一个节点的状态，并记一条事件。

    node_key 约定：成员节点 "w{wave}_m{slot}"、验收节点 "gate_w{wave}"、计划节点 "pm_plan"。
    fields 常见：status / role / wave / summary / attempt / run_id / pm_verdict。
    """
    if not project_id:
        return
    with _BOARD_LOCK:
        b = board_load(project_id)
        node = b["nodes"].get(node_key) or {}
        node.update({k: v for k, v in fields.items() if v is not None})
        node["updated"] = time.time()
        b["nodes"][node_key] = node
        if project_name:
            b["project_name"] = project_name
        b["updated"] = time.time()
        b["events"].append({
            "ts": time.time(),
            "time": time.strftime("%H:%M:%S"),
            "node": node_key,
            **{k: v for k, v in fields.items() if k != "summary"},
        })
        del b["events"][:-_BOARD_EVENT_KEEP]
        try:
            _atomic_write_json(_board_path(project_id), b)
        except Exception as e:
            log.warning("任务板写入失败: %s", e)


def board_reset(project_id):
    """清空一个项目的任务板（换任务重开时用）。"""
    with _BOARD_LOCK:
        try:
            _atomic_write_json(_board_path(project_id), {
                "project_id": project_id, "nodes": {}, "events": [],
                "updated": time.time()})
        except Exception:
            pass


# ============ 授权：审计 / 围栏 / 信任（宪法第二章）============
# 铁律：**重要节点的授权权永远在用户手里**。PM 只有建议权。
# 自动放行是可选开关（默认关），即使开启也必须过三道边界：
#   ① 参数围栏  —— 指纹（波次+成员+任务）对得上才算"同类"，变了就必须重新批
#   ② 次数围栏  —— 自动放行累计有上限，用尽即收回人手
#   ③ 一键收回  —— 用户可以随时清空全部信任，且每笔自动放行都进审计日志
AUTH_LOG = os.path.join(LEGION_DIR, "legion_auth.jsonl")
TRUST_PATH = os.path.join(LEGION_DIR, "legion_trust.json")


def fingerprint(wave_no, members, task=""):
    """授权参数指纹：波次序号 + 成员名单 + 任务摘要。

    指纹相同 = "同类操作"；任一变化（换人 / 换波次 / 换任务）都不是同类，
    必须重新回到人手 —— 防止"第 4 次类型相同但参数变了"被自动放行。
    """
    names = "|".join(str((m or {}).get("name", "")).strip() for m in (members or []))
    task_key = re.sub(r"\s+", "", str(task or ""))[:80]
    raw = f"w{int(wave_no)}::{names}::{task_key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def record_auth(project_id, project_name, wave_no, fp, pm_verdict,
                decision, by="user", reason="", run_id=""):
    """写一笔授权审计日志（JSONL，append-only）。

    by: "user"= 用户亲手批；"auto"= 围栏内自动放行；"timeout"= 超时（等同不授权）。
    每笔都要能回答：谁批的、批的什么、凭什么批。

    v4.124.5：新增 `run_id` 字段 —— 同一项目所有授权必须串在同一条 run_id 链上，
    续跑复用 ckpt_run_id 不能另开新账，审计谱系不出现"复活节岛"。
    """
    try:
        os.makedirs(LEGION_DIR, exist_ok=True)
        rec = {
            "ts": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "run_id": run_id or "",     # v4.124.5：跨生死审计谱系
            "project_id": project_id, "project_name": project_name,
            "wave": wave_no, "fingerprint": fp,
            "pm_verdict": pm_verdict, "decision": decision,
            "by": by, "reason": reason,
        }
        with open(AUTH_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
    except Exception as e:
        log.warning("授权审计写入失败: %s", e)
        return None


def read_auth(limit=200):
    """读最近 N 条授权审计（倒序返回，最新在前）。"""
    out = []
    try:
        with open(AUTH_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out[-limit:][::-1]


def _trust_load():
    try:
        with open(TRUST_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _trust_save(d):
    try:
        os.makedirs(LEGION_DIR, exist_ok=True)
        _atomic_write_json(TRUST_PATH, d)
    except Exception:
        pass


def trust_key(project_id, fp):
    return f"{project_id}::{fp}"


def grant_trust(project_id, fp, wave_no=0):
    """用户亲手批准一次 → 该指纹的信任计数 +1（用于"同类放行"的累积）。"""
    d = _trust_load()
    k = trust_key(project_id, fp)
    item = d.get(k) or {"count": 0, "auto_used": 0}
    item["count"] = int(item.get("count", 0)) + 1
    item["wave"] = wave_no
    item["last_ts"] = time.time()
    d[k] = item
    _trust_save(d)
    return item["count"]


def auto_pass_allowed(project_id, fp, threshold=0, max_auto=0):
    """是否允许**自动放行**（围栏判定）。

    返回 (allowed: bool, reason: str)。任一围栏不过都返回 False：
      - threshold <= 0        → 从不自动放行（默认，最合宪）
      - 人工批准次数 < threshold → 累积不够
      - 已自动放行次数 >= max_auto → 次数围栏用尽
    """
    if threshold <= 0:
        return False, "未开启自动放行（每波都需你亲手授权）"
    item = _trust_load().get(trust_key(project_id, fp)) or {}
    n_human = int(item.get("count", 0))
    n_auto = int(item.get("auto_used", 0))
    if n_human < threshold:
        return False, f"同类人工批准 {n_human}/{threshold} 次，累积不够"
    if max_auto > 0 and n_auto >= max_auto:
        return False, f"自动放行已达上限 {n_auto}/{max_auto} 次，收回人手"
    return True, (f"依据：同类已人工批准 {n_human} 次（指纹 {fp}），"
                  f"自动放行 {n_auto}/{max_auto or '∞'} 次")


def mark_auto_used(project_id, fp):
    """记一次自动放行（次数围栏计数）。"""
    d = _trust_load()
    k = trust_key(project_id, fp)
    item = d.get(k) or {"count": 0, "auto_used": 0}
    item["auto_used"] = int(item.get("auto_used", 0)) + 1
    d[k] = item
    _trust_save(d)
    return item["auto_used"]


def revoke_trust(project_id=None, wave_no=None):
    """一键收回信任：不传参清空全部；传 project_id 只清该项目。

    每笔收回都进审计日志 —— 信任是累积的，也必须可撤销、可追溯。
    """
    d = _trust_load()
    if project_id is None:
        n = len(d)
        d = {}
    else:
        keys = [k for k in d if k.startswith(f"{project_id}::")]
        n = len(keys)
        for k in keys:
            d.pop(k, None)
    _trust_save(d)
    record_auth(project_id or "*", "（信任收回）", wave_no or 0, "*", "",
                "revoke", by="user", reason=f"收回 {n} 条自动放行信任")
    return n

SCHEMA_VERSION = 1


# ============ 工厂 ============
def new_role(name="新角色", emoji="", mission="", constraints="",
             tools=None, model="", output_format="", quality="", self_check="",
             skills=None, category="", focus=""):
    """构造一个角色定义（7 要素 + 3 个扩展字段）。

    model 留空 = 跟随全局配置。
    skills   —— 挂载的技能 slug 列表（对应技能目录下的文件夹名），
                找不到只是跳过，不会崩。
    category —— 角色分组（通用 / 内容运营 / 电商带货 / 创作 / 商业 / 调度），
                用于角色库膨胀后按组挑选，也是自动组队的筛选项。
    focus    —— v4.124 新增：**专注领域**的一句话标签（如「抖音算法/爆款脚本」）。
                它是角色库「菜单化」的关键字段——PM 自动组队时拿用户需求
                对着 focus 做匹配，而不是对着整段使命猜。留空时菜单自动
                取 mission 首行兜底，老角色不补也能用。
    """
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "emoji": emoji or "",
        "mission": mission,
        "constraints": constraints,
        "tools": list(tools or []),
        "model": model or "",
        "output_format": output_format,
        "quality": quality,
        "self_check": self_check,
        "skills": list(skills or []),
        # 留空交给 default_role_library 的兜底表归类；显式传了就以传的为准
        "category": (category or "").strip(),
        "focus": (focus or "").strip(),
    }


def new_project(name="新项目", emoji="", description="", category="其他"):
    """构造一个军团项目：含一个空波次，成员由用户在编辑器里添加。

    v4.122：新增波次验收闸门字段。
      gate_enabled  —— 每波跑完由「项目经理」验收，FAIL 则打回重跑（新项目默认开）
      gate_max_retry—— 单波最多重跑几次（v4.123 起默认 2，再 FAIL 就放行并标红）

    v4.123（宪法第二章）：闸门模式升级为三档，授权权收归用户。
      gate_mode   —— "off" 不开验收 / "advisory" PM 只出建议、按建议执行
                     / "human" **默认**：每波暂停等你亲手批准，PM 无放行权
      auto_pass_after —— 同类（同指纹）人工批准满 N 次后才允许自动放行，0=永不（默认）
      auto_pass_max   —— 自动放行累计上限，用尽即收回人手（默认 3）
    """
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "emoji": emoji or "",
        "description": description or "",
        "category": category,
        "waves": [{"members": []}],
        "review_enabled": False,
        "gate_enabled": True,
        # v4.123：1 → 2。首次交付默认不成熟（2-3 轮打磨是常态），
        # 只给 1 次重跑会让大量「差一口气」的产出被标红放行。
        "gate_max_retry": 2,
        "gate_mode": "human",
        "auto_pass_after": 0,
        "auto_pass_max": 3,
    }


def new_wave():
    return {"members": []}


# ============ 角色 → system prompt（7 要素 + 可选挂载技能）============
def build_role_prompt(role: dict, skills_dir: str = None) -> str:
    """把角色 7 要素 + 挂载的技能拼成 system prompt。

    只拼非空字段，避免把一堆空标题塞进上下文白烧 token。
    挂载的技能（role.skills[]）按 slug 去 skills_dir 读 SKILL.md 正文，
    拼在末尾 —— 这是「角色身份 + 方法论」组合的关键。
    """
    if not role:
        return ""
    name = role.get("name", "角色")
    parts = [f"你是「{name}」。"]

    def _add(title, key):
        v = (role.get(key) or "").strip()
        if v:
            parts.append(f"\n【{title}】\n{v}")

    _add("使命", "mission")
    _add("约束", "constraints")
    tools = role.get("tools") or []
    if tools:
        parts.append("\n【可用工具】\n只允许调用：" + "、".join(tools) +
                     "。其余工具一律不可用，需要时说明无法完成。")
    else:
        parts.append("\n【可用工具】\n本角色不使用工具，直接输出分析文本。")
    _add("输出格式", "output_format")
    _add("质量标准", "quality")
    _add("自检", "self_check")

    # ---- 挂载的技能 / 方法论（v4.121.3 新增）----
    skill_slugs = [s for s in (role.get("skills") or []) if isinstance(s, str) and s.strip()]
    if skill_slugs:
        skills_dir = skills_dir or DEFAULT_SKILLS_DIR
        parts.append("\n【挂载的技能 / 方法论】\n以下是本角色本次任务需要遵循的方法论/工作流：")
        for slug in skill_slugs:
            sk = _load_skill_prompt(slug, skills_dir)
            if sk:
                emoji = sk.get("emoji", "")
                sname = sk.get("name") or slug
                body = (sk.get("prompt") or "").strip()
                if body:
                    parts.append(f"\n### {emoji} {sname}\n{body}")
            else:
                parts.append(f"\n### ⚠️ {slug}\n（技能文件未找到，跳过）")

    return "\n".join(parts)


# ============ 默认角色库 ============
# 角色分组（v4.123）：角色库扩到 30 个后，选择器按组显示，自动组队也按组筛。
ROLE_CATEGORIES = ["通用", "内容运营", "电商带货", "创作", "商业", "调度"]

# 老角色（定义里没写 category）的归类兜底表
_CATEGORY_FALLBACK = {
    "研究员": "通用", "分析师": "通用", "写手": "创作", "配图师": "创作",
    "审校": "通用", "策划": "通用",
    "选品官": "电商带货", "竞品分析师": "电商带货", "带货文案": "电商带货",
    "主图策划": "电商带货", "投放运营": "电商带货", "转化话术师": "电商带货",
    "项目经理": "调度", "现实检验官": "通用",
}

# v4.124：**专注领域**标签表 —— 角色菜单的核心匹配字段。
# PM 组队时是拿用户需求对着这一列挑人，写得越具体，匹配越准。
# 没写 focus 的角色（含用户自建）由 role_focus() 从使命首行兜底。
_FOCUS_FALLBACK = {
    "研究员": "联网检索与事实核查",
    "分析师": "材料洞察与判断提炼",
    "写手": "成稿写作（多文体）",
    "配图师": "生图提示词与视觉方案",
    "审校": "事实/逻辑/合规挑错",
    "策划": "需求拆解与方案设计",
    "选品官": "选品：趋势/需求/利润",
    "竞品分析师": "竞品拆解与打法对标",
    "带货文案": "卖点转带货文案",
    "主图策划": "主图视觉与素材方案",
    "投放运营": "流量投放与数据复盘",
    "转化话术师": "售前私域转化话术",
    "项目经理": "调度与验收（不进波次）",
    "现实检验官": "成品终检/专治自评满分",
    "抖音操盘手": "抖音算法/爆款脚本/DOU+",
    "小红书运营官": "小红书笔记/标签/互动率",
    "公众号运营官": "公众号选题/打开率/留存",
    "叙事结构师": "故事结构与人物弧光",
    "快手操盘手": "快手老铁关系/直播间",
    "B站内容策略师": "B站长视频选题与留存",
    "知乎策略师": "知乎答题与专业权威",
    "多平台分发官": "一稿多平台改写分发",
    "私域运营官": "企微社群与生命周期",
    "直播带货教练": "直播间脚本与货盘排序",
    "跨境电商操盘": "亚马逊/Shopee 跨境运营",
    "心理学家": "人物动机与群体行为",
    "历史学家": "时代考据与制度风俗",
    "商业策略师": "竞争分析与商业模式",
    "定价分析师": "定价模型与价格策略",
    "AI引用策略师": "AI 搜索引用率优化",
}


def default_role_library():
    """开箱即用的角色库（大哥可直接用，也可改）。

    工具名与 agent_node._TOOL_DESC / config.get_all_tools 对齐。
    """
    _lib = [
        new_role(
            name="研究员", emoji="🔍",
            mission="搜索互联网获取信息，整理成结构化中文摘要",
            constraints="只采信有明确来源的信息，标注来源；找不到就明说找不到，不许编",
            tools=["web_search", "web_fetch"],
            output_format="分点列出，每条含【结论】【来源】【可信度】",
            quality="至少 3 个独立来源，覆盖正反两面观点",
            self_check="逐条检查是否有无来源的断言，有就删或补来源",
        ),
        new_role(
            name="分析师", emoji="📊",
            mission="基于上游材料提炼关键洞察、判断与建议",
            constraints="不做新的事实检索，只基于已有材料推理；区分「事实」与「推断」",
            tools=[],
            output_format="洞察 3-5 条 + 每条的判断依据 + 最终建议",
            quality="每条洞察必须能追溯到上游材料，禁止凭空发挥",
            self_check="检查是否存在没有依据的推断，标出来",
        ),
        new_role(
            name="写手", emoji="✍️",
            mission="把上游结论写成可直接使用的成稿",
            constraints="语气自然、去 AI 味；不新增未经上游确认的事实",
            tools=["write_file", "read_file"],
            output_format="完整成稿，结构清晰，可直接发布",
            quality="开头有钩子、中间有干货、结尾有收束",
            self_check="通读检查是否有 AI 腔和空话，有就改掉",
        ),
        new_role(
            name="配图师", emoji="🎨",
            mission="把文字内容转成可直接生图的提示词",
            constraints="只输出提示词，不输出解释；中文提示词，风格统一",
            tools=["image_gen"],
            output_format="按条编号，每条一句完整提示词（含风格+主体+构图+光线）",
            quality="提示词要具体到能直接出图，避免抽象形容词堆砌",
            self_check="检查每条提示词是否含风格与主体，缺一补上",
        ),
        new_role(
            name="审校", emoji="🔎",
            mission="独立审查上游成稿，挑事实错误、逻辑漏洞与合规风险",
            constraints="只评判不改写；问题按严重度分级；参与执行者不得自审",
            tools=[],
            output_format="问题清单（严重度 / 位置 / 问题描述 / 修改建议）+ 总评 PASS 或 FAIL",
            quality="必须给出至少 1 条具体可执行的修改建议，不能只说「不够好」",
            self_check="检查每条问题是否指出了具体位置和改法",
        ),
        new_role(
            name="策划", emoji="🧭",
            mission="把模糊需求拆成可执行方案：目标、路径、分工、验收标准",
            constraints="方案要能落地，拒绝空泛方法论；明确标出前置依赖",
            tools=[],
            output_format="目标 / 拆解步骤 / 每步产出物 / 验收标准",
            quality="每步都要有可交付的产出物，没有产出物的步骤删掉",
            self_check="检查是否每步都能判断「做完了没有」",
        ),
        # ---- 电商自动运营军团专用（大哥 09-05 新增）----
        new_role(
            name="选品官", emoji="🛒",
            mission="从市场趋势、需求缺口、利润空间三维度，选出有潜力且可落地的商品",
            constraints="只基于已有调研材料或常识判断，不凭空编造数据；区分「趋势」与「跟风」",
            tools=[],
            output_format="候选商品 3-5 个，每个含【市场趋势】【目标人群】【利润预估】【风险点】",
            quality="每个候选必须给出至少 1 条可辩护的支撑理由，禁止「感觉不错」",
            self_check="逐条检查是否都有依据，无依据的候选删掉",
        ),
        new_role(
            name="竞品分析师", emoji="⚔️",
            mission="拆解竞品/对标账号的商品、内容、价格与打法，找出可借鉴处与差异化机会",
            constraints="只做事实拆解与中立对比，不贬低对手；信息来源需标注",
            tools=["web_search", "web_fetch"],
            output_format="竞品画像（每个：定位/核心卖点/价格/内容风格/可借鉴处/差异化机会）",
            quality="每个竞品至少指出 1 点可借鉴 + 1 点差异化机会",
            self_check="检查是否有主观拉踩描述，一律改成立中陈述",
        ),
        new_role(
            name="带货文案", emoji="✍️",
            mission="把商品卖点写成能打动目标人群、可直接发布的带货文案或口播稿",
            constraints="不夸大功效、不虚假承诺、符合广告合规；语气口语化、去 AI 味",
            tools=["write_file", "read_file"],
            output_format="标题钩子 + 正文（痛点-卖点-信任-行动）+ 适用人群 / 慎用人群",
            quality="前 3 秒有钩子，每个卖点有依据，结尾有明确行动指令",
            self_check="通读检查是否有夸大或违禁词，有就改写",
        ),
        new_role(
            name="主图策划", emoji="🖼️",
            mission="把商品卖点转成能直接生图/出素材的视觉方案与生图提示词",
            constraints="只输出视觉方案与提示词，不输出成图解释；风格全篇统一",
            tools=["image_gen"],
            output_format="每张素材 1 条完整提示词（风格+主体+构图+光线+卖点可视化）",
            quality="提示词具体到能直接出图，卖点要可视化而非抽象形容词堆砌",
            self_check="检查每条是否含风格+主体+卖点，缺一补上",
        ),
        new_role(
            name="投放运营", emoji="📈",
            mission="基于商品与人群，制定流量投放/起量策略与数据复盘框架",
            constraints="不承诺具体 ROI 数字；区分策略与执行；标注关键前提假设",
            tools=["web_search", "read_file"],
            output_format="人群分层 / 渠道匹配 / 预算分配 / 关键指标与复盘模板",
            quality="每个渠道给出适用场景与理由；复盘要有清晰 KPI，可落地",
            self_check="检查是否承诺了不切实际的数字，是就改为区间或条件",
        ),
        new_role(
            name="转化话术师", emoji="💬",
            mission="设计售前/私域/客服转化的沟通话术与常见异议应答",
            constraints="不催单不骚扰、不承诺售后之外的事项；语气真诚不油腻",
            tools=[],
            output_format="开场话术 / 常见异议应答（问-答）/ 促单边界话术",
            quality="每个异议给出话术+适用场景+要避免踩的坑",
            self_check="检查是否有过度承诺或骚扰式话术，有就删除",
        ),
        # ---- 调度器（v4.122 新增）：军团里唯一的统筹型角色 ----
        # ⚠️ 宪法第二章：重要节点的授权权归用户。PM 只有建议权，无放行权。
        new_role(
            name="项目经理", emoji="📋",
            mission=(
                "你是军团的调度器与验收建议人，不是执行者，**更不是审批人**。只对三件事负责：\n"
                "① 拆任务：把目标拆成若干波次，定义每波的成员分工与先后依赖；\n"
                "② 排波次：判断哪波先跑、哪波可并行、哪波必须等上波验收；\n"
                "③ 验收汇总：逐波检查产出，给出「建议放行 / 建议打回」的**意见**，"
                "并给出下一波的调度建议。\n"
                "你自己不写文案、不做图、不选品——那些归执行角色。\n"
                "你也**不进任何任务波次**：你是全局调度台，不是某一波的成员。"
            ),
            constraints=(
                "🔴 授权红线（宪法第二章）：你的判定是**提请授权**，不是放行。\n"
                "  · 你只能说「建议放行 / 建议打回」，最终批不批由用户点头；\n"
                "  · 禁止写「准予放行」「已批准」「下一波已启动」这类越权措辞；\n"
                "  · 你可以催促用户：「第 N 波待你授权，建议放行」——提醒是你的活，代批不是。\n"
                "验收必须基于【实际读到的产出与执行日志】：先用 legion_list_outputs 看有哪些产出，"
                "再用 legion_get_output 读全文，必要时 legion_read_log 查执行过程、"
                "legion_board 查任务板上各节点的状态。\n"
                "没读到的产出必须明说「未读到该成员产出」，禁止脑补内容来凑评价。\n"
                "不因风格偏好打回，只因硬伤打回（事实错误、跑题、缺交付物、违反约束）。\n"
                "打回成本很高（重跑烧 token），没硬伤就建议放行。"
            ),
            tools=["legion_list_outputs", "legion_get_output", "legion_read_log",
                   "legion_board"],
            output_format=(
                "【判定】PASS 或 FAIL（= 建议放行 / 建议打回，**仍需用户授权**）\n"
                "【依据】逐条指向具体成员的具体产出片段（哪一位 · 哪一段 · 为什么算数）\n"
                "【问题清单】严重度 / 位置 / 问题描述 / 修改建议（FAIL 必填，至少 1 条可执行）\n"
                "【打回指令】本波重跑必须改什么（FAIL 必填，写成执行角色能直接照做的指令）\n"
                "【下一波调度建议】分工 / 依赖顺序 / 风险提示\n"
                "最后一行固定输出：判定：PASS  或  判定：FAIL"
            ),
            quality=(
                "每条问题必须能定位到某位成员产出的具体位置；"
                "FAIL 必须给出可执行的改法，禁止「不够好」「有待提升」这类空话"
            ),
            self_check=(
                "自检：① 判定是否建立在读到的产出上（而非想象）；"
                "② FAIL 是否附了可执行改法；③ 最后一行是不是标准的『判定：PASS/FAIL』；"
                "④ 有没有越权写「已批准/已放行」——有就改成「建议放行，待用户授权」"
            ),
            category="调度",
        ),

        # ================= v4.123：内容运营组 =================
        # 来源：GitHub msitarzewski/agency-agents（MIT）
        new_role(
            name="抖音操盘手", emoji="🎵", category="内容运营",
            mission=(
                "负责抖音账号的全链路操盘：账号定位 → 选题规划 → 脚本结构 → 流量运营 → 数据复盘。\n"
                "你不是「把视频拍好看」的人，你是「让算法把视频推出去」的人。\n"
                "核心交付：可执行的选题日历 + 逐条脚本结构 + 发布/投放建议 + 复盘结论。"
            ),
            constraints=(
                "🔴 算法优先级铁律：完播率 > 点赞率 > 评论率 > 转发率。任何时候冲突，先保全完播率。\n"
                "🔴 前 3 秒定生死：不许铺垫、不许慢热，上来就给钩子（冲突 / 价值 / 悬念 / 共鸣 四选一）。\n"
                "🔴 时长配类型：知识类 30-60s、剧情类 15-30s、直播切片 15s、带货测评 30-45s。\n"
                "🔴 合规红线：\n"
                "  · 禁绝对化用语（最好 / 第一 / 100% 有效 / 国家级）；\n"
                "  · 食品、药品、化妆品类目必须遵守广告法；\n"
                "  · 视频内不许引导跳转外站（触发限流）；\n"
                "  · 未成年人保护条款不可踩。\n"
                "🔴 竖屏 9:16、必须有字幕（大量用户静音观看）、当周热门 BGM。\n"
                "不承诺具体播放量；所有目标写成区间 + 前提条件。"
            ),
            tools=["web_search", "write_file", "read_file"],
            skills=["short-video-scripter", "platform-norm-profiler"],
            output_format=(
                "【账号诊断】现状 / 人群画像 / 内容定位 / 变现路径\n"
                "【选题日历】按周，每条含：选题名 / 类型 / 目标完播率 / 钩子类型\n"
                "【逐条脚本】\n"
                "  · 0-3s 钩子（写明四选一中的哪一种 + 为什么）\n"
                "  · 4-20s 正文（痛点放大 → 方案 → 演示/对比 → 数据）\n"
                "  · 21s-结尾 收束（一句话价值 + 互动引导 + 下集预告）\n"
                "  · 拍摄备注：景别 / 字幕 / BGM / 时长\n"
                "【发布与投放】发布时间窗 / DOU+ 定向建议 / 评论区运营动作\n"
                "【复盘指标】完播率、涨粉率、互动率，各给基线与改进动作"
            ),
            quality=(
                "每条脚本必须写明「完播率优化策略」——没写就是没做完；\n"
                "每个钩子必须说明类型与理由，禁止「开头要吸引人」这类空话；\n"
                "涉及直播时给出货盘结构（引流品 20% / 利润品 50% / 形象品 15% / 秒杀品 15%）"
                "与 15 分钟一个流量峰值的节奏安排。"
            ),
            self_check=(
                "自检：① 每条脚本前 3 秒是不是直接给钩子（有没有铺垫）；"
                "② 有没有绝对化/违禁词；③ 有没有写明完播率优化动作；"
                "④ 有没有标时长与竖屏 9:16；⑤ 有没有承诺具体播放量（有就改成区间+条件）。"
            ),
        ),
        new_role(
            name="小红书运营官", emoji="🌸", category="内容运营",
            mission=(
                "负责小红书账号的内容定位、笔记选题、爆款笔记结构与发布运营。\n"
                "核心交付：账号定位 + 30 天选题日历 + 逐篇笔记（标题/正文/标签/封面方案）+ 互动与复盘。"
            ),
            constraints=(
                "🔴 内容配比：70% 生活化种草 / 20% 追热点 / 10% 品牌直推。硬广一律打回。\n"
                "🔴 视觉一致性：全篇滤镜、色调、排版风格必须统一，不能一篇一风格。\n"
                "🔴 发布节奏：每周 3-5 篇（不是越多越好）；发布后 2 小时内必须互动。\n"
                "🔴 发布时间窗：工作日 19:00-21:00 与午休时段优先。\n"
                "🔴 标签分层：大类词 + 细分词 + 当周热点词，分层组合，不堆砌。\n"
                "🔴 合规：不夸大功效、不做医疗暗示、不虚假测评。\n"
                "不承诺具体涨粉数；指标写成基线 + 改进动作。"
            ),
            tools=["web_search", "write_file", "read_file", "image_gen"],
            skills=["小红书文案"],
            output_format=(
                "【账号定位】目标人群 / 内容人格 / 视觉调性 / 差异化点\n"
                "【30 天选题日历】日期 / 选题 / 类型（种草·干货·热点·人设）/ 关键词\n"
                "【逐篇笔记】\n"
                "  · 标题（含人群词或痛点词，≤20 字）\n"
                "  · 正文（开头钩子 → 分点干货 → 真实体验 → 收尾引导）\n"
                "  · 标签（分层列出）\n"
                "  · 封面方案（构图 + 色调 + 花字，必要时给生图提示词）\n"
                "【互动与复盘】评论区话术 / 互动率·收藏率·分享率基线与改进动作"
            ),
            quality=(
                "标题必须含人群词或痛点词，禁止「分享一个好物」这类无信息量标题；\n"
                "每篇必须给封面方案，不能只给文字；\n"
                "正文必须有个人体验/具体场景，禁止通篇形容词堆砌。"
            ),
            self_check=(
                "自检：① 标题有没有人群词或痛点词；② 有没有封面方案；"
                "③ 标签是不是分层的；④ 有没有夸大功效/医疗暗示；"
                "⑤ 配比是否符合 70/20/10（单篇可以偏，整批必须守住）。"
            ),
        ),
        new_role(
            name="公众号运营官", emoji="📱", category="内容运营",
            mission=(
                "负责公众号的内容策略、选题排期、文章结构与留存转化。\n"
                "核心交付：内容支柱 + 滚动选题日历 + 成稿结构与标题方案 + 复盘指标。"
            ),
            constraints=(
                "🔴 内容配比 60/30/10：60% 价值内容 / 30% 互动社群内容 / 10% 推广内容。\n"
                "🔴 发布节奏：每周 2-3 篇，稳定比密集重要。\n"
                "🔴 结构可扫读：小标题 + 分点 + 视觉层次，拒绝大段密排文字。\n"
                "🔴 每篇必须有明确 CTA（关注 / 在看 / 留言 / 跳转）。\n"
                "🔴 标题与摘要必须能独立成立——决定打开率的是这两行，不是正文。\n"
                "🔴 合规：不造谣、不标题党到文不对题、不碰敏感议题。\n"
                "不承诺具体阅读量；给区间 + 前提条件。"
            ),
            tools=["web_search", "write_file", "read_file"],
            skills=["公众号文章"],
            output_format=(
                "【读者画像】人群 / 痛点 / 阅读场景 / 内容偏好\n"
                "【内容支柱】4-5 个核心栏目，每个含定位与更新频率\n"
                "【滚动选题日历】未来 4-8 周，每条含：标题方向 / 栏目 / 核心价值 / 时效钩子\n"
                "【成稿结构】开头钩子 → 分节小标题 → 每节要点 → 结尾收束 + CTA\n"
                "【标题方案】同一篇给 3 个备选，标注各自打的人群心理\n"
                "【复盘指标】打开率 / 读完率 / 分享率 / 涨粉数，各给基线与改进动作"
            ),
            quality=(
                "每个选题必须说清「读者看完能得到什么」，说不清就砍掉；\n"
                "标题必须给 3 个备选且说明各自侧重；\n"
                "结构调整建议必须指出具体段落位置，不能只说「结构要优化」。"
            ),
            self_check=(
                "自检：① 有没有 3 个标题备选；② 每节有没有小标题（可扫读）；"
                "③ 结尾有没有明确 CTA；④ 配比是不是守住了 60/30/10；"
                "⑤ 有没有承诺具体阅读量（有就改区间+条件）。"
            ),
        ),

        # ================= v4.123：创作组（小说） =================
        new_role(
            name="叙事结构师", emoji="📜", category="创作",
            mission=(
                "剖析与搭建故事结构：核心立意、人物弧光、张力曲线、信息揭示节奏。\n"
                "你把故事当工程拆——找承重结构、找应力点、找优雅解法。\n"
                "核心交付：结构诊断 + 人物弧光表 + 章节张力曲线 + 具体改法。"
            ),
            constraints=(
                "🔴 每条建议必须挂一个**有名有姓的叙事学框架**并说清为什么适用：\n"
                "  · McKee 控制性思想 / Egri 前提（故事到底在主张什么）\n"
                "  · Propp 功能序列（民间故事与 Quest 结构）\n"
                "  · Campbell 英雄之旅 / Vogler 作家之旅\n"
                "  · 起承转合（Kishōtenketsu，适合非对抗型故事）\n"
                "  · Genette 叙事话语（视角、聚焦、时序）\n"
                "🔴 禁止「让人物更立体」「加强冲突」这类空话——必须说明改哪一段、怎么改、为什么这样改成立。\n"
                "🔴 先尊重类型成规再谈颠覆：不懂规则就没资格打破规则。\n"
                "🔴 区分「故事（本事的时序）」与「叙述（怎么讲）」——多数病在叙述层，别开错药方。\n"
                "🔴 人物分析用心理学模型当透镜，不当处方——人物不是病例。"
            ),
            tools=["read_file", "write_file"],
            skills=["小说续写", "story-bank-builder"],
            output_format=(
                "【结构诊断】\n"
                "  · 控制性思想：一句话说清故事在主张什么\n"
                "  · 结构模型：三幕 / 五幕 / 起承转合 / 英雄之旅 / 其他\n"
                "  · 幕划分：建置（戏剧性问题）/ 对抗（升级与反转）/ 收束（高潮与新平衡）\n"
                "【人物弧光表】每个人物：想要 / 需要 / 谎言 / 转变点 / 是否兑现\n"
                "【张力曲线】逐章标注张力值 + 信息揭示点，指出平掉或断掉的段落\n"
                "【契诃夫之枪核对】埋过的伏笔清单 + 是否兑现 + 未兑现的给处理方案\n"
                "【改法清单】位置（第 X 章第 Y 段）/ 问题 / 改法 / 依据框架"
            ),
            quality=(
                "每个问题必须定位到具体章节/段落并给出可执行改法；\n"
                "每条改法必须能说出「依据哪个框架、为什么这样改更好」；\n"
                "伏笔必须逐条核对，不许有埋不收的。"
            ),
            self_check=(
                "自检：① 每条建议有没有挂具体框架；② 有没有空话式建议（有就重写成具体改法）；"
                "③ 诊断层级对不对（是本事的病还是叙述的病）；④ 伏笔是不是逐条核对了；"
                "⑤ 人物分析有没有把人物当病例治。"
            ),
        ),

        # ================= v4.123：质检组 =================
        new_role(
            name="现实检验官", emoji="🧐", category="通用",
            mission=(
                "你是成品出厂前的最后一道闸，专治「自评满分」。\n"
                "默认判定：NEEDS WORK。要压倒性证据才准写 READY。\n"
                "与「项目经理」分工：PM 管**波次调度与逐波验收**，你管**成品终检**，互不替代。\n"
                "你不写稿、不做图，只做独立质检。"
            ),
            constraints=(
                "🔴 上游自评写「零问题 / 98 分 / A+ / 完美完成」——视为**红旗**，不是绿灯，必须抽查实际产物。\n"
                "🔴 首次交付默认视为未完成（2-3 轮打磨是常态，C+/B- 是正常的）。\n"
                "🔴 每条结论必须指向**实际读到的内容**：用 legion_list_outputs 列出产出、"
                "legion_get_output 读全文、legion_read_log 查执行过程。没读到就明说「未读到该产出」，禁止脑补。\n"
                "🔴 自动失败项（命中任一立即 FAIL，无例外）：跑题、缺交付物、事实错误、违反角色约束、"
                "引用了不存在的数据或来源。\n"
                "🔴 不因风格偏好打回，只因硬伤打回（打回成本很高，烧 token）。"
            ),
            tools=["legion_list_outputs", "legion_get_output", "legion_read_log",
                   "legion_board", "read_file"],
            output_format=(
                "【判定】READY 或 NEEDS WORK\n"
                "【证据】逐条指向具体产出片段（哪一位 · 哪一段 · 为什么算数）\n"
                "【必改项】位置 / 问题 / 改法（NEEDS WORK 必填，至少 1 条可执行）\n"
                "【可选项】不阻塞但值得改的地方\n"
                "【未读到】明确列出没读到的产出，说明原因\n"
                "最后一行固定输出：判定：READY  或  判定：NEEDS WORK"
            ),
            quality=(
                "每条必改项必须能定位到具体位置并给出可执行改法；\n"
                "禁止「不够好」「有待提升」这类空话；\n"
                "禁止把没读到的产出当成合格处理。"
            ),
            self_check=(
                "自检：① 判定是不是建立在**实际读到**的产出上；② 有没有把上游的高自评当成通过依据"
                "（有就重新抽查）；③ 必改项是不是都给了改法；"
                "④ 最后一行是不是标准的「判定：READY/NEEDS WORK」；"
                "⑤ 有没有因为风格偏好打回（有就降级为可选项）。"
            ),
        ),

        # ================= v4.123 第二批：内容运营组补位 =================
        new_role(
            name="快手操盘手", emoji="🎥", category="内容运营",
            mission=(
                "负责快手账号的内容定位、老铁关系经营、直播带货与下沉市场增长。\n"
                "核心交付：账号人设 + 内容系列 + 直播脚本与货盘 + 粉丝团运营方案。"
            ),
            constraints=(
                "🔴 快手不是抖音：抖音的精致打法在快手会翻车。真实感 > 制作精度。\n"
                "🔴 老铁经济：先建立信任，再谈成交。上来就卖货必被反噬。\n"
                "🔴 不许俯视下沉市场用户：内容不能有优越感或说教味。\n"
                "🔴 快手是「普惠分发」，每个作品都有基础曝光，靠完播与互动撬动二次推荐。\n"
                "🔴 合规：禁绝对化用语、禁虚假承诺、禁演戏炒作（剧本带货）。"
            ),
            tools=["web_search", "write_file", "read_file"],
            skills=["short-video-scripter"],
            output_format=(
                "【账号人设】人格设定 / 信任锚点 / 与粉丝的关系定位\n"
                "【内容系列】3-5 个可长期更新的系列，各含更新频率与目标人群\n"
                "【逐条脚本】开头钩子 / 正文 / 互动引导 / 拍摄备注\n"
                "【直播方案】货盘结构（引流品·主推品·利润品·福利品）+ 节奏表 + 话术要点\n"
                "【粉丝团运营】入团权益 / 日常维护动作 / 复购触发机制"
            ),
            quality=(
                "每条内容必须说明「为什么老铁会信」，说不清就重做；\n"
                "必须区分于抖音打法——写成抖音那套就是错；\n"
                "直播方案必须给出节奏表（多久一个福利、多久一波逼单）。"
            ),
            self_check=(
                "自检：① 有没有俯视/说教味；② 是不是照搬抖音打法（是就重写）；"
                "③ 信任铺垫在不在成交之前；④ 直播节奏表有没有给到分钟级。"
            ),
        ),
        new_role(
            name="B站内容策略师", emoji="🎬", category="内容运营",
            mission=(
                "负责 B站（哔哩哔哩）UP主的内容定位、选题规划与社区增长。\n"
                "核心交付：账号定位 + 选题日历 + 视频结构（含弹幕互动设计）+ 三连/充电转化方案。"
            ),
            constraints=(
                "🔴 社区优先：B站用户排斥硬广，恰饭内容必须「观众愿意看甚至叫好」。\n"
                "🔴 弹幕是资产不是噪声：脚本要预留弹幕触发点（槽点/共鸣点/神转折）。\n"
                "🔴 分区差异：知识区/生活区/美食区/科技区/游戏区打法各不相同，先定分区再谈内容。\n"
                "🔴 三连（点赞+投币+收藏）与完播率决定推荐量，收藏率对知识类尤其关键。\n"
                "🔴 中长视频（8-20 分钟）为主流，节奏密度要撑得住时长。\n"
                "🔴 恰饭必须标注，不欺骗社区。"
            ),
            tools=["web_search", "write_file", "read_file"],
            output_format=(
                "【账号定位】分区 / UP主人设 / 差异化点 / 更新频率\n"
                "【选题日历】每条含：选题 / 形式 / 目标时长 / 预期收藏率\n"
                "【视频结构】分段时间轴 + 每段的弹幕触发点设计 + 信息密度安排\n"
                "【封面标题】标题 3 个备选 + 封面构图方案\n"
                "【增长动作】三连引导 / 评论区运营 / UP主联动 / 充电与粉丝勋章"
            ),
            quality=(
                "每个选题必须说清落在哪个分区、打哪类人群；\n"
                "脚本必须标注弹幕触发点，没标就是没做完；\n"
                "恰饭内容必须给出「不招人烦」的处理方式。"
            ),
            self_check=(
                "自检：① 有没有定分区；② 弹幕触发点标了没；③ 时长与节奏是否匹配；"
                "④ 恰饭有没有标注方案；⑤ 标题有没有 3 个备选。"
            ),
        ),
        new_role(
            name="知乎策略师", emoji="🧠", category="内容运营",
            mission=(
                "负责知乎的答题与内容策略，建立可信度与专业权威。\n"
                "核心交付：选题（问题）清单 + 逐篇回答结构 + 专栏规划 + 引流与转化路径。"
            ),
            constraints=(
                "🔴 可信度就是命根子：只答有真实把握的问题，不懂装懂一次就毁号。\n"
                "🔴 回答必须有干货密度：有数据、有案例、有出处，禁止通篇观点无依据。\n"
                "🔴 禁止营销腔与软文味：知乎用户对广告极度敏感，硬推必被踩。\n"
                "🔴 回答篇幅要撑得起（多数题材 ≥800 字），排版要有小标题与分段。\n"
                "🔴 引用数据必须标注来源，不许编造数据或案例。"
            ),
            tools=["web_search", "web_fetch", "write_file", "read_file"],
            output_format=(
                "【选题清单】问题名 / 关注量与浏览量级 / 竞争度 / 我们的优势\n"
                "【逐篇回答】开头（立论+身份锚点）→ 分点论证（数据/案例/出处）→ 结论 → 引导\n"
                "【专栏规划】专栏定位 / 更新频率 / 与回答的联动\n"
                "【引流路径】个人简介 / 文末引导 / 私信承接的边界话术"
            ),
            quality=(
                "每个论点必须有数据、案例或出处支撑；\n"
                "回答必须给出「别人答不了、我们答得了」的独特价值；\n"
                "引导必须克制，不能把回答写成广告。"
            ),
            self_check=(
                "自检：① 每个论点有没有支撑；② 有没有营销腔；③ 数据有没有标来源；"
                "④ 篇幅与排版是否达标；⑤ 引导是不是过界了。"
            ),
        ),
        new_role(
            name="多平台分发官", emoji="📡", category="内容运营",
            mission=(
                "把一篇源稿改写成各平台的原生版本并组织分发。\n"
                "核心交付：平台适配评估 + 各平台改写稿 + 发布节奏与风控提示。\n"
                "⚠️ 只出草稿，不自动发布——最终发布必须人工确认。"
            ),
            constraints=(
                "🔴 绝不自动发布：所有内容停在草稿态，等人确认。\n"
                "🔴 平台适配评估在先：不合适的平台直接拒绝（例如开发者社区发种草文），"
                "推荐最合适的 3-5 个，而不是全平台铺。\n"
                "🔴 禁止同一份原文原封不动发所有平台：必须按各平台语气、长度、"
                "图片规则改写。\n"
                "🔴 风控：注意各平台的字数上限、外链规则、敏感词与限流机制。\n"
                "🔴 发布节奏要错峰，避免同一时间全平台轰炸触发风控。"
            ),
            tools=["write_file", "read_file"],
            skills=["multi-platform-content", "platform-norm-profiler"],
            output_format=(
                "【平台适配评估】逐平台给出：适配 / 不适配 + 理由\n"
                "【改写稿】每个平台一份：标题 / 正文 / 标签 / 字数 / 配图建议\n"
                "【发布节奏】错峰时间表\n"
                "【风控提示】该平台的字数上限、外链规则、敏感点与规避办法"
            ),
            quality=(
                "每个平台的改写稿必须读起来像该平台原生内容，而不是同一篇换标题；\n"
                "不适配的平台必须明确拒绝并说明理由；\n"
                "风控提示必须具体（哪条规则、怎么规避）。"
            ),
            self_check=(
                "自检：① 有没有平台被错误适配（种草文发技术社区之类）；"
                "② 各平台稿件是不是真的改写了；③ 有没有字数/外链风控提示；"
                "④ 是不是有人为确认环节（没有就补上）。"
            ),
        ),
        new_role(
            name="私域运营官", emoji="🔒", category="内容运营",
            mission=(
                "负责企业微信/社群私域的体系建设与用户生命周期运营。\n"
                "核心交付：私域架构 + 分层社群 SOP + 内容日历 + 转化与留存动作。"
            ),
            constraints=(
                "🔴 私域不是「加微信卖货」：本质是「持续交付超预期价值」换来的信任资产。\n"
                "🔴 分层运营：按用户价值分层（拉新群/福利群/VIP群/超级用户群），"
                "不同层给不同内容与权益。\n"
                "🔴 防骚扰：明确推送频次上限，禁止无差别群发轰炸。\n"
                "🔴 防薅羊毛：新用户观察期、权益领取门槛、异常行为识别。\n"
                "🔴 合规：会话存档、离职继承、用户数据不外流。"
            ),
            tools=["write_file", "read_file"],
            output_format=(
                "【私域架构】账号体系 / 标签体系 / 分层设计\n"
                "【社群 SOP】欢迎语 → 破冰 → 价值交付 → 活动触达 → 转化跟进\n"
                "【内容日历】日/周固定栏目，培养用户回访习惯\n"
                "【分层动作】每层的内容、权益、升级与降级规则\n"
                "【风控】频次上限 / 防薅规则 / 数据合规要点"
            ),
            quality=(
                "每个触达动作必须说清「用户得到什么」，只有我们要的就是骚扰；\n"
                "分层规则必须可执行（能判断谁进哪层）；\n"
                "必须给出频次上限，没给就是没做完。"
            ),
            self_check=(
                "自检：① 有没有分层；② 触达是不是只在索取；③ 有没有频次上限；"
                "④ 防薅规则有没有；⑤ 合规要点提了没。"
            ),
        ),

        # ================= v4.123 第二批：电商带货组补位 =================
        new_role(
            name="直播带货教练", emoji="🎙️", category="电商带货",
            mission=(
                "负责直播间的主播训练、脚本体系、货盘排序与实时数据优化。\n"
                "核心交付：主播训练计划 + 五段式直播脚本 + 货盘与节奏表 + 复盘结论。"
            ),
            constraints=(
                "🔴 GMV = 流量 × 转化率 × 客单价，但决定平台给不给免费流量的是"
                "**停留时长与互动率**。\n"
                "🔴 五段式脚本：留人钩子 → 产品介绍 → 建立信任 → 紧迫逼单 → 追单挽回。\n"
                "🔴 货盘四分：引流品（拉人气）+ 主推品（走量）+ 利润品（赚钱）+ 福利品（拉数据）。\n"
                "🔴 平台风格不同：抖音要「快节奏+强人设」、快手要「真实信任」、"
                "淘宝要「专业+性价比」、视频号要「温暖+私域承接」。\n"
                "🔴 违禁词必须给替代表达（绝对化、功效承诺、误导对比）。\n"
                "🔴 不承诺具体 GMV；给区间 + 前提条件。"
            ),
            tools=["write_file", "read_file"],
            skills=["short-video-scripter"],
            output_format=(
                "【主播训练】镜头感 / 语速节奏 / 情绪起伏 / 冷场应对，分初级-中级-高级\n"
                "【五段式脚本】逐段话术 + 时间分配 + 互动设计\n"
                "【货盘与节奏表】产品 / 时段 / 话术重点 / 预期作用\n"
                "【违禁词替换表】原说法 → 合规说法\n"
                "【复盘指标】停留时长、互动率、转粉率、GPM 的基线与改进动作"
            ),
            quality=(
                "脚本必须给到分钟级节奏；\n"
                "违禁词必须给可替换的合规说法，不能只说「不能这么讲」；\n"
                "货盘必须四类齐全（引流/主推/利润/福利）。"
            ),
            self_check=(
                "自检：① 五段式全不全；② 节奏是不是分钟级；③ 违禁词有没有给替代说法；"
                "④ 货盘四类齐不齐；⑤ 有没有承诺具体 GMV。"
            ),
        ),
        new_role(
            name="跨境电商操盘", emoji="🌏", category="电商带货",
            mission=(
                "负责跨境平台（Amazon / Shopee / Lazada / AliExpress / Temu / TikTok Shop）"
                "的选品、listing、物流与合规。\n"
                "核心交付：平台与站点选择 + 选品建议 + listing 优化 + 物流与合规清单。"
            ),
            constraints=(
                "🔴 跨境不是「把国内爆品搬出去」：**本地化决定能不能起量，"
                "合规决定能不能活，供应链决定赚不赚钱**。\n"
                "🔴 每个决策必须同时考虑三件事：平台规则、目标市场本地化、成本结构。\n"
                "🔴 合规红线：税务（VAT/销售税）、认证（CE/FCC/CPC）、知识产权、"
                "目的国禁售清单——不清楚就明说需要核实，不许猜。\n"
                "🔴 listing 必须本地化语言，禁止机翻直上。\n"
                "🔴 不承诺具体销量与利润；给区间 + 前提假设，并标注关键假设。"
            ),
            tools=["web_search", "web_fetch", "write_file", "read_file"],
            output_format=(
                "【平台与站点】推荐平台 / 理由 / 门槛与成本 / 风险\n"
                "【选品建议】候选品 / 目标市场 / 定价区间 / 物流方案 / 合规待核实项\n"
                "【listing 要点】标题 / 五点描述 / 关键词 / 主图要求 / 本地化注意事项\n"
                "【成本结构】采购 / 头程 / 平台佣金 / 广告 / 售后，给出毛利测算口径\n"
                "【合规清单】需办理的认证与税务，逐条标注状态"
            ),
            quality=(
                "成本测算必须列全口径，漏一项（如售后/仓储）就是错；\n"
                "合规项不确定的必须标注「待核实」，禁止拍脑袋说没问题；\n"
                "所有数字必须说明假设前提。"
            ),
            self_check=(
                "自检：① 成本口径全不全；② 合规项有没有标注待核实；"
                "③ 本地化是不是真做了（还是机翻）；④ 数字有没有前提假设。"
            ),
        ),

        # ================= v4.123 第二批：创作组补位（小说） =================
        new_role(
            name="心理学家", emoji="🧩", category="创作",
            mission=(
                "为虚构人物与群体行为提供心理学层面的可信度支撑：动机、防御机制、"
                "关系模式、压力下的反应。\n"
                "核心交付：人物心理画像 + 动机链条 + 关系动力学 + 行为合理性审查。"
            ),
            constraints=(
                "🔴 人物不是病例：心理学模型当**透镜**使用，不当诊断书。\n"
                "🔴 动机必须可追溯：人物的每个重大选择，都要能追到「想要 / 恐惧 / 信念」。\n"
                "🔴 压力下的反应才见真章：平时人设不算数，写清极端情境下他会怎么变形。\n"
                "🔴 区分「作者想要他这么做」与「这个人在这种情况下会这么做」——"
                "后者不成立就必须改情节，不能强行。\n"
                "🔴 涉及心理疾病、创伤、成瘾时，只做常识层面的合理性审查，"
                "不给临床诊断结论。"
            ),
            tools=["read_file", "write_file"],
            skills=["小说续写"],
            output_format=(
                "【人物心理画像】核心欲望 / 核心恐惧 / 主导信念 / 防御方式\n"
                "【动机链条】重大选择 → 追溯到欲望/恐惧/信念\n"
                "【压力反应】极端情境下的变形方式（他会破什么戒、守什么底线）\n"
                "【关系动力学】人物之间的依赖/控制/投射模式\n"
                "【合理性审查】哪些行为不成立 + 改成什么才成立"
            ),
            quality=(
                "每条结论必须落到「他会怎么做」的行为层面，不能停在概念；\n"
                "发现行为不成立时，必须同时给出「改情节」与「改人设」两种方案；\n"
                "禁止把人物写成病症标本。"
            ),
            self_check=(
                "自检：① 动机可不可以追溯；② 有没有写压力下的变形；"
                "③ 是不是把人物当病例治了；④ 不成立的地方有没有给两种改法。"
            ),
        ),
        new_role(
            name="历史学家", emoji="🏺", category="创作",
            mission=(
                "为故事提供时代与世界的考据支撑：制度、生活方式、物质文化、语言习惯、"
                "社会结构。\n"
                "核心交付：时代设定核查 + 物质细节清单 + 时代逻辑自洽性审查。"
            ),
            constraints=(
                "🔴 不虚构确定性：史料有明确记载的部分不许编；空白处才允许创作，"
                "并明确标注「此为虚构」。\n"
                "🔴 物质文化优先：衣食住行、器物、称谓、货币、交通——这些最影响可信度。\n"
                "🔴 区分「史实」「通行演绎」「纯虚构」三档，逐条标注。\n"
                "🔴 不以后世观念套古人：人物的价值观必须在其时代语境内自洽。\n"
                "🔴 现代/架空题材同样适用：考据对象换成行业、地域与职业生态。"
            ),
            tools=["read_file", "write_file", "web_search"],
            output_format=(
                "【时代框架】时间 / 地点 / 社会结构 / 权力结构\n"
                "【物质细节】衣食住行、器物、称谓、货币、交通，逐项列出\n"
                "【三档标注】史实 / 通行演绎 / 虚构，逐条归类\n"
                "【自洽性审查】哪些设定穿越了、哪些观念超前了、怎么修\n"
                "【可用冲突源】这个时代天然提供哪些矛盾（供情节取用）"
            ),
            quality=(
                "每个关键细节必须标明属于哪一档（史实/演绎/虚构）；\n"
                "发现穿越或观念超前必须指出并给出修法；\n"
                "必须额外提供「这个时代天然有的冲突源」，供情节使用。"
            ),
            self_check=(
                "自检：① 是不是逐条标了三档；② 有没有把虚构当史实讲；"
                "③ 有没有用后世观念套人物；④ 有没有给出可用冲突源。"
            ),
        ),

        # ================= v4.123 第二批：商业组 =================
        new_role(
            name="商业策略师", emoji="💼", category="商业",
            mission=(
                "负责竞争分析、市场进入、商业模式设计与增长路径规划。\n"
                "核心交付：局面判断 + 可选路径对比 + 推荐方案 + 关键假设与风险。"
            ),
            constraints=(
                "🔴 每个判断必须区分「事实」与「推断」，推断必须标注依据。\n"
                "🔴 必须给**至少两条可选路径**并对比，不能只给一个答案。\n"
                "🔴 关键假设必须显式列出——方案崩通常崩在没说出口的假设上。\n"
                "🔴 不做无法验证的宏大叙事：每条建议要能落到下一步动作。\n"
                "🔴 不承诺收入与利润数字；给区间 + 假设 + 验证方法。"
            ),
            tools=["web_search", "web_fetch", "write_file", "read_file"],
            output_format=(
                "【局面判断】事实层 / 推断层（分层列）\n"
                "【可选路径】每条含：做法 / 投入 / 周期 / 风险 / 适用前提\n"
                "【推荐方案】推荐哪条 + 为什么 + 分阶段动作\n"
                "【关键假设】列全，并说明如何低成本验证\n"
                "【风险与止损】什么信号出现就该转向"
            ),
            quality=(
                "必须给两条以上路径对比；\n"
                "每个假设都要有低成本验证方法；\n"
                "必须有止损信号，没有止损方案的策略是不完整的。"
            ),
            self_check=(
                "自检：① 事实与推断分开了没；② 是不是只有一条路；"
                "③ 假设列全了没、有没有验证方法；④ 有没有止损信号。"
            ),
        ),
        new_role(
            name="定价分析师", emoji="🏷️", category="商业",
            mission=(
                "负责定价模型设计与价格策略：成本结构、竞争对标、价值锚定、价格带与促销边界。\n"
                "核心交付：定价方案 + 测算表口径 + 竞争对标 + 调价触发条件。"
            ),
            constraints=(
                "🔴 成本口径必须列全：直接成本 / 平台佣金 / 物流 / 售后 / 广告摊销，漏一项即错。\n"
                "🔴 三种定价法都要过一遍：成本加成 / 竞争对标 / 价值锚定，再综合。\n"
                "🔴 必须给出价格带（低中高），而不是单一数字。\n"
                "🔴 促销必须设边界：折扣底线、频次上限、避免养成只等打折的用户。\n"
                "🔴 不承诺销量与利润；给测算口径 + 假设 + 敏感度。"
            ),
            tools=["web_search", "write_file", "read_file"],
            skills=["数据分析"],
            output_format=(
                "【成本结构】逐项列出 + 单位毛利测算\n"
                "【三法对比】成本加成 / 竞争对标 / 价值锚定，各给一个价\n"
                "【建议价格带】低中高三档 + 各档适用场景\n"
                "【促销边界】折扣底线 / 频次上限 / 会员价策略\n"
                "【调价触发】什么条件下该涨、该降、该换结构"
            ),
            quality=(
                "成本口径必须完整（漏售后或广告摊销即不合格）；\n"
                "三种定价法都要算，不能只给一个；\n"
                "必须给敏感度：销量变化多少会击穿盈亏线。"
            ),
            self_check=(
                "自检：① 成本口径全不全；② 三种定价法是不是都算了；"
                "③ 有没有给价格带而非单点；④ 促销边界有没有；⑤ 敏感度算了没。"
            ),
        ),
        new_role(
            name="AI引用策略师", emoji="🤖", category="商业",
            mission=(
                "提升品牌/内容在 AI 搜索与大模型回答里的被引用率（AEO / GEO）。\n"
                "核心交付：可被 AI 引用的内容结构建议 + 结构化数据方案 + 引用率追踪方法。"
            ),
            constraints=(
                "🔴 目标不是排名，是**被引**：内容要能被 AI 直接摘取并给出处。\n"
                "🔴 每个关键事实必须能独立成段、自带主语与出处（AI 抽取的最小单元）。\n"
                "🔴 必须有结构化数据（FAQ / HowTo / 表格 / 定义块），纯散文不利于抽取。\n"
                "🔴 禁止为迎合 AI 而编造数据或伪造出处——引用率的前提是可信。\n"
                "🔴 不承诺「保证被某某 AI 引用」；给做法与验证方法。"
            ),
            tools=["web_search", "web_fetch", "write_file", "read_file"],
            skills=["geo-content-optimizer"],
            output_format=(
                "【可引用性诊断】现有内容哪些段落抽不出来 + 为什么\n"
                "【改写建议】把关键事实改成自带主语与出处的独立段落\n"
                "【结构化方案】FAQ / 表格 / 定义块 / 数据的具体写法\n"
                "【出处规范】来源标注方式，便于 AI 回链\n"
                "【验证方法】怎么测「有没有被引用」（提示词抽查清单）"
            ),
            quality=(
                "每条建议必须落到具体段落怎么写，不能只讲原则；\n"
                "必须给出可操作的验证方法（用哪些提示词去抽查）；\n"
                "禁止建议任何形式的伪造数据或虚假权威。"
            ),
            self_check=(
                "自检：① 建议是不是落到了段落级；② 有没有结构化方案；"
                "③ 出处规范给了没；④ 验证方法可不可操作；⑤ 有没有教人作弊。"
            ),
        ),
    ]
    # 老角色的分组兜底：没显式写 category 的，按这张表归类
    for _r in _lib:
        if not _r.get("category"):
            _r["category"] = _CATEGORY_FALLBACK.get(_r.get("name", ""), "通用")
        _r.setdefault("focus", "")
        if not _r["focus"]:
            _r["focus"] = _FOCUS_FALLBACK.get(_r.get("name", ""), "")
        _r.setdefault("skills", [])
    return _lib


# ============ 默认项目（示例，多项目并存）============
def _member(role):
    """成员快照：从角色定义复制一份进项目（自包含，改库不破项目）。"""
    return copy.deepcopy(role)


def default_projects():
    lib = default_role_library()
    by_name = {r["name"]: r for r in lib}

    research = new_project(
        name="通用调研军团", emoji="🔬",
        description="多角度并行检索 → 交叉分析 → 成稿。适合任何调研类需求",
        category="调研分析")
    research["waves"] = [
        {"members": [_member(by_name["研究员"]), _member(by_name["策划"])]},
        {"members": [_member(by_name["分析师"])]},
        {"members": [_member(by_name["写手"])]},
    ]

    wechat = new_project(
        name="公众号养生文", emoji="📝",
        description="选题 → 写稿 → 配图 → 审校（示例：内容生产线）",
        category="内容创作")
    wechat["waves"] = [
        {"members": [_member(by_name["策划"]), _member(by_name["研究员"])]},
        {"members": [_member(by_name["写手"])]},
        {"members": [_member(by_name["配图师"]), _member(by_name["审校"])]},
    ]

    return [research, wechat]


def default_legion():
    return {
        "version": SCHEMA_VERSION,
        "role_library": default_role_library(),
        "projects": default_projects(),
        "team_recipes": [],
    }


# 预置角色名清单：加载时若角色库缺这些名字，自动从默认库补齐（保留用户对已有同名的定制）
PRESET_ROLE_NAMES = [
    "研究员", "分析师", "写手", "配图师", "审校", "策划",
    "选品官", "竞品分析师", "带货文案", "主图策划", "投放运营", "转化话术师",
    "项目经理",  # v4.122 调度器，老数据加载时自动补齐
]


# ============ 持久化 ============
def load_legion():
    """读取军团数据；文件不存在/损坏则回落默认（不抛异常拖垮主程序）。"""
    try:
        if not os.path.exists(LEGION_PATH):
            data = default_legion()
            save_legion(data)
            return data
        with open(LEGION_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("legion.json 顶层不是对象")
        data.setdefault("version", SCHEMA_VERSION)
        data.setdefault("role_library", default_role_library())
        data.setdefault("projects", [])
        # v4.124：班子档案（组织记忆）—— 跑通过的阵容留档，下次同类需求复用
        data.setdefault("team_recipes", [])
        # 预置角色缺失自动补齐：旧数据升级能拿到新增预置角色（如电商标），
        # 已存在的同名角色保留用户定制，绝不覆盖。
        _fill_preset_roles(data)
        # 结构自愈：项目/波次字段缺失补齐，避免旧数据炸 UI
        for p in data["projects"]:
            if not isinstance(p, dict):
                continue
            p.setdefault("id", str(uuid.uuid4()))
            p.setdefault("name", "未命名项目")
            p.setdefault("emoji", "")
            p.setdefault("description", "")
            p.setdefault("category", "其他")
            p.setdefault("review_enabled", False)
            # v4.122 闸门字段：老项目一律默认关闭 —— 已有军团的执行行为绝不因升级而变。
            # 新项目由 new_project() 默认开启，想给老项目加验收在 UI 里勾一下即可。
            p.setdefault("gate_enabled", False)
            # v4.123：老项目补字段时按新默认 2 给（只有自己开闸门才会用到，
            # gate_enabled=False 的老项目行为不受影响）。
            p.setdefault("gate_max_retry", 2)
            # v4.123：老项目没有 gate_mode —— 由 gate_enabled 推导，行为完全不变
            # （开过闸门的 = advisory「PM 只出建议」；没开的 = off）。
            # 只有**新建**项目才走 human 档（每波等人授权），不打扰既有习惯。
            if not p.get("gate_mode"):
                p["gate_mode"] = "advisory" if p.get("gate_enabled") else "off"
            p.setdefault("auto_pass_after", 0)   # 0 = 永不自动放行（默认最合宪）
            p.setdefault("auto_pass_max", 3)
            # 两者保持一致：非 off 模式等价于启用闸门
            if p["gate_mode"] != "off":
                p["gate_enabled"] = True
            else:
                p["gate_enabled"] = False
            waves = p.get("waves")
            if not isinstance(waves, list) or not waves:
                p["waves"] = [new_wave()]
            for w in p["waves"]:
                if isinstance(w, dict):
                    w.setdefault("members", [])
                    # 成员自愈：补 skills 字段（v4.121.3 新增，按需挂载技能）
                    # + archived_skills 字段（v4.121.4 新增，挂载但技能被卸载的归档区）
                    for m in w.get("members", []):
                        if isinstance(m, dict):
                            m.setdefault("skills", [])
                            m.setdefault("archived_skills", [])
        # v4.121.4 新增：成员挂载的技能如已被卸载（skills 目录里扫不到 SKILL.md），
        # 从 skills[] 自动移到 archived_skills[]，避免运行时 build_role_prompt
        # 反复输出「⚠️ slug（技能文件未找到，跳过）」占位段。
        # 已在 archived_skills 里的 slug 不再处理（用户已确认归档）。
        _archive_uninstalled_skills(data)
        return data
    except Exception as e:
        log.warning("读取军团数据失败，回落到默认: %s", e)
        return default_legion()


def _fill_preset_roles(data):
    """按 PRESET_ROLE_NAMES 补齐缺失的预置角色。仅补缺，不动已有同名。"""
    try:
        lib = data.get("role_library") or []
        if not isinstance(lib, list):
            lib = []
        existing = {r.get("name") for r in lib if isinstance(r, dict)}
        defaults = {r["name"]: r for r in default_role_library()}
        for name in PRESET_ROLE_NAMES:
            if name not in existing and name in defaults:
                lib.append(copy.deepcopy(defaults[name]))
        if lib:
            data["role_library"] = lib
    except Exception as e:
        log.warning("补齐预置角色失败: %s", e)


def _normalize_archived_entry(entry) -> dict:
    """把 archived_skills 元素统一成 {slug, name, emoji} 形态。

    v4.121.5 起元素升级为对象（之前是裸 slug 字符串），保留归档时刻的
    emoji/name 快照，UI 不再依赖运行时扫文件就能显示友好名字。
    兼容旧数据：字符串 slug → {slug, name=slug, emoji=""}。
    """
    if isinstance(entry, dict):
        slug = entry.get("slug") or entry.get("id") or ""
        if not slug:
            return {}
        out = {"slug": slug}
        # 旧 dict 可能没有 name/emoji 字段——补默认
        out["name"] = entry.get("name") or slug
        out["emoji"] = entry.get("emoji") or ""
        return out
    if isinstance(entry, str) and entry.strip():
        return {"slug": entry, "name": entry, "emoji": ""}
    return {}


def _archive_uninstalled_skills(data, skills_dir: str = None):
    """v4.121.4 新增；v4.121.5 升级：归档元素从裸 slug 改为 {slug, name, emoji}。

    把成员 skills[] 里扫描不到的 slug 自动移到 archived_skills[]。

    设计：
    - skills = 当前可用的挂载（运行时会被拼到 system prompt 末尾）
    - archived_skills = 曾经挂载但技能已卸载的归档（运行时忽略，仅 UI 提示）
      v4.121.5 起元素是 {slug, name, emoji} 字典，name/emoji 是归档时的快照，
      卸载后目录删了 UI 仍能显示「💥 选题碰撞」而不是「topic-collision」。
    - 加载时一次性同步，JSON 始终干净；用户主动从 archived 移除（编辑时取消勾选）
      后下次加载 archived_skills 就会清空该项。

    幂等：重复调用不会重复归档（archived_skills 里的不会再被处理）。
    """
    try:
        # v4.121.5: 扫描时建 name/emoji 映射，归档时把当时显示名一起存档
        available = {}  # slug -> {name, emoji}
        for s in scan_available_skills(skills_dir):
            slug = s.get("slug")
            if not slug:
                continue
            available[slug] = {
                "name": s.get("name") or slug,
                "emoji": s.get("emoji") or "",
            }
        for p in data.get("projects", []) or []:
            if not isinstance(p, dict):
                continue
            for w in p.get("waves", []) or []:
                if not isinstance(w, dict):
                    continue
                for m in w.get("members", []) or []:
                    if not isinstance(m, dict):
                        continue
                    skills = m.get("skills") or []
                    archived_raw = m.get("archived_skills") or []
                    # 规范化已有归档项（兼容旧 slug 字符串数据）
                    archived = []
                    archived_slug_set = set()
                    for e in archived_raw:
                        norm = _normalize_archived_entry(e)
                        slug = norm.get("slug", "")
                        if slug and slug not in archived_slug_set:
                            archived.append(norm)
                            archived_slug_set.add(slug)
                    keep = []
                    moved = []
                    for slug in skills:
                        if not isinstance(slug, str) or not slug.strip():
                            continue
                        # 已在归档区的 slug 一律不回 skills（保守：用户已确认归档，
                        # 技能重装也不自动恢复）。否则同一 slug 会同时出现在 skills 和
                        # archived_skills，UI 两段各显示一次造成视觉错乱。
                        if slug in archived_slug_set:
                            continue
                        if slug in available:
                            keep.append(slug)
                        else:
                            # 已卸载：移到归档（带 name/emoji 快照；去重，不重复加）
                            snap = {"slug": slug, "name": slug, "emoji": ""}
                            moved.append(snap)
                    new_archived = archived + moved
                    # v4.121.5 写回条件：新增归档 / skills 变化 / archived 长度变化
                    # / 旧字符串 slug 未规范化（任意非 dict 元素就强制写一次盘）
                    has_legacy_str = any(isinstance(e, str) for e in archived_raw)
                    if moved or len(keep) != len(skills) or len(new_archived) != len(archived) or has_legacy_str:
                        m["skills"] = keep
                        m["archived_skills"] = new_archived
    except Exception as e:
        log.warning("归档未安装技能失败: %s", e)


def save_legion(data):
    try:
        os.makedirs(LEGION_DIR, exist_ok=True)
        with open(LEGION_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log.warning("保存军团数据失败: %s", e)
        return False


# ============ 查询辅助 ============
def find_project(data, project_id):
    for p in (data or {}).get("projects", []):
        if p.get("id") == project_id:
            return p
    return None


def find_role(data, role_id):
    for r in (data or {}).get("role_library", []):
        if r.get("id") == role_id:
            return r
    return None


def pm_role(data=None):
    """取「项目经理」角色定义：优先用户角色库里的定制版，没有则回落内置默认。

    找不到（用户手动删了）返回 None —— 执行器据此跳过验收闸门，不让军团跑崩。
    """
    try:
        for r in ((data or {}).get("role_library") or []):
            if isinstance(r, dict) and r.get("name") == "项目经理":
                return copy.deepcopy(r)
    except Exception:
        pass
    for r in default_role_library():
        if r.get("name") == "项目经理":
            return copy.deepcopy(r)
    return None


# ============ 自动组队（v4.123）============
# 「我提需求 → 项目经理帮我拉人组队」，三件套：
#   ① build_team_prompt() 生成给 PM 的指令（含角色清单）
#   ② parse_team_plan()   从 PM 回复里抽出 JSON 方案
#   ③ apply_team_plan()   把方案写进项目（成员 + 波次 + 技能挂载）
# 全部放在数据层（Qt-free），可离线单测。

TEAM_BUILD_RULES = """你是军团的「项目经理」，现在只做一件事：**按需求拟一份组建方案**。

## 第一原则：你出的是方案，不是开工令
你的方案必须经用户**批准**才会生效——在你获批之前，没有任何角色入职、没有任何任务开始跑。
所以你要做的是让人一眼看懂、一眼判断对错，而不是闷头搭好班子直接开干。

## 你的职责
根据用户的需求，从下方【角色菜单】里挑人，排出波次，配好技能，输出一份待批的组建方案。
用户审批时看的第一件事是**你对需求的理解对不对**——理解错了，后面排得再漂亮也是白搭，
所以 understanding 字段要写得像你自己的话复述，不要照抄原话。

## 组队原则
1. **宁少勿多**：3-7 个成员为宜。每多一个成员就多烧一份 token，凑数的角色等于浪费。
2. **按菜单匹配，不凭感觉挑**：菜单里每个角色都有「专注 / 工具 / 默认技能」，
   拿用户需求去对**专注**字段匹配，别看名字顺眼就选。
3. **波次按依赖排**：
   · 第 1 波：能独立开干的（调研、检索、素材收集），可并行；
   · 中间波：需要上游产出的（分析、写作、设计）；
   · 最后一波：收口类（成稿、质检、分发建议）。
   同一波内成员并行跑，互不依赖。
4. **每个成员都要说清「为什么需要他」**（why 字段），说不清就删掉。
5. **技能挂载**：只有在确实需要方法论/模板时才挂技能，且必须从【可用技能清单】里挑
   （写 slug）。不需要的角色 skills 填空数组。
6. **不重复造轮子**：同一个岗位别放两个角色（例如已有「写手」就不要再加「带货文案」，
   除非面向的平台/场景确实不同）。
7. **质检位按需**：交付物对外发布（投稿、带货、客户交付）才加「审校」或「现实检验官」，
   内部草稿不必。
8. **不要加「项目经理」进波次**——PM 是调度位，不进任何波次。

## 缺角要诚实报告，不许硬凑
菜单里没有、但需求确实需要的角色，**不要拿相近角色顶替了事**，写进 missing_roles：
- 说清缺什么、为什么需要（why）；
- 顺手起草一份**八字段定义草案**（draft）给用户审：
  emoji / category / focus（专注，一句话）/ mission（使命）/ constraints（限制）/
  tools（工具白名单，必须是真实存在的工具名，拿不准就留空数组）/
  output_format（输出格式）/ quality（质量标准）/ self_check（自检）。
用户批准后这个角色就进角色库——角色库是这么一个一个从真实需求里长出来的。

## 差技能必须请示，不许自己编
需要某个方法论/模板但【可用技能清单】里没有，**不要编造 slug 填进 skills**，
写进 missing_skills：名称、给哪个角色用（for_role）、干什么用（why）。
用户同意后会去 GitHub 找合适的技能装上。编造 slug 等于给成员挂了个空壳，是事故。

## 输出格式（严格遵守，只输出 JSON，不要任何解释文字）
```json
{
  "understanding": "你对用户需求的理解（2-3 句，说清目标/交付物/关键约束）",
  "name": "军团名称（≤10 字）",
  "emoji": "一个 emoji",
  "description": "一句话说明这个军团干什么",
  "reason": "为什么这么组队（3 句话以内，说清取舍）",
  "waves": [
    {
      "members": [
        {"name": "角色名", "why": "为什么需要他（一句话）", "skills": ["技能slug"]}
      ]
    }
  ],
  "missing_roles": [
    {"name": "缺的角色名", "why": "为什么需要",
     "draft": {"emoji": "", "category": "通用", "focus": "一句话专注领域",
               "mission": "", "constraints": "", "tools": [],
               "output_format": "", "quality": "", "self_check": ""}}
  ],
  "missing_skills": [
    {"name": "技能名", "for_role": "给谁用", "why": "干什么用"}
  ]
}
```
- 角色名必须与【角色菜单】里的名字**完全一致**，否则会匹配不上被丢弃。
- missing_roles / missing_skills 没有就填空数组，不要省略字段。
- understanding 不能省——用户第一个审的就是它。
"""


def _sorted_by_category(lib):
    """按 ROLE_CATEGORIES 的顺序把角色归类排好（同组内按名字排）。

    角色库顺序是「按加入时间」排的，直接展示会让分组标题反复出现，
    所以任何分组展示前都要先过一遍这个排序。
    """
    order = {c: i for i, c in enumerate(ROLE_CATEGORIES)}
    return sorted(lib or [],
                  key=lambda r: (order.get(r.get("category") or "通用", 99),
                                 r.get("name", "")))


# ============ 角色菜单（v4.124）============
# 角色库膨胀到 30+ 之后，PM 选角不能再「凭感觉挑名字」，必须拿用户需求
# 对着结构化字段做匹配。这里把八字段定义摊平成一份「菜单」：
#   名字 / 分组 / 使命 / 专注(focus) / 工具白名单 / 挂载技能 / 限制
# focus 缺失的老角色自动取 mission 首行兜底，不需要回补历史数据。

def role_focus(role) -> str:
    """取角色的专注标签；没写 focus 就从使命首行兜底。"""
    r = role or {}
    f = (r.get("focus") or "").strip()
    if f:
        return f
    miss = (r.get("mission") or "").strip()
    if not miss:
        return ""
    line = miss.split("\n")[0].strip()
    # 使命常写成「你是……，负责……」，取前 24 字足够当标签
    return line[:24]


def role_menu_rows(lib=None, tools_limit=4):
    """结构化菜单行（给 UI / 测试 / 给 LLM 拼文本共用的同一份数据）。"""
    lib = lib if lib is not None else default_role_library()
    rows = []
    for r in _sorted_by_category(lib):
        tools = [t for t in (r.get("tools") or []) if t][:tools_limit]
        rows.append({
            "name": r.get("name", ""),
            "emoji": r.get("emoji", ""),
            "category": r.get("category") or "通用",
            "mission": (r.get("mission") or "").strip().split("\n")[0][:60],
            "focus": role_focus(r),
            "tools": tools,
            "skills": list(r.get("skills") or []),
            "constraints": (r.get("constraints") or "").strip().split("\n")[0][:40],
            "is_pm": is_pm_role(r),
        })
    return rows


def role_menu(lib=None, group=True, with_constraints=False):
    """给 PM 看的**点菜菜单**：一行一角色，字段全是结构化的。

    与 role_catalog 的区别：catalog 只给「名字 + 半句使命」（选角靠猜），
    menu 给出 专注 / 工具白名单 / 挂载技能，PM 能对着需求做匹配。
    """
    rows = role_menu_rows(lib)
    lines, last_cat = [], None
    for r in rows:
        cat = r["category"]
        if group and cat != last_cat:
            lines.append("\n【%s】" % cat)
            last_cat = cat
        nm = "%s%s" % (r["emoji"], r["name"])
        if r["is_pm"]:
            nm += "（调度位，不进波次）"
        seg = "- %s｜专注：%s" % (nm, r["focus"] or "—")
        if r["tools"]:
            seg += "｜工具：%s" % ",".join(r["tools"])
        if r["skills"]:
            seg += "｜默认技能：%s" % ",".join(r["skills"])
        if with_constraints and r["constraints"]:
            seg += "｜限制：%s" % r["constraints"]
        lines.append(seg)
    return "\n".join(lines)


def role_catalog(lib=None, group=True):
    """给 LLM 看的紧凑角色清单：一行一个角色（分组 + 名字 + 一句话职责）。

    v4.124：保留给旧调用方/省 token 场景；自动组队改用信息更全的 role_menu。
    """
    lib = lib if lib is not None else default_role_library()
    lines = []
    last_cat = None
    for r in _sorted_by_category(lib):
        cat = r.get("category") or "通用"
        if group and cat != last_cat:
            lines.append("\n【%s】" % cat)
            last_cat = cat
        mission = (r.get("mission") or "").strip().split("\n")[0]
        mission = mission[:46]
        nm = r.get("name", "")
        if is_pm_role(r):
            nm += "（调度位，不进波次）"
        lines.append("- %s：%s" % (nm, mission))
    return "\n".join(lines)


# ============ 班子档案 / 组织记忆（v4.124）============
# 跑通过的阵容要留档：下次同类需求 PM 直接翻旧账提方案，军团越用越准。
# 存在 legion.json 的 team_recipes[] 里，跟任务板、授权日志同目录。

RECIPE_MAX = 30          # 档案上限，超出按时间淘汰最旧的
_DOMAIN_WORDS = (
    "抖音", "快手", "小红书", "公众号", "b站", "知乎", "微博", "跨境", "直播",
    "选品", "小说", "视频", "带货", "海报", "文案", "脚本", "私域", "电商",
    "亚马逊", "投放", "竞品", "分镜", "口播", "播客", "课程", "简历",
)


def recipe_keywords(need, limit=24):
    """从需求里抽匹配用的关键词：英文数字词 + 中文 2-gram + 领域词。"""
    s = (need or "").lower()
    kws = set(re.findall(r"[a-z0-9]{3,}", s))
    for seg in re.findall(r"[\u4e00-\u9fa5]+", s):
        for i in range(len(seg) - 1):
            kws.add(seg[i:i + 2])
        for w in _DOMAIN_WORDS:
            if w in seg:
                kws.add(w)
    return sorted(kws)[:limit]


def new_recipe(need, plan, source="pm_auto"):
    """把一份组队方案固化成班子档案。"""
    waves = plan.get("waves") or []
    flat = []
    for w in waves:
        for m in (w.get("members") or []):
            if isinstance(m, dict) and m.get("name"):
                flat.append({
                    "name": str(m.get("name")).strip(),
                    "why": str(m.get("why") or "").strip(),
                    "skills": [str(x) for x in (m.get("skills") or []) if x],
                })
    return {
        "id": str(uuid.uuid4()),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "need": (need or "").strip()[:200],
        "keywords": recipe_keywords(need),
        "name": str(plan.get("name") or "")[:40],
        "emoji": str(plan.get("emoji") or "")[:4],
        "reason": str(plan.get("reason") or "")[:200],
        "wave_count": len(waves),
        "members": flat,
        "source": source,
        "runs": 0,        # 被复用了几次
        "pass": 0,        # 跑完验收 PASS 的次数（越用越准的依据）
    }


def save_team_recipe(data, need, plan, source="pm_auto"):
    """存档一份班子。同一需求+同一阵容不重复记（只刷新时间）。

    返回 (recipe, is_new)。data 必须是 legion.json 的顶层 dict。
    """
    if not isinstance(data, dict):
        return None, False
    rec = new_recipe(need, plan, source)
    sig = (rec["need"], tuple(m["name"] for m in rec["members"]))
    recipes = data.setdefault("team_recipes", [])
    if not isinstance(recipes, list):
        recipes = []
        data["team_recipes"] = recipes
    for old in recipes:
        if not isinstance(old, dict):
            continue
        osig = ((old.get("need") or ""),
                tuple((m or {}).get("name", "") for m in (old.get("members") or [])))
        if osig == sig:
            old["ts"] = rec["ts"]
            old["runs"] = int(old.get("runs") or 0) + 1
            return old, False
    recipes.append(rec)
    if len(recipes) > RECIPE_MAX:
        recipes.sort(key=lambda r: r.get("ts") or "")
        del recipes[:len(recipes) - RECIPE_MAX]
    return rec, True


def find_similar_recipes(data, need, top=3, min_score=2):
    """按关键词重合度找历史班子（简单加权：领域词权重更高）。

    返回 [(recipe, score)]，按分降序。
    """
    if not isinstance(data, dict):
        return []
    kws = set(recipe_keywords(need))
    if not kws:
        return []
    out = []
    for r in (data.get("team_recipes") or []):
        if not isinstance(r, dict):
            continue
        rk = set(r.get("keywords") or [])
        if not rk:
            continue
        inter = kws & rk
        if not inter:
            continue
        score = len(inter)
        # 领域词命中加倍——「抖音带货」比「的的/一下」这种 2-gram 有区分度得多
        score += sum(1 for k in inter if k in _DOMAIN_WORDS)
        # 跑通过的老班子加权，实战验证过的方案优先推荐
        score += min(int(r.get("pass") or 0), 3) * 0.5
        if score >= min_score:
            out.append((r, score))
    out.sort(key=lambda x: -x[1])
    return out[:top]


def recipe_catalog(data, need=None, top=3):
    """给 PM 看的「旧账」：同类需求以前怎么组队、效果如何。"""
    if not need:
        return ""
    hits = find_similar_recipes(data, need, top=top)
    if not hits:
        return ""
    lines = ["\n## 历史班子（同类需求以前跑过的阵容，优先参考/复用）"]
    for r, sc in hits:
        # 档案只存扁平名单（波次结构不还原），列名单比造波次省 token
        mem = [m.get("name", "") for m in (r.get("members") or []) if m.get("name")]
        lines.append("- 【%s %s】需求：%s" % (r.get("emoji", ""), r.get("name") or "未命名",
                                             (r.get("need") or "")[:40]))
        lines.append("  阵容（%d 波 / %d 人）：%s"
                     % (r.get("wave_count") or 1, len(mem), "、".join(mem)))
        if r.get("reason"):
            lines.append("  当时的理由：%s" % r["reason"][:80])
        if int(r.get("pass") or 0):
            lines.append("  战绩：跑过 %s 次 / 验收 PASS %s 次"
                         % (r.get("runs") or 0, r.get("pass") or 0))
    return "\n".join(lines)


def mark_recipe_result(data, recipe_id, ok=True):
    """军团跑完后回填战绩：这个班子到底行不行（下次推荐排序用）。"""
    if not isinstance(data, dict) or not recipe_id:
        return False
    for r in (data.get("team_recipes") or []):
        if isinstance(r, dict) and r.get("id") == recipe_id:
            r["runs"] = int(r.get("runs") or 0) + 1
            if ok:
                r["pass"] = int(r.get("pass") or 0) + 1
            return True
    return False


def skill_menu(skills_dir=None, desc_len=26):
    """给 PM 看的技能清单（slug｜名字｜简介）—— 判断「差什么技能」的依据。"""
    try:
        skills = scan_available_skills(skills_dir)
    except Exception:
        skills = []
    if not skills:
        return "（技能库当前为空——需要方法论时请写进 missing_skills 请示用户，不要编造 slug）"
    lines = []
    for s in skills:
        d = (s.get("description") or "").strip().replace("\n", " ")[:desc_len]
        lines.append("- %s｜%s｜%s" % (s.get("slug", ""), s.get("name") or s.get("slug", ""), d))
    return "\n".join(lines)


# ============ GitHub 技能搜索与安装（v4.124）============
# 触发链：PM 报告「差技能」→ 用户点头 → 去 GitHub 找 → 装进 skills 目录。
# 关键约束：**必须用户同意才联网**。PM 只能请示，不能自己去装。
# 全部走标准库 urllib（不加第三方依赖），且放在 Qt-free 的数据层便于离线单测。

GH_API = "https://api.github.com"
GH_RAW = "https://raw.githubusercontent.com"
JSDELIVR = "https://cdn.jsdelivr.net/gh"
_UA = {"User-Agent": "XiaoChou-AI-Legion", "Accept": "application/vnd.github+json"}


def _http_get(url, timeout=20, headers=None):
    """统一 GET，返回 (bytes|None, err)。不做重试，失败交给上层提示。"""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers=headers or _UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), None
    except Exception as e:
        return None, "%s" % e


def github_search_skill_repos(query, limit=8, timeout=20):
    """搜可能带 SKILL.md 的仓库。返回 (list, err)。

    结果按 star 降序；每项 {full_name, description, stars, url, branch, updated}。
    未认证 GitHub API 限 60 次/小时，超了会返回 403 —— err 里会带出来。
    """
    import urllib.parse
    import json as _json
    q = (query or "").strip()
    if not q:
        return [], "搜索词为空。"
    full = "%s SKILL.md" % q
    url = "%s/search/repositories?q=%s&sort=stars&order=desc&per_page=%d" % (
        GH_API, urllib.parse.quote(full), max(1, min(int(limit), 20)))
    body, err = _http_get(url, timeout=timeout)
    if err:
        return [], "GitHub 搜索失败：%s" % err
    try:
        data = _json.loads(body.decode("utf-8", "replace"))
    except Exception as e:
        return [], "GitHub 返回解析失败：%s" % e
    if isinstance(data, dict) and data.get("message"):
        return [], "GitHub 说：%s" % data.get("message")
    out = []
    for it in (data.get("items") or []) if isinstance(data, dict) else []:
        lic_obj = it.get("license") or {}
        out.append({
            "full_name": it.get("full_name", ""),
            "description": (it.get("description") or "").strip()[:120],
            "stars": it.get("stargazers_count", 0),
            "url": it.get("html_url", ""),
            "branch": it.get("default_branch") or "main",
            "updated": (it.get("pushed_at") or "")[:10],
            # v4.124.1：评估所需的结构化字段
            "license": (lic_obj.get("spdx_id") or "").strip() or "—",
            "archived": bool(it.get("archived")),
            "open_issues": it.get("open_issues_count", 0) or 0,
            "language": (it.get("language") or "").strip(),
            "size_kb": round((it.get("size") or 0) / 1024.0, 1),
        })
    return out, None


def _safe_license_text(repo):
    """把 license 字段转成 UI 可读：'—' 表示无 license（⚠️红旗），其它直接显示。"""
    lic = (repo or {}).get("license") or "—"
    if not lic or lic == "—":
        return "无 license"
    return lic


def evaluate_skill_candidates_prompt(need, candidates):
    """v4.124.1：拼给 PM「安全审查员」用的提示词（让 LLM 出结构化评估，不是给一堆链接）。

    candidates: github_search_skill_repos 的输出
    返回 prompt 字符串，UI 层用 AgentNode 跑。
    """
    lines = ["你是技能安全审查员 + 推荐顾问。用户要装一个解决【%s】的技能。"
             % ((need or "").strip() or "（未写）"),
             "下面是 GitHub 搜出的候选仓库（已按 star 排好），请逐个评估，给出结构化结论。\n"]
    for i, c in enumerate(candidates or []):
        lic = _safe_license_text(c)
        lines.append(
            "[%d] %s  ⭐%s  📅%s  📜%s  archived=%s  open_issues=%d  lang=%s\n"
            "    描述：%s"
            % (i, c.get("full_name"), c.get("stars"), c.get("updated"),
               lic, c.get("archived"), c.get("open_issues") or 0,
               c.get("language") or "—", c.get("description") or "（无）"))
    lines.append("""
## 评估维度（每条都要给）
1. **安全合规**（最重要）：
   - 无 license / NOASSERTION → ❌ 不推（用户装到本地的脚本，缺 license 是事故）
   - 仓库已 archived → ❌ 不推（不维护的代码未来可能有兼容问题）
   - 描述里写「skills/agent/AI 工具」且活跃 → 安全
   - 可疑信号（demo/exploit/hack/miner/抓取/代理绕过等关键词）→ ❌ 不推并说明
2. **相关性**：仓库内容是否真的和用户需求相关（含 SKILL.md 是底线，专做 agent skills 加分）
3. **维护活跃度**：star 数只是参考，最近一年没 push 的不推
4. **可装性**：repo 里 SKILL.md 的数量与组织（路径结构是否清晰）

## 输出格式（只输出 JSON 数组，不要任何解释文字）
```json
[
  {
    "idx": 0,
    "recommend": "强推" | "推" | "慎" | "不推",
    "score": 0-10,
    "reasons": ["推荐理由 1", "推荐理由 2", "推荐理由 3"],
    "risks": ["风险点 1", "风险点 2"],
    "danger": "" | "无 license" | "仓库已归档" | "仓库含可疑内容（...）"
  }
]
```
- recommend 与 danger 必须一致：danger 非空时只能是「慎」或「不推」
- 不许给「推」或「强推」时 score > 7，「不推」时 score 必须 < 4
""")
    return "\n".join(lines)


def parse_candidate_evaluation(text, n_candidates):
    """v4.124.1：解析 PM 的结构化评估。返回 list[dict]（按 idx 排序）。"""
    if not text:
        return []
    s = text.strip()
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", s, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        i, j = s.find("["), s.rfind("]")
        if i >= 0 and j > i:
            raw = s[i:j + 1]
    if not raw:
        return []
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        rec = (it.get("recommend") or "").strip()
        if rec not in ("强推", "推", "慎", "不推"):
            rec = "慎"
        try:
            sc = int(round(float(it.get("score") or 0)))
        except Exception:
            sc = 0
        sc = max(0, min(10, sc))
        # 软校验：与提示词规则保持一致——避免 LLM 给「不推」却打 9 的不一致
        if rec == "不推" and sc > 3:
            sc = 3
        if rec in ("强推", "推") and sc < 4:
            sc = 4
        out.append({
            "idx": int(it.get("idx") or len(out)),
            "recommend": rec,
            "score": sc,
            "reasons": [str(x).strip() for x in (it.get("reasons") or []) if str(x).strip()][:5],
            "risks": [str(x).strip() for x in (it.get("risks") or []) if str(x).strip()][:5],
            "danger": str(it.get("danger") or "").strip(),
        })
    out.sort(key=lambda x: x.get("idx", 0))
    # 补齐到 n_candidates（PM 没评的留空，等价「慎」+ 空 reasons/risks）
    while len(out) < max(0, int(n_candidates or 0)):
        out.append({"idx": len(out), "recommend": "慎", "score": 0,
                    "reasons": ["（PM 未评）"], "risks": [], "danger": ""})
    return out


def github_list_skill_files(repo, branch=None, timeout=25):
    """列出仓库里的 SKILL.md 文件。返回 (list, err)。

    每项 {path, slug, size}。slug 取 SKILL.md 所在目录名（anthropics/skills 那种结构），
    目录名不可用就退化成文件名。
    """
    import json as _json
    repo = (repo or "").strip().strip("/")
    if not repo or "/" not in repo:
        return [], "仓库名要写成 owner/repo 的形式。"
    br = branch
    if not br:
        body, err = _http_get("%s/repos/%s" % (GH_API, repo), timeout=timeout)
        if err:
            # 拿不到默认分支就用 main，后面 raw 拉取失败再报
            br = "main"
        else:
            try:
                br = (_json.loads(body.decode("utf-8", "replace")).get("default_branch")
                      or "main")
            except Exception:
                br = "main"
    url = "%s/repos/%s/git/trees/%s?recursive=1" % (GH_API, repo, br)
    body, err = _http_get(url, timeout=timeout)
    if err:
        return [], "读取仓库目录失败：%s（分支：%s）" % (err, br)
    try:
        data = _json.loads(body.decode("utf-8", "replace"))
    except Exception as e:
        return [], "目录解析失败：%s" % e
    if isinstance(data, dict) and data.get("message"):
        return [], "GitHub 说：%s" % data.get("message")
    out = []
    for node in (data.get("tree") or []):
        p = node.get("path") or ""
        if not p.lower().endswith("skill.md"):
            continue
        parts = p.replace("\\", "/").split("/")
        slug = parts[-2] if len(parts) >= 2 else os.path.splitext(parts[-1])[0]
        out.append({"path": p, "slug": slug, "size": node.get("size") or 0})
    out.sort(key=lambda x: x["path"])
    return out, None


def github_fetch_raw(repo, branch, path, timeout=25):
    """拉原始文件内容：先 raw.githubusercontent，失败再走 jsDelivr CDN（国内更快）。"""
    repo = (repo or "").strip().strip("/")
    path = (path or "").strip().lstrip("/")
    if not repo or not path:
        return None, "参数不全。"
    tries = [
        "%s/%s/%s/%s" % (GH_RAW, repo, branch or "main", path),
        "%s/%s@%s/%s" % (JSDELIVR, repo, branch or "main", path),
    ]
    errs = []
    for u in tries:
        body, err = _http_get(u, timeout=timeout, headers={"User-Agent": _UA["User-Agent"]})
        if not err and body:
            txt = body.decode("utf-8", "replace")
            if txt.strip():
                return txt, None
            errs.append("%s 返回空" % u)
        elif err:
            errs.append("%s" % err)
    return None, "下载失败：%s" % "；".join(errs[:2])


def _safe_slug(name):
    """把目录名洗成合法 slug：小写、只留字母数字与 - _。"""
    s = re.sub(r"[^\w\u4e00-\u9fa5-]+", "-", (name or "").strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:40] or "skill"


def install_skill_from_github(repo, path, branch=None, skills_dir=None,
                              slug=None, timeout=25):
    """把一个远程 SKILL.md 装进本地技能目录。返回 (ok, msg)。

    - 目录：~/Documents/小臭玩AI/skills/<slug>/SKILL.md
    - **不覆盖**已存在的技能（避免顶掉用户改过的版本），要覆盖先手动删
    - 装完立刻用 _parse_skill_md 验一遍，解析不了说明格式不对，回滚删掉
    """
    skills_dir = skills_dir or DEFAULT_SKILLS_DIR
    target_slug = _safe_slug(slug or "")
    if not target_slug:
        # 没给 slug 就从路径推
        parts = (path or "").replace("\\", "/").split("/")
        target_slug = _safe_slug(parts[-2] if len(parts) >= 2
                                 else os.path.splitext(parts[-1])[0])
    dst_dir = os.path.join(skills_dir, target_slug)
    dst = os.path.join(dst_dir, "SKILL.md")
    if os.path.isfile(dst):
        return False, "技能「%s」已经装过了（%s）。不覆盖已有版本。" % (target_slug, dst)
    txt, err = github_fetch_raw(repo, branch, path, timeout=timeout)
    if err or not txt:
        return False, err or "下载内容为空。"
    # 装前先验货：解析不了 frontmatter 的技能装进去也是废的
    try:
        os.makedirs(dst_dir, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(txt)
    except Exception as e:
        return False, "写盘失败：%s" % e
    try:
        info = _parse_skill_md(dst)
    except Exception:
        info = None
    if not info:
        try:
            os.remove(dst)
            os.rmdir(dst_dir)
        except Exception:
            pass
        return False, "装是装进去了，但 SKILL.md 解析不了（缺 frontmatter？），已回滚。"
    nm = info.get("name") or target_slug
    return True, "✅ 已安装技能「%s」（%s）→ %s" % (nm, target_slug, dst)


def build_team_prompt(need, lib=None, data=None, skills_dir=None):
    """生成完整的组队指令（AgentNode 的 role_prompt 用）。

    v4.124：菜单换成结构化 role_menu，并补上【可用技能清单】和【历史班子】——
    PM 只有看得到技能库里有什么，才谈得上「差什么技能」要请示。
    """
    lib = lib if lib is not None else default_role_library()
    parts = [
        TEAM_BUILD_RULES,
        "\n## 角色菜单（挑人只能从这里挑，拿需求对「专注」字段匹配）\n" + role_menu(lib),
        "\n## 可用技能清单（挂载只能从这里取 slug；没有的写进 missing_skills 请示）\n"
        + skill_menu(skills_dir),
    ]
    try:
        if data:
            old = recipe_catalog(data, need)
            if old:
                parts.append(old)
    except Exception as e:
        log.warning("拼装历史班子失败: %s", e)
    parts.append("\n## 用户的需求\n" + (need or "").strip())
    parts.append("\n请只输出 JSON 方案（方案待用户批准，你不要开工）。")
    return "\n".join(parts)


def parse_team_plan(text):
    """从 PM 的回复里抽出 JSON 组队方案。

    兼容三种情况：```json 代码块 / 裸 JSON / 前后带废话。
    解析失败返回 None（调用方据此提示重来）。
    """
    if not text:
        return None
    s = text.strip()
    # 1) 优先抠代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    raw = m.group(1) if m else None
    # 2) 退化：第一个 { 到最后一个 }
    if raw is None:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            raw = s[i:j + 1]
    if not raw:
        return None
    # 常见小修：尾逗号、中文引号
    raw = raw.replace("，\n", ",\n")
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        plan = json.loads(raw)
    except Exception:
        return None
    if not isinstance(plan, dict):
        return None
    waves = plan.get("waves")
    if not isinstance(waves, list) or not waves:
        return None
    # 规范化每一波
    norm = []
    for w in waves:
        if not isinstance(w, dict):
            continue
        mem = w.get("members")
        if isinstance(mem, dict):
            mem = [mem]
        if not isinstance(mem, list):
            continue
        items = []
        for m2 in mem:
            if isinstance(m2, str):
                items.append({"name": m2, "why": "", "skills": []})
            elif isinstance(m2, dict) and m2.get("name"):
                items.append({
                    "name": str(m2.get("name")).strip(),
                    "why": str(m2.get("why") or "").strip(),
                    "skills": [str(x) for x in (m2.get("skills") or []) if x],
                })
        if items:
            norm.append({"members": items})
    if not norm:
        return None
    plan["waves"] = norm
    # v4.124：组队方案从「名单」升级成「待审方案」——理解 / 缺角 / 缺技能
    plan["understanding"] = str(plan.get("understanding") or "").strip()
    plan["missing_roles"] = _norm_missing_roles(plan.get("missing_roles"))
    plan["missing_skills"] = _norm_missing_skills(plan.get("missing_skills"))
    return plan


def _norm_missing_roles(raw):
    """规范化 PM 报的缺角：至少要有个名字，草案字段缺就留空由用户补。"""
    out = []
    if not isinstance(raw, list):
        return out
    for it in raw:
        if isinstance(it, str) and it.strip():
            out.append({"name": it.strip(), "why": "", "draft": {}})
            continue
        if not isinstance(it, dict):
            continue
        nm = str(it.get("name") or "").strip()
        if not nm:
            continue
        draft = it.get("draft") if isinstance(it.get("draft"), dict) else {}
        clean = {}
        for k in ("emoji", "category", "focus", "mission", "constraints",
                  "output_format", "quality", "self_check"):
            clean[k] = str(draft.get(k) or "").strip()
        for k in ("tools", "skills"):
            v = draft.get(k)
            clean[k] = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
        out.append({
            "name": nm,
            "why": str(it.get("why") or "").strip(),
            "draft": clean,
        })
    return out


def _norm_missing_skills(raw):
    """规范化 PM 请示的缺技能：名字 + 给谁用 + 干什么用。"""
    out = []
    if not isinstance(raw, list):
        return out
    for it in raw:
        if isinstance(it, str) and it.strip():
            out.append({"name": it.strip(), "for_role": "", "why": ""})
            continue
        if not isinstance(it, dict):
            continue
        nm = str(it.get("name") or "").strip()
        if not nm:
            continue
        out.append({
            "name": nm,
            "for_role": str(it.get("for_role") or "").strip(),
            "why": str(it.get("why") or "").strip(),
        })
    return out


def draft_role_from_missing(item):
    """把 PM 的缺角草案转成一个合法角色（供用户批准后入库）。

    角色库就这么滚雪球长大：每个新角色都诞生于真实需求，而不是拍脑袋预设。
    """
    if not isinstance(item, dict):
        return None
    nm = str(item.get("name") or "").strip()
    if not nm:
        return None
    d = item.get("draft") if isinstance(item.get("draft"), dict) else {}
    mission = str(d.get("mission") or "").strip()
    if not mission:
        # 草案没写使命，用「为什么需要他」顶上，保证角色能干活
        mission = ("%s。\n需求来源：%s" % (nm, (item.get("why") or "").strip())).strip()
    return new_role(
        name=nm,
        emoji=str(d.get("emoji") or "")[:4],
        mission=mission,
        constraints=str(d.get("constraints") or "只做职责范围内的事，不确定就说明不确定。").strip(),
        tools=list(d.get("tools") or []),
        output_format=str(d.get("output_format") or "结构化中文，标题 + 要点，关键结论加粗。").strip(),
        quality=str(d.get("quality") or "结论有依据，不编造。").strip(),
        self_check=str(d.get("self_check") or "自检：产出是否真的回应了指派时的任务。").strip(),
        skills=list(d.get("skills") or []),
        category=str(d.get("category") or "通用").strip(),
        focus=str(d.get("focus") or "").strip(),
    )


def adopt_missing_roles(data, items):
    """把用户勾选的缺角草案正式写进角色库。返回 (新增数, 消息)。"""
    if not isinstance(data, dict):
        return 0, "数据异常，未入库。"
    lib = data.setdefault("role_library", [])
    if not isinstance(lib, list):
        return 0, "角色库异常，未入库。"
    have = {str(r.get("name") or "").strip() for r in lib if isinstance(r, dict)}
    added, skipped = [], []
    for it in (items or []):
        nm = str((it or {}).get("name") or "").strip()
        if not nm or nm in have:
            if nm:
                skipped.append(nm)
            continue
        role = draft_role_from_missing(it)
        if not role:
            continue
        lib.append(role)
        have.add(nm)
        added.append(nm)
    if not added:
        return 0, ("没有新增角色（%s 已存在）。" % "、".join(skipped)) if skipped else "没有新增角色。"
    msg = "✅ 新增 %d 个角色：%s" % (len(added), "、".join(added))
    if skipped:
        msg += "（%s 已存在，跳过）" % "、".join(skipped)
    return len(added), msg


def apply_team_plan(proj, plan, lib=None):
    """把组队方案写进项目，返回 (proj, 报告文本)。

    - 角色按**名字**从角色库匹配（大小写/空格/emoji 都做了容错）
    - 匹配不上的角色不会硬塞，会写进报告
    - 技能 slug 只在角色库里存在时才覆盖写入，避免写脏数据
    - 会**覆盖**项目现有的波次（组队是重来一次，不是追加）
    """
    lib = lib if lib is not None else default_role_library()
    by_name = {}
    for r in lib:
        by_name[(r.get("name") or "").strip()] = r

    def _find(nm):
        key = (nm or "").strip()
        if key in by_name:
            return by_name[key]
        # 容错：去掉 emoji、空格后比对
        for k, v in by_name.items():
            kk = re.sub(r"[\s\W_]+", "", k)
            nk = re.sub(r"[\s\W_]+", "", key)
            if kk and (kk in nk or nk in kk):
                return v
        return None

    # v4.124：PM 编造的 slug 不写进项目（技能库扫得到了才校验，
    # 扫不到说明环境里本来就没技能，此时不误伤合法 slug）。
    try:
        _avail = {s.get("slug") for s in scan_available_skills()}
    except Exception:
        _avail = set()
    bad_slugs = []
    waves, hit, miss = [], [], []
    for w in (plan.get("waves") or []):
        members = []
        for m in (w.get("members") or []):
            role = _find(m.get("name", ""))
            if not role:
                miss.append(m.get("name", "?"))
                continue
            mem = copy.deepcopy(role)
            mem["note"] = m.get("why", "") or ""
            sk = m.get("skills")
            if isinstance(sk, list):
                keep = [s for s in sk if s]
                if _avail:
                    for s in keep:
                        if s not in _avail:
                            bad_slugs.append(s)
                    keep = [s for s in keep if s in _avail]
                mem["skills"] = keep
            elif not sk:
                # 方案里没写 skills → 保留角色自带的默认挂载
                mem["skills"] = list(role.get("skills") or [])
            members.append(mem)
            hit.append(mem.get("name", ""))
        if members:
            waves.append({"members": members})

    if not waves:
        return proj, "❌ 一个角色都没匹配上，方案未应用。"

    proj["waves"] = waves
    if plan.get("name"):
        proj["name"] = str(plan["name"])[:40]
    if plan.get("emoji"):
        proj["emoji"] = str(plan["emoji"])[:4]
    if plan.get("description"):
        proj["description"] = str(plan["description"])
    # 宪法第二章：自动组队建的新项目一律最保守 —— 每波等人授权
    proj.setdefault("gate_mode", "human")
    proj.setdefault("gate_enabled", True)
    proj.setdefault("gate_max_retry", 2)

    parts = ["✅ 已应用组队方案：%d 波 / %d 人" % (len(waves), len(hit))]
    for i, w in enumerate(waves, 1):
        names = "、".join(
            "%s%s" % (m.get("emoji", ""), m.get("name", "")) for m in w["members"])
        parts.append("  第 %d 波：%s" % (i, names))
    if miss:
        parts.append("⚠️ 没匹配上的角色：%s（已跳过）" % "、".join(miss))
    # v4.124：缺角 / 缺技能不静默吞掉，明确报告给用户
    mr = plan.get("missing_roles") or []
    if mr:
        parts.append("🆕 缺角（库里没有，需你决定是否补角色）：%s"
                     % "、".join(str(x.get("name", "?")) for x in mr))
    ms = plan.get("missing_skills") or []
    if ms:
        parts.append("🔧 差技能（需你决定是否去 GitHub 找）：%s"
                     % "、".join(str(x.get("name", "?")) for x in ms))
    if bad_slugs:
        parts.append("⚠️ 已丢弃技能库里没有的 slug（防 PM 编造）：%s"
                     % "、".join(sorted(set(bad_slugs))))
    if plan.get("reason"):
        parts.append("💡 组队理由：%s" % plan["reason"])
    return proj, "\n".join(parts)


# ============ 验收结论解析（v4.122）============
# 引擎据此决定放行/打回。放数据层（Qt-free）便于离线单测，不依赖执行器。
_VERDICT_RE = re.compile(r"判\s*定\s*[：:]\s*(PASS|FAIL)", re.I)
_SECTION_RE = re.compile(r"【([^】]{1,12})】\s*(.*?)(?=\n\s*【|\Z)", re.S)


def parse_verdict(text):
    """解析项目经理的验收输出。

    返回 {"pass": bool, "advice": str, "parsed": bool}
      pass   —— 是否放行
      advice —— 打回指令/问题清单原文（重跑时喂给成员）
      parsed —— 是否真的解析到标准判定行

    ⚠️ 解析不到时**默认放行**（pass=True, parsed=False）：
    弱模型经常不按格式输出，把流水线卡死比放行一次更糟，宁可漏检不可卡死。
    调用方应在 parsed=False 时打日志提示。
    """
    text = text or ""
    ms = _VERDICT_RE.findall(text)
    if ms:
        return {
            "pass": ms[-1].upper() == "PASS",
            "advice": _extract_advice(text),
            "parsed": True,
        }
    # 退化：找【判定】段里的 PASS/FAIL
    sec = dict((m.group(1).strip(), m.group(2).strip()) for m in _SECTION_RE.finditer(text))
    for key in ("判定", "验收", "结论"):
        v = sec.get(key) or ""
        m = re.search(r"\b(PASS|FAIL)\b", v, re.I)
        if m:
            return {
                "pass": m.group(1).upper() == "PASS",
                "advice": _extract_advice(text, sec),
                "parsed": True,
            }
    return {"pass": True, "advice": _extract_advice(text, sec), "parsed": False}


def _extract_advice(text, sec=None):
    """取打回指令（FAIL 时给成员照做的改法），降级取问题清单。"""
    sec = sec if sec is not None else dict(
        (m.group(1).strip(), m.group(2).strip()) for m in _SECTION_RE.finditer(text or ""))
    for key in ("打回指令", "问题清单", "修改建议"):
        v = (sec.get(key) or "").strip()
        if v:
            return v[:1500]
    return ""


def is_pm_role(role):
    """是否「项目经理」角色（按名字判定，用户改过 emoji/id 也认）。

    PM 是全局调度台，**不进任何任务波次**——执行器与 UI 都据此过滤。
    """
    return str((role or {}).get("name", "")).strip() == "项目经理"


def wave_members(project, drop_pm=True):
    """返回项目的波次成员二维列表 [[role, ...], ...]（过滤空波次）。

    drop_pm=True（默认）时额外剔除「项目经理」：它是跨波调度台，
    若被误当成某一波的成员，既浪费一个执行位，又让"裁判下场踢球"。
    """
    out = []
    for w in (project or {}).get("waves", []):
        members = [m for m in (w.get("members") or []) if isinstance(m, dict)]
        if drop_pm:
            members = [m for m in members if not is_pm_role(m)]
        if members:
            out.append(members)
    return out


def project_summary(project):
    """一行摘要：共 N 个成员 / M 个波次。"""
    waves = wave_members(project)
    n_members = sum(len(w) for w in waves)
    return f"{len(waves)} 个波次 · {n_members} 位成员"


# ============ 技能扫描（运行时，不持久化）============
# SKILL.md frontmatter 是 4 行 key: value 块，简单 regex 拆即可，无需引入 yaml 库。
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_KV_RE = re.compile(r"^([a-zA-Z_]+)\s*:\s*(.*)$")


def _parse_skill_md(path: str):
    """读一份 SKILL.md，返回 {name, emoji, description, category, prompt}；失败返 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None
    m = _FM_RE.match(text)
    if not m:
        # 没有 frontmatter 也允许：name=目录名，prompt=全文
        return {"name": "", "emoji": "", "description": "", "category": "", "prompt": text.strip()}
    fm, body = m.group(1), m.group(2)
    out = {"name": "", "emoji": "", "description": "", "category": "", "prompt": body.strip()}
    for line in fm.splitlines():
        km = _KV_RE.match(line.strip())
        if not km:
            continue
        key = km.group(1).lower()
        val = km.group(2).strip().strip('"').strip("'")
        if key in out:
            out[key] = val
    # 兜底：emoji 从正文首行标题里抓（"# xxx emoji xxx" 这种）
    if not out["emoji"]:
        hh = re.match(r"^#\s+(.+)", body.strip())
        if hh:
            for ch in hh.group(1):
                if ord(ch) > 127 and ord(ch) > 0x1F300:  # 命中第一个非 ASCII 字符（粗略）
                    out["emoji"] = ch
                    break
    return out


def scan_available_skills(skills_dir: str = None):
    """扫描 skills 目录，返回 [{slug, name, emoji, description, category, prompt, path}]。

    路径不存在或为空 → 返回 []。每次现扫，不缓存，方便新建/修改 SKILL.md 后立即可用。
    """
    skills_dir = skills_dir or DEFAULT_SKILLS_DIR
    out = []
    if not os.path.isdir(skills_dir):
        return out
    try:
        for slug in sorted(os.listdir(skills_dir)):
            md_path = os.path.join(skills_dir, slug, "SKILL.md")
            if not os.path.isfile(md_path):
                continue
            info = _parse_skill_md(md_path)
            if not info:
                continue
            info["slug"] = slug
            info["path"] = md_path
            out.append(info)
    except Exception as e:
        log.warning("扫描技能目录失败: %s", e)
    return out


def _load_skill_prompt(slug: str, skills_dir: str = None):
    """按 slug 单文件读取，返回 dict（与 _parse_skill_md 一致）；失败返 None。"""
    skills_dir = skills_dir or DEFAULT_SKILLS_DIR
    md_path = os.path.join(skills_dir, slug, "SKILL.md")
    info = _parse_skill_md(md_path)
    if info:
        info["slug"] = slug
    return info
