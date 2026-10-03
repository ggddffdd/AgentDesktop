# -*- coding: utf-8 -*-
"""节点画布阶段 C（图片局部编辑·结果图导出）· 判据套件。

验证目标（设计稿 §9 阶段 C / CANVAS_RESEARCH_REPORT §4）：
  A 静态：image_local_edit 引擎模块与符号 / VALID_TRANSFORM_OPS 常量 /
          canvas_export 结果图导出函数 / canvas_panel 工具栏与对话框操作下拉存在。
  B 行为（纯 numpy + PIL，无 GUI 实例化 widget）：
          1) rect region：区域内变、区域外不变（局部语义）
          2) contrast 整图变换生效
          3) polygon region：仅多边形内变化
          4) export_node_local_edit_image 真实读写 PNG 往返 + 像素变化
          5) export_all_local_edit_images 仅导出「图片类 + 含 edits」节点
          6) inpaint 默认抛 UnsupportedEditMode；注入 inpaint_fn 后生效
          7) 多条编辑按序叠加（先变亮再灰度 = 灰阶）
          8) region=None 应用整图
  C 结构：切片 apply_local_edits 含掩码分支(np.where) 与 region_to_mask 调用；
          export_node_local_edit_image 含 il.apply_local_edits 调用。

支持 CX_PATH（注入 sys.path）与 CX_CHECK（按判据首 token 过滤，逗号分隔）。
"""

import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_cx = os.environ.get("CX_PATH")
if _cx and _cx not in sys.path:
    sys.path.insert(0, _cx)

import numpy as np                       # noqa: E402
import image_local_edit as il            # noqa: E402
import canvas_graph as cg                # noqa: E402
import canvas_export as ce               # noqa: E402
import canvas_panel as cp                # noqa: E402
from PIL import Image                    # noqa: E402

PANEL_SRC = open(os.path.join(ROOT, "canvas_panel.py"), encoding="utf-8").read()
EXPORT_SRC = open(os.path.join(ROOT, "canvas_export.py"), encoding="utf-8").read()
IL_SRC = open(os.path.join(ROOT, "image_local_edit.py"), encoding="utf-8").read()

results = []


def check(name, ok, detail=""):
    # CX_CHECK 过滤：仅保留匹配首 token 前缀的判据（供扰动脚本定点断言）
    tok = name.split()[0] if name.split() else name
    _cxcheck = os.environ.get("CX_CHECK", "").strip()
    if _cxcheck:
        if not any(tok.startswith(p.strip()) for p in _cxcheck.split(",") if p.strip()):
            return
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


def _img_two_tone():
    """100x100 RGB：左半 0.2 灰，右半 0.8 灰（float 0..1），便于看局部变化。"""
    a = np.zeros((100, 100, 3), dtype=np.float64)
    a[:, :50] = 0.2
    a[:, 50:] = 0.8
    return a


# =========================================================================
# A 静态：符号与契约片段存在性
# =========================================================================
def section_a():
    print("\n===== A. 静态契约 =====")
    for sym in ("def region_to_mask", "def apply_op", "def apply_local_edits"):
        check("A1 引擎符号 %s 存在" % sym, sym in IL_SRC)
    check("A2 VALID_TRANSFORM_OPS 含 7 操作 + UnsupportedEditMode",
          "VALID_TRANSFORM_OPS" in IL_SRC and "UnsupportedEditMode" in IL_SRC
          and all(o in IL_SRC for o in il.VALID_TRANSFORM_OPS))
    check("A3 export 结果图函数存在",
          "def export_node_local_edit_image" in EXPORT_SRC
          and "def export_all_local_edit_images" in EXPORT_SRC)
    check("A4 export 集成 import 引擎 + node_supports_local_edit",
          "import image_local_edit as il" in EXPORT_SRC
          and "node_supports_local_edit" in EXPORT_SRC)
    check("A5 工具栏 btn_export + _export_local_edited_images",
          "btn_export" in PANEL_SRC and "_export_local_edited_images" in PANEL_SRC)
    check("A6 LocalEditDialog op 下拉 + build_edit 组装 params[\"op\"]",
          "cmb_op" in PANEL_SRC and 'params["op"]' in PANEL_SRC)
    check("A7 cmb_op 选项来自 VALID_TRANSFORM_OPS_C",
          "VALID_TRANSFORM_OPS_C" in PANEL_SRC)


# =========================================================================
# B 行为：像素变换 + 文件往返 + 批量 + inpaint 契约
# =========================================================================
def section_b():
    print("\n===== B. 行为（无 GUI）=====")

    # B1 rect region：区域内变、区域外不变
    a = _img_two_tone()
    edits = [{"mode": "transform",
              "region": {"type": "rect", "x": 0, "y": 0, "w": 0.5, "h": 1},
              "instruction": "局部提亮", "params": {"op": "brightness", "factor": 2}}]
    out = il.apply_local_edits(a, edits)
    left = out[:, :50].mean()
    right = out[:, 50:].mean()
    check("B1 rect 区域内变外不变",
          abs(left - 0.4) < 1e-6 and abs(right - 0.8) < 1e-9,
          "left=%.4f right=%.4f" % (left, right))

    # B2 contrast 整图变换：0.2→(0.2-0.5)*2+0.5=-0.1→clip 0.0；0.8→1.1→clip 1.0
    a = _img_two_tone()
    out = il.apply_local_edits(a, [{"mode": "transform", "region": None,
                                    "instruction": "加对比", "params": {"op": "contrast", "factor": 2}}])
    check("B2 contrast 整图生效",
          abs(out[:, :50].mean() - 0.0) < 1e-6 and out[:, 50:].mean() > 0.99,
          "left=%.4f right=%.4f" % (out[:, :50].mean(), out[:, 50:].mean()))

    # B3 polygon region：仅多边形内变化
    a = np.full((100, 100, 3), 0.5, dtype=np.float64)
    poly = {"type": "polygon", "points": [[0.1, 0.1], [0.1, 0.9], [0.9, 0.5]]}
    out = il.apply_local_edits(a, [{"mode": "transform", "region": poly,
                                   "instruction": "三角区提亮", "params": {"op": "brightness", "factor": 2}}])
    inside = out[50, 20].mean()          # (x=20,y=50) 在三角内
    outside = out[95, 95].mean()         # 右下角在外
    check("B3 polygon 仅内变", abs(inside - 1.0) < 1e-6 and abs(outside - 0.5) < 1e-9,
          "inside=%.4f outside=%.4f" % (inside, outside))

    # B4 真实 PNG 读写往返
    g = cg.build_sample_graph()
    g.add_local_edit("img", {"mode": "transform",
                             "region": {"type": "rect", "x": 0, "y": 0, "w": 0.5, "h": 1},
                             "instruction": "局部提亮", "params": {"op": "brightness", "factor": 2}})
    d = tempfile.mkdtemp()
    src = os.path.join(d, "src.png")
    Image.fromarray((_img_two_tone() * 255).astype("uint8")).save(src)
    outp = os.path.join(d, "out.png")
    info = ce.export_node_local_edit_image(g, "img", src, outp)
    check("B4 导出结果图存在且 applied=1",
          os.path.isfile(outp) and info.get("applied") == 1, str(info))
    res = np.asarray(Image.open(outp)).astype(np.float64) / 255.0
    check("B4b 结果图左半变亮", abs(res[:, :50].mean() - 0.4) < 2e-2,
          "left=%.4f" % res[:, :50].mean())

    # B5 批量：仅导出「图片类 + 含 edits」节点
    g = cg.build_sample_graph()
    g.add_local_edit("img", {"mode": "transform", "region": None,
                             "instruction": "提亮", "params": {"op": "brightness", "factor": 1.3}})
    d = tempfile.mkdtemp()
    img_src = os.path.join(d, "img.png")
    Image.fromarray((np.zeros((50, 50, 3), np.uint8) + 100)).save(img_src)
    g.nodes["img"].config["image_path"] = img_src
    # final 节点无 edits、无 image_path，应跳过
    res_list = ce.export_all_local_edit_images(
        g, d, src_resolver=lambda n: (n.config or {}).get("image_path"))
    check("B5 仅含edits节点导出", len(res_list) == 1 and res_list[0]["node_id"] == "img"
          and res_list[0]["applied"] == 1, str(res_list))
    check("B5b 结果文件存在", os.path.isfile(os.path.join(d, "img_edited.png")))

    # B6 inpaint 默认抛错 + 注入回调
    a = _img_two_tone()
    raised = False
    try:
        il.apply_local_edits(a, [{"mode": "inpaint", "region": None,
                                  "instruction": "去水印", "params": {}}])
    except il.UnsupportedEditMode:
        raised = True
    check("B6 inpaint 默认抛 UnsupportedEditMode", raised)
    inj = il.apply_local_edits(a, [{"mode": "inpaint", "region": None,
                                    "instruction": "x", "params": {}}],
                               inpaint_fn=lambda arr, r, i, p: np.clip(arr * 0.5, 0, 1))
    check("B6b inpaint 注入回调生效", abs(inj.mean() - a.mean() * 0.5) < 1e-6)

    # B7 多次编辑按序叠加（先变亮再灰度 = 灰阶）
    a = _img_two_tone()
    out = il.apply_local_edits(a, [
        {"mode": "transform", "region": None, "instruction": "亮", "params": {"op": "brightness", "factor": 2}},
        {"mode": "transform", "region": None, "instruction": "灰", "params": {"op": "grayscale"}},
    ])
    check("B7 顺序：亮度后灰度→灰阶",
          np.allclose(out[:, :, 0], out[:, :, 1]) and np.allclose(out[:, :, 1], out[:, :, 2]))
    check("B7b 左半灰度亮度=0.4", abs(out[10, 10, 0] - 0.4) < 1e-6)

    # B8 region=None 应用整图
    a = np.full((40, 40, 3), 0.3, dtype=np.float64)
    out = il.apply_local_edits(a, [{"mode": "transform", "region": None,
                                    "instruction": "亮", "params": {"op": "brightness", "factor": 1.5}}])
    check("B8 region=None 整图变换", abs(out.mean() - 0.45) < 1e-6)

    # B9 grayscale 作用于彩色图 → 三通道相等（灰阶），用于钉住 grayscale 行为
    a = np.zeros((20, 20, 3), dtype=np.float64)
    a[:, :10] = [1.0, 0.0, 0.0]      # 左半纯红
    a[:, 10:] = [0.0, 1.0, 0.0]      # 右半纯绿
    out = il.apply_local_edits(a, [{"mode": "transform", "region": None,
                                   "instruction": "灰度", "params": {"op": "grayscale"}}])
    check("B9 grayscale 彩色图→灰阶(三通道相等)",
          np.allclose(out[:, :, 0], out[:, :, 1])
          and np.allclose(out[:, :, 1], out[:, :, 2])
          and abs(out[0, 0, 0] - 1 / 3) < 1e-6,
          "r=%.4f g=%.4f b=%.4f" % (out[0, 0, 0], out[0, 0, 1], out[0, 0, 2]))


# =========================================================================
# C 结构：切片确认掩码分支 / 引擎调用链路
# =========================================================================
def section_c():
    print("\n===== C. 结构切片 =====")
    il_slice = _slice_func(IL_SRC, "apply_local_edits")
    check("C1 切片 apply_local_edits 含掩码分支 np.where", "np.where" in il_slice)
    check("C2 切片 apply_local_edits 含 region_to_mask 调用", "region_to_mask" in il_slice)
    ex_slice = _slice_func(EXPORT_SRC, "export_node_local_edit_image")
    check("C3 切片 export 含 il.apply_local_edits", "apply_local_edits" in ex_slice)


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)
    print("\n===== 横幅 =====")
    print("判据: %d  通过: %d  失败: %d" % (len(results), passed, failed))
    print("RESULT: %s" % ("ALL GREEN" if failed == 0 else "HAS FAIL"))
    raise SystemExit(1 if failed else 0)
