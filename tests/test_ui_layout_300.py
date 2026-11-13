# -*- coding: utf-8 -*-
"""v4.300.0：侧栏会话管理 + 欢迎页真重构判据。

防的是 v4.299.0 的「只在旧页面上加控件」：本套件钉结构、行为和功能保留，
不要求某一串 QSS 永远不变。运行：python tests/test_ui_layout_300.py
"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def read(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        return ""
    return io.open(p, encoding="utf-8-sig").read()


def fn_seg(src, name):
    try:
        tree = ast.parse(src)
    except Exception:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


UI = read("ui.py")
SM = read("ui_session_mixin.py")
SIDEBAR = fn_seg(UI, "_build_sidebar")
WELCOME = fn_seg(UI, "_build_welcome_page")
RECENT = fn_seg(UI, "_refresh_recent_on_welcome")
CARD = fn_seg(UI, "_on_welcome_card")
CHAT = fn_seg(UI, "_build_chat_page")
REFRESH = fn_seg(SM, "_refresh_session_combo")

print("== A. 侧栏：新对话 + 搜索 + 最近会话 + 管理 ==")
check("A1 侧栏有完整文案的新对话按钮", 'self.sidebar_new_btn = QPushButton("＋ 新对话")' in SIDEBAR)
check("A2 新对话按钮复用 _new_session", "sidebar_new_btn.clicked.connect(self._new_session)" in SIDEBAR)
check("A3 侧栏有会话搜索框", "self.sidebar_session_search = QLineEdit()" in SIDEBAR
      and "搜索对话" in SIDEBAR)
check("A4 搜索输入接过滤方法", "sidebar_session_search.textChanged.connect" in SIDEBAR
      and "_filter_sidebar_sessions" in SIDEBAR)
check("A5 侧栏有独立会话列表容器", "self.sidebar_session_container = QWidget()" in SIDEBAR)
check("A6 最近会话渲染到侧栏而非欢迎页卡片墙", "sidebar_session_layout" in RECENT)
check("A7 当前会话也保留在侧栏列表（可看到选中态）",
      "s.sid != self.store.active_sid" not in RECENT)
check("A8 自动化专属会话不进用户会话列表", "SESSION_SID_PREFIX" in RECENT)
check("A9 会话搜索委托 all_sorted(query)", "all_sorted(query" in RECENT)
check("A10 有会话右键管理入口", "def _open_sidebar_session_menu" in UI
      and "customContextMenuRequested" in RECENT)
check("A11 管理菜单含重命名/置顶/删除", all(x in fn_seg(UI, "_open_sidebar_session_menu")
      for x in ("重命名", "置顶", "删除")))
check("A12 折叠侧栏会隐藏搜索和会话区", "sidebar_session_search" in fn_seg(UI, "_set_sidebar_collapsed")
      and "sidebar_session_container" in fn_seg(UI, "_set_sidebar_collapsed"))

print("== B. 侧栏导航：轻选中态而非粗胶囊 ==")
NAVSTYLE = fn_seg(UI, "_update_nav_styles")
check("B1 选中态用浅蓝底 card_blue_bg", "card_blue_bg" in NAVSTYLE)
check("B2 选中文字和图标用 accent", NAVSTYLE.count("accent") >= 3)
check("B3 导航不再使用 24px 大胶囊", "border-radius:24px" not in NAVSTYLE)
check("B4 导航行高收敛到 38px 或以下", "btn.setFixedHeight(38)" in SIDEBAR)
check("B5 功能导航默认展开，不再只剩对话/更多", "self._nav_more_open = True" in SIDEBAR
      and "self._nav_more_wrap.setVisible(True)" in SIDEBAR)
check("B6 品牌只保留侧栏一处，顶栏不重复 Logo",
      UI.count('logo_name = QLabel("小臭玩AI")') == 1)

print("== C. 欢迎页：居中输入区 + 轻建议 ==")
check("C1 主标题为完整设计文案", "今天想做点什么？" in WELCOME)
check("C2 标题和副标题均居中", WELCOME.count("Qt.AlignCenter") >= 3)
check("C3 快速输入卡使用纵向布局（输入区 + 工具条）", "QVBoxLayout(quick_box)" in WELCOME)
check("C4 快速输入卡最大宽 680", "quick_box.setMaximumWidth(680)" in WELCOME)
check("C5 有真实附件按钮并接既有附件链", "welcome_attach_btn" in WELCOME
      and "welcome_attach_btn.clicked.connect(self._on_attach_file)" in WELCOME)
check("C6 建议按钮是 52px 轻建议而非大色块卡片", "card.setFixedHeight(52)" in WELCOME)
check("C6b 快速输入区分配足够宽度，不被双 stretch 挤成三分之一",
      "quick_center.addWidget(quick_box, 4)" in WELCOME)
check("C7 建议卡不再使用彩色大底", "background:{card_bg}" not in WELCOME)
check("C8 移除卡片下方的独立打开链接", 'page_btn = QPushButton("打开%s"' not in WELCOME)
check("C9 最近对话区不再重复塞进欢迎页", "recent_header = QHBoxLayout()" not in WELCOME)
check("C10 点建议卡会回填欢迎页输入框", "self.welcome_input.setText" in CARD)
check("C11 点建议卡后焦点留在欢迎页输入框", "self.welcome_input.setFocus()" in CARD)

print("== D. 对话页：会话管理归侧栏，功能不丢 ==")
check("D1 顶栏显示当前会话标题", "self.chat_session_title = QPushButton()" in CHAT)
check("D2 旧会话下拉实例保留但不再占顶栏", "self.session_combo = _NoWheelCombo()" in CHAT
      and "hb.addWidget(self.session_combo)" not in CHAT)
check("D3 顶栏不再重复放新建按钮", "hb.addWidget(new_btn)" not in CHAT)
check("D4 会话管理与溢出菜单仍保留", "hb.addWidget(mgr_btn)" in CHAT
      and "hb.addWidget(self._chat_overflow_btn)" in CHAT)
check("D5 刷新会话时同步顶栏标题", "chat_session_title" in REFRESH)
check("D6 session_combo 过滤/排序契约仍在", "SESSION_SID_PREFIX" in REFRESH
      and "pinned.sort" in REFRESH and "normal.sort" in REFRESH)

print("== E. 动态行为（offscreen Qt）==")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QPushButton, QMainWindow
    import ui as UI_MOD
    _app = QApplication.instance() or QApplication([])
    w = UI_MOD.ChatWindow.__new__(UI_MOD.ChatWindow)
    QMainWindow.__init__(w)
    w.welcome_input = QLineEdit()
    w.input_box = QTextEdit()
    card = QPushButton()
    w.welcome_cards = [card]
    card.clicked.connect(w._on_welcome_card)
    card.click()
    check("E1 点击「文件对账」建议后欢迎输入框真实出现提示词",
          "核对这两份文件" in w.welcome_input.text(), w.welcome_input.text())
except Exception as e:
    check("E1 建议卡动态行为", False, "%s: %s" % (type(e).__name__, e))

print("\n汇总：PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
