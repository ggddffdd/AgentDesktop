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

import json
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
# 阶段 B 纯辅助：图片局部编辑的「是否可编辑」判断 + 遮罩区域几何（无 Qt 依赖）
# --------------------------------------------------------------------------
# 支持局部编辑的输出端口类型（与 canvas_graph._PORT_ACCEPTS["image"] 对齐）
_IMAGE_PORT_TYPES = {"image", "scene", "character_views", "keyframe"}


def node_supports_local_edit(node: "cg.CanvasNode") -> bool:
    """节点是否可对其图片类产出做局部编辑（生图/场景图/参考图等）。"""
    if node is None:
        return False
    if node.node_type == "gen_image":
        return True
    return any(p.port_type in _IMAGE_PORT_TYPES for p in node.outputs.values())


def norm_rect(region: Optional[dict], iw: float, ih: float):
    """把归一化 region 转成像素矩形 (x, y, w, h)。

    region=None → 整图；rect → 直接用 x/y/w/h；polygon → 取点集包围盒。
    iw/ih 为原图像素尺寸。结果用于 QPainter 裁剪 / inpaint 掩码对齐。
    """
    iw, ih = float(iw), float(ih)
    if not region:
        return (0.0, 0.0, iw, ih)
    if region.get("type") == "rect":
        return (region["x"] * iw, region["y"] * ih,
                region["w"] * iw, region["h"] * ih)
    pts = region.get("points", [])
    if pts:
        xs = [p[0] * iw for p in pts]
        ys = [p[1] * ih for p in pts]
        x0, y0 = min(xs), min(ys)
        return (x0, y0, max(xs) - x0, max(ys) - y0)
    return (0.0, 0.0, iw, ih)


def region_svg_overlay(region: Optional[dict], ox: float, oy: float,
                       ow: float, oh: float) -> str:
    """在节点矩形 (ox,oy,ow,oh) 内，把归一化 region 画成 SVG 叠加（红色虚线）。

    供 export_svg 标记「已局部编辑」节点：整图→节点内框；rect→对应区域；
    polygon→闭合路径。返回 SVG 片段字符串（可能为空）。
    """
    if not region:
        return (f'<rect x="{ox+4:.1f}" y="{oy+4:.1f}" width="{ow-8:.1f}" '
                f'height="{oh-8:.1f}" fill="none" stroke="#D93025" '
                f'stroke-width="2" stroke-dasharray="5 3"/>')
    if region.get("type") == "rect":
        rx = ox + region["x"] * ow
        ry = oy + region["y"] * oh
        rw = region["w"] * ow
        rh = region["h"] * oh
        return (f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" '
                f'fill="rgba(217,48,37,0.15)" stroke="#D93025" stroke-width="2" '
                f'stroke-dasharray="5 3"/>')
    pts = region.get("points", [])
    if pts:
        d = "M " + " L ".join("%.1f %.1f" % (ox + p[0] * ow, oy + p[1] * oh)
                              for p in pts) + " Z"
        return (f'<path d="{d}" fill="rgba(217,48,37,0.15)" stroke="#D93025" '
                f'stroke-width="2" stroke-dasharray="5 3"/>')
    return ""


# --------------------------------------------------------------------------
# 视图层（Qt）：把 ScenePlan 画成可见画布（复用第 0 步三坑保护法）
# --------------------------------------------------------------------------
from PySide6.QtCore import QPointF, QRectF, Qt, Signal  # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath,  # noqa: E402
                           QPen, QPolygonF, QUndoCommand, QUndoStack)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFormLayout,  # noqa: E402
                               QFrame, QGraphicsEllipseItem, QGraphicsPathItem,
                               QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSceneMouseEvent, QGraphicsTextItem, QGraphicsView,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
                               QTextEdit, QVBoxLayout, QWidget)


def _port_pos(spec: NodeSpec, side: str, idx: int, count: int):
    """计算某端口在节点边缘的逻辑坐标（side: 'L' 输入 / 'R' 输出）。"""
    x = spec.x if side == "L" else spec.x + spec.w
    y = spec.y + (idx + 1) / (count + 1) * spec.h
    return QPointF(x, y)


class PortItem(QGraphicsEllipseItem):
    """可交互端口：输出端口(side='R')可由鼠标拖出连线；输入端口('L')为连线终点。

    端口是节点的子项；鼠标在输出端口按下即通知场景进入「连线模式」。
    """

    def __init__(self, node_id, port, side, port_type, x, y, parent):
        super().__init__(QRectF(x - PORT_R, y - PORT_R, PORT_R * 2, PORT_R * 2), parent)
        self.node_id = node_id
        self.port = port
        self.side = side                 # 'L' 输入 / 'R' 输出
        self.port_type = port_type
        color = "#1A73E8" if side == "L" else "#E37400"
        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(QColor("#FFFFFF"), 1))
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor if side == "R" else Qt.CrossCursor)

    def mousePressEvent(self, ev: QGraphicsSceneMouseEvent):
        # 仅输出端口可发起连线
        if self.side == "R":
            scene = self.scene()
            if isinstance(scene, CanvasScene):
                scene.begin_link(self)
            ev.accept()
        else:
            ev.ignore()


class CanvasNodeItem(QGraphicsRectItem):
    def __init__(self, spec: NodeSpec, node=None):
        super().__init__(QRectF(spec.x, spec.y, spec.w, spec.h))
        self.spec = spec
        self.node = node                  # 对应 CanvasNode（pos/config 写回目标）
        self.setBrush(QBrush(QColor(spec.fill)))
        self.setPen(QPen(QColor(spec.border), 2))
        self.setFlags(QGraphicsRectItem.ItemIsSelectable |
                      QGraphicsRectItem.ItemIsMovable)
        self.setAcceptHoverEvents(True)

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
        # 端口：左输入 / 右输出（可交互 PortItem）
        ports = node.inputs if node is not None else {}
        for i, p in enumerate(spec.in_ports):
            pos = _port_pos(spec, "L", i, len(spec.in_ports))
            ptype = ports.get(p).port_type if p in ports else "data"
            PortItem(spec.id, p, "L", ptype, pos.x(), pos.y(), self)
        ports = node.outputs if node is not None else {}
        for i, p in enumerate(spec.out_ports):
            pos = _port_pos(spec, "R", i, len(spec.out_ports))
            ptype = ports.get(p).port_type if p in ports else "data"
            PortItem(spec.id, p, "R", ptype, pos.x(), pos.y(), self)

        self._drag_start = QPointF(spec.x, spec.y)

    def mousePressEvent(self, ev):
        self._drag_start = self.pos()
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        # 拖动结束（位置变化）写回 node.pos 并 push 撤销命令
        cur = self.pos()
        if cur != self._drag_start and self.node is not None:
            scene = self.scene()
            if isinstance(scene, CanvasScene):
                scene.on_node_moved(self, self._drag_start, cur)


class CanvasEdgeItem(QGraphicsPathItem):
    def __init__(self, spec: EdgeSpec, frm: NodeSpec, to: NodeSpec):
        super().__init__()
        self.spec = spec
        self.edge_tuple = (spec.from_node, spec.from_port, spec.to_node, spec.to_port)
        self.setFlags(QGraphicsPathItem.ItemIsSelectable)
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
    """画布场景：渲染节点/连线，并承载阶段 A 的编辑交互（拖拽写回 + 端口连线）。"""

    def __init__(self, graph=None, undo_stack=None, parent=None):
        super().__init__(parent)
        self.graph = graph
        self.undo_stack = undo_stack
        self._link_src = None          # 发起连线的输出 PortItem
        self._temp_link = None         # 临时连线 QGraphicsPathItem

    def build(self, plan: ScenePlan, graph=None):
        if graph is not None:
            self.graph = graph
        self.clear()
        node_map = {n.id: n for n in plan.nodes}
        g_nodes = self.graph.nodes if self.graph else {}
        for n in plan.nodes:
            self.addItem(CanvasNodeItem(n, g_nodes.get(n.id)))
        for e in plan.edges:
            frm = node_map.get(e.from_id)
            to = node_map.get(e.to_id)
            if frm and to:
                self.addItem(CanvasEdgeItem(e, frm, to))
        self.setSceneRect(0, 0, plan.width, plan.height)

    # ---- 连线交互（输出端口拖到输入端口）----
    def begin_link(self, port_item: PortItem):
        if port_item.side != "R":
            return
        self._link_src = port_item
        self._temp_link = QGraphicsPathItem()
        self._temp_link.setPen(QPen(QColor("#1A73E8"), 2, Qt.DashLine))
        self.addItem(self._temp_link)

    def _clear_link(self):
        if self._temp_link is not None:
            self.removeItem(self._temp_link)
            self._temp_link = None
        self._link_src = None

    def mouseMoveEvent(self, ev):
        if self._link_src is not None and self._temp_link is not None:
            p0 = self._link_src.scenePos() + QPointF(PORT_R, PORT_R)
            p1 = ev.scenePos()
            path = QPainterPath()
            path.moveTo(p0)
            cx = (p0.x() + p1.x()) / 2
            path.cubicTo(QPointF(cx, p0.y()), QPointF(cx, p1.y()), p1)
            self._temp_link.setPath(path)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._link_src is not None:
            self._finish_link(ev)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def _finish_link(self, ev):
        src = self._link_src
        self._clear_link()
        if src is None or self.graph is None:
            return
        # 找鼠标下的输入端口
        target = None
        for it in self.items(ev.scenePos()):
            if isinstance(it, PortItem) and it.side == "L":
                target = it
                break
        if target is None:
            return
        f = (src.node_id, src.port, target.node_id, target.port)
        # 预校验（不实际留下边）：connect_data 内部做端口存在 + 类型兼容校验
        try:
            self.graph.connect_data(*f)
        except ValueError:
            return
        self.graph.remove_data_edge(*f)   # 回滚试探
        cmd = ConnectCommand(self.graph, self.undo_stack, *f)
        if self.undo_stack is not None:
            self.undo_stack.push(cmd)
        else:
            cmd.redo()

    # ---- 节点拖动写回 ----
    def on_node_moved(self, item, start: QPointF, end: QPointF):
        if self.graph is None:
            return
        old = (start.x(), start.y())
        new = (end.x(), end.y())
        cmd = MoveNodeCommand(self.graph, self.undo_stack, item.spec.id, old, new, item)
        if self.undo_stack is not None:
            self.undo_stack.push(cmd)
        else:
            cmd.redo()


# --------------------------------------------------------------------------
# 撤销 / 重做命令（QUndoCommand 包裹编辑操作；item 为可选，判据可传 None 纯测数据层）
# --------------------------------------------------------------------------
class MoveNodeCommand(QUndoCommand):
    def __init__(self, graph, undo_stack, node_id, old_pos, new_pos, item=None):
        super().__init__("移动节点 %s" % node_id)
        self.graph = graph
        self.node_id = node_id
        self.old_pos = old_pos
        self.new_pos = new_pos
        self.item = item

    def _apply(self, pos):
        self.graph.set_node_pos(self.node_id, pos[0], pos[1])
        if self.item is not None:
            self.item.setPos(QPointF(pos[0], pos[1]))

    def redo(self):
        self._apply(self.new_pos)

    def undo(self):
        self._apply(self.old_pos)


class EditConfigCommand(QUndoCommand):
    def __init__(self, graph, undo_stack, node_id, old_cfg, new_cfg):
        super().__init__("编辑配置 %s" % node_id)
        self.graph = graph
        self.node_id = node_id
        self.old_cfg = old_cfg
        self.new_cfg = new_cfg

    def redo(self):
        self.graph.set_node_config(self.node_id, self.new_cfg)

    def undo(self):
        self.graph.set_node_config(self.node_id, self.old_cfg)


class ConnectCommand(QUndoCommand):
    def __init__(self, graph, undo_stack, from_node, from_port, to_node, to_port):
        super().__init__("连接 %s.%s -> %s.%s" %
                         (from_node, from_port, to_node, to_port))
        self.graph = graph
        self.f = (from_node, from_port, to_node, to_port)

    def redo(self):
        self.graph.connect_data(*self.f)

    def undo(self):
        self.graph.remove_data_edge(*self.f)


class RemoveEdgeCommand(QUndoCommand):
    def __init__(self, graph, undo_stack, from_node, from_port, to_node, to_port):
        super().__init__("删除连线 %s.%s -> %s.%s" %
                         (from_node, from_port, to_node, to_port))
        self.graph = graph
        self.f = (from_node, from_port, to_node, to_port)

    def redo(self):
        self.graph.remove_data_edge(*self.f)

    def undo(self):
        self.graph.connect_data(*self.f)


# --------------------------------------------------------------------------
# 阶段 B：图片局部编辑的撤销命令（整份快照 set_local_edits，idempotent）
# --------------------------------------------------------------------------
class AddLocalEditCommand(QUndoCommand):
    def __init__(self, graph, undo_stack, node_id, edit):
        super().__init__("添加局部编辑 %s" % node_id)
        self.graph = graph
        self.node_id = node_id
        self.old_list = graph.get_local_edits(node_id)
        rec = graph._make_edit_record(edit)
        rec["id"] = "le%04d" % (len(self.old_list) + 1)
        self.new_list = self.old_list + [rec]
        self.record = rec

    def redo(self):
        self.graph.set_local_edits(self.node_id, self.new_list)

    def undo(self):
        self.graph.set_local_edits(self.node_id, self.old_list)


class RemoveLocalEditCommand(QUndoCommand):
    def __init__(self, graph, undo_stack, node_id, idx):
        super().__init__("删除局部编辑 %s#%d" % (node_id, idx))
        self.graph = graph
        self.node_id = node_id
        self.idx = idx
        self.old_list = graph.get_local_edits(node_id)
        self.removed = dict(self.old_list[idx]) if 0 <= idx < len(self.old_list) else {}
        self.new_list = [e for i, e in enumerate(self.old_list) if i != idx]

    def redo(self):
        self.graph.set_local_edits(self.node_id, self.new_list)

    def undo(self):
        self.graph.set_local_edits(self.node_id, self.old_list)


class EditLocalEditCommand(QUndoCommand):
    def __init__(self, graph, undo_stack, node_id, idx, edit):
        super().__init__("修改局部编辑 %s#%d" % (node_id, idx))
        self.graph = graph
        self.node_id = node_id
        self.idx = idx
        self.old_list = graph.get_local_edits(node_id)
        self.old_record = dict(self.old_list[idx]) if 0 <= idx < len(self.old_list) else {}
        rec = graph._make_edit_record(edit)
        rec["id"] = self.old_record.get("id") or ("le%04d" % (idx + 1))
        self.new_list = list(self.old_list)
        self.new_list[idx] = rec

    def redo(self):
        self.graph.set_local_edits(self.node_id, self.new_list)

    def undo(self):
        self.graph.set_local_edits(self.node_id, self.old_list)


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


class MaskEditorWidget(QGraphicsView):
    """轻量遮罩编辑器：在归一化 0..1 的预览区上画矩形 / 多边形选区。

    不依赖真实图像（stub 不产真图）；以占位背景表示图像区，所有坐标归一化
    存于 region dict（rect / polygon / None=整图），供指令持久化与后续执行器使用。
    """

    def __init__(self, parent=None, size=300):
        super().__init__(parent)
        self._size = size
        self._scene = QGraphicsScene(0, 0, size, size, self)
        self.setScene(self._scene)
        self.setFixedSize(size + 10, size + 10)
        self.setRenderHint(QPainter.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCursor(Qt.CrossCursor)
        bg = QGraphicsRectItem(0, 0, size, size)
        bg.setBrush(QBrush(QColor("#ECEFF1")))
        bg.setPen(QPen(QColor("#9AA4B2")))
        bg.setFlag(QGraphicsRectItem.ItemIsSelectable, False)
        bg.setFlag(QGraphicsRectItem.ItemIsMovable, False)
        self._scene.addItem(bg)
        self._overlay = None
        self._region = None
        self._mode = "rect"
        self._drag = None
        self._poly = []            # 归一化点列表 [[x,y],...]

    def set_mode(self, mode):
        self._mode = mode
        self._poly = []
        self._drag = None
        self._redraw()

    def get_region(self):
        return self._region

    def set_region(self, region):
        self._region = region
        self._poly = []
        if region and region.get("type") == "polygon":
            self._poly = [list(p) for p in region.get("points", [])]
        self._redraw()

    def _to_norm(self, scene_pos):
        s = float(self._size)
        return (max(0.0, min(1.0, scene_pos.x() / s)),
                max(0.0, min(1.0, scene_pos.y() / s)))

    def _redraw(self):
        if self._overlay is not None:
            self._scene.removeItem(self._overlay)
            self._overlay = None
        s = float(self._size)
        reg = self._region
        if self._mode == "polygon" and self._poly:
            poly = QPolygonF([QPointF(p[0] * s, p[1] * s) for p in self._poly])
            item = QGraphicsPolygonItem(poly)
            item.setBrush(QBrush(QColor(217, 48, 37, 60)))
            item.setPen(QPen(QColor("#D93025"), 2))
            self._scene.addItem(item)
            self._overlay = item
            return
        if reg:
            if reg.get("type") == "rect":
                rx = reg["x"] * s
                ry = reg["y"] * s
                rw = reg["w"] * s
                rh = reg["h"] * s
                item = QGraphicsRectItem(rx, ry, rw, rh)
                item.setBrush(QBrush(QColor(217, 48, 37, 60)))
                item.setPen(QPen(QColor("#D93025"), 2, Qt.DashLine))
                self._scene.addItem(item)
                self._overlay = item
            elif reg.get("type") == "polygon":
                poly = QPolygonF([QPointF(p[0] * s, p[1] * s)
                                  for p in reg.get("points", [])])
                item = QGraphicsPolygonItem(poly)
                item.setBrush(QBrush(QColor(217, 48, 37, 60)))
                item.setPen(QPen(QColor("#D93025"), 2))
                self._scene.addItem(item)
                self._overlay = item

    def mousePressEvent(self, ev):
        sp = self.mapToScene(ev.pos())
        if self._mode == "rect":
            self._drag = self._to_norm(sp)
            ev.accept()
        else:  # polygon：点选加顶点
            self._poly.append(list(self._to_norm(sp)))
            self._redraw()
            ev.accept()

    def mouseMoveEvent(self, ev):
        if self._mode == "rect" and self._drag is not None:
            cur = self._to_norm(self.mapToScene(ev.pos()))
            x = min(self._drag[0], cur[0])
            y = min(self._drag[1], cur[1])
            w = abs(cur[0] - self._drag[0])
            h = abs(cur[1] - self._drag[1])
            self._region = {"type": "rect", "x": x, "y": y, "w": w, "h": h}
            self._redraw()
            ev.accept()
        else:
            super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._mode == "rect" and self._drag is not None:
            self._drag = None
            ev.accept()
        else:
            super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if self._mode == "polygon" and self._poly:
            self._region = {"type": "polygon",
                            "points": [list(p) for p in self._poly]}
            self._poly = []
            self._redraw()
            ev.accept()
        else:
            super().mouseDoubleClickEvent(ev)


class LocalEditDialog(QDialog):
    """图片局部编辑对话框：选目标资产 + 模式 + 指令 + 参数 + 遮罩编辑器。"""

    def __init__(self, node, parent=None):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("局部编辑 · %s" % node.id)
        self.resize(360, 540)
        lay = QVBoxLayout(self)

        hl = QHBoxLayout()
        hl.addWidget(QLabel("作用资产:"))
        self.cmb_target = QComboBox()
        self.cmb_target.addItems(list(node.outputs.keys()))
        hl.addWidget(self.cmb_target)
        hl.addStretch(1)
        lay.addLayout(hl)

        lay.addWidget(QLabel("遮罩（拖拽矩形 / 多点后双击闭合多边形）:"))
        self.mask = MaskEditorWidget(self)
        lay.addWidget(self.mask)

        hl2 = QHBoxLayout()
        hl2.addWidget(QLabel("模式:"))
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["transform", "inpaint"])
        self.cmb_mode.currentTextChanged.connect(
            lambda m: self.mask.set_mode("polygon" if m == "inpaint" else "rect"))
        hl2.addWidget(self.cmb_mode)
        self.btn_rect = QPushButton("矩形")
        self.btn_poly = QPushButton("多边形")
        self.btn_rect.clicked.connect(lambda: self.mask.set_mode("rect"))
        self.btn_poly.clicked.connect(lambda: self.mask.set_mode("polygon"))
        hl2.addWidget(self.btn_rect)
        hl2.addWidget(self.btn_poly)
        hl2.addStretch(1)
        lay.addLayout(hl2)

        lay.addWidget(QLabel("编辑指令:"))
        self.edit_instr = QLineEdit()
        self.edit_instr.setPlaceholderText("例：去掉右下角水印 / 背景换成蓝天 / 换发型")
        lay.addWidget(self.edit_instr)

        lay.addWidget(QLabel("参数(JSON,可选):"))
        self.edit_params = QLineEdit()
        self.edit_params.setPlaceholderText('{"strength":0.5}')
        lay.addWidget(self.edit_params)

        bl = QHBoxLayout()
        self.btn_ok = QPushButton("添加")
        self.btn_cancel = QPushButton("取消")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        bl.addStretch(1)
        bl.addWidget(self.btn_ok)
        bl.addWidget(self.btn_cancel)
        lay.addLayout(bl)
        self._result = None

    def build_edit(self) -> dict:
        mode = self.cmb_mode.currentText()
        region = self.mask.get_region()
        params = {}
        ptxt = self.edit_params.text().strip()
        if ptxt:
            try:
                params = json.loads(ptxt)
            except Exception:
                params = {}
        return {
            "target": self.cmb_target.currentText(),
            "mode": mode,
            "region": region,
            "instruction": self.edit_instr.text().strip(),
            "params": params,
        }


class PropertyPanel(QWidget):
    """右侧属性面板：绑定选中节点的 config，可编辑后写回（带撤销）。

    阶段 B：若选中节点是图片类（生图/场景图等），额外显示「局部编辑」按钮，
    点击经 local_edit_callback 打开遮罩编辑器对话框。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.graph = None
        self.node = None
        self.undo_stack = None
        self.local_edit_callback = None
        lay = QVBoxLayout(self)
        self.lbl = QLabel("（未选中节点）")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        form = QFormLayout()
        self.edit_prompt = QLineEdit()
        form.addRow("提示词(prompt)", self.edit_prompt)
        lay.addLayout(form)
        self.edit_config = QTextEdit()
        self.edit_config.setPlaceholderText("节点 config（JSON）")
        lay.addWidget(self.edit_config)
        self.btn_apply = QPushButton("应用更改")
        self.btn_apply.clicked.connect(self.apply)
        lay.addWidget(self.btn_apply)
        # 阶段 B：局部编辑入口（仅图片类节点可见）
        self.btn_local = QPushButton("局部编辑")
        self.btn_local.clicked.connect(self._on_local)
        lay.addWidget(self.btn_local)
        lay.addStretch(1)

    def bind(self, graph, node, undo_stack):
        self.graph = graph
        self.node = node
        self.undo_stack = undo_stack
        if node is None:
            self.lbl.setText("（未选中节点）")
            self.edit_prompt.clear()
            self.edit_config.clear()
            self.btn_local.setVisible(False)
            return
        self.lbl.setText("节点: %s\n类型: %s\n状态: %s" % (
            node.id, NODE_TYPES.get(node.node_type, node.node_type), node.status))
        self.edit_prompt.setText(str(node.config.get("prompt", "")))
        self.edit_config.setPlainText(
            json.dumps(node.config, ensure_ascii=False, indent=1))
        # 阶段 B：局部编辑按钮按节点类型显隐 + 显示已有编辑数
        le = node.config.get("local_edits")
        le = le if isinstance(le, list) else []
        self.btn_local.setVisible(node_supports_local_edit(node))
        self.btn_local.setText("局部编辑(%d)" % len(le))

    def _on_local(self):
        if self.node is not None and self.local_edit_callback is not None:
            self.local_edit_callback(self.node)

    def apply(self):
        if self.node is None or self.graph is None:
            return
        try:
            new_cfg = json.loads(self.edit_config.toPlainText() or "{}")
        except Exception:
            new_cfg = dict(self.node.config)
        if self.edit_prompt.text().strip():
            new_cfg["prompt"] = self.edit_prompt.text().strip()
        old = dict(self.node.config)
        if new_cfg == old:
            return
        cmd = EditConfigCommand(self.graph, self.undo_stack, self.node.id, old, new_cfg)
        if self.undo_stack is not None:
            self.undo_stack.push(cmd)
        else:
            cmd.redo()


class CanvasPanel(QWidget):
    """独立画布面板（阶段 A：可编辑 + 撤销/重做 + 属性面板 + 端口连线）。"""

    def __init__(self, graph: Optional["cg.CanvasGraph"] = None, parent=None):
        super().__init__(parent)
        self.graph = graph
        self.undo_stack = QUndoStack(self)
        self.scene = CanvasScene(graph, self.undo_stack, self)
        self.view = CanvasView(self)
        self.property_panel = PropertyPanel(self)
        # 阶段 B：属性面板「局部编辑」按钮 → 打开遮罩编辑器
        self.property_panel.local_edit_callback = self._open_local_edit_for

        # 工具栏
        bar = QHBoxLayout()
        btn_fit = QPushButton("适配视图")
        btn_fit.clicked.connect(self._fit)
        btn_undo = QPushButton("撤销")
        btn_undo.clicked.connect(self.undo_stack.undo)
        btn_redo = QPushButton("重做")
        btn_redo.clicked.connect(self.undo_stack.redo)
        btn_del = QPushButton("删除选中连线")
        btn_del.clicked.connect(self._delete_selected)
        btn_local = QPushButton("局部编辑")
        btn_local.clicked.connect(self._open_local_edit)
        bar.addWidget(btn_fit)
        bar.addWidget(btn_undo)
        bar.addWidget(btn_redo)
        bar.addWidget(btn_del)
        bar.addWidget(btn_local)
        bar.addStretch(1)
        legend = QLabel("● 完成  ● 进行  ● 失败  ● 待办 蓝色实线=数据 灰色虚线=顺序")
        bar.addWidget(legend)

        # 详情
        self.detail = QListWidget()
        self.detail.setMaximumHeight(120)

        left = QVBoxLayout()
        left.addLayout(bar)
        left.addWidget(self.view, 1)
        left.addWidget(self.detail)
        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addWidget(self.property_panel, 0)
        self.setLayout(lay)

        # 选中变化 → 刷新属性面板
        self.scene.selectionChanged.connect(self._on_selection_changed)

        if graph is not None:
            self.render_graph(graph)

    def render_graph(self, graph: "cg.CanvasGraph"):
        self.graph = graph
        self.scene.graph = graph
        self.plan = layout_graph(graph)
        self.scene.build(self.plan, graph)
        self.view.setScene(self.scene)
        self._fit()
        self._refresh_detail(None)

    def _on_selection_changed(self):
        items = self.scene.selectedItems()
        node_item = next((it for it in items
                          if isinstance(it, CanvasNodeItem)), None)
        if node_item is not None and node_item.node is not None:
            self.property_panel.bind(self.graph, node_item.node, self.undo_stack)
            self._refresh_detail(node_item.spec.id)
        else:
            self.property_panel.bind(self.graph, None, self.undo_stack)

    def _delete_selected(self):
        for it in self.scene.selectedItems():
            if isinstance(it, CanvasEdgeItem):
                cmd = RemoveEdgeCommand(self.graph, self.undo_stack, *it.edge_tuple)
                if self.undo_stack is not None:
                    self.undo_stack.push(cmd)
                else:
                    cmd.redo()
                break

    # ---- 阶段 B：图片局部编辑入口 ----
    def _selected_node(self):
        for it in self.scene.selectedItems():
            if isinstance(it, CanvasNodeItem) and it.node is not None:
                return it.node
        return None

    def _open_local_edit(self):
        """工具栏「局部编辑」：对当前选中节点打开遮罩编辑器对话框。"""
        node = self._selected_node()
        if node is None or not node_supports_local_edit(node):
            self.detail.addItem("请先选中一个图片类节点（如「生图」）再局部编辑")
            return
        self._open_local_edit_for(node)

    def _open_local_edit_for(self, node):
        """对指定图片类节点打开遮罩编辑器，确认后压入 AddLocalEditCommand。"""
        if self.graph is None or not node_supports_local_edit(node):
            return
        dlg = LocalEditDialog(node, self)
        if dlg.exec() == QDialog.Accepted:
            edit = dlg.build_edit()
            if not edit.get("instruction"):
                return
            cmd = AddLocalEditCommand(self.graph, self.undo_stack, node.id, edit)
            if self.undo_stack is not None:
                self.undo_stack.push(cmd)
            else:
                cmd.redo()
            self._on_selection_changed()      # 刷新属性面板（按钮计数）

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
        le = n.config.get("local_edits")
        le = le if isinstance(le, list) else []
        if le:
            self.detail.addItem("局部编辑: %d 条" % len(le))
            for r in le:
                self.detail.addItem("  · [%s] %s: %s" % (
                    r.get("mode"), r.get("id"), r.get("instruction")))


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
