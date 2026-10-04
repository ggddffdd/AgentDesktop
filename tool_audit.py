# -*- coding: utf-8 -*-
"""工具执行审计落盘（公共 helper，v4.211.4）

为什么要它
----------
大哥自写的「宪法第二章」第 ③ 条边界是「每笔自动放行留审计痕（可抽查）」。
军团侧（`legion_permissions.py`，v4.167.0）早就做到了 —— 每次非只读决策落
`legion_tool_audit.jsonl`（线程锁 + 参数摘要 + 脱敏预览）。

主对话链（`agent.py` + `permissions.PermissionEngine.decide()`）当时不是这样：
`decide()` 是**纯内存决策**，`Decision` 返回完就没了。事后只能翻
`route_log.jsonl` 里那条 `event=tool`，而它有三个够不着的地方：
  · 只有 12 位**不可逆**哈希，回答不了「确认的那笔到底杀了哪个进程 / 跑了什么命令」；
  · 没有「谁批的」（用户点过 / 会话信任自动放行 / 配置白名单）；
  · 它是**路由诊断日志**（超 20MB 就滚成 `.1`），证据会被静默滚掉。

本模块把军团那份落盘范式抽成公共件：军团与主对话**共用同一把锁、同一套脱敏
规则、同一套字段名**，避免出现两套"看起来一样、细节不一样"的账本 ——
两个账本口径一旦不一致，"主对话记了只读而军团没记"就会被误读成"军团漏了"。

三条硬约束
----------
1. **审计是证据，不是闸门**：写盘失败绝不改变决策、绝不抛异常。
   调用方拿到的 `Decision` 与有没有审计**无关**。失败只 warning 一次
   （刷屏会把真正的日志冲掉）。
2. **默认不落盘**：`PermissionEngine(audit_dir="")` = 关闭。这是**故意的**：
   判据套件会大量构造该引擎（授权层有 7~9 个套件），默认落盘等于把测试记录
   写进大哥的真实账本 —— **污染证据比不记更糟**。应用侧必须在构造点显式接线，
   由 `tests/test_tool_audit_c.py` F 组的 AST 判据守着这个接线不许被删。
3. **只读不记**（与军团同一口径）：只读决策量大且无风险，全记会把账本淹掉。
   口径必须两边一致，故此处**不做过滤**、由调用方按 `classify()` 判定
   （军团在 `check()` 里判，主对话在 `_audit_decision()` 里判），本模块只管写。

字段（与 `legion_tool_audit.jsonl` 同构，只多 `origin` 用于区分通道）
--------------------------------------------------------------------
`ts / time / origin / tool / args_digest / args_preview / decision / rule /
reason / by`
军团侧另有 `run_id / project_id / project_name / wave / role`（由 extra 带入）。
"""

import hashlib
import json
import os
import threading
import time

# 两个通道共用同一把锁：主对话有工作线程、军团是多线程，追加写不能靠"通常不碰撞"。
# 分开两把锁也不会写坏，但共用一把能让"谁在写"的排查简单一点。
AUDIT_LOCK = threading.Lock()

ARG_DIGEST_LEN = 12          # 与军团一致（test_legion_permissions 断言 len == 12）
ARG_PREVIEW_MAX = 200        # 预览截断长度
REASON_MAX = 300             # 原因截断长度

MAIN_AUDIT_NAME = "tool_audit.jsonl"            # 主对话链账本
LEGION_AUDIT_NAME = "legion_tool_audit.jsonl"   # 军团账本（文件名由 UI 面板读取，勿改）

# 参数里可能是敏感值的键（写审计前抹掉）
SENSITIVE_KEY_HINTS = ("key", "token", "secret", "password", "cookie",
                       "authorization", "credential")


def digest(args) -> str:
    """参数摘要哈希（12 位十六进制）—— 用于「参数变了必须重新批」的留痕比对。

    注意哈希**不可逆**：它只能证明"参数变没变"，回答不了"干了啥"。
    后者靠 `redact()`。两者必须**成对**写进同一条记录，缺了 redact
    的账本就只是一堆无法对账的哈希。
    """
    try:
        blob = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        blob = repr(args)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:ARG_DIGEST_LEN]


def redact(args) -> str:
    """参数可读摘要（脱敏 + 截断）—— 账本里能看出"干了啥"，但不留敏感值。"""
    try:
        obj = args if isinstance(args, dict) else {"_": args}
        safe = {}
        for k, v in obj.items():
            if any(h in str(k).lower() for h in SENSITIVE_KEY_HINTS):
                safe[k] = "***"
            elif isinstance(v, str) and len(v) > 80:
                safe[k] = v[:80] + "…"
            else:
                safe[k] = v
        txt = json.dumps(safe, ensure_ascii=False, default=str)
    except Exception:
        txt = repr(args)
    return txt[:ARG_PREVIEW_MAX]


def default_dir() -> str:
    """默认审计落点：`<USER_DATA_DIR>/logs`（与 route_log 同一目录）。

    config 是**惰性导入**的：它在模块级别 `import memory_store`，若在导入期就把
    它拉进来，会和「memory_store → … → permissions」形成环。审计是旁路，
    不该在导入图上占位置。
    """
    try:
        from config import USER_DATA_DIR
        base = USER_DATA_DIR
    except Exception:
        base = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI")
    return os.path.join(base, "logs")


class _OnceWarn:
    """同一进程内每个 tag 只报一次（审计失败若刷屏，会把真正的日志冲掉）。"""

    def __init__(self):
        self._done = set()

    def warn(self, tag, msg):
        if tag in self._done:
            return
        self._done.add(tag)
        try:
            import logging
            logging.getLogger(__name__).warning(msg)
        except Exception:
            pass


_warned = _OnceWarn()


def write(audit_dir, filename, rec, tag="audit") -> bool:
    """把一条记录追加到 `<audit_dir>/<filename>`。返回是否写成功。

    **绝不抛异常**：目录不可写 / 磁盘满 / 路径非法只是少一条痕，
    不能让一次工具调用因此失败，更不能影响权限决策的结果。
    """
    if not audit_dir:
        return False
    try:
        os.makedirs(audit_dir, exist_ok=True)
        path = os.path.join(audit_dir, filename)
        line = json.dumps(rec, ensure_ascii=False)
        with AUDIT_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception:
        _warned.warn(tag, "工具审计写入失败（只少一条痕，不影响主流程）")
        return False


def build_record(tool, args, decision, rule, reason="", by="engine",
                 origin="main", allowed=None, need_confirm=None, extra=None) -> dict:
    """组一条审计记录。

    字段名与军团侧同构（`ts/time/origin/tool/args_digest/args_preview/
    decision/rule/reason/by`），便于把两个账本放在一起抽查。
    """
    rec = {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "origin": origin,
        "tool": str(tool or ""),
        "args_digest": digest(args),
        "args_preview": redact(args),
        "decision": str(decision or ""),
        "rule": str(rule or ""),
        "reason": str(reason or "")[:REASON_MAX],
        "by": str(by or ""),
    }
    if allowed is not None:
        rec["allowed"] = bool(allowed)
    if need_confirm is not None:
        rec["need_confirm"] = bool(need_confirm)
    if extra:
        rec.update(extra)
    return rec
