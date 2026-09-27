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
import json
import hashlib
import logging
from dataclasses import dataclass

from risk import RiskClass, classify, tier_of, ALWAYS_CONFIRM

log = logging.getLogger(__name__)


def args_fingerprint(args):
    """参数指纹（v4.169.0 P0-5）。规范化后取 sha256 前 16 位。

    用 `sort_keys=True` 保证 dict 顺序不影响结果 —— 否则同一笔操作
    因序列化顺序不同会算出两个指纹，用户会莫名其妙地被重复问。
    """
    try:
        s = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        s = repr(args)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


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

    def trust_tool(self, name, args=None):
        """信任单个工具的**这一笔参数形态**（本次会话）。

        v4.169.0（审查 P0-5）：授权粒度从「工具名」收紧为「工具名 + 参数指纹」。

        原来是 `session_allow.add(name)` —— 只记工具名。于是用户确认
        `run_command("echo ok")` 之后，同一会话里 `run_command("<危险命令>")`
        也不再询问 —— **一次授权被无限放大**。同理 `write_file` 确认一个路径后，
        写任意其它路径（只要在作用域内）也免确认。

        现在按参数规范化指纹记账：**参数有任何变化都要重新确认**。
        用户显式点「本次会话全部信任」仍然走 `set_session_trusted()`（"*"），
        那是明确的粗粒度授权，保留其语义。
        """
        self.session_allow.add(f"{name}#{args_fingerprint(args)}")

    def is_trusted(self, name, args=None):
        """该工具+该参数是否已被本次会话信任（诊断/测试用）。"""
        if self.session_trusted or "*" in self.session_allow:
            return True
        return f"{name}#{args_fingerprint(args)}" in self.session_allow

    # ---------- 路径作用域 ----------
    def in_scope(self, path):
        if not self.scope_paths:
            return True
        try:
            p = os.path.normcase(os.path.abspath(path))
        except Exception:
            return False
        # 审计修复 C2：裸 startswith 会让作用域 "D:\docs\小臭玩AI" 放行兄弟目录
        # "D:\docs\小臭玩AI_backup\..."（前缀越界写文件出沙箱）。改为「相等，或
        # 以带分隔符的目录前缀开头」；normcase 统一 Windows 盘符大小写。
        for s in self.scope_paths:
            s = os.path.normcase(s)
            if p == s:
                return True
            prefix = s if s.endswith((os.sep, "/")) else s + os.sep
            if p.startswith(prefix):
                return True
        return False

    # ---------- 核心决策 ----------
    def decide(self, name, args=None, explicit_intent=True):
        """给定工具名与参数，返回 Decision。

        v4.74 边界安全增强：
        - 对外动作白名单（external_allow）：EXTERNAL 工具未授权一律阻止，防 agent 私自对外。
        - auto 模式不再无脑放行：EXEC/EXTERNAL 仍需用户确认（防越权自动化）。

        v4.167.0 顺序修正：**硬围栏（外发白名单 / 路径作用域）先于会话信任**。
        原顺序让"本次会话全部信任"绕过这两道边界；信任只该省掉弹窗，不该放开边界。

        v4.169.0（审查 P0-3）加两道：
        - **硬确认档** `ALWAYS_CONFIRM`（技能安装/创建、删自动化、删数据）：
          任何模式都必须人工确认，不受 auto / 会话信任 / 白名单影响。
          原来 auto 模式分支只看 risk 不看 tier，把标成"手动"的这几个直接放行了。
        - **来源闸** `explicit_intent`：本轮用户消息**没有执行意图**（纯提问/讨论/评价）时，
          模型自行发起的**非只读**操作一律需确认。
          这是「没下命令却干活」的最后一道闸 —— 前面的路由若误判漏过来，
          这一层还能兜住：生图/写记忆/建自动化都不会偷偷跑掉。
          只读（查资料、看文件）不打扰，否则问一句"为什么"会变得寸步难行。
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

        # 2) 硬围栏（**必须先于会话信任**）
        #    v4.167.0：原实现把 session trust 放在最前，于是「本次会话全部信任」
        #    会把外发白名单与路径作用域一起跳过 —— 这与大哥定的三道边界不一致：
        #    信任的意义是"省一次弹窗"，不是"放开边界"。越界写入与未授权外发，
        #    任何时候都不该被"信任"放行。
        if risk == RiskClass.EXTERNAL and name not in self.external_allow:
            log.warning("对外操作被白名单拦截: %s", name)
            return Decision(False, False,
                            f"对外操作需白名单授权，'{name}' 未授权已阻止", "external_block")
        if name == "write_file":
            path = (args or {}).get("path", "")
            if path and not self.in_scope(path):
                return Decision(False, False,
                                 f"路径超出允许范围（{path}），已阻止写入", "scope")

        # 2.5) 硬确认档（v4.169.0 P0-3）：任何模式都要人工确认 ——
        #      先于会话信任/白名单，所以"本次会话全部信任"也免不了它。
        if name in ALWAYS_CONFIRM:
            return Decision(True, True,
                            f"'{name}' 属必须人工确认的操作（安装/创建技能、删数据），"
                            f"不受执行模式与会话信任影响",
                            "always_confirm")

        # 2.6) 来源闸（v4.169.0 P0-3）：本轮用户消息没有执行意图（纯提问/讨论/评价）时，
        #      模型自行发起的**非只读**操作一律要确认。
        #      只读不拦 —— 问一句"为什么"不该寸步难行；写入/执行/外发则必须问。
        #      注意 classify() 对未登记的工具有 fail-closed 语义（落 EXTERNAL），
        #      所以这里 `!= READ` 的写法天然把"没登记的工具"也纳入了确认范围。
        if not explicit_intent and risk != RiskClass.READ:
            _what = {RiskClass.WRITE_LOCAL: "写入",
                     RiskClass.EXEC: "执行",
                     RiskClass.EXTERNAL: "对外"}.get(risk, "副作用")
            return Decision(True, True,
                            f"你本轮的消息没有执行意图，'{name}' 是模型自主发起的{_what}操作，"
                            f"需要你确认",
                            "implicit_intent")

        # 3) 会话信任（v4.169.0 P0-5：**绑参数指纹**，不再按工具名放行）
        #    · "*" / session_trusted —— 用户显式点过「本次会话全部信任」，粗粒度放行；
        #    · "name#指纹" —— 用户确认过**这一笔**；参数一变就得重新问。
        if self.session_trusted or "*" in self.session_allow:
            return Decision(True, False, "本次会话已全部信任", "session:*")
        if f"{name}#{args_fingerprint(args)}" in self.session_allow:
            return Decision(True, False, "本次会话已信任该操作（同工具 + 同参数）", "session")

        # 4) 对外动作已在白名单 + auto 模式 → 自动放行（保留 v4.74 语义）
        if risk == RiskClass.EXTERNAL and self.mode == "auto":
            return Decision(True, False, "对外操作已在白名单，自动放行", "external_allow")

        # 5) 配置白名单（auto_allow）：自定义模式靠它放行指定手动工具
        if name in self.auto_allow:
            return Decision(True, False, "在免确认白名单中", "auto_allow")


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
