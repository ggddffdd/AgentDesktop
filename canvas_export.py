# -*- coding: utf-8 -*-
"""节点画布第 4 步 · 渠道层（仅「导出」/ 不「发布」）。

设计稿 §9 第 4 步原定「渠道层（导出 / 发布抖音·视频号）」。经与用户确认：
**只做导出、不做发布**——发布链路（平台鉴权 / 上传 / 平台 API）留给后续，
本步只把画布状态落盘 + 渲染成图。

导出 = 把「可编辑的设计画布」当前状态持久化 / 转成可交付物：
  * export_project_json(g, path)  —— 工程文件（节点 id/类型/位置/参数/资产引用 + 连线）
                                      可 import_project_json 重新载入继续编辑，
                                      这是「可编辑设计画布」闭环的前提（能存盘/读回）。
  * import_project_json(path)     —— 反序列化回 CanvasGraph（结构 + 默认 stub 执行器，
                                      可重渲染 / 再编辑 / 再导出）。
  * export_svg(g, path)           —— 把 layout_graph 几何渲染成 SVG（纯逻辑、无 Qt 依赖，可判据）。
  * export_png(g, path)           —— 基于 Qt 视图快照（GUI 渲染；Qt 懒加载，import 本模块不触发 PySide6）。

复用：layout_graph（canvas_panel）做几何；AssetRef 引用原样写入（画布只持引用，不建文件）。
导出是「可编辑设计画布」的前置：能存盘 / 读回 = 有编辑闭环。图片局部编辑（第 4 步
延伸目标，见 CANVAS_RESEARCH_REPORT.md）会把「遮罩 + 编辑指令」存进节点 config / 资产操作，
本步的 export/import 已为它预留了 config 字段的持久化通道。
"""

import json
import os
from typing import Optional

import canvas_graph as cg
# 几何复用第 3 步渲染层（layout_graph 为纯逻辑、无 Qt 依赖）
from canvas_panel import layout_graph


# --------------------------------------------------------------------------
# 工程文件：导出 / 导入（可编辑画布的存盘与读回）
# --------------------------------------------------------------------------
def export_project_json(g: "cg.CanvasGraph", path: str) -> dict:
    """把可编辑画布状态序列化为工程 JSON（含节点位置/参数/资产引用 + 连线）。

    返回写入的 dict（便于判据断言）。父目录不存在时自动创建。
    """
    data = {
        "version": 1,
        "generator": "canvas_export",
        "graph": g.to_dict(),   # 已含 nodes(类型/位置/参数/状态/产出) + 连线
    }
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


def import_project_json(path: str) -> "cg.CanvasGraph":
    """从工程 JSON 反序列化回 CanvasGraph（结构 + 默认 stub 执行器，可重渲染/再编辑）。

    节点重建时恢复 id / 类型 / 端口 / config / 位置；连线（数据边 + 顺序边）原样重建。
    执行器用默认 stub（第 5 步接真执行器时再替换为真实实现）。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    gd = data.get("graph", data)

    g = cg.CanvasGraph()
    for nid, nd in gd.get("nodes", {}).items():
        node = cg.CanvasNode(
            nid, nd["type"],
            inputs={k: cg.Port(k, v) for k, v in nd.get("inputs", {}).items()},
            outputs={k: cg.Port(k, v) for k, v in nd.get("outputs", {}).items()},
            config=nd.get("config", {}),
            pos=nd.get("pos"),
        )
        node.status = nd.get("status", "pending")
        g.add_node(node)

    for e in gd.get("data_edges", []):
        frm, fp = e["from"].split(".", 1)
        to, tp = e["to"].split(".", 1)
        g.connect_data(frm, fp, to, tp, label=e.get("label", ""))
    for e in gd.get("order_edges", []):
        g.connect_order(e["from"], e["to"], reason=e.get("reason", ""))
    return g


# --------------------------------------------------------------------------
# SVG 渲染（纯逻辑，无 Qt 依赖）：把 layout_graph 几何转成矢量图
# --------------------------------------------------------------------------
def _port_y(spec, port_name: str, side: str) -> float:
    ports = spec.out_ports if side == "R" else spec.in_ports
    idx = ports.index(port_name) if port_name in ports else 0
    count = len(ports) or 1
    return spec.y + (idx + 1) / (count + 1) * spec.h


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def export_svg(g: "cg.CanvasGraph", path: str) -> dict:
    """把画布渲染成 SVG 矢量图（纯逻辑，无 Qt）。返回 layout_graph 的 ScenePlan。

    节点 = 圆角矩形 + 标题 + 状态 + 资产落地绿点；数据边 = 蓝色贝塞尔，
    顺序边 = 灰色虚线。几何完全来自 layout_graph（同样尊重手动 pos）。
    """
    plan = layout_graph(g)
    node_map = {n.id: n for n in plan.nodes}

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{plan.width:.0f}" '
        f'height="{plan.height:.0f}" viewBox="0 0 {plan.width:.0f} {plan.height:.0f}">'
    ]
    # 边（先画，置于节点之下）
    for e in plan.edges:
        frm = node_map.get(e.from_id)
        to = node_map.get(e.to_id)
        if not frm or not to:
            continue
        if e.kind == "data":
            x0 = frm.x + frm.w
            y0 = _port_y(frm, e.from_port, "R")
            x1 = to.x
            y1 = _port_y(to, e.to_port, "L")
            cx = (x0 + x1) / 2
            d = (f"M {x0:.1f} {y0:.1f} C {cx:.1f} {y0:.1f} "
                 f"{cx:.1f} {y1:.1f} {x1:.1f} {y1:.1f}")
            color, dash = "#1A73E8", ""
        else:
            x0 = frm.x + frm.w
            y0 = frm.y + frm.h / 2
            x1 = to.x
            y1 = to.y + to.h / 2
            d = f"M {x0:.1f} {y0:.1f} L {x1:.1f} {y1:.1f}"
            color, dash = "#9AA4B2", ' stroke-dasharray="6 4"'
        parts.append(
            f'<path d="{d}" stroke="{color}" stroke-width="2" fill="none"{dash}/>')
    # 节点
    for n in plan.nodes:
        parts.append(
            f'<rect x="{n.x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" height="{n.h:.1f}" '
            f'rx="6" fill="{n.fill}" stroke="{n.border}" stroke-width="2"/>')
        parts.append(
            f'<text x="{n.x + 8:.1f}" y="{n.y + 22:.1f}" font-size="13" '
            f'font-family="Microsoft YaHei, sans-serif" font-weight="bold">'
            f'{_esc(n.label)}</text>')
        parts.append(
            f'<text x="{n.x + 8:.1f}" y="{n.y + 40:.1f}" font-size="11" '
            f'fill="#666666">{_esc(n.status)}</text>')
        if n.registered:
            parts.append(
                f'<circle cx="{n.x + n.w - 12:.1f}" cy="{n.y + n.h - 12:.1f}" '
                f'r="4" fill="#1E8E3E"/>')
    parts.append("</svg>")

    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return plan


# --------------------------------------------------------------------------
# PNG 快照（GUI 渲染；Qt 懒加载，import 本模块不触发 PySide6）
# --------------------------------------------------------------------------
def export_png(g: "cg.CanvasGraph", path: str) -> bool:
    """用 Qt 视图把画布抓成 PNG（第 0 步坑③：按 DPR 还原清晰度）。

    需要 QApplication 事件循环，通常在 GUI 上下文调用；判据套件不覆盖此路径
    （Qt 部件实例化在本沙箱被拦，属环境限制非代码缺陷）。
    """
    from PySide6.QtWidgets import QApplication
    from canvas_panel import CanvasPanel

    app = QApplication.instance() or QApplication([])
    panel = CanvasPanel(g)
    panel.render_graph(g)
    ok = panel.view.snapshot(path)
    return ok
