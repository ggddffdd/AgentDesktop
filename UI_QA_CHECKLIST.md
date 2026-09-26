# 小臭玩AI · UI 交付前 QA Checklist

> 视觉系统的**唯一约束真源**见同目录 [`DESIGN.md`](./DESIGN.md)。本文件是**加面板 / 重构 UI 时的验收关卡**，逐条过完才能合入。
> 适用范围：**PySide6 / Qt 桌面原生**本体；Web 化分支（digital-twin / 视频 Agent 的 FastAPI+前端）另按 Web 范式，但第 3 节对比度与第 5 节反模式同样适用。
>
> 配色、字号、圆角、间距 token 全部来自 `ui.py` 的 `THEME` 字典（lines 90–173）。**不要在这里另起一套值**，要和 `DESIGN.md` 保持一致。

---

## 0. 何时照过（触发条件）

满足任意一条，就必须把本 checklist 走一遍：

- 新增 / 重写一个面板、对话框、浮卡、状态条、工具栏。
- 改动既有控件的配色、字体、间距、动画。
- 从 `THEME` 之外新写了一段 `setStyleSheet`（硬编码十六进制）。
- 接手别人写的 UI PR，看不到它违反哪条。

不触发的情况：纯逻辑改动、不改任何样式字符串、只动后端。

---

## 1. 已知对比度风险点（实测，优先于泛规则）

> 基准：WCAG AA — **正文 ≥ 4.5:1**、**大字(≥18px 或 14px 粗体) / UI 控件边界 ≥ 3.0:1**。
> 下表比值用 `ui.py` 真实 `THEME` 值计算。结论已落判定，直接照做。

| 组合 | 比值 | 正文(≥4.5) | 大字/UI(≥3.0) | 结论 / 用法 |
|------|----:|----|----|------|
| `text`(#202124)  on  `bg`(#F7F8FC) | 15.17 | ✅ | ✅ | 默认正文，安全 |
| `dim`(#5F6368)  on  `bg` | 5.70 | ✅ | ✅ | 次级文字，安全 |
| `faint`/`placeholder`(#9AA0A6)  on  `bg` | **2.49** | ❌ | ❌ | ⚠️ **风险**：占位符/禁用态偏灰，连 UI 阈值都不够。仅可用于**完全非必要**的装饰，正文/标签禁用 |
| `accent` 蓝(#1A73E8)  on  `bg` | **4.24** | ⚠️ | ✅ | 卡线 4.5 下，**只能做大字/链接/图标**，不可做小号正文 |
| `accent2` 紫(#A142F4)  on  `bg` | **4.30** | ⚠️ | ✅ | 同上，仅大字/UI 控件 |
| `ok` 绿(#34A853)  on  `bg` | **2.88** | ❌ | ❌ | ⚠️ **风险**：绿色**绝不可作浅底上的文字色**，只作图标/状态点/边框 |
| `danger`(#EA4335)  on  `bg` | **3.70** | ⚠️ | ✅ | 仅可用作 UI 控件/图标/大字；小号正文禁用 |
| `warn` 黄(#FBBC04)  on  `bg` | **1.61** | ❌ | ❌ | ⚠️ **严重风险**：黄色**不可作文字色**；只作边框/图标/底色填充 |
| `WHITE`  on  `accent` 蓝 | 4.51 | ✅ | ✅ | 蓝色按钮白字，安全（临界，勿再压暗蓝） |
| `WHITE`  on  `accent2` 紫 | 4.56 | ✅ | ✅ | 紫色按钮白字，安全 |
| `WHITE`  on  `ok` 绿 | **3.06** | ⚠️ | ✅ | 绿色按钮白字**仅限大号/粗体**；小号白字绿底不达标 |
| `WHITE`  on  `danger` | **3.92** | ⚠️ | ✅ | 红色按钮白字仅限大号/粗体 |
| `WHITE`  on  `warn` 黄 | **1.71** | ❌ | ❌ | ⚠️ **严重风险**：白字黄底几乎不可见 → 黄底**必须配深色文字**(#202124) |
| `text`  on  `user_bg`(#E8F0FE 蓝卡) | 14.05 | ✅ | ✅ | 用户气泡，安全 |
| `sidebar_active_text`(白)  on  `sidebar_active`(蓝) | 4.51 | ✅ | ✅ | 侧栏选中项，安全 |

**硬规则（合入前必查）：**
- ❌ 禁止 `faint`/`ok`/`warn` 作为浅底(`bg`/`surface`/`card`)上的文字色。
- ❌ 禁止白字配 `warn` 黄底（#FBBC04）。
- ⚠️ `accent`/`accent2`/`danger` 作文字色时，字号必须 ≥ 14px 粗体或 ≥ 18px；小号正文改用 `text`/`dim`。

---

## 2. 配色纪律

- [ ] 所有颜色引用 `THEME[...]`，**不硬编码十六进制**（grep 检查见 §6）。
- [ ] 新增语义色先加进 `THEME` 字典，不在局部样式里造新值。
- [ ] 状态色只用 `ok`/`warn`/`danger`/`accent`/`accent2` 五色体系，不引入第六种强调色。
- [ ] 透明叠色（如 `card_blue_bg`）须以 `rgba(...)` 叠在白卡上，且叠后文字对比度仍满足 §1。

---

## 3. 焦点可见（focus 态）—— Qt 实现

Web 的 `:focus-visible` 在 Qt 用伪状态 + 焦点策略落地：

- [ ] 每个可交互控件 `setFocusPolicy(Qt.StrongFocus)`（默认多数已是，但自定义 QWidget 容易漏）。
- [ ] 键盘 Tab 切换时**必须有可见焦点框**：QSS 写
  ```css
  QWidget:focus { border: 2px solid #1A73E8; }
  QPushButton:focus { outline: 2px solid #1A73E8; outline-offset: 2px; }
  ```
  或用 `THEME["focus_glow"]`（`0 0 0 3px rgba(26,115,232,0.15)`）做柔和光晕。
- [ ] 不要用 `setFocusPolicy(Qt.NoFocus)` 偷偷关掉焦点，除非该控件确实不可聚焦（纯展示）。
- [ ] 手测：全程只用键盘 Tab / Shift+Tab / 空格 / 回车走一遍新面板，确认每个可点元素都有高亮。

---

## 4. 动画减弱偏好（reduced motion）—— Qt 实现

尊重系统"减弱动效"设置，不强制用户看动画：

- [ ] 取系统偏好：
  ```python
  from PySide6.QtGui import QGuiApplication
  animate = QGuiApplication.styleHints().animate()   # True=允许动画
  duration = QGuiApplication.styleHints().animationDuration()  # ms
  ```
- [ ] 所有 `QPropertyAnimation` / `QVariantAnimation` 的 `duration` 在 `animate()==False` 时**置 0**（瞬间切换，不渐变）。
- [ ] 面板进出场、tooltip 淡入、气泡弹出等任何 >150ms 的过渡都要走这个开关。
- [ ] 不依赖 `QSS transition`（Qt 样式表不支持），动画一律用 `QPropertyAnimation` 以便可被开关切断。
- [ ] 手测：Windows「设置 → 辅助功能 → 视觉效果 → 动画」关掉后，新面板不再有渐变/滑动。

---

## 5. 反模式护栏（Do's & Don'ts）

> 来源：ui-ux-pro-max-skill 的跨栈反模式，已翻译为 Qt 语境。

**Don't（禁止）：**
- ❌ 在浅底上用黄色(#FBBC04)/绿色(#34A853)做文字（见 §1 实测）。
- ❌ 用纯黑 `#000000` 或纯白 `#FFFFFF` 当正文底——小臭是浅灰底 `#F7F8FC`，正文用 `text`(#202124)。
- ❌ 圆角乱跳：只用 **6 / 8 / 10px** 三档（DESIGN.md §4），不要出现 2px、14px、20px 混用。
- ❌ 重阴影拟物：小臭是扁平化，禁止 `box-shadow` 式大投影；分隔用 1px `border`(#E5E7EB)。
- ❌ 字号自由发挥：只用 **11 / 12 / 13 / 15 / 16px**（DESIGN.md §3），禁止 10px 以下、17px 以上散值。
- ❌ placeholder 用 `#9AA0A6` 还当主要提示——它对比度仅 2.49，重要提示改用 `dim` 或加图标。
- ❌ 每个面板各写一套配色/QSS，导致风格漂移（视觉真源只认 `THEME` + `DESIGN.md`）。

**Do（提倡）：**
- ✅ 新控件颜色、字号、圆角、间距**先查 DESIGN.md token**，没有就加进 `THEME` 再引用。
- ✅ 浮卡/顶栏/状态条统一 `surface`(#FFFFFF) + 1px `border`。
- ✅ 强调操作（主按钮）用 `accent` 蓝 + 白字；次级按钮用 `panel2`(#F1F3F4) 底 + `text` 字。
- ✅ 聊天/状态区分用 `user_bg`(蓝卡) / `asst_bg`(白卡) 双色体系，不新造气泡色。
- ✅ 状态点/进度用 `ok`/`warn`/`danger` 作**图标或小色块**，不当大段文字。

---

## 6. 验收方式（怎么查）

**A. 硬编码颜色 grep（合入前必跑）：**
```bash
# 在改动涉及的文件里，找出 THEME 之外的十六进制颜色
grep -nE "#[0-9a-fA-F]{6}" <改动文件.py>
# 逐条确认：要么引用 THEME["..."]，要么属于 DESIGN.md 明确允许的 token
```
> 例外：QSS 里直接写 `THEME["x"]` 引用的值、以及 `rgba(...)` 叠色（已在 §2 允许）不算违规。

**A-0. 自动化护栏（已内置，无需手动 grep）：** `build_safe.py` 在跑 PyInstaller **之前**会自动调用 `ui_hex_guard.py` 扫描全部 UI 源文件，发现「基线之外的新裸 hex」直接 `BUILD_EXIT=1` 阻断构建（fail-fast，不浪费 20 分钟打包）。当前存量（2026-09-21 审计：85 HARD + 45 SOFT = 130 处，含角色色/错误红/HTML 边框色/白字等历史代码）已登记进 `ui_hex_baseline.txt`，护栏对基线内静默、只拦**新引入**的裸 hex（新颜色→HARD 失败；THEME 内却写死→SOFT 警告）。清理掉一部分存量后，运行 `python ui_hex_guard.py --write-baseline` 重新生成基线即可逐步收紧。基线文件缺失时护栏自动降级为「仅警告」模式（绝不阻塞构建）。

**B. 对比度复核：** 新组合若不在 §1 表中，用同款 WCAG 公式算比值（脚本见 `DESIGN.md` 附录或自写 `lum()`/`ratio()`），正文 ≥4.5、UI ≥3.0 才放行。

**C. 手测三连：**
1. 键盘 Tab 走查焦点框可见（§3）。
2. 系统关动画后新面板无渐变（§4）。
3. 125% / 150% 缩放下布局不破、文字不裁切（§7）。

---

## 7. 高 DPI / 缩放

- [ ] PySide6(Qt6) 默认开启高 DPI，不要手加 `AA_EnableHighDpiScaling`（旧 Qt5 残留代码要删）。
- [ ] 布局间距用 `QSS px` token（DESIGN.md §5 间距档），**不在 Python 里硬编码 `setFixedWidth(123)` 类散值**做关键布局。
- [ ] 图标用 SVG（`lucide` 线性风）或 `QIcon`，不用固定像素位图，避免 150% 下发糊。
- [ ] 手测：Windows 显示缩放切到 125% / 150%，新面板无错位、无文字截断、无滚动条异常。

---

## 8. 触控 / 最小命中区

- [ ] 可点击元素（按钮、列表项、标签页、图标按钮）最小高 **40px**（参考 Android 48dp / 桌面 40px 实际下限）。
- [ ] 相邻可点元素间距 ≥ 8px，避免误触。
- [ ] 图标按钮也要有 `toolTip`，且命中区不小于 40×40，不能只是 16px 图标。

---

## 9. 与 DESIGN.md 的关系

- `DESIGN.md` = **设计系统说明书**（token 定义、视觉语言、Agent 约束 prompt）。
- `UI_QA_CHECKLIST.md`（本文件）= **交付关卡**（加 UI 时逐条过）。
- 二者冲突以 `DESIGN.md` 为准；若发现 checklist 漏了某条该进设计系统的规则，先补 `DESIGN.md`，再在此引用。
- 本文件不进运行时，下次 `build_safe.py` 会自动随源码同步进 dist，无需单独处理。

---

*生成依据：`ui.py` `THEME`(90–173) 实测值 + WCAG 2.1 对比度公式 + ui-ux-pro-max-skill 反模式范式（经 Qt 翻译）。最后更新：v4.161.0 时期。*
