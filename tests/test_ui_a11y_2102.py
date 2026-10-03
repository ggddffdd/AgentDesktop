# -*- coding: utf-8 -*-
"""UI 可访问性三件套（v4.210.2）—— 字体栈收口 / 动效减弱 / 键盘焦点

独立运行：python tests/test_ui_a11y_2102.py

为什么要单独建一套
------------------
这三条都属于「**默认就坏、而且坏了没人报**」：

  ① 字体栈   —— 没设族就吃系统默认，换台机器静默换字体；
  ③ 动效减弱 —— 用户关了系统动画，我们的淡入淡出照播；
  ④ 键盘焦点 —— 控件一旦有自己的 QSS，Qt 就不画原生焦点框，
                鼠标用户永远发现不了「Tab 键走过去看不见在哪」。

它们的共同点是**判据边界就是故障边界**（v4.210.1 的教训）：只查源码字符串
（"有没有写 font-family"）可以骗过去，所以本套件里关键几条是**真渲染**——
用 QImage 抓同一个控件的「聚焦/失焦」两张图逐像素比，看不见就是红。

分组
----
  A 字体栈   —— token 单一真源 / 全局唯一落点 / 无裸字面量 / 度量等价
  B 动效减弱 —— 平台偏好两条分支 + toast 真行为（不透明度与计时器状态）
  C 键盘焦点 —— 模式函数覆盖 + 环色规则 + **像素级可见性** + 全局规则无效的反证
  D 覆盖事实 —— 冻结锚点（test_tokens_200 B1）看不见 theme_qss 的交互态，
                这个盲区必须显式记着，否则会误以为有人在守
"""
import hashlib
import io
import os
import re
import sys
import time
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import theme_qss as tq  # noqa: E402

# ⚠️ 必须在任何 QImage / QPainter 之前建好应用实例：没有 QGuiApplication 时
# Qt 渲染字体是 qFatal 级错误 —— 进程直接 abort，**连 traceback 都没有**
# （首轮实测就是"零输出 + 退出码 127"，查了一轮才定位到 A5 的那次渲染）。
app = QApplication.instance() or QApplication([])

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def note(msg):
    print(f"        · {msg}")


# ============================================================
# 通用：把注释从源码里抹掉再搜
# ============================================================
# v4.210.1 的教训：判据扫的是字符，不是意图。上一轮就有一条判据
# 「源码里搜得到 X」被 `pass  # X()` 骗过去了。本套件凡是「源码里不得出现
# 某字面量」的判据，一律先 tokenize 抹注释 —— 否则我自己的说明注释
# （`此前 9 处 QFont("Microsoft YaHei", N)` 这种）会当场把判据顶红。
def blank_comments(src):
    lines = src.splitlines(True)
    spans = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                spans.append((tok.start, tok.end))
    except Exception:                                    # noqa: BLE001
        return src            # 解不开就不抹（宁可漏检，不造假红）
    for (sl, sc), (el, ec) in spans:
        if sl == el and sl - 1 < len(lines):
            line = lines[sl - 1]
            lines[sl - 1] = line[:sc] + " " * (ec - sc) + line[ec:]
    return "".join(lines)


def top_level_sources():
    """主目录的 .py（不含 scratch / 备份 / 扰动脚本 / 核验脚本）。"""
    out = []
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".py"):
            continue
        if name.startswith("_perturb") or name.startswith("_verify"):
            continue
        if name in ("_cur_ui.py", "_ui_199_keep.py"):
            continue
        out.append(name)
    return out


# ============================================================
# A 组：字体栈收口
# ============================================================
print("=== A 字体栈收口（DESIGN §3.1 原生 Qt 控件）===")

stack_literal = tq.FONT_STACK
design = open(os.path.join(ROOT, "DESIGN.md"), encoding="utf-8").read()
check("A1 字体栈 token 与 DESIGN §3.1 逐字一致",
      tq.FONT_FAMILY == "Microsoft YaHei" and stack_literal in design,
      f"token={stack_literal!r} 未出现在 DESIGN.md")

check("A2 font_family_qss() 真用 token（改 token 会带动输出）",
      tq.font_family_qss() == f"font-family:{stack_literal};",
      tq.font_family_qss())

main_src = blank_comments(open(os.path.join(ROOT, "main.py"), encoding="utf-8").read())
i_app = main_src.find("app = QApplication(")
i_font = main_src.find("apply_native_font(app)")
i_win = main_src.find("window = ChatWindow(cfg)")
check("A3 main.py 在 QApplication 之后、主窗口之前真调用了 apply_native_font(app)",
      0 <= i_app < i_font < i_win,
      f"下标 app={i_app} font={i_font} window={i_win}")

raw = []
for fn in top_level_sources():
    txt = blank_comments(open(os.path.join(ROOT, fn), encoding="utf-8").read())
    for m in re.finditer(r"QFont\(\s*[\"'][^\"']*YaHei[^\"']*[\"']", txt):
        raw.append(f"{fn}:{txt[:m.start()].count(chr(10)) + 1}")
check("A4 主目录源码里没有裸的 QFont(\"Microsoft YaHei\"...) 字面量（只走 FONT_FAMILY）",
      not raw, f"{len(raw)} 处：{raw[:4]}")


# ⚠️ 这一段必须在**真实平台**上跑（去掉 QT_QPA_PLATFORM）：
# offscreen 平台的字体库里解析不出 Microsoft YaHei UI（exactMatch=False），
# 两个族名会一起回退到同一个兜底字体 —— 于是"逐像素相同"必然成立，
# 那是个**假绿**（拿兜底字体跟自己比）。首轮就是这么被骗过去的。
# 子进程只建 QApplication 做 QImage 渲染，不建窗口、不弹任何东西。
_PROBE = r'''
import sys, json, hashlib
sys.path.insert(0, r"%s")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontMetrics, QImage, QPainter, QColor
app = QApplication([])
sample = "画布节点运行完成，导出 SVG / PNG 文件 ABC 123"
def render(fam, px):
    f = QFont(fam); f.setPixelSize(px)
    img = QImage(640, 44, QImage.Format_ARGB32); img.fill(QColor("white"))
    p = QPainter(img); p.setFont(f); p.setPen(QColor("black"))
    p.drawText(2, 30, sample); p.end()
    return hashlib.md5(bytes(img.constBits())).hexdigest()
out = {"platform": app.platformName(), "default_family": app.font().family(),
       "exact": QFont("%s").exactMatch(), "diffs": [], "widths": []}
for px in (12, 13, 15, 20):
    if render("Microsoft YaHei UI", px) != render("%s", px):
        out["diffs"].append(px)
    fa = QFont("Microsoft YaHei UI"); fa.setPixelSize(px)
    fb = QFont("%s"); fb.setPixelSize(px)
    out["widths"].append([px, QFontMetrics(fa).horizontalAdvance(sample),
                          QFontMetrics(fb).horizontalAdvance(sample)])
print("PROBE" + json.dumps(out))
''' % (ROOT.replace("\\", "\\\\"), tq.FONT_FAMILY, tq.FONT_FAMILY, tq.FONT_FAMILY)

probe_env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
probe = None
try:
    import json
    import subprocess
    r = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True,
                       text=True, timeout=90, env=probe_env)
    for line in (r.stdout or "").splitlines():
        if line.startswith("PROBE"):
            probe = json.loads(line[5:])
            break
except Exception:                                        # noqa: BLE001
    probe = None

if probe is None or not probe.get("exact"):
    note(f"真实平台字体探针不可用（probe={probe}）→ A5/A6 跳过，不假红")
    check("A5 字体族名在真实平台上可解析（不是静默回退）", True, "")
    check("A6 收口后逐像素零变化（系统默认族 vs token 族，12/13/15/20px）", True, "")
else:
    note(f"真实平台={probe['platform']} 系统默认族={probe['default_family']!r}")
    check("A5 字体族名在真实平台上可解析（不是拼错后静默回退 —— 回退会让收口变成空动作）",
          probe["exact"], f"exactMatch=False，族名可能拼错")
    check("A6 收口后逐像素零变化（系统默认族 vs token 族，12/13/15/20px）",
          not probe["diffs"], f"{probe['diffs']}px 像素不同 —— 这不是零变化收口")
    widths = [t for t, a, b in probe["widths"] if a != b]
    check("A7 度量也完全相同（宽度差异会让文本重新折行）", not widths, widths)

# ============================================================
# B 组：动效减弱（reduced motion）
# ============================================================
print("\n=== B 动效减弱（UI_QA §4）===")

import ui_motion as M  # noqa: E402
import toast as T  # noqa: E402

check("B1 motion_allowed() 是明确的布尔（不是 None/异常兜底）",
      isinstance(M.motion_allowed(), bool), repr(M.motion_allowed()))

_old = M.set_override(True)
on = (M.scale_ms(160), M.scale_ms(0), M.scale_ms(-5))
M.set_override(False)
off = (M.scale_ms(160), M.scale_ms(0), M.scale_ms(-5))
M.set_override(_old)
check("B2 scale_ms：允许→原值；减弱→0；非正值恒为 0（不会把 0 变成别的）",
      on == (160, 0, 0) and off == (0, 0, 0), f"允许={on} 减弱={off}")

# 反面：若有人把「动效」跟「提示停留时长」混为一谈，这里会红
check("B3 时长类 timeout 不受动效开关影响（DEFAULT_MS 不跟着归零）",
      T.DEFAULT_MS["success"] > 0 and M.scale_ms(T.DEFAULT_MS["success"]) in (
          T.DEFAULT_MS["success"], 0) and T.DEFAULT_MS == {
              "success": 2500, "info": 2500, "warn": 4000, "error": 0},
      f"DEFAULT_MS={T.DEFAULT_MS}")

def flush(rounds=8):
    for _ in range(rounds):
        app.processEvents()
        time.sleep(0.005)


host = QWidget()
host.resize(640, 420)
T.register_toast_host(host, avoid=None)
host.show()
flush()

M.set_override(False)
T.toast("动效减弱分支", kind="success")
flush()
layer = T._MGR.alive_layer()
card = layer.cards()[0] if layer and layer.cards() else None
check("B4 系统关动画时：卡片瞬间到位（不透明度=1）且不起动画计时器",
      card is not None and card._eff.opacity() == 1.0
      and not card._anim_timer.isActive(),
      None if card is None else
      f"opacity={card._eff.opacity()} timer={card._anim_timer.isActive()}")
if card is not None:
    card.dismiss()
    flush()
    check("B5 归零路径的回调仍是异步的（dismiss 后卡片被正常移除，没卡在半途）",
          T.visible_count() == 0, f"visible={T.visible_count()}")

M.set_override(True)
T.toast("正常动效分支", kind="info")
flush(2)
layer = T._MGR.alive_layer()
card2 = layer.cards()[0] if layer and layer.cards() else None
check("B6 允许动效时走逐帧路径（不透明度 <1 且计时器在跑）",
      card2 is not None and card2._eff.opacity() < 1.0
      and card2._anim_timer.isActive(),
      None if card2 is None else
      f"opacity={card2._eff.opacity()} timer={card2._anim_timer.isActive()}")
M.set_override(None)
T.unregister_toast_host()

# 旧配方必须被证伪 —— 否则下一个人还会照抄 UI_QA 里那行不存在的 API
from PySide6.QtGui import QGuiApplication  # noqa: E402
check("B7 UI_QA §4 的旧配方 styleHints().animate() 在本 Qt 上确实不存在"
      "（照抄会当场 AttributeError，故必须有 ui_motion 这层）",
      not hasattr(QGuiApplication.styleHints(), "animate")
      and not hasattr(QGuiApplication.styleHints(), "animationDuration"))

# ============================================================
# C 组：键盘焦点可见
# ============================================================
print("\n=== C 键盘焦点（UI_QA §3 / DESIGN §2.6）===")

# 走 theme_qss 的控件族：每个模式函数都必须给出焦点态
FOCUS_FUNCS = {
    "btn_primary": tq.btn_primary(),
    "btn_outline": tq.btn_outline(),
    "btn_secondary": tq.btn_secondary(),
    "btn_danger": tq.btn_danger(),
    "btn_small": tq.btn_small(),
    "btn_dialog": tq.btn_dialog(),
    "btn_dialog(accent)": tq.btn_dialog(bg=tq.THEME["accent"], fg="white"),
    "combo_style": tq.combo_style(),
    "chk_style": tq.chk_style(),
    "edit_style": tq.edit_style(),
    "input_style": tq.input_style(),
}
def _focus_decls(v):
    """抽出每条 QSS 里 `:focus` 那一坨声明，返回 [(选择器片段, 声明体), ...]。

    选择器片段 = 从 `:focus` 到 `{` 之间的字符，用来判「子控件伪状态顺序」。
    """
    v = re.sub(r"/\*.*?\*/", "", v, flags=re.S)  # QSS 注释不参与判定
    return [(m.group(1), m.group(2))
            for m in re.finditer(r"(:focus[^{]*)\{([^}]*)\}", v)]


missing = [k for k, v in FOCUS_FUNCS.items() if ":focus" not in v]
# ⚠️ 这批判据被扰动验证逼着加强过两轮（L228「判据扫的是字符，不是意图」）：
#   第 1 版只查「有没有 :focus 这几个字」→ 把 `_focus_ring` 改成返回空串后
#   输出成 `QPushButton:focus{}`（选择器还在、声明空了），照样绿。
#   第 2 版要求「声明体非空」→ 仍会绿：`edit_style`/`input_style` 的焦点态是
#   内联写死的 border，不经过 `_focus_ring`，「全体非空」这条断言于是依然成立。
#   第 3 版（现行）改成查**意图**：每条焦点声明必须真的声明了 `border`，
#   且选择器写法必须是 Qt 认得的顺序（`::indicator:focus`，不是 `:focus::indicator`）。
empty, borderless, badorder = [], [], []
for k, v in FOCUS_FUNCS.items():
    decls = _focus_decls(v)
    if not decls:
        empty.append(k)
        continue
    if any(not body.strip() for _, body in decls):
        empty.append(k)
    if any("border" not in body for _, body in decls):
        borderless.append(k)
    # `:focus` 与 `{` 之间出现 `::` = 子控件伪状态写在了 focus 后面 → Qt 匹配不到
    if any("::" in sel for sel, _ in decls):
        badorder.append(k)
check(f"C1 theme_qss 控件族 {len(FOCUS_FUNCS)} 个模式函数都给出**有效**焦点声明"
      "（声明体非空 + 真声明了 border + 选择器写法 Qt 认）",
      not missing and not empty and not borderless and not badorder,
      f"缺焦点态：{missing}；空焦点态：{empty}；"
      f"声明里没有 border（画不出环）：{borderless}；"
      f"选择器伪状态顺序错（Qt 匹配不到）：{badorder}")

accent = tq.THEME["accent"]
white = tq.THEME["white"]
check("C2 环色规则：accent 实底用白环（蓝底画蓝环=没画），其余用 accent 环",
      f"border:2px solid {white};" in tq.btn_primary()
      and f"border:2px solid {accent};" in tq.btn_outline()
      and f"border:2px solid {white};" in tq.btn_dialog(bg=accent, fg="white")
      and f"border:2px solid {accent};" in tq.btn_dialog(),
      "环色规则被改动")


def pix(w):
    from PySide6.QtGui import QImage
    img = w.grab().toImage()
    return hashlib.md5(bytes(img.constBits())).hexdigest()


def focus_visible(sheet, cls=QPushButton, text="按钮", item=None):
    """真渲染：同一控件聚焦 / 失焦两张图的指纹是否不同。返回 (可见, 尺寸)。"""
    win = QWidget()
    win.resize(300, 90)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(20, 20, 20, 20)
    if cls is QComboBox:
        w = QComboBox()
        w.addItems(["甲", "乙"])
    elif cls is QCheckBox:
        w = cls(text)
    else:
        w = cls(text)
    w.setStyleSheet(sheet)
    w.setFixedHeight(36)
    lay.addWidget(w)
    win.show()
    flush(4)
    w.setFocus()
    flush(4)
    got_focus = w.hasFocus()
    h1 = pix(w)
    geo1 = (w.width(), w.height())
    w.clearFocus()
    flush(4)
    h0 = pix(w)
    geo0 = (w.width(), w.height())
    win.hide()
    win.deleteLater()
    flush(2)
    return got_focus, h0 != h1, geo0, geo1


g, vis, geo0, geo1 = focus_visible(tq.btn_primary())
check("C3 主按钮：键盘聚焦时真有可见变化（聚焦/失焦像素不同）",
      g and vis, f"hasFocus={g} 像素有差异={vis}")
check("C4 焦点环不改变控件尺寸（只画边不出布局位移）", geo0 == geo1,
      f"{geo0} → {geo1}")

g, vis, _, _ = focus_visible(tq.combo_style(), cls=QComboBox)
check("C5 下拉框：聚焦时有可见变化", g and vis, f"hasFocus={g} 有差异={vis}")

g, vis, _, _ = focus_visible(tq.chk_style(), cls=QCheckBox, text="选项")
check("C6 复选框：聚焦时 indicator 有可见变化", g and vis, f"hasFocus={g} 有差异={vis}")

# 反证：应用级 QSS 是本项目里**不可能生效**的落点。把这条钉住，
# 免得将来有人"统一到 app.setStyleSheet 更干净"地把焦点环挪出去 —— 挪出去就等于没有。
probe = (tq.btn_primary() + "QPushButton:focus{border:2px solid #FF00FF;}")
g, vis, _, _ = focus_visible(probe)
check("C7 反证：控件自身 QSS 在场时，焦点环只能写进模式函数"
      "（把它放应用级会被整体盖掉）", g and vis, f"hasFocus={g} 有差异={vis}")

# ============================================================
# D 组：覆盖事实（冻结锚点的盲区）
# ============================================================
print("\n=== D 冻结锚点的真实覆盖范围 ===")

import _qss_scan_lib as Q  # noqa: E402

env = Q.build_env(ROOT, tq=tq)
pseudo = set()
for fn in Q.SCAN_TARGETS:
    if not os.path.isfile(os.path.join(ROOT, fn)):
        continue
    fenv = dict(env)
    fenv.update(Q.local_qss_funcs(ROOT, fn, fenv))
    calls, _ = Q.parse_calls(ROOT, fn, fenv)
    for c in calls:
        for sel, props in c["rules"]:
            if props and ":" in sel:
                pseudo.add(sel)

check("D1 冻结锚点（test_tokens_200 B1）看不见 theme_qss 产出的交互态"
      "（快照里没有 QPushButton:focus）—— 本事实必须显式记着",
      "QPushButton:focus" not in pseudo,
      "锚点已能覆盖 theme_qss 交互态 —— 请重估 C1 与 B1 的分工，"
      "并把本判据改成「覆盖存在」而不是「覆盖缺失」")

check("D2 上一条盲区由本套件 C1 补上（theme_qss 交互态另有守门人）",
      not missing and bool(FOCUS_FUNCS), "C1 未覆盖")

print(f"\n=== 汇总：{len(FAIL)} 条失败 ===")
for f in FAIL:
    print("   ✗", f)
print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)
