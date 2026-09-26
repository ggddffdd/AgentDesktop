# -*- coding: utf-8 -*-
"""小臭玩AI — 军团成员权限适配器（v4.167.0）

为什么需要它（审查 #1，P0）
--------------------------
军团原有的"波次授权"只控制**这一波能不能进下一波**。成员在波内拿到
`run_command` / `write_file` 之后，走的是：

    AgentNode.run() → exec_tool(allowed_tools=self.tool_names) → 直接执行

而 `exec_tool()` 只检查角色工具白名单，**不调用 PermissionEngine.decide()**
（该引擎在整个仓库里只在 agent.py 的主对话链被调用两处）。
于是：

  · 成员拿到 run_command 后不会被单独确认；
  · 成员写文件不受作用域约束（可写到工作区之外）；
  · 「本次会话全部信任」这类主对话链的信任态与军团完全无关（军团也没接）。

这与大哥自写的「宪法第二章」三道边界不一致：
  ① 参数围栏（参数变了必须重新批）
  ② 一键收回（信任可撤销）
  ③ 每笔自动放行留审计痕（可抽查）

本适配器的定位
--------------
在 `AgentNode → exec_tool` 之间插一道**军团专用**闸门，不改变主对话链行为：

    LegionWorker → AgentNode → LegionPermissionAdapter.check()
                                     ↓ 放行才继续
                                   exec_tool

三条规则（对应审查 §1 的 1–5 条）：

1. **成员默认只读**。EXEC（跑命令/代码/控桌面）与 EXTERNAL（外发）默认**拒绝**，
   必须由"本波已获用户放行"显式授权后才放行 —— 授权不再只挂在"能不能进下一波"。
2. **写入受作用域约束**。`write_file` 只能落在工作区/产物目录内 —— 比主对话链的
   `[~/Documents, ~/Desktop, APP_DIR, ~]` 更窄（军团不需要碰桌面与程序目录）。
3. **围栏先于信任**。外发白名单与路径作用域**先判**，会话信任只用来"省弹窗"。
   适配器刻意**不复用**主对话链那个引擎实例（它可能已被 set_session_trusted），
   自建实例、范围更窄，从根上避免"信任外溢到军团"。

审计：每次非只读决策都落 `LEGION_DIR/legion_tool_audit.jsonl`，
含 run_id / wave / role / tool / 参数摘要哈希 / 决策 / 命中规则 / 批准来源，
供事后抽查（对应第 ③ 条边界）。
"""

import hashlib
import json
import os
import threading
import time

from permissions import PermissionEngine
from risk import RiskClass, classify

# 军团里明确禁止的命令模式（即便本波已放行也不放）——危险且不可逆。
# 注意：这是"最低底线"，不是完整防护；真正的边界是路径作用域 + 授权。
DANGEROUS_CMD_PATTERNS = (
    "rm -rf /", "rm -rf /*", "format ", "format.com", "diskpart",
    "del /f /s /q c:", "rd /s /q c:", "mkfs", "reg delete hklm",
    "bcdedit", "vssadmin delete", "cipher /w",
    "shutdown /", "net user ", "netsh advfirewall set",
)

# 非只读决策才写审计（只读量大且无风险，全记会把账本淹掉）
_AUDIT_NAME = "legion_tool_audit.jsonl"
_AUDIT_LOCK = threading.Lock()      # 军团是多线程的，审计不能靠"通常不碰撞"
_ARG_DIGEST_LEN = 12
_ARG_PREVIEW_MAX = 200

# 参数里可能是敏感值的键（写审计前抹掉）
_SENSITIVE_KEY_HINTS = ("key", "token", "secret", "password", "cookie",
                        "authorization", "credential")


def _digest(args) -> str:
    """参数摘要哈希：用于"参数变了必须重新批"的留痕比对。"""
    try:
        blob = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        blob = repr(args)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:_ARG_DIGEST_LEN]


def _redact(args) -> str:
    """参数可读摘要（脱敏 + 截断）——审计里能看"干了啥"，但不留敏感值。"""
    try:
        obj = args if isinstance(args, dict) else {"_": args}
        safe = {}
        for k, v in obj.items():
            if any(h in str(k).lower() for h in _SENSITIVE_KEY_HINTS):
                safe[k] = "***"
            elif isinstance(v, str) and len(v) > 80:
                safe[k] = v[:80] + "…"
            else:
                safe[k] = v
        txt = json.dumps(safe, ensure_ascii=False, default=str)
    except Exception:
        txt = repr(args)
    return txt[:_ARG_PREVIEW_MAX]


class LegionDecision:
    """一次军团工具权限决策。"""

    __slots__ = ("allowed", "reason", "rule", "needs_auth")

    def __init__(self, allowed, reason, rule, needs_auth=False):
        self.allowed = allowed
        self.reason = reason
        self.rule = rule
        self.needs_auth = needs_auth   # 因"本波未获授权"而拒（供回执文案区分）

    def __repr__(self):
        return f"<LegionDecision {'allow' if self.allowed else 'deny'} {self.rule}>"


class LegionPermissionAdapter:
    """军团成员的权限闸门：默认只读，危险能力需本波显式授权。

    用法（在 LegionWorker 里）：
        self._perm = LegionPermissionAdapter(
            run_id=self.run_id, project_id=self.pid, project_name=pname)
        ...
        # 波次被放行时（decision == "pass"）——唯一的授权注入点
        self._perm.grant_wave(wave_no, by="user")
        ...
        # 成员执行时（_wrap 里把适配器挂到 agent 上）
        agent.perm = self._perm
    """

    def __init__(self, run_id: str = "", project_id: str = "",
                 project_name: str = "", scope_paths=None,
                 external_allow=None, gate_mode: str = "human",
                 audit_dir: str = ""):
        self.run_id = run_id or ""
        self.project_id = project_id or ""
        self.project_name = project_name or ""
        self.gate_mode = gate_mode or "human"
        self._lock = threading.RLock()

        # 作用域：军团比主对话链更窄（不碰桌面与程序目录）
        if scope_paths:
            self.scope_paths = list(scope_paths)
        else:
            self.scope_paths = self._default_scope()
        self.external_allow = set(external_allow or [])

        # 复用引擎的 in_scope 实现（边界算法已在 v4.74 修过前缀越界问题），
        # 但用**自己的实例 + 自己的范围** —— 绝不复用主对话链那个（可能带 session trust）。
        self._engine = PermissionEngine(
            mode="interactive", scope_paths=self.scope_paths,
            external_allow=sorted(self.external_allow))

        # 授权账：wave -> {"exec": set, "external": set, ...}；"*" 表示全波通用
        self._grants = {}
        # 审计落点
        self._audit_dir = audit_dir or self._default_audit_dir()
        self._audit_failed_logged = False

    # ---------- 默认目录 ----------

    @staticmethod
    def _default_scope():
        """军团允许写入的根：工作区 + 产物目录（都取自 config，单一事实源）。"""
        out = []
        try:
            import config
            for p in (getattr(config, "WORKSPACE_DIR", ""),
                      getattr(config, "PRODUCTS_DIR", ""),
                      getattr(config, "USER_DATA_DIR", "")):
                if p and p not in out:
                    out.append(p)
        except Exception:
            pass
        if not out:
            out.append(os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI"))
        return out

    @staticmethod
    def _default_audit_dir():
        try:
            import legion
            return getattr(legion, "LEGION_DIR", "") or ""
        except Exception:
            return ""

    # ---------- 授权（唯一注入点：波次放行时） ----------

    @staticmethod
    def _wave_key(wave):
        """波次键归一：'*' 表示"任意波通用"，数字波次转 int，None → 0。"""
        if wave == "*":
            return "*"
        try:
            return int(wave)
        except (TypeError, ValueError):
            return 0

    def grant_wave(self, wave: int, classes=("exec",), by: str = "user",
                   reason: str = "") -> dict:
        """放行某一波时授予该波成员的危险能力。

        classes 缺省只授 EXEC（跑命令/代码）；EXTERNAL 永远还要过白名单，
        所以这里即便授了 external，白名单外的工具仍会被拒。
        """
        key = self._wave_key(wave)
        with self._lock:
            bucket = self._grants.setdefault(key, set())
            for c in classes:
                bucket.add(c)
        rec = self._audit("__grant__", {"wave": key, "classes": sorted(classes)},
                          LegionDecision(True, "本波已放行，授予能力", "grant"),
                          role="", wave=key, by=by,
                          extra_reason=reason)
        return rec or {}

    def grant_all(self, classes=("exec",), by: str = "gate_off", reason: str = "") -> dict:
        """非 human 模式下的一次性授权（用户已放弃逐波把关）。仍记审计。"""
        return self.grant_wave("*", classes=classes, by=by, reason=reason)

    def revoke(self, wave=None, by: str = "user") -> dict:
        """一键收回（对应「信任可撤销」）。wave=None 收回全部。"""
        with self._lock:
            if wave is None:
                self._grants.clear()
            else:
                self._grants.pop(self._wave_key(wave), None)
        return self._audit("__revoke__", {"wave": wave},
                           LegionDecision(False, "授权已收回", "revoke"),
                           role="", wave=wave if isinstance(wave, int) else 0,
                           by=by) or {}

    def is_granted(self, cls: str, wave=None) -> bool:
        with self._lock:
            buckets = []
            if wave is not None:
                buckets.append(self._grants.get(self._wave_key(wave), set()))
            buckets.append(self._grants.get("*", set()))
            return any(cls in b for b in buckets)

    def granted_waves(self) -> list:
        with self._lock:
            return sorted(self._grants.keys(), key=str)

    # ---------- 核心决策 ----------

    def check(self, tool: str, args=None, role: str = "", wave=0) -> LegionDecision:
        """成员要执行某工具 → 返回是否放行（非只读决策一律落审计）。"""
        args = args or {}
        try:
            risk = classify(tool)
        except Exception:
            risk = RiskClass.EXTERNAL     # 分类失败按最保守处理

        dec = self._decide(tool, args, risk, wave)

        # 只读不记（量大且无风险）；其余全记，可抽查
        if risk != RiskClass.READ:
            self._audit(tool, args, dec, role=role, wave=wave,
                        by="user" if dec.rule == "grant" else "engine")
        return dec

    def _decide(self, tool, args, risk, wave) -> LegionDecision:
        # ① 只读：放行
        if risk == RiskClass.READ:
            if tool == "read_file":
                p = (args or {}).get("path", "")
                if p and not self._in_scope(p):
                    return LegionDecision(
                        False, f"读取超出工作区范围（{p}），已阻止", "scope")
            return LegionDecision(True, "只读操作", "read")

        # ② EXTERNAL：白名单是硬围栏（先于任何信任/授权）
        if risk == RiskClass.EXTERNAL:
            if tool not in self.external_allow:
                return LegionDecision(
                    False,
                    f"对外操作需白名单授权，'{tool}' 未授权已阻止", "external_block")
            if not self.is_granted("external", wave):
                return LegionDecision(
                    False, f"对外操作 '{tool}' 需本波授权（默认只读）",
                    "needs_auth", needs_auth=True)
            return LegionDecision(True, "对外操作已在白名单且本波已授权", "external")

        # ③ EXEC：命令底线黑名单 → 授权门槛
        if risk == RiskClass.EXEC:
            if tool == "run_command":
                bad = self._dangerous_command(args)
                if bad:
                    return LegionDecision(
                        False,
                        f"命令命中禁止模式（{bad}），军团一律不执行", "dangerous_cmd")
            if not self.is_granted("exec", wave):
                return LegionDecision(
                    False,
                    f"'{tool}' 属执行类，成员默认只读；需本波放行后才可调用",
                    "needs_auth", needs_auth=True)
            return LegionDecision(True, "执行类操作：本波已授权", "grant")

        # ④ WRITE_LOCAL：写入必须留在工作区内
        if risk == RiskClass.WRITE_LOCAL:
            if tool == "write_file":
                p = (args or {}).get("path", "")
                if not p:
                    return LegionDecision(False, "未提供写入路径", "scope")
                if not self._in_scope(p):
                    return LegionDecision(
                        False,
                        f"写入超出工作区范围（{p}），军团只允许写自己的工作区", "scope")
            return LegionDecision(True, "本地写入（工作区内）", "write_local")

        # ⑤ 未知类别：保守拒绝
        return LegionDecision(False, f"未知风险类别，保守拒绝：{tool}", "unknown")

    @staticmethod
    def _dangerous_command(args):
        cmd = ""
        if isinstance(args, dict):
            cmd = str(args.get("command") or args.get("cmd") or "")
        low = cmd.lower()
        for pat in DANGEROUS_CMD_PATTERNS:
            if pat in low:
                return pat.strip()
        return ""

    def _in_scope(self, path) -> bool:
        try:
            return bool(self._engine.in_scope(path))
        except Exception:
            return False

    # ---------- 审计 ----------

    def _audit(self, tool, args, dec, role="", wave=0, by="engine",
               extra_reason=""):
        """写一笔工具级审计（加锁 + 脱敏 + 参数摘要）。失败不影响主流程。"""
        if not self._audit_dir:
            return None
        rec = {
            "ts": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "run_id": self.run_id,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "wave": wave,
            "role": role,
            "tool": tool,
            "args_digest": _digest(args),
            "args_preview": _redact(args),
            "decision": "allow" if dec.allowed else "deny",
            "rule": dec.rule,
            "reason": (dec.reason + (f"；{extra_reason}" if extra_reason else ""))[:300],
            "by": by,
        }
        try:
            os.makedirs(self._audit_dir, exist_ok=True)
            path = os.path.join(self._audit_dir, _AUDIT_NAME)
            line = json.dumps(rec, ensure_ascii=False)
            # 军团是多线程系统，审计是授权体系的证据 —— 不能靠"通常不碰撞"
            with _AUDIT_LOCK:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            return rec
        except Exception:
            # 只报一次，别把日志刷满
            if not self._audit_failed_logged:
                self._audit_failed_logged = True
                try:
                    import logging
                    logging.getLogger(__name__).warning("军团工具审计写入失败")
                except Exception:
                    pass
            return None

    def audit_path(self) -> str:
        return os.path.join(self._audit_dir, _AUDIT_NAME) if self._audit_dir else ""

    def state(self) -> dict:
        """快照，供任务板/报告/测试。"""
        with self._lock:
            return {
                "run_id": self.run_id,
                "gate_mode": self.gate_mode,
                "scope_paths": list(self.scope_paths),
                "external_allow": sorted(self.external_allow),
                "granted": {str(k): sorted(v) for k, v in self._grants.items()},
            }
