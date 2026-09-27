# -*- coding: utf-8 -*-
"""风险分类模块（v4.50，借鉴 andrewyng/openworker 的 risk.py 设计）

把每个工具按「副作用」归到 4 个风险档：
- READ        : 只读/观察，无副作用（搜索、读文件、截图、查询…）
- WRITE_LOCAL : 改本地文件或本地状态，不外发（写文件、生图、记记忆、数据库写…）
- EXEC        : 跑代码/命令、控制桌面应用（run_python、鼠标键盘、窗口/进程…）
- EXTERNAL    : 触碰外部服务/网络（发邮件、webhook、MCP…）

这是权限引擎（permissions.py）的唯一事实来源；旧 TOOL_TIER 已迁移到此。

v4.171.0 合并：原先「风险类」与「显示档位」分在两张表
（`RISK_MAP` + `_TIER_OVERRIDE`），而 permissions.decide 的 auto 分支只看前者 ——
于是标在后者上的「手动」在自动模式下整套失效。现在**一个工具的
风险类 / 档位覆盖 / 硬确认全部写在同一条声明里**，
结构上不可能再出现「某处只看了两张表中的一张」。
"""

import logging
from enum import Enum

log = logging.getLogger(__name__)


class RiskClass(str, Enum):
    READ = "read"
    WRITE_LOCAL = "write_local"
    EXEC = "exec"
    EXTERNAL = "external"

    @property
    def label(self):
        return {
            RiskClass.READ: "只读",
            RiskClass.WRITE_LOCAL: "本地写入",
            RiskClass.EXEC: "执行/控制",
            RiskClass.EXTERNAL: "外部操作",
        }[self]


# 核心 25+ 工具 + 动态扩展工具（system_/software_/app_/mouse_/keyboard_/browser_/...）
RISK_MAP = {
    # ── READ（自主）──
    "web_search": RiskClass.READ,
    "web_fetch": RiskClass.READ,
    "read_file": RiskClass.READ,
    "rag_index": RiskClass.READ,
    "rag_search": RiskClass.READ,
    "analyze_image": RiskClass.READ,
    "log_query": RiskClass.READ,
    "chart_gen": RiskClass.READ,
    "context_compress": RiskClass.READ,
    "context_summary": RiskClass.READ,
    "db_query": RiskClass.READ,
    "skill_search": RiskClass.READ,
    "screenshot": RiskClass.READ,
    "clipboard_read": RiskClass.READ,
    "process_list": RiskClass.READ,
    "window_list": RiskClass.READ,
    "window_get_info": RiskClass.READ,
    "app_get_text": RiskClass.READ,
    "app_list_controls": RiskClass.READ,
    "app_window_state": RiskClass.READ,
    "app_screenshot": RiskClass.READ,
    "sys_info": RiskClass.READ,
    "list_automation": RiskClass.READ,  # v4.89：查自动化任务列表，只读
    "search_memory": RiskClass.READ,  # v4.92：搜记忆，纯查询（此前漏登记，靠 permission_external_allow 白名单硬兜）
    # ── WRITE_LOCAL（半自主 / 本地写入）──
    "image_gen": RiskClass.WRITE_LOCAL,
    "video_gen": RiskClass.WRITE_LOCAL,
    "use_skill": RiskClass.WRITE_LOCAL,
    "remember": RiskClass.WRITE_LOCAL,
    "clipboard_write": RiskClass.WRITE_LOCAL,
    "schedule": (RiskClass.WRITE_LOCAL, "manual"),
    # 档位覆盖：本地写默认 semi，这几个保持旧 manual 标签（比默认**更严**）
    "write_file": (RiskClass.WRITE_LOCAL, "manual"),
    "db_insert": (RiskClass.WRITE_LOCAL, "manual"),
    "db_update": (RiskClass.WRITE_LOCAL, "manual"),
    # 第三列 True = 硬确认（任何模式都要人点）
    "db_delete": (RiskClass.WRITE_LOCAL, "manual", True),
    "skill_install": (RiskClass.WRITE_LOCAL, "manual", True),
    "create_skill": (RiskClass.WRITE_LOCAL, None, True),  # v4.84(热修15·B)：模型自动建技能→落待审核目录，仅本地写、无外发，解除外部白名单拦截
    "create_automation": RiskClass.WRITE_LOCAL,  # v4.89：建自动化任务→写本地 automation_tasks.json，仅本地写、无外发
    "delete_automation": (RiskClass.WRITE_LOCAL, None, True),  # v4.89：删自动化任务，仅本地写
    "run_workflow": RiskClass.WRITE_LOCAL,  # v4.92：编排入口，仅通知主线程启动、不直接执行；内部危险步骤各自过 risk（此前漏登记）
    # ── v4.169.0 补登记：此前遗漏的工具 ──
    # `classify()` 对未登记工具 fallback 成 EXTERNAL，而 EXTERNAL 必须出现在
    # `external_allow` 白名单里才放行 —— 默认配置只有 3 项，于是这些工具的
    # 实际表现是"**被当成对外操作直接拦掉**"（只读查询也一样）。
    # 这是做批次 A 权限审查时顺带扫出来的（用 TOOL_DEFS 与实际登记表比对）。
    # ── READ（只读查询）──
    "director_status": RiskClass.READ,
    "legion_board": RiskClass.READ,
    "legion_find_asset": RiskClass.READ,
    "legion_get_output": RiskClass.READ,
    "legion_get_sources": RiskClass.READ,
    "legion_list_outputs": RiskClass.READ,
    "legion_read_log": RiskClass.READ,
    "webhook_events": RiskClass.READ,          # 查 webhook 事件记录
    # ── WRITE_LOCAL（本地生成 / 修改，无外发）──
    "director_confirm": RiskClass.WRITE_LOCAL,        # 确认并推进导演台流程
    "director_gen_clues": RiskClass.WRITE_LOCAL,
    "director_merge": RiskClass.WRITE_LOCAL,          # 合成本地成片
    "director_revise_character": RiskClass.WRITE_LOCAL,
    "director_revise_clip": RiskClass.WRITE_LOCAL,
    "director_revise_clue": RiskClass.WRITE_LOCAL,
    "director_revise_keyframe": RiskClass.WRITE_LOCAL,
    "director_revise_shots": RiskClass.WRITE_LOCAL,
    "director_revise_story": RiskClass.WRITE_LOCAL,
    "director_rollback": RiskClass.WRITE_LOCAL,       # 回滚到历史版本（本地写）
    "legion_report_issue": RiskClass.WRITE_LOCAL,     # 写本地问题记录
    # 刻意**不登记**的（保持 EXTERNAL = 必须白名单）：
    #   send_email（真发邮件）、webhook_start / webhook_stop（对外暴露端口）

    # ── EXEC（手动 / 执行控制）──
    "run_command": RiskClass.EXEC,
    "run_python": RiskClass.EXEC,
    "mouse_click": RiskClass.EXEC,
    "mouse_move": RiskClass.EXEC,
    "mouse_scroll": RiskClass.EXEC,
    "keyboard_press": RiskClass.EXEC,
    "keyboard_type": RiskClass.EXEC,
    "window_focus": RiskClass.EXEC,
    "process_kill": RiskClass.EXEC,
    "process_start": RiskClass.EXEC,
    "app_click": RiskClass.EXEC,
    "app_focus": RiskClass.EXEC,
    "app_kill": RiskClass.EXEC,
    "app_launch": RiskClass.EXEC,
    "app_type": RiskClass.EXEC,
    "app_wait_for": RiskClass.EXEC,
    # v4.80：browser_open / browser_read 是被动只读（打开网页截图 / 提取文本），
    # 与 web_fetch 同级，降为 READ（自动执行、免确认），消除「每次确认→原样重试→去重护栏拦截」死循环。
    # browser_click / browser_fill 会真实点击/填表，保留 EXEC（手动确认）以防误操作。
    "browser_open": RiskClass.READ,
    "browser_click": RiskClass.EXEC,
    "browser_fill": RiskClass.EXEC,
    "browser_read": RiskClass.READ,
    # ── EXTERNAL（手动 / 外部操作）──
    "send_email": RiskClass.EXTERNAL,
    "webhook_start": RiskClass.EXTERNAL,
    "webhook_stop": RiskClass.EXTERNAL,
    "webhook_events": RiskClass.EXTERNAL,
}


def _policy(name):
    """解析一条工具策略声明 → (风险类, 档位覆盖, 硬确认)。

    **唯一入口**：`classify` / `tier_of` / `ALWAYS_CONFIRM` 全部从这里取。
    合并的意义就在这 —— 一个工具的性质与等级永远出自同一条声明，
    不可能再出现「permissions 只看了风险类、没看档位覆盖」那类静默失效。

    声明形态（见 RISK_MAP 内注释）：
        RiskClass.X                   → (X, None, False)
        (RiskClass.X, "manual")       → (X, "manual", False)
        (RiskClass.X, None, True)     → (X, None, True)
        (RiskClass.X, "manual", True) → (X, "manual", True)
    """
    v = RISK_MAP.get(name)
    if v is None:
        return None, None, False
    if isinstance(v, tuple):
        risk = v[0] if len(v) > 0 else None
        tier = v[1] if len(v) > 1 else None
        hard = bool(v[2]) if len(v) > 2 else False
        return risk, tier, hard
    return v, None, False


def classify(name):
    """返回工具风险档，未知工具默认 EXTERNAL（最严格、需确认）。"""
    risk, _tier, _hard = _policy(name)
    if risk is not None:
        return risk
    # 前缀兜底（动态扩展工具）
    if (name.startswith(("browser_", "app_", "mouse_", "keyboard_",
                         "window_focus", "process_kill", "process_start",
                         "system_", "software_", "mcp_"))):
        return RiskClass.EXEC
    if name.startswith("db_"):
        return RiskClass.WRITE_LOCAL
    if name.startswith("clipboard_"):
        return RiskClass.READ
    if name.startswith(("rag_", "skill_", "window_", "process_list",
                        "system_info", "log_", "context_", "chart_",
                        "analyze_", "read_", "web_", "sys_")):
        return RiskClass.READ
    return RiskClass.EXTERNAL


# 显示用等级（供系统提示分组，保持与旧 TOOL_TIER 分组一致）
# 风险档 → 显示等级
_RISK_TO_TIER = {
    RiskClass.READ: "auto",
    RiskClass.WRITE_LOCAL: "semi",
    RiskClass.EXEC: "manual",
    RiskClass.EXTERNAL: "manual",
}
# 档位严格度（校验「覆盖只能更严」用）
_TIER_ORDER = {"auto": 0, "semi": 1, "manual": 2}


def tier_of(name):
    """返回工具显示等级：'auto' | 'semi' | 'manual'（默认 manual）。

    v4.171.0：档位覆盖不再单独存一张表，直接读 `RISK_MAP` 里同一条声明。
    """
    risk, tier, _hard = _policy(name)
    if tier:
        return tier
    return _RISK_TO_TIER[classify(name)]


# v4.169.0（审查 P0-3）：**任何模式都必须人工确认**的硬档。
#
# 这组工具的共同点：**要么让可执行指令落盘（技能），要么删持久数据**，
# 误触代价不可逆。所以单立一档，不受 mode（含 auto）、session trust、
# auto_allow 白名单影响 —— 用户必须亲手点。
#
# v4.171.0：改为**从 RISK_MAP 推导**（声明里第三列为 True 的那些）。
# 原来是一份手抄的第二名单 —— 加一个硬确认工具要改两处，迟早漏一处。
ALWAYS_CONFIRM = frozenset(n for n in RISK_MAP if _policy(n)[2])


def validate_policy():
    """自检工具策略表，返回问题清单（空 = 没问题）。

    合并成一张表之后，这些以前会**静默生效**的问题现在能一次性查出来：
      · 档位值非法；
      · **覆盖档位比默认更松** —— 说明风险类定错了（该改风险类，
        而不是用档位覆盖放松），否则又是一个"标了却没生效"；
      · 只读工具被标硬确认（只读不该要求人点）。
    """
    problems = []
    for n in RISK_MAP:
        risk, tier, hard = _policy(n)
        if risk not in _RISK_TO_TIER:
            problems.append(f"{n}: 未知风险类 {risk!r}")
            continue
        default = _RISK_TO_TIER[risk]
        if tier is not None:
            if tier not in _TIER_ORDER:
                problems.append(f"{n}: 非法档位 {tier!r}")
            elif _TIER_ORDER[tier] < _TIER_ORDER[default]:
                problems.append(
                    f"{n}: 覆盖档位 {tier!r} 比默认 {default!r} 更松 —— "
                    f"若它其实不危险，应当改风险类，而不是用档位覆盖放松")
        if hard and risk == RiskClass.READ:
            problems.append(f"{n}: 只读工具不该标硬确认")
    return problems


# 导入时自检：有问题只记 error 日志、不抛异常（生产不能因此起不来）；
# 测试套件会硬性断言 validate_policy() 返回空。
_policy_problems = validate_policy()
if _policy_problems:
    for _pb in _policy_problems:
        log.error("工具策略表有问题：%s", _pb)


def grouped_tools():
    """按显示等级（auto/semi/manual）分组所有已知工具，供系统提示清单使用。"""
    groups = {"auto": [], "semi": [], "manual": []}
    for n in RISK_MAP:
        groups.setdefault(tier_of(n), []).append(n)
    for k in groups:
        groups[k].sort()
    return groups
