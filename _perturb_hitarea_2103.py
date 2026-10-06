# -*- coding: utf-8 -*-
"""触控命中区判据的扰动验证（v4.210.3）

被测判据：tests/test_hitarea_2103.py（H 扫描器可信 / F 硬底线 / P 已修点 / B 盲区记账）

每个用例：把**生产源码或判据自身**改成真实会犯的错 → 跑一次被测套件 →
看预期那条判据是否出现在失败名单里 → 还原。全绿而扰动不红 = 判据是空的。

为什么这批判据特别需要证
------------------------
命中区这类判据的失效方式**不是**「断言写错」，而是「谓词变空」：
  · 高度提取器取成宽度  → 那几处违规根本不会进记录 → F2「不得新增」照样绿
    （本套件首版就踩过：`setFixedSize(56, 26)` 被当 56px，automation 两处 26px 静默消失）
  · 源码按 utf-8 读     → 带 BOM 的文件 ast.parse 失败被整个跳过 → 无记录、无违规
  · 名字启发式失配      → 只剩 AST 口径，子类构造器的控件看不见
所以本文件里 ⑤~⑧ 四个用例**专门打判据自身**，而不是打业务代码。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

UI = os.path.join(ROOT, "ui.py")
WG = os.path.join(ROOT, "ui_widgets.py")   # v4.216.0：chk/pin 随 SessionManagerDialog 搬家
SKILL = os.path.join(ROOT, "skill_market_ui.py")
TOAST = os.path.join(ROOT, "toast.py")
DIRECTOR = os.path.join(ROOT, "director_panel.py")
SUITE = os.path.join(ROOT, "tests", "test_hitarea_2103.py")

# (说明, 目标文件, 原串, 新串, 预期变红的判据关键字)
# ⚠️ 新串里的 `# 扰动` 不是装饰：护栏靠它识别「被强杀后留在磁盘上的变异体」。
CASES = [
    ("① 已修点被改回 24px（进了 <32px 名单却没登记）",
     UI, "self.api_key_toggle.setFixedSize(32, 32)",
     "self.api_key_toggle.setFixedSize(24, 24)  # 扰动", "F2"),

    ("② 已修点掉破硬底线（16px 方图标，鼠标都难点）",
     # v4.216.0：这颗 chk（SessionManagerDialog 里）随对话框族搬到 ui_widgets.py；
     # ui.py:889 的 speech_chk 仍在原地（它不带 \n 边界前缀，本来就不匹配）。
     # 锚点必须带换行+缩进：ui_widgets.py 里 `delb/pin/chk` 同段落，
     # 不加边界会命中多处而报「锚点不唯一」。
     WG, "\n        chk.setFixedSize(32, 32)",
     "\n        chk.setFixedSize(16, 16)  # 扰动", "F1"),

    ("③ 已登记的 30px 被改到 20px（既漂移又破硬底线）",
     SKILL, "install_btn.setFixedHeight(30)",
     "install_btn.setFixedHeight(20)  # 扰动", "F1"),

    ("④ 「pin」被改回 30px（钉子失效，但仍在 24px 硬底线之上）",
     WG, "pin.setFixedSize(32, 32)",
     "pin.setFixedSize(30, 30)  # 扰动", "P ui_widgets.py :: pin"),

    ("⑤ 高度提取器退化成取第 1 个参数（把宽度当高度 → 违规静默消失）",
     SUITE, "        return ints[1]",
     "        return ints[0]  # 扰动", "H2e"),

    ("⑥ 源码按 utf-8 读（带 BOM 的文件 ast.parse 失败被整个跳过）",
     SUITE, '    return read_bytes(name).decode("utf-8-sig")',
     '    return read_bytes(name).decode("utf-8")  # 扰动', "H1"),

    ("⑦ 名字启发式失配（只剩 AST 口径，子类构造器的控件看不见）",
     SUITE, '    r"(btn|button|icon|chip|tab|link|toggle|close|del|remove|expand|collapse|install)",',
     '    r"(?!)",  # 扰动', "B2"),

    ("⑧ 按钮构造器集合清空（AST 口径失效 → 可点控件识别不出来）",
     SUITE, 'CLICKY_CTORS = {"QPushButton", "QToolButton", "QComboBox", "QCheckBox",',
     'CLICKY_CTORS = {}  # 扰动\n_UNUSED = {"QPushButton", "QToolButton", "QComboBox", "QCheckBox",',
     "H2b"),

    ("⑨ 登记值被改错（清单与实际对不上）",
     SUITE, '("director_chat.py", "self.expand_btn"): 24,',
     '("director_chat.py", "self.expand_btn"): 32,  # 扰动', "F3"),
]

# 护栏：快照全部目标源码 + SIGTERM/SIGINT/atexit 三重还原 + 残留变异预检
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

TARGETS = [UI, WG, SKILL, TOAST, DIRECTOR, SUITE]
# ⚠️ 2026-10-05 事故护栏：CASES 里引用的每个文件都必须在 TARGETS 里 ——
#    还原逻辑是 `open(path,"w")` 先截断再 `f.write(ORIG[path])`，若 path 不在
#    ORIG 会 KeyError，且异常发生在截断之后、写入之前 → 目标文件被清成 0 字节
#    （ui_widgets.py 曾因此被清空，靠拆分器+git HEAD 重建）。
_case_files = {c[1] for c in CASES}
_missing = _case_files - set(TARGETS)
assert not _missing, "CASES 引用了不在 TARGETS 还原表里的文件：%s" % _missing
_guard.arm(TARGETS)

ORIG = {p: open(p, encoding="utf-8", newline="").read() for p in TARGETS}


def run_suite():
    try:
        p = subprocess.run([PY, SUITE], capture_output=True, text=True,
                           timeout=180, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return None
    return p.stdout + p.stderr


results = []
for desc, path, old, new, expect in CASES:
    src = open(path, encoding="utf-8", newline="").read()
    if src.count(old) != 1:
        results.append((desc, False, "锚点不唯一/未命中（%d 次）：%r"
                        % (src.count(old), old[:48])))
        continue
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(old, new, 1))
        out = run_suite()
    finally:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(ORIG[path])
    if out is None:
        results.append((desc, False, "套件超时"))
        continue
    # ⚠️ 先确认套件真跑完了：变异体写成语法错误时输出里一条失败都没有，
    # 只看「失败名单有没有关键词」会把「没跑完」误判成「判据是空的」。
    if "PASS=" not in out:
        results.append((desc, False,
                        "套件未产出统计（变异体语法错误 / 套件自身崩溃）：%s"
                        % (out.strip().splitlines() or ["<无输出>"])[-1][:100]))
        continue
    failed = (re.findall(r"^\s*\[FAIL\] (.+)$", out, re.M)
              + re.findall(r"^\s*✗ (.+)$", out, re.M))
    hit = any(expect in f for f in failed)
    results.append((desc, hit,
                    ("命中：" + "; ".join(f[:70] for f in failed[:2])) if hit
                    else "未命中，失败名单=%s" % [f[:60] for f in failed[:3]]))

print("=== 触控命中区判据扰动验证（test_hitarea_2103） ===")
for desc, ok, extra in results:
    print("  [%s] %s" % ("OK  " if ok else "FAIL", desc))
    if extra:
        print("          %s" % extra)
n = sum(1 for _, ok, _ in results if ok)
print("\n=== 扰动结果：%d/%d 命中 ===" % (n, len(results)))
_still = [p for p in TARGETS
          if open(p, encoding="utf-8", newline="").read() != ORIG[p]]
print("源码已全部恢复" if not _still else "**未恢复，请检查：%s**" % _still)
left = _guard.find_leftovers()
if left:
    print("**发现残留变异标记：%s**" % left[:3])
print("PERTURB PASS=%d FAIL=%d" % (n, len(results) - n))
sys.exit(0 if (n == len(results) and not _still and not left) else 1)
