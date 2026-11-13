# -*- coding: utf-8 -*-
"""v4.301.0：创作页 / 系统页统一工作区骨架判据。

钉住真实结构与动态行为，而不是只检查几张截图：
  A. 共享页头、间距、滚动容器与卡片工厂。
  B. 生图 / 生视频 / 数字人 / 导演台 / 工具 / 任务 / 设置七页全部接入。
  C. 创作参数与结果分区、任务空态单入口、导演台步骤条防裁切。
  D. 原按钮处理链不变，共享骨架的 action 与滚动行为真实有效。
"""
import ast
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_SBX = tempfile.mkdtemp(prefix="xc_workspace301_")
os.environ.setdefault("XC_USER_DATA_DIR", os.path.join(_SBX, "userdata"))
os.environ.setdefault("XC_WORKSPACE_DIR", os.path.join(_SBX, "workspace"))

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
UW = read("ui_widgets.py")
TWIN = read("digital_twin_panel.py")
DIRECTOR = read("director_panel.py")
AUTO = read("automation_panel.py")
SHELL = fn_seg(UW, "build_workspace_shell")
CARD = fn_seg(UW, "workspace_card")
IMG = fn_seg(UI, "_build_image_page")
VIDEO = fn_seg(UI, "_build_video_page")
TOOLS = fn_seg(UI, "_build_tools_page")
SETTINGS = fn_seg(UI, "_build_settings_page")
DIRECTOR_BUILD = fn_seg(DIRECTOR, "build_director_panel")
DIRECTOR_CHAT = read("director_chat.py")
TWIN_BUILD = fn_seg(TWIN, "build_twin_panel")
AUTO_BUILD = fn_seg(AUTO, "build_automation_panel")
AUTO_REFRESH = fn_seg(AUTO, "_refresh_list")

print("== A. 共享工作区骨架 ==")
check("A1 ui_widgets 提供共享页面骨架", "def build_workspace_shell" in UW)
check("A2 页头标题 / 副标题有稳定对象名", all(x in SHELL for x in (
    "workspaceHeader", "workspaceTitle", "workspaceSubtitle")))
check("A3 标题与副标题复用既有字号 token", "label_title_xl" in SHELL and "label_second" in SHELL)
check("A4 七页统一内容边距 32/28/32/24", "setContentsMargins(32, 28, 32, 24)" in SHELL)
check("A5 正文统一 16px 区块间距", "setSpacing(16)" in SHELL)
check("A6 可选滚动区可缩放且禁横向滚动", all(x in SHELL for x in (
    "setWidgetResizable(True)", "ScrollBarAlwaysOff", "scroll_transparent")))
check("A7 页头 action 复用主按钮样式并可点击", all(x in SHELL for x in (
    "workspaceAction", "btn_primary", "PointingHandCursor", "clicked.connect")))
check("A8 共享卡片工厂有稳定对象名和统一内边距", all(x in CARD for x in (
    "workspaceCard", "setContentsMargins(20, 18, 20, 18)", "setSpacing(12)")))

print("== B. 七页全部接入共享骨架 ==")
for name, seg in (("生图", IMG), ("生视频", VIDEO), ("数字人", TWIN_BUILD),
                  ("导演台", DIRECTOR_BUILD), ("工具", TOOLS),
                  ("任务", AUTO_BUILD), ("设置", SETTINGS)):
    check("B-%s 使用 build_workspace_shell" % name, "build_workspace_shell(" in seg)
check("B8 数字人正文可滚动，不再压缩全部控件", "scroll=True" in TWIN_BUILD)
check("B9 导演台正文可滚动且底部导演对话仍在页外层", "scroll=True" in DIRECTOR_BUILD
      and "outer.addWidget(bar)" in DIRECTOR_BUILD)
check("B10 设置保留滚动正文", "scroll=True" in SETTINGS)
check("B11 设置内嵌表单标题改为模型与连接，不再重复页面设置",
      'self._popup_title(layout, "模型与连接")' in UI)

print("== C. 创作与系统页内容分区 ==")
for name, seg in (("生图", IMG), ("生视频", VIDEO), ("数字人", TWIN_BUILD)):
    check("C-%s 有生成结果分区" % name, "生成结果" in seg)
check("C1 生图参数卡与结果卡均使用共享卡片", IMG.count("workspace_card(") >= 2)
check("C2 生视频参数卡与结果卡均使用共享卡片", VIDEO.count("workspace_card(") >= 2)
check("C3 数字人结果卡使用共享卡片且有最小结果高度", "workspace_card(" in TWIN_BUILD
      and "twin_result.setMinimumHeight(120)" in TWIN_BUILD)
check("C4 工具技能市场入口迁入页头 action", 'action_text="浏览技能市场"' in TOOLS
      and "market_btn.clicked.connect(self._open_skill_market)" in TOOLS)
check("C5 任务新建入口迁入页头 action", 'action_text="＋ 新建任务"' in AUTO_BUILD
      and "app.auto_new_btn" in AUTO_BUILD)
check("C6 空任务时隐藏页头新建，空态只留一个入口", "auto_new_btn.setVisible(bool(tasks))" in AUTO_REFRESH)
check("C7 有任务时仍保留原新建处理链", "_open_edit(app, None)" in AUTO_BUILD)
check("C8 导演台步骤标签允许随宽度压缩", "QSizePolicy.Ignored" in DIRECTOR_BUILD)
check("C9 导演台步骤间距与内边距收敛，防最后一项裁切",
      "step_lay.setSpacing(4)" in DIRECTOR_BUILD and "padding:0 6px" in fn_seg(DIRECTOR, "_step_style"))
check("C10 剧本与分镜长说明必须换行，不再把滚动正文撑出视口",
      "shint.setWordWrap(True)" in DIRECTOR_BUILD
      and "shhint.setWordWrap(True)" in DIRECTOR_BUILD)
check("C11 导演对话默认收起高度给主操作留首屏空间，展开档不变",
      "H_COLLAPSED = 80" in DIRECTOR_CHAT and "H_EXPANDED = 340" in DIRECTOR_CHAT)

print("== D. 原业务处理链保留 ==")
check("D1 生图按钮仍连接 _gen_image", "gen.clicked.connect(self._gen_image)" in IMG)
check("D2 生视频按钮仍连接 _gen_video", "gen.clicked.connect(self._gen_video)" in VIDEO)
check("D3 生图结果仍双击打开", "image_result.itemDoubleClicked.connect(self._open_image_result)" in IMG)
check("D4 生视频结果仍双击打开", "video_result.itemDoubleClicked.connect(self._open_video_result)" in VIDEO)
check("D5 数字人生成与停止链仍在", "_twin_generate(app)" in TWIN_BUILD and "_twin_stop(app)" in TWIN_BUILD)
check("D6 导演台开始与聊天条仍在", "_director_start(app)" in DIRECTOR_BUILD
      and "DirectorChatBar(app)" in DIRECTOR_BUILD)

print("== E. 共享骨架动态行为（offscreen Qt）==")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QWidget, QLabel, QScrollArea
    import ui_widgets
    app = QApplication.instance() or QApplication([])
    page = QWidget()
    hits = []
    lay, scroll, action = ui_widgets.build_workspace_shell(
        page, "测试标题", "测试副标题", action_text="测试操作",
        action_callback=lambda: hits.append(1))
    check("E1 非滚动骨架不额外包 QScrollArea", scroll is None)
    check("E2 页边距与间距由真实布局生效",
          lay.contentsMargins().left() == 32 and lay.contentsMargins().top() == 28
          and lay.contentsMargins().right() == 32 and lay.contentsMargins().bottom() == 24
          and lay.spacing() == 16)
    title = page.findChild(QLabel, "workspaceTitle")
    subtitle = page.findChild(QLabel, "workspaceSubtitle")
    check("E3 标题与副标题真实创建", title is not None and title.text() == "测试标题"
          and subtitle is not None and subtitle.text() == "测试副标题")
    action.click()
    check("E4 页头 action 点击真实触发回调", hits == [1])
    scroll_page = QWidget()
    _lay2, scroll2, _action2 = ui_widgets.build_workspace_shell(
        scroll_page, "滚动页", "长正文", scroll=True)
    check("E5 滚动骨架返回真实 QScrollArea", isinstance(scroll2, QScrollArea)
          and scroll2.widgetResizable())
    card, card_lay = ui_widgets.workspace_card()
    check("E6 卡片对象名与内边距真实生效", card.objectName() == "workspaceCard"
          and card_lay.contentsMargins().left() == 20 and card_lay.spacing() == 12)
    from types import SimpleNamespace
    from PySide6.QtWidgets import QPushButton
    import automation_panel
    auto_app = SimpleNamespace(
        automation_page=QWidget(),
        automation_store=SimpleNamespace(list_all=lambda: []))
    automation_panel.build_automation_panel(auto_app)
    new_buttons = [b for b in auto_app.automation_page.findChildren(QPushButton)
                   if b.text() == "＋ 新建任务" and not b.isHidden()]
    check("E7 空任务真实只保留一个可用新建入口",
          auto_app.auto_new_btn.isHidden() and len(new_buttons) == 1,
          "未隐藏按钮数=%d" % len(new_buttons))

    # 1280×820 真窗口验收：只看 isVisible 不够，控件下半截可能被
    # 滚动视口裁掉。用窗口命中测试钉住「开始导演」底边仍真实可点。
    import config
    from ui import ChatWindow
    win = ChatWindow(config.load_config())
    win.resize(1280, 820)
    win.show()
    app.processEvents()
    win._switch_nav(6)
    app.processEvents()
    go = win.director_go
    bottom = go.mapTo(win, go.rect().bottomLeft())
    hit = win.childAt(bottom.x() + go.width() // 2, bottom.y() - 2)
    check("E8 1280×820 导演台开始按钮底边仍真实可点",
          hit is go, "命中=%s" % type(hit).__name__)
    overflow = []
    for name, idx in (("生图", 3), ("生视频", 4), ("数字人", 5),
                      ("导演台", 6), ("工具", 7), ("任务", 8), ("设置", 9)):
        win._switch_nav(idx)
        app.processEvents()
        for area in win.main_stack.currentWidget().findChildren(QScrollArea):
            if area.isVisible() and area.horizontalScrollBar().maximum() > 0:
                overflow.append(name)
    check("E9 1280×820 七页均无横向滚动溢出", not overflow, overflow)
    win.close()
    app.processEvents()
except Exception as e:
    check("E 组共享骨架动态行为", False, "%s: %s" % (type(e).__name__, e))

print("\n汇总：PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
