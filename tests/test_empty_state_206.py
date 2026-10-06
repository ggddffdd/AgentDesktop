# -*- coding: utf-8 -*-
"""空状态组件验收（v4.206.0，DESIGN.md §12 波次 1）

三个调用点：会话空态（ui.py）/ 技能审核（ui.py）/ 自动化任务（automation_panel.py）

判据分五类（独立运行：python tests/test_empty_state_206.py）：

A 组件契约  —— 真的造一个出来看结构、点按钮、喂坏数据
B token 化   —— 样式全走 theme_qss，输出值写死（不拿被测常量当基准）
C 视觉零变化 —— 会话空态是"抽出来复用"，不是重设计：参数与常量逐项等于 v4.112 原值
D 接线三处  —— 运行时真调（自动化面板用桩 app，不启主窗口）
E 边界      —— 旧文案不许残留，接入点数写死（不许提前乱接）

教训沿用：L178/L181/L185 —— 判据不能只"源码里有字符串"，也不能拿被测常量当基准。
"""
import ast
import logging
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
import automation_panel as AP    # noqa: E402

ROOT_FILES = ["ui.py", "automation_panel.py", "empty_state.py", "theme_qss.py"]


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


print("=== v4.206.0 空状态组件验收（DESIGN §12 波次 1）===")

# ------------------------------------------------------------- A 组件契约
print("\n-- A 段：组件契约 --")
fired = []
w = ES.empty_state("还没有对话", hint="点击下方开始你的第一条对话",
                   action="新建对话", on_action=lambda: fired.append(1), icon="对话")
check("A1 返回 QWidget 且背景透明", isinstance(w, QWidget)
      and "transparent" in w.styleSheet())

labels = w.findChildren(QLabel)
btn_list = w.findChildren(QPushButton)
badge = [l for l in labels if not l.pixmap().isNull()]
plain = [l for l in labels if l.pixmap().isNull()]
check("A2 徽章/主/副 三个 QLabel + 一个按钮",
      len(badge) == 1 and len(plain) == 2 and len(btn_list) == 1,
      f"badge={len(badge)} plain={len(plain)} btn={len(btn_list)}")
texts = [l.text() for l in plain]
check("A3 主副文案按序传入", texts == ["还没有对话", "点击下方开始你的第一条对话"],
      f"实际 {texts}")
check("A4 徽章 44×44 / 按钮 116×32（写死设计值，不取组件常量当基准）",
      badge and badge[0].width() == 44 and badge[0].height() == 44
      and btn_list[0].width() == 116 and btn_list[0].height() == 32,
      f"badge={badge[0].width() if badge else '-'}x{badge[0].height() if badge else '-'} "
      f"btn={btn_list[0].width()}x{btn_list[0].height()}")

btn_list[0].click()
check("A5 点按钮真的触发回调", fired == [1], f"fired={fired}")

w2 = ES.empty_state("暂无待审核技能", hint="模型自动创建的技能会出现在这里", icon="技能")
check("A6 无 action → 不渲染按钮", len(w2.findChildren(QPushButton)) == 0)
w3 = ES.empty_state("暂无待审核技能", hint="说明", icon="技能")
check("A7 有 hint 无 action → 徽章+主+副 齐全", len(w3.findChildren(QLabel)) == 3)

w4 = ES.empty_state("点了没反应", action="确定")     # 没给 on_action
b4 = w4.findChildren(QPushButton)
check("A8 有 action 无 on_action → 按钮禁用（不做死按钮）",
      bool(b4) and not b4[0].isEnabled())

w5 = ES.empty_state("无图标", hint="不画徽章")
check("A9 不给 icon → 不画徽章",
      len([l for l in w5.findChildren(QLabel) if not l.pixmap().isNull()]) == 0)


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.recs = []

    def emit(self, r):
        self.recs.append(r.getMessage())


cap = _Cap()
logging.getLogger("empty_state").addHandler(cap)
logging.getLogger("empty_state").setLevel(logging.WARNING)
try:
    w6 = ES.empty_state("坏图标", icon="不存在的图标名")
    check("A10 图标名无效 → 降级纯文字，不抛异常",
          len([l for l in w6.findChildren(QLabel)]) == 1
          and any("图标" in m for m in cap.recs),
          f"labels={len(w6.findChildren(QLabel))} log={cap.recs}")
except Exception as e:                                   # noqa: BLE001
    check("A10 图标名无效 → 降级纯文字，不抛异常", False, str(e))

# --------------------------------------------------------------- B token 化
print("\n-- B 段：样式 token 化（期望值写死，不取被测常量当基准）--")
ES_SRC = read("empty_state.py")
# 颜色字面量两种写法都要抓：`#RRGGBB` 和 `rgba(r,g,b,a)` ——
# 只抓前者的话，把样式写成 rgba() 就能绕过（扰动 ⑦ 验证过这个洞）
COLOR_LITERAL = re.compile(r"#[0-9A-Fa-f]{6}\b|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+")
check("B1 empty_state.py 内无颜色字面量（hex 与 rgba 两种都算）",
      not COLOR_LITERAL.search(ES_SRC),
      f"命中 {COLOR_LITERAL.findall(ES_SRC)[:3]}")
check("B2 empty_state.py 内无 font-size 字面量", "font-size" not in ES_SRC)
check("B2b empty_state.py 内无 border-radius 字面量", "border-radius" not in ES_SRC)

# 值写死：这些 hex 是 THEME 里的真值，改了 THEME 也应该红（那才是视觉变化）
check("B3 empty_badge 输出写死值",
      TQ.empty_badge() == "background:rgba(26,115,232,0.08);border-radius:22px;",
      f"实际 {TQ.empty_badge()}")
check("B4 empty_title 输出写死值",
      TQ.empty_title()
      == "color:#5F6368;font-size:13px;font-weight:600;padding-top:4px;",
      f"实际 {TQ.empty_title()}")
check("B5 empty_hint 输出写死值",
      TQ.empty_hint() == "color:#6B7280;font-size:12px;", f"实际 {TQ.empty_hint()}")
b_qss = TQ.empty_action_btn()
for frag, why in (("#1A73E8", "accent 底"), ("#FFFFFF", "白字"),
                  ("border-radius:16px", "胶囊 16"), ("font-size:12px", "12px"),
                  ("font-weight:600", "600"), ("#1765CC", "hover accent_hover")):
    check(f"B6 行动按钮含 {frag}（{why}）", frag in b_qss, f"实际 {b_qss}")

# 组件里控件样式确实来自 token（接线判据，不是"定义了就以为用了"）
check("B7 徽章样式 == empty_badge()", badge[0].styleSheet() == TQ.empty_badge())
check("B8 主文案样式 == empty_title()", plain[0].styleSheet() == TQ.empty_title())
check("B9 副文案样式 == empty_hint()", plain[1].styleSheet() == TQ.empty_hint())
check("B10 按钮样式 == empty_action_btn()", btn_list[0].styleSheet() == b_qss)

# ------------------------------------------------------- C 视觉零变化（会话）
print("\n-- C 段：会话空态是'抽出来复用'，参数与常量逐项等于 v4.112 原值 --")
UI_SRC = read("ui.py")
check("C1 旧的 20 行内联 QSS 已从 ui.py 消失（border-radius:22px 不再手写）",
      "border-radius:22px" not in UI_SRC)
check("C2 胶囊按钮的旧内联 QSS 已从 ui.py 消失",
      "border-radius:16px;font-size:12px" not in UI_SRC)
check("C3 组件常量 == v4.112 原值（44/20/116×32/边距 8/间距 8）",
      (ES.BADGE_SIZE, ES.ICON_SIZE, ES.ACTION_W, ES.ACTION_H)
      == (44, 20, 116, 32) and ES.MARGIN_V == (0, 8, 0, 8) and ES.SPACING == 8,
      f"{ES.BADGE_SIZE}/{ES.ICON_SIZE}/{ES.ACTION_W}x{ES.ACTION_H} "
      f"{ES.MARGIN_V} {ES.SPACING}")


def method_src(name, fname):
    tree = ast.parse(UI_SRC)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == fname:
            return "\n".join(UI_SRC.splitlines()[n.lineno - 1:n.end_lineno])
    return None


welcome_src = method_src("ui.py", "_refresh_recent_on_welcome") or ""
for frag in ('empty_state(', '"还没有对话"',
             'hint="点击下方开始你的第一条对话"',
             'action="新建对话"', 'icon="对话"'):
    check(f"C4 会话空态保留 {frag}", frag in welcome_src)

# ----------------------------------------------------------------- D 接线三处
print("\n-- D 段：接线三处（自动化面板真跑，ui.py 走 AST）--")
review_src = method_src("ui.py", "_open_skill_review_dialog") or ""
check("D1 技能审核空态走组件（图标=技能，无按钮）",
      "empty_state(" in review_src and 'icon="技能"' in review_src
      and "action=" not in review_src)

lay = QVBoxLayout()
lay.addStretch(1)
calls = []
app_stub = SimpleNamespace(auto_list_lay=lay,
                            automation_store=SimpleNamespace(list_all=lambda: []))
orig_open = AP._open_edit
try:
    AP._open_edit = lambda a, t: calls.append((a, t))
    AP._refresh_list(app_stub)
    check("D2 自动化空列表 → 插入了 1 个占位块（列表 = 占位 + stretch）",
          lay.count() == 2, f"count={lay.count()}")
    w_auto = lay.itemAt(0).widget()
    b_auto = w_auto.findChildren(QPushButton) if w_auto else []
    check("D3 自动化空态带行动按钮「＋ 新建任务」",
          bool(b_auto) and b_auto[0].text() == "＋ 新建任务",
          f"按钮 {[b.text() for b in b_auto]}")
    # ⚠️ patch 必须活到点击之后：早一步恢复，点到的就是真 _open_edit（会去造 QDialog）
    if b_auto:
        b_auto[0].click()
        check("D4 点按钮真的打开新建对话框（传 app + task=None）",
              calls == [(app_stub, None)], f"calls={calls}")
finally:
    AP._open_edit = orig_open

n_ui = UI_SRC.count("empty_state(")
wg_src = read("ui_widgets.py")  # v4.216.0：会话管理器的空态随 SessionManagerDialog 迁入
n_wg = wg_src.count("empty_state(")
ap_src = read("automation_panel.py")
n_ap = ap_src.count("empty_state(")
# v4.207.0：会话管理搜索空态接了 2 处（有/无关键词两分支）→ 2+2 = 4。
# 构成：① 会话空态 ② 技能审核 ③ 搜索·有关键词 ④ 搜索·无关键词
# v4.210.0：4 → 5（新增欢迎页「这里是最近对话」分支，DESIGN §12.8 P2-2）
check("D5 ui.py+ui_widgets.py 接入点数 == 5（会话×2 + 技能审核 + 欢迎页×2）",
      # v4.216.0：SessionManagerDialog 的 2 处随对话框族迁到 ui_widgets.py
      n_ui + n_wg == 5, f"实际 ui={n_ui} ui_widgets={n_wg}")
check("D6 automation_panel 接入点数 == 1", n_ap == 1, f"实际 {n_ap}")
others = []
for fn in sorted(os.listdir(ROOT)):
    if not fn.endswith(".py") or fn in ROOT_FILES or fn.startswith("_"):
        continue
    if "empty_state(" in read(fn):
        others.append(fn)
# v4.208.0：波次 3 已完成，legion_ui 合法接入（军团波次成员空态）。
# v4.216.0：ui_widgets.py 合法接入（SessionManagerDialog 的 2 处空态随
# 对话框族从 ui.py 搬过去 —— 是搬家，不是新扩散）。
# 判据仍是"白名单显式写出"—— 仍然守"不许乱扩散"。
check("D7 接入文件白名单 == {legion_ui, ui_widgets}（不许乱扩散）",
      others == ["legion_ui.py", "ui_widgets.py"], f"实际 {others}")

# ------------------------------------------------------------------- E 边界
print("\n-- E 段：旧写法不许残留 --")
check("E1 自动化旧文案已消失",
      "还没有任务。点击上方" not in ap_src)
check("E2 技能审核旧单行拼接已消失",
      "暂无待审核技能。模型自动创建的技能会出现在这里，" not in UI_SRC)

print(f"\n=== 汇总：{len(FAIL)} 条失败 ===")
for f in FAIL:
    print("   ✗", f)
print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)
