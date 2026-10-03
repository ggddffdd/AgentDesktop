# -*- coding: utf-8 -*-
"""阶段 C · 图片局部编辑「结果图」引擎（纯逻辑，无 Qt 依赖）。

把阶段 B 写入节点 config["local_edits"] 的「遮罩 + 指令」真正应用到像素上，
产出可被导出 / 落盘的结果图。设计原则：

  * 核心函数只吃 numpy 数组（H,W,3 的 RGB，uint8 0..255 或 float 0..1 都收，
    内部统一归一化到 float 0..1 处理，返回 0..1 float），不碰文件、不碰 GUI，
    因此判据套件可在本沙箱无头运行。
  * 文件 ↔ 数组的适配（load/save PNG）放在 canvas_export 的集成层，
    本模块只负责「数组 → 数组」的像素变换。
  * region（归一化 0..1）经 region_to_mask 转成像素掩码；transform 类操作
    只在掩码内生效，掩码外像素原样保留（局部编辑的「局部」语义）。
  * inpaint（AI 局部重绘）不在本模块实现（需要外部模型），
    通过 inpaint_fn 回调注入；未注入时诚实抛 UnsupportedEditMode，不伪造结果。

操作集 VALID_TRANSFORM_OPS：
  brightness  亮度   params.factor   (>1 变亮 / <1 变暗)
  contrast    对比度 params.factor   (>1 更强)
  saturation  饱和度 params.factor   (>1 更艳)
  grayscale   灰度   无参数
  invert      反相   无参数
  blur        模糊    params.sigma   (像素半径)
  sharpen     锐化    params.amount  (强度)
"""

import numpy as np

# 支持的非 AI 区域变换操作
VALID_TRANSFORM_OPS = (
    "brightness", "contrast", "saturation",
    "grayscale", "invert", "blur", "sharpen",
)


class UnsupportedEditMode(Exception):
    """inpaint 等需要外部模型的操作在未注入回调时抛出（诚实占位，不伪造结果）。"""
    pass


# --------------------------------------------------------------------------
# region → 像素掩码
# --------------------------------------------------------------------------
def region_to_mask(shape, region):
    """把归一化 region 转成 (H, W) 的 bool 掩码（True = 区域内）。

    shape: 数组 shape (H, W) 或 (H, W, C)。
    region:
      None             → 全图（True）
      {"type":"rect", "x","y","w","h"}（均 0..1）
      {"type":"polygon", "points":[[x,y],...]}（归一化，>=3 点）
      其它/未知        → 视为全图（安全默认）
    """
    H = shape[0]
    W = shape[1] if shape[1] is not None else 0
    try:
        W = int(shape[1])
        H = int(shape[0])
    except Exception:
        return np.ones((H, W), dtype=bool)

    if not region:
        return np.ones((H, W), dtype=bool)

    if region.get("type") == "rect":
        x0 = int(round(region["x"] * W))
        y0 = int(round(region["y"] * H))
        x1 = int(round((region["x"] + region["w"]) * W))
        y1 = int(round((region["y"] + region["h"]) * H))
        x0 = max(0, min(W, x0)); x1 = max(0, min(W, x1))
        y0 = max(0, min(H, y0)); y1 = max(0, min(H, y1))
        m = np.zeros((H, W), dtype=bool)
        if y1 > y0 and x1 > x0:
            m[y0:y1, x0:x1] = True
        return m

    if region.get("type") == "polygon":
        return _polygon_mask(H, W, region.get("points") or [])

    # 未知 region 视作整图，避免「不编辑」被误当成「丢数据」
    return np.ones((H, W), dtype=bool)


def _polygon_mask(H, W, points):
    """用 PIL 在 L 通道上画白色多边形，再转 bool 掩码（无头环境可用）。"""
    from PIL import Image, ImageDraw
    mask = Image.new("L", (W, H), 0)
    pts = [(float(px) * W, float(py) * H) for px, py in points]
    if len(pts) >= 3:
        ImageDraw.Draw(mask).polygon([(round(x), round(y)) for x, y in pts],
                                     fill=255)
    return np.array(mask) > 0


# --------------------------------------------------------------------------
# 单条 transform 操作（arr 约定为 float 0..1，HWC）
# --------------------------------------------------------------------------
def _pil_filter(arr, op, params):
    """blur / sharpen 走 PIL 滤波（区域混合在 apply_local_edits 内完成）。"""
    from PIL import Image, ImageFilter
    img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype("uint8"))
    if op == "blur":
        sigma = float(params.get("sigma", 2.0))
        pil_op = ImageFilter.GaussianBlur(radius=max(0.0, sigma))
    else:  # sharpen
        amount = float(params.get("amount", 1.5))
        pil_op = ImageFilter.UnsharpMask(
            radius=2, percent=max(0, int(amount * 100)), threshold=0)
    out = img.filter(pil_op)
    return np.array(out).astype(np.float64) / 255.0


def apply_op(arr, op, params):
    """对整张 arr 应用一条 transform 操作（float 0..1）。返回新数组。

    op 不在 VALID_TRANSFORM_OPS 时抛 ValueError（调用方据此提示用户）。
    """
    op = (op or "").lower()
    p = params or {}
    if op == "brightness":
        f = float(p.get("factor", 1.2))
        return np.clip(arr * f, 0, 1)
    if op == "contrast":
        f = float(p.get("factor", 1.2))
        return np.clip((arr - 0.5) * f + 0.5, 0, 1)
    if op == "saturation":
        f = float(p.get("factor", 1.3))
        gray = arr.mean(axis=2, keepdims=True)
        return np.clip(arr + (arr - gray) * (f - 1), 0, 1)
    if op == "grayscale":
        gray = arr.mean(axis=2, keepdims=True)
        return np.repeat(gray, 3, axis=2)
    if op == "invert":
        return 1.0 - arr
    if op in ("blur", "sharpen"):
        return _pil_filter(arr, op, p)
    raise ValueError("未知 transform 操作: %r（支持 %s）" % (op, VALID_TRANSFORM_OPS))


# --------------------------------------------------------------------------
# 批量应用（阶段 C 主入口，数组 → 数组）
# --------------------------------------------------------------------------
def apply_local_edits(arr, edits, inpaint_fn=None):
    """把 edits（node.config["local_edits"] 同构列表）逐条应用到 arr 上。

    arr: HWC uint8(0..255) 或 float(0..1)；返回 float 0..1（不改入参）。

    transform → 取 params["op"] 指定的操作，仅在 region 掩码内生效；
                params 无 op 时跳过该条（诚实：不凭空猜测要做什么）。
    inpaint   → 调 inpaint_fn(arr, region, instruction, params)；
                未注入 inpaint_fn 时抛 UnsupportedEditMode。

    多条编辑按列表顺序叠加（先变亮再灰度 = 灰阶），符合「步骤式局部编辑」直觉。
    """
    a = np.asarray(arr, dtype=np.float64)
    if a.size == 0:
        return a
    if a.max() > 1.0:          # 视作 0..255
        a = a / 255.0
    out = a.copy()

    for e in edits or []:
        if not isinstance(e, dict):
            continue
        mode = e.get("mode", "transform")
        region = e.get("region")
        if mode == "inpaint":
            if inpaint_fn is None:
                raise UnsupportedEditMode(
                    "inpaint 需外部模型，未提供 inpaint_fn（阶段 C 未接真执行器）")
            out = inpaint_fn(out, region, e.get("instruction", ""),
                             e.get("params") or {})
            continue
        # transform
        op = (e.get("params") or {}).get("op")
        if not op:
            continue
        mask = region_to_mask(out.shape, region)
        new = apply_op(out, op, e.get("params") or {})
        if out.ndim == 3 and mask.ndim == 2:
            mask3 = mask[..., np.newaxis]
        else:
            mask3 = mask
        out = np.where(mask3, new, out)
    return out
