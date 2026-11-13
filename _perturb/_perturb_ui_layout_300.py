# -*- coding: utf-8 -*-
"""v4.300 布局判据扰动：截图发现的真实回退必须能被测试抓红。"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard  # noqa: E402

UI = os.path.join(ROOT, "ui.py")
SUITE = os.path.join(ROOT, "tests", "test_ui_layout_300.py")
CASES = [
    ("功能导航又默认折没", "self._nav_more_open = True",
     "self._nav_more_open = False  # 扰动：默认折没", "B5"),
    ("顶栏又重复品牌", "        tb_layout = QHBoxLayout(self.title_bar)",
     "        tb_layout = QHBoxLayout(self.title_bar)\n"
     "        logo_name = QLabel(\"小臭玩AI\")  # 扰动：重复品牌\n"
     "        tb_layout.addWidget(logo_name)",
     "B6"),
    ("轻建议又长回大卡片", "card.setFixedHeight(52)",
     "card.setFixedHeight(64)  # 扰动：大卡片", "C6"),
    ("输入区又被挤成三分之一", "quick_center.addWidget(quick_box, 4)",
     "quick_center.addWidget(quick_box, 1)  # 扰动：挤窄输入区", "C6b"),
]


def run_suite():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, SUITE], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def main():
    _perturb_guard.arm([UI])
    original = io.open(UI, encoding="utf-8", newline="").read()
    npass = nfail = 0
    baseline = run_suite()
    if "FAIL=0" not in baseline:
        print("BASELINE RED：套件本身未全绿")
        print(baseline[-1200:])
        print("PERTURB PASS=0 FAIL=%d" % len(CASES))
        return 1
    for label, old, new, want in CASES:
        src = io.open(UI, encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("MISS %s：锚点数量=%d" % (label, src.count(old)))
            nfail += 1
            continue
        try:
            io.open(UI, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
            out = run_suite()
        finally:
            io.open(UI, "w", encoding="utf-8", newline="").write(original)
        red = [line for line in out.splitlines() if "[FAIL]" in line]
        if "PASS=" in out and any(want in line for line in red):
            print("PASS %s → %s" % (label, want))
            npass += 1
        else:
            print("MISS %s：期望 %s，实际 %s" % (label, want, red[:3] or out[-200:]))
            nfail += 1
    restored = io.open(UI, encoding="utf-8", newline="").read() == original
    if not restored:
        print("FAIL 源码未恢复")
        nfail += 1
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
