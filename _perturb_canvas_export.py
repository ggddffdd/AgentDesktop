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
TEST = os.path.join(HERE, "tests", "test_canvas_export.py")
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"

# (变异名, 目标判据, 原串, 替换为)
MUTATIONS = [
    ("M1", "A1", "def export_project_json(", "def export_project_json_x("),
    ("M2", "A2", "def import_project_json(", "def import_project_json_x("),
    ("M3", "A3", "def export_svg(", "def export_svg_x("),
    ("M4", "A4", "def export_png(", "def export_png_x("),
    ("M5", "A5", '"graph": g.to_dict()', '"graphx": g.to_dict()'),
    ("M6", "B6", "plan = layout_graph(g)", "plan = layout_graph_X(g)"),
    ("M7", "A7", '"version": 1', '"version": 2'),
    ("M8", "B5", "pos=nd.get(\"pos\"),", "pos=None,"),
]


def run_with(original_path, check_name):
    """用 CX_PATH 指向指定 canvas_export 副本、CX_CHECK 只验目标判据，返回 exit code。"""
    env = dict(os.environ)
    env["CX_PATH"] = original_path
    env["CX_CHECK"] = check_name
    p = subprocess.run([PY, TEST], env=env,
                       capture_output=True, text=True)
    return p.returncode


def main():
    with open(SRC, "r", encoding="utf-8") as f:
        base = f.read()

    passed = 0
    total = 0
    for mname, target, old, new in MUTATIONS:
        total += 1
        # 生成变异副本
        mut = base.replace(old, new, 1)
        if mut == base:
            print(f"  [SKIP] {mname}: 未命中锚点 {old!r}")
            continue
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".py",
                                         dir=tempfile.gettempdir(),
                                         delete=False, encoding="utf-8")
        tf.write(mut)
        tf.close()
        rc = run_with(tf.name, target)
        ok = rc != 0  # 目标判据应翻红（exit!=0）
        print(f"  [{'OK ' if ok else 'FAIL'}] {mname} -> {target}: 变异后 exit={rc} "
              f"(期望 !=0)")
        if ok:
            passed += 1
        os.unlink(tf.name)

    # ---- 反向基线：原文件 + 同判据应绿（exit=0）----
    base_file = tempfile.NamedTemporaryFile(mode="w", suffix=".py",
                                            dir=tempfile.gettempdir(),
                                            delete=False, encoding="utf-8")
    base_file.write(base)
    base_file.close()
    rc_a1 = run_with(base_file.name, "A1")
    rc_b2 = run_with(base_file.name, "B2")
    base_ok = (rc_a1 == 0 and rc_b2 == 0)
    print(f"  [{'OK ' if base_ok else 'FAIL'}] 反向基线: 原文件 A1 exit={rc_a1} "
          f"B2 exit={rc_b2} (期望均=0)")
    os.unlink(base_file.name)

    print("\n" + "=" * 56)
    print(f"  导出扰动：{passed}/{total} 命中翻红；反向基线 {'OK' if base_ok else 'FAIL'}")
    print("=" * 56)
    sys.exit(0 if (passed == total and base_ok) else 1)


if __name__ == "__main__":
    main()
