# -*- coding: utf-8 -*-
"""第 5 步执行器 · 扰动脚本（非空转验证）。

机制：对每个被测源文件做单点变异（覆盖写回）→ 用 CX_CHECK 只跑目标判据 →
断言该判据由绿变红（returncode!=0）→ 还原。末尾跑一次全量判据确认不改动时
全绿（反向基线）。

命中点必须真实命中红名，否则视为扰动失效（ERR 报错）。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
TEST = os.path.join("tests", "test_canvas_executor.py")

EXEC = os.path.join(ROOT, "executors.py")
GRAPH = os.path.join(ROOT, "canvas_graph.py")
IL = os.path.join(ROOT, "image_local_edit.py")

# 备份原始内容，结束时确保还原
_BACKUP = {EXEC: open(EXEC).read(), GRAPH: open(GRAPH).read(), IL: open(IL).read()}


def _env(check):
    e = dict(os.environ)
    e["CX_CHECK"] = check
    return e


def _run(check=None):
    env = _env(check) if check else dict(os.environ)
    return subprocess.run([PY, TEST], cwd=ROOT, env=env,
                          capture_output=True, text=True)


FAILS = []


def _pg(desc, check, filepath, old, new):
    # 先确认基线（未变异）该判据是绿的
    base = _run(check)
    if base.returncode != 0:
        print("ERR 基线已红: %s [%s]" % (desc, check))
        FAILS.append(desc)
        return
    src = open(filepath).read()
    if old not in src:
        print("ERR 锚点未命中: %s" % desc)
        FAILS.append(desc)
        return
    open(filepath, "w").write(src.replace(old, new, 1))
    mut = _run(check)
    if mut.returncode == 0:
        print("ERR 未变红: %s -> %s" % (desc, check))
        FAILS.append(desc)
    else:
        print("PG OK: %s -> %s 变红" % (desc, check))
    # 还原
    open(filepath, "w").write(src)


def main():
    # PG1 gen_image_executor 不应用局部编辑 → B2 红
    _pg("gen_image_executor 跳过 apply_local_edits", "B2", EXEC,
        "out_arr = il.apply_local_edits(arr, edits, inpaint_fn=inpaint_fn)",
        "out_arr = arr  # 扰动：不应用局部编辑")

    # PG2 源图不落盘 → B1 红
    _pg("源图不写盘", "B1", EXEC,
        "    base.save(path)",
        "    pass  # 扰动：不落盘源图")

    # PG3 use_real_executors 不替换 executor → B1 红
    _pg("use_real_executors 不替换 executor", "B1", EXEC,
        "        node.executor = build_executor(node, asset_root, inpaint_fn, video_fn, graph, motion_fn)",
        "        node.executor = node.executor  # 扰动：不替换")

    # PG4 有 edits 也不写编辑图 → B2 红
    _pg("edits 分支被跳过", "B2", EXEC,
        "        if edits:",
        "        if False:  # 扰动：即使有编辑也不写编辑图")

    # PG5 inpaint 未注入时不诚实抛错 → B5 红
    _pg("inpaint 缺失时不抛 UnsupportedEditMode", "B5", IL,
        "                raise UnsupportedEditMode(\n"
        "                    \"inpaint 需外部模型，未提供 inpaint_fn（阶段 C 未接真执行器）\")",
        "                pass  # 扰动：不诚实抛错")

    # PG6 build_executor 强制回落 passthrough → B1 红
    _pg("build_executor 强制回落 passthrough", "B1", EXEC,
        "    factory = DEFAULT_EXECUTORS.get(node.node_type)",
        "    factory = None  # 扰动：强制回落 passthrough")

    # 反向基线：所有文件已还原，全量判据应全绿
    final = _run()
    if final.returncode != 0:
        print("ERR 反向基线非绿（可能未完全还原）")
        FAILS.append("反向基线")
    else:
        print("反向基线 ALL GREEN")


if __name__ == "__main__":
    try:
        main()
    finally:
        # 无论如何还原三份原始文件
        for fp, content in _BACKUP.items():
            open(fp, "w").write(content)
    if FAILS:
        print("\n扰动失败项: %s" % ", ".join(FAILS))
        sys.exit(1)
    print("\n全部扰动命中 + 反向基线绿")
    sys.exit(0)
