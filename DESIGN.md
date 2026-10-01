# DESIGN.md — 小臭玩AI（deepseek-desktop）视觉系统说明书

> 本文件是小臭玩AI 桌面端（PySide6 / Qt）的**视觉真源（Single Source of Truth）**。
> 任何 AI agent 或人工改动 UI 时，必须先读此文件，照此生成样式，**禁止凭空发明配色/圆角/字号**。
> 与 `AGENTS.md`（若存在）分工：`AGENTS.md` 管"怎么造（架构/代码）"，本文件管"怎么好看（视觉语言）"。
>
> 范式参考：Google Stitch 提出的 `DESIGN.md` 9 章节结构（2026 年新范式），按小臭的 Qt 现实落地。

---

## 0. 现状与栈约束（务必先看）

- **技术栈**：纯 PySide6 桌面原生应用（非 Web）。视觉通过 `ui.py` 顶部的 `THEME` 字典（Python dict，集中色值）+ 各组件内联 `setStyleSheet(...)`（QSS 字符串）实现。
- **`qt_material` 已弃用**：历史版本用过 qt-material 换肤，现已被手写 `THEME` 字典 + QSS 取代。**源码中无任何 `apply_stylesheet(` 调用，也无 `import qt_material`**。不要在改造时重新引入 qt-material，除非明确要替换整条样式链。
- **深浅主题**：`THEME` 字典注释写着"暗色主题调色板"，但**实际值是浅色（Light）基底**（bg `#F7F8FC`、surface `#FFFFFF`）。这是历史遗留的注释矛盾——**当前落地为浅色，深色为未实现**。凡改动 UI 一律以"浅色基底"为准，不要再被旧注释误导。
- **两套字体栈并存**：
  - 聊天渲染区（QWebEngine / `chat_web`）用 Web 字体栈（见 §3）。
  - 原生 Qt 控件（PySide6 widget）未强制 `font-family`，靠系统默认 + 显式字号；应统一收口到 §3 规定的原生栈。
- **Web 化分支继承**：`digital-twin`（数字分身）与 `video-agent`（视频 Agent）的 Web 版（FastAPI + 前端）**直接复用本文件的颜色 token 与字体/圆角规则**，把 token 平移为 CSS 变量即可，无需重新设计。
- **真源位置**：色值真源 = `ui.py` 的 `THEME` 字典；组件样式散落在各 panel 的 `setStyleSheet` 调用中（这是当前反模式，见 §7）。

---

## 1. Visual Theme & Atmosphere（气质 / 密度 / 设计哲学）

- **气质**：干净、克制、专业。Google Material 衍生风——浅底、白卡、蓝强调，低饱和、无重阴影。
- **密度**：中高密度（信息密集的生产力工具，非营销落地页）。适合多面板 + 长列表。
- **设计哲学**：
  - 用**边框 + 圆角**而非阴影区分层级（扁平化）。
  - 单一主强调蓝（`#1A73E8`），第二强调紫（`#A142F4`）仅用于"审稿/交付"类特殊节点，不滥用。
  - 状态色只来自 danger/ok/warn 三色，不与品牌色混淆。
  - 中文优先，字体清晰可读，不做花哨特效。

---

## 2. Color Palette & Roles（语义色名 + hex + 角色）

> 规则：**所有颜色必须引用 `THEME[key]`，禁止在业务代码里写裸 hex**。下表 `key` 即 `THEME` 字典的键。

### 2.1 中性 / 表面

| 角色 | key | hex | 用途 |
|------|-----|-----|------|
| 工作区底 | `bg` | `#F7F8FC` | 主窗口背景 |
| 侧栏底 | `sidebar` | `#EEF1F8` | 左侧导航栏背景 |
| 卡片/表面 | `surface` / `card` / `panel` | `#FFFFFF` | 浮卡、顶栏、状态条、输入框背景 |
| 次级面板 | `panel2` | `#F1F3F4` | 嵌套面板底 |
| 边框 | `border` | `#E5E7EB` | 卡片/输入框描边 |
| 边框高亮 | `border_highlight` | `#D2D5DA` | hover 描边 |
| 分隔线 | `separator` | `#E5E7EB` | 区域分隔 |

### 2.2 文本

| 角色 | key | hex | 用途 |
|------|-----|-----|------|
| 主文 | `text` | `#202124` | 标题、正文 |
| 次文 | `dim` | `#5F6368` | 说明、副标题 |
| 弱文 | `faint` / `placeholder` / `weak` | `#6B7280` | 占位符、角标、禁用态 |

> **§2.2.1 弱文色对比度（v4.197.0，硬性）**
> 旧值 `#9AA0A6` 在白底只有 **2.64:1**，远低于 WCAG AA 正文要求的 **4.5:1**，
> 而它承载了状态条全部文字、placeholder、角标、时间戳 —— 全项目最大的一处可读性欠账。
> 现统一为 `#6B7280`（**4.83:1**），仍明显浅于主文 `#202124`（16.1:1），层级不丢。
> **改色时不能用"比原来深一点"当判据，必须实算对比度** ——
> `#A0A0A0`（2.61:1）、`#8E8E8E`（3.28:1）看起来都"够深"，其实都不达标。
> 机器守卫：`tests/test_readability_197.py`（真算 WCAG 相对亮度，不是比对 hex 字符串）。

### 2.3 品牌强调

| 角色 | key | hex | 用途 |
|------|-----|-----|------|
| 主强调（蓝） | `accent` | `#1A73E8` | 主按钮、链接、激活项、焦点 |
| 主强调 hover | `accent_hover` | `#1765CC` | 按钮 hover |
| 主强调 pressed | `accent_pressed` | `#155BB5` | 按钮按下 |
| 主强调 禁用 | `accent_disabled` | `#A6C4F0` | 禁用按钮 |
| 第二强调（紫） | `accent2` / `delivery_purple` | `#A142F4` | 审稿/交付类特殊节点 |
| 品红 | `magenta` | `#EC4899` | 交付相关点缀 |
| 链接 | `link` | `#1A73E8` | 文本链接（同主蓝） |

### 2.4 语义状态

| 角色 | key | hex | 用途 |
|------|-----|-----|------|
| 危险 | `danger` | `#EA4335` | 删除、错误、警示 |
| 成功 | `ok` | `#34A853` | 完成、通过 |
| 警告 | `warn` | `#FBBC04` | 进行中、待确认 |
| 工具运行 | `tool_running` | `#FBBC04` | 工具执行中 |
| 工具完成 | `tool_done` | `#34A853` | 工具执行完成 |

### 2.5 聊天气泡

| 角色 | key | hex |
|------|-----|-----|
| 用户气泡底 | `user_bg` | `#E8F0FE` |
| 用户气泡边 | `user_border` | `#C6DAFC` |
| 用户文字 | `user_text` | `#202124` |
| 助手气泡底 | `asst_bg` | `#FFFFFF` |
| 助手气泡边 | `asst_border` | `#E5E7EB` |
| 助手文字 | `asst_text` | `#202124` |

### 2.6 焦点 / 侧栏 / 头像

- 焦点辉光：`focus_glow` = `0 0 0 3px rgba(26,115,232,0.15)`（所有可聚焦控件 hover/focus 时显示蓝环）
- 侧栏 hover：`sidebar_hover` `#E4E8F2`；激活项底 `sidebar_active` `#1A73E8` + 字 `sidebar_active_text` `#FFFFFF` + 左边条 `sidebar_active_bar` `#1A73E8`
- 头像渐变（蓝）：`#4E8FD9 → #2B5FA8`（圆形，圆角 = size/2）
- 助手头像底 `avatar_asst` `#1A73E8`；用户头像底 `avatar_user` `#202124`

### 2.7 欢迎卡（专用，勿外溢）

| key | 值 | 用途 |
|-----|-----|------|
| `card_blue_bg` | `rgba(26,115,232,0.08)` | 蓝卡背景 |
| `card_blue_icon` | `#1A73E8` | 蓝卡图标 |
| `card_green_bg` | `rgba(52,168,83,0.10)` | 绿卡背景 |
| `card_green_icon` | `#34A853` | 绿卡图标 |
| `card_orange_bg` | `rgba(251,188,4,0.12)` | 橙卡背景 |
| `card_orange_icon` | `#FBBC04` | 橙卡图标 |

### 2.8 渐变（预留 / 兼容，新版默认不用）

- `prism`：虹彩线性渐变（FF6B9D→C44569→F8B500→00D2FF→7B68EE→FF69B4）——**当前未使用**，仅保留兼容。
- `prism_soft`：同色系低透明软渐变——**当前未使用**。

---

## 3. Typography Rules（字体族 + 完整层级表）

### 3.1 字体族

- **Web 渲染区（QWebEngine 聊天）实际栈**：
  `-apple-system, "Segoe UI Variable", "Segoe UI", "Microsoft YaHei", system-ui, sans-serif`
- **原生 Qt 控件（规定收口）**：统一 `"Microsoft YaHei", system-ui, sans-serif`。
  - ⚠️ 现状：原生控件未强制 font-family，靠系统默认。所有新增/改造控件**必须**显式设 QFont 为该栈，避免中英混排字体不一致。

### 3.2 字号层级（基于现有 setStyleSheet 实测）

| 层级 | 字号 | weight | 用途 |
|------|------|--------|------|
| 微标签 | 12px | 400 | 角标、时间戳、状态小字（v4.197.0：11px → 12px，与次级合并同档） |
| 次级 | 12px | 400 | 提示、滚动条标签、次级说明 |
| 正文/控件 | 13px | 400/500 | **默认值**：正文、按钮文字、输入框 |
| 标题 | 15px | 600 | 区块标题、输入框文字、列表主项 |
| 图标按钮 | 16px | 600 | 纯图标按钮（无文字） |
| 页面大标题 | 20px | 700 | 页面级大标题（v4.182.0 收编 9 处 → `THEME["font_title_xl"]` / `theme_qss.label_title_xl`） |

- 行高：正文 `line-height: 1.6`（Web 区）；原生 QLabel 默认即可。
- **14px 专用值**：仅限主输入框与强调按钮（2026-09-30「分场景归档」决策），token `theme_qss.F["input"]`；标题类（weight≥600）一律归 15px。
- **豁免字号**（不收层级表）：17px=附件胶囊（radius:17 配套）；22px=HTML 导出模板（Web 区）；28px=欢迎页大标题（展示型特例）。

**§3.2.1 字号下限与 token 同源（v4.197.0，硬性）**

1. **页面内任何可见文字不得小于 12px。** 11px 在 100%/125% 系统缩放下笔画糊、中英混排尤甚；
   且它只比次级小 1px，多占一档层级却换不来区分度 —— 已与次级合并同档（全项目 41 处归一）。
2. **`ui.THEME['font_*']` 与 `theme_qss.F[...]` 是手写镜像的两份，必须同值。**
   改字号时只改一处不会报错，只会让某些面板悄悄小/大 1px —— 这是最难查的一类漂移。
   机器守卫：`tests/test_readability_197.py`（AST 取出两份字面量字典逐键比对，不 import Qt）。
3. 提高字号时**必须同步检查所在容器的固定高度**：`min-height:12px` 装 12px 字会被裁底
   （本轮 `status_label` 就是这么踩到的，已一并抬到 16px）。

---

## 4. Component Stylings（组件 + 状态）

> 以下为当前各组件内联 QSS 的实测收敛值。改造时**统一引用 `THEME[key]`**，不得改写裸值。

### 4.1 按钮

- **主按钮**：`background:accent; color:#FFFFFF; border:none; border-radius:10px; padding:8px 20px; font-size:13px; font-weight:600;`
  - hover → `accent_hover`；pressed → `accent_pressed`；disabled → `accent_disabled`
- **次级按钮**：`background:transparent; border:1px solid border; border-radius:8px; padding:0 14px; font-size:13px;`
  - hover → `secondary_btn_hover` `#F1F3F4`；pressed → `secondary_btn_press` `#E8EAED`
- **图标按钮**：`background:transparent; border:none; font-size:16px; font-weight:600; border-radius:6px;`

### 4.2 输入控件（QLineEdit / QTextEdit / QComboBox）

- `border:1px solid border; border-radius:8px; padding:8px 10px; font-size:13px; color:text;`
- 占位符 `placeholder` `#6B7280`（v4.197.0，见 §2.2.1）
- 焦点：`border:1px solid accent` + `focus_glow` 蓝环

### 4.3 卡片 / 面板

- 背景 `card` `#FFFFFF`；`border:1px solid border; border-radius:8px;`
- 标题 `text` 15px/600；说明 `dim` 13px/`line-height:1.6`

### 4.4 列表行 / 导航项

- 默认透明；hover → `row_hover` `#F7F8FC`（或 `sidebar_hover` `#E4E8F2` 在侧栏）
- 激活项：侧栏 `sidebar_active` `#1A73E8` 底 + 白字 + 左侧 `sidebar_active_bar` 蓝条

#### 4.4.1 导航分组（v4.198.0 新增·硬性）

- 导航 10 项分三组：**工作**（对话/编排/军团）、**创作**（生图/生视频/数字人/导演台）、
  **系统**（工具/任务/设置）。组间距 10px，组标题 `faint` + `font_micro` + `padding-left:16px`。
- **铁律：分组是纯视觉的，绝不能改变 `nav_defs` 的顺序。**
  因为 `nav_defs` 顺序 == `main_stack` 页面顺序（`_switch_nav(idx) -> setCurrentIndex(idx+1)`）。
  按「好看」重排 = 军团页静默串页。
- 实现方式：分组写成模块级常量 `NAV_GROUPS = ((组名, (成员...)), ...)`，
  渲染时只决定「在哪一项前插组标题」，`enumerate` 出来的 `i` 不受影响。
- 新增导航项必须改**两处**：`nav_defs` 插项 + `NAV_GROUPS` 加成员。
  只改一处 → `tests/test_nav_ia_198.py` A4/A5/A6 判红（A4 抓顺序、A5 抓遗漏、A6 抓多余）。

### 4.5 状态指示 / 标签

- 小标签：`border-radius:6px; padding:2px 6px; font-size:12px;`（危险 `danger` / 成功 `ok` / 警告 `warn` / 紫 `accent2`）
- 工具状态点：`tool_running` `#FBBC04`（黄）/ `tool_done` `#34A853`（绿）

#### 4.5.1 底部状态条（v4.198.0 修订·硬性）

- 高度 **28px**（原 24px 装不下 12px 字 + chip 内边距，字会被上下裁）。
- **状态条只放状态**：连接状态、任务状态条、计费信息。
  **不放操作说明** —— 「Enter 发送 · Shift+Enter 换行」已移入输入框 placeholder
  （用户视线在输入框，说明放那儿才有用；放状态条最右端等于没有）。
- 计费信息（要不要花钱）用 **chip 样式**抬出来：`bg` 底 + `border` 边 +
  `border-radius:10px` + `padding:3px 10px`，字色用 `dim` 而不是 `faint`。
  不能和「● 已连接」一个灰字样式混在一起。

---

## 5. Layout Principles（间距 / 网格 / 留白）

- **现状问题**：间距散落在每个 `setStyleSheet` 的 `padding` 里（8px 10px / 8px 20px / 0 14px / 12px 0 等），**无统一间距 token**。这是反模式，改造时应立规矩：

| token | 值 | 用途 |
|-------|-----|------|
| xs | 4px | 紧凑间隙、角标内边距 |
| sm | 8px | 控件内边距、行间隙 |
| md | 12px | 区块内边距、卡片 padding |
| lg | 16px | 面板 padding、区块间距 |
| xl | 20px | 主按钮横向 padding、大间隙 |

- **建议**：新增布局统一用上述 4 的倍数（8pt 网格精神），不再写任意 px。
- **留白哲学**：卡片内 padding ≥ 12px；区块之间 ≥ 16px；不挤不空，信息密集但不压迫。

---

## 6. Depth & Elevation（阴影 / 表面层级）

- **现状**：扁平化。几乎无 `box-shadow`，层级靠 `1px border` + `border-radius` + 底色深浅区分。
- **焦点态**是唯一的"辉光"：`focus_glow` = `0 0 0 3px rgba(26,115,232,0.15)`。
- **规定**：
  - 不要用重阴影堆叠层数（会破坏"干净克制"气质）。
  - 浮层（弹窗/下拉）可用一层极淡阴影（`0 4px 16px rgba(0,0,0,0.08)`）做轻微抬升，但不强制。
  - 表面层级顺序：bg（最底）< panel2 < card/surface（白卡浮起）< 焦点/激活项（蓝）。

---

## 7. Do's and Don'ts（反模式护栏）

### DO
- ✅ 所有颜色引用 `THEME[key]`，不写裸 hex。
- ✅ 新增/改造控件显式设字体族 `"Microsoft YaHei", system-ui, sans-serif` 与 §3.2 字号。
- ✅ 圆角只用三档：6px（小）/ 8px（标准）/ 10px（大）。
- ✅ **矩形元素**（卡片 / 面板 / 按钮 / 标签 / 输入框）严格遵守上面三档。
- ✅ **圆形与胶囊是一类独立造型，不套三档**（见下方 §7.1 豁免清单）。
- ✅ 状态色只用 danger/ok/warn，品牌色只用 accent/accent2。
- ✅ 中文 UI 一律中文文案，字体清晰。

### DON'T
- ❌ **在业务代码里硬编码 hex**（现状 §4/§5 散落大量裸值，是待清理的反模式，不要新增）。
- ❌ 被 `THEME` 字典顶部"暗色主题"注释误导——**当前是浅色基底**。
- ❌ 重新引入 `qt_material` 而不先移除 `THEME` 整条链。
- ❌ 引入超出 accent/accent2/danger/ok/warn/magenta 之外的新颜色（除非经本文件评审追加）。
- ❌ 圆角/间距"随手写"——不遵守三档圆角与 §5 间距 token。
- ❌ **把胶囊/圆形按三档"归正"**（会把发送/停止按钮压成圆角方块、头像变方，
     是事故级视觉回退——改前先查 §7.1 豁免清单）。
- ❌ 用重阴影做层级（破坏扁平克制气质）。
- ❌ 把 Web 版（HTML/CSS）的 DESIGN.md 方法直接套到 Qt 原生控件——Qt 用 QSS/THEME，不是 CSS 变量。

---

### 7.1 圆角豁免清单（v4.181.1 确立）

三档圆角管的是**矩形元素**。以下三类不套三档，**任何"圆角归一"脚本或改动都必须跳过**：

| 类别 | 规则 | 现存实例 |
|---|---|---|
| **圆形** | `radius = 尺寸 ÷ 2` | 头像（`{size // 2}px` 动态）、6×6 状态点 `3px`、8×8 状态点 `4px`、26px 缩略图 `13px` |
| **胶囊** | `radius = 高度 ÷ 2` | 34px 发送/停止/录音按钮 `17px`、48px 圆形按钮 `24px`、44px 徽章 `22px`、36px 搜索框 `18px`、32px 输入卡 `16px`、28px 步骤徽章与**聊天气泡** `14px`、自动化 badge `9px` |
| **滚动条** | §10 明文：细滚动条宽 6px、**圆角 3px** | `ui.py` thumb `3px` / handle `4px`；`chat_web.py` thumb `5px` |

**依据**：§2 头像条目已写明「圆形，圆角 = size/2」；§10 明文规定滚动条圆角。
**代价**：误把胶囊归成 6/8/10 会把按钮压成圆角方块、头像变方，是事故级视觉回退。

**误改守卫**：`tests/test_radius_norm.py` 会扫描全项目圆角，
断言「矩形元素不得出现三档外的值」，同时断言上述豁免项**保持在豁免值**。

---

## 8. Responsive Behavior（Qt 桌面适配）

- **无传统 Web 断点**。桌面窗口为主，关键适配点：
  - **高 DPI**：程序应启用 `Qt.AA_EnableHighDpiScaling`（Qt6 默认开），QSS 用 px 即可自动缩放。
  - **最小窗口**：建议主窗口 min width ≥ 960px；窄于该值时侧栏可折叠为图标。
  - **侧栏折叠（v4.198.0 已落地）**：窗口宽度 `< NAV_COLLAPSE_WIDTH(1100)` 自动收成
    64px 图标栏（`NAV_COLLAPSED_W`），≥ 该值恢复 256px（`NAV_EXPANDED_W`）。
    纯视觉切换：不动页面栈、不动导航下标。实现要求两条 ——
    ① 必须有**状态短路**（`_nav_collapsed` 比对），否则拖动边框每帧重排会卡；
    ② 折叠时**文字必须隐藏**（组标题 / 导航文字 / Logo 名 / 用户名），
    否则 64px 栏里文字被裁。守卫见 `tests/test_nav_ia_198.py` B 组、D 组。
  - **触控目标**：可点击控件最小 28–32px 高（按钮 padding 已满足）；图标按钮至少 32×32。
  - **滚动条**：细滚动条样式（宽 6px、圆角 3px、轨道 `rgba(148,163,184,0.25)`，见 ui.py 实测），保持克制。
  - **折叠策略**：面板（编排页/对话页）按功能切换，日常只面对对话页，不堆所有面板于同一屏。

---

## 9. Agent Prompt Guide（给 AI 改 UI 的快速约束）

> 当让 AI agent（含 WorkBuddy 自身）修改小臭 UI 时，把下面这段作为 prompt 前缀注入：

```
你是小臭玩AI（PySide6 桌面端）的 UI 改造助手。视觉规则见仓库根 DESIGN.md，必须严格遵守：
1. 颜色只从 ui.py 的 THEME 字典取 key，绝不写裸 hex。
2. 圆角只用 6/8/10px 三档；间距只用 4/8/12/16/20px。
3. 字体族 "Microsoft YaHei", system-ui, sans-serif；字号 11/12/13/15/16px。
4. 状态色仅 danger(#EA4335)/ok(#34A853)/warn(#FBBC04)；品牌色仅 accent(#1A73E8)/accent2(#A142F4)。
5. 当前为浅色基底（bg #F7F8FC，surface #FFFFFF），不要改成深色。
6. 不要引入 qt_material；不要加重阴影。
7. 改动后自检：是否引用了 THEME key？是否守住三档圆角？中文文案是否清晰？
```

---

## 附：与 Web 化分支的视觉继承

- `digital-twin`（数字分身）与 `video-agent`（视频 Agent）的 **Web 版**直接复用本文件的颜色 token：把 §2 的 `key→hex` 平移为 CSS 变量（如 `--accent: #1A73E8`），字体栈复用 §3.1 Web 栈，圆角/间距复用 §4/§5。
- 这样桌面端（Qt）与 Web 端共享同一套视觉语言，跨端一致。

---

*本文件由视觉现状（`ui.py` THEME 字典 + 内联 QSS）抽取整理而成，作为小臭视觉唯一真源。改动视觉后请同步更新本文件对应章节。*
