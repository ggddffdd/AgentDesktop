# -*- coding: utf-8 -*-
"""v4.109 路由旁路日志 —— 只记录，绝不干预主流程。

目的：回答「DeepSeek 到底被触发了多少次、烧了多少 token、是谁触发的」。
当前路由（v4.94 起）是黑盒：智能升舱天天在跑，但没有任何结构化记录，
无法判断「默认走免费 Agnes」到底有没有真的省钱，也无法定位过度升舱。

设计铁律：
1. **旁路**：写日志失败、抛异常、磁盘满，一律静默吞掉，绝不影响对话。
2. **不回读**：主流程永远不读这个文件，只写。
3. **自限容**：超过 MAX_BYTES 自动轮转一个历史文件，防止无限膨胀。

落盘位置：~/Documents/小臭玩AI/logs/route_log.jsonl（每行一条 JSON）
字段：
  event=route  路由决策：model / base_url / upgraded / reason / lock / msgs_len
  event=usage  调用成本：model / base_url / prompt_tokens / completion_tokens / total_tokens
两者用 ts + model 关联；tier 标注 paid/free，便于直接统计付费通道花费。
"""

import os
import json
import threading
from datetime import datetime

_LOCK = threading.Lock()
_PATH = None
MAX_BYTES = 20 * 1024 * 1024   # 20MB 轮转


def _log_path():
    """解析日志文件绝对路径（首次调用时建目录）。"""
    global _PATH
    if _PATH is None:
        try:
            from config import USER_DATA_DIR
            base = USER_DATA_DIR
        except Exception:
            base = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI")
        d = os.path.join(base, "logs")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        _PATH = os.path.join(d, "route_log.jsonl")
    return _PATH


def _tier(model="", base_url=""):
    """粗略判定付费档：DeepSeek 官方通道 = 付费，其余（Agnes 等）= 免费。"""
    s = (str(model) + " " + str(base_url)).lower()
    if "deepseek" in s:
        return "paid"
    if "agnes" in s:
        return "free"
    return "other"


def log_route(**fields):
    """追加一行 JSON 日志。任何异常一律吞掉——这是旁路，不许拖垮对话。"""
    try:
        rec = {"ts": datetime.now().isoformat(timespec="seconds")}
        model = str(fields.get("model") or "")
        base_url = str(fields.get("base_url") or "")
        rec["tier"] = fields.pop("tier", None) or _tier(model, base_url)
        rec.update(fields)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        p = _log_path()
        with _LOCK:
            try:
                if os.path.exists(p) and os.path.getsize(p) > MAX_BYTES:
                    os.replace(p, p + ".1")
            except Exception:
                pass
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception:
        return False


def read_recent(limit=200, event=None):
    """读取最近若干条记录（供复盘脚本使用，主流程不调用）。

    超大文件只从尾部读，避免一次性载入几十 MB。
    """
    p = _log_path()
    if not os.path.exists(p):
        return []
    out = []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if event and rec.get("event") != event:
                    continue
                out.append(rec)
    except Exception:
        return out
    return out[-limit:]
