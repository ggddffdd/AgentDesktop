# -*- coding: utf-8 -*-
"""空状态组件 波次 2 验收（v4.207.0，DESIGN.md §12波次 2）

这一波与波次 1 不同：**接了 1 处，故意不接 2 处**。
所以判据的重点不只是"接对了"，还有"不接的理由不会被人顺手改掉"。

改动清单：
  接组件：#3 会话搜索无结果（新增 compact 形态）
  不接   ：#2 长期记忆（QTextEdit 占位串）、#4 会话预览（行内兜底）—— 只统一文案
  附带修：#3 原文案"没有匹配的会话"在"一个会话都没有"时在说谎 → 按有无搜索词分两句

判据分六类（独立运行：python tests/test_empty_state_207.py）：

A compact 契约   —— 真造、横排、无徽章、无按钮、坏图标降级
B token 化       —— compact 走theme_qss，输出值写死
C 接线 + 文案分叉 —— AST 判据写死两种文案；运行时用桩 store 真跑 _refresh
D 视觉零变化     —— compact 的行高/留白与被替换的旧写法等价
E 故意不接的守护 —— #2 #4 必须仍是QLabel/占位串，且理由在 DESIGN 里有记录
F 旧文案零残留   —— 反向计数

教训沿用：L181（不拿被测常量当基准）/ L185（要有反向计数）/ L188（颜色要抓 rgba）/
L189（多副本字符串要带上下文）。
"""
import ast
import os
import re
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QPushButton, QVBoxLayout, QWidget,
)

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def _hook(t, v, tb):
    print(f"\n[!] 未捕获异常：{t.__name__}: {v}")
    print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
    sys.exit(1)


sys.excepthook = _hook

_app = QApplication.instance() or QApplication(sys.argv)

import empty_state as ES          # noqa: E402
import theme_qss as TQ            # noqa: E402

UI = os.path.join(ROOT, "ui.py")


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def ui_lines():
    """行切片取源码（不用 ast.get_source_segment —— CRLF 仓库里会静默返回空串，L184）。"""
    with open(UI, encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n").split("\n")


def find_method(name, src_lines=None):
    """返回某个 def 的源码文本（按缩进块切，避开 CRLF 坑）。"""
    lines = src_lines if src_lines is not None else ui_lines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"\s*def {re.escape(name)}\(", ln):
            start = i
            break
    if start is None:
        return ""
    indent = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        out.append(ln)
    return "\n".join(out)


def _strip_comments(src):
    """剥掉整行注释与行尾注释，只留代码。

    为什么需要：判据里"空态调用点数""旧文案零残留"都按字符串数，
    而代码注释里会**故意引用**旧文案和函数名来解释改动
    （如"# 不接empty_state"、"原来那句「没有匹配的会话」"）。
    不剥就会把注释里的引用当成残留 —— 第一版 F1/E10 就这样误红了。
    """
    out = []
    for ln in src.replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if s.startswith("#"):
            continue
        out.append(re.sub(r"#.*$", "", ln))
    return "\n".join(out)


def _slice_fn(src, name):
    """切出单个函数体：从 `def name(` 到下一个**顶格**的 def/注释块/赋值为止。

    两个坑（都踩过）：
    - 用 split("\\ndef ") 在"最后一个函数"上会一路切到文件尾（没有下一个 def），
      把 Toast 段里合法的 warn 色 #FBBC04 也抓进来 → 误红。
    - 只认 `def ` 开头也不行：函数后面可能紧跟 `# ====` 注释块（分段标题），
      那也是顶层内容，不该算进函数体。
    """
    lines = src.replace("\r\n", "\n").split("\n")
    out, on = [], False
    for ln in lines:
        if ln.startswith(f"def {name}("):
            on = True
        elif on and ln and not ln[0].isspace():   # 顶格非空行 = 分段边界
            break
        if on:
            out.append(ln)
    return "\n".join(out)


print("=== v4.207.0 空状态 波次 2 验收（DESIGN §12）===")

# ------------------------------------------------------------ A compact 契约

print("\n--- A compact 形态契约 ---")
w = ES.empty_state("没有匹配「x」的会话", compact=True, icon="对话")
check("A1 返回 QWidget", isinstance(w, QWidget), type(w).__name__)
check("A2 整行固定高 32", w.height() == 32, w.height())
_lbls = w.findChildren(QLabel)
check("A3 只有图标+主文案两个控件", len(_lbls) == 2, len(_lbls))
check("A4 无徽章（没有 44px 控件）", not any(l.width() == 44 for l in _lbls),
      [(l.text(), l.width()) for l in _lbls])
check("A5 无按钮", len(w.findChildren(QPushButton)) == 0)
check("A6 容器背景透明", "transparent" in w.styleSheet(), w.styleSheet())

# A6b：第一版只查了"运行时没有 44px 控件"，那拦不住**实现里加一个固定 44 的东西**。
# 读源码确认 compact 分支压根不碰 BADGE_SIZE / empty_badge（v4.207 扰动第 3 条漏在这）。
_es_all_pre = read("empty_state.py")
_cbody_pre = _slice_fn(_es_all_pre, "_compact_state")
check("A6b _compact_state 实现里不出现 BADGE_SIZE（不画徽章）",
      "BADGE_SIZE" not in _cbody_pre,
      [l for l in _cbody_pre.split("\n") if "BADGE_SIZE" in l])
check("A6c _compact_state 不引用 empty_badge（徽章底色 token）",
      "empty_badge" not in _cbody_pre)
check("A6d _compact_state 不含 setFixedSize(44（防有人硬画方块）",
      "setFixedSize(44" not in _cbody_pre)

_w2 = ES.empty_state("还没有会话", hint="在主界面发起对话后会出现在这里", compact=True)
check("A7 compact 支持副文案", len(_w2.findChildren(QLabel)) == 2,
      len(_w2.findChildren(QLabel)))
check("A8 compact 无 icon 也不崩", isinstance(
    ES.empty_state("纯文字", compact=True), QWidget))

_w3 = ES.empty_state("x", action="不该出现", on_action=lambda: None, compact=True)
check("A9 compact 下 action 被忽略（不渲染按钮）",
      len(_w3.findChildren(QPushButton)) == 0,
      len(_w3.findChildren(QPushButton)))

# A9b/A9c：第一版只查了"调用方传了 action 时不该渲染"，那是**参数侧**。
# 真正要防的是**实现侧**有人把按钮加回 compact 分支 —— 所以直接读源码确认
# _compact_state 函数体里根本没有 QPushButton（v4.207 扰动第4 条就漏在这里）。
_es_all = read("empty_state.py")
_compact_body = _slice_fn(_es_all, "_compact_state")
check("A9b _compact_state 实现里没有 QPushButton（防有人加回来）",
      "QPushButton" not in _compact_body,
      [l for l in _compact_body.split("\n") if "QPushButton" in l])
check("A9c compact 用的是 QHBoxLayout（横排）",
      "QHBoxLayout" in _compact_body)
check("A9d compact 不用 QVBoxLayout（竖排会把行高顶到 3 倍）",
      "QVBoxLayout" not in _compact_body)
check("A9e compact 分支在入口就return（不会 fallthrough 到完整形态）",
      "if compact:\n        return _compact_state(" in _es_all)

_w4 = ES.empty_state("x", compact=True, icon="根本不存在的图标")
check("A10 坏图标名降级为纯文字（1 个控件）",
      len(_w4.findChildren(QLabel)) == 1, len(_w4.findChildren(QLabel)))

_w5 = ES.empty_state("还没有对话", hint="提示", action="新建对话",
                     on_action=lambda: None, icon="对话")
check("A11 完整形态未被 compact 污染（仍有徽章 44）",
      any(l.width() == 44 for l in _w5.findChildren(QLabel)))
check("A12 完整形态仍有按钮", len(_w5.findChildren(QPushButton)) == 1)

# --------------------------------------------------------------- B token 化

print("\n--- B token 化 ---")
check("B1 empty_title_compact 存在", hasattr(TQ, "empty_title_compact"))
# 期望值写死，不用被测函数自己算（L181）
check("B2 compact token 输出写死",
      TQ.empty_title_compact() == "color:#5F6368;font-size:12px;",
      TQ.empty_title_compact())
check("B3 compact 用的是 dim 而非 placeholder",
      TQ.THEME["dim"] in TQ.empty_title_compact()
      and TQ.THEME["placeholder"] not in TQ.empty_title_compact())

_es_src = read("empty_state.py")
_tq_src = read("theme_qss.py")
# 颜色字面量：#RRGGBB 与 rgba(r,g,b) 都要抓（L188）
# _slice_fn 见文件头部（切函数体，避开Toast 段里合法的 warn 色 #FBBC04）。
_compact_fn = _slice_fn(_tq_src, "empty_title_compact")
_hexes = re.findall(r"#[0-9A-Fa-f]{6}\b", _compact_fn)
_rgbas = re.findall(r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+", _compact_fn)
check("B4 theme_qss.empty_title_compact 无颜色字面量",
      not _hexes and not _rgbas, f"hex={_hexes} rgba={_rgbas}")
_sizes = re.findall(r"font-size:\s*\d+px", _compact_fn)
_radii = re.findall(r"border-radius:\s*\d+px", _compact_fn)
check("B5 theme_qss.empty_title_compact 无字号/圆角字面量",
      not _sizes and not _radii, f"size={_sizes} radius={_radii}")

_hexes2 = re.findall(r"#[0-9A-Fa-f]{6}\b", _es_src)
_rgbas2 = re.findall(r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+", _es_src)
check("B4b empty_state.py 无颜色字面量",
      not _hexes2 and not _rgbas2, f"hex={_hexes2} rgba={_rgbas2}")
_sizes2 = re.findall(r"font-size:\s*\d+px", _es_src)
_radii2 = re.findall(r"border-radius:\s*\d+px", _es_src)
check("B5b empty_state.py 无字号/圆角字面量",
      not _sizes2 and not _radii2, f"size={_sizes2} radius={_radii2}")

# compact 的样式确实来自 token
_m_lbl = [l for l in _w2.findChildren(QLabel) if l.text() == "还没有会话"][0]
check("B6 控件样式 == token 输出（主文案）",
      "12px" in _m_lbl.styleSheet(), _m_lbl.styleSheet())

# --------------------------------------------------------- C 接线 + 文案分叉

print("\n--- C 接线与文案分叉 ---")
_lines = ui_lines()
_ui_src = "\n".join(_lines)
_refresh = find_method("_refresh", _lines)
check("C1 _refresh 里接了 empty_state", "empty_state(" in _refresh)
check("C2 两处调用都带 compact=True",
      _refresh.count("compact=True") == 2, _refresh.count("compact=True"))
check("C3 旧的内联 QLabel 空态已移除",
      'QLabel("没有匹配的会话")' not in _ui_src)
check("C4 旧的硬编码样式已移除",
      "font-size:13px;padding:12px 0;" not in _refresh)

# 文案分叉：两种情况必须不同，且都要写死
check("C5 有搜索词分支写死文案",
      'f"没有匹配「{q}」的会话"' in _refresh)
check("C6 无搜索词分支写死文案", '"还没有会话"' in _refresh)
check("C7 两句文案不是同一句（防空态说谎）",
      '没有匹配「{q}」的会话' in _refresh and '"还没有会话"' in _refresh)
check("C8 判据依据是 search.text() 而非 folder",
      "self.search.text()" in _refresh)
check("C9 空搜索词分支不指引不存在的按钮",
      "新建" not in _refresh.split('"还没有会话"')[1][:200],
      _refresh.split('"还没有会话"')[1][:200])

# 运行时真跑 _refresh（桩 store）
print("\n--- C2 运行时：桩 store 真跑 _refresh ---")


class _Sess:
    def __init__(self, title):
        self.title = title
        self.folder = ""
        self.pinned = False
        self.created = 0
        self.messages = []


class _Store:
    def __init__(self, items):
        self._items = items

    def all_sorted(self, query="", folder=""):
        if not query:
            return list(self._items)
        q = query.strip().lower()
        return [s for s in self._items if q in (s.title or "").lower()]

    def list_folders(self):
        return []


class _FakeDlg:
    """只借 _refresh 用到的那几个控件，避开 QDialog 的类型检查。"""

    def __init__(self, items, search_text=""):
        self.store = _Store(items)
        self.list_lay = QVBoxLayout()
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self._row_sids = []
        self._checks = []
        self.search = SimpleNamespace(text=lambda: search_text)
        # _refresh(initial=True) 会走 folder_filter 的 clear/addItem/findText/setCurrentIndex
        self.folder_filter = SimpleNamespace(
            currentText=lambda: "全部会话",
            findText=lambda t: -1,
            setCurrentIndex=lambda i: None,
            blockSignals=lambda b: None,
            clear=lambda: None,
            addItem=lambda t: None,
        )
        # 有匹配时会走 _build_row；本套件只关心空态分支，给个会自证的桩即可
        self._build_row_calls = 0

    def _build_row(self, s):
        self._build_row_calls += 1
        w = QWidget()
        self.list_lay.addWidget(w)
        return w


from ui import SessionManagerDialog          # noqa: E402

# _refresh 是 SessionManagerDialog 的**方法**，不是模块级函数 —— 取它的 unbound 版本，
# 直接喂桩对象跑，避免起真的 QDialog（QDialog.__init__ 不吃 SimpleNamespace）。
_real_refresh = SessionManagerDialog._refresh


def _run_refresh(items, search_text=""):
    d = _FakeDlg(items, search_text)
    _real_refresh(d, initial=True)
    return d


_d = _run_refresh([], "")
_lbls0 = _d.list_lay.itemAt(0).widget().findChildren(QLabel)
_txts0 = [l.text() for l in _lbls0]
check("C10 无会话+空搜索词 → 显示「还没有会话」",
      "还没有会话" in _txts0, _txts0)
check("C11 无会话+空搜索词 → 附带说明", any("主界面" in t for t in _txts0), _txts0)

_d2 = _run_refresh([_Sess("文件差异核对")], "zzz不存在的词")
_txts1 = [l.text() for l in _d2.list_lay.itemAt(0).widget().findChildren(QLabel)]
check("C12 有关键词无匹配 → 回显关键词",
      any("zzz不存在的词" in t for t in _txts1), _txts1)
check("C13 有关键词无匹配 → 不是「还没有会话」",
      "还没有会话" not in _txts1, _txts1)

_d3 = _run_refresh([_Sess("文件差异核对")], "文件")
check("C14 有匹配时不出空态（列表正常渲染）",
      _d3.list_lay.count() == 1
      and not _d3.list_lay.itemAt(0).widget().findChildren(QLabel),
      _d3.list_lay.count())

# ------------------------------------------------------- D 视觉零变化等价性

print("\n--- D 视觉零变化（compact 等价于被替换的旧写法）---")
check("D1 COMPACT_H == 旧写法的行高基准 32", ES.COMPACT_H == 32, ES.COMPACT_H)
check("D2 compact 上下留白 == 旧写法 padding 12px/2（8+8 视觉等价）",
      ES.COMPACT_MARGIN_V == (8, 8, 8, 8), ES.COMPACT_MARGIN_V)
check("D3 compact 图标 18 < 完整形态 20（窄容器里更轻）",
      ES.COMPACT_ICON_SIZE < ES.ICON_SIZE,
      f"{ES.COMPACT_ICON_SIZE} vs {ES.ICON_SIZE}")
check("D4 COMPACT_ICON_SIZE 已写死 18（防被随手改大）",
      ES.COMPACT_ICON_SIZE == 18, ES.COMPACT_ICON_SIZE)
# 旧写法是 13px placeholder，新 compact 是 12px dim —— 记为**有意变化**
check("D5 字号从 13px 降到 12px 是有意的（窄栏需要更轻）",
      TQ.F["second"] == "12px" and TQ.F["body"] == "13px",
      f"second={TQ.F['second']} body={TQ.F['body']}")

# ------------------------------------------- E 故意不接的两处：守护判据

print("\n--- E #2 #4 故意不接组件（守护判据）---")
_mem = find_method("_refresh_memory_view", _lines)
check("E1 #2 长期记忆仍是 setPlainText 占位（没被换成组件）",
      "setPlainText" in _mem and "empty_state(" not in _mem)
check("E2 #2 旧文案（含圆括号）已清零",
      "（暂无长期记忆" not in _ui_src)
check("E3 #2 新文案写死", '"暂无长期记忆，对话中小臭会自动积累"' in _mem)
check("E4 #2 理由写进了代码注释",
      "不接empty_state" in _mem and "空区域" in _mem)

_row_src = _ui_src.split("def _build_recent_row")[1] if "def _build_recent_row" in _ui_src else _ui_src
check("E5 #4 预览仍是行内 QLabel 兜底",
      'QLabel(_session_preview(s) or "还没有消息")' in _ui_src)
# E5b：第一版E5 只查了"那句QLabel 还在"，但有人在**后面追加**一个 empty_state
# 调用、把真正的预览组件换掉，E5 照样绿。必须反向计数：这一行里不许出现组件。
_prev_idx = [i for i, ln in enumerate(_lines) if "_session_preview(s) or" in ln]
_prev_line = [_lines[i] for i in _prev_idx]
check("E5b #4 那一行里没有 empty_state（防追加式偷换）",
      bool(_prev_line) and not any("empty_state" in ln for ln in _prev_line),
      _prev_line)
# E5c：不写死行号（L175：行号会漂），按"离那一行±6 行"取窗口
_win = ([_lines[i] for i in range(max(0, _prev_idx[0] - 6),
                                min(len(_lines), _prev_idx[0] + 7))]
        if _prev_idx else [])
check("E5c #4 上下 6 行内无 empty_state 调用",
      _win and not any("empty_state(" in ln for ln in _win),
      [ln for ln in _win if "empty_state(" in ln])
check("E6 #4 旧文案「暂无内容」清零", 'or "暂无内容"' not in _ui_src)
check("E7 #4 理由写进了代码注释",
      "不接empty_state" in _ui_src or "不接 empty_state" in _ui_src)

_design = read("DESIGN.md")
check("E8 DESIGN 里记了不接的理由（§12.5）",
      "12.5" in _design and "不接" in _design)
check("E9 DESIGN 记录了 #3 文案分叉这件事",
      "说谎" in _design or "分叉" in _design)

# 接入点数写死：ui.py 剥注释后 = 4 处
#   1) 搜索无结果·有关键词（本轮新增）
#   2) 搜索无结果·无关键词（本轮新增，同一if 下的两个分支）
#   3) 会话空态（v4.206）
#   4) 技能审核（v4.206）
# 数**调用点**而不是 import 行；且必须剥掉注释 —— 注释里解释"不接 empty_state"
# 的那句话也含这个字样，不剥会多数一处（第一版就多数了，红得莫名其妙）。
_ui_code_for_count = _strip_comments(_ui_src)
_ui_calls = len(re.findall(r"\bempty_state\(", _ui_code_for_count))
# v4.210.0：4 → 5（同上，DESIGN §12.8 P2-2）
check("E10 ui.py 内 empty_state 调用点写死为 5（搜索×2 + 会话 + 技能审核 + 欢迎页）",
      _ui_calls == 5, _ui_calls)
# E10c：只写死"总数 == 4"的话，**同时删掉一处再加一处**是察觉不到的
# （净变化为 0）。所以再判一次搜索空态的两个分支各自都在。
check("E10c 搜索空态两个分支都在（防止一处被删一处被补）",
      _refresh.count("compact=True") == 2
      and "没有匹配「{q}」的会话" in _refresh
      and '"还没有会话"' in _refresh)
check("E10b _strip_comments 确实能剥掉注释（自证工具有效）",
      _strip_comments("# empty_state( 是注释\nx = empty_state(1)") .count("empty_state(") == 1,
      _strip_comments("# empty_state( 是注释\nx = empty_state(1)"))
_ap_src = read("automation_panel.py")
check("E11 automation_panel.py 仍接 1 处",
      len(re.findall(r"\bempty_state\(", _ap_src)) == 1)
_legion = read("legion_ui.py")
# E12：v4.207 时这里守的是"波次 3 的活还没做，legion_ui 不许接"。
# **v4.208.0 波次 3 已完成**，legion_ui 合法接了 1 处（军团波次成员空态），
# 所以判据从"零接入"翻成"**恰好 1 处、且是波次成员那处**"——
# 翻的时候不能只改成"不红了"，那会把守防线一起撤掉。
_leg_code = _strip_comments(_legion)
# v4.210.0：1 → 2（新增「还没有波次」，DESIGN §12.8 P2-3）。
# 不改成"不红了"：这里守的是**数量**，加一处就该把它登记进来。
check("E12a legion_ui 接入 2 处（波次成员 + 0 波空态）",
      len(re.findall(r"\bempty_state\(", _leg_code)) == 2,
      len(re.findall(r"\bempty_state\(", _leg_code)))
check("E12b legion_ui 这一处是「波次成员空态」而不是别的",
      '"本波还没有成员"' in _leg_code)
check("E12c legion_ui 已 import empty_state",
      re.search(r"^\s*from\s+empty_state\s+import", _legion, re.M) is not None)

# ------------------------------------------------------- F 旧文案零残留

print("\n--- F 旧文案零残留（反向计数）---")
# 判据要点：**代码**里不许残留，但**注释/文档**里可以引用旧文案来解释改动。
# _strip_comments 的定义见文件头部（E10 也要用）。
_ui_code = _strip_comments(_ui_src)
_ap_code = _strip_comments(_ap_src)
for _old, _name in (("没有匹配的会话", "F1 #3 旧文案"),
                    ("（暂无长期记忆", "F2 #2 旧文案"),
                    ('or "暂无内容"', "F3 #4 旧文案")):
    check(f"{_name} 代码里零残留（注释可引用）",
          _old not in _ui_code and _old not in _ap_code, _old)
check("F4 #3 旧文案确实在注释里被引用为「改动说明」（证明 F1 不是靠删注释过的）",
      "没有匹配的会话" in _ui_src)

print(f"\nPASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
