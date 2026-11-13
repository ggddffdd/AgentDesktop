# -*- coding: utf-8 -*-
"""v4.301 工作区骨架判据扰动：真实改坏源码，目标判据必须翻红。"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard  # noqa: E402

SUITE = os.path.join(ROOT, "tests", "test_workspace_shell_301.py")
CASES = [
    ("ui_widgets.py", "共享页边距回退", "lay.setContentsMargins(32, 28, 32, 24)",
     "lay.setContentsMargins(24, 20, 24, 20)  # 扰动：旧边距", "A4"),
    ("ui_widgets.py", "共享卡片内边距回退", "lay.setContentsMargins(20, 18, 20, 18)",
     "lay.setContentsMargins(8, 8, 8, 8)  # 扰动：卡片挤回去", "A8"),
    ("ui.py", "生图页脱离共享骨架",
     "lay, _, _ = build_workspace_shell(\r\n            page, \"生图 · Agnes Image\",",
     "lay, _, _ = build_workspace_shell_REMOVED(  # 扰动：脱离共享骨架\r\n            page, \"生图 · Agnes Image\",", "B-生图"),
    ("automation_panel.py", "空任务又显示两个新建入口",
     "app.auto_new_btn.setVisible(bool(tasks))",
     "app.auto_new_btn.setVisible(True)  # 扰动：重复入口", "C6"),
    ("director_panel.py", "分镜长说明又不换行", "shhint.setWordWrap(True)",
     "shhint.setWordWrap(False)  # 扰动：横向撑宽", "C10"),
    ("director_chat.py", "导演对话条又挤占主操作首屏", "H_COLLAPSED = 80",
     "H_COLLAPSED = 200  # 扰动：旧高度", "C11"),
]


def run_suite():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, SUITE], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def main():
    files = sorted({os.path.join(ROOT, rel) for rel, *_ in CASES})
    _perturb_guard.arm(files)
    originals = {p: io.open(p, encoding="utf-8", newline="").read() for p in files}
    baseline = run_suite()
    if "FAIL=0" not in baseline:
        print("BASELINE RED：套件本身未全绿")
        print(baseline[-1200:])
        print("PERTURB PASS=0 FAIL=%d" % len(CASES))
        return 1
    npass = nfail = 0
    for rel, label, old, new, want in CASES:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            print("MISS %s：锚点数量=%d" % (label, src.count(old)))
            nfail += 1
            continue
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
            out = run_suite()
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(originals[path])
        red = [line for line in out.splitlines() if "[FAIL]" in line]
        if "PASS=" in out and any(want in line for line in red):
            print("PASS %s → %s" % (label, want))
            npass += 1
        else:
            print("MISS %s：期望 %s，实际 %s" % (label, want, red[:3] or out[-200:]))
            nfail += 1
    for path, original in originals.items():
        if io.open(path, encoding="utf-8", newline="").read() != original:
            print("FAIL 源码未恢复：%s" % os.path.basename(path))
            nfail += 1
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
