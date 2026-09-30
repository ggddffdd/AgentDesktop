"""v4.181.1 回归：Qt.Popup 内嵌弹层（设置 / 技能库）必须被钳进屏幕可用区。

背景：v4.180.0 修的是 **QDialog 类**弹窗（`clamp_dialog_to_screen`），大哥实测
「授权弹窗超出屏幕点不到」已解决。但「设置」「技能库」这两个是 **Qt.Popup 内嵌弹层**，
走的是另一条链路 `_show_popup → move(pos) + show()` —— 既不限高也不钳位。
设置弹层有 5 个分组，内容总高轻松过 700px，而笔记本 @150% 缩放可用高度可能
只有 472px → 大哥实测「设置页的设置弹层向下突出屏幕」。

本套件钉住三件事：
  1. 源码接线上：`_popup_base` 必须包 QScrollArea（内容可滚动，限高不丢内容）；
     `_show_popup` 必须调用 `clamp_popup_to_screen`（不能退回裸 move+show）。
  2. 纯函数行为：`clamp_popup_to_screen` 在「下方放不下」时必须上移、在「右边越界」
     时必须左推，且结果整块落在可用区内。
  3. 内容安全性：限高后内容走滚动容器，而不是被裁掉。

用 AST + 假对象（不真起 Qt 事件循环），保证可在 CI / offscreen 下跑。
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_p = _f = 0


def check(label, got, exp=True, extra=""):
    global _p, _f
    ok = (got == exp)
    _p += ok
    _f += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<52} got={got!s:<6} exp={exp}  {extra}")


def main():
    src_path = os.path.join(ROOT, "ui.py")
    # ui.py 带 UTF-8 BOM，ast.parse 不认 → 用 utf-8-sig 吃掉 BOM
    with open(src_path, encoding="utf-8-sig") as f:
        src = f.read()
    tree = ast.parse(src)

    print("-- 1) 源码接线 --")

    # _popup_base 必须自建 QScrollArea（内容可滚动）
    popup_base = None
    show_popup = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name == "_popup_base":
                popup_base = node
            elif node.name == "_show_popup":
                show_popup = node

    check("_popup_base 存在", popup_base is not None)
    check("_show_popup 存在", show_popup is not None)

    pb_src = ast.get_source_segment(src, popup_base) or ""
    check("_popup_base 建了 QScrollArea",
          "QScrollArea()" in pb_src)
    check("_popup_base 把内容装进 scroll（setWidget）",
          "scroll.setWidget(inner)" in pb_src)

    sp_src = ast.get_source_segment(src, show_popup) or ""
    check("_show_popup 调用 clamp_popup_to_screen",
          "clamp_popup_to_screen(" in sp_src)
    # 不能退回裸 move（钳制函数内部会 move，这里指不能出现「move 完就直接 show」的旧写法）
    check("_show_popup 不再裸 move(pos)",
          "popup.move(pos)" not in sp_src)

    # 模块级必须有 clamp_popup_to_screen 定义
    has_fn = any(isinstance(n, ast.FunctionDef) and n.name == "clamp_popup_to_screen"
                 for n in tree.body)
    check("模块级定义 clamp_popup_to_screen", has_fn)

    print("-- 2) 纯函数行为（假 Qt 对象）--")

    # 造一个最小可用的假 popup：只实现 clamp_popup_to_screen 用到的接口
    class FakeScreen:
        def __init__(self, x, y, w, h):
            self._g = (x, y, w, h)

        def availableGeometry(self):
            class G:
                pass
            g = G()
            g.x = lambda: self._g[0]
            g.y = lambda: self._g[1]
            g.width = lambda: self._g[2]
            g.height = lambda: self._g[3]
            return g

    class FakePopup:
        def __init__(self, w, h, screen):
            self._w, self._h = w, h
            self._screen = screen
            self.moved_to = None
            self.max_h = None
            self.max_w = None

        def screen(self):
            return self._screen

        def setMaximumHeight(self, v):
            self.max_h = v

        def setMaximumWidth(self, v):
            self.max_w = v

        def adjustSize(self):
            pass  # 保持 sizeHint 尺寸

        def width(self):
            return self._w

        def height(self):
            return self._h

        def resize(self, w, h):
            self._w, self._h = w, h

        def move(self, x, y):
            self.moved_to = (x, y)

    # 用 exec 把真函数抠出来跑（不 import ui —— 它要拉起 Qt 依赖）
    fn_src = None
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "clamp_popup_to_screen":
            fn_src = ast.get_source_segment(src, n)
    ns = {"QPoint": lambda x, y: type("P", (), {"x": lambda s: x, "y": lambda s: y})()}
    exec(fn_src, ns)
    clamp = ns["clamp_popup_to_screen"]

    # 场景 A：可用区 1536×912 @125%，弹层高 800，锚点 y=12 → 能放下，保持锚点
    scr_a = FakeScreen(0, 0, 1536, 912)
    pa = FakePopup(360, 800, scr_a)
    r = clamp(pa, type("P", (), {"x": lambda s: 300, "y": lambda s: 12})(), margin=16)
    check("A 高度被限到可用区内", pa.height() <= 912 - 32)
    check("A 底部不越界", r.y() + pa.height() <= 912)
    check("A 保留锚点 y=12", r.y() == 12)

    # 场景 B：可用区 911×472（1366×768@150%），弹层高 800 → 放不下，必须上移
    scr_b = FakeScreen(0, 0, 911, 472)
    pb = FakePopup(360, 800, scr_b)
    r = clamp(pb, type("P", (), {"x": lambda s: 200, "y": lambda s: 12})(), margin=16)
    check("B 高度被限到 ≤440", pb.height() <= 472 - 32)
    check("B 底部不越界（核心：不再向下突出屏幕）",
          r.y() + pb.height() <= 472)
    check("B 顶部不越界", r.y() >= 0)

    # 场景 C：右边越界（弹层贴着侧边栏右缘弹出，宽 360，可用宽 300）
    scr_c = FakeScreen(0, 0, 300, 600)
    pc = FakePopup(360, 400, scr_c)
    r = clamp(pc, type("P", (), {"x": lambda s: 290, "y": lambda s: 12})(), margin=16)
    check("C 右边不越界", r.x() + pc.width() <= 300)
    check("C 左边不越界", r.x() >= 0)

    # 场景 D：带任务栏偏移的可用区（x=0,y=40 起）—— 必须按可用区原点算，不是屏幕原点
    scr_d = FakeScreen(0, 40, 1280, 640)
    pd = FakePopup(360, 900, scr_d)
    r = clamp(pd, type("P", (), {"x": lambda s: 100, "y": lambda s: 12})(), margin=16)
    check("D 顶部不低于可用区 y=40", r.y() >= 40)
    check("D 底部不越界", r.y() + pd.height() <= 40 + 640)

    print("-- 3) 内容安全性 --")
    check("限高后仍设了 maximumHeight（防内容再撑破）",
          pb.max_h is not None and pb.max_h <= 472 - 32)

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
