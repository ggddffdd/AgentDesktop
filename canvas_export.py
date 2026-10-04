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
  * export_png(g, path)           —— 基于 Qt 视图快照（GUI 渲染）。Qt 是 canvas_panel → ui 间接引入的，
                                      本模块自身不新增 PySide6 依赖；快照调用留在函数内（延迟执行）。

复用：layout_graph（canvas_panel）做几何；AssetRef 引用原样写入（画布只持引用，不建文件）。
导出是「可编辑设计画布」的前置：能存盘 / 读回 = 有编辑闭环。图片局部编辑（第 4 步
延伸目标，见 CANVAS_RESEARCH_REPORT.md）会把「遮罩 + 编辑指令」存进节点 config / 资产操作，
本步的 export/import 已为它预留了 config 字段的持久化通道。
"""

import json
import os
from typing import Optional

import canvas_graph as cg
# 状态枚举的唯一事实源在 task_graph（画布节点状态沿用 Task.status）——
# 导入校验要用它判「status 是否合法」，不许在这里另抄一份白名单。
import task_graph as tg
# 几何复用第 3 步渲染层（layout_graph 为纯逻辑、无 Qt 依赖）
from canvas_panel import layout_graph, region_svg_overlay
# 颜色单一事实源：SVG 里的 fill/stroke 同样是 UI 呈现，不该写裸 hex
# （ui_hex_guard 构建期会拦；canvas_panel 也早已 from ui import THEME，不新增依赖层次）
from ui import THEME


# --------------------------------------------------------------------------
# 工程文件：导出 / 导入（可编辑画布的存盘与读回）
# --------------------------------------------------------------------------
class CanvasImportError(ValueError):
    """工程文件导入失败（JSON 非法 / 结构不对 / 端口非法 / 边端点缺失）。

    单独一个类型，是为了让调用方能把「用户给的工程文件不对」与「我们自己代码有
    bug」分开处理：前者该弹提示让用户改文件，后者该上报崩溃。都混成 ValueError
    的话，UI 只能一律说「导入失败，请重试」—— 用户重试一百次也没用。
    """


def _split_endpoint(e, key: str, i: int, where: str):
    """把一条边的 from/to 拆成 (节点, 端口)。

    data_edges 写成 "节点.端口"；order_edges 只写节点 id（不传资产），端口留空。
    旧实现直接 `e["from"].split(".", 1)` 解包 —— 少一个点就抛
    「not enough values to unpack」，连是哪条边都不说。
    """
    if not isinstance(e, dict):
        raise CanvasImportError(f"{where}: {key}[{i}] 必须是对象")
    out = []
    for side in ("from", "to"):
        raw = e.get(side)
        if not isinstance(raw, str) or not raw.strip():
            raise CanvasImportError(
                f"{where}: {key}[{i}].{side} 必须是非空字符串，收到 {raw!r}")
        raw = raw.strip()
        if key == "data_edges":
            node, sep, port = raw.partition(".")
            if not sep or not node or not port:
                raise CanvasImportError(
                    f"{where}: {key}[{i}].{side}={raw!r} 必须形如「节点.端口」")
            out.append((node, port))
        else:
            out.append((raw, ""))
    return out[0], out[1]


def _require_nodes(nodes: dict, from_id: str, to_id: str, where: str,
                   key: str, i: int) -> None:
    for nid in (from_id, to_id):
        if nid not in nodes:
            raise CanvasImportError(
                f"{where}: {key}[{i}] 引用了不存在的节点 {nid!r}"
                f"（文件里的节点: {sorted(nodes)[:8]}）")


# 本读写器支持的工程文件版本。**缺 version 字段按 v1 处理** —— v1 与 v2 的节点/边
# 结构相同，差别只在端口写法（v1 裸字符串、v2 {"type","multi"}），而端口解析本来就
# 两种都认。**未知版本必须报错**：未来版本若新增字段，本版会静默丢掉它，用户看到的
# 是「文件能打开、内容却少了一截」—— 这比干脆打不开更糟。
SUPPORTED_VERSIONS = (1, 2)


def _validate_local_edits(le, where: str, nid: str) -> None:
    """校验 config.local_edits 的结构（list[dict]，字段与 image_local_edit 对齐）。

    只做**结构**校验（类型 / 取值域），不做语义完备性校验 —— 例如 inpaint 条目缺
    instruction 要到执行期才知道有没有意义，导入期替它下结论会误伤手工编写的工程文件。
    """
    if le is None:
        return
    if not isinstance(le, list):
        raise CanvasImportError(
            f"{where}: 节点 {nid}.config.local_edits 必须是数组，"
            f"收到 {type(le).__name__}")
    for i, e in enumerate(le):
        p = f"{where}: 节点 {nid}.config.local_edits[{i}]"
        if not isinstance(e, dict):
            raise CanvasImportError(f"{p} 必须是对象，收到 {type(e).__name__}")
        mode = e.get("mode", "transform")
        if mode not in ("transform", "inpaint"):
            raise CanvasImportError(
                f"{p}.mode={mode!r} 非法（可选: transform / inpaint）")
        for k, want in (("params", dict), ("instruction", str), ("region", dict)):
            if k in e and e[k] is not None and not isinstance(e[k], want):
                raise CanvasImportError(
                    f"{p}.{k} 必须是 {want.__name__} 或 null，"
                    f"收到 {type(e[k]).__name__}")


def _validate_data_edges(nodes: dict, gd, where: str) -> None:
    """数据边的两项结构校验：**不许重复** + **单入端口不许被多条边连**。

    这两件在 `connect_data` 里本来也会拒，但那是运行时、且已经建了一半图。导入是
    「要么整份能用、要么明确报错」的场合，必须在建图前就指出是第几条边出的问题。
    """
    edges = gd.get("data_edges", []) or []
    seen = set()
    single_in_owner = {}
    for i, e in enumerate(edges):
        frm, to = _split_endpoint(e, "data_edges", i, where)
        key = (frm[0], frm[1], to[0], to[1])
        if key in seen:
            raise CanvasImportError(
                f"{where}: data_edges[{i}] 与前面某条边完全重复："
                f"{frm[0]}.{frm[1]} -> {to[0]}.{to[1]}")
        seen.add(key)
        spec = ((nodes.get(to[0]) or {}).get("inputs", {}) or {}).get(to[1])
        # v2 端口是 {"type","multi"}；v1 是裸字符串（没有 multi 概念 → 一律按单入）
        multi = spec.get("multi", False) if isinstance(spec, dict) else False
        if multi:
            continue
        prev = single_in_owner.get(to)
        if prev is not None:
            raise CanvasImportError(
                f"{where}: 端口 {to[0]}.{to[1]} 是单入端口，却被 data_edges[{prev}] "
                f"与 data_edges[{i}] 连了两条边"
                f'（确需多入请把该端口写成 {{"type": ..., "multi": true}}）')
        single_in_owner[to] = i


def _validate_project(gd, path: str, version=None) -> None:
    """导入前的结构预检：错误必须能定位到「哪个文件的哪个节点/哪条边」。

    为什么先校验再建图：`CanvasNode` / `Port` / `connect_data` 的异常是「就事论事」
    的（例如「非法端口类型: 'imgae'」），但它说不出是**哪个文件、哪个节点**的哪个
    端口。工程文件是给人改的，报错不指路等于没报。
    """
    where = os.path.basename(path or "工程文件")
    # ① 版本：只放行明确支持的版本。缺字段按 v1（历史文件本来就没写 version）。
    if version is not None:
        if isinstance(version, bool) or not isinstance(version, int):
            raise CanvasImportError(
                f"{where}: version 必须是整数，收到 {version!r}")
        if version not in SUPPORTED_VERSIONS:
            raise CanvasImportError(
                f"{where}: 工程文件 version={version} 不受支持"
                f"（本版支持 {sorted(SUPPORTED_VERSIONS)}）—— "
                f"请改用更新版本的程序打开，不要用旧版读取后另存")
    if not isinstance(gd, dict):
        raise CanvasImportError(
            f"{where}: 顶层必须是对象，收到 {type(gd).__name__}")
    nodes = gd.get("nodes", {})
    if not isinstance(nodes, dict):
        raise CanvasImportError(f"{where}: nodes 必须是「节点id -> 节点定义」的对象")
    for nid, nd in nodes.items():
        if not isinstance(nid, str) or not nid.strip():
            raise CanvasImportError(f"{where}: 节点 id 必须是非空字符串，收到 {nid!r}")
        if not isinstance(nd, dict):
            raise CanvasImportError(f"{where}: 节点 {nid} 的定义必须是对象")
        ntype = nd.get("type")
        if ntype not in cg.NODE_TYPES:
            raise CanvasImportError(
                f"{where}: 节点 {nid} 的 type={ntype!r} 不是合法节点类型"
                f"（可选: {sorted(cg.NODE_TYPES)}）")
        for side in ("inputs", "outputs"):
            ports = nd.get(side, {})
            if not isinstance(ports, dict):
                raise CanvasImportError(f"{where}: 节点 {nid}.{side} 必须是对象")
            for pname, spec in ports.items():
                # v2 dict {"type","multi"}；v1 裸字符串。两种都只取 type 做校验。
                ptype = spec.get("type") if isinstance(spec, dict) else spec
                if ptype not in cg.VALID_PORT_TYPES:
                    raise CanvasImportError(
                        f"{where}: 节点 {nid}.{side} 端口 {pname!r} 的类型 "
                        f"{ptype!r} 非法（可选: {sorted(cg.VALID_PORT_TYPES)}）")
                if isinstance(spec, dict) and "multi" in spec \
                        and not isinstance(spec["multi"], bool):
                    raise CanvasImportError(
                        f"{where}: 节点 {nid}.{side} 端口 {pname!r} 的 multi "
                        f"必须是布尔，收到 {spec['multi']!r}")
        # ② status：只认 task_graph.VALID_STATUSES。拼错的状态不会报错、只会静默
        #    落到「未知颜色」，画布上看不出异常但状态语义已经丢了。
        st = nd.get("status")
        if st is not None and st not in tg.VALID_STATUSES:
            raise CanvasImportError(
                f"{where}: 节点 {nid}.status={st!r} 不是合法状态"
                f"（可选: {list(tg.VALID_STATUSES)}）")
        # ③ placeholder：必须是布尔 —— 读回时 bool("false") == True，会静默反转语义
        ph = nd.get("placeholder")
        if ph is not None and not isinstance(ph, bool):
            raise CanvasImportError(
                f"{where}: 节点 {nid}.placeholder 必须是布尔，收到 {ph!r}")
        if "config" in nd and not isinstance(nd["config"], dict):
            raise CanvasImportError(f"{where}: 节点 {nid}.config 必须是对象")
        # ④ config.local_edits 结构（局部编辑记录的字段类型）
        if isinstance(nd.get("config"), dict) and "local_edits" in nd["config"]:
            _validate_local_edits(nd["config"]["local_edits"], where, nid)
        pos = nd.get("pos")
        if pos is not None and (not isinstance(pos, (list, tuple)) or len(pos) != 2):
            raise CanvasImportError(
                f"{where}: 节点 {nid}.pos 必须是 [x, y] 或 null，收到 {pos!r}")
        # ⑤ pos 两个值都必须是数字：字符串 "10" 会在布局层被当数字参与运算，
        #    结果是「能打开、但节点位置莫名其妙」。
        if pos is not None:
            for i, v in enumerate(pos):
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise CanvasImportError(
                        f"{where}: 节点 {nid}.pos[{i}] 必须是数字，收到 {v!r}")
    for key in ("data_edges", "order_edges"):
        if not isinstance(gd.get(key, []), list):
            raise CanvasImportError(f"{where}: {key} 必须是数组")
    for i, e in enumerate(gd.get("data_edges", []) or []):
        frm, to = _split_endpoint(e, "data_edges", i, where)
        _require_nodes(nodes, frm[0], to[0], where, "data_edges", i)
    for i, e in enumerate(gd.get("order_edges", []) or []):
        frm, to = _split_endpoint(e, "order_edges", i, where)
        _require_nodes(nodes, frm[0], to[0], where, "order_edges", i)
    # ⑥⑦ 数据边重复 + 单入端口被多条边连（要等节点都确认存在之后才判）
    _validate_data_edges(nodes, gd, where)


def export_project_json(g: "cg.CanvasGraph", path: str) -> dict:
    """把可编辑画布状态序列化为工程 JSON（含节点位置/参数/连线）。

    **产物引用（`out_assets`）有意不写进工程文件**：它是运行期状态，跨会话、换机器
    后路径基本失效，存进去只会让下次打开时看到一片指向不存在文件的「假产物」。
    工程文件只承载「画布结构 + 编辑意图」。这**不是兼容缺口**，别去补 ——
    2026-10-04 已核实导出（`CanvasNode.to_dict` 无此字段）与导入两侧都不碰 out_assets。

    返回写入的 dict（便于判据断言）。父目录不存在时自动创建。

    version=2：端口写成 {"type": ..., "multi": ...}。v1 只写端口类型字符串，
    `multi` 在存盘时就被丢掉 —— 多入端口读回来会退化成单入（运行时才被单入校验
    拦住，画布上看不出任何差别）。导入侧对 v1 保持兼容。
    """
    data = {
        "version": 2,
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

    节点重建时恢复 id / 类型 / 端口(含 multi) / config / 位置 / status / placeholder；
    连线（数据边 + 顺序边）原样重建，由 `connect_data` 再走一遍端口存在 + 类型兼容
    + 不成环校验。
    **不恢复 `out_assets`**（与导出侧对称）：产物引用是运行期状态、不进工程文件，
    所以「节点上的产物路径」在存盘-读回后一律为空，需要重跑才有产出。
    执行器用默认 stub（第 5 步接真执行器时再替换为真实实现）。

    失败一律抛 CanvasImportError（带文件名 + 具体节点/边定位），不返回半成品图。
    """
    where = os.path.basename(path or "工程文件")
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise CanvasImportError(f"{where}: 不是合法 JSON（{e}）")
    # 版本在外层包装里（不在 graph 内层），必须在这里取出并交给校验：未知版本若被
    # 静默按旧结构读取，它新增的字段会在导入时无声消失（"能打开、内容少一截"）。
    version = data.get("version") if isinstance(data, dict) else None
    gd = data.get("graph", data) if isinstance(data, dict) else data
    _validate_project(gd, path, version)

    g = cg.CanvasGraph()
    for nid, nd in gd.get("nodes", {}).items():
        try:
            node = cg.CanvasNode(
                nid, nd["type"],
                inputs={k: cg.port_from_spec(k, v)
                        for k, v in nd.get("inputs", {}).items()},
                outputs={k: cg.port_from_spec(k, v)
                         for k, v in nd.get("outputs", {}).items()},
                config=nd.get("config", {}),
                pos=nd.get("pos"),
            )
        except (ValueError, KeyError, TypeError) as e:
            # 预检已过，这里再炸说明字段组合有问题（如缺 type）—— 照样带定位抛出
            raise CanvasImportError(f"{where}: 节点 {nid} 无法重建：{e}")
        node.status = nd.get("status", "pending")
        node.placeholder = bool(nd.get("placeholder", False))
        g.add_node(node)

    for i, e in enumerate(gd.get("data_edges", []) or []):
        (node_a, port_a), (node_b, port_b) = _split_endpoint(
            e, "data_edges", i, where)
        try:
            g.connect_data(node_a, port_a, node_b, port_b, label=e.get("label", ""))
        except ValueError as ex:
            raise CanvasImportError(
                f"{where}: data_edges[{i}] {e.get('from')} -> {e.get('to')} "
                f"连不上：{ex}")
    for i, e in enumerate(gd.get("order_edges", []) or []):
        try:
            g.connect_order(e["from"], e["to"], reason=e.get("reason", ""))
        except ValueError as ex:
            raise CanvasImportError(f"{where}: order_edges[{i}] 连不上：{ex}")
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
            color, dash = THEME["accent"], ""
        else:
            x0 = frm.x + frm.w
            y0 = frm.y + frm.h / 2
            x1 = to.x
            y1 = to.y + to.h / 2
            d = f"M {x0:.1f} {y0:.1f} L {x1:.1f} {y1:.1f}"
            color, dash = THEME["canvas_pending"], ' stroke-dasharray="6 4"'
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
            f'fill="{THEME["canvas_status_text"]}">{_esc(n.status)}</text>')
        if n.placeholder:
            # 占位产出：空心橙圈（THEME canvas_incomplete），与「真出片」的实心绿点区分开。
            # 占位物同样 registered，画实心绿点会被读成真出片。
            parts.append(
                f'<circle cx="{n.x + n.w - 12:.1f}" cy="{n.y + n.h - 12:.1f}" '
                f'r="4" fill="none" stroke="{THEME["canvas_incomplete"]}" stroke-width="2"/>')
            parts.append(
                f'<text x="{n.x + 8:.1f}" y="{n.y + n.h - 22:.1f}" font-size="9" '
                f'fill="{THEME["canvas_incomplete"]}">占位</text>')
        elif n.registered:
            parts.append(
                f'<circle cx="{n.x + n.w - 12:.1f}" cy="{n.y + n.h - 12:.1f}" '
                f'r="4" fill="{THEME["canvas_completed"]}"/>')
        # 阶段 B：标记已局部编辑的节点（红色虚线遮罩 + 角标）
        raw = g.nodes.get(n.id)
        le = raw.config.get("local_edits") if raw is not None else None
        le = le if isinstance(le, list) else []
        if le:
            parts.append(region_svg_overlay(le[0].get("region"), n.x, n.y, n.w, n.h))
            parts.append(
                f'<text x="{n.x + 8:.1f}" y="{n.y + n.h - 6:.1f}" font-size="9" '
                f'fill="{THEME["canvas_failed"]}">局部编辑 {len(le)}</text>')
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


# --------------------------------------------------------------------------
# 阶段 C：图片局部编辑「结果图」导出（依赖 image_local_edit 引擎 + PIL）
# --------------------------------------------------------------------------
import numpy as np  # noqa: E402
import image_local_edit as il  # noqa: E402
from canvas_panel import node_supports_local_edit  # noqa: E402
try:
    from PIL import Image  # noqa: E402
    _HAS_PIL = True
except Exception:  # pragma: no cover - PIL 缺失仅降级文件 IO
    Image = None
    _HAS_PIL = False


def _load_arr(path: str) -> np.ndarray:
    """读图像文件为 RGB numpy 数组（uint8）。"""
    if not _HAS_PIL:
        raise RuntimeError("未安装 Pillow，无法读写图像文件")
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)


def _save_arr(arr: np.ndarray, path: str) -> None:
    """把 float 0..1 / uint8 0..255 的 RGB 数组写成 PNG。"""
    if not _HAS_PIL:
        raise RuntimeError("未安装 Pillow，无法读写图像文件")
    a = np.asarray(arr, dtype=np.float64)
    if a.max() > 1.0:
        a = a / 255.0
    a = (np.clip(a, 0, 1) * 255.0).round().astype("uint8")
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    Image.fromarray(a).save(path)


def export_node_local_edit_image(g: "cg.CanvasGraph", node_id: str,
                                 src_path: str, out_path: str,
                                 inpaint_fn=None) -> dict:
    """把某图片类节点上记录的 local_edits 应用到 src_path 原图，写出结果图。

    返回摘要 dict：{node_id, applied(编辑条数), out_path, size[H,W], skipped}。
    节点无 local_edits 或 src 不存在时跳过（skipped=True）。
    需要 Pillow；inpaint 模式需注入 inpaint_fn（外部模型），否则诚实抛错。
    """
    n = g.nodes.get(node_id)
    if n is None:
        raise ValueError(f"节点不存在: {node_id}")
    edits = g.get_local_edits(node_id)
    if not edits:
        return {"node_id": node_id, "applied": 0, "skipped": True,
                "out_path": out_path}
    if not (src_path and os.path.isfile(src_path)):
        return {"node_id": node_id, "applied": 0, "skipped": True,
                "out_path": out_path, "error": "source image not found"}
    arr = _load_arr(src_path)
    res = il.apply_local_edits(arr, edits, inpaint_fn=inpaint_fn)
    _save_arr(res, out_path)
    return {"node_id": node_id, "applied": len(edits),
            "out_path": out_path, "size": list(res.shape[:2]), "skipped": False}


def export_all_local_edit_images(g: "cg.CanvasGraph", base_dir: str,
                                 src_resolver=None, inpaint_fn=None) -> list:
    """批量导出所有「图片类 + 含 local_edits」节点的结果图。

    src_resolver(node) -> 原图路径（默认取 node.config.get("image_path")）。
    每个节点导出为 base_dir/{node_id}_edited.png。返回各节点摘要 list。
    """
    if src_resolver is None:
        def src_resolver(node):
            return (node.config or {}).get("image_path")
    os.makedirs(base_dir, exist_ok=True)
    results = []
    for nid, n in g.nodes.items():
        if not node_supports_local_edit(n):
            continue
        if not g.get_local_edits(nid):
            continue
        src = src_resolver(n)
        if not src or not os.path.isfile(src):
            results.append({"node_id": nid, "applied": 0, "skipped": True,
                            "out_path": None, "error": "source image not found"})
            continue
        out = os.path.join(base_dir, "%s_edited.png" % nid)
        info = export_node_local_edit_image(
            g, nid, src, out, inpaint_fn=inpaint_fn)
        results.append(info)
    return results
