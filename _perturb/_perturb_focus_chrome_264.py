# -*- coding: utf-8 -*-
"""扰动：专注对话（v4.264.0）接线改坏必红。

证明侧栏默认只露出对话、工具细节默认关、产物芯片、人话档往返、
确认可收回、备份走 toast，一旦被改回去会被 tests/test_focus_chrome_264.py 抓红。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import _perturb_guard  # noqa: E402

UI = os.path.join(HERE, "ui.py")
FOCUS = os.path.join(HERE, "ui_focus.py")
HOST = os.path.join(HERE, "ui_focus_host.py")
JUDGE = os.path.join(HERE, "tests", "test_focus_chrome_264.py")

CASES = [
    ("V1 对话组又塞进编排",
     UI,
     '("对话", ("对话",)),',
     '("对话", ("对话", "编排")),  # 扰动：对话组又塞进编排',
     ["F1"]),
    ("V2 工具细节缺省改成开",
     FOCUS,
     "    return bool(cfg.get(SHOW_TOOL_DETAILS_KEY, False))",
     "    return True  # 扰动：缺省改成开",
     ["F17"]),
    ("V3 芯片去掉「打开」",
     FOCUS,
     "'<a href=\"app://openfile/%s\">打开 %s</a>'\n"
     "            '<a href=\"app://reveal/%s\">在文件夹显示</a>' % (enc, safe, enc)",
     "'<a href=\"app://openfile/%s\">查看 %s</a>'\n"
     "            '<a href=\"app://reveal/%s\">在文件夹显示</a>' % (enc, safe, enc)"
     "  # 扰动：去掉打开",
     ["F25"]),
    ("V4 助手做事映不回去",
     FOCUS,
     "        return PERSONA_ASSIST",
     '        return ""  # 扰动：助手做事映不回去',
     ["F11"]),
    ("V5 普通确认不再写明可以收回",
     FOCUS,
     '        note = "允许后才会做。勾选「少问」后，输入区会出现徽章，点徽章可以收回。"',
     '        note = "允许后才会做。"  # 扰动：不再写明可以收回',
     ["F29"]),
    ("V6 立即备份的成功回执改回弹窗",
     UI,
     "            toast(str(msg)[:120], kind=_bk_kind, detail=str(msg))",
     '            QMessageBox.information(self, "立即备份", str(msg)[:120])  # 扰动：成功回执改回弹窗',
     ["F33"]),
    ("V7 ChatWindow 丢掉专注对话 mixin",
     UI,
     "class ChatWindow(FocusChromeMixin, ChatAuditMixin, QMainWindow):",
     "class ChatWindow(ChatAuditMixin, QMainWindow):  # 扰动：丢掉专注对话 mixin",
     ["F15"]),
    ("V8 助手做事又关工具",
     FOCUS,
     '    return True, "interactive"',
     '    return False, "interactive"  # 扰动：助手做事又关工具',
     ["F9"]),
    ("V9 信息卡不刷新 Agent 开",
     HOST,
     '        lbl.setText("开" if on else "关")',
     '        lbl.setText("关")  # 扰动：信息卡不刷新',
     ["F44"]),
    ("V10 旧档助手做事不升工具",
     FOCUS,
     '        cfg["agent_mode"] = True',
     '        cfg["agent_mode"] = False  # 扰动：旧档助手做事不升工具',
     ["F37"]),
    ("V11 启动不再对齐旧档",
     UI,
     "        if ui_focus.reconcile_persona_cfg(self.cfg):\n"
     "            self._save_cfg()\n",
     "        if False:  # 扰动：启动不再对齐旧档\n"
     "            self._save_cfg()\n",
     ["F41"]),
    ("V12 输入区不对齐人话档",
     UI,
     "        self._sync_persona_widgets()\n"
     "        return input_area\n",
     "        return input_area  # 扰动：输入区不对齐人话档\n",
     ["F42"]),
]


def _drop_pyc():
    """P19：同一秒内改回源码，.pyc 的 mtime 可能仍新于源码，变异等于没发生。"""
    cache = os.path.join(HERE, "__pycache__")
    if not os.path.isdir(cache):
        return
    for name in ("ui_focus", "ui", "ui_widgets", "ui_focus_host"):
        for fn in os.listdir(cache):
            if fn.startswith(name + "."):
                try:
                    os.remove(os.path.join(cache, fn))
                except OSError:
                    pass


def run_judge():
    _drop_pyc()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    r = subprocess.run([sys.executable, JUDGE], cwd=HERE,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", env=env)
    out = (r.stdout or "") + (r.stderr or "")
    red = []
    for line in out.splitlines():
        s = line.strip()
        if "[FAIL]" in s:
            red.append(s)
    return red, out


def main():
    _perturb_guard.arm([UI, FOCUS, HOST])
    npass = nfail = 0
    verbose_tail = []

    base_red, _ = run_judge()
    if base_red:
        print("BASELINE_RED 基线本身就是红的，判据不可用：")
        for x in base_red[:8]:
            print("   ", x)
        nfail += 1
    else:
        print("BASELINE GREEN（判据当前全绿）")

    originals = {}
    for path in (UI, FOCUS, HOST):
        originals[path] = io.open(path, encoding="utf-8").read()

    for label, path, old, new, want in CASES:
        src = originals[path]
        if old not in src:
            print("SKIP  %s —— 变异点在源码中找不到" % label)
            nfail += 1
            continue
        io.open(path, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
        try:
            red, out = run_judge()
        finally:
            io.open(path, "w", encoding="utf-8", newline="\n").write(src)

        hit = [w for w in want if any(w in x for x in red)]
        if hit and not any(x.startswith("SKIP") for x in red):
            print("PASS  %s → 命中 %s" % (label, "、".join(hit)))
            npass += 1
        else:
            print("MISS  %s → 期望命中 %s，实际 %s"
                  % (label, "、".join(want), red[:4] or "无红行"))
            nfail += 1
            if not red:
                verbose_tail.append(out[-800:])

    print()
    for t in verbose_tail:
        print("---- 变异体输出尾 ----")
        print(t)
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
