# -*- coding: utf-8 -*-
"""UI 可访问性三件套判据的扰动验证（v4.210.2）

被测判据：tests/test_ui_a11y_2102.py（A 字体栈 / B 动效减弱 / C 键盘焦点 / D 覆盖事实）

每个用例：把**生产源码**改成真实会犯的错 → 跑一次被测套件 → 看预期那条判据
是否出现在失败名单里 → 还原。全绿而扰动不红 = 判据是空的。

为什么这批判据特别需要证：它们守的三条都是「默认就坏、坏了没人报」的性质 ——
字体吃系统默认、动画不顾用户偏好、控件有了自己的 QSS 就不画焦点框。
判据若只是「源码里搜得到某字符串」，会被注释里的同名文本骗过去（v4.210.1
的 C14 就这么假绿过一次），所以本轮关键判据都是**真渲染 + 真行为**。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

TQ = os.path.join(ROOT, "theme_qss.py")
MAIN = os.path.join(ROOT, "main.py")
UI = os.path.join(ROOT, "ui.py")
MOTION = os.path.join(ROOT, "ui_motion.py")
TOAST = os.path.join(ROOT, "toast.py")
SUITE = os.path.join(ROOT, "tests", "test_ui_a11y_2102.py")

# (说明, 目标文件, 原串, 新串, 预期变红的判据关键字)
# ⚠️ 新串里的 `# 扰动` 不是装饰：护栏靠它识别「被强杀后留在磁盘上的变异体」，
#    没有标记的残留是**静默的**（源码被留在变异态而没人知道）。
CASES = [
    ("① 字体栈不再含雅黑（与 DESIGN §3.1 脱钩）",
     TQ, """FONT_STACK = '"Microsoft YaHei", system-ui, sans-serif'""",
     """FONT_STACK = '"Segoe UI", sans-serif'  # 扰动""", "A1"),

    ("② 族名换成真会改渲染的另一种字体（零视觉变化不再成立）",
     TQ, 'FONT_FAMILY = "Microsoft YaHei"',
     'FONT_FAMILY = "SimSun"  # 扰动', "A6"),

    ("③ main.py 不再应用字体栈（收口只剩 token，不落地）",
     MAIN, "    apply_native_font(app)",
     "    pass  # 扰动 apply_native_font(app)", "A3"),

    ("④ 又写回裸字体族字面量（绕开 FONT_FAMILY）",
     UI, "page.setFont(QFont(FONT_FAMILY, 13))",
     'page.setFont(QFont("Microsoft YaHei", 13))  # 扰动', "A4"),

    ("⑤ scale_ms 不再受动效开关影响（关了动画照算时长）",
     MOTION, "    return ms if motion_allowed() else 0",
     "    return ms  # 扰动", "B2"),

    ("⑥ toast 不再过 scale_ms（用户关了动画照样淡入）",
     TOAST, "        ms = motion.scale_ms(ms)",
     "        ms = ms  # 扰动 scale_ms(ms)", "B4"),

    ("⑦ 焦点环返回空串（全项目按钮失去焦点提示）",
     TQ, '    return f"border:2px solid {ring};"',
     '    return ""  # 扰动', "C1"),

    ("⑧ 环色规则反转（accent 实底上画蓝环 = 看不见）",
     TQ, '    ring = THEME["white"] if bg == THEME["accent"] else THEME["accent"]',
     '    ring = THEME["accent"] if bg == THEME["accent"] else THEME["white"]  # 扰动',
     "C2"),

    ("⑨ 复选框焦点写成 Qt 匹配不到的形态（`:focus::indicator`）",
     TQ, "QCheckBox::indicator:focus{{border",
     "QCheckBox:focus::indicator{{border", "C6"),
]

# 护栏：快照全部目标源码 + SIGTERM/SIGINT/atexit 三重还原 + 残留变异预检
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

TARGETS = [TQ, MAIN, UI, MOTION, TOAST]
_guard.arm(TARGETS)

ORIG = {p: open(p, encoding="utf-8", newline="").read() for p in TARGETS}


def run_suite():
    try:
        p = subprocess.run(
            [PY, SUITE], capture_output=True, text=True, timeout=240, cwd=ROOT,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
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
    # ⚠️ 必须先确认套件真跑完了：变异体写成语法错误（或把套件自己搞崩）时，
    # 输出里**一条失败都没有** —— 只看「失败名单里有没有关键词」会把
    # 「套件根本没跑完」误判成「判据是空的」。首轮 ① 就是这么假通过的
    # （变异串少了个引号 → 语法错误 → 零输出 → 看起来像未命中）。
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

print("=== UI 可访问性判据扰动验证（test_ui_a11y_2102） ===")
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
# 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总
print("PERTURB PASS=%d FAIL=%d" % (n, len(results) - n))
sys.exit(0 if (n == len(results) and not _still and not left) else 1)
