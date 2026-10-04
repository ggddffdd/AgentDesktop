# -*- coding: utf-8 -*-
"""画布「手感档」判据扰动 —— tests/test_canvas_ops.py 的 12 处防线逐个拆。

守的是 v4.211.9 手感档（滚轮缩放 / 框选平移 / 右键建删节点 / 参数行 schema），
外加落地时连带修掉的两个既有缺陷：

  ① 撤销重绘信号驱动（indexChanged）—— 只靠按钮刷新时，键盘 Delete /
     右键菜单 / 直调 undo() 的路径画面不动；
  ② topo() 原是 task_list() 的 dict 插入序 —— 「删节点→撤销」把任务重排到
     dict 尾部后"拓扑序"失真，layout_graph 崩 max() empty。

口径与 `_perturb_canvas_rerun_refresh.py` 一致（比更早的脚本严两处）：
  ① 必须目标判据本身翻红（rc!=0 一律算命中的旧口径会掩盖哑弹）；
  ② 子进程 timeout=120 —— 变异导致挂起记失败，不拖死整轮回归。

⚠️ canvas_graph.py 是 CRLF：变异走 text-mode 读写（读 \r\n→\n、写 \n→\r\n，
与上轮 PR2/PR4 同模式），还原由护栏按字节快照写回，收尾逐字节比对。

独立运行：python _perturb_canvas_ops.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
TEST = os.path.join(ROOT, "tests", "test_canvas_ops.py")
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
TEST_TIMEOUT = 120

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

SNAP = _guard.arm()

TG = os.path.join(ROOT, "task_graph.py")
CG = os.path.join(ROOT, "canvas_graph.py")
CP = os.path.join(ROOT, "canvas_panel.py")

# (name, 必须全红的判据前缀列表, 被测文件, old 锚点, new 替换)
MUTATIONS = [
    # ---- A 缩放钳制 ----
    # PO1：钳制拆掉 → 超限滚轮照样生效 → A5（上限）/A6（下限）红。
    ("PO1_wheel_no_clamp", ["A5", "A6"], CP,
     "        cur = self.transform().m11()\n"
     "        if not (self.MIN_ZOOM <= cur * factor <= self.MAX_ZOOM):\n"
     "            return\n"
     "        self.scale(factor, factor)",
     "        self.scale(factor, factor)  # 扰动：不钳制"),

    # ---- B 框选/平移 ----
    # PO2：dragMode 退回 ScrollHandDrag（左键平移、无法框选）→ B1 红。
    ("PO2_scrollhand_drag", ["B1"], CP,
     "        # 左键拖空白 = 框选（RubberBand）；平移走中键（_pan_start/_pan_stop 切模式）。\n"
     "        # 旧版是 ScrollHandDrag（左键平移）—— 那样左键就没法框选了。\n"
     "        self.setDragMode(QGraphicsView.RubberBandDrag)",
     "        # 扰动：退回左键平移\n"
     "        self.setDragMode(QGraphicsView.ScrollHandDrag)"),

    # ---- C 右键建节点 ----
    # PO3：新建节点不落位鼠标处（pos=None 走自动布局）→ C3 红。
    ("PO3_add_no_pos", ["C3"], CP,
     "            pos=self.scene_pos)",
     "            pos=None)  # 扰动：不落位"),

    # PO11：右键「新建」整条支路失效 → C1 红。
    ("PO11_menu_add_off", ["C1"], CP,
     "        if kind == \"add\":\n"
     "            nid = self._unique_node_id(payload)\n"
     "            cmd = AddNodeCommand(self.graph, self.undo_stack, payload, nid,\n"
     "                                 (pos.x(), pos.y()))\n"
     "        else:\n"
     "            cmd = RemoveNodeCommand(self.graph, self.undo_stack, payload)",
     "        if kind == \"add\":\n"
     "            return  # 扰动：建节点无效\n"
     "        else:\n"
     "            cmd = RemoveNodeCommand(self.graph, self.undo_stack, payload)"),

    # PO5：撤销栈变化不再信号驱动重绘 → 直调 undo 的路径画面不动 → C8 红。
    ("PO5_no_indexchanged", ["C8"], CP,
     "        self.undo_stack.indexChanged.connect(self._refresh_view)",
     "        pass  # 扰动：撤销不重绘"),

    # ---- D 删节点 ----
    # PO4：remove_node 不删数据边（悬空边）→ D2 红。
    #      悬空边被 topo/layout 的存在性过滤兜住，不会死循环（终止性安全）。
    ("PO4_del_no_edges", ["D2"], CG,
     "        for f, fp, t, tp, _lbl in data_snap:\n"
     "            self.remove_data_edge(f, fp, t, tp)",
     "        pass  # 扰动：数据边不删"),

    # PO10：remove_task 不清残余依赖 → 直连 blocked_by/blocks 留悬空 id → D4 红。
    #      锚点必须覆盖**完整** for 块：只换前半会把 blocks 分支的缩进悬空
    #      → 语法错 → 判据以"逐组 FAIL"方式红，目标判据名反而出现不了。
    ("PO10_remove_task_no_clear", ["D4"], TG,
     "            for other in self._tasks.values():\n"
     "                if task_id in other.blocked_by:\n"
     "                    other.blocked_by.remove(task_id)\n"
     "                if task_id in other.blocks:\n"
     "                    other.blocks.remove(task_id)",
     "            for other in self._tasks.values():  # 扰动：什么都不清\n"
     "                pass"),

    # ---- E Delete 键 ----
    # PO6：Delete 键不收集选中节点 → E1 红。
    ("PO6_delkey_off", ["E1"], CP,
     "            ids = [it.spec.id for it in self.selectedItems()\n"
     "                   if isinstance(it, CanvasNodeItem)]",
     "            ids = []  # 扰动：Delete 不删"),

    # ---- F 参数行 schema ----
    # PO7：gen_video 的 schema 清空 → 不生成参数行 → F1 红。
    ("PO7_schema_empty", ["F1"], CP,
     "        \"gen_video\": [(\"参考源(上游.端口)\", \"ref_source\", \"str\"),\n"
     "                      (\"参考端口\", \"ref_port\", \"str\")],",
     "        \"gen_video\": [],  # 扰动：不生成参数行"),

    # PO8：apply 不收集参数行 → 填了也不写回 config → F2 红。
    ("PO8_apply_no_params", ["F2"], CP,
     "        for key, edit in self.param_rows.items():\n"
     "            txt = edit.text().strip()\n"
     "            if txt:\n"
     "                new_cfg[key] = txt\n"
     "            else:\n"
     "                new_cfg.pop(key, None)",
     "        pass  # 扰动：参数行不写回"),

    # ---- G topo 真拓扑序 ----
    # PO9：topo 退回 dict 插入序（原始 bug）→ 「删→撤」后 img 排尾 → G1 红，
    #      layout 前提崩 → G2 红（_run_part 兜异常转 FAIL）。
    ("PO9_topo_revert", ["G1", "G2"], CG,
     "        deps = {}\n"
     "        for f, t in self._edge_pairs():\n"
     "            if f in self.nodes and t in self.nodes:\n"
     "                deps.setdefault(t, set()).add(f)",
     "        return list(self.nodes.keys())  # 扰动：dict 序伪拓扑\n"
     "        deps = {}\n"
     "        for f, t in self._edge_pairs():\n"
     "            if f in self.nodes and t in self.nodes:\n"
     "                deps.setdefault(t, set()).add(f)"),

    # ---- H 假控件防线 ----
    # PO12：给 passthrough 类节点塞引擎不读的假键 → H4 红（假控件判据）。
    ("PO12_stray_schema_key", ["H4"], CP,
     "        \"source_prompt\": [],",
     "        \"source_prompt\": [(\"不存在的参数\", \"fake_key_xyz\", \"str\")],  # 扰动：假控件"),
]


def run_test():
    env = dict(os.environ)
    env["TG_PATH"] = TG
    env["CG_PATH"] = CG
    env["CP_PATH"] = CP
    env["QT_QPA_PLATFORM"] = "offscreen"
    try:
        p = subprocess.run([PY, TEST], capture_output=True, text=True, env=env,
                           cwd=ROOT, timeout=TEST_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes)
               else (e.stdout or ""))
        return 124, out + "\n[扰动] 子进程超时 %ds —— 变异导致挂起而非判据翻红" % TEST_TIMEOUT
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def target_failed(targets, rc, out):
    """目标判据**每一条**都得真翻红（收紧口径见模块 docstring）。"""
    if rc == 124:
        return False, "子进程超时（挂起，不算翻红）"
    missing = [t for t in targets if ("[FAIL] %s" % t) not in out]
    if not missing:
        return True, ""
    if rc != 0 and "PASS=" not in out:
        return True, "整套崩（无统计行）"
    return False, "未红的是: %s" % ", ".join(missing)


def restore(path):
    blob = SNAP.get(os.path.abspath(path))
    if blob is None:
        return
    with open(path, "wb") as f:
        f.write(blob)


def main():
    ok_all = True

    # 反向基线：不变异必须 ALL GREEN
    rc, out = run_test()
    base_ok = (rc == 0) and ("ALL GREEN" in out)
    print("反向基线（无变异，全跑）: %s" % ("OK ALL GREEN" if base_ok else "FAIL"))
    if not base_ok:
        print(out[-3000:])
    ok_all = ok_all and base_ok

    results = []
    try:
        for name, targets, path, old, new in MUTATIONS:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            if old not in content:
                print("  [SKIP] %s — 锚点缺失: %r" % (name, old[:48]))
                results.append((name, False))
                ok_all = False
                continue
            if content.count(old) != 1:
                print("  [SKIP] %s — 锚点不唯一（命中 %d 次）: %r"
                      % (name, content.count(old), old[:48]))
                results.append((name, False))
                ok_all = False
                continue

            with open(path, "w", encoding="utf-8") as f:
                f.write(content.replace(old, new, 1))
            try:
                rc, out = run_test()
                hit, why = target_failed(targets, rc, out)
            finally:
                restore(path)

            print("  [%s] %-26s 目标 %s %s%s"
                  % ("OK " if hit else "BAD", name, "/".join(targets),
                     "✓" if hit else "✗", ("  — " + why) if why else ""))
            results.append((name, hit))
            ok_all = ok_all and hit

            with open(path, "rb") as f:
                if f.read() != SNAP[os.path.abspath(path)]:
                    print("  [BAD] %s 还原后字节不一致！" % name)
                    ok_all = False
    finally:
        for _p in (TG, CG, CP):
            restore(_p)

    for _p in (TG, CG, CP):
        with open(_p, "rb") as f:
            same = f.read() == SNAP[os.path.abspath(_p)]
        if not same:
            print("  [BAD] 收尾还原失败: %s" % os.path.basename(_p))
            ok_all = False

    total = len(MUTATIONS)
    ok_n = sum(1 for _, h in results if h)
    print("\n扰动总数: %d  有效: %d  基线: %s" %
          (total, ok_n, "OK" if base_ok else "FAIL"))
    print("PERTURB PASS=%d FAIL=%d"
          % (ok_n, total - ok_n + (0 if base_ok else 1)))
    if ok_all:
        print("ALL PERTURB OK: 判据非空转")
        sys.exit(0)
    print("PERTURB FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
