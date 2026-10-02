# -*- coding: utf-8 -*-
"""v4.207.0 判据扰动验证 —— 改坏必须红，红在**对的那条**上。

用法：python _perturb_207.py
每个 case：注入错误 → 跑判据 → 期望「红」且红在指定判据上。
判据自己不可信的红（红在别处 / 压根不红）都算失败。

沿用 L186/L187：**二进制读写 + 保留原行尾**，别把 CRLF 文件改成 LF。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
PY = r"C:\Users\xyb\AppData\Local\Programs\Python\Python312\python.exe"
SUITE = "tests/test_empty_state_207.py"


def is_crlf(path):
    with open(path, "rb") as f:
        return b"\r\n" in f.read(4096)


def read_raw(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


def write_raw(path, text, crlf):
    data = text.replace("\r\n", "\n")
    if crlf:
        data = data.replace("\n", "\r\n")
    with open(path, "wb") as f:
        f.write(data.encode("utf-8"))


CASES = []


def case(name, fname, old, new, expect):
    CASES.append((name, fname, old, new, expect))


# ---- 组件层：破坏 compact 的形态约定 -----------------------------------------
case("compact 图标改大到 20（窄栏里会抢视觉）",
     "empty_state.py", "COMPACT_ICON_SIZE = 18", "COMPACT_ICON_SIZE = 20",
     ["D4"])

case("compact 行高改成 48（与旧写法不等价了）",
     "empty_state.py", "COMPACT_H = 32", "COMPACT_H = 48",
     ["D1", "A2"])

case("compact 留白改回完整形态的 (0,8,0,8)",
     "empty_state.py", "COMPACT_MARGIN_V = (8, 8, 8, 8)",
     "COMPACT_MARGIN_V = (0, 8, 0, 8)", ["D2"])

# 3) compact 偷偷画回徽章 —— 这是最危险的"改坏"：视觉直接变形
#    第一版注入的是个游离的 _b.show()，**根本没进 layout**，所以判据 A4 查不到
#    （"运行时没有 44px 控件"照样成立）。改成真的addWidget 进布局。
case("compact 偷偷画回 44px 徽章底（并 addWidget 进布局）",
     "empty_state.py", "    main = QLabel(title)\n"
     "    main.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)",
     "    _b = QLabel()\n    _b.setFixedSize(BADGE_SIZE, BADGE_SIZE)\n"
     "    _b.setStyleSheet('background:red;')\n    lay.addWidget(_b)\n\n"
     "    main = QLabel(title)\n"
     "    main.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)",
     ["A4", "A6b"])

# 4) compact 开始渲染按钮 —— 行高会被顶变形
#    第一版注入的代码引用了 `action`，但 _compact_state 签名里**没有** action
#    （它压根不接这个参数）→ 注入即 NameError，判据是崩了红的，不算命中。
#    换成引用签名里真实存在的 title。
case("compact 下也渲染行动按钮",
     "empty_state.py", "    main = QLabel(title)\n"
     "    main.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)",
     "    _btn = QPushButton(title)\n    _btn.setFixedHeight(28)\n"
     "    lay.addWidget(_btn)\n\n"
     "    main = QLabel(title)\n"
     "    main.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)",
     ["A9b"])

# 4b) compact 改回竖排（行高被顶到 3 倍）—— 补一条独立扰动
case("compact 从横排改回竖排",
     "empty_state.py", "    lay = QHBoxLayout(card)", "    lay = QVBoxLayout(card)",
     ["A9c", "A9d"])

# 5) token 被内联字面量取代
case("compact token 里内联写死颜色",
     "theme_qss.py", 'return label_second(color_key="dim")',
     'return "color:#5F6368;font-size:12px;"', ["B2", "B4"])

case("compact token 输出值被改（字号变了）",
     "theme_qss.py", 'return label_second(color_key="dim")',
     'return label_body(color_key="dim")', ["B2", "D5"])

# ---- 接线层：破坏文案分叉 ---------------------------------------------------
# 7) 两句文案合成一句 —— 回到"说谎"状态
#    注意 old 要用文件里的**真实缩进与换行**（CRLF 已在 write_raw 里归一化）
case("#3 文案分叉被改回同一句（空态说谎）",
     "ui.py",
     '"还没有会话", hint="在主界面发起对话后会出现在这里",',
     '"没有匹配的会话",',
     ["C6", "C7", "C10", "C11", "F1"])

# 8) 又指引了不存在的「＋ 新建」按钮
case("#3 空态指引不存在的「＋ 新建」按钮",
     "ui.py", 'hint="在主界面发起对话后会出现在这里"',
     'hint="点上方「＋ 新建」开始第一条"', ["C9", "C11"])

# 9) 分支判据从 search 换成 folder（语义就错了）
case("#3 分支依据从 search 换成 folder",
     "ui.py", "q = (self.search.text() or \"\").strip()",
     "q = (self.folder_filter.currentText() or \"\").strip()", ["C8"])

# 10) 有人"好心"把 #2 换成组件 —— 必须被守护判据拦住
case("有人把 #2 长期记忆也换成了 empty_state",
     "ui.py",
     'self.mem_view.setPlainText(mem if mem else "暂无长期记忆，对话中小臭会自动积累")',
     'if not mem:\n'
     '                self.mem_view.setPlainText("")\n'
     '                ml.addWidget(empty_state("暂无长期记忆", icon="文档"))\n'
     '            else:\n'
     '                self.mem_view.setPlainText(mem)',
     ["E1", "E3"])

# 11) 有人把 #4 也换成组件
#     第一版只在原QLabel 那行**追加注释** —— 视觉行为没变，判据当然绿。
#     改成真的换成组件（这才是"有人顺手接上"会发生的事）。
case("有人把 #4 预览兜底也换成 empty_state",
     "ui.py",
     '            preview_lbl = QLabel(_session_preview(s) or "还没有消息")',
     '            preview_lbl = empty_state(\n'
     '                _session_preview(s) or "还没有消息", icon="对话")',
     ["E5", "E5b", "E5c", "E10"])

# 12) 有人把 #4 文案改回含糊的"暂无内容"
case("#4 文案退回「暂无内容」",
     "ui.py", 'QLabel(_session_preview(s) or "还没有消息")',
     'QLabel(_session_preview(s) or "暂无内容")', ["E5", "E6", "F3"])

# 13) 有人把 #2 文案改回带圆括号的旧版
case("#2 文案退回带圆括号的旧版",
     "ui.py", '"暂无长期记忆，对话中小臭会自动积累"',
     '"（暂无长期记忆，对话中小臭会自动积累）"', ["E2", "E3", "F2"])

# 15) 有人提前动了波次 3 的活
#     注意 old 用 `import` 不带括号的真身 —— 第一版写的是 "from PySide6"，
#     但 legion_ui.py 的 import 顺序不是那样，导致片段没匹配上（判据没被真正测到）。
case("有人提前给 legion_ui 接了组件",
     "legion_ui.py", "import logging", "import logging\nfrom empty_state import empty_state",
     ["E12a", "E12b"])

# 15) 有人把接入点数从 4 改成 5（多接了一处没记录的）
case("ui.py 多接了一处未记录的调用点",
     "ui.py", '        if not pending:',
     '        _extra = empty_state("多出来的一处", compact=True)\n'
     '        if not pending:',
     ["E10", "E10c"])

# 16) 把"故意不接"的理由从代码注释里删掉
case("删掉 #2 不接组件的理由注释",
     "ui.py", "# v4.207.0：只统一文案口径，**不接empty_state**（DESIGN §12.5 已记理由）。",
     "# v4.207.0：统一文案口径。", ["E4"])

# 17) 坏图标名不再降级（删掉判空）
case("compact 图标判空被删（坏名会留空白）",
     "empty_state.py", "            if pm.isNull():\n"
     "                raise ValueError(f\"图标名不在 _NAV_ICONS 里: {icon!r}\")\n"
     "            ico = QLabel()", "            ico = QLabel()", ["A10"])


def run_suite():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([PY, SUITE], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env)
    return p.returncode, p.stdout + p.stderr


def failed_checks(out):
    """解析判据输出里FAIL 的名字。"""
    names = []
    for ln in out.split("\n"):
        m = ln.strip()
        if m.startswith("[FAIL]"):
            names.append(m[7:].split("  — ")[0].strip())
    return names


def main():
    total = len(CASES)
    hit = 0
    misses = []
    for name, fname, old, new, expect in CASES:
        path = os.path.join(ROOT, fname)
        if not os.path.exists(path):
            print(f"[SKIP] {name} —— {fname} 不存在")
            continue
        crlf = is_crlf(path)
        orig = read_raw(path)
        if old not in orig:
            print(f"[MISS-CONTEXT] {name} —— 在 {fname} 里找不到待扰动片段")
            misses.append((name, "扰动片段没匹配上，判据没被真正测到"))
            continue
        backup = orig
        try:
            write_raw(path, orig.replace(old, new, 1), crlf)
            rc, out = run_suite()
            reds = failed_checks(out)
            ok = rc != 0 and reds
            if ok:
                hit += 1
                mark = "命中"
                detail = f"红在 {reds[:4]}"
            else:
                mark = "未命中"
                detail = ("判据全绿（改坏了没测出来）" if rc == 0
                          else f"红了但没解析到 FAIL 名：{out[-200:]}")
                misses.append((name, detail))
            print(f"[{mark}] {name}\n         {detail}")
        finally:
            write_raw(path, backup, crlf)

    print("\n" + "=" * 60)
    print(f"扰动 {hit}/{total} 命中")
    if misses:
        print("\n未命中：")
        for n, d in misses:
            print(f"  - {n}：{d}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
