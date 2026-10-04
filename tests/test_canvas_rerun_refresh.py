# -*- coding: utf-8 -*-
"""画布「运行」重跑语义 + 自动重绘 + 局部编辑参数键 —— 判据套件。

背景（2026-10-04 实测定位的三个缺陷）：

  1. 🔴 「运行」按钮**静默假成功**：`build_demo()` 建图后已用 stub 把示例流跑成终态，
     而 `TaskGraph.is_ready()` 要求 `status == "pending"` → `run()` 里 ready 列表为空 →
     走进 `all_done` 分支直接返回，**真执行器一次都没被调用**；`_run_graph()` 却照样
     打印「运行完成，产物落在…」并列出 stub 当初编造的 `canvas_stage\\...` 路径。
     实测：磁盘 0 文件（全盘搜 `img_source.png` 只命中测试套件 `mkdtemp` 造的）。
  2. 画布**不自动重绘**：`render_graph` 从前只在 `CanvasPanel.__init__` 调一次 →
     连/删线、跑完、撤销都只改数据层，画面纹丝不动（删边后场景仍是 6 节点 7 连线）。
  3. 局部编辑的**「锐化」参数框不生效**：对话框写 `factor`，引擎 `_pil_filter` 读 `amount`
     → 静默回落默认 1.5。

五组判据（独立运行：QT_QPA_PLATFORM=offscreen python tests/test_canvas_rerun_refresh.py）：

  A 重跑语义 —— 静态契约 + 行为 + **端到端落盘** + **对照实验**（证明 reset 是必要条件）
  B `_run_graph` 端到端 —— offscreen 真造 CanvasPanel，点运行后磁盘必须真有文件
  C 自动重绘 —— 删数据边/顺序边、撤销、detail 不被冲掉
  D 局部编辑参数键 —— **行为验证**：对话框造的 params 必须被引擎真采纳（不是静默回落）
  E 源码契约 —— AST 限定在目标函数体内（防「忘了调」，见 L270）

⚠️ 本套件只在 `%TEMP%` 下的临时目录里写产物，不碰用户真实目录。
⚠️ B 组会把 cwd 切到临时目录（`_run_graph` 按 `os.getcwd()` 落盘），跑完恢复。
"""

import ast
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print("  [%s] %s%s" % (tag, name, ("  — " + str(extra)) if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def _hook(t, v, tb):
    print("\n[!] 未捕获异常：%s: %s" % (t.__name__, v))
    print("PASS=%d FAIL=%d" % (CHECKED - len(FAIL), len(FAIL) + 1))
    sys.exit(1)


sys.excepthook = _hook

TG_PATH = os.environ.get("TG_PATH") or os.path.join(ROOT, "task_graph.py")
CG_PATH = os.environ.get("CG_PATH") or os.path.join(ROOT, "canvas_graph.py")
CP_PATH = os.environ.get("CP_PATH") or os.path.join(ROOT, "canvas_panel.py")

TG_SRC = open(TG_PATH, encoding="utf-8").read()
CG_SRC = open(CG_PATH, encoding="utf-8").read()
CP_SRC = open(CP_PATH, encoding="utf-8").read()


def _walk(d):
    out = []
    for r, _dirs, files in os.walk(d):
        for x in files:
            out.append(os.path.relpath(os.path.join(r, x), d))
    return sorted(out)


def _no_net(*a, **k):
    raise RuntimeError("判据不联网：故意让 gen_video 失败（只验 img 是否真落盘）")


def _run_part(fn):
    """逐组执行并把异常变成一条 FAIL —— 否则一组崩了后面几组一行都不跑（L258）。"""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("%s 组抛异常（已转成一条 FAIL）" % fn.__name__, False, repr(e))


# ---------------------------------------------------------------- A 重跑语义
def _a():
    print("A 组 · 重跑语义")
    check("A1 静态：TaskGraph 定义了 reset()", "def reset(self) -> int:" in TG_SRC)
    check("A2 静态：CanvasGraph 定义了 reset_for_rerun()",
          "def reset_for_rerun(self) -> int:" in CG_SRC)

    import canvas_graph as cg

    g = cg.build_sample_graph()
    g.run({})                                    # stub 预跑（= build_demo 干的事）
    before = {k: v.status for k, v in g.nodes.items()}
    check("A3 预跑后节点确实是终态（复现缺陷前提）",
          before and all(s == "completed" for s in before.values()), str(before))

    n_reset = g.reset_for_rerun()
    after = {k: v.status for k, v in g.nodes.items()}
    check("A4 reset_for_rerun 把节点全部归回 pending",
          all(s == "pending" for s in after.values()), str(after))
    check("A5 reset_for_rerun 清空 out_assets（重跑要重新登记，不能留旧引用）",
          all(v is None for n in g.nodes.values() for v in n.out_assets.values()))
    check("A6 reset_for_rerun 返回节点数", n_reset == len(g.nodes),
          "%s vs %s" % (n_reset, len(g.nodes)))

    work = tempfile.mkdtemp(prefix="canvas_rerun_")

    # A7 端到端：预跑 → reset → 真执行器 → run → 磁盘真出现文件
    ar = os.path.join(work, "ar")
    g2 = cg.build_sample_graph()
    g2.run({})
    g2.reset_for_rerun()
    g2.use_real_executors(ar)
    g2.run({})
    files = _walk(ar)
    check("A7 ★ 端到端：预跑后 reset 再 run，磁盘真出现 img_source.png",
          any("img_source.png" in x for x in files), str(files))

    # A8 对照实验：不 reset 就一个文件都不落盘 —— 证明 reset 是**必要条件**而非摆设
    ar2 = os.path.join(work, "ar2")
    g3 = cg.build_sample_graph()
    g3.run({})
    g3.use_real_executors(ar2)                   # 故意不 reset
    g3.run({})
    files2 = _walk(ar2)
    check("A8 对照：不 reset 时一个文件都不落盘（复现原缺陷）", files2 == [], str(files2))


# ------------------------------------------------- B _run_graph 端到端（UI）
def _b():
    print("B 组 · _run_graph 端到端（offscreen 真造 CanvasPanel）")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
    except Exception as e:  # noqa: BLE001
        check("B0 PySide6 可用（画布 UI 的前提）", False, repr(e))
        return

    import agnes_bridge
    agnes_bridge.get_agnes_video_fn = lambda: _no_net   # 不联网

    from canvas_panel import CanvasPanel, build_demo

    work = tempfile.mkdtemp(prefix="canvas_run_")
    orig_cwd = os.getcwd()
    try:
        os.chdir(work)
        g = build_demo()
        panel = CanvasPanel(g)
        panel._run_graph()
        files = _walk(os.path.join(work, "canvas_runtime"))
        detail = [panel.detail.item(i).text() for i in range(panel.detail.count())]

        check("B1 ★ 点「运行」后磁盘真出现 img_source.png",
              any("img_source.png" in x for x in files), str(files))
        check("B2 detail 有「运行完成」行", any("运行完成" in t for t in detail), str(detail[:3]))
        check("B3 detail 没有被写成「运行失败」",
              not any("运行失败" in t for t in detail), str(detail[:3]))
        check("B4 运行后节点不再是清一色 pending（真跑了）",
              any(n.status != "pending" for n in g.nodes.values()),
              str({k: v.status for k, v in g.nodes.items()}))
    finally:
        os.chdir(orig_cwd)


# ------------------------------------------------------------- C 自动重绘
def _c():
    print("C 组 · 自动重绘")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])

    from canvas_panel import (CanvasPanel, build_demo, CanvasNodeItem,
                              CanvasEdgeItem)

    g = build_demo()
    panel = CanvasPanel(g)

    def counts():
        it = panel.scene.items()
        return (sum(1 for i in it if isinstance(i, CanvasNodeItem)),
                sum(1 for i in it if isinstance(i, CanvasEdgeItem)))

    def pick(kind):
        return next((i for i in panel.scene.items()
                     if isinstance(i, CanvasEdgeItem) and i.spec.kind == kind), None)

    c0 = counts()
    e = pick("data")
    if e is None:
        check("C1 示例图里有数据边（前提）", False, "找不到 data 边")
        return
    panel.scene.clearSelection()
    e.setSelected(True)
    d0 = len(g.data_edges)
    panel._delete_selected()
    check("C1 ★ 删数据边后画面立刻少一条线（自动重绘）",
          counts()[1] == c0[1] - 1 and len(g.data_edges) == d0 - 1,
          "%s -> %s / data_edges %d -> %d" % (c0, counts(), d0, len(g.data_edges)))

    panel._undo()
    check("C2 撤销后画面与数据都恢复",
          counts()[1] == c0[1] and len(g.data_edges) == d0, str(counts()))

    e2 = pick("order")
    if e2 is not None:
        o0 = len(g.order_edges)
        panel.scene.clearSelection()
        e2.setSelected(True)
        panel._delete_selected()
        check("C3 ★ 顺序边也能删（旧实现只调 remove_data_edge，点了没反应）",
              len(g.order_edges) == o0 - 1 and counts()[1] == c0[1] - 1,
              "order_edges %d -> %d / %s" % (o0, len(g.order_edges), counts()))
        panel._undo()
        check("C4 撤销后顺序边恢复", len(g.order_edges) == o0,
              "order_edges=%d" % len(g.order_edges))
    else:
        check("C3 示例图里有顺序边（前提）", False, "找不到 order 边")

    panel.detail.clear()
    panel.detail.addItem("哨兵：这是操作结果，不该被重绘冲掉")
    panel._refresh_view()
    txt = [panel.detail.item(i).text() for i in range(panel.detail.count())]
    check("C5 重绘不冲掉详情区（那里承载操作结果反馈）",
          txt == ["哨兵：这是操作结果，不该被重绘冲掉"], str(txt))

    # C6：静态契约（AST 限定作用域，防被同名残留骗 —— 文件里 emit 有 3 处，
    #     全文匹配打掉任意一处都翻不红）
    import ast as _ast
    _fl = next((n for n in _ast.walk(_ast.parse(CP_SRC))
                if isinstance(n, _ast.FunctionDef) and n.name == "_finish_link"), None)
    _fl_src = _ast.get_source_segment(CP_SRC, _fl) if _fl else ""
    check("C6 CanvasScene 定义了 graphChanged 且连线后发信号",
          "graphChanged = Signal()" in CP_SRC
          and "self.graphChanged.emit()" in _fl_src,
          "_finish_link 体里有 emit（scope-limited）")


# --------------------------------------------- D 局部编辑参数键（行为验证）
def _d():
    print("D 组 · 局部编辑参数键（行为验证：UI 造的参数必须被引擎真采纳）")
    import numpy as np
    import image_local_edit as il
    import canvas_graph as cg
    from canvas_panel import LocalEditDialog

    arr = np.zeros((12, 12, 3), dtype=np.float64)
    arr[:, :6] = 0.15
    arr[:, 6:] = 0.85          # 有明显对比，滤波/增益类才看得出差别

    node = cg.build_sample_graph().nodes["img"]

    for op, val in (("sharpen", "3.0"), ("blur", "4.0"), ("brightness", "1.6")):
        dlg = LocalEditDialog(node)
        dlg.cmb_mode.setCurrentText("transform")
        dlg.cmb_op.setCurrentText(op)
        dlg.edit_factor.setText(val)
        params = dlg.build_edit()["params"]
        r_def = il.apply_op(arr.copy(), op, {})
        r_ui = il.apply_op(arr.copy(), op, params)
        check("D %s：对话框填的参数真被引擎采纳（不是静默回落默认值）" % op,
              not np.allclose(r_def, r_ui),
              "params=%s 与默认结果无差别 → 键名没对上" % params)

    # 三条静默丢弃的键（错写时引擎用默认值）反证：写错键的行为确实等于"没写"
    wrong = il.apply_op(arr.copy(), "sharpen", {"factor": 3.0})
    base = il.apply_op(arr.copy(), "sharpen", {})
    check("D 反证：键写错（factor）时引擎确实静默用默认值（说明上面三条判据有效）",
          np.allclose(wrong, base))


# ------------------------------------------------------------- E 源码契约
def _e():
    print("E 组 · 源码契约（AST 限定在目标函数体内，见 L270）")
    tree = ast.parse(CP_SRC)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_run_graph"), None)
    ok_e1, detail = False, "未找到 _run_graph"
    if fn is not None:
        reset_l, run_l = [], []
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                nm = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                if nm == "reset_for_rerun":
                    reset_l.append(n.lineno)
                elif nm == "run":
                    run_l.append(n.lineno)
        ok_e1 = bool(reset_l) and bool(run_l) and min(reset_l) < min(run_l)
        detail = "reset@%s run@%s" % (sorted(reset_l), sorted(run_l))
    check("E1 ★ _run_graph 内 reset_for_rerun 早于 run（防「忘了调」）", ok_e1, detail)

    check("E2 RemoveEdgeCommand 带 kind/label 参数",
          'kind="data", label=""' in CP_SRC)
    check("E3 RemoveEdgeCommand.redo 有顺序边分支（remove_order_edge）",
          "self.graph.remove_order_edge(" in CP_SRC)
    check("E4 _delete_selected 把边的 kind 传下去", "kind=it.spec.kind" in CP_SRC)
    check("E5 render_graph 带 refresh_detail 参数", "refresh_detail: bool = True" in CP_SRC)
    check("E6 TaskGraph.reset 用 self._lock（与 run 的并发语义一致）",
          "def reset(self) -> int:" in TG_SRC and "with self._lock:" in TG_SRC)


if __name__ == "__main__":
    for _part in (_a, _b, _c, _d, _e):
        _run_part(_part)
    print("\nPASS=%d FAIL=%d" % (CHECKED - len(FAIL), len(FAIL)))
    if FAIL:
        print("FAIL 项: " + "; ".join(FAIL))
        sys.exit(1)
    print("ALL GREEN")
    sys.exit(0)
