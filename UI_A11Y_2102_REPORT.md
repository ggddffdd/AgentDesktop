# UI 体验打磨报告 · v4.210.2

> 主题：**字体栈收口 / 动画减弱偏好 / 键盘焦点可见**
> 提交：`24afe95`（已推送 `origin/main`）｜日期：2026-10-04
> 一句话：三件「本来就该有、默认不会发生」的事，全部落地且**零视觉变化**。

---

## 一、做了什么

| # | 项 | 落点 | 性质 |
|---|---|---|---|
| ① | 字体栈收口 | `theme_qss.FONT_FAMILY/FONT_STACK/apply_native_font()` + `main.py` 启动调用 | 零视觉变化 |
| ② | 动画减弱偏好真生效 | 新建 `ui_motion.py`，`toast.py` 接入 | 关动画后立即到位 |
| ③ | 键盘焦点可见 | `theme_qss` 6 个 btn 族 + combo + chk 补 `:focus` 环 | 零尺寸变化（内描边） |
| ④ | 命中区复核 | 只出清单，**不动视觉** | 待拍板 |

---

## 二、三个必须记住的机制（本轮实测，旧文档是错的）

### 2.1 应用级 QSS 的 `:focus` 在本项目里一行都不生效

控件自身的 QSS 会把**整条规则**盖掉（连基础态 `border` 都盖）。本项目 5 个快照文件里
有 **106 处** `setStyleSheet` 调用 `theme_qss` 家族，几乎每个按钮都有各自的 QSS。

> 所以「统一挪到 app 级更干净」的实际效果 = **焦点提示全部消失**。
> 焦点环的唯一有效落点是 `theme_qss` 的模式函数。反证：守卫 C7。

配套两条实测结论：
- 复选框必须写 `QCheckBox::indicator:focus`；**`:focus::indicator` Qt 匹配不到**。
- `THEME["focus_glow"]`（`0 0 0 3px rgba(...)`）**不能用于 QSS** —— Qt QSS 不支持 box-shadow。

### 2.2 `styleHints().animate()` 在本 Qt 上不存在

`UI_QA_CHECKLIST.md §4` 原先给的配方在 PySide6 6.11.1 上直接 `AttributeError`
（`styleHints().accessibility()` 只暴露 `contrastPreference`）——
**这个开关此前一直是「写着但没生效」**。

真实通道是 Win32 `SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION = 0x1042)`，
已封装为 `ui_motion`（读取异常 fail-open；非 Windows 亦返回 True）。

边界：**只归零动效，不动停留时长** —— `toast.DEFAULT_MS` 属信息可读性，
关动画 ≠ 让提示一闪而过（守卫 B3）。

### 2.3 字体栈收口为什么是零视觉变化

本机系统默认族就是 `Microsoft YaHei UI`，与 token 族 `Microsoft YaHei`
在 12 / 13 / 15 / 20px 下**逐像素相同**（QImage md5 全等、`horizontalAdvance` 全等）。

收口做的不是换字体，是把「本来就一样」从**靠系统默认**变成**我们说了算** ——
换台没装雅黑 UI 的机器，以前会静默回退到别的族，现在不会。

---

## 三、判据与扰动

| 项 | 结果 |
|---|---|
| `tests/test_ui_a11y_2102.py` | **PASS=23 FAIL=0** |
| `_perturb_ui_a11y.py` | **9/9 命中**（已登记进 `tests/.perturb_manifest.txt`） |
| `_perturb_toast_204.py` | 9/9（确认改 toast 未破坏旧判据） |
| `_perturb_200.py` | 13/13 |
| `_perturb_canvas_panel.py` | 17/17 |
| 全量回归 | 90 套件全绿（重打包后 `test_frozen_smoke` 亦 188/188） |

**守卫组成**：A 组（字体栈，含真实平台子进程逐像素比对）／B 组（动效，真行为）／
C 组（焦点，真渲染聚焦-失焦像素比对）／D 组（冻结锚点盲区显式记账）。

### C1 判据被扰动逼着改了三版

值得单独记，因为每一版都「看起来在守，其实守不住」：

| 版本 | 断言 | 怎么被骗过去 |
|---|---|---|
| v1 | 文本里含 `:focus` | `_focus_ring` 返回空串 → `QPushButton:focus{}`（选择器还在、声明空了）照样绿 |
| v2 | `:focus{...}` 声明体非空 | `edit_style`/`input_style` 焦点态是**内联写死**的 border、不经过 `_focus_ring` → 「全体非空」依然成立 |
| v3（现行） | 声明体非空 **且** 真声明了 `border` **且** 选择器伪状态顺序 Qt 认 | ✓ 被 ⑦（空环）和 ⑨（顺序错）双双翻红 |

> 结论：判据扫的必须是**意图**，不是字符。

---

## 四、重点发现：冻结锚点的覆盖率幻觉

`tests/test_tokens_200.py` 的 B1 声称在守 `theme_qss` 的交互态。
但本轮改了 6 个 btn 函数后 **B1 依然全绿（PASS=46 FAIL=0）**。

深挖量化：

- 5 个快照文件里含 `theme_qss` 调用的 `setStyleSheet` 共 **106 处**；
- 其中**只有 3 处**进得了快照；
- 原因：`_qss_scan_lib.parse_calls` 只求值顶层 `X.setStyleSheet(<可静态求值表达式>)`，
  而 `from theme_qss import btn_primary` + `b.setStyleSheet(btn_primary())` 里
  `btn_primary` 不在 env → **整条从快照消失**。

即：**锚点对 theme_qss 交互态的覆盖率约 3%**。这正是「判据边界 = 故障边界」的同款病。

处置：套件里用 D1/D2 两条**显式记账**（D1 断言快照里没有 `QPushButton:focus`，
把这个盲区钉成已知事实；D2 声明该盲区由 C1 补位），而不是假装它不存在。

---

## 五、进包核验

| 项 | 结果 |
|---|---|
| 构建 | `BUILD_EXIT=0`，robocopy 同步 dist 成功 |
| exe mtime | `2026-10-04 01:23`，晚于最后一次源码提交 `2026-10-03 22:35` ✓ |
| `_verify_pyz_v42102.py` | **26/26 PASS** |
| 冻结冒烟 | `test_frozen_smoke.py` **188/188** |
| 源码密钥扫描 | 0 命中 |
| **产物**密钥扫描 | 0 命中（解 PYZ **2305** 模块 / **411244** 条字符串常量） |

进包核验的关键几条：

- `ui_motion`（本轮唯一新模块）**真在 PYZ 模块表里** —— 它是 `toast.py` 的顶层 import，
  静态分析能顺到，但「会不会被收进去」只能核验；漏了的表现是打包后一开程序就崩。
- `main` 里 `apply_native_font` **真被调用**（co_names 断言）——「定义存在 ≠ 落地」。
- **入口脚本 `main` 不在 PYZ，在 PKG(CArchive)** —— 这一点上一版核验脚本没暴露，
  因为上版没碰 `main`。本轮补上 `CArchiveReader` 通道 + 同样做字节码指纹。
- 逐模块字节码指纹一致（源码 `compile()` vs PYZ 抽出的 code object）。

### 密钥扫描的规则修正

首轮扫描报 **20 处命中**，全是假阳性：numpy 的 git commit hash、pydantic 的版本校验 hash、
pygments 的 Cocoa 类名（`UIViewControllerInteractiveTransitioning`）、
docx 的 XML 命名空间（`org/drawingml/2006/wordprocessingDrawing`）——
它们全长成「40 位字母数字」。

> **「扫出一堆噪音」比「扫不出来」更危险** —— 真泄露会被埋在噪音里没人看。
> 已把泛规则改成**必须带赋值上下文**（`aws_secret_access_key=...` /
> `secret_key=...` 之类），并把 `sk-<全小写连字符>`（SPDX 许可证标识符）列入白名单。
> 收紧后：0 命中。

---

## 六、待拍板：命中区存量

静态扫描口径（`setFixedHeight` / `setFixedSize(_,h)` / `setMinimumHeight` / QSS `min-height`
的固定值 <40px 且变量名像可点控件；已剔除纯展示的 `*_lbl` / `logo_icon`）：

> **83 处 <40px，其中 13 处 <32px。**

最该修的一批：

| 高度 | 位置 | 控件 |
|---|---|---|
| 16px | `toast.py:129` | 关闭按钮 |
| 18px | `ui.py:2401` / `ui.py:2412` | 重试 / 清空 |
| 22px | `ui.py:5758` | 删除 |
| 24px | `director_chat.py:149` | 展开 |
| 26px | `automation_panel.py:416/423`、`ui.py:1644` | 编辑 / 删除 |
| 28px | `director_panel.py:2497`、`ui.py:3402`、`ui.py:7343` | 删除 / 模型下拉 / 折叠 |
| 30px | `skill_market_ui.py:364`、`ui.py:8040` | 安装 / API Key 显隐 |

另有 3 处 `logo_icon` 未接 `clicked`（是纯展示却被画成按钮）。

⚠️ 改这些动的是**实际视觉尺寸**，按项目约定属「改样式」范畴，**需单独拍板**后再动，
不随本轮零视觉变化的收口一起改。

---

## 七、副产品：修掉一处存量文档不一致

`README.md` 的版本号此前停在 `v4.210.0`，未跟随 `config.py` 的 `v4.210.1`。
三套判据同时在报（`test_bugfix_2092` C3 / `test_frozen_smoke` / `test_p0p1_runcmd_fix_186` F5b）。
本轮一并跟到 `v4.210.2`。

---

## 八、留档与不入库

- **入库**：`_perturb_ui_a11y.py`（判据的疫苗，按 `.gitignore` 的 `!_perturb_*.py` 例外）。
- **不入库（按 `.gitignore` 的 `_*` 规则）**：`_verify_pyz_v42102.py`、`_scan_exe_secrets.py`
  留本地作一次性核验工具。若希望长期保留这两个（尤其产物密钥扫描器），
  需改 `.gitignore` 或改用非下划线命名 —— **属策略决定，未擅自改**。
