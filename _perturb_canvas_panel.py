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
    # ---- Wave D #7：占位语义透传链（canvas_graph → layout_graph → 画布）----
    # PI：删 NodeSpec.placeholder 字段 → A10 静态红
    ("PI_no_ph_field", "A10", "    placeholder: bool = False\n", "", True),
    # PJ：删 NodeSpec.to_dict 里的 placeholder → A10 静态红
    ("PJ_no_ph_todict", "A10", '"placeholder": self.placeholder,', "", True),
    # PK：layout_graph 不再透传占位标记 → A11 静态红
    ("PK_no_ph_pass", "A11",
     'placeholder=bool(getattr(n, "placeholder", False)),', "", True),
    # PL：资产小圆不再分占位（占位也画实心）→ A12 静态红
    ("PL_ph_dot", "A12", "        if spec.placeholder:",
     "        if False:  # 扰动：占位也画实心点", True),
    # PM：状态文字不再标占位 → A13 静态红
    ("PM_ph_text", "A13",
     'spec.status + (" · 占位" if spec.placeholder else "")', "spec.status", True),
    # PN：详情面板不再单列占位条数 → A14 静态红
    ("PN_ph_detail", "A14", '"占位产出: %d 个（未接真生成，仅流程占位）" % n_ph',
     '"占位产出: %s" % n_ph', True),
    # ---- 2026-10-03 画布页打不开事故：D 真实渲染组的自证 ----
    # PO：调用方把 panel 版字段写回模型层的 from_node/to_node（**事故原形**）。
    #     A/B/C 三组全绿，只有真造 CanvasPanel 的 D1 会红 —— 这正是 D 组存在的理由。
    ("PO_edge_field_call", "D1",
     "spec.from_id, spec.from_port, spec.to_id, spec.to_port",
     "spec.from_node, spec.from_port, spec.to_node, spec.to_port", False),
    # PP：定义方把 EdgeSpec 字段改名为 from_node/to_node（改名漏改的另一半）→ D4 哨兵红
    ("PP_edge_field_def", "D4",
     "    from_id: str\n    to_id: str",
     "    from_node: str\n    to_node: str", False),
]

# 打在 **测试脚本自身** 上的变异（不是 canvas_panel.py）：
# A15 断言「切片器切 class 要覆盖整个类体」，只有让 _slice_func 回退到
# 「找到下一个 def/class 就停」的旧写法（2026-10-03 残留④ 的原始 bug）才打得红。
TEST_MUTATIONS = [
    ("PT_slice_class_stops_early", "A15",
     "        if ind <= indent:\n            nxt = nm\n            break",
     "        if True:  # 扰动：回退到「找到下一个 def/class 就停」\n"
     "            nxt = nm\n            break"),
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

    # 备份原文件（被测源码 + 测试脚本自身 —— 后者也要被打变异）
    bak = tempfile.NamedTemporaryFile(delete=False, suffix=".bak")
    bak.close()
    shutil.copy(ORIG, bak.name)
    tb = tempfile.NamedTemporaryFile(delete=False, suffix=".tbak")
    tb.close()
    shutil.copy(TEST, tb.name)

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

        # ---- 打在测试脚本自身的变异：切片器回退（A15 的自证判据）----
        for name, target, old, new in TEST_MUTATIONS:
            content = open(TEST, encoding="utf-8").read()
            if old not in content:
                print("  [SKIP] %s — 锚点缺失: %r" % (name, old[:40]))
                results.append((name, False))
                ok_all = False
                continue
            open(TEST, "w", encoding="utf-8").write(content.replace(old, new, 1))
            rc, out = run_test(True)        # 切片器属静态层，只跑 A 组
            hit = target_failed(target, rc, out)
            print("  [%s] %s 目标%s红 %s" %
                  ("OK " if hit else "BAD", name, target, "✓" if hit else "✗"))
            results.append((name, hit))
            ok_all = ok_all and hit
            shutil.copy(tb.name, TEST)      # 恢复
    finally:
        shutil.copy(bak.name, ORIG)
        shutil.copy(tb.name, TEST)
        for _f in (bak.name, tb.name):
            try:
                os.remove(_f)
            except Exception:
                pass

    _total = len(MUTATIONS) + len(TEST_MUTATIONS)
    print("\n扰动总数: %d  有效: %d  基线: %s" %
          (_total, sum(1 for _, h in results if h), "OK" if base_ok else "FAIL"))
    # 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总。
    _ok_n = sum(1 for _, h in results if h)
    print("PERTURB PASS=%d FAIL=%d"
          % (_ok_n, _total - _ok_n + (0 if base_ok else 1)))
    if ok_all:
        print("ALL PERTURB OK: 判据非空转")
        sys.exit(0)
    print("PERTURB FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
