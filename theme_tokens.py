"""theme_tokens.py —— THEME 主题 token 唯一真源（v4.216.0 从 ui.py 迁出）

依赖方向：本文件是叶子模块（只依赖标准库），ui.py / theme_qss.py /
各 panel 均从这里取 THEME；`from ui import THEME` 经 ui.py re-export 依旧成立。
搬迁为纯代码搬移，字典内容零变化。
"""

# ===== 暗色主题调色板（基于 Codex 参考设计稿浅→暗映射） =====
THEME = {
    # ---- Backgrounds (bento-console-flat-light · Google 蓝) ----
    "bg": "#F7F8FC",          # 主窗口/工作区底色
    "sidebar": "#EEF1F8",      # 侧栏底
    "surface": "#FFFFFF",
    "surface_raised": "#FFFFFF",
    "white": "#FFFFFF",         # 纯白：供 Python 上下文（QColor / _nav_icon_pixmap 参数）引用，与 QSS 的 white 关键字等价
    "card": "#FFFFFF",         # 所有浮卡/顶栏/状态条/输入框

    # ---- Panels ----
    "panel": "#FFFFFF",
    "panel2": "#F1F3F4",
    "elev": "#FFFFFF",
    "border": "#E5E7EB",
    "border_highlight": "#D2D5DA",

    # ---- Text ----
    "text": "#202124",
    "dim": "#5F6368",
    # v4.197.0 可读性一期：旧值 #9AA0A6 在白底只有 **2.6:1**（WCAG AA 需 4.5），
    # 而它承载了状态条全部文字、placeholder、角标、时间戳 —— 全项目最大的一处
    # 可读性欠账。#6B7280 = 4.8:1，仍明显浅于正文 #202124（15.9:1），层级不丢。
    # 改键不改值：THEME 键名一个不动，500+ 处引用零改动。
    "faint": "#6B7280",
    "placeholder": "#6B7280",
    "weak": "#6B7280",

    # ---- Accent (Google 蓝 #1A73E8) ----
    "accent": "#1A73E8",
    "accent_hover": "#1765CC",
    "accent_pressed": "#155BB5",
    "accent_disabled": "#A6C4F0",
    "accent2": "#A142F4",      # 强调紫（审稿类节点）
    "danger": "#EA4335",
    "ok": "#34A853",
    "warn": "#FBBC04",
    "link": "#1A73E8",

    # ---- Delivery ----
    "delivery_purple": "#A142F4",
    "magenta": "#EC4899",

    # ---- Welcome cards ----
    "card_blue_bg": "rgba(26,115,232,0.08)",
    "card_blue_icon": "#1A73E8",
    "card_green_bg": "rgba(52,168,83,0.10)",
    "card_green_icon": "#34A853",
    "card_orange_bg": "rgba(251,188,4,0.12)",
    "card_orange_icon": "#FBBC04",
    # v4.199.0：第四张卡（数字人视频）用紫，accent2 同值，不引入新色
    "card_purple_bg": "rgba(162,66,244,0.10)",
    "card_purple_icon": "#A142F4",

    # ---- Chat bubbles (浅色，蓝强调) ----
    "user_bg": "#E8F0FE",
    "user_border": "#C6DAFC",
    "user_text": "#202124",
    "asst_bg": "#FFFFFF",
    "asst_border": "#E5E7EB",
    "asst_text": "#202124",

    # ---- Tools ----
    "tool_running": "#FBBC04",
    "tool_done": "#34A853",

    # ---- Sidebar ----
    "sidebar_hover": "#E4E8F2",
    "sidebar_active": "#1A73E8",
    "sidebar_active_bar": "#1A73E8",
    "sidebar_active_text": "#FFFFFF",
    "separator": "#E5E7EB",
    "avatar_asst": "#1A73E8",
    "avatar_user": "#202124",

    # ---- Delivery labels ----
    "delivery_blue": "#1A73E8",
    "delivery_green": "#34A853",
    "delivery_orange": "#FBBC04",

    # ---- 交互状态补充 ----
    "blue_hover": "#E4E8F2",
    "border_hover": "#D2D5DA",
    "focus_glow": "0 0 0 3px rgba(26,115,232,0.15)",
    "row_hover": "#F7F8FC",
    "row_hover_alt": "#E4E8F2",
    "secondary_btn_hover": "#F1F3F4",
    "secondary_btn_press": "#E8EAED",

    # ---- 状态色（批2 从裸 hex 收编：值取代码精确值，零视觉变化，单一事实源）----
    "danger_text": "#EF4444",        # 错误/删除态红（导演台/删除按钮）
    "danger_red2": "#DC2626",        # 删除红描边（ui.py 删除按钮）
    "danger_brown": "#C0392B",       # 删除红（技能市场）
    "danger_text_dark": "#991B1B",   # 深红文字（数字分身错误态）
    "danger_bg": "#FDECEA",          # 错误态浅红底
    "danger_bg2": "#FEE2E2",         # 错误态浅红底2
    "danger_border": "#FCA5A5",      # 错误态浅红描边
    "danger_border2": "#F3B6B1",     # 错误态浅红描边2
    "danger_hover_bg": "#FECACA",    # 错误态 hover 浅红底
    "on_green": "#1B7A3D",           # 启用绿（技能/工具）
    "on_green2": "#1A7F37",          # 推荐/启用绿
    "live_green": "#22C55E",         # 在线/成功绿
    "warn_gold": "#B8860B",          # 警告琥珀（技能市场/工具）
    "warn_gold2": "#9A6700",         # 慎装琥珀（legion_ui 推荐）
    "warn_amber": "#D97706",         # 警告橙（导演台）
    "warn_orange": "#B45309",        # 警告橙（工具管理器）
    "accent_violet": "#6C5CE7",      # 技能市场强调紫
    "accent_violet_light": "#8A6FE8", # 紫高亮
    "accent_violet_dark": "#5A4BD4",  # 紫按下
    "accent_violet_bg": "#CFC8EF",   # 紫禁用底
    "accent_violet_bg2": "#F0EEFB",  # 紫标签浅底
    "text_dark": "#1A1A1A",          # 深文本
    "text_dark2": "#1F2328",         # 深文本（tooltip/HTML）
    "gray2": "#A0A0A0",              # 浅灰（行前景）
    "bg_alt": "#F6F7F9",             # 浅底（技能市场滚动区）


    # ---- 数据/特征色（批3 从裸 hex 收编；带前缀命名，表明是特征局部语义，非全局通用色）----
    "role_green": "#2E7D32",         # 角色色：大哥（绿）
    "role_pm_blue": "#1565C0",       # 角色色：项目经理（蓝）
    "role_sys_gray": "#8A8A8A",      # 角色色：系统/日志（灰）
    "role_purple": "#6A1B9A",        # 角色色：成果（紫）
    "role_wave_gray": "#555555",     # 角色色：波次分隔条（深灰）
    "role_default_gray": "#333333",  # 角色色：未知角色兜底（深灰）
    "role_alert_red": "#C62828",     # 角色色：系统警示红（失败/拦截/缺口）
    "tag_red": "#B91C1C",            # legion_ui 推荐标签：不推（红）
    "row_gray_bg": "#F3F3F3",        # legion_ui 禁用行浅灰底
    "danger_hover_dark": "#3A1F1F",  # 导演台危险按钮 hover 深红底
    "tooltip_text": "#1F2937",       # 全局 tooltip 文字（main）
    "tooltip_border": "#D5DBE3",     # 全局 tooltip 描边（main）
    "card_red_border": "#E6B0AA",    # 技能市场删除按钮浅红描边
    "card_border": "#E6E8EB",        # 技能市场卡片边框

    # ---- 导出/模板/特征色（批4 从裸 hex 收编：HTML 导出模板 + QSS 头像渐变/危险 hover/置顶/标记）----
    "tpl_asst_bubble": "#F1F3F5",  # 导出 HTML：助手气泡底
    "tpl_tool_bg": "#FAFAFA",      # 导出 HTML：工具块底
    "tpl_tool_role": "#2563EB",    # 导出 HTML：工具角色名色
    "tpl_pre_bg": "#0F172A",       # 导出 HTML：<pre> 代码块底
    "tpl_pre_text": "#E2E8F0",     # 导出 HTML：<pre> 代码文字
    "tpl_code_bg": "#F1F1F1",      # 导出 HTML：<code> 行内代码底
    "tpl_avatar_grad1": "#4E8FD9", # 头像徽标渐变浅端
    "tpl_avatar_grad2": "#2B5FA8", # 头像徽标渐变深端
    "danger_hover_red": "#FCE8E6", # 危险按钮 hover 浅红底（批量删除/删除会话）
    "pinned_color": "#F4B400",     # 置顶星标金
    "btn_delete_hover": "#B3261E", # 停止按钮 hover 深红
    "tpl_marker_ai": "#6366F1",    # 默认 AI 头像标记蓝紫
    "tpl_marker_user": "#6B7280",  # 默认用户头像标记灰
    "tpl_hl_bg": "#FFD54F",        # 关键词高亮底

    # ---- Prism 虹彩渐变 (保留兼容，新版未使用) ----
    "prism": "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #FF6B9D,stop:0.2 #C44569,stop:0.4 #F8B500,stop:0.6 #00D2FF,stop:0.8 #7B68EE,stop:1 #FF69B4)",
    "prism_soft": "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 rgba(255,107,157,0.14),stop:0.5 rgba(0,210,255,0.10),stop:1 rgba(123,104,238,0.14))",

    # ---- 字号 token（v4.182.0，DESIGN.md §3.2 层级表 · theme_qss.F 同源镜像）----
    # v4.197.0：11px → 12px。11px 在 100%/125% 缩放下笔画糊、且与 12px 仅差 1px
    # 却多占一档层级；归到 12px 后与 font_second 合并，层级表更干净。
    "font_micro": "12px",      # 微标签：角标、时间戳、状态小字（10/11px 已归一到此）
    "font_second": "12px",     # 次级：提示、次级说明、小按钮文字
    "font_body": "13px",       # 正文/控件（默认值）
    "font_input": "14px",      # 输入框/强调按钮专用（分场景归档决策）
    "font_title": "15px",      # 标题（weight>=600）
    "font_icon_btn": "16px",   # 纯图标按钮
    "font_title_xl": "20px",   # 页面大标题

    # ---- 间距 token（v4.200.0，DESIGN.md §3.3 · theme_qss.S 同源镜像）----
    # 取值来自全量实测（_qss_dup_scan 统计 ui.py + 各 panel 的 padding/margin/
    # setSpacing 裸值频次）：4/8/12/16/20/24 六档覆盖了非 0 值的绝大多数，
    # 32 虽出现 12 次但都是「区块级大留白」，另有归属，不进基座六档。
    # 类型是 int —— layout 的 setSpacing/setContentsMargins 要 int；拼 QSS 时
    # 用 theme_qss.gap() 拿带 px 的字符串，不要在调用处手写 f"...px"。
    "space_xs": 4,      # 图标与文字的贴身间隙、chip 竖向内距
    "space_sm": 8,      # chip/小控件的内距
    "space_md": 12,     # 列表行、输入框默认内距
    "space_lg": 16,     # 卡片内距（最高频）
    "space_xl": 20,     # 卡片之间、区块之间
    "space_xxl": 24,    # 页面级留白
}
