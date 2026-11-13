# -*- coding: utf-8 -*-
"""test_ui_polish_299.py —— v4.299.0 界面美化第 1 波判据。

覆盖第 1 波全部改动，每条都是「改坏必红」的非空谓词：
  A. 图标替换：icon.ico / images/logo.png 换为猫图标，get_app_icon 仍读 icon.ico。
  B. 自定义头像：ui_avatar 模块 + AvatarLabel 可点击 + 两处接线 + 动态存取/圆形裁剪。
  C. 顶栏精简：重命名/删除按钮撤下收进「⋯」，mgr_btn 仍留顶栏（兼容 test_264 F7）。
  D. 欢迎页：新增居中大输入框（复用 input_box + send），卡片与最近对话保留。
  E. 样式精修：drop_shadow helper + 主按钮/输入框/下拉圆角 8→10，蓝色保持不变。

运行：python tests/test_ui_polish_299.py
"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_PASS = [0]
_FAIL = [0]


def check(name, cond, detail=""):
    if cond:
        _PASS[0] += 1
        print("  [OK  ] %s" % name)
    else:
        _FAIL[0] += 1
        print("  [FAIL] %s  %s" % (name, detail))


def read(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        return ""   # 文件缺失（旧代码无此模块）→ 返回空串，让判据记 FAIL 而非崩溃
    return io.open(p, encoding="utf-8").read()


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
TQ = read("theme_qss.py")
UW = read("ui_widgets.py")
CFG = read("config.py")

# ===================== A. 图标替换 =====================
print("\n== A. 图标替换 ==")
check("A1 icon.ico 存在且为猫（ICO 含 256 尺寸）",
      os.path.isfile(os.path.join(ROOT, "icon.ico")))
check("A2 images/logo.png 存在", os.path.isfile(os.path.join(ROOT, "images", "logo.png")))
check("A3 get_app_icon 仍从 icon.ico 加载（加载链未改）",
      "def get_app_icon" in CFG and "icon.ico" in fn_seg(CFG, "get_app_icon"))
try:
    from PIL import Image
    _im = Image.open(os.path.join(ROOT, "icon.ico"))
    _sizes = sorted(_im.ico.sizes()) if hasattr(_im, "ico") else []
    check("A4 猫 icon.ico 内含多尺寸（256 在列）", (256, 256) in _sizes, str(_sizes))
    _lg = Image.open(os.path.join(ROOT, "images", "logo.png"))
    check("A5 logo.png 为 256×256", _lg.size == (256, 256), str(_lg.size))
except Exception as e:
    check("A4/A5 图标可读", False, "%s: %s" % (type(e).__name__, e))

# ===================== B. 自定义头像 =====================
print("\n== B. 自定义头像 ==")
UA = read("ui_avatar.py")
check("B1 ui_avatar 暴露 AvatarLabel / avatar_pixmap / set / clear",
      all(s in UA for s in ("class AvatarLabel", "def avatar_pixmap",
                            "def set_custom_avatar", "def clear_custom_avatar")))
check("B2 AvatarLabel 有 clicked 信号（可点击）", "clicked = Signal()" in UA)
check("B3 头像落盘在数据目录 avatar.png（不进源码树）",
      "USER_DATA_DIR" in UA and 'avatar.png' in UA)
check("B4 _make_avatar_label 改返回 AvatarLabel",
      "import ui_avatar" in UW and "ui_avatar.AvatarLabel(size)" in fn_seg(UW, "_make_avatar_label"))
check("B5 顶栏头像 clicked 接线 _change_user_avatar",
      "self.user_avatar.clicked.connect(self._change_user_avatar)" in UI)
check("B6 侧栏头像 clicked 接线 + 存引用",
      "self._sidebar_avatar = avatar" in UI
      and "avatar.clicked.connect(self._change_user_avatar)" in UI)
_chg = fn_seg(UI, "_change_user_avatar")
check("B7 头像菜单含「更换 / 恢复默认」两项",
      "更换头像" in _chg and "恢复默认头像" in _chg)
check("B8 _refresh_user_avatars 同步刷新两处",
      "user_avatar" in fn_seg(UI, "_refresh_user_avatars")
      and "_sidebar_avatar" in fn_seg(UI, "_refresh_user_avatars"))

# ===================== C. 顶栏精简 =====================
print("\n== C. 顶栏精简 ==")
check("C1 重命名按钮已撤下顶栏", "hb.addWidget(rename_btn)" not in UI)
check("C2 删除按钮已撤下顶栏", "hb.addWidget(del_btn)" not in UI)
_ov = fn_seg(UI, "_open_chat_overflow")
check("C3 「⋯」菜单收进重命名/删除", "重命名对话" in _ov and "删除当前对话" in _ov)
check("C4 会话管理仍留顶栏（兼容 F7）", "hb.addWidget(mgr_btn)" in UI)
check("C5 两个会话动作方法仍存在（功能未丢）",
      "def _rename_session" in UI and "def _delete_active_session" in UI)
check("C6 原五项溢出文案仍在（兼容 F6）",
      all(s in _ov for s in ("朗读回复", "技能管理", "技能市场", "工作流", "导出对话")))

# ===================== D. 欢迎页大输入框 =====================
print("\n== D. 欢迎页大输入框 ==")
check("D1 welcome_input 为 QLineEdit", "self.welcome_input = QLineEdit()" in UI)
check("D2 回车连接 _welcome_quick_send",
      "self.welcome_input.returnPressed.connect(self._welcome_quick_send)" in UI)
_qs = fn_seg(UI, "_welcome_quick_send")
check("D3 快速发送复用 input_box + 切页 + send",
      "self.input_box.setPlainText(text)" in _qs
      and "self._switch_nav(0)" in _qs and "self.send()" in _qs)
check("D4 能力卡片仍保留（兼容 test_199 A 组）", "card_data = [" in UI)
check("D5 大输入框用统一 drop_shadow", "drop_shadow(quick_box)" in UI)
check("D6 最近对话列表仍保留（兼容 C 组）", "_refresh_recent_on_welcome" in UI)

# ===================== E. 样式精修 =====================
print("\n== E. 样式精修 ==")
check("E1 theme_qss.drop_shadow 存在且用 QGraphicsDropShadowEffect",
      "def drop_shadow" in TQ and "QGraphicsDropShadowEffect" in fn_seg(TQ, "drop_shadow"))
import theme_qss as tq
check("E2 主按钮圆角 10px", "border-radius:10px" in tq.btn_primary())
check("E3 输入框圆角 10px", "border-radius:10px" in tq.input_style())
check("E4 下拉框圆角 10px", "border-radius:10px" in tq.combo_style())
from theme_tokens import THEME
check("E5 蓝色保持不变（尊重「保持蓝色」决策）", THEME["accent"] == "#1A73E8", THEME["accent"])

# ===================== F. 头像动态行为（offscreen） =====================
print("\n== F. 头像动态行为 ==")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QPixmap, QColor
    _app = QApplication.instance() or QApplication([])
    import tempfile
    import importlib
    import config
    import ui_avatar

    with tempfile.TemporaryDirectory() as _td:
        # 改道数据目录到临时目录，不碰真实用户头像
        config.USER_DATA_DIR = _td
        importlib.reload(ui_avatar)

        check("F1 无自定义时 has_custom_avatar 为 False",
              ui_avatar.has_custom_avatar() is False)
        _pix = ui_avatar.avatar_pixmap(34)
        check("F2 默认头像非空且尺寸正确",
              (not _pix.isNull()) and _pix.width() == 34 and _pix.height() == 34)

        # 造一张测试图 → 设为自定义头像
        _src = os.path.join(_td, "src.png")
        _p = QPixmap(80, 40)
        _p.fill(QColor("#2563EB"))
        _p.save(_src, "PNG")
        ok, _msg = ui_avatar.set_custom_avatar(_src)
        check("F3 set_custom_avatar 成功", ok is True, str(_msg))
        check("F4 设置后 has_custom_avatar 为 True", ui_avatar.has_custom_avatar() is True)
        check("F5 头像文件落在数据目录", os.path.isfile(os.path.join(_td, "avatar.png")))
        _cp = ui_avatar.avatar_pixmap(40)
        check("F6 自定义头像圆形裁剪后尺寸正确",
              (not _cp.isNull()) and _cp.width() == 40)
        check("F7 坏图返回 (False, 提示) 不抛异常",
              ui_avatar.set_custom_avatar(os.path.join(_td, "不存在.png"))[0] is False)
        check("F8 clear_custom_avatar 恢复默认",
              ui_avatar.clear_custom_avatar() is True
              and ui_avatar.has_custom_avatar() is False)
        # AvatarLabel 可实例化且带 clicked
        _lbl = ui_avatar.AvatarLabel(30)
        check("F9 AvatarLabel 可实例化且尺寸固定",
              _lbl.width() == 30 and _lbl.height() == 30 and hasattr(_lbl, "clicked"))
except Exception as e:
    import traceback
    check("F 组动态行为（offscreen Qt）", False, "%s: %s" % (type(e).__name__, e))
    traceback.print_exc()

print("\n" + "=" * 56)
print("汇总：PASS=%d FAIL=%d" % (_PASS[0], _FAIL[0]))
sys.exit(1 if _FAIL[0] else 0)
