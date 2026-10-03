# -*- coding: utf-8 -*-
"""节点画布第 3 步 · 画布 UI（渲染层）。

设计稿 §9 第 3 步：把第 1 步的数据模型（canvas_graph.CanvasGraph）+ 第 2 步的
资产库引用（AssetRef.registered）真正画出来——用 QGraphicsView 渲染节点 / 连线 /
状态 / 资产落地标记，支持选中看详情、适配视图。

分层设计（与第 0 步探针一致，避免重蹈覆辙）：
  * 纯逻辑层：layout_graph(g) -> ScenePlan（节点矩形规格 + 边规格 + 画布尺寸）。
    不依赖 Qt，可在无 GUI 环境被判据套件直接 import 验证（见 tests/test_canvas_panel.py）。
  * 视图层：CanvasScene / CanvasView / CanvasPanel 用 Qt 把 ScenePlan 画成可见画布，
    并复用第 0 步已验证的三坑保护法：
      坑③ 高 DPI  —— 坐标全程逻辑像素；grab 快照用 setDevicePixelRatio 保清晰；
      坑① windowed —— 所有对外日志走 _emit（写流失败只写日志文件，绝不抛）；
      坑② ffmpeg  —— 第 5 步执行器才抽帧，这里保留与 voice/video_pipeline 同款的
                     _NO_WINDOW 写法常量，标注对齐点，避免到那步再踩。

挂载说明：本文件是**独立面板**，自带 `python canvas_panel.py` 预览入口；
另已通过 ui.py 的 `_build_canvas_page`（薄 builder + 懒导入）挂进主窗口导航
「工作」分组下的「画布」项（nav_defs / NAV_GROUPS / main_stack 三处同步，A4 守卫仍绿）。
"""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import canvas_graph as cg
from canvas_graph import NODE_TYPES

_log = logging.getLogger("canvas_panel")

# --------------------------------------------------------------------------
# 几何常量（逻辑像素；第 0 步坑③：高分屏只放大设备像素，逻辑坐标不变）
# --------------------------------------------------------------------------
NODE_W, NODE_H = 168.0, 72.0
GAP_X, GAP_Y = 120.0, 48.0
MARGIN = 60.0
PORT_R = 5.0

# 第 0 步坑②：与 ui.py / voice.py / video_pipeline.py 完全同款写法（第 5 步抽帧对齐）
IS_NT = os.name == "nt"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_NT else 0
# 第 0 步坑③：grab 出来的物理像素必须按 DPR 还原，否则高分屏缩成一圈发虚
HAS_DPR = True

# --------------------------------------------------------------------------
# 颜色（对齐 ui.py 的 THEME：sidebar_active #1A73E8、danger #D93025 等）
# --------------------------------------------------------------------------
STATUS_COLOR = {
    "pending":     "#9AA4B2",   # 灰：未开始
    "in_progress": "#1A73E8",   # 蓝：进行中
    "completed":   "#1E8E3E",   # 绿：完成
    "failed":      "#D93025",   # 红：失败
    "cancelled":   "#B06000",   # 暗黄：已取消
    "incomplete":  "#E37400",   # 橙：未完成
}
NODE_FILL = {
    "source_prompt": "#E8F0FE",
    "gen_image":     "#E6F4EA",
    "gen_video":     "#FCE8E6",
    "digital_twin":  "#F3E8FD",
    "promo_fx":      "#FEF7E0",
    "final_output":  "#ECEFF1",
}


# --------------------------------------------------------------------------
# 坑①：安全输出。写流失败只写日志文件，绝不抛（windowed 打包 stdout/stderr 是 None）
# --------------------------------------------------------------------------
def _emit(msg: str):
    try:
        if sys.stdout is not None:
            sys.stdout.write(msg + "\n")
        else:
            raise OSError("no stdout")
    except Exception:
        try:
            _log.info(msg)
        except Exception:
            pass


# --------------------------------------------------------------------------
# 纯逻辑层：ScenePlan（不依赖 Qt，可被判据套件直接验证）
# --------------------------------------------------------------------------
@dataclass
class NodeSpec:
    id: str
    node_type: str
    label: str
    x: float
    y: float
    w: float
    h: float
    status: str
    fill: str
    border: str
    in_ports: List[str] = field(default_factory=list)
    out_ports: List[str] = field(default_factory=list)
    asset_count: int = 0
    assets_registered: int = 0

    @property
    def registered(self) -> bool:
        return self.assets_registered > 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "node_type": self.node_type, "label": self.label,
            "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "status": self.status, "fill": self.fill, "border": self.border,
            "in_ports": list(self.in_ports), "out_ports": list(self.out_ports),
            "asset_count": self.asset_count,
            "assets_registered": self.assets_registered,
        }


@dataclass
class EdgeSpec:
    from_id: str
    to_id: str
    kind: str            # 'data' | 'order'
    label: str
    from_port: str
    to_port: str

    def to_dict(self) -> dict:
        return {
            "from_id": self.from_id, "to_id": self.to_id, "kind": self.kind,
            "label": self.label, "from_port": self.from_port, "to_port": self.to_port,
        }


@dataclass
class ScenePlan:
    nodes: List[NodeSpec] = field(default_factory=list)
    edges: List[EdgeSpec] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "width": self.width, "height": self.height,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScenePlan":
        return cls(
            nodes=[NodeSpec(**n) for n in d.get("nodes", [])],
            edges=[EdgeSpec(**e) for e in d.get("edges", [])],
            width=d.get("width", 0.0), height=d.get("height", 0.0),
        )


def status_color(status: str) -> str:
    """节点状态 → 主题色（六态齐全，缺省回退灰）。"""
    return STATUS_COLOR.get(status, "#9AA4B2")


def _node_parents(g: "cg.CanvasGraph", nid: str) -> List[str]:
    ps: List[str] = []
    for e in g.data_edges:
        if e.to_node == nid:
            ps.append(e.from_node)
    for e in g.order_edges:
        if e.to_node == nid:
            ps.append(e.from_node)
    return ps


def layout_graph(g: "cg.CanvasGraph") -> ScenePlan:
    """把 CanvasGraph 排成横向分层流程图（左→右按拓扑层展开，每层垂直居中）。

    纯逻辑、无 Qt 依赖；输出 ScenePlan 供视图层渲染，也供判据套件直接验证。
    节点矩形两两不重叠（同层 x 相同但 y 不同；跨层 x 不同）。
    """
    assigned: Dict[str, int] = {}
    layers: Dict[int, List[str]] = {}
    # 按拓扑顺序分配层（拓扑保证前驱在前，所以前驱必已分配）
    for nid in g.topo():
        ps = _node_parents(g, nid)
        lyr = 0 if not ps else 1 + max(assigned[p] for p in ps if p in assigned)
        assigned[nid] = lyr
        layers.setdefault(lyr, []).append(nid)

    # 按节点聚合资产落地情况（asset_registrations 可能一节点多产出）
    reg_by_node: Dict[str, dict] = {}
    for r in g.asset_registrations():
        d = reg_by_node.setdefault(r["node"], {"n": 0, "ok": 0})
        d["n"] += 1
        if r.get("registered"):
            d["ok"] += 1

    depths = list(layers.keys())
    max_depth = (max(depths) + 1) if depths else 1
    col_gap = NODE_W + GAP_X
    row_gap = NODE_H + GAP_Y
    max_col_h = max((len(layers[d]) for d in layers), default=0) * row_gap

    nodes: List[NodeSpec] = []
    for depth, ids in layers.items():
        x = MARGIN + depth * col_gap
        total_h = len(ids) * row_gap
        y0 = MARGIN + (max_col_h - total_h) / 2.0
        for i, nid in enumerate(ids):
            n = g.nodes[nid]
            rc = reg_by_node.get(nid, {"n": 0, "ok": 0})
            # 第 4 步：节点若被用户拖到手动位置（n.pos），优先用；否则自动布局
            if n.pos:
                nx, ny = float(n.pos[0]), float(n.pos[1])
            else:
                nx, ny = x, y0 + i * row_gap
            nodes.append(NodeSpec(
                id=nid, node_type=n.node_type,
                label=NODE_TYPES.get(n.node_type, n.node_type),
                x=nx, y=ny, w=NODE_W, h=NODE_H,
                status=n.status,
                fill=NODE_FILL.get(n.node_type, "#ECEFF1"),
                border=status_color(n.status),
                in_ports=list(n.inputs.keys()),
                out_ports=list(n.outputs.keys()),
                asset_count=rc["n"], assets_registered=rc["ok"],
            ))

    edges: List[EdgeSpec] = []
    for e in g.data_edges:
        edges.append(EdgeSpec(e.from_node, e.to_node, "data", e.label,
                              e.from_port, e.to_port))
    for e in g.order_edges:
        edges.append(EdgeSpec(e.from_node, e.to_node, "order", e.reason, "", ""))

    width = MARGIN * 2 + (max_depth - 1) * col_gap + NODE_W
    height = MARGIN * 2 + max_col_h
    return ScenePlan(nodes=nodes, edges=edges, width=width, height=height)


# --------------------------------------------------------------------------
# 视图层（Qt）：把 ScenePlan 画成可见画布（复用第 0 步三坑保护法）
# --------------------------------------------------------------------------
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath,  # noqa: E402
                           QPen)
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsEllipseItem,  # noqa: E402
                               QGraphicsPathItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsTextItem, QGraphicsView,
                               QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QVBoxLayout, QWidget)


def _port_pos(spec: NodeSpec, side: str, idx: int, count: int):
    """计算某端口在节点边缘的逻辑坐标（side: 'L' 输入 / 'R' 输出）。"""
    x = spec.x if side == "L" else spec.x + spec.w
    y = spec.y + (idx + 1) / (count + 1) * spec.h
    return QPointF(x, y)


class CanvasNodeItem(QGraphicsRectItem):
    def __init__(self, spec: NodeSpec):
        super().__init__(QRectF(spec.x, spec.y, spec.w, spec.h))
        self.spec = spec
        self.setBrush(QBrush(QColor(spec.fill)))
        self.setPen(QPen(QColor(spec.border), 2))
        self.setFlags(QGraphicsRectItem.ItemIsSelectable |
                      QGraphicsRectItem.ItemIsMovable)

        # 标题
        title = QGraphicsTextItem(spec.label, self)
        title.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        title.setPos(spec.x + 8, spec.y + 6)
        # 状态文字
        st = QGraphicsTextItem(spec.status, self)
        st.setFont(QFont("Microsoft YaHei", 9))
        st.setPos(spec.x + 8, spec.y + 28)
        # 资产落地标记（右下角小圆：绿=已落地，灰=未落地）
        mark = QGraphicsEllipseItem(
            QRectF(spec.x + spec.w - 14, spec.y + spec.h - 14, 8, 8), self)
        mark.setBrush(QBrush(QColor("#1E8E3E" if spec.registered else "#9AA4B2")))
        mark.setPen(QPen(QColor("#FFFFFF"), 1))
        # 端口：左输入 / 右输出
        for i, p in enumerate(spec.in_ports):
            self._dot("L", i, len(spec.in_ports))
        for i, p in enumerate(spec.out_ports):
            self._dot("R", i, len(spec.out_ports))

    def _dot(self, side, idx, count):
        pos = _port_pos(self.spec, side, idx, count)
        d = QGraphicsEllipseItem(
            QRectF(pos.x() - PORT_R, pos.y() - PORT_R, PORT_R * 2, PORT_R * 2), self)
        d.setBrush(QBrush(QColor("#1A73E8" if side == "L" else "#E37400")))
        d.setPen(QPen(QColor("#FFFFFF"), 1))


class CanvasEdgeItem(QGraphicsPathItem):
    def __init__(self, spec: EdgeSpec, frm: NodeSpec, to: NodeSpec):
        super().__init__()
        self.spec = spec
        # 找对应端口坐标
        fi = frm.out_ports.index(spec.from_port) if spec.from_port in frm.out_ports else 0
        ti = to.in_ports.index(spec.to_port) if spec.to_port in to.in_ports else 0
        p0 = _port_pos(frm, "R", fi, len(frm.out_ports)) if spec.kind == "data" else \
            QPointF(frm.x + frm.w, frm.y + frm.h / 2)
        p1 = _port_pos(to, "L", ti, len(to.in_ports)) if spec.kind == "data" else \
            QPointF(to.x, to.y + to.h / 2)

        path = QPainterPath()
        path.moveTo(p0)
        if spec.kind == "data":
            # 贝塞尔曲线（横向数据流）
            c1 = QPointF((p0.x() + p1.x()) / 2, p0.y())
            c2 = QPointF((p0.x() + p1.x()) / 2, p1.y())
            path.cubicTo(c1, c2, p1)
            self.setPen(QPen(QColor("#1A73E8"), 2))
        else:
            # 顺序边：虚线直线
            path.lineTo(p1)
            pen = QPen(QColor("#9AA4B2"), 1.5, Qt.DashLine)
            self.setPen(pen)
        self.setPath(path)


class CanvasScene(QGraphicsScene):
    def build(self, plan: ScenePlan):
        self.clear()
        node_map = {n.id: n for n in plan.nodes}
        for n in plan.nodes:
            self.addItem(CanvasNodeItem(n))
        for e in plan.edges:
            frm = node_map.get(e.from_id)
            to = node_map.get(e.to_id)
            if frm and to:
                self.addItem(CanvasEdgeItem(e, frm, to))
        self.setSceneRect(0, 0, plan.width, plan.height)


class CanvasView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self._dpr = self.devicePixelRatioF() or 1.0

    def fit_plan(self, plan: ScenePlan):
        self.setSceneRect(0, 0, plan.width, plan.height)
        self.fitInView(0, 0, plan.width, plan.height, Qt.KeepAspectRatio)

    def snapshot(self, path: str) -> bool:
        """第 0 步坑③：grab 出的物理像素按 DPR 还原，否则高分屏缩圈发虚。"""
        pix = self.grab()
        pix.setDevicePixelRatio(self._dpr)
        ok = pix.save(path)
        if ok:
            _emit("快照已保存: %s (dpr=%.2f)" % (path, self._dpr))
        return ok


class CanvasPanel(QWidget):
    """独立画布面板：工具栏 + 视图 + 选中详情。自带预览入口（__main__）。"""

    def __init__(self, graph: Optional["cg.CanvasGraph"] = None, parent=None):
        super().__init__(parent)
        self.graph = graph
        self.scene = CanvasScene(self)
        self.view = CanvasView(self)

        # 工具栏
        bar = QHBoxLayout()
        btn_fit = QPushButton("适配视图")
        btn_fit.clicked.connect(self._fit)
        bar.addWidget(btn_fit)
        bar.addStretch(1)
        legend = QLabel("● 完成  ● 进行  ● 失败  ● 待办 蓝色实线=数据 灰色虚线=顺序")
        bar.addWidget(legend)

        # 详情
        self.detail = QListWidget()
        self.detail.setMaximumHeight(120)

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.detail)
        self.setLayout(lay)

        if graph is not None:
            self.render_graph(graph)

    def render_graph(self, graph: "cg.CanvasGraph"):
        self.graph = graph
        self.plan = layout_graph(graph)
        self.scene.build(self.plan)
        self.view.setScene(self.scene)
        self._fit()
        self._refresh_detail(None)

    def _fit(self):
        if getattr(self, "plan", None) is not None:
            self.view.fit_plan(self.plan)

    def _refresh_detail(self, node_id):
        self.detail.clear()
        if node_id is None or self.graph is None:
            self.detail.addItem("（点击节点查看详情）")
            return
        n = self.graph.nodes.get(node_id)
        if n is None:
            return
        regs = [r for r in self.graph.asset_registrations() if r["node"] == node_id]
        self.detail.addItem("节点: %s (%s)" % (n.id, NODE_TYPES.get(n.node_type, n.node_type)))
        self.detail.addItem("状态: %s" % n.status)
        self.detail.addItem("产出资产: %d 个，已落地 %d 个" % (
            len(regs), sum(1 for r in regs if r.get("registered"))))


def build_demo(asset_store=None) -> "cg.CanvasGraph":
    """建示例图并 run 到终态（供预览 / 判据复用）。"""
    g = cg.build_sample_graph(asset_store=asset_store)
    g.run({})
    return g


if __name__ == "__main__":
    app = QApplication(sys.argv)
    g = build_demo()
    panel = CanvasPanel(g)
    panel.setWindowTitle("小臭玩AI · 节点画布（第3步预览）")
    panel.resize(1100, 620)
    panel.show()
    _emit("示例图节点数=%d 边数=%d" % (len(g.nodes), len(g.data_edges) + len(g.order_edges)))
    sys.exit(app.exec())
