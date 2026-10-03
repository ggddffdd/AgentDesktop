# -*- coding: utf-8 -*-
"""节点画布阶段 A · 扰动脚本：证明编辑层判据非空转。

方法：对 canvas_graph.py / canvas_panel.py 做**原地单点变异**（删/改一处核心契约写法），
运行 test_canvas_edit.py，断言目标判据由绿变红。每个扰动独立备份→变异→跑→还原。
末尾反向基线：不改动文件时判据全绿（证明判据本身可绿，红是变异所致）。
"""

import os
import re
import shutil
import subprocess
import sys

ROOT = r"D:/小臭玩AI/deepseek-desktop"
PANEL = os.path.join(ROOT, "canvas_panel.py")
GRAPH = os.path.join(ROOT, "canvas_graph.py")
TEST = os.path.join(ROOT, "tests", "test_canvas_edit.py")
PY = r"C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()


def run_test():
    """返回 (fail_names, all_green)。"""
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
        # canvas_graph 编辑方法
        ("PG1 set_node_pos 不写回", "B1",
         GRAPH, "        n.pos = (float(x), float(y))",
         "        n.pos = old  # 扰动：不写回新值"),
        ("PG2 set_node_config 不写回", "B3",
         GRAPH, "        n.config = dict(config)",
         "        n.config = old  # 扰动：不写回新配置"),
        ("PG3 remove_data_edge 禁用", "B4",
         GRAPH, "        target = None",
         "        return None  # 扰动：禁用删边"),
        # canvas_panel 命令类 / 交互
        ("PG4 MoveNodeCommand.redo 失效", "B5",
         PANEL, "        self._apply(self.new_pos)",
         "        pass  # 扰动：redo 不应用"),
        ("PG5 ConnectCommand.redo 失效", "B6",
         PANEL, "        self.graph.connect_data(*self.f)",
         "        pass  # 扰动：redo 不连线"),
        ("PG6 ConnectCommand.undo 失效", "B6b",
         PANEL, "        self.graph.remove_data_edge(*self.f)",
         "        pass  # 扰动：undo 不删边"),
        ("PG7 EditConfigCommand.redo 失效", "B7",
         PANEL, "        self.graph.set_node_config(self.node_id, self.new_cfg)",
         "        pass  # 扰动：redo 不写配置"),
        ("PG8 节点拖动不写回 on_node_moved", "A6",
         PANEL, "            scene.on_node_moved(self, self._drag_start, cur)",
         "            pass  # 扰动：拖动不通知场景"),
    ]

    hits = 0
    for label, target, path, old, new in cases:
        if perturb(label, target, path, old, new):
            hits += 1

    print("\n=== 扰动汇总 ===")
    print("命中 %d / %d" % (hits, len(cases)))
    # 还原后再次确认全绿
    _, final_green = run_test()
    print("还原后 ALL GREEN=%s" % final_green)
    sys.exit(0 if (hits == len(cases) and base_green and final_green) else 1)


if __name__ == "__main__":
    main()
