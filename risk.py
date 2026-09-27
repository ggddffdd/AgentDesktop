# -*- coding: utf-8 -*-
"""风险分类模块（v4.50，借鉴 andrewyng/openworker 的 risk.py 设计）

把每个工具按「副作用」归到 4 个风险档：
- READ        : 只读/观察，无副作用（搜索、读文件、截图、查询…）
- WRITE_LOCAL : 改本地文件或本地状态，不外发（写文件、生图、记记忆、数据库写…）
- EXEC        : 跑代码/命令、控制桌面应用（run_python、鼠标键盘、窗口/进程…）
- EXTERNAL    : 触碰外部服务/网络（发邮件、webhook、MCP…）

这是权限引擎（permissions.py）的唯一事实来源；旧 TOOL_TIER 已迁移到此。
"""

from enum import Enum


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
    "schedule": RiskClass.WRITE_LOCAL,
    "write_file": RiskClass.WRITE_LOCAL,
    "db_insert": RiskClass.WRITE_LOCAL,
    "db_update": RiskClass.WRITE_LOCAL,
    "db_delete": RiskClass.WRITE_LOCAL,
    "skill_install": RiskClass.WRITE_LOCAL,
    "create_skill": RiskClass.WRITE_LOCAL,  # v4.84(热修15·B)：模型自动建技能→落待审核目录，仅本地写、无外发，解除外部白名单拦截
    "create_automation": RiskClass.WRITE_LOCAL,  # v4.89：建自动化任务→写本地 automation_tasks.json，仅本地写、无外发
    "delete_automation": RiskClass.WRITE_LOCAL,  # v4.89：删自动化任务，仅本地写
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


def classify(name):
    """返回工具风险档，未知工具默认 EXTERNAL（最严格、需确认）。"""
    if name in RISK_MAP:
        return RISK_MAP[name]
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
# 个别覆盖：保持旧 manual 标签（写文件/定时/数据库写/装技能 仍标「手动」）
_TIER_OVERRIDE = {
    "write_file": "manual",
    "schedule": "manual",
    "db_insert": "manual",
    "db_update": "manual",
    "db_delete": "manual",
    "skill_install": "manual",
}


def tier_of(name):
    """返回工具显示等级：'auto' | 'semi' | 'manual'（默认 manual）。"""
    if name in _TIER_OVERRIDE:
        return _TIER_OVERRIDE[name]
    return _RISK_TO_TIER[classify(name)]


# v4.169.0（审查 P0-3）：**任何模式都必须人工确认**的硬档。
#
# 背景：`_TIER_OVERRIDE` 把几个工具标成 manual（意图＝"必须人工确认"），但
# permissions.decide 的 auto 模式分支原本只看 risk 不看 tier：
#     if self.mode == "auto":
#         if risk == RiskClass.EXEC: 需确认
#         return 允许                     # ← WRITE_LOCAL 全放行
# 于是那份 override 在 auto 模式下**整套失效** —— 标了"手动"的技能安装
# 在自动模式下直接装、直接覆盖同名 SKILL.md。
#
# 这组工具的共同点：**要么让可执行指令落盘（技能），要么删持久数据**，
# 误触代价不可逆。所以单独立一档，不受 mode（含 auto）、session trust、
# auto_allow 白名单影响 —— 用户必须亲手点。
ALWAYS_CONFIRM = frozenset({
    "skill_install",      # 装技能（可能覆盖同名 SKILL.md）
    "create_skill",       # 建技能（写入会被后续加载的指令文件）
    "delete_automation",  # 删自动化任务
    "db_delete",          # 删数据库记录
})


def grouped_tools():
    """按显示等级（auto/semi/manual）分组所有已知工具，供系统提示清单使用。"""
    groups = {"auto": [], "semi": [], "manual": []}
    for n in RISK_MAP:
        groups.setdefault(tier_of(n), []).append(n)
    for k in groups:
        groups[k].sort()
    return groups
