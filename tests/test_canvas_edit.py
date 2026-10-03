# -*- coding: utf-8 -*-
"""节点画布阶段 A（可编辑设计画布）· 编辑交互层判据套件。

验证目标（设计稿 §9 阶段 A）：
  A 静态：编辑能力符号/方法存在 + 关键契约代码片段（拖动写回 pos、端口连线、
          撤销命令类、属性面板、撤销栈）在源码中落地。
  B 行为（无 GUI 实例化 widget，纯数据层 + 命令类 redo/undo）：
          1) set_node_pos 写回 → layout_graph 尊重手动位置
          2) 导出→导入 pos 闭环保留（可编辑画布存盘/读回不丢位置）
          3) set_node_config / EditConfigCommand 改配置 + 撤销恢复
          4) remove_data_edge / ConnectCommand / RemoveEdgeCommand 增删边 + 撤销
          5) MoveNodeCommand 改动坐标 + 撤销恢复
  C 结构：用 _slice_func 切片命令类/节点类，确认它们确实调用了底层编辑方法。

判据可在无 GUI 环境运行：命令类继承 QUndoCommand（QtGui）无需 QApplication；
Qt 部件实例化在沙箱被拦属环境限制，不影响本套件（不创建 QWidget）。
"""

import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import canvas_graph as cg                       # noqa: E402
import canvas_panel as cp                        # noqa: E402
import canvas_export as ce                        # noqa: E402

PANEL_SRC = open(os.path.join(ROOT, "canvas_panel.py"), encoding="utf-8").read()
GRAPH_SRC = open(os.path.join(ROOT, "canvas_graph.py"), encoding="utf-8").read()

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    mark = "PASS" if ok else "FAIL"
    print("[%s] %s%s" % (mark, name, (" :: " + detail) if detail else ""))


def _slice_func(src, name):
    """提取 src 中顶层 def/class `name` 的源码切片（含嵌套成员）。"""
    lines = src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"^(def|class)\s+{re.escape(name)}(\s*\(|\s*:|$)", ln):
            start = i
            break
    if start is None:
        return ""
    body = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.strip() == "":
            body.append(ln)
            continue
        lead = len(ln) - len(ln.lstrip(" "))
        if lead == 0:
            break
        body.append(ln)
    return "\n".join(body)


# =========================================================================
# A 静态：符号与契约片段存在性
# =========================================================================
def section_a():
    print("\n===== A. 静态契约 =====")
    # 数据层编辑方法
    for m in ("set_node_pos", "set_node_config", "remove_data_edge", "remove_order_edge"):
        check("A1 数据层方法 %s 存在" % m, ("def %s" % m) in GRAPH_SRC)
    # 视图层符号
    check("A2 QUndoStack/QUndoCommand 引入", "QUndoStack" in PANEL_SRC and "QUndoCommand" in PANEL_SRC)
    for sym in ("class PortItem", "class PropertyPanel", "class MoveNodeCommand",
                "class EditConfigCommand", "class ConnectCommand", "class RemoveEdgeCommand"):
        check("A3 视图符号 %s 存在" % sym, sym in PANEL_SRC)
    check("A4 选中刷新 selectionChanged.connect",
          "selectionChanged.connect" in PANEL_SRC)
    check("A5 CanvasScene 连线交互 begin_link/_finish_link",
          "def begin_link" in PANEL_SRC and "def _finish_link" in PANEL_SRC)

    # 关键契约切片
    node_slice = _slice_func(PANEL_SRC, "CanvasNodeItem")
    check("A6 节点拖动松手写回 on_node_moved",
          "on_node_moved" in node_slice)
    mv = _slice_func(PANEL_SRC, "MoveNodeCommand")
    check("A7 MoveNodeCommand 调用 set_node_pos", "set_node_pos" in mv)
    cc = _slice_func(PANEL_SRC, "ConnectCommand")
    check("A8 ConnectCommand 调 connect_data + remove_data_edge",
          "connect_data" in cc and "remove_data_edge" in cc)
    ec = _slice_func(PANEL_SRC, "EditConfigCommand")
    check("A9 EditConfigCommand 调 set_node_config",
          "set_node_config" in ec)
    re_ = _slice_func(PANEL_SRC, "RemoveEdgeCommand")
    check("A10 RemoveEdgeCommand 调 remove_data_edge",
          "remove_data_edge" in re_)
    sc = _slice_func(PANEL_SRC, "CanvasScene")
    check("A11 CanvasScene 端口 mouseRelease 收尾连线",
          "mouseReleaseEvent" in sc and "_finish_link" in sc)


# =========================================================================
# B 行为：纯数据层 + 命令类 redo/undo
# =========================================================================
def section_b():
    print("\n===== B. 行为（无 GUI）=====")
    g = cg.build_sample_graph()
    g.run({})

    # B1 set_node_pos 写回 → layout_graph 尊重坐标
    g.set_node_pos("img", 300, 200)
    plan = cp.layout_graph(g)
    img_spec = next(s for s in plan.nodes if s.id == "img")
    check("B1 set_node_pos 被 layout 尊重 (x=300)", abs(img_spec.x - 300) < 1e-6,
          "x=%.1f" % img_spec.x)

    # B2 导出→导入 pos 闭环保留
    g2 = cg.build_sample_graph()
    g2.run({})
    g2.set_node_pos("vid", 520, 360)
    tmp = os.path.join(tempfile.gettempdir(), "canvas_pos_roundtrip.json")
    ce.export_project_json(g2, tmp)
    g3 = ce.import_project_json(tmp)
    check("B2 导出→导入 pos 保留 (vid=(520,360))",
          g3.nodes["vid"].pos == (520.0, 360.0),
          "imported=%r" % (g3.nodes["vid"].pos,))
    os.remove(tmp)

    # B3 set_node_config 改配置 + 撤销恢复
    g.set_node_config("src", {"prompt": "一条测试提示词"})
    check("B3 set_node_config 写回 to_dict",
          g.nodes["src"].to_dict()["config"].get("prompt") == "一条测试提示词")
    g.set_node_config("src", {})
    check("B3b 配置可还原", g.nodes["src"].to_dict()["config"] == {})

    # B4 remove_data_edge 删边
    before = len(g.data_edges)
    removed = g.remove_data_edge("src", "prompt", "img", "prompt")
    after = len(g.data_edges)
    check("B4 remove_data_edge 删一条边", before - after == 1 and removed is not None)
    gone = any(e.from_node == "src" and e.from_port == "prompt"
               and e.to_node == "img" and e.to_port == "prompt"
               for e in g.data_edges)
    check("B4b to_dict 不含被删边", not gone)
    # 复原（重连，供后续判据）
    g.connect_data("src", "prompt", "img", "prompt")

    # B5 MoveNodeCommand redo/undo（独立子图，避免被 B1 污染坐标）
    g5 = cg.build_sample_graph()
    g5.run({})
    cmd = cp.MoveNodeCommand(g5, None, "img", (0.0, 0.0), (300.0, 200.0), None)
    cmd.redo()
    check("B5 MoveNodeCommand.redo 改坐标", g5.nodes["img"].pos == (300.0, 200.0),
          "pos=%r" % (g5.nodes["img"].pos,))
    cmd.undo()
    check("B5b .undo 恢复坐标", g5.nodes["img"].pos == (0.0, 0.0))

    # B6 ConnectCommand redo/undo 增删边
    # 注：必须用**独立小图**。示例图里 final.main / final.promo 各自已有一条数据边，
    # 往同一个单入端口再连第二条会被 connect_data 拒绝
    # （Wave A #3：单入端口禁止静默覆盖），
    # 所以「在示例图上随便挑一条边」的旧写法本质上依赖的是被修掉的覆盖 bug。
    g6 = cg.CanvasGraph()
    s6 = cg.CanvasNode("s", "source_prompt",
                       outputs={"prompt": cg.Port("prompt", "prompt")})
    t6 = cg.CanvasNode("t", "gen_image",
                       inputs={"prompt": cg.Port("prompt", "prompt")})
    g6.add_node(s6)
    g6.add_node(t6)
    n0 = len(g6.data_edges)
    cc = cp.ConnectCommand(g6, None, "s", "prompt", "t", "prompt")
    cc.redo()
    check("B6 ConnectCommand.redo 增边", len(g6.data_edges) == n0 + 1)
    e6 = g6.data_edges[0] if g6.data_edges else None
    check("B6c redo 后边为 s.prompt→t.prompt",
          e6 is not None
          and (e6.from_node, e6.from_port, e6.to_node, e6.to_port)
          == ("s", "prompt", "t", "prompt"))
    cc.undo()
    check("B6b .undo 删边", len(g6.data_edges) == n0)

    # B6d/B6e（Wave B #2）：连线命令的 redo/undo 必须把**底层依赖**一起增删。
    # 只删边不撤依赖 = 幽灵依赖 —— 节点被一条已不存在的连线绑住，画布上看不出来。
    def _deps_of(g, nid):
        for td in g._tg.task_list():
            if td["id"] == nid:
                return list(td.get("blockedBy", []))
        return []

    check("B6d 撤销连线后底层依赖同步撤销（不留幽灵依赖）",
          _deps_of(g6, "t") == [], str(_deps_of(g6, "t")))
    cc.redo()
    check("B6e 重做后依赖恢复且只有一条（无重复项）",
          _deps_of(g6, "t") == ["s"], str(_deps_of(g6, "t")))
    cc.undo()

    # B7 EditConfigCommand redo/undo
    ecmd = cp.EditConfigCommand(g, None, "src", {}, {"prompt": "X"})
    ecmd.redo()
    check("B7 EditConfigCommand.redo 改配置", g.nodes["src"].config.get("prompt") == "X")
    ecmd.undo()
    check("B7b .undo 还原配置", g.nodes["src"].config == {})

    # B8 RemoveEdgeCommand redo/undo
    n1 = len(g.data_edges)
    rcmd = cp.RemoveEdgeCommand(g, None, "src", "prompt", "img", "prompt")
    rcmd.redo()
    check("B8 RemoveEdgeCommand.redo 删边", len(g.data_edges) == n1 - 1)
    rcmd.undo()
    check("B8b .undo 恢复边", len(g.data_edges) == n1)


# =========================================================================
# C 结构：命令类确实绑定底层编辑方法（切片非空转）
# =========================================================================
def section_c():
    print("\n===== C. 结构绑定 =====")
    mv = _slice_func(PANEL_SRC, "MoveNodeCommand")
    check("C1 切片 MoveNodeCommand 非空含 set_node_pos", "set_node_pos" in mv)
    cc = _slice_func(PANEL_SRC, "ConnectCommand")
    check("C2 切片 ConnectCommand 非空含 connect_data", "connect_data" in cc)
    re_ = _slice_func(PANEL_SRC, "RemoveEdgeCommand")
    check("C3 切片 RemoveEdgeCommand 非空含 remove_data_edge", "remove_data_edge" in re_)
    node = _slice_func(PANEL_SRC, "CanvasNodeItem")
    check("C4 切片 CanvasNodeItem 非空含 on_node_moved 调用",
          "on_node_moved" in node)


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
    passed = sum(1 for _, ok in results if ok)
    failed = len(results) - passed
    print("\n===== 判据汇总 =====")
    print("总计 %d 项，通过 %d，失败 %d" % (len(results), passed, failed))
    if failed:
        print("FAIL=" + ",".join(n for n, ok in results if not ok))
        sys.exit(1)
    print("ALL GREEN")
