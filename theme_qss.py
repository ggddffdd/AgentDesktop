# -*- coding: utf-8 -*-
"""theme_qss.py —— QSS 中心样式层（v4.182.0，Task #818）

把散落在 ui.py / director_panel.py 等文件里的重复 setStyleSheet 模式收敛为
**模式函数**：调用方一行拿到带交互态（hover/pressed/disabled/focus）的完整 QSS，
颜色/圆角/字号全部引用 THEME token，禁止裸 hex。

设计约束（第三档决策，2026-09-30 大哥拍板）：
1. **视觉零变化**：本层函数的默认输出与既有内联 QSS 逐字符等价（唯一例外：
   字号归一映射，见 fs() 说明）。
2. **保交互态**：所有函数输出必须含 hover / disabled / focus 态——收编时
   交互态丢失 = 视觉回归，release_check 门禁会拦。
3. **字号层级**（DESIGN.md §3.2）：
   11 微标签 / 12 次级 / 13 正文·控件（默认）/ 15 标题 / 16 图标按钮 /
   20 页面大标题（本次新增 font_title_xl）。
   豁免：14px = 输入框/强调按钮专用值（保留，入 token）；17/22/28 = 胶囊/
   Web 模板/欢迎页展示型特例，不收。
4. **依赖方向**：本文件只 import ui（拿 THEME），不被 ui.py 导入——
   任何文件 `from theme_qss import ...` 都不会造成循环依赖。
   （onboarding.py 的 THEME 是 main.py 传参注入的，与本层无关。）

用法示例：
    from theme_qss import btn_primary, btn_secondary, edit_style, F
    btn.setStyleSheet(btn_primary())
    edit.setStyleSheet(edit_style())
    label.setStyleSheet(f"color:{THEME['dim']};font-size:{F['body']};")
"""
import sys
import os

# 允许直接以脚本方式导入同目录 ui.py（与 director_panel.py 同套约定）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui import THEME  # noqa: E402


# ============================================================
# 字号 token（DESIGN.md §3.2 层级表 · 值均为 px 字符串，直接拼 QSS）
# ============================================================
F = {
    # v4.197.0：11px → 12px（与 ui.py THEME['font_micro'] 同源，两处必须同步）。
    # 11px 在 100%/125% 缩放下笔画糊，且只比 second 小 1px 却多占一档。
    "micro":    "12px",   # 微标签：角标、时间戳、状态小字
    "second":   "12px",   # 次级：提示、次级说明、小按钮文字
    "body":     "13px",   # 正文/控件（默认值）：正文、按钮、输入框
    "input":    "14px",   # 输入框/强调按钮专用（14px 分场景归档决策）
    "title":    "15px",   # 标题（weight>=600）：区块标题、列表主项
    "icon_btn": "16px",   # 纯图标按钮
    "title_xl": "20px",   # 页面大标题（v4.182.0 新增，收编 9 处 20px）
}

# 字号归一映射（10→11 归一；14px 场景判定见 DECISION 注记）：
# 表外字号收编时用此表转换，QSS 输出值不变 Token 名不变。
FONT_NORMALIZE = {
    "10px": F["micro"],   # 10px → 12px（9 处微标签归一）
    "11px": F["micro"],   # 11px → 12px（v4.197.0：微标签不再小于 12px）
}


# ============================================================
# 间距 token（DESIGN.md §3.3 · v4.200.0 立）
# ============================================================
# 与 F 的关键差别：**F 是手写镜像，S 是从 THEME 派生**。
# v4.197.0 踩过一次「ui.py 和 theme_qss.py 两份手写字典不同步」，字号那一层
# 只能靠探针事后比对；间距这层从一开始就只写真源（ui.py THEME），本层取派生值，
# 结构上不可能漂移 —— 探针只需断言派生关系成立即可。
# ⚠️ 本段的改动很容易被误恢复：theme_qss.py 相对 HEAD 一直有新内容，
#    任何 `git checkout HEAD -- theme_qss.py` 都会把它连根拔掉。
_SPACE_KEYS = ("xs", "sm", "md", "lg", "xl", "xxl")
S = {k: THEME["space_" + k] for k in _SPACE_KEYS}


def gap(key: str) -> str:
    """取间距 token 的 QSS 形式：gap('lg') → '16px'。

    layout 的 setSpacing/setContentsMargins 要 int → 用 S[key]；
    拼 QSS 的 padding/margin 要带单位 → 用 gap(key)。不要在调用处手写
    f"{16}px"，那等于又造了一份字面量。
    """
    return f"{S[key]}px"


def fs(size_key: str) -> str:
    """取字号 token。fs('body') → '13px'。拼 QSS 用，不传裸数字。"""
    return F[size_key]


# ============================================================
# 字体权重 token
# ============================================================
W = {
    "normal": 400,
    "medium": 500,
    "semibold": 600,
    "bold": 700,
}


# ============================================================
# 字体族 token（DESIGN.md §3.1「原生 Qt 控件（规定收口）」）
# ============================================================
# 真源只有这一份。此前全项目散着 9 处 `QFont("Microsoft YaHei", N)` 字面量，
# 有的控件设、有的控件不设（不设的吃系统默认）—— 同一面板里两种字体族，
# 正是 §3.1 点名要消灭的「中英混排字体不一致」。
FONT_FAMILY = "Microsoft YaHei"
# 完整栈（含兜底），与 DESIGN §3.1 逐字一致；Web 渲染区（QWebEngine）另有自己的栈，不在此列。
FONT_STACK = '"Microsoft YaHei", system-ui, sans-serif'


def font_family_qss() -> str:
    """取 QSS 形式的 font-family 片段：font_family_qss() → 'font-family:"Microsoft YaHei", system-ui, sans-serif;'"""
    return f"font-family:{FONT_STACK};"


def apply_native_font(app) -> None:
    """把原生 Qt 控件的字体族收口到 FONT_STACK —— **全应用唯一落点**。

    为什么用 app.setFont 而不是 app.setStyleSheet("*.{font-family:...}")：
      ① QSS 的通用选择子要在每个 widget polish 时参与匹配，启动期是净成本
         （config.py v4.152.2 那段实测过「简化 QSS 不省总时间」）；
      ② setFont 只改一次 QFont，所有未显式设族的控件自动继承。
    **只改族、不动字号**：拿现成的 app.font() 改 families，pointSize 原样保留。

    ⚠️ 为什么这是「视觉零变化」而不是一次换肤：本机（Win11 中文）系统默认族
    就是 `Microsoft YaHei UI`，与本 token 的 `Microsoft YaHei` 在 12/13/15/20px
    下**逐像素相同**（QImage+QPainter 渲染 md5 全等，见 test_ui_a11y_2102 A5）。
    换句话说，这一步不是"换字体"，是把"本来就一样的字体"从"靠系统默认"变成
    "我们说了算" —— 换台默认族不是雅黑的机器才不会悄悄跑偏。

    非 Windows / 取不到 font() 时静默跳过（不抛异常，不阻断启动）。
    """
    try:
        f = app.font()
        f.setFamilies([FONT_FAMILY, "Segoe UI", "sans-serif"])
        app.setFont(f)
    except Exception:
        pass


# ============================================================
# 模式函数：按钮族
# ============================================================
def _focus_ring(bg: str) -> str:
    """焦点环的 QSS 片段（v4.210.2）。DESIGN §2.6 的 `focus_glow` 落地形态。

    为什么不是 `focus_glow` 原样：Qt QSS **不支持 box-shadow**，那种「向外扩
    3px 的半透明辉光」画不出来，能画的只有 border。所以取等价语义：
    `border:2px solid accent`（DESIGN §4.2 给输入框的焦点写法就是 accent 描边）。

    为什么底色要说进来：**蓝底上画蓝环等于没画**。accent 实底按钮
    （btn_primary / btn_dialog(accent)）改用白环 —— 它在蓝底上对比度最高，
    也与「主按钮 = 蓝底白字」这套语言一致。

    放在本层（而不是 `QApplication.setStyleSheet` 的全局规则）是因为实测：
    **应用级 QSS 会被控件自身的 QSS 整个盖掉**（连基础态 border 都盖得掉），
    全局 `QPushButton:focus{...}` 在本项目里一行都不会生效 —— 项目里几乎每个
    按钮都有自己的 setStyleSheet。唯一有效落点就是这几行模式函数。
    """
    ring = THEME["white"] if bg == THEME["accent"] else THEME["accent"]
    return f"border:2px solid {ring};"


def btn_primary(font_size: str = "13px", weight: int = 600,
                disabled_color: str = None) -> str:
    """主按钮（accent 底白字）。

    默认参数 == ui.py `_primary_btn_style`（L3620）：13px/600/disabled=accent_disabled。
    director_panel.py `_btn_accent_style`（L187）是「强调按钮 14px」场景
    （分场景归档决策），等价调用：
        btn_primary(font_size=F["input"], weight=W["medium"], disabled_color=THEME["dim"])
    """
    dis = disabled_color or THEME["accent_disabled"]
    return (
        f"QPushButton{{background:{THEME['accent']};color:white;border:none;"
        f"border-radius:8px;padding:0 16px;font-size:{font_size};font-weight:{weight};}}"
        f"QPushButton:focus{{{_focus_ring(THEME['accent'])}}}"
        f"QPushButton:hover{{background:{THEME['accent_hover']};}}"
        f"QPushButton:disabled{{background:{dis};color:white;}}"
    )


def btn_outline() -> str:
    """线框普通按钮（card 底 + text 色 + blue_hover）。

    源自 director_panel.py `_btn_style`（L180）。与 btn_secondary 的差异：
    文字用 text（非 dim）、padding 0 12px（非 16px）、hover blue_hover
    （非 panel2 提亮）、带 disabled 态。两者语义不同，勿混用。
    """
    return (
        f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
        f"border:1px solid {THEME['border']};border-radius:8px;padding:0 12px;font-size:13px;}}"
        f"QPushButton:focus{{{_focus_ring(THEME['card'])}}}"
        f"QPushButton:hover{{background:{THEME['blue_hover']};}}"
        f"QPushButton:disabled{{color:{THEME['dim']};}}"
    )


def btn_secondary() -> str:
    """次级按钮（card 底 + border 描边）。

    合并 ui.py `_secondary_btn_style`（L3626）与 director_panel.py `_btn_style`
    （L180）：取 ui.py 版 hover（panel2 + 文字提亮 + 描边高亮），
    padding 统一 0 16px（director 版 0 12px）。
    """
    return (
        f"QPushButton{{background:{THEME['card']};border:1px solid {THEME['border']};"
        f"border-radius:8px;padding:0 16px;font-size:13px;font-weight:500;color:{THEME['dim']};}}"
        f"QPushButton:focus{{{_focus_ring(THEME['card'])}}}"
        f"QPushButton:hover{{background:{THEME['panel2']};color:{THEME['text']};"
        f"border-color:{THEME['border_highlight']};}}"
    )


def btn_danger() -> str:
    """危险按钮（card 底 + 红字，hover 深红底）。源自 director_panel.py L194。"""
    return (
        f"QPushButton{{background:{THEME['card']};color:{THEME['danger_text']};"
        f"border:1px solid {THEME['border']};border-radius:8px;padding:0 12px;font-size:12px;}}"
        f"QPushButton:focus{{{_focus_ring(THEME['card'])}}}"
        f"QPushButton:hover{{background:{THEME['danger_hover_dark']};}}"
    )


def btn_small() -> str:
    """小按钮（radius 6px + 12px 字号）。源自 director_panel.py L200。"""
    return (
        f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
        f"border:1px solid {THEME['border']};border-radius:6px;padding:0 12px;font-size:12px;}}"
        f"QPushButton:focus{{{_focus_ring(THEME['card'])}}}"
        f"QPushButton:hover{{background:{THEME['blue_hover']};}}"
        f"QPushButton:disabled{{color:{THEME['dim']};}}"
    )


def btn_dialog(bg: str = None, fg: str = None) -> str:
    """弹层按钮（radius 10px、padding 8px 20px、600 字重）。源自 ui.py ThemedDialog._btn_style（L1169）。

    默认参数即次级态（card 底）；传 accent 配色即主态（hover/pressed 随 bg 判断）。
    """
    if bg is None:
        bg = THEME["card"]
    if fg is None:
        fg = THEME["text"]
    hover = THEME["accent_hover"] if bg == THEME["accent"] else THEME["panel2"]
    pressed = THEME["accent"] if bg == THEME["accent"] else THEME["elev"]
    return (
        f"QPushButton{{background:{bg};color:{fg};border:none;border-radius:10px;"
        f"padding:8px 20px;font-size:13px;font-weight:600;}}"
        f"QPushButton:focus{{{_focus_ring(bg)}}}"
        f"QPushButton:hover{{background:{hover};}}"
        f"QPushButton:pressed{{background:{pressed};}}"
    )


# ============================================================
# 模式函数：输入控件族
# ============================================================
def edit_style() -> str:
    """QTextEdit 输入框。源自 director_panel.py `_edit_style`（L207）。"""
    return (
        f"QTextEdit{{background:{THEME['card']};border:1px solid {THEME['border']};"
        f"border-radius:10px;padding:12px 12px;font-size:13px;color:{THEME['text']};}}"
        f"QTextEdit:focus{{border:1px solid {THEME['accent']};}}"
    )


def input_style(widget: str = "QLineEdit", font_size: str = None) -> str:
    """单行输入框（QLineEdit，14px 主输入框场景传 F["input"]）。

    14px 专用值按「分场景归档」决策：主输入框保留 14px 并入 token。
    默认 13px 与 §3.2 一致。
    """
    size = font_size or F["body"]
    return (
        f"{widget}{{background:{THEME['card']};border:1px solid {THEME['border']};"
        f"border-radius:8px;padding:8px 10px;font-size:{size};color:{THEME['text']};}}"
        f"{widget}:focus{{border:1px solid {THEME['accent']};}}"
    )


def combo_style() -> str:
    """下拉框。源自 director_panel.py `_combo_style`（L213）。"""
    return (
        f"QComboBox{{background:{THEME['card']};border:1px solid {THEME['border']};"
        f"border-radius:8px;padding:0 12px;font-size:13px;color:{THEME['text']};}}"
        f"QComboBox::drop-down{{border:none;}}"
        f"QComboBox:focus{{{_focus_ring(THEME['card'])}}}"
        f"QComboBox:disabled{{color:{THEME['dim']};}}"
    )


def scroll_transparent() -> str:
    """透明无边框滚动区（v4.201.0 收口 13 处）。

    这 13 处在 3 个文件里有**两种书写顺序**（`border` 在前 / `background` 在前），
    语义完全是同一件事。收口后统一由本函数产出，属性集合与任一旧写法等价。
    """
    return "QScrollArea{border:none;background:transparent;}"


def chk_style() -> str:
    """复选框。源自 director_panel.py `_chk_style`（L220）。

    焦点环上在 **indicator 子控件**（16px 方框）而不是整个 QCheckBox：
    后者会在「方框 + 文字」外面套一个大框，看着像文本框而不是勾选框。
    ⚠️ 写法是 `::indicator:focus` 而不是 `:focus::indicator` —— 后者在
    Qt 6.11 上**匹配不到、不会画任何东西**（实测像素比对无差异）。
    """
    return (
        f"QCheckBox{{color:{THEME['text']};font-size:13px;spacing:6px;}}"
        f"QCheckBox::indicator{{width:16px;height:16px;}}"
        f"QCheckBox::indicator:focus{{border:2px solid {THEME['accent']};}}"
    )


# ============================================================
# 模式函数：标签/微标签族
# ============================================================
def _w(weight) -> str:
    """字重片段。weight 为 None → 不输出（保持调用点原样，向后兼容 102 处）。"""
    if not weight:
        return ""
    return f"font-weight:{W[weight]};"


def label_micro(color_key: str = "faint", weight: str = None) -> str:
    """微标签（12px）：角标、时间戳、状态小字。10px 表外字号归一到这。"""
    return f"color:{THEME[color_key]};font-size:{F['micro']};" + _w(weight)


def label_second(color_key: str = "dim", weight: str = None) -> str:
    """次级标签（12px）：提示、次级说明。weight 取 W 的键名。"""
    return f"color:{THEME[color_key]};font-size:{F['second']};" + _w(weight)


def label_body(color_key: str = "text", weight: str = None) -> str:
    """正文标签（13px）。"""
    return f"color:{THEME[color_key]};font-size:{F['body']};" + _w(weight)


def label_title(color_key: str = "text", weight: str = "semibold") -> str:
    """区块标题（15px，默认 600）。"""
    return (
        f"color:{THEME[color_key]};font-size:{F['title']};" + _w(weight)
    )


def label_title_xl(color_key: str = "text", weight: str = "bold") -> str:
    """页面大标题（20px/700）。收编 9 处 20px 页头（v4.182.0 新增层级）。"""
    return (
        f"color:{THEME[color_key]};font-size:{F['title_xl']};" + _w(weight)
    )


# ============================================================
# 空状态样式（DESIGN.md §12 · v4.206.0 新增）
# ============================================================
# 4 件套：徽章底 / 主文案 / 副文案 / 行动按钮。
# 刻意**不另立字号层级** —— 主副文案直接复用 label_body / label_micro，
# 空状态不是新的一档字号，只是"同样字号的另一种用法"。
# 新形态只有两个：圆形徽章、胶囊行动按钮。


def empty_badge(bg_key: str = "card_blue_bg", size: int = 44) -> str:
    """空状态圆形徽章底色。size 传偶数（border-radius = size/2）。"""
    return f"background:{THEME[bg_key]};border-radius:{size // 2}px;"


def empty_title() -> str:
    """空状态主文案：13px / 600 / dim + 4px 上留白（与徽章拉开一点距离）。"""
    return label_body(color_key="dim", weight="semibold") + "padding-top:4px;"


def empty_hint() -> str:
    """空状态副文案：12px / faint。"""
    return label_micro()


def empty_action_btn() -> str:
    """空状态行动按钮：胶囊（radius 16 = 高 32 的一半）、accent 底白字、12px/600。"""
    return (
        f"QPushButton{{background:{THEME['accent']};color:{THEME['white']};"
        f"border:none;border-radius:16px;font-size:{F['second']};font-weight:{W['semibold']};"
        f"padding:0 16px;}}"
        f"QPushButton:hover{{background:{THEME['accent_hover']};}}"
    )


def empty_title_compact() -> str:
    """compact 形态主文案：12px / dim（不复用 empty_title 的 13px/600 + 4px 上留白）。

    v4.207.0 新增。compact 是**行内**形态：单行、不居中、不带徽章框，
    13px/600 在 200px 宽的侧栏里会显得过重，且 4px 上留白是给徽章让位的，
    没有徽章时这段留白就是莫名其妙的空白。
    """
    return label_second(color_key="dim")


def empty_hint_compact() -> str:
    """compact 形态副文案：12px / faint（v4.209.2 补，修 nguyenly compact hint 没走 token）。

    背景：`_compact_state` 里副文案原先只写了 `background:transparent;`，
    既无 color 也无 font-size → Qt 回退到**系统默认 9pt 纯黑**，
    而同排的 compact 主文案是 12px/dim 灰。结果副文案比主文案还抢眼，
    视觉层级直接倒置（见 DESIGN §12.5 表格新增的「副文案」行）。

    为什么值跟完整形态的 empty_hint 一样（都是 label_micro）：
    F['micro'] 与 F['second'] **同为 12px**（v4.197.0 归一的结果），
    compact 主副文案本来就应该是同级字号，靠**颜色**分 dim/faint 拉开层级。
    所以这里不另起一个新值，直接复用 `label_micro()` —— 但保留独立 token 名，
    是因为它是组件契约的一部分：后人改这里不会顺手改坏完整形态。
    """
    return label_micro()


# ============================================================
# Toast 样式（DESIGN.md §11 · v4.204.0 新增）
# ============================================================
# 语义色只用在「左侧 3px 竖条 + 12px 图标点」，底色恒为 card ——
# 原因：warn = #FBBC04 是亮黄，整块做底色会让白字读不清、黑字刺眼。
TOAST_KIND_COLOR = {
    "success": "ok",       # 保存/复制/导出成功
    "info":    "accent",   # 中性提示
    "warn":    "warn",     # 已继续但有折损
    "error":   "danger",   # 操作失败
}


def toast_card() -> str:
    """toast 卡片：白底 + 1px 边框 + 圆角 8（不带内边距，内边距由布局给）。"""
    return (
        f"background:{THEME['card']};"
        f"border:1px solid {THEME['border']};"
        f"border-radius:8px;"
    )


def toast_bar(kind: str) -> str:
    """左侧语义竖条（3px 宽，由调用方定宽）。"""
    return f"background:{THEME[TOAST_KIND_COLOR[kind]]};border-radius:2px;"


def toast_dot(kind: str) -> str:
    """语义图标点（12px）。"""
    return f"color:{THEME[TOAST_KIND_COLOR[kind]]};font-size:{F['micro']};"


def toast_close_btn() -> str:
    """关闭按钮：默认淡色、hover 变深。"""
    return (
        f"QPushButton{{background:transparent;border:none;"
        f"color:{THEME['faint']};font-size:{F['second']};padding:0 2px;}}"
        f"QPushButton:hover{{color:{THEME['text']};}}"
    )
