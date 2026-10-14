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

import os
import threading

import tool_audit
from permissions import PermissionEngine
from risk import (RiskClass, classify, ALWAYS_CONFIRM, command_danger_level,
                  task_risk_level, TASK_CONFIRM_LEVELS)

# 军团里明确禁止的命令模式（即便本波已放行也不放）——危险且不可逆。
# 注意：这是"最低底线"，不是完整防护；真正的边界是路径作用域 + 授权。
DANGEROUS_CMD_PATTERNS = (
    "rm -rf /", "rm -rf /*", "format ", "format.com", "diskpart",
    "del /f /s /q c:", "rd /s /q c:", "mkfs", "reg delete hklm",
    "bcdedit", "vssadmin delete", "cipher /w",
    "shutdown /", "net user ", "netsh advfirewall set",
)

def _exec_blob(args):
    """取出命令/代码文本，供 risk.command_danger_level 使用（不另写探测器）。"""
    if not isinstance(args, dict):
        return str(args or "")
    raw = args.get("command") or args.get("cmd") or args.get("code") or ""
    if not isinstance(raw, str):
        raw = str(raw)
    return raw


# 非只读决策才写审计（只读量大且无风险，全记会把账本淹掉）
#
# v4.211.4：这一份 `_AUDIT_LOCK / _digest / _redact` 原本在军团里是**独立实现**。
# 主对话链（permissions.decide）落地审计时也要同一套落盘范式 —— 两份「看起来
# 一样」的实现迟早会漂移，漂移了就会出现「同样的参数两个账本对不上」。故抽成
# 公共件 tool_audit：两边共用同一把锁、同一套脱敏规则、同一套字段名。
# 下面这些名字保留为**别名**，不破坏既有引用（tests/test_legion_permissions.py
# 就直接调 `lp._digest`）。
_AUDIT_NAME = tool_audit.LEGION_AUDIT_NAME
_AUDIT_LOCK = tool_audit.AUDIT_LOCK
_ARG_DIGEST_LEN = tool_audit.ARG_DIGEST_LEN
_ARG_PREVIEW_MAX = tool_audit.ARG_PREVIEW_MAX
_SENSITIVE_KEY_HINTS = tool_audit.SENSITIVE_KEY_HINTS
_digest = tool_audit.digest
_redact = tool_audit.redact


class LegionDecision:
    """一次军团工具权限决策。"""

    __slots__ = ("allowed", "reason", "rule", "needs_auth", "needs_reconfirm")

    def __init__(self, allowed, reason, rule, needs_auth=False,
                 needs_reconfirm=False):
        self.allowed = allowed
        self.reason = reason
        self.rule = rule
        self.needs_auth = needs_auth   # 因"本波未获授权"而拒（供回执文案区分）
        # v4.265.0：本波已 grant 仍要人手再点一次（ALWAYS_CONFIRM / 高危参数 / critical）
        self.needs_reconfirm = needs_reconfirm

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
        # v4.265.0：用户原始任务文本（task_risk_level 的单一真源输入，不另抄规则）
        self.task_text = ""
        # 逐次二次确认账：(wave, tool, args_digest)。波次 grant 不能代替它。
        self._reconfirmed = set()
        # 可选回调 (tool, args, decision) -> bool。未挂回调时二次确认一律拒绝。
        self.reconfirm_cb = None
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

    def set_task(self, text: str) -> None:
        """记下用户原始任务，供 task_risk_level 收紧 write/exec。"""
        self.task_text = str(text or "")

    def grant_reconfirm(self, tool: str, args=None, wave=0) -> None:
        """记下「这一笔」已经人手二次确认（参数一变就失效）。"""
        key = self._reconfirm_key(tool, args, wave)
        with self._lock:
            self._reconfirmed.add(key)

    def _reconfirm_key(self, tool, args, wave):
        return (self._wave_key(wave), str(tool or ""), _digest(args))

    def _is_reconfirmed(self, tool, args, wave) -> bool:
        key = self._reconfirm_key(tool, args, wave)
        with self._lock:
            return key in self._reconfirmed

    def check(self, tool: str, args=None, role: str = "", wave=0) -> LegionDecision:
        """成员要执行某工具 → 返回是否放行（非只读决策一律落审计）。"""
        args = args or {}
        try:
            risk = classify(tool)
        except Exception:
            risk = RiskClass.EXTERNAL     # 分类失败按最保守处理

        dec = self._decide(tool, args, risk, wave)
        if (not dec.allowed) and dec.needs_reconfirm:
            cb = self.reconfirm_cb
            ok = False
            if callable(cb):
                try:
                    ok = bool(cb(tool, args, dec))
                except Exception:
                    ok = False
            if ok:
                self.grant_reconfirm(tool, args, wave)
                dec = self._decide(tool, args, risk, wave)
                if (not dec.allowed) and dec.needs_reconfirm:
                    dec = LegionDecision(
                        False,
                        dec.reason + "（二次确认未能解除，按拒绝处理）",
                        dec.rule, needs_auth=True, needs_reconfirm=False)

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
            return self._finish_allow(
                tool, args, risk, wave,
                "对外操作已在白名单且本波已授权", "external")

        # ③ EXEC：命令底线黑名单 → 授权门槛 → 参数级高危再确认
        #    短黑名单只是不可逆底线；高危但可逆的操作走 risk.command_danger_level
        #    （规则表留在 risk.py，这里禁止再抄一份）。
        if risk == RiskClass.EXEC:
            if tool in ("run_command", "run_python"):
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
            return self._finish_allow(
                tool, args, risk, wave, "执行类操作：本波已授权", "grant")

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
            return self._finish_allow(
                tool, args, risk, wave, "本地写入（工作区内）", "write_local")

        # ⑤ 未知类别：保守拒绝
        return LegionDecision(False, f"未知风险类别，保守拒绝：{tool}", "unknown")

    def _finish_allow(self, tool, args, risk, wave, reason, rule) -> LegionDecision:
        """本会放行时，再套主链同一套「必须再确认」规则。

        规则只从 risk.py 引用：ALWAYS_CONFIRM / command_danger_level /
        task_risk_level。本波 grant 与会话信任都不能跳过。
        """
        erule, ereason = self._extra_confirm(tool, args, risk)
        if erule and not self._is_reconfirmed(tool, args, wave):
            return LegionDecision(
                False, ereason, erule, needs_auth=True, needs_reconfirm=True)
        return LegionDecision(True, reason, rule)

    def _extra_confirm(self, tool, args, risk):
        """返回 (rule, reason)；不需要再确认时返回 ("", "")。"""
        if tool in ALWAYS_CONFIRM:
            return (
                "always_confirm",
                f"'{tool}' 属必须人工确认的操作（规则来自 risk.ALWAYS_CONFIRM），"
                f"本波授权不能代替逐次确认",
            )
        if risk == RiskClass.EXEC and tool in ("run_command", "run_python"):
            label = command_danger_level(_exec_blob(args))
            if label:
                return (
                    "high_risk_exec",
                    f"'{tool}' 含高危操作（{label}），需再次确认后才执行",
                )
        if risk in (RiskClass.WRITE_LOCAL, RiskClass.EXEC):
            lab = self._task_critical_label()
            if lab:
                return (
                    "task_risk_critical",
                    f"本任务属高风险（{lab}），'{tool}' 需逐次确认",
                )
        return ("", "")

    def _task_critical_label(self):
        lvl, lab = task_risk_level(getattr(self, "task_text", "") or "")
        if lvl in TASK_CONFIRM_LEVELS:
            return lab or lvl
        return ""

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
        # 落盘范式（锁 / 摘要 / 脱敏 / 一次告警）统一在公共件里，与主对话链的
        # tool_audit.jsonl 同构；origin 让两个账本可以合并抽查。
        rec = tool_audit.build_record(
            tool, args,
            "allow" if dec.allowed else "deny", dec.rule,
            reason=(dec.reason + (f"；{extra_reason}" if extra_reason else "")),
            by=by, origin="legion", allowed=dec.allowed,
            extra={"run_id": self.run_id, "project_id": self.project_id,
                   "project_name": self.project_name, "wave": wave, "role": role})
        if tool_audit.write(self._audit_dir, _AUDIT_NAME, rec, tag="legion"):
            return rec
        self._audit_failed_logged = True
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
