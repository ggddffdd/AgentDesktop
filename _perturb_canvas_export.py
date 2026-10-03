# -*- coding: utf-8 -*-
"""节点画布第 4 步 · 导出判据扰动（证明判据非空转）。

逐个把 canvas_export.py 里的导出契约写法删掉，用 CX_PATH 指向变异副本、
CX_CHECK 只验目标判据，断言该判据由绿变红（exit!=0）。最后用反向基线
（原文件 + 同判据应绿）确认判据本身有效、非巧合。

用法：python _perturb_canvas_export.py
"""

import os
import sys
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "canvas_export.py")
GRAPH = os.path.join(HERE, "canvas_graph.py")
UIHG = os.path.join(HERE, "ui_hex_guard.py")
TEST = os.path.join(HERE, "tests", "test_canvas_export.py")
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检
sys.path.insert(0, HERE)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

# (变异名, 目标判据, 原串, 替换为)
MUTATIONS = [
    ("M1", "A1", "def export_project_json(", "def export_project_json_x("),
    ("M2", "A2", "def import_project_json(", "def import_project_json_x("),
    ("M3", "A3", "def export_svg(", "def export_svg_x("),
    ("M4", "A4", "def export_png(", "def export_png_x("),
    ("M5", "A5", '"graph": g.to_dict()', '"graphx": g.to_dict()'),
    ("M6", "B6", "plan = layout_graph(g)", "plan = layout_graph_X(g)"),
    ("M7", "A7", '"version": 2', '"version": 3'),
    ("M8", "B5", "pos=nd.get(\"pos\"),", "pos=None,"),
    # ---- Wave C（复审 #4 / #5）：导入校验 ----
    ("M9", "A8", "class CanvasImportError(ValueError):",
     "class _NoImportError(ValueError):  # 扰动：去掉专用导入异常"),
    # 锚点必须带上上一行：函数定义行 `def _validate_project(gd, path) -> None:` 里
    # 也含 `_validate_project(gd, path)`，只匹配调用串会先打到定义行上（源码直接语法坏掉）。
    ("M10", "A9",
     '    gd = data.get("graph", data) if isinstance(data, dict) else data\n'
     "    _validate_project(gd, path)",
     '    gd = data.get("graph", data) if isinstance(data, dict) else data\n'
     "    pass  # 扰动：不做结构预检"),
    ("M11", "B11",
     '    where = os.path.basename(path or "工程文件")\n'
     "    if not isinstance(gd, dict):",
     '    where = ""  # 扰动：报错不带文件名\n'
     "    if not isinstance(gd, dict):"),
    ("M12", "B13", "            if not sep or not node or not port:",
     "            if False:  # 扰动：不校验「节点.端口」格式"),
]

# 打在 canvas_graph.py 上的变异（端口 schema），经 CG_PATH 注入给判据：
# 判据必须在 import canvas_export 之前先把变异版塞进 sys.modules["canvas_graph"]，
# 否则 canvas_export 里 `import canvas_graph as cg` 拿到的还是真文件（假绿）。
GRAPH_MUTATIONS = [
    ("G1", "C3", '"multi": bool(self.multi)',
     '"multi": False  # 扰动：序列化时丢掉 multi'),
    ("G2", "C3", "    if isinstance(spec, dict):",
     "    if False:  # 扰动：端口只认裸字符串 spec"),
]

# 打在 ui_hex_guard.py 上的变异（护栏扫描边界），经 UIHG_PATH 注入给判据。
# A12 断言「扫描集合不由注释决定」——回退到按原文（含注释）判定就会把它打红。
UIHG_MUTATIONS = [
    ("U1", "A12", "'THEME' in probe",
     "'THEME' in txt  # 扰动：回退到按原文（含注释）判定扫描范围"),
]


def run_with(check_name, cx_path=None, cg_path=None, uihg_path=None):
    """CX_PATH / CG_PATH / UIHG_PATH 指向变异副本、CX_CHECK 只验目标判据，返回 exit code。"""
    env = dict(os.environ)
    env["CX_CHECK"] = check_name
    if cx_path:
        env["CX_PATH"] = cx_path
    if cg_path:
        env["CG_PATH"] = cg_path
    if uihg_path:
        env["UIHG_PATH"] = uihg_path
    p = subprocess.run([PY, TEST], env=env,
                       capture_output=True, text=True)
    return p.returncode


def _write_tmp(src_text, suffix):
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=suffix,
                                     dir=tempfile.gettempdir(),
                                     delete=False, encoding="utf-8")
    tf.write(src_text)
    tf.close()
    return tf.name


def main():
    with open(SRC, "r", encoding="utf-8") as f:
        base = f.read()
    with open(GRAPH, "r", encoding="utf-8") as f:
        graph_base = f.read()
    with open(UIHG, "r", encoding="utf-8") as f:
        uihg_base = f.read()

    passed = 0
    total = 0
    # ---- canvas_export.py 变异（CX_PATH 注入）----
    for mname, target, old, new in MUTATIONS:
        total += 1
        mut = base.replace(old, new, 1)
        if mut == base:
            print(f"  [SKIP] {mname}: 未命中锚点 {old!r}")
            continue
        tf = _write_tmp(mut, ".py")
        rc = run_with(target, cx_path=tf)
        ok = rc != 0  # 目标判据应翻红（exit!=0）
        print(f"  [{'OK ' if ok else 'FAIL'}] {mname} -> {target}: 变异后 exit={rc} "
              f"(期望 !=0)")
        if ok:
            passed += 1
        os.unlink(tf)

    # ---- canvas_graph.py 变异（CG_PATH 注入，验端口 schema 的导入侧契约）----
    for mname, target, old, new in GRAPH_MUTATIONS:
        total += 1
        mut = graph_base.replace(old, new, 1)
        if mut == graph_base:
            print(f"  [SKIP] {mname}: 未命中锚点 {old!r}")
            continue
        # 变异副本必须落在仓库根目录：判据 import 时按模块名解析，
        # 同目录才能让 canvas_export 里的 `import canvas_graph` 命中变异版。
        tf = os.path.join(HERE, "canvas_mut_graph_%s.py" % mname)
        with open(tf, "w", encoding="utf-8") as f:
            f.write(mut)
        try:
            rc = run_with(target, cg_path=tf)
        finally:
            try:
                os.remove(tf)
            except OSError:
                pass
        ok = rc != 0
        print(f"  [{'OK ' if ok else 'FAIL'}] {mname} -> {target}: 变异后 exit={rc} "
              f"(期望 !=0)")
        if ok:
            passed += 1

    # ---- ui_hex_guard.py 变异（UIHG_PATH 注入，验「扫描边界不由注释决定」）----
    for mname, target, old, new in UIHG_MUTATIONS:
        total += 1
        mut = uihg_base.replace(old, new, 1)
        if mut == uihg_base:
            print(f"  [SKIP] {mname}: 未命中锚点 {old!r}")
            continue
        tf = _write_tmp(mut, ".py")
        rc = run_with(target, uihg_path=tf)
        ok = rc != 0
        print(f"  [{'OK ' if ok else 'FAIL'}] {mname} -> {target}: 变异后 exit={rc} "
              f"(期望 !=0)")
        if ok:
            passed += 1
        os.unlink(tf)

    # ---- 反向基线：原文件 + 同判据应绿（exit=0）----
    base_file = _write_tmp(base, ".py")
    uihg_file = _write_tmp(uihg_base, ".py")
    graph_file = os.path.join(HERE, "canvas_mut_graph_base.py")
    with open(graph_file, "w", encoding="utf-8") as f:
        f.write(graph_base)
    try:
        rc_a1 = run_with("A1", cx_path=base_file)
        rc_b2 = run_with("B2", cx_path=base_file)
        rc_c3 = run_with("C3", cx_path=base_file, cg_path=graph_file)
        rc_a12 = run_with("A12", uihg_path=uihg_file)
    finally:
        os.unlink(base_file)
        os.unlink(uihg_file)
        try:
            os.remove(graph_file)
        except OSError:
            pass
    base_ok = (rc_a1 == 0 and rc_b2 == 0 and rc_c3 == 0 and rc_a12 == 0)
    print(f"  [{'OK ' if base_ok else 'FAIL'}] 反向基线: 原文件 A1 exit={rc_a1} "
          f"B2 exit={rc_b2} C3(oracle) exit={rc_c3} A12 exit={rc_a12} (期望均=0)")

    print("\n" + "=" * 56)
    print(f"  导出扰动：{passed}/{total} 命中翻红；反向基线 {'OK' if base_ok else 'FAIL'}")
    print("=" * 56)
    # 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总。
    print("PERTURB PASS=%d FAIL=%d"
          % (passed, total - passed + (0 if base_ok else 1)))
    sys.exit(0 if (passed == total and base_ok) else 1)


if __name__ == "__main__":
    main()
