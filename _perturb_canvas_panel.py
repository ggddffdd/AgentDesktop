# -*- coding: utf-8 -*-
"""节点画布第 3 步 · 判据扰动（canvas_panel.py）。

逐个删/改 canvas_panel.py 里的第 3 步契约写法，证明对应判据会真红（非空转）。

做法：原地变异 ROOT/canvas_panel.py（先备份到临时文件，跑完恢复），
同时让 A 静态切片（读 CANVAS_PATH）与 B 行为 import 都指向被变异的文件，
对每个变异断言目标判据从 OK 变成 FAIL（或 suite 硬崩，也算有效）。
反向基线（不变异）必须 ALL GREEN，证明判据在原始文件上不是天生假绿。

独立运行：python _perturb_canvas_panel.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
ORIG = os.path.join(ROOT, "canvas_panel.py")
TEST = os.path.join(ROOT, "tests", "test_canvas_panel.py")
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

# (name, target判据, old锚点, new替换, static_only)
MUTATIONS = [
    # PA：X 坐标反向展开 → A7 静态红；全跑时 B5(单调)/B6(final最右) 也红
    ("PA_coord_reverse", "A7",
     "x = MARGIN + depth * col_gap",
     "x = MARGIN + (max_depth - depth) * col_gap", False),
    # PB：删分层公式 1+ → A6 静态红
    ("PB_layer_formula", "A6",
     "1 + max(assigned[p] for p in ps if p in assigned)",
     "max(assigned[p] for p in ps if p in assigned)", True),
    # PC：删坑③ setDevicePixelRatio（代码行）→ A2 静态红
    ("PC_dpr", "A2", "pix.setDevicePixelRatio(self._dpr)", "", True),
    # PD：删坑② _NO_WINDOW → A3 静态红
    ("PD_nowindow", "A3", "CREATE_NO_WINDOW", "", True),
    # PE：删坑① _emit → A4 静态红
    ("PE_emit", "A4", "def _emit", "", True),
    # PF：删 registered 资产标记 → A5 静态红
    ("PF_registered", "A5", "def registered", "", True),
    # PG：删六态色 incomplete（canvas_panel.py 里 STATUS_COLOR 的键）→ A8 静态红
    ("PG_status_colors", "A8", "incomplete", "", True),
    # PH：删 from_dict 往返 → A9 静态红
    ("PH_from_dict", "A9", "def from_dict", "", True),
]


def run_test(static):
    env = dict(os.environ)
    env["CANVAS_PATH"] = ORIG
    if static:
        env["CANVAS_STATIC"] = "1"
    p = subprocess.run([PY, TEST], capture_output=True, text=True, env=env, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def target_failed(target, rc, out):
    if ("[FAIL] %s" % target) in out:
        return True
    # 关键代码被删导致 import/语法崩：suite 非 0 退出也算扰动有效
    if rc != 0:
        return True
    return False


def main():
    ok_all = True
    # 反向基线：不变异，全跑必须 ALL GREEN
    rc, out = run_test(static=False)
    base_ok = (rc == 0) and ("ALL GREEN" in out)
    print("反向基线（无变异，全跑）: %s" % ("OK ALL GREEN" if base_ok else "FAIL"))
    ok_all = ok_all and base_ok

    # 备份原文件
    bak = tempfile.NamedTemporaryFile(delete=False, suffix=".bak")
    bak.close()
    shutil.copy(ORIG, bak.name)

    results = []
    try:
        for name, target, old, new, static in MUTATIONS:
            content = open(ORIG, encoding="utf-8").read()
            if old not in content:
                print("  [SKIP] %s — 锚点缺失: %r" % (name, old[:40]))
                results.append((name, False))
                ok_all = False
                continue
            mutated = content.replace(old, new, 1)
            open(ORIG, "w", encoding="utf-8").write(mutated)
            rc, out = run_test(static)
            hit = target_failed(target, rc, out)
            # 额外：PA 全跑时还要验 B5/B6 红
            extra = ""
            if name == "PA_coord_reverse":
                b5 = ("[FAIL] B5" in out) or rc != 0
                b6 = ("[FAIL] B6" in out) or rc != 0
                extra = " (B5=%s B6=%s)" % (b5, b6)
                hit = hit and b5 and b6
            print("  [%s] %s 目标%s红 %s%s" %
                  ("OK " if hit else "BAD", name, target, "✓" if hit else "✗", extra))
            results.append((name, hit))
            ok_all = ok_all and hit
            shutil.copy(bak.name, ORIG)  # 恢复
    finally:
        shutil.copy(bak.name, ORIG)
        try:
            os.remove(bak.name)
        except Exception:
            pass

    print("\n扰动总数: %d  有效: %d  基线: %s" %
          (len(MUTATIONS), sum(1 for _, h in results if h), "OK" if base_ok else "FAIL"))
    if ok_all:
        print("ALL PERTURB OK: 判据非空转")
        sys.exit(0)
    print("PERTURB FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
