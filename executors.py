# -*- coding: utf-8 -*-
"""第 5 步执行器：把画布节点从「stub 只登记阶段路径」升级为「真正产出文件并落盘」。

设计原则：
  * 纯本地、无外部网络 / API 依赖 —— 判据可在无头沙箱运行。
  * 可插拔：CanvasGraph.use_real_executors(asset_root, inpaint_fn) 按 node_type
    分配真实执行器；gen_image 走「真实生图 + 局部编辑落盘」主链路，其余类型走
    「落盘占位」passthrough，保持 run 链路完整、资产可登记。
  * inpaint（AI 局部重绘）默认诚实抛 UnsupportedEditMode（与阶段 C 一致），运行时
    由外部注入 inpaint_fn（如 Agnes 图生图 / OpenHuman 重绘）接入真模型。
  * gen_image 产出流程：PIL 本地生成源图 → 落盘 {asset_root}/image/{node}_image.png
    → 若有 local_edits，调 image_local_edit.apply_local_edits 渲染编辑图
    → 落盘 {node}_image_edited.png（inpaint 经 inpaint_fn）→ 把编辑图作为端口产出 path。

复用 image_local_edit（阶段 C 引擎）与 canvas_graph（AssetRef / 类型常量）。
"""

import os

import numpy as np
from PIL import Image, ImageDraw

import image_local_edit as il
from image_local_edit import UnsupportedEditMode
from canvas_graph import AssetRef, NODE_TYPES


# --------------------------------------------------------------------------
# 文件 / 路径辅助
# --------------------------------------------------------------------------
_EXT = {
    "image": ".png", "clip": ".mp4", "video": ".mp4", "final": ".mp4",
    "prompt": ".txt", "script": ".txt", "audio": ".mp3", "data": ".json",
}


def stage_path(asset_root, kind, node_id, port):
    """真实落盘路径：{asset_root}/{kind}/{node_id}_{port}.{ext}（目录自动创建）。"""
    d = os.path.join(asset_root, kind)
    os.makedirs(d, exist_ok=True)
    ext = _EXT.get(kind, ".bin")
    return os.path.join(d, f"{node_id}_{port}{ext}")


def _png_to_array(path):
    img = Image.open(path).convert("RGB")
    return np.asarray(img, dtype=np.uint8)


def _array_to_png(arr, path):
    a = np.asarray(arr, dtype=np.float64)
    if a.size and a.max() > 1.0:
        a = a / 255.0
    img = Image.fromarray((np.clip(a, 0, 1) * 255).astype("uint8"))
    img.save(path)


def _seed_hue(prompt):
    """按 prompt 文本稳定生成一个色相（占位图可复现 + 好看）。"""
    s = (prompt or "").strip()
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xffffffff
    return h % 360


def _make_source_image(node, path, size=(480, 270)):
    """用 PIL 生一张源图（水平渐变 + prompt 文字标注），落盘到 path。"""
    w, h = size
    hue = _seed_hue(node.config.get("prompt") if isinstance(node.config, dict) else None)
    base = Image.new("RGB", (w, h), (30, 30, 40))
    px = base.load()
    for x in range(w):
        t = x / max(1, w - 1)
        # 色相由 prompt 决定，明度做水平渐变，保证左右两侧像素明显不同（利于局部编辑验证）
        r = int(40 + 180 * t) % 256
        g = (hue * 2) % 256
        b = int(60 + 120 * (1 - t)) % 256
        for y in range(h):
            px[x, y] = (r, g, b)
    draw = ImageDraw.Draw(base)
    prompt = (node.config.get("prompt") if isinstance(node.config, dict) else "") or node.id
    try:
        draw.text((10, 10), prompt[:40], fill=(255, 255, 255))
    except Exception:
        pass
    base.save(path)
    return path


# --------------------------------------------------------------------------
# gen_image 真实执行器（含局部编辑落盘）
# --------------------------------------------------------------------------
def gen_image_executor(node, asset_root, inpaint_fn=None):
    """gen_image 节点的真实执行器。返回 (state) -> dict 闭包。

    闭包捕获 node，run 时实时读 node.config["local_edits"]（最新值）。
    """
    out_ports = [p for p, pt in node.outputs.items() if pt.port_type == "image"]

    def _exec(state):
        src_path = stage_path(asset_root, "image", node.id, "source")
        _make_source_image(node, src_path)
        arr = _png_to_array(src_path).astype(np.float64) / 255.0
        edits = (node.config or {}).get("local_edits") or []
        if edits:
            out_arr = il.apply_local_edits(arr, edits, inpaint_fn=inpaint_fn)
            out_path = stage_path(asset_root, "image", node.id, "edited")
            _array_to_png(out_arr, out_path)
        else:
            out_path = src_path
        produced = None
        for p in out_ports:
            ref = AssetRef(
                kind="image",
                name="%s:%s" % (NODE_TYPES.get(node.node_type, node.node_type), node.id),
                path=out_path,
                meta={"tags": [node.node_type, "image"],
                      "project": "canvas_runtime",
                      "task": "节点 %s 生图（第5步执行器）" % node.id},
            )
            node.out_assets[p] = ref
            produced = ref
        return {
            "node": node.id, "type": node.node_type,
            "out_kind": "image", "source": src_path,
            "out_path": out_path, "applied": len(edits),
            "produced": produced.to_dict() if produced else None,
        }
    return _exec


# --------------------------------------------------------------------------
# 非图片节点：落盘占位 passthrough（保持 run 链路完整 + 资产可登记）
# --------------------------------------------------------------------------
def passthrough_executor(node, asset_root):
    """非图片节点的真实落盘占位：写一个小 manifest 文件 + 登记 AssetRef。"""

    def _exec(state):
        outs = []
        for p, pt in node.outputs.items():
            kind = pt.port_type
            if kind not in _EXT:
                kind = "data"
            path = stage_path(asset_root, kind, node.id, p)
            with open(path, "w", encoding="utf-8") as f:
                f.write("# canvas runtime placeholder\nnode=%s\nport=%s\nkind=%s\n"
                        % (node.id, p, kind))
            ref = AssetRef(
                kind=kind,
                name="%s:%s:%s" % (NODE_TYPES.get(node.node_type, node.node_type),
                                    node.id, p),
                path=path,
                meta={"tags": [node.node_type, kind],
                      "project": "canvas_runtime",
                      "task": "节点 %s 产出 %s（第5步 passthrough）" % (node.id, kind)},
            )
            node.out_assets[p] = ref
            outs.append(ref.to_dict())
        return {"node": node.id, "type": node.node_type, "outputs": outs}
    return _exec


# --------------------------------------------------------------------------
# 注册表 + 装配
# --------------------------------------------------------------------------
# node_type -> factory(node, asset_root, inpaint_fn=None)
DEFAULT_EXECUTORS = {
    "gen_image": lambda node, asset_root, inpaint_fn=None:
        gen_image_executor(node, asset_root, inpaint_fn),
}


def build_executor(node, asset_root, inpaint_fn=None):
    """按 node_type 选真实执行器工厂；未知类型回落 passthrough。"""
    factory = DEFAULT_EXECUTORS.get(node.node_type)
    if factory is not None:
        return factory(node, asset_root, inpaint_fn)
    return passthrough_executor(node, asset_root)


def apply_real_executors(graph, asset_root, inpaint_fn=None):
    """给整张图的所有节点装上真实执行器（run 前调用）。返回被替换的节点 id 列表。"""
    replaced = []
    for nid, node in graph.nodes.items():
        node.executor = build_executor(node, asset_root, inpaint_fn)
        replaced.append(nid)
    return replaced
