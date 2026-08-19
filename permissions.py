# -*- coding: utf-8 -*-
"""权限引擎（v4.50，借鉴 andrewyng/openworker 的 permissions.py 设计）

统一管理「工具调用是否需要用户确认」，把决策逻辑从 UI/agent 里抽出来：
- 5 种模式：discuss（仅讨论）/ plan（只规划）/ interactive（默认问）/
            auto（全放行）/ custom（按白名单放行）
- 会话信任：用户点「本次会话全部信任」或确认框勾选信任后，本会话不再逐个问
- 免确认白名单：配置里写好的工具名（旧 skip_confirm 开关等价于此设全集）
- 路径作用域：本地写入/执行类工具写文件必须落在允许目录（用户目录内），安全边界

引擎只做决策（decide），UI 只负责弹窗，互不耦合。
"""

import os
import logging
from dataclasses import dataclass

from risk import RiskClass, classify, tier_of

log = logging.getLogger(__name__)


# 模式中文标签（value -> 显示文本）
MODES = {
    "interactive": "交互（危险操作逐个问）",
    "plan": "规划（只做只读，不实际执行）",
    "auto": "自动（全部直接执行）",
    "discuss": "仅讨论（不执行任何操作）",
    "custom": "自定义（仅白名单免确认）",
}


@dataclass
class Decision:
    """一次权限决策结果。"""
    allowed: bool       # 是否允许执行
    needs_user: bool    # 执行前是否需要用户确认
    reason: str         # 人类可读原因（用于日志/提示）
    rule: str = ""      # 命中规则（mode:auto / session / auto_allow / risk:exec ...）


class PermissionEngine:
    def __init__(self, mode="interactive", auto_allow=None, scope_paths=None, external_allow=None):
        self.mode = mode if mode in MODES else "interactive"
        # 配置级免确认白名单（工具名集合）
        self.auto_allow = set(auto_allow or [])
        # 对外动作白名单（EXTERNAL 工具必须在此才放行；空=任何对外操作都不自动执行）
        self.external_allow = set(external_allow or [])
        # 路径作用域（本地写入允许的根目录，绝对路径）
        self.scope_paths = [os.path.abspath(p) for p in (scope_paths or [])]
        # 会话级信任：工具名集合，含 "*" 表示信任全部
        self.session_allow = set()
        self.session_trusted = False

    # ---------- 配置变更 ----------
    def set_mode(self, mode):
        if mode in MODES:
            self.mode = mode

    def set_session_trusted(self):
        """本次会话全部信任（确认框勾选 / 设置里点按钮）。"""
        self.session_trusted = True
        self.session_allow.add("*")

    def trust_tool(self, name):
        """信任单个工具（本次会话）。"""
        self.session_allow.add(name)

    # ---------- 路径作用域 ----------
    def in_scope(self, path):
        if not self.scope_paths:
            return True
        try:
            p = os.path.abspath(path)
        except Exception:
            return False
        return any(p.startswith(s) for s in self.scope_paths)

    # ---------- 核心决策 ----------
    def decide(self, name, args=None):
        """给定工具名与参数，返回 Decision。

        v4.74 边界安全增强：
        - 对外动作白名单（external_allow）：EXTERNAL 工具未授权一律阻止，防 agent 私自对外。
        - auto 模式不再无脑放行：EXEC/EXTERNAL 仍需用户确认（防越权自动化）。
        """
        risk = classify(name)
        tier = tier_of(name)  # v4.42 三级语义（auto/semi/manual）

        # 1) 模式优先
        if self.mode == "discuss":
            return Decision(False, False, "仅讨论模式：不执行任何操作", "mode:discuss")
        if self.mode == "plan":
            if risk == RiskClass.READ:
                return Decision(True, False, "规划模式：允许只读操作", "mode:plan")
            return Decision(False, False, "规划模式：仅允许只读，不实际执行写入/命令", "mode:plan")

        # 2) 会话信任（"*" 或具体工具名）
        if self.session_trusted or "*" in self.session_allow or name in self.session_allow:
            return Decision(True, False, "本次会话已信任该操作", "session")

        # 3) 对外动作白名单：EXTERNAL 必须显式授权，否则一律阻止（防 agent 私自对外）
        if risk == RiskClass.EXTERNAL:
            if name not in self.external_allow:
                log.warning("对外操作被白名单拦截: %s", name)
                return Decision(False, False,
                                f"对外操作需白名单授权，'{name}' 未授权已阻止", "external_block")
            if self.mode == "auto":
                return Decision(True, False, "对外操作已在白名单，自动放行", "external_allow")

        # 4) 配置白名单（auto_allow）：自定义模式靠它放行指定手动工具
        if name in self.auto_allow:
            return Decision(True, False, "在免确认白名单中", "auto_allow")

        # 5) 路径作用域：写文件必须落在允许目录（越界直接阻止）
        if name == "write_file":
            path = (args or {}).get("path", "")
            if path and not self.in_scope(path):
                return Decision(False, False,
                                 f"路径超出允许范围（{path}），已阻止写入", "scope")

        # 6) auto 模式：执行类（EXEC）仍需确认，防越权自动化；只读/本地写入直行
        if self.mode == "auto":
            if risk == RiskClass.EXEC:
                return Decision(True, True, "自动模式：执行操作需确认", "auto:gate")
            return Decision(True, False, "自动模式：允许执行", "auto")

        # 7) 交互模式（默认）：沿用 v4.42 三级语义
        #    auto/semi 直接执行；manual（执行/外部 + 个别本地写入覆盖）需确认
        if tier in ("auto", "semi"):
            return Decision(True, False, "该操作可直接执行", "tier:" + tier)
        return Decision(True, True, "需要用户确认后执行", "tier:manual")
