# -*- coding: utf-8 -*-
"""空状态组件 波次 3 验收 + 全项目回扫（v4.208.0，DESIGN.md §12 波次 3）

本波做两件事：
  1. 接 #7a 军团波次成员空态（完整形态）
  2. **全项目回扫验收** —— 这是 §12.3 对波次 3 的原始承诺，一直没做

回扫的最大收获不是"发现漏接"，而是发现 **§12.1 的清点口径漏了一类**：
「操作无结果的一次性回执」（_set_status / QMessageBox.information / chat_panel.say），
全项目约 30 处。它没有"区域"可占，从头到尾就该排除在组件化之外。

判据分六类（独立运行：python tests/test_empty_state_208.py）：

A 接线#7a     —— legion_ui 真跑 _rebuild_waves（桩 legion），看真出组件
B 视觉与参数  —— 尺寸/文案写死；不给行动按钮（下方已有真按钮）
C #7b 不接    —— 授权记录仍是 MessageBox 一次性回执，守护判据
D 导演台 2 处 —— 与 #2 同类，只统一文案不接组件
E 全项目回扫  —— 分类判据：5 类形态各有归属，没有"漏网的真空区域"
F 版本/文档   —— DESIGN/CHANGELOG 记了本波的决定

沿用L181（不拿被测常量当基准）/ L185（要有反向计数）/ L190（先剥注释再数字符串）。
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
    QApplication, QLabel, QPushButton, QWidget,
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

import empty_state as ES       # noqa: E402
import theme_qss as TQ         # noqa: E402

LEGION = os.path.join(ROOT, "legion_ui.py")


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def strip_comments(src):
    """剥注释（见 L190：注释里会故意引用旧文案解释改动，不剥会误判残留）。"""
    out = []
    for ln in src.replace("\r\n", "\n").split("\n"):
        if ln.strip().startswith("#"):
            continue
        out.append(re.sub(r"#.*$", "", ln))
    return "\n".join(out)


def lines_of(name):
    with open(os.path.join(ROOT, name), encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n").split("\n")


_leg_src = read("legion_ui.py")
_leg_code = strip_comments(_leg_src)
_leg_lines = lines_of("legion_ui.py")
_ui_code = strip_comments(read("ui.py"))
_dp_code = strip_comments(read("director_panel.py"))
_ap_code = strip_comments(read("automation_panel.py"))

print("=== v4.208.0 空状态 波次 3 验收 + 全项目回扫（DESIGN §12）===")

# ------------------------------------------------------------- A 接线 #7a

print("\n--- A #7a 军团波次成员空态接线 ---")
check("A1 legion_ui.py import 了 empty_state",
      re.search(r"^\s*from\s+empty_state\s+import\s+empty_state", _leg_src, re.M)
      is not None)
check("A2 旧的括号注记已移除",
      'QLabel("（本波还没有成员' not in _leg_code)
check("A3 旧文案在代码里零残留",
      "本波还没有成员，点下面" not in _leg_code)
check("A4 组件调用已就位", "empty_state(" in _leg_code)
# v4.210.0：1 → 2（新增 0 波空态）—— 本波**有意**再加一处，不是误加
check("A5 legion_ui 调用点写死为 2（波次成员 + 0 波空态）",
      len(re.findall(r"\bempty_state\(", _leg_code)) == 2,
      len(re.findall(r"\bempty_state\(", _leg_code)))
check("A6 主文案写死", '"本波还没有成员"' in _leg_code)
check("A7 副文案写死且指向真实存在的按钮",
      '"点下面「+ 添加成员」给他派活。"' in _leg_code)
check("A8 图标用「军团」（已验证在 _NAV_ICONS 里）", 'icon="军团"' in _leg_code)
check("A9 **不给行动按钮**（下方 2798 行已有真按钮，重复入口是设计错误）",
      "action=" not in _leg_code.split("empty_state(")[1].split(")")[0])

# 运行时：真跑 _rebuild_waves
print("\n--- A2 运行时：桩数据源真跑 _rebuild_waves ---")

import legion_ui as LU      # noqa: E402


class _FakeHost:
    """只桩掉「数据来源」和「三个宿主状态」，其余全走真代码。

    刻意**不替换 QGroupBox** —— 上一版把它换成假类，结果 gate_box 那段
    真控件（_NoWheelCombo / QSpinBox…）全要跟着桩，桩得越多越测不到东西。
    改成只桩legion.find_project（返回固定项目）+ 借真的 _clear_layout /
    _rebuild_waves，这样波次框和空态组件都是**真控件**。
    """

    def __init__(self, project):
        from PySide6.QtWidgets import QVBoxLayout
        self.data = {"projects": [project]}
        self.waves_lay = QVBoxLayout()
        self._gate_rebuild_pending = False
        self.cur_project_id = project.get("id", "p1")   # _cur_project 会用到
        self.added = None

    # 借 LegionWindow 的真实现（这两个是纯布局/查数据的辅助，没有 UI 依赖）
    _clear_layout = LU.LegionWindow._clear_layout
    _cur_project = LU.LegionWindow._cur_project
    # gate_box 里的按钮会连到这里；空态路径不点它，借真实现即可（它只弹 MessageBox）
    _on_revoke_trust = LU.LegionWindow._on_revoke_trust
    _on_view_auth = LU.LegionWindow._on_view_auth

    def _add_member(self, wi):
        self.added = wi


import legion as _legion_mod      # noqa: E402

_orig_find = _legion_mod.find_project
try:
    _proj = {"name": "测试项目", "waves": [{"members": []}]}
    _host = _FakeHost(_proj)
    _legion_mod.find_project = lambda data, pid: _proj
    LU.LegionWindow._rebuild_waves(_host)
    # 第 0 项是 gate_box（真 QGroupBox），波次框在其后
    _gate = _host.waves_lay.itemAt(0).widget()
    _w0 = _host.waves_lay.itemAt(1).widget()
    _ls = _w0.findChildren(QLabel) if _w0 else []
    _texts = [l.text() for l in _ls]
    check("A10 波次框里出现了空态主文案", "本波还没有成员" in _texts, _texts)
    check("A11 副文案也在", any("添加成员" in t for t in _texts), _texts)
    check("A12 徽章渲染成功（44px 控件存在）",
          any(l.width() == 44 for l in _ls), [(l.text(), l.width()) for l in _ls])
    # A13：第一版查的是"整个波次框里没有 QPushButton" —— 错，因为波次框下方
    # 本来就有「+ 添加成员」「删除本波」两个真按钮（2798/2800 行）。
    # 真正要守的是「**空态组件自己**没渲染按钮」，所以只查组件那棵子树。
    _es_sub = [w for w in _w0.findChildren(QWidget)
               if w.__class__.__name__ == "QWidget"
               and w.styleSheet() == "background:transparent;"]
    _es_btns = []
    for _w in _es_sub:
        _es_btns.extend(_w.findChildren(QPushButton))
    check("A13 空态组件自己没渲染行动按钮（不制造重复入口）",
          len(_es_btns) == 0, len(_es_btns))
    check("A13c 波次框里「+ 添加成员」真按钮仍在（没被组件顶掉）",
          any(b.text() == "+ 添加成员" for b in _w0.findChildren(QPushButton)),
          [b.text() for b in _w0.findChildren(QPushButton)])
    check("A13b gate_box 仍是真 QGroupBox（证明没被桩掉）",
          _gate is not None and _gate.__class__.__name__ == "QGroupBox",
          type(_gate).__name__ if _gate else None)

    # 有成员时不该出空态
    _proj2 = {"name": "有成员", "waves": [{"members": [
        {"name": "小臭", "emoji": "🤖", "tools": [], "skills": [],
         "archived_skills": []}]}]}
    _host2 = _FakeHost(_proj2)
    _legion_mod.find_project = lambda data, pid: _proj2
    LU.LegionWindow._rebuild_waves(_host2)
    _w1 = _host2.waves_lay.itemAt(1).widget()
    _t1 = [l.text() for l in _w1.findChildren(QLabel)] if _w1 else []
    check("A14 有成员时不出现空态", "本波还没有成员" not in _t1, _t1)
    check("A15 有成员时该成员名字确实渲染了（反向：不是把所有行都吞了）",
          any("小臭" in t for t in _t1), _t1)
finally:
    _legion_mod.find_project = _orig_find

# --------------------------------------------------------- B 视觉与参数

print("\n--- B 视觉与参数 ---")
check("B1 组件仍是 44px 徽章（完整形态，没误用 compact）",
      ES.BADGE_SIZE == 44, ES.BADGE_SIZE)
check("B2 legion_ui 用的是完整形态（没传 compact）",
      "compact=True" not in _leg_code)
check("B3 文案去掉了圆括号注记语气",
      "（本波还没有成员" not in _leg_code)
# B4：要查**同一段注释块内**的理由。
# 踩过的坑：`find("# v4.208.0")` 撞到文件里**第一处**（61 行的 import 说明），
# 不是波次空态那段（2755 行）—— 定位必须用完整锚点。
_seg_start = _leg_src.find("# v4.208.0：接empty_state 完整形态")
_seg = _leg_src[_seg_start:_seg_start + 800] if _seg_start >= 0 else ""
check("B4 理由写进了代码注释（查原始源码，剥了注释就查不到）",
      all(k in _seg for k in ("这块区域", "重复入口", "QTextEdit", "2798")),
      [k for k in ("这块区域", "重复入口", "QTextEdit", "2798") if k not in _seg])

# ------------------------------------------------------------ C #7b 不接

print("\n--- C #7b 授权记录不接组件（守护判据）---")
check("C1 授权记录仍是 QMessageBox 一次性回执",
      'QMessageBox.information(self, "授权记录"' in _leg_code)
check("C2 那句文案未被改动",
      "还没有任何授权记录。跑一次带验收的军团后这里就有。" in _leg_code)
# C3：反向 —— 授权记录那段里不许出现 empty_state
_auth_seg = _leg_code.split('def _on_view_auth')[1].split("\n    def ")[0] \
    if "def _on_view_auth" in _leg_code else ""
check("C3 _on_view_auth 里没有 empty_state",
      "empty_state" not in _auth_seg,
      [l for l in _auth_seg.split("\n") if "empty_state" in l])
check("C4 授权记录空态没被加上图标（回执不该长成空态卡）",
      "empty_state(" not in _auth_seg)

# --------------------------------------------------------- D 导演台 2 处

print("\n--- D 导演台 2 处：统一文案不接组件 ---")
check("D1 旧文案（带括号）已清零",
      "（暂无，这镜可能还没生成" not in _dp_code
      and "（暂无，可能这镜还没生成过" not in _dp_code)
check("D2 新文案写死（第 1 处）",
      '"暂无，这镜可能还没生成 / 或被重置"' in _dp_code)
check("D3 新文案写死（第 2 处）", '"暂无，可能这镜还没生成过"' in _dp_code)
check("D4 director_panel 未 import empty_state（确认不接组件）",
      not re.search(r"^\s*from\s+empty_state\s+import", read("director_panel.py"), re.M))
check("D5 两处仍是 setPlainText（框保留）",
      _dp_code.count("setPlainText(prompt or ") == 2,
      _dp_code.count("setPlainText(prompt or "))

# ------------------------------------------------------- E 全项目回扫验收

print("\n--- E 全项目回扫（§12.3 对波次 3 的承诺）---")
# E1：接组件的4 处（+本波 1 处= 5 个调用点，分布在 3 个文件）
_counts = {
    "ui.py": len(re.findall(r"\bempty_state\(", _ui_code)),
    # v4.216.0：SessionManagerDialog 的 2 处随对话框族迁到 ui_widgets.py（搬家）
    "ui_widgets.py": len(re.findall(
        r"\bempty_state\(", strip_comments(read("ui_widgets.py")))),
    "automation_panel.py": len(re.findall(r"\bempty_state\(", _ap_code)),
    "legion_ui.py": len(re.findall(r"\bempty_state\(", _leg_code)),
}
# v4.210.0：4+1+1=6 → 5+1+2=8；v4.216.0：ui 5 → ui 3 + ui_widgets 2（总数不变）
check("E1 全项目 empty_state 调用点 = 3+2+1+2 = 8",
      _counts == {"ui.py": 3, "ui_widgets.py": 2,
                  "automation_panel.py": 1, "legion_ui.py": 2},
      _counts)
# E2：组件化只碰了这 4 个文件，没扩散（ui_widgets 是 v4.216.0 搬家带入）
#注意要排除 empty_state.py 自己（它当然"import 自己"的匹配串）——
# 第一版没排除，把组件自己算成"扩散"了，红得莫名其妙。
_all_py = [f for f in os.listdir(ROOT)
           if f.endswith(".py") and not f.startswith("_")
           and f not in ("setup.py", "empty_state.py")]
_users = sorted(f for f in _all_py
                if re.search(r"^\s*from\s+empty_state\s+import", read(f), re.M))
check("E2 只有 4 个文件 import 了 empty_state（未扩散）",
      _users == ["automation_panel.py", "legion_ui.py", "ui.py", "ui_widgets.py"],
      _users)
# E3：第 5 类形态（一次性回执）全项目存在，但**一处都没接组件** —— 分类判据
_receipt_pat = re.compile(
    r"_set_status\(|QMessageBox\.information\(|chat_panel\.say\(")
_n_receipt = sum(1 for f in _all_py
                 for ln in strip_comments(read(f)).split("\n")
                 if _receipt_pat.search(ln))
check("E3 第 5 类（一次性回执）确实遍布项目（回扫的前提）",
      _n_receipt >= 20, f"共 {_n_receipt} 处")
# E4：这些回执所在的函数里不该有 empty_state（分类落实的硬判据）
# 判据实现的坑：用 `^(\s*)def X\(.*?(?=^\1def |\Z)` 配 re.S 时，\Z 分支会在
# 遇到**缩进更浅的非 def 行**（比如顶层代码）时一路吃到文件尾，把整个后半段
# 都算成"函数体" —— 于是 legion_ui/ui里明明是不同函数的东西被判成同一体。
# 改成按缩进块切：只有缩进比 def 更深的行才算函数体。
def _iter_func_bodies(src):
    lines = src.replace("\r\n", "\n").split("\n")
    cur_name, cur_indent, buf = None, None, []
    for ln in lines:
        m = re.match(r"^(\s*)def (\w+)\(", ln)
        if m:
            if cur_name:
                yield cur_name, "\n".join(buf)
            cur_name, cur_indent, buf = m.group(2), len(m.group(1)), [ln]
            continue
        if cur_name is None:
            continue
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= cur_indent:
            yield cur_name, "\n".join(buf)
            cur_name, cur_indent, buf = None, None, []
            if m:  # 顶层 def 已处理
                pass
        else:
            buf.append(ln)
    if cur_name:
        yield cur_name, "\n".join(buf)


_bad = []
_receipt_funcs = 0
for f in _all_py:
    src = strip_comments(read(f))
    for fname, body in _iter_func_bodies(src):
        if not (_receipt_pat.search(body) or "MessageBox" in body):
            continue
        _receipt_funcs += 1
        if "empty_state(" in body:
            _bad.append(f"{f}:{fname}")
check("E4a 确实扫到了多处带弹窗/回执的函数（判据前提）",
      _receipt_funcs >= 10, f"扫到 {_receipt_funcs} 个")
# E4b 第一版写成"带MessageBox 的函数里不许有 empty_state"，红在
# `_open_skill_review_dialog`（合法组合：空态卡 + 关闭按钮）。
# 第二版改成"回执式文案（还没有X）不许进 empty_state"，又红在
# `_refresh_list`（"还没有任务"）—— 这两处是**合法空态**。
# 教训：「还没有…」这种句式本身**不能**用来区分空态与回执；
# 区别在于**有没有区域可占**。所以只能按调用点逐个写死，不许做句式推断。
_known_ok = {
    "还没有对话",# ui.py 会话空态（区域空，合法）
    "还没有会话",         # ui.py 搜索空态 compact（区域空，合法）
    "还没有任务",         # automation_panel（区域空，合法）
    "本波还没有成员",     # legion_ui（GroupBox 区域空，合法）
    "暂无待审核技能",     # ui.py 技能审核（区域空，合法）
    # v4.210.0 新增两处（DESIGN §12.8），都是「区域空」不是回执：
    "还没有波次",         # legion_ui 波次区（删光最后一波后，区域空）
    "这里是最近对话",     # ui.py 欢迎页（有会话但被过滤掉，列表区空）
}
_seen = set()
for f in _all_py:
    src = strip_comments(read(f))
    for fname, body in _iter_func_bodies(src):
        for m in re.finditer(r'empty_state\(\s*"([^"]*)"', body):
            _seen.add(m.group(1))
check("E4b 传给 empty_state 的主文案全部是已登记的合法空态（无一是回执）",
      _seen <= _known_ok, sorted(_seen - _known_ok))
check("E4b2 5 处合法空态都真的在用（防止把判据放水成空集）",
      len(_seen & _known_ok) >= 5, sorted(_seen))
# 真正的硬判据：#7b 授权记录那句回执**仍在 MessageBox 里**（没被删），
# 且**没有**出现在任何 empty_state 的参数里。
# 注意第一版写成"文案全项目零残留"是错的 —— 那句回执本来就该存在于
# _on_view_auth 里（它就是一次性回执的正文），要守的是"别进 empty_state"。
_auth_seg2 = _leg_code.split("def _on_view_auth")[1].split("\n    def ")[0] \
    if "def _on_view_auth" in _leg_code else ""
check("E4b3 授权记录回执仍在 MessageBox 里（没被删掉）",
      "还没有任何授权记录" in _auth_seg2
      and "QMessageBox.information" in _auth_seg2)
check("E4b4 它没有出现在 empty_state 的参数里",
      "还没有任何授权记录" not in _seen,
      sorted(_seen))
# E4c 导演台那两处「一次性回执式」的状态提示不许接组件
_dp_funcs = dict(_iter_func_bodies(_dp_code))
check("E4c 导演台 _set_status 自身没被改成组件",
      "empty_state(" not in _dp_code.split("def _set_status")[1].split("\ndef ")[0])
# E5 导演台那类 QTextEdit 占位串也不接（与 #2 同类）
# 实际是 4 处不是 2 处：2 处带兜底文案（2938/2980，本轮统一了措辞）
# + 2 处 `setPlainText(text or "")`（1955/4056，空串兜底、无文案，本来就不用改）
_te_ph = re.findall(r'setPlainText\(\s*\w+\s+or\s+"', _dp_code)
check("E5 导演台 QTextEdit 占位串共 4 处（框全部保留，未被换成组件）",
      len(_te_ph) == 4, len(_te_ph))
check("E5b 其中 2 处有兜底文案、2 处是空串兜底",
      len(re.findall(r'setPlainText\(prompt or "', _dp_code)) == 2
      and len(re.findall(r'setPlainText\(text or ""\)', _dp_code)) == 2,
      (len(re.findall(r'setPlainText\(prompt or "', _dp_code)),
       len(re.findall(r'setPlainText\(text or ""\)', _dp_code))))

# ----------------------------------------------------------- F 版本与文档

print("\n--- F 文档与版本 ---")
_design = read("DESIGN.md")
check("F1 DESIGN 记了波次 3 的接线决定", "本波还没有成员" in _design)
check("F2 DESIGN 记了 #7b 不接的理由", "一次性回执" in _design)
check("F3 DESIGN 的清点口径已从 4 类改成 5 类（多处交叉印证，不靠单一串）",
      "第 5 类" in _design and "五种" in _design
      and "一次性回执" in _design and "没有\"区域\"可占" in _design,
      [k for k in ("第 5 类", "五种", "一次性回执", '没有"区域"可占')
       if k not in _design])
_cl = read("CHANGELOG.md")
check("F4 CHANGELOG 有 v4.208.0 条目", "v4.208.0" in _cl)
_cfg = read("config.py")
# F5 第一版把版本号**写死**成 v4.208.0 —— 下一个版本一升它就红，
# 于是每版都要来改一次判据（v4.209 就撞上了）。改成**跟随当前版本**：
# 只要求 config 里有一个 vX.Y.Z 形式的版本号，且与 README 一致。
# 「版本号三方一致」这件事本身由 release_check.py 把关，这里不重复。
_m = re.search(r'APP_VERSION = "v(\d+)\.(\d+)\.(\d+)"', _cfg)
check("F5 config.APP_VERSION 存在且是 vX.Y.Z 形式", _m is not None,
      _cfg[_cfg.find("APP_VERSION"):_cfg.find("APP_VERSION") + 40])
# README 里版本号带粗体标记（`**v4.209.0**`），且 config.py 里还留着更早的
# 历史版本号（v4.164.0 等）。所以两边都要**精确定位**：
# config 抓 `APP_VERSION = "..."`，README 抓「当前版本」后面第一个版本号
#（中间可能夹 `**` 等markdown 标记）。
_rd = read("README.md")
_r = None
_i = _rd.find("当前版本")
if _i >= 0:
    _r = re.search(r'v\d+\.\d+\.\d+', _rd[_i:_i + 60])
check("F5b README 版本与 config 一致（跟随当前版本，不写死）",
      _m and _r and _m.group(0).split('"')[1] == _r.group(0),
      f"config={_m.group(0) if _m else '?'} README={_r.group(0) if _r else '?'}")

print(f"\nPASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
