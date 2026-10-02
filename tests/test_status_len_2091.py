# -*- coding: utf-8 -*-
"""状态栏文案长度验收（v4.209.1，DESIGN.md §13.4）

背景：v4.209.0 波 A 改完 12 处回执文案后，我**没量就断言**"20~28 字大概率仍被
截断"，并据此排了波 B（开 wordWrap + 限高 2 行）。v4.209.1 实测发现：
**原假设不成立** —— 导演台状态栏是**全宽**的，不是窄栏。

实测数据（真造 QLabel 跑出来的，不是公式估）：
  容器 700px → 标签可用 668px   容器 520px → 可用 488px
  容器 620px → 标签可用 588px   容器 460px → 可用 432px（卡满点）

所以真正该做的只有一件：把**最贴边的那条**（原 432px）改短。
本判据守的就是「不再有贴边文案」这件事。

判据分四类（独立运行：python tests/test_status_len_2091.py）：

A 长度守门   —— 真造 QLabel + QFontMetrics 实测，不是估宽公式
B 改后复扫   —— 导演台全部 _set_status 文案里无 ≥THRESH 的
C 布局未动   —— 没有 wordWrap / 限高（v4.209.1 明确**不开**）
D 文档与版本

沿用 L181（期望值写死，不拿被测量当基准）/ L196（版本跟随式，不写死）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QFont, QFontMetrics  # noqa: E402

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

# ---- 阈值：实测得来，不是猜的 ------------------------------------------------
# 容器 460px → 标签可用 432px（内边距 16×2 后）。这是实测的卡满点。
# 取它当阈值：文案宽度 ≥ 460px 视为"贴边"（在更窄的容器里会被截）。
THRESH_PX = 460
# v4.209.1 改后应达到的上限（408px 那条）。写死，不拿被测量当基准（L181）。
EXPECT_MAX_PX = 408

# 12px 中文字体：9pt。**必须与 director_panel 里label_second() 的实际字号一致**，
# 否则量出来的宽没有意义。
_F = QFont()
_F.setPointSize(9)
_FM = QFontMetrics(_F)


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def strip_comments(src):
    """剥注释（L190：注释里会引用旧文案）。"""
    out = []
    for ln in src.replace("\r\n", "\n").split("\n"):
        if ln.strip().startswith("#"):
            continue
        out.append(re.sub(r"#.*$", "", ln))
    return "\n".join(out)


_dp_raw = read("director_panel.py")
_dp = strip_comments(_dp_raw)

# 抓所有 _set_status(app, "字面量") 的文案（_RAW 保留全部，_ALL 去重后按宽降序）
_RAW = []
for _m in re.finditer(r'_set_status\(\s*app,\s*("(?:[^"\\]|\\.)*")', _dp):
    _t = _m.group(1)[1:-1].replace("\\n", " ")
    if _t:
        _RAW.append(_t)
_seen = {t: _FM.horizontalAdvance(t) for t in _RAW}
_ALL = sorted(_seen.items(), key=lambda x: -x[1])

print("=== v4.209.1 状态栏文案长度验收（DESIGN §13.4）===")

# ------------------------------------------------------------- A 长度守门

print("\n--- A 真造 QLabel 实测（非估宽公式）---")
_probe = "已回滚上游资产：关键帧/视频/成片已标记「基于旧资产」，建议重新生成"
check("A1 QFontMetrics 可用（9pt ≈ 12px）", _FM.horizontalAdvance("测试") > 0)
check("A2 改后的回滚文案实测宽度 == 期望值（写死 408）",
      _FM.horizontalAdvance(_probe) == EXPECT_MAX_PX,
      f"实测 {_FM.horizontalAdvance(_probe)}px，期望 {EXPECT_MAX_PX}px")
# 反向：真造控件验证"宽度够用"这个结论，不是只看字宽数字
for _cw, _expect_ok in ((620, True), (520, True), (460, True)):
    _h = QWidget(); _h.resize(_cw, 120)
    _l = QVBoxLayout(_h); _l.setContentsMargins(16, 16, 16, 16)
    _lb = QLabel(_probe); _lb.setStyleSheet("font-size:12px;")
    _l.addWidget(_lb)
    _h.show(); _app.processEvents()
    check(f"A3 容器 {_cw}px 下不截断（标签可用 {_lb.width()}px）",
          _lb.width() >= _FM.horizontalAdvance(_probe) if _expect_ok else True,
          f"可用 {_lb.width()}px < 需要 {_FM.horizontalAdvance(_probe)}px")
    _h.close()

# ------------------------------------------------------------- B 改后复扫

print("\n--- B 导演台全部 _set_status 文案复扫---")
check("B1 抓到的文案条数 == 55（写死，不拿被测量当基准）",
      len(_RAW) == 55, len(_RAW))
check("B1b 去重后 == 50 条", len(_ALL) == 50, len(_ALL))
# B1c 把"为什么是 55 不是 56"记进判据：全项目有 1 处 `_set_status(app, "")`
# 是"清空状态栏"用的空串，被 `if _t:` 过滤掉了。基准值 55 = 56 - 1。
# 第一版我按 56 写死 → 判据自红；查下去才发现是基准值取错，不是代码变了。
check("B1c 差的那 1 条是「清空状态栏」的空串调用（解释 55 这个数）",
      len(re.findall(r'_set_status\(\s*app,\s*""', _dp)) == 1,
      len(re.findall(r'_set_status\(\s*app,\s*""', _dp)))
_over = [(t, w) for t, w in _ALL if w >= THRESH_PX]
check(f"B2 无 ≥{THRESH_PX}px 的贴边文案", not _over,
      [f"{w}px {t[:30]}" for t, w in _over[:3]])
check(f"B3 全局最长 ≤ {EXPECT_MAX_PX}px",
      _ALL[0][1] <= EXPECT_MAX_PX, f"最长 {_ALL[0][1]}px：{_ALL[0][0][:30]}")
print(f"      （去重后共 {len(_ALL)} 条，前 3："
      f"{[f'{w}px' for _, w in _ALL[:3]]}）")

# 那条最长的确实被改了
# 那条最长的确实被改了：B4 第一版只查"'下游'不在 _dp"，
# 换一句同样不含"下游"但更长的文案照样绿 → 改成要求**完整原文在位**。
_ROLLBACK = "已回滚上游资产：关键帧/视频/成片已标记「基于旧资产」，建议重新生成"
check("B4 改短后的回滚文案完整在位（不是只查'下游'不在）",
      _ROLLBACK in _dp)
check("B4b 原 432px 版已不存在",
      "已回滚上游资产：下游关键帧" not in _dp)
check("B4c 该文案实测就是 408px（登记的期望值）",
      _FM.horizontalAdvance(_ROLLBACK) == EXPECT_MAX_PX,
      _FM.horizontalAdvance(_ROLLBACK))
check("B5 改动的理由写进了代码注释",
      "432px" in _dp_raw and "余量" in _dp_raw)

# ------------------------------------------------------------- C 布局未动

print("\n--- C 明确不开 wordWrap（v4.209.1 的结论）---")
check("C1 director_status 没有 setWordWrap",
      "director_status.setWordWrap" not in _dp)
check("C2 没给状态栏加固定高度/限高",
      not re.search(r'director_status\.setFixedHeight|director_status\.setMaximumHeight',
                    _dp))
check("C3 _set_status 函数本身未被改（仍只设 color + text）",
      'color = THEME["accent"] if not err else THEME["danger_text"]' in _dp
      and "setStyleSheet(f\"color:{color};font-size:12px;\")" in _dp)
# C3b：第一版只查了 setStyleSheet 那行 —— 有人把 setText 改成"塞个 \n 折成两行"
# 照样能过（v4.209.1 扰动第 3 条就漏在这）。补：setText 必须还是原样单句。
check("C3b setText 仍是原样单句（没被改成两行渲染）",
      "    app.director_status.setText(text)" in _dp_raw,
      [ln.strip() for ln in _dp_raw.split("\n")
       if "director_status.setText" in ln])
check("C3c 状态栏里没有人为插入的换行符",
      not re.search(r'director_status\.setText\([^)]*\\n', _dp))
check("C4 QLabel 仍是单行未换行（构造处没加 wordWrap 参数）",
      'app.director_status = QLabel("")' in _dp)

# ------------------------------------------------------------- D 文档与版本

print("\n--- D 文档与版本---")
_dz = read("DESIGN.md")
# D1/D2 第一版只查"13.4 存在"一个串 —— 把整节内容换掉也不红。
# 改成：切出 §13.4 那一节，**降级理由 + 阈值来源 + 数据表**必须同时在场。
_i = _dz.find("### 13.4")
_s = _dz[_i:_i + 2000] if _i >= 0 else ""
check("D1 DESIGN §13.4 记了波 B 降级这个决定（不只是有小节号）",
      "降级" in _s and "假设不成立" in _s,
      [k for k in ("降级", "假设不成立") if k not in _s])
check("D2 DESIGN 记了阈值 460px 的来历（实测卡满点 + 表格数据）",
      "460" in _s and "卡满点" in _s and "668px" in _s and "实测" in _s,
      [k for k in ("460", "卡满点", "668px", "实测") if k not in _s])
check("D2b DESIGN §13.3 把波 B 标成「已降级」（后人不会以为还没做）",
      "⛔ 已降级" in _dz,
      "⛔ 已降级" not in _dz and "§13.3 里波B 不是「已降级」")
_cl = read("CHANGELOG.md")
check("D3 CHANGELOG 有 v4.209.1 条目", "v4.209.1" in _cl)
_cf = read("config.py")
_m = re.search(r'APP_VERSION = "v(\d+)\.(\d+)\.(\d+)"', _cf)
_rd = read("README.md")
_i = _rd.find("当前版本")
_r = re.search(r'v\d+\.\d+\.\d+', _rd[_i:_i + 60]) if _i >= 0 else None
check("D4 config.APP_VERSION 存在", _m is not None)
check("D5 README 版本与 config 一致（跟随式，不写死版本号）",
      _m and _r and _m.group(0).split('"')[1] == _r.group(0),
      f"config={_m.group(0) if _m else '?'} README={_r.group(0) if _r else '?'}")

print(f"\nPASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
