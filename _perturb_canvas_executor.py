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
# 首跑绿、复跑红（疑似文件系统瞬时占用）—— 计入命中但单列出来，不静默吞。
FLAKY = []
# 检查点计数（每个 _pg case + 末尾反向基线各 +1）—— 供统一输出契约报 PASS 总数。
# 不硬编码 case 数：以后增删 case 忘了改常量就报不出准数（这正是跨版本哑弹的成因之一）。
CHECKS = [0]


def _pg(desc, check, filepath, old, new):
    CHECKS[0] += 1
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
        # 复跑一次再判：Windows 下「刚写回的 .py 被子进程读到旧内容」偶发出现过
        # （2026-10-04 实测 PG13/B14 一次假哑弹，连跑三次均稳定通过）。
        # 两次都绿才算真哑弹；首跑绿复跑红的如实标成 FLAKY 并单列，不静默吞掉。
        again = _run(check)
        if again.returncode != 0:
            print("PG FLAKY: %s -> %s 首跑绿、复跑红（疑似文件系统瞬时占用）"
                  % (desc, check))
            FLAKY.append(desc)
        else:
            print("ERR 未变红: %s -> %s" % (desc, check))
            FAILS.append(desc)
    else:
        print("PG OK: %s -> %s 变红" % (desc, check))
    # 还原
    open(filepath, "w", encoding="utf-8").write(src)


def _pg_multi(desc, check, edits):
    """**多点同时变异**：edits = [(filepath, old, new), ...]。

    为什么需要它（2026-10-04 实测）：同一个缺陷可能有多道**相互独立、各自充分**的
    防线。典型的如「假路径冒充成功」——执行器内的 `validate_output` 和统一入口
    `assess_asset` 任一道在都能拦住，于是**单点变异永远翻不红**。这不是判据失效，
    但如果不做处理，脚本只能报哑弹，真正的风险是「判据其实已经空转、断言写错了」
    会被淹没在哑弹堆里看不出来。

    故：把两道都拆掉再断言。判据此时必须翻红 —— 证明它真的在测「有没有防线」，
    而不是测了一个恒真式。全部锚点先算好再落盘，任一未命中就整体放弃，
    绝不留半截变异。
    """
    CHECKS[0] += 1
    base = _run(check)
    if base.returncode != 0:
        print("ERR 基线已红: %s [%s]" % (desc, check))
        FAILS.append(desc)
        return
    plan = []
    for filepath, old, new in edits:
        src = open(filepath, encoding="utf-8").read()
        if old not in src:
            print("ERR 锚点未命中: %s (%s)" % (desc, os.path.basename(filepath)))
            FAILS.append(desc)
            return
        plan.append((filepath, src, src.replace(old, new, 1)))
    for filepath, _src, text in plan:
        open(filepath, "w", encoding="utf-8").write(text)
    try:
        mut = _run(check)
        if mut.returncode == 0:
            again = _run(check)      # 同 _pg：复跑一次防文件系统瞬时占用
            if again.returncode != 0:
                print("PG FLAKY: %s -> %s 首跑绿、复跑红（疑似文件系统瞬时占用）"
                      % (desc, check))
                FLAKY.append(desc)
            else:
                print("ERR 未变红: %s -> %s（多道防线全拆仍绿 = 判据可能空转）"
                      % (desc, check))
                FAILS.append(desc)
        else:
            print("PG OK: %s -> %s 变红" % (desc, check))
    finally:
        for filepath, src, _text in plan:
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
        "        node.executor = build_executor(node, asset_root, inpaint_fn, video_fn, graph, motion_fn, text2img_fn)",
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

    # PG11 「假路径冒充成功」这个缺陷现有**两道相互独立、各自充分**的防线：
    #   ① 执行器内 validate_output（第一道，尽早报错；自定义执行器没有它）
    #   ② 统一入口 canvas_graph.assess_asset（第二道，登记时兜底）
    # 任一道在都能让 B9 绿 —— 所以单点变异必然哑弹（实测：只删① 绿、只删② 也绿）。
    # 这不是判据失效，但必须证明「两道全拆时 B9 确实翻红」，否则无法排除
    # 「B9 断言已空转」。故用 _pg_multi 同时拆两道。
    _pg_multi("假路径冒充成功（执行器自查 + 统一入口双双关闭）", "B9", [
        (EXEC, "        out_path = validate_output(out_path, \"clip\", asset_root)",
         "        pass  # 扰动：执行器自查关闭\n"),
        (GRAPH,
         "    if not os.path.isfile(path):\n"
         '        return ASSET_INVALID, "文件不存在: %s" % path\n'
         "    try:\n"
         "        if os.path.getsize(path) <= 0:\n"
         '            return ASSET_INVALID, "文件为空: %s" % path\n'
         "    except OSError as e:\n"
         '        return ASSET_INVALID, "文件无法读取: %s (%s)" % (path, e)\n',
         "    pass  # 扰动：统一入口不再判「文件是否存在/非空」\n"),
    ])

    # PG12 同理（越目录产出）：改「执行器自查 + 统一入口目录围栏」两道一起拆。
    _pg_multi("越目录产出冒充成功（执行器自查 + 统一入口双双关闭）", "B10", [
        (EXEC, "        res = validate_output(res, \"video\", asset_root)",
         "        pass  # 扰动：执行器自查关闭\n"),
        (GRAPH,
         "    if asset_root:\n"
         "        try:\n"
         "            root = os.path.abspath(asset_root)\n"
         "            if os.path.commonpath([os.path.abspath(path), root]) != root:\n"
         '                return ASSET_INVALID, "越出资产目录: %s" % path\n'
         "        except ValueError:\n"
         '            return ASSET_INVALID, "越出资产目录: %s" % path\n'
         '    return ASSET_REAL, ""\n',
         '    return ASSET_REAL, ""  # 扰动：统一入口不查越目录\n'),
    ])

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
    CHECKS[0] += 1
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
    # 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总。
    print("PERTURB PASS=%d FAIL=%d" % (CHECKS[0] - len(FAILS), len(FAILS)))
    if FLAKY:
        # 计命中但单独列出：该 case 的「变异写回→子进程读取」时序不稳，值得盯住。
        print("\n扰动首跑不稳定（复跑通过）: %s" % ", ".join(FLAKY))
    if FAILS:
        print("\n扰动失败项: %s" % ", ".join(FAILS))
        sys.exit(1)
    print("\n全部扰动命中 + 反向基线绿")
    sys.exit(0)
