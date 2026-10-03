# -*- coding: utf-8 -*-
"""节点画布阶段 B · 扰动脚本：证明图片局部编辑判据非空转。

方法：对 canvas_graph.py / canvas_panel.py / canvas_export.py 做**原地单点变异**
（删/改一处核心契约写法），运行 tests/test_canvas_localedit.py，断言目标判据由绿变红。
每个扰动独立备份→变异→跑→还原。末尾反向基线：不改动文件时判据全绿。
"""

import os
import re
import shutil
import subprocess
import sys

ROOT = r"D:/小臭玩AI/deepseek-desktop"
GRAPH = os.path.join(ROOT, "canvas_graph.py")
PANEL = os.path.join(ROOT, "canvas_panel.py")
EXPORT = os.path.join(ROOT, "canvas_export.py")
TEST = os.path.join(ROOT, "tests", "test_canvas_localedit.py")
PY = r"C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()


def run_test():
    """返回 (fail_tokens, all_green)。"""
    out = subprocess.run([PY, TEST], capture_output=True, text=True).stdout
    fails = re.findall(r"\[FAIL\] (\S+)", out)
    green = "ALL GREEN" in out
    return fails, green


def perturb(label, target, path, old, new):
    """备份 path → 替换 old→new(一次) → 跑判据 → 还原。断言 target 变红。"""
    bak = path + ".bak"
    shutil.copy(path, bak)
    try:
        src = open(path, encoding="utf-8").read()
        if old not in src:
            print("[ERR ] %s :: 锚点未命中: %r" % (label, old))
            return False
        src2 = src.replace(old, new, 1)
        open(path, "w", encoding="utf-8").write(src2)
        fails, _ = run_test()
        hit = target in fails
        print("[%s] %s :: 目标 %s %s" % (
            "HIT" if hit else "MISS", label, target,
            "变红✓" if hit else "仍绿✗"))
        return hit
    finally:
        shutil.move(bak, path)


def main():
    print("=== 反向基线（不改动文件）===")
    base_fails, base_green = run_test()
    print("[BASE] ALL GREEN=%s  FAIL=%d %s" % (base_green, len(base_fails), base_fails))

    cases = [
        # 数据层 add_local_edit 不追加
        ("PG1 add_local_edit 不写回", "B1",
         GRAPH, "        self.set_local_edits(node_id, le + [rec])",
         "        self.set_local_edits(node_id, le)  # 扰动：不追加"),
        # _make_edit_record 不校验 instruction
        ("PG2 instruction 不校验", "B2",
         GRAPH, '            raise ValueError("局部编辑指令 instruction 不能为空")',
         "            pass  # 扰动：不校验 instruction"),
        # AddLocalEditCommand 不追加
        ("PG3 AddLocalEditCommand.redo 失效", "B4",
         PANEL, "        self.new_list = self.old_list + [rec]",
         "        self.new_list = list(self.old_list)  # 扰动：redo 不追加"),
        # RemoveLocalEditCommand 不删除
        ("PG4 RemoveLocalEditCommand.redo 失效", "B5",
         PANEL, "        self.new_list = [e for i, e in enumerate(self.old_list) if i != idx]",
         "        self.new_list = list(self.old_list)  # 扰动：redo 不删除"),
        # EditLocalEditCommand 不替换
        ("PG5 EditLocalEditCommand.redo 失效", "B6",
         PANEL, "        self.new_list[idx] = rec",
         "        pass  # 扰动：redo 不替换"),
        # norm_rect rect 公式错误
        ("PG6 norm_rect rect 公式错", "B7b",
         PANEL, '        return (region["x"] * iw, region["y"] * ih,',
         "        return (region['x'] * 0.0, region['y'] * 0.0,  # 扰动：公式错"),
        # export_svg 不标记局部编辑节点（禁用整个 if le: 标记块）
        ("PG7 export_svg 不标记局部编辑", "B12",
         EXPORT,
         '        if le:\n            parts.append(region_svg_overlay(le[0].get("region"), n.x, n.y, n.w, n.h))',
         '        if False:  # 扰动：不标记局部编辑节点'),
    ]

    hits = 0
    for label, target, path, old, new in cases:
        if perturb(label, target, path, old, new):
            hits += 1

    print("\n=== 扰动汇总 ===")
    print("命中 %d / %d" % (hits, len(cases)))
    _, final_green = run_test()
    print("还原后 ALL GREEN=%s" % final_green)
    sys.exit(0 if (hits == len(cases) and base_green and final_green) else 1)


if __name__ == "__main__":
    main()
