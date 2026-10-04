# -*- coding: utf-8 -*-
"""画布「手感档」判据套件：滚轮缩放 / 框选平移 / 右键建删节点 / 参数行 schema / topo 真拓扑序。

背景（v4.211.9，ComfyUI 手感第一档）：
  外部分析核实：画布缺滚轮缩放、框选多选、右键菜单建/删节点、结构化参数控件。
  落地时的两个连带修复也一并钉死：
  ① 撤销重绘改信号驱动（undo_stack.indexChanged）—— 只靠按钮刷新时，
     键盘 Delete / 右键菜单 / 探针直调 undo() 的路径画面不动（实测踩过）。
  ② topo() 原先是 task_list() 的 dict 插入序 —— 「删节点→撤销恢复」把任务
     重排到 dict 尾部后"拓扑序"失真，layout_graph 的「前驱必已分配」前提崩
     （ValueError: max() empty，v4.211.9 实测复现）。已改 Kahn 真拓扑序。

八组判据（独立运行：QT_QPA_PLATFORM=offscreen python tests/test_canvas_ops.py）：

  A 滚轮缩放 —— 行为：放大/缩小/上限钳制
  B 框选与平移 —— 行为：RubberBandDrag、中键切平移、框选多选
  C 右键建节点 —— 行为：建节点/落位/端口/底层任务/撤销/场景同步（直调 undo 路径）
  D 删节点 —— 行为：连带清边/任务删除/撤销恢复（节点+边+端口+任务）/重做
  E Delete 键 —— 行为：删选中节点 + 撤销
  F 参数行 schema —— 行为：按类型生成/写回/清空移除/不摆假控件
  G topo 回归 —— 「删→撤」后 dict 序被打乱，topo 仍真拓扑 + layout 不崩 + 依赖清理
  H 源码契约 —— AST/文本：钳制引用常量、indexChanged 连接、schema 只含引擎真读键、
    remove_node 连带清边、remove_task 双向清依赖、topo 不再返回 task_list()

⚠️ 本套件不写磁盘产物（纯内存图 + offscreen 渲染）。
"""

import ast
import os
import sys

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
EX_PATH = os.environ.get("EX_PATH") or os.path.join(ROOT, "executors.py")

TG_SRC = open(TG_PATH, encoding="utf-8").read()
CG_SRC = open(CG_PATH, encoding="utf-8").read()
CP_SRC = open(CP_PATH, encoding="utf-8").read()
EX_SRC = open(EX_PATH, encoding="utf-8").read()


def _run_part(fn):
    """逐组执行并把异常变成一条 FAIL —— 否则一组崩了后面几组一行都不跑（L258）。"""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("%s 组抛异常（已转成一条 FAIL）" % fn.__name__, False, repr(e))


# ---------------------------------------------------------------- A 滚轮缩放
def _a():
    print("A 组 · 滚轮缩放")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, QPoint, Qt
    from PySide6.QtGui import QWheelEvent, QTransform
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    import canvas_panel as cp

    g = cp.build_demo()
    panel = cp.CanvasPanel(g)
    v = panel.view
    m0 = v.transform().m11()          # __init__ 里 _fit() 已适配，初始值 ≠ 1 正常
    check("A1 初始缩放为正", m0 > 0, "m11=%.3f" % m0)

    v.scale(1.25, 1.25)
    check("A2 scale 生效", abs(v.transform().m11() - m0 * 1.25) < 1e-9,
          "%.3f -> %.3f" % (m0, v.transform().m11()))

    up = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    before = v.transform().m11()
    v.wheelEvent(up)
    check("A3 滚轮上=放大", v.transform().m11() > before,
          "%.3f -> %.3f" % (before, v.transform().m11()))

    # 缩小在 1.0 基准上验证（fit 后 m11 可能低于 MIN_ZOOM，此时缩小被钳制是正确行为）
    t = v.transform()
    v.setTransform(QTransform(1, 0, 0, 1, t.dx(), t.dy()))
    dn = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, -120),
                     Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    v.wheelEvent(dn)
    check("A4 滚轮下=缩小（1.0 -> 0.8）", abs(v.transform().m11() - 0.8) < 1e-9,
          "%.3f" % v.transform().m11())

    t = v.transform()
    v.setTransform(QTransform(cp.CanvasView.MAX_ZOOM, 0, 0, cp.CanvasView.MAX_ZOOM,
                              t.dx(), t.dy()))
    m = v.transform().m11()
    v.wheelEvent(up)
    check("A5 ★ 上限钳制（超限滚轮被拒）", abs(v.transform().m11() - m) < 1e-9,
          "%.3f" % v.transform().m11())
    t = v.transform()
    v.setTransform(QTransform(cp.CanvasView.MIN_ZOOM, 0, 0, cp.CanvasView.MIN_ZOOM,
                              t.dx(), t.dy()))
    m = v.transform().m11()
    v.wheelEvent(dn)
    check("A6 ★ 下限钳制", abs(v.transform().m11() - m) < 1e-9, "%.3f" % v.transform().m11())


# ------------------------------------------------------------ B 框选与平移
def _b():
    print("B 组 · 框选与平移")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, QPoint, Qt, QRectF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    import canvas_panel as cp

    g = cp.build_demo()
    panel = cp.CanvasPanel(g)
    v = panel.view
    check("B1 ★ dragMode=RubberBandDrag（左键空白=框选）",
          v.dragMode() == cp.QGraphicsView.RubberBandDrag)

    ev = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, 0),
                     Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    v._pan_start(ev)
    check("B2 中键按下切平移模式（ScrollHandDrag）",
          v.dragMode() == cp.QGraphicsView.ScrollHandDrag)
    v._pan_stop(ev)
    check("B3 松开还原框选模式", v.dragMode() == cp.QGraphicsView.RubberBandDrag)

    path = cp.QPainterPath()
    path.addRect(QRectF(-100, -100, 5000, 5000))
    panel.scene.setSelectionArea(path)
    sel = [it for it in panel.scene.selectedItems()
           if isinstance(it, cp.CanvasNodeItem)]
    check("B4 ★ 框选全图能选中全部 6 节点", len(sel) == 6, "实际 %d" % len(sel))


# ------------------------------------------------------------ C 右键建节点
def _c():
    print("C 组 · 右键建节点")
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    import canvas_panel as cp

    g = cp.build_demo()
    panel = cp.CanvasPanel(g)
    n0 = len(g.nodes)

    panel.scene._apply_menu_choice("add", "gen_image", QPointF(300, 400))
    check("C1 ★ 建节点 nodes+1", len(g.nodes) == n0 + 1)
    nid = "gen_image_1"
    check("C2 唯一 id 生成", nid in g.nodes)
    node = g.nodes[nid]
    check("C3 ★ 落位在鼠标位置", node.pos == (300.0, 400.0), str(node.pos))
    check("C4 默认端口齐（input=prompt / output=image）",
          list(node.inputs) == ["prompt"] and list(node.outputs) == ["image"])
    check("C5 底层任务已注册", g._tg._tasks.get(nid) is not None)

    # 撤销/重做走**直调**路径（不经按钮）—— 验证 indexChanged 信号驱动重绘
    panel.undo_stack.undo()
    check("C6 撤销建节点", nid not in g.nodes and len(g.nodes) == n0)
    panel.undo_stack.redo()
    check("C7 重做恢复", nid in g.nodes)
    panel.undo_stack.undo()
    check("C8 ★ 场景同步重绘（直调 undo 后画面也回 6 节点）",
          len([i for i in panel.scene.items()
               if isinstance(i, cp.CanvasNodeItem)]) == n0)
    check("C9 二次建节点 id 不冲突",
          panel.scene._unique_node_id("gen_image") == "gen_image_1")


# ---------------------------------------------------------------- D 删节点
def _d():
    print("D 组 · 删节点（连带清边 + 撤销恢复）")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    import canvas_panel as cp

    g = cp.build_demo()
    n0, de0, oe0 = len(g.nodes), len(g.data_edges), len(g.order_edges)

    # 数据层直调（不经 UI 信号链）：删节点的语义本身不需要渲染；
    # UI 的删除路径（右键/Delete 键）由 C/E 组覆盖。分开测的另一个好处：
    # 变异引发悬空边时 layout 会级联崩（异常经信号传播中断整组），
    # 那样红的是"组抛异常"而不是 D2 —— 拆开才能红得精确。
    cmd = cp.RemoveNodeCommand(g, None, "img")
    cmd.redo()
    check("D1 ★ 删节点 nodes-1", len(g.nodes) == n0 - 1)
    check("D2 ★ 关联数据边连带清掉（img 参与 2 条）",
          len(g.data_edges) == de0 - 2, "%d -> %d" % (de0, len(g.data_edges)))
    check("D3 底层任务已删", g._tg._tasks.get("img") is None)
    # D4 直测 remove_task 自己的防线：canvas 的边集路径下 undepend 会先清依赖，
    # 这道防线守的是「不经边集的直连依赖」（别的调用方直接 tg.depend 上去的）。
    tg = g._tg
    tg.create("extra_a", "x", lambda s: dict(s))
    tg.create("extra_b", "y", lambda s: dict(s))
    tg.depend("extra_b", "extra_a")
    removed = tg.remove_task("extra_a")
    check("D4 ★ remove_task 清残余依赖（直连 blocked_by 不留悬空 id）",
          removed and "extra_a" not in tg._tasks["extra_b"].blocked_by
          and "extra_a" not in tg._tasks["extra_b"].blocks)

    cmd.undo()
    check("D5 ★ 撤销恢复节点", "img" in g.nodes)
    check("D6 ★ 撤销恢复全部边（数据+顺序）",
          len(g.data_edges) == de0 and len(g.order_edges) == oe0,
          "data %d/%d order %d/%d" % (len(g.data_edges), de0, len(g.order_edges), oe0))
    img = g.nodes["img"]
    check("D7 端口类型随撤销恢复",
          img.inputs["prompt"].port_type == "prompt"
          and img.outputs["image"].port_type == "image")
    check("D8 任务随撤销恢复", g._tg._tasks.get("img") is not None)

    cmd.redo()
    check("D9 重做再删", "img" not in g.nodes)
    cmd.undo()

    try:
        g.remove_node("不存在的节点")
        check("D10 删不存在节点抛 KeyError", False)
    except KeyError:
        check("D10 删不存在节点抛 KeyError", True)


# ---------------------------------------------------------------- E Delete 键
def _e():
    print("E 组 · Delete 键删除选中节点")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    import canvas_panel as cp

    g = cp.build_demo()
    panel = cp.CanvasPanel(g)
    panel.scene.clearSelection()
    target = next(it for it in panel.scene.items()
                  if isinstance(it, cp.CanvasNodeItem) and it.spec.id == "twin")
    target.setSelected(True)
    panel.scene.keyPressEvent(
        QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
    check("E1 ★ Delete 键删掉选中节点", "twin" not in g.nodes)
    panel.undo_stack.undo()
    check("E2 Delete 撤销恢复", "twin" in g.nodes)


# ------------------------------------------------------------ F 参数行 schema
def _f():
    print("F 组 · 参数行 schema（只做引擎真读的键）")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    import canvas_panel as cp

    g = cp.build_demo()
    panel = cp.CanvasPanel(g)
    pp = panel.property_panel

    pp.bind(g, g.nodes["vid"], panel.undo_stack)
    check("F1 ★ gen_video 生成 2 参数行（ref_source/ref_port）",
          sorted(pp.param_rows) == ["ref_port", "ref_source"], str(sorted(pp.param_rows)))
    pp.param_rows["ref_source"].setText("img.image")
    pp.apply()
    check("F2 ★ 参数写回 config", g.nodes["vid"].config.get("ref_source") == "img.image")
    pp.param_rows["ref_source"].setText("")
    pp.apply()
    check("F3 清空=回到「未指定」（键移除）", "ref_source" not in g.nodes["vid"].config)

    pp.bind(g, g.nodes["src"], panel.undo_stack)
    check("F4 ★ passthrough 类节点不摆假控件（0 参数行）", len(pp.param_rows) == 0)


# ------------------------------------------------- G topo 真拓扑序（回归判据）
def _g():
    print("G 组 · topo 真拓扑序（「删→撤」回归）")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    import canvas_panel as cp

    # 数据层直调（不经 UI 信号链）：与 D 组同理 —— 变异 topo 引发的 layout
    # 崩溃若经信号传播，红的是"组抛异常"而不是 G1/G2，扰动口径会误判哑弹。
    g = cp.build_demo()
    cmd = cp.RemoveNodeCommand(g, None, "img")
    cmd.redo()                          # 删 img（dict 序被打乱的前提）
    cmd.undo()                          # 撤销恢复：add_node 把 img 重插到 dict 尾部

    order = g.topo()
    check("G1 ★ 删→撤后 topo 仍是真拓扑序（img 排在 vid 前）",
          "img" in order and "vid" in order and order.index("img") < order.index("vid"),
          str(order))
    try:
        n = len(cp.layout_graph(g).nodes)
    except Exception as e:  # noqa: BLE001
        n = -1
    check("G2 ★ 同状态下 layout_graph 不再崩（原缺陷：max() empty）",
          n == len(g.nodes), "layout=%s nodes=%s" % (n, len(g.nodes)))
    check("G3 topo 覆盖全部节点", sorted(order) == sorted(g.nodes), str(order))
    check("G4 干净图的 topo 与既有判据断言一致",
          cp.build_demo().topo() == ["src", "img", "vid", "twin", "promo", "final"])


# ------------------------------------------------------------- H 源码契约
def _h():
    print("H 组 · 源码契约")

    # H1 wheelEvent 钳制必须引用常量（self.MIN_ZOOM/MAX_ZOOM 是 Attribute 不是 Name）
    tree = ast.parse(CP_SRC)
    wheel = next((n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "wheelEvent"), None)
    attrs = {x.attr for x in ast.walk(wheel) if isinstance(x, ast.Attribute)} if wheel else set()
    check("H1 wheelEvent 存在且钳制引用 MIN_ZOOM/MAX_ZOOM",
          wheel is not None and "MIN_ZOOM" in attrs and "MAX_ZOOM" in attrs)

    # H2 dragMode 是 RubberBandDrag（左键留给框选）
    check("H2 CanvasView 初始化为 RubberBandDrag",
          "setDragMode(QGraphicsView.RubberBandDrag)" in CP_SRC)

    # H3 撤销重绘信号驱动（直调 undo/push 的路径也得重画）
    check("H3 ★ undo_stack.indexChanged 连接 _refresh_view",
          "undo_stack.indexChanged.connect(self._refresh_view)" in CP_SRC)

    # H4 PARAM_SCHEMAS 只含引擎真读的键 —— 每个键都能在 executors.py 里找到读取点
    schemas = re_search_param_schemas(CP_SRC)
    engine_keys = set()
    for m in re_findall_config_gets(EX_SRC):
        engine_keys.add(m)
    stray = [k for keys in schemas.values() for k in keys
             if k not in engine_keys and k != "prompt"]
    check("H4 ★ PARAM_SCHEMAS 只含引擎真读的键（不摆假控件）",
          schemas is not None and stray == [], "越界键: %s" % stray)

    # H5 remove_node 连带清边 + 清任务（三件缺一就是悬空）
    rm = next((n for n in ast.walk(ast.parse(CG_SRC))
               if isinstance(n, ast.FunctionDef) and n.name == "remove_node"), None)
    calls = {x.func.attr for x in ast.walk(rm) if isinstance(x, ast.Call)
             and isinstance(x.func, ast.Attribute)} if rm else set()
    check("H5 ★ remove_node 调用 remove_data_edge + remove_order_edge + remove_task",
          {"remove_data_edge", "remove_order_edge", "remove_task"} <= calls, str(calls))

    # H6 remove_task 双向清依赖
    rt = next((n for n in ast.walk(ast.parse(TG_SRC))
               if isinstance(n, ast.FunctionDef) and n.name == "remove_task"), None)
    rt_names = {x.attr for x in ast.walk(rt) if isinstance(x, ast.Attribute)} if rt else set()
    check("H6 remove_task 双向清 blocked_by 与 blocks",
          rt is not None and "blocked_by" in rt_names and "blocks" in rt_names)

    # H7 topo 不再返回 task_list()（dict 插入序 = 伪拓扑）。
    # 剔 docstring 后查函数体：必须依赖 _edge_pairs，且不含 task_list 调用。
    topo_fn = next((n for n in ast.walk(ast.parse(CG_SRC))
                    if isinstance(n, ast.FunctionDef) and n.name == "topo"), None)
    body_src = ""
    if topo_fn is not None:
        body_nodes = [stmt for stmt in topo_fn.body
                      if not (isinstance(stmt, ast.Expr)
                              and isinstance(stmt.value, ast.Constant))]
        body_src = "\n".join(ast.unparse(s) for s in body_nodes)
    check("H7 ★ topo() 不再返回 task_list()（dict 序伪拓扑已根除）",
          topo_fn is not None and "task_list" not in body_src
          and "_edge_pairs" in body_src)


def re_search_param_schemas(src):
    """从 CP 源码抠 PARAM_SCHEMAS 字典（类型 → [config键...]）。"""
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == "PropertyPanel":
            for s in n.body:
                if isinstance(s, ast.Assign) and any(
                        getattr(t, "id", None) == "PARAM_SCHEMAS" for t in s.targets):
                    out = {}
                    for k, v in zip(s.value.keys, s.value.values):
                        out[ast.literal_eval(k)] = [
                            ast.literal_eval(t.elts[1]) for t in v.elts]
                    return out
    return None


def re_findall_config_gets(src):
    """executors.py 里出现过的 config 读取键（cfg.get / node.config.get 等文本匹配）。"""
    import re
    keys = set()
    for m in re.finditer(r'(?:config|cfg)[^"\n]{0,40}?\.get\(\s*"([^"]+)"', src):
        keys.add(m.group(1))
    return keys


if __name__ == "__main__":
    for _part in (_a, _b, _c, _d, _e, _f, _g, _h):
        _run_part(_part)
    print("\nPASS=%d FAIL=%d" % (CHECKED - len(FAIL), len(FAIL)))
    if FAIL:
        sys.exit(1)
    print("ALL GREEN")
