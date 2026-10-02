# -*- coding: utf-8 -*-
"""v4.209.2 BUG 审核修复验收（P1-1 compact 副文案 token / P1-2 run_all 假绿）

来源：2026-10-02 的《UI 优化 BUG 审核》独立复审，两条 P1 都是**实测**抓出来的：

P1-1  `empty_state` 的 compact 形态里，副文案只写了 `background:transparent;` ——
      既无 color 也无 font-size。Qt 于是回退到**系统默认**，实测是 **#000000 纯黑**，
      而同排主文案是 `#5F6368` 灰 → 副文案比主文案还抢眼，视觉层级倒置。
      （审核报告同时说"字号回退成 9pt"，这一条要**修正**：本探针实测
       系统默认 9pt 在 96 DPI 下 == 12px，**与 token 值碰巧相等**，
       所以字号其实没变，真正可见的症状只有"纯黑"。修的价值在别处：
       不再依赖系统默认值 —— 换 DPI / 换默认字体时它才会真的跑偏。）

P1-2  `tests/test_v4102_image_compress.py` 一条断言输出都没有，只有
      `=== IMAGE_COMPRESS_OK ===` 横幅；`run_all.py` 当时只认"有没有 OK 横幅"，
      用 `_n` 去数 PASS:/[OK]/✅/✓，`_n=0` 也照样返回 `PASS=0 status=ok` 假绿。
      后果：这类套件将来把断言全删光，只要留着横幅，回归照样全绿。

判据分三组（独立运行：python tests/test_bugfix_2092.py）：

A 组件渲染实测 —— 真起 Qt 造控件读 QPalette / QFont，不是读源码字符串
B 回归入口行为 —— 直接喂合成输出给 run_all.classify()，问"会被判成什么"
C 文档与版本 —— DESIGN/CHANGELOG/config/README 三方一致

沿用 L181（期望值写死，不拿被测量当基准）/ L195（白名单只能证明"我改的还在"，
证明不了"该改的都改了" → B9 做成**集合登记式**：第四个同类文件出现即红）。
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
from PySide6.QtGui import QPalette  # noqa: E402

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


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def read_t(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


# 保活容器：holder 若是局部变量，函数返回后 Python 会回收它，C++ 侧的 QWidget
# 跟着析构，后面再读子控件就 "Internal C++ object already deleted"。
# （第一版判据就崩在这里：A1~A5 是纯源码判据，不需要控件，所以前 5 条看不出问题。）
_KEEP = []


def _mount(w):
    """把组件塞进真实布局并 show，让样式真正生效（否则 stylesheet 不抛光）。"""
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.addWidget(w)
    holder.show()
    _app.processEvents()
    _KEEP.append(holder)
    _KEEP.append(w)
    return holder


def _text_labels(w):
    """取组件里的文字 QLabel（排除图标 —— 图标带 pixmap）。"""
    out = []
    for lb in w.findChildren(QLabel):
        pm = lb.pixmap()
        if pm is not None and not pm.isNull():
            continue
        out.append(lb)
    return out


print("=== v4.209.2 BUG 审核修复验收 ===")

# ============================================================ A P1-1 渲染实测

print("\n--- A compact 副文案 token（真起 Qt 读 QPalette / QFont）---")

import ui as _ui                      # noqa: E402
import theme_qss as _tq               # noqa: E402
from empty_state import empty_state   # noqa: E402

_A_src = read("empty_state.py")
_A_qss = read("theme_qss.py")

check("A1 theme_qss 定义了 empty_hint_compact",
      "def empty_hint_compact(" in _A_qss)
check("A2 token 返回值同时含 color: 与 font-size:（不是空壳 token）",
      "color:" in _tq.empty_hint_compact()
      and "font-size:" in _tq.empty_hint_compact(),
      _tq.empty_hint_compact())
check("A3 token 颜色取 THEME['faint']（以 THEME 表为基准，不手抄 hex）",
      f"color:{_ui.THEME['faint']};" in _tq.empty_hint_compact().lower()
      or f"color:{_ui.THEME['faint']};" in _tq.empty_hint_compact(),
      _tq.empty_hint_compact())
check("A4 empty_state.py 的 compact 分支调用 empty_hint_compact",
      re.search(r"from theme_qss import .*empty_hint_compact", _A_src) is not None
      and "sub.setStyleSheet(empty_hint_compact())" in _A_src)
check("A5 compact 副文案不再只写裸 background:transparent（BUG 原状）",
      'sub.setStyleSheet("background:transparent;")' not in _A_src)

_w = empty_state("还没有会话",
                 hint="在主界面发起对话后会出现在这里",
                 compact=True, icon="对话")
_mount(_w)
_lbs = _text_labels(_w)
check("A6 compact 有两个文字标签（主 + 副）", len(_lbs) == 2, len(_lbs))
_title, _hint = _lbs[0], _lbs[1]

_c_hint = _hint.palette().color(QPalette.WindowText).name()
_c_title = _title.palette().color(QPalette.WindowText).name()
check("A7 副文案前景色 == faint（BUG 时是 #000000）",
      _c_hint.lower() == _ui.THEME["faint"].lower(),
      f"实测 {_c_hint}，期望 {_ui.THEME['faint']}")
check("A8 副文案不是纯黑（BUG 值的反向判据）",
      _c_hint.lower() != "#000000", _c_hint)


def _lum(hexcolor):
    """近似明度（0~255），用于判层级有没有倒置。"""
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.299 * r + 0.587 * g + 0.114 * b


check("A9 副文案比主文案浅（层级没倒置：主 dim 更重、副 faint 更轻）",
      _lum(_c_hint) > _lum(_c_title),
      f"副 {_c_hint}({_lum(_c_hint):.0f}) vs 主 {_c_title}({_lum(_c_title):.0f})")
check("A10 主文案仍是 dim（这次改动没误伤它）",
      _c_title.lower() == _ui.THEME["dim"].lower(),
      f"实测 {_c_title}，期望 {_ui.THEME['dim']}")

# 字号：系统默认 9pt 在 96 DPI 下**正好 == 12px**，所以字号其实没变，
# 真正修掉的是"依赖系统默认值"这件事。判据要问的是"显式设了吗"，不是"变大了吗"。
_default = QLabel("还没有会话")
_mount(_default)
check("A11 副文案显式设了 font-size（pixelSize==12，来自 token）",
      _hint.font().pixelSize() == 12, _hint.font().pixelSize())
check("A12 未设样式的 QLabel pixelSize 为 -1（反证 A11 的 12 不是巧合）",
      _default.font().pixelSize() == -1, _default.font().pixelSize())

# 两形态一致性：紧凑与完整形态的副文案同色，避免出现"两处都叫副文案却不一样"
_wf = empty_state("还没有对话", hint="点击下方开始你的第一条对话",
                  action="新建对话", icon="对话")
_mount(_wf)
_lbs_f = _text_labels(_wf)
_c_full_hint = _lbs_f[1].palette().color(QPalette.WindowText).name()
check("A13 compact 与完整形态的副文案同色（两形态不分裂）",
      _c_full_hint.lower() == _c_hint.lower(),
      f"full={_c_full_hint} compact={_c_hint}")
check("A14 完整形态主文案仍是 dim body（没被 compact 的改动带跑）",
      _lbs_f[0].palette().color(QPalette.WindowText).name().lower()
      == _ui.THEME["dim"].lower(),
      _lbs_f[0].palette().color(QPalette.WindowText).name())
check("A15 compact 整行高仍是 32（补样式没把行撑高）", _w.height() == 32,
      _w.height())

_design = read("DESIGN.md")
_i = _design.find("**compact 形态规格**")
_sec = _design[_i:_i + 1400] if _i >= 0 else ""
# A16 第一版写的是 `"副文案" in _sec and "faint" in _sec` —— 扰动实测**不红**：
# 把表格那一行抹掉后，"副文案" 和 "faint" 在该小节别处（v4.209.2 的说明段）还在。
# 关键词在场 ≠ 那一行在场（L195 同款毛病）。改成**行级**：必须有一行表格
# 同时含「副文案」和「12px/faint」。
_row_ok = any(
    ln.strip().startswith("|") and "副文案" in ln and "12px/faint" in ln
    for ln in _sec.split("\n")
)
check("A16 DESIGN §12.5 的 compact 规格表确有「副文案」这一行（行级，非关键词）",
      _row_ok,
      [ln.strip()[:50] for ln in _sec.split("\n") if "副文案" in ln])

# ============================================================ B P1-2 行为级

print("\n--- B 回归入口：直接喂合成输出（不是只看源码里有没有那个 if）---")

import run_all as _ra  # noqa: E402

_B_src = read_t("run_all.py")
check("B1 run_all 把判定抽成了可调用的 classify()（否则只能看到、验不到）",
      hasattr(_ra, "classify") and "def classify(" in _B_src)


_HAVE_CL = hasattr(_ra, "classify")


def _classify(out, rc=0):
    """喂合成输出。classify 被改名/删掉时返回哨兵，让判据**红**而不是崩 ——
    崩了那是"没跑起来"，不等于"判据生效"（L191）。"""
    if not _HAVE_CL:
        return ("fake.py", 0, 0, "<MISSING classify()>", 0.0, "")
    return _ra.classify("fake.py", rc, out, "", 0.0)


# B2 是核心：**BUG 原状的输入**（有 OK 横幅、0 条断言）必须判 EMPTY，不能是 ok。
_r = _classify("跑了一堆 print\n=== IMAGE_COMPRESS_OK ===\n")
check("B2 有 OK 横幅但 0 条断言 → EMPTY（BUG 原状不再假绿）",
      _r[3] == "EMPTY" and _r[2] >= 1, f"status={_r[3]} n_fail={_r[2]}")
# B3 防"修过头"：正常横幅套件不能被误杀
_r3 = _classify("=== ALL_TWIN_V2_OK ===\n[OK] a\n[OK] b\n[OK] c\n")
check("B3 有横幅且有 3 条 [OK] → ok / PASS=3（没误杀正常横幅套件）",
      _r3[3] == "ok" and _r3[1] == 3, f"status={_r3[3]} n_pass={_r3[1]}")
_r3b = _classify("✅ 用例一\n✅ 用例二\nALL_TWIN_BG_OK\n")
check("B3b ✅ 型横幅套件同样不被误杀", _r3b[3] == "ok" and _r3b[1] == 2,
      f"status={_r3b[3]} n_pass={_r3b[1]}")
# B4 主路径不受影响
_r4 = _classify("PASS=4 FAIL=0\n")
check("B4 标准 PASS=/FAIL= 套件走原路径（PASS=4 ok）",
      _r4[3] == "ok" and _r4[1] == 4 and _r4[2] == 0,
      f"status={_r4[3]} pass={_r4[1]} fail={_r4[2]}")
_r5 = _classify("PASS=2 FAIL=1\n", rc=1)
check("B5 真失败的套件仍判 FAILED（不是被新逻辑兜成 ok）",
      _r5[3] == "FAILED", _r5[3])
# B6/B7 旧行为保留（回归的是"没改坏"，不是"改了"
_r6 = _classify("啥也没输出\n")
check("B6 空输出仍判 EMPTY（v4.186.0 的旧行为没被带跑）", _r6[3] == "EMPTY", _r6[3])
_r7 = _classify("REGRESS_FAIL: ['a','b']\n")
check("B7 REGRESS_FAIL 非空仍判 FAILED（旧行为没被带跑）",
      _r7[3] == "FAILED", _r7[3])

# 那个被点名的套件本身：改成计数式判据、输出统计、退出码受控
_img = read_t("test_v4102_image_compress.py")
check("B8 image_compress 不再用裸 assert（改成计数式 check）",
      not re.search(r"^\s*assert\s", _img, re.M),
      [ln.strip()[:40] for ln in _img.split("\n")
       if re.match(r"\s*assert\s", ln)])
check("B9 image_compress 末尾输出 PASS=/FAIL= 且退出码受控",
      re.search(r'print\(f?"?PASS=\{.*\} FAIL=\{', _img) is not None
      and "sys.exit(1 if _FAILS else 0)" in _img)
check("B10 该套件判据条数 ≥3（不是只留了个壳）",
      len(re.findall(r"^\s*_check\(", _img, re.M)) >= 3,
      len(re.findall(r"^\s*_check\(", _img, re.M)))

# B11 是**集合登记式**判据（L195：白名单证明不了"该改的都改了"）。
# 横幅型套件里源码不出现 PASS= 的，逐个登记并写明"为什么它是安全的"，
# 数量写死 —— 冒出第 4 个必须有人来看一眼，而不是静默混过去。
# 值是**源码里的字面量**（用来证明"确实会动态产出标记"），不是运行时长相 ——
# twin_bg 的 [OK] 是 `f"[{'OK' if ok else 'FAIL'}]"` 拼出来的，
# 源码里压根没有 "[OK]" 这三个字符（第一版判据按运行时长相找，它自己红了）。
_EXPECT_BANNER_NO_PASS = {
    "test_hotfix15.py": "PASS:",            # print("PASS:", msg)
    "test_v4102_1021_twin_bg.py": "'OK'",   # f"[{'OK' if ok else 'FAIL'}]"
    "test_v4102_twin_v2.py": "✅",          # print(f"✅ {name}")
}
_found = {}
import pathlib  # noqa: E402

for _p in sorted(pathlib.Path(HERE).glob("test_*.py")):
    _s = _p.read_text(encoding="utf-8", errors="replace")
    _banner = (re.search(r"=\s*(?:ALL_)?[A-Z0-9_]{2,}_OK\s*=", _s)
               or re.search(r'print\(\s*f?"[^"]*_OK"', _s))
    if _banner and "PASS=" not in _s:
        _found[_p.name] = _s
check("B11 横幅型但源码无 PASS= 的套件集合 == 登记的 3 个（冒出第 4 个即红）",
      set(_found) == set(_EXPECT_BANNER_NO_PASS),
      f"实测 {sorted(_found)}，登记 {sorted(_EXPECT_BANNER_NO_PASS)}")
for _n, _marker in _EXPECT_BANNER_NO_PASS.items():
    _s = _found.get(_n, "")
    # 光登记名字不够：必须真的能在源码里找到"动态产出标记"的那条 print
    check(f"B12 {_n} 确实会动态产出 {_marker}（登记理由成立）",
          _marker in _s)

# ============================================================ C 文档与版本

print("\n--- C 文档与版本 ---")

_cl = read("CHANGELOG.md")
check("C1 CHANGELOG 有 v4.209.2 条目", "v4.209.2" in _cl)
_cf = read("config.py")
_m = re.search(r'APP_VERSION = "v(\d+)\.(\d+)\.(\d+)"', _cf)
_rd = read("README.md")
_i = _rd.find("当前版本")
_r = re.search(r"v\d+\.\d+\.\d+", _rd[_i:_i + 60]) if _i >= 0 else None
check("C2 config 里能解析出版本号", _m is not None)
check("C3 README 与 config 版本一致（跟随式，不写死版本号）",
      _m and _r and _m.group(0).split('"')[1] == _r.group(0),
      f"config={_m.group(0) if _m else '?'} README={_r.group(0) if _r else '?'}")
# 不写死当前版本号（L196：写死的版本号下次升版必红，逼人去改判据而不是改代码）。
# 这里守的是**防回退**：版本不得低于本波落地的那一版。
check("C4 config 版本不低于 v4.209.2（防回退，不写死当前版本号）",
      _m and tuple(int(x) for x in _m.group(0).split('"')[1][1:].split(".")) >= (4, 209, 2),
      _m.group(0) if _m else "?")
# C5 第一版也是关键词式（"v4.209.2" in _design and "empty_hint_compact" in _design）
# —— 扰动把「### 12.7 v4.209.2」这个小标题抹掉后照样绿，因为这两个词在
# §12.5 的说明段里也出现。改成**切片级**：§12.7 那一节里三条要素必须同时在场。
_i7 = _design.find("### 12.7")
_sec7 = _design[_i7:_i7 + 2200] if _i7 >= 0 else ""
check("C5 DESIGN §12.7 记了本波两条 P1（版本 + token + classify 三要素同时在场）",
      all(k in _sec7 for k in ("v4.209.2", "empty_hint_compact", "classify")),
      [k for k in ("v4.209.2", "empty_hint_compact", "classify") if k not in _sec7])

print(f"\nPASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
