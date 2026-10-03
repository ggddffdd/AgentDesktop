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

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检。
# 必须先于下面的 _BACKUP 读取执行：预检发现上次强杀残留的变异体时立即报错，
# 否则会把「已污染的源码」当成基线，得到「基线已红」这种极具误导性的结论。
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

# 备份原始内容，结束时确保还原
_BACKUP = {EXEC: open(EXEC, encoding="utf-8").read(),
           GRAPH: open(GRAPH, encoding="utf-8").read(),
           IL: open(IL, encoding="utf-8").read()}


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
    src = open(filepath, encoding="utf-8").read()
    if old not in src:
        print("ERR 锚点未命中: %s" % desc)
        FAILS.append(desc)
        return
    open(filepath, "w", encoding="utf-8").write(src.replace(old, new, 1))
    mut = _run(check)
    if mut.returncode == 0:
        print("ERR 未变红: %s -> %s" % (desc, check))
        FAILS.append(desc)
    else:
        print("PG OK: %s -> %s 变红" % (desc, check))
    # 还原
    open(filepath, "w", encoding="utf-8").write(src)


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

    # ---- Wave A #6：执行器产出校验 validate_output ----
    # PG7 撤掉「存在/非空/可读」三重校验 → B8c 红（假路径能被当成功）
    _pg("validate_output 撤掉存在/非空/可读校验", "B8c", EXEC,
        "    if not os.path.isfile(p):\n"
        "        raise UnsupportedEditMode(\"执行器输出文件不存在: %s\" % p)\n"
        "    try:\n"
        "        if os.path.getsize(p) <= 0:\n"
        "            raise UnsupportedEditMode(\"执行器输出文件为空: %s\" % p)\n"
        "    except OSError as e:\n"
        "        raise UnsupportedEditMode(\"执行器输出无法读取: %s (%s)\" % (p, e))",
        "    pass  # 扰动：撤掉存在/非空/可读三重校验")

    # PG8 撤掉空文件校验 → B8d 红
    _pg("validate_output 不查空文件", "B8d", EXEC,
        "        if os.path.getsize(p) <= 0:",
        "        if False:  # 扰动：空文件也算成功")

    # PG9 撤掉扩展名相符校验 → B8e 红
    _pg("validate_output 不查扩展名", "B8e", EXEC,
        "    if exts and not p.lower().endswith(exts):",
        "    if False:  # 扰动：扩展名不校验")

    # PG10 撤掉目录围栏 → B8f 红
    _pg("validate_output 不查越目录", "B8f", EXEC,
        "    if asset_root:",
        "    if False:  # 扰动：不查越目录")

    # PG11 gen_video_executor 不接 validate_output → B9 红（假路径冒充成功）
    _pg("gen_video 不校验产出", "B9", EXEC,
        "        out_path = validate_output(out_path, \"clip\", asset_root)",
        "        pass  # 扰动：不校验产出")

    # PG12 promo_fx 不接 validate_output → B10 红（越目录产出冒充成功）
    _pg("promo_fx 不校验产出", "B10", EXEC,
        "        res = validate_output(res, \"video\", asset_root)",
        "        pass  # 扰动：不校验产出")

    # ---- Wave D #8：上游选择语义 ----
    # PG13 忽略 config.ref_source（显式指定失效）→ B14 红
    _pg("select_upstream 忽略 ref_source（显式指定失效）", "B14", EXEC,
        '    explicit = str(cfg.get("ref_source") or "").strip()',
        '    explicit = ""  # 扰动：忽略 ref_source')

    # PG14 指不到时不再抛错（静默回落）→ A16 红（判据数 raise 次数）
    _pg("ref_source 指不到时不报错", "A16", EXEC,
        '        raise UnsupportedEditMode(\n'
        '            "ref_source=%r 指不到 kind=%s 的上游资产（可用: %s）"',
        '        _unused = (\n'
        '            "ref_source=%r 指不到 kind=%s 的上游资产（可用: %s）"')

    # PG15 直接上游改成「按连线顺序」而非「按输入端口声明顺序」→ B13 红
    _pg("上游顺序改成按连线先后（不再按端口声明顺序）", "B13", EXEC,
        '    for port_name in (node.inputs or {}):\n'
        '        for e in getattr(graph, "data_edges", []) or []:\n'
        '            if e.to_node != node.id or e.to_port != port_name:\n'
        '                continue',
        '    _edges_in_order = [e for e in (getattr(graph, "data_edges", []) or [])\n'
        '                       if e.to_node == node.id]  # 扰动：按连线顺序\n'
        '    for port_name in [e.to_port for e in _edges_in_order]:\n'
        '        for e in getattr(graph, "data_edges", []) or []:\n'
        '            if e.to_node != node.id or e.to_port != port_name:\n'
        '                continue')

    # PG16 gen_video 回退旧行为（默默取递归收集的第一个，不回显来源）→ B15 红
    _pg("gen_video 回退「默默取第一个」", "B15", EXEC,
        '        src_path, src_from = select_upstream(graph, node, "image")',
        '        _old_paths = _upstream_asset_paths(graph, node, "image")'
        '  # 扰动：回退旧行为\n'
        '        src_path, src_from = (_old_paths[0] if _old_paths else None), ""')

    # PG17 passthrough 不标占位（占位物冒充真出片）→ B12 红
    _pg("passthrough 产出不标占位", "B12", EXEC,
        "                placeholder=True,\n", "")

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
            open(fp, "w", encoding="utf-8").write(content)
    if FAILS:
        print("\n扰动失败项: %s" % ", ".join(FAILS))
        sys.exit(1)
    print("\n全部扰动命中 + 反向基线绿")
    sys.exit(0)
