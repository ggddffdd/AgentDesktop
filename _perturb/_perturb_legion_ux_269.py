# -*- coding: utf-8 -*-
"""v4.269.0 扰动：军团少弹窗 / 阵容摘要 / 进度一句。

改坏进度句、缺角摘要、技术日志折叠、批准按钮、短需求内联提示，
tests/test_legion_ux_269.py 必须翻红。零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _perturb_guard as G  # noqa: E402

PY = sys.executable
FB = os.path.join(ROOT, "legion_feedback.py")
CHAT = os.path.join(ROOT, "legion_chat.py")
UI = os.path.join(ROOT, "legion_ui.py")
JUDGE = os.path.join(ROOT, "tests", "test_legion_ux_269.py")

CASES = [
    ("V1 失败不再点名", FB,
     '    errors = [m for m in members if m.get("status") == "error"]\n',
     '    errors = [m for m in members if m.get("status") == "err_off"]  # 扰动\n',
     False),
    ("V2 缺角摘要被抹掉", FB,
     '            mr.append(str(g.get("name")).strip())\n',
     '            pass  # 扰动：缺角不进摘要\n',
     False),
    ("V3 技术日志不再折叠", FB,
     '    return any(m in t for m in _FOLD_MARKERS)\n',
     '    return False  # 扰动：技术日志不再折叠\n',
     False),
    ("V4 批准按钮不接线", CHAT,
     '        self.btn_team_approve.clicked.connect(self._on_team_approve_clicked)\n',
     '        pass  # 扰动：批准按钮不接线\n',
     False),
    ("V5 短需求改回弹窗", UI,
     '            self.status.setText("至少写 6 个字，说清要干什么。")\n',
     '            QMessageBox.information(self, "说清楚点", "至少写 6 个字。")  # 扰动\n',
     False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    cases = list(CASES)
    files = [FB, CHAT, UI]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    try:
        for name, fp, old, new, _replace_all in cases:
            total += 1
            src = snaps[fp]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            cnt = src.count(old)
            if cnt != 1:
                print("SKIP %s: old 串出现 %d 次" % (name, cnt))
                continue
            mutated = src.replace(old, new, 1)
            with open(fp, "w", encoding="utf-8", newline="\n") as f:
                f.write(mutated)
            rc, out = run_judge()
            failed = (rc != 0) and ("FAIL" in out)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(fp, "w", encoding="utf-8", newline="\n") as f:
                f.write(src)
    finally:
        for fp, src in snaps.items():
            with open(fp, "w", encoding="utf-8", newline="\n") as f:
                f.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
