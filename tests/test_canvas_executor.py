# -*- coding: utf-8 -*-
"""第 5 步执行器 · 判据套件（A 静态 / B 行为 / C 结构）。

验证：gen_image 真实落盘 + 局部编辑（transform/inpaint）在运行时真正生效；
其余节点走落盘 passthrough；inpaint 未注入诚实抛错、注入回调则生效。

支持环境变量：
  CX_PATH   若设置，将其所在目录插入 sys.path 最前（兼容源码重定向）
  CX_CHECK  若设置（逗号分隔前缀），只跑匹配前缀的判据（扰动脚本精准断言用）
判据全绿打印横幅 "ALL GREEN"，有 FAIL 以退出码 1 结束。
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.environ.get("CX_PATH"):
    sys.path.insert(0, os.path.dirname(os.environ["CX_PATH"]) or ".")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import canvas_graph as cg  # noqa: E402
import image_local_edit as il  # noqa: E402
import executors as ex  # noqa: E402

EXEC_SRC = open(os.path.join(ROOT, "executors.py"), encoding="utf-8").read()
GRAPH_SRC = open(os.path.join(ROOT, "canvas_graph.py"), encoding="utf-8").read()


# --------------------------------------------------------------------------
# 判据框架
# --------------------------------------------------------------------------
_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond)))
    mark = "OK " if cond else "FAIL"
    line = "  [%s] %s" % (mark, name)
    if not cond and detail:
        line += "  :: %s" % detail
    print(line)


_CX_CHECK = os.environ.get("CX_CHECK", "")
_ONLY = [c.strip() for c in _CX_CHECK.split(",") if c.strip()] if _CX_CHECK else None


def run(name, cond, detail=""):
    if _ONLY and not any(name.startswith(o) for o in _ONLY):
        return
    check(name, cond, detail)


def _img_arr(path):
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)


def _run_gen_image(root, local_edits=()):
    """建示例图 + 给 img 节点加 edits + 装真实执行器 + run，返回 (graph, 源图数组, 编辑图数组)。"""
    g = cg.build_sample_graph()
    for e in local_edits:
        g.add_local_edit("img", e)
    g.use_real_executors(root)
    g.run({})
    ref = g.nodes["img"].out_assets["image"]
    src = _img_arr(os.path.join(root, "image", "img_source.png"))
    out = _img_arr(ref.path) if ref and os.path.exists(ref.path) else None
    return g, src, out


# --------------------------------------------------------------------------
# A 静态
# --------------------------------------------------------------------------
print("== A 静态 ==")
run("A1 executors 模块含 gen_image_executor",
    hasattr(ex, "gen_image_executor"))
run("A2 executors 模块含 passthrough_executor",
    hasattr(ex, "passthrough_executor"))
run("A3 executors 模块含 build_executor",
    hasattr(ex, "build_executor"))
run("A4 executors 模块含 apply_real_executors",
    hasattr(ex, "apply_real_executors"))
run("A5 CanvasGraph.use_real_executors 存在",
    hasattr(cg.CanvasGraph, "use_real_executors"))
run("A6 CanvasGraph.set_executor 存在",
    hasattr(cg.CanvasGraph, "set_executor"))
run("A7 gen_image_executor 调用 apply_local_edits（局部编辑引擎）",
    "apply_local_edits" in EXEC_SRC)
_a8 = ("_array_to_png" in EXEC_SRC) and (".save(" in EXEC_SRC) \
      and ("stage_path" in EXEC_SRC) and ("_make_source_image" in EXEC_SRC)
run("A8 gen_image_executor 真实写盘（_array_to_png / .save）", _a8)
run("A9 use_real_executors 装配真实执行器（调 apply_real_executors）",
    "apply_real_executors" in GRAPH_SRC)
run("A10 passthrough 真实写占位文件（open(path",
    "open(path" in EXEC_SRC and "placeholder" in EXEC_SRC)


# --------------------------------------------------------------------------
# B 行为
# --------------------------------------------------------------------------
print("== B 行为 ==")

# B1 真实产图文件存在、合法 PNG、非空
root_b1 = tempfile.mkdtemp()
g_b1 = cg.build_sample_graph()
g_b1.use_real_executors(root_b1)
g_b1.run({})
ref_b1 = g_b1.nodes["img"].out_assets["image"]
p_b1 = ref_b1.path if ref_b1 else None
b1_ok = bool(p_b1 and os.path.exists(p_b1) and p_b1.endswith(".png")
             and os.path.getsize(p_b1) > 0)
if b1_ok:
    try:
        im = Image.open(p_b1)
        b1_ok = im.format == "PNG" and im.size[0] > 0
    except Exception:
        b1_ok = False
run("B1 运行后 img 节点产出合法 PNG 文件且非空", b1_ok, p_b1 or "no path")

# 对照：无编辑的源图数组（供后续比对）
root_plain = tempfile.mkdtemp()
g_plain, src_plain, out_plain = _run_gen_image(root_plain)

# B2 整体灰度生效
root_b2 = tempfile.mkdtemp()
g_b2, src_b2, out_b2 = _run_gen_image(
    root_b2,
    [{"target": "image", "mode": "transform", "region": None,
      "instruction": "整体灰度", "params": {"op": "grayscale"}}])
b2_gray = (out_b2 is not None
           and np.allclose(out_b2[..., 0], out_b2[..., 1], atol=2)
           and np.allclose(out_b2[..., 1], out_b2[..., 2], atol=2))
run("B2 含 grayscale 编辑后三通道相等（灰度化生效）", b2_gray)
run("B2b 灰度结果与未编辑版本不同（像素真的变了）",
    out_b2 is not None and out_plain is not None
    and not np.array_equal(out_b2, out_plain))

# B3 多编辑顺序生效（灰度 + 亮度）
root_b3 = tempfile.mkdtemp()
g_b3, _, _ = _run_gen_image(
    root_b3,
    [{"target": "image", "mode": "transform", "region": None,
      "instruction": "灰度", "params": {"op": "grayscale"}},
     {"target": "image", "mode": "transform", "region": None,
      "instruction": "变亮", "params": {"op": "brightness", "factor": 1.5}}])
out_b3 = _img_arr(g_b3.nodes["img"].out_assets["image"].path)
# 仅灰度的对照
root_b3g = tempfile.mkdtemp()
g_b3g, _, _ = _run_gen_image(
    root_b3g,
    [{"target": "image", "mode": "transform", "region": None,
      "instruction": "灰度", "params": {"op": "grayscale"}}])
out_b3g = _img_arr(g_b3g.nodes["img"].out_assets["image"].path)
run("B3 多编辑顺序生效：灰度+亮度 ≠ 仅灰度",
    not np.array_equal(out_b3, out_b3g))
run("B3b 多编辑后仍保持灰度",
    np.allclose(out_b3[..., 0], out_b3[..., 1], atol=2))

# B4 矩形区域局部编辑：仅掩码内变、外不变
root_b4 = tempfile.mkdtemp()
g_b4, _, out_b4 = _run_gen_image(
    root_b4,
    [{"target": "image", "mode": "transform",
      "region": {"type": "rect", "x": 0, "y": 0, "w": 0.5, "h": 1.0},
      "instruction": "左半灰度", "params": {"op": "grayscale"}}])
H, W, _ = out_b4.shape
left = out_b4[:, :W // 2, :]
right = out_b4[:, W // 2:, :]
right_src = src_plain[:, W // 2:, :]
run("B4 左半（掩码内）灰度化", np.allclose(left[..., 0], left[..., 1], atol=2))
run("B4 右半（掩码外）保持原样不变", np.allclose(right, right_src, atol=2))

# B5 inpaint 未注入 → 诚实抛 UnsupportedEditMode（直接调 executor 闭包）
root_b5 = tempfile.mkdtemp()
g_b5 = cg.build_sample_graph()
g_b5.add_local_edit("img", {"target": "image", "mode": "inpaint",
                            "region": None, "instruction": "去水印",
                            "params": {"strength": 0.8}})
g_b5.use_real_executors(root_b5)
threw = False
try:
    g_b5.nodes["img"].executor({})
except il.UnsupportedEditMode:
    threw = True
run("B5 inpaint 未注入 inpaint_fn 时诚实抛 UnsupportedEditMode", threw)

# B5b 经 run 链路：注入缺失 → 该节点 failed（不冒充成功）
root_b5b = tempfile.mkdtemp()
g_b5b = cg.build_sample_graph()
g_b5b.add_local_edit("img", {"target": "image", "mode": "inpaint",
                             "region": None, "instruction": "去水印",
                             "params": {}})
g_b5b.use_real_executors(root_b5b)
g_b5b.run({})
run("B5b 经 run 链路 inpaint 缺失 → img 节点 status=failed（诚实）",
    g_b5b.nodes["img"].status == "failed")

# B6 inpaint 注入回调 → 生效
root_b6 = tempfile.mkdtemp()
called = {"n": 0}
def _inp(arr, region, instr, params):
    called["n"] += 1
    return np.clip(arr * 0.5, 0, 1)
g_b6 = cg.build_sample_graph()
g_b6.add_local_edit("img", {"target": "image", "mode": "inpaint",
                            "region": None, "instruction": "去水印",
                            "params": {}})
g_b6.use_real_executors(root_b6, inpaint_fn=_inp)
g_b6.run({})
out_b6 = _img_arr(g_b6.nodes["img"].out_assets["image"].path)
src_b6 = _img_arr(os.path.join(root_b6, "image", "img_source.png"))
run("B6 inpaint_fn 被调用且结果被应用（像素减半）",
    called["n"] == 1 and np.allclose(out_b6, src_b6 * 0.5, atol=3))

# B7 passthrough：其余节点落盘占位 + 全部 completed + 资产登记
root_b7 = tempfile.mkdtemp()
g_b7 = cg.build_sample_graph()
g_b7.use_real_executors(root_b7)
g_b7.run({})
final_ref = list(g_b7.nodes["final"].out_assets.values())[0] if \
    g_b7.nodes["final"].out_assets else None
p_b7 = final_ref.path if final_ref else None
run("B7 final_output 节点落盘占位文件且登记",
    bool(p_b7 and os.path.exists(p_b7)))
run("B7b 所有节点 run 后均为 completed",
    all(n.status == "completed" for n in g_b7.nodes.values()))
run("B7c 资产被登记（registered=True）",
    final_ref is not None and final_ref.registered)


# --------------------------------------------------------------------------
# C 结构
# --------------------------------------------------------------------------
print("== C 结构 ==")
run("C1 切片 gen_image_executor 含真实写盘（_array_to_png 调用）",
    "_array_to_png(out_arr, out_path)" in EXEC_SRC)
run("C2 切片 apply_real_executors 含 node.executor 赋值",
    "node.executor = build_executor" in EXEC_SRC)
run("C3 切片 use_real_executors 调用 executors 模块",
    "apply_real_executors(self, asset_root, inpaint_fn)" in GRAPH_SRC)


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------
_COUNT = [r for r in _RESULTS if not _ONLY or any(r[0].startswith(o) for o in _ONLY)]
_FAILED = [n for n, ok in _COUNT if not ok]
total = len(_COUNT)
if _ONLY:
    print("\n=== 子集 %s 判据: %d 项, 失败 %d ===" %
          (",".join(_ONLY), total, len(_FAILED)))
else:
    print("\n=== 判据: 共 %d 项, 失败 %d ===" % (total, len(_FAILED)))
if _FAILED:
    for n in _FAILED:
        print("  FAIL: %s" % n)
    print("RESULT: RED")
    sys.exit(1)
if _ONLY:
    print("SUBSET ALL GREEN")
else:
    print("ALL GREEN")
sys.exit(0)
