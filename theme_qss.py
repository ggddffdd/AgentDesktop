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
    "micro":    "11px",   # 微标签：角标、时间戳、状态小字
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
    "10px": F["micro"],   # 10px → 11px（9 处微标签归一）
}


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
# 模式函数：按钮族
# ============================================================
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
        f"QPushButton:hover{{background:{THEME['panel2']};color:{THEME['text']};"
        f"border-color:{THEME['border_highlight']};}}"
    )


def btn_danger() -> str:
    """危险按钮（card 底 + 红字，hover 深红底）。源自 director_panel.py L194。"""
    return (
        f"QPushButton{{background:{THEME['card']};color:{THEME['danger_text']};"
        f"border:1px solid {THEME['border']};border-radius:8px;padding:0 12px;font-size:12px;}}"
        f"QPushButton:hover{{background:{THEME['danger_hover_dark']};}}"
    )


def btn_small() -> str:
    """小按钮（radius 6px + 12px 字号）。源自 director_panel.py L200。"""
    return (
        f"QPushButton{{background:{THEME['card']};color:{THEME['text']};"
        f"border:1px solid {THEME['border']};border-radius:6px;padding:0 12px;font-size:12px;}}"
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
        f"QComboBox:disabled{{color:{THEME['dim']};}}"
    )


def chk_style() -> str:
    """复选框。源自 director_panel.py `_chk_style`（L220）。"""
    return (
        f"QCheckBox{{color:{THEME['text']};font-size:13px;spacing:6px;}}"
        f"QCheckBox::indicator{{width:16px;height:16px;}}"
    )


# ============================================================
# 模式函数：标签/微标签族
# ============================================================
def label_micro(color_key: str = "faint") -> str:
    """微标签（11px）：角标、时间戳、状态小字。10px 表外字号归一到这。"""
    return f"color:{THEME[color_key]};font-size:{F['micro']};"


def label_second(color_key: str = "dim") -> str:
    """次级标签（12px）：提示、次级说明。"""
    return f"color:{THEME[color_key]};font-size:{F['second']};"


def label_body(color_key: str = "text") -> str:
    """正文标签（13px）。"""
    return f"color:{THEME[color_key]};font-size:{F['body']};"


def label_title(color_key: str = "text") -> str:
    """区块标题（15px/600）。"""
    return (
        f"color:{THEME[color_key]};font-size:{F['title']};"
        f"font-weight:{W['semibold']};"
    )


def label_title_xl(color_key: str = "text") -> str:
    """页面大标题（20px/700）。收编 9 处 20px 页头（v4.182.0 新增层级）。"""
    return (
        f"color:{THEME[color_key]};font-size:{F['title_xl']};"
        f"font-weight:{W['bold']};"
    )
