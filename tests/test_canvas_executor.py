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
# Wave A #6：执行器产出校验
run("A11 executors 含 validate_output（执行器产出校验）",
    hasattr(ex, "validate_output"))
run("A12 executors 含 _OUTPUT_EXTS 映射（kind → 允许扩展名）",
    isinstance(getattr(ex, "_OUTPUT_EXTS", None), dict)
    and {"image", "clip", "video"}.issubset(ex._OUTPUT_EXTS))
run("A13 三个执行器均接入了 validate_output（gen_image / gen_video / promo_fx）",
    EXEC_SRC.count("validate_output(") >= 4)  # 1 定义 + 3 调用

# ---- Wave D 复审 #8：上游选择语义（谁当参考帧必须说得清、可复现）----
run("A14 executors 提供上游选择 API（select_upstream / _upstream_assets / _direct_upstream）",
    all(hasattr(ex, n) for n in
        ("select_upstream", "_upstream_assets", "_direct_upstream")))
run("A15 gen_video / promo_fx 改用确定性的上游选择（不再直接取递归收集的第一个）",
    'select_upstream(graph, node, "image")' in EXEC_SRC
    and '_upstream_assets(graph, node, "image")' in EXEC_SRC)
_SEL_SRC = EXEC_SRC.split("def select_upstream", 1)[1].split("\ndef ", 1)[0]
run("A16 上游选择支持显式指定（config.ref_source / config.ref_port），指不到就抛错",
    'cfg.get("ref_source")' in _SEL_SRC and 'cfg.get("ref_port")' in _SEL_SRC
    and _SEL_SRC.count("raise UnsupportedEditMode") >= 2,
    "显式指定指不到却静默回落 → 用户点名要 A 却拿到 B，比直接报错难查得多")

# ---- Wave D 复审 #7：占位产出必须显式标记（不能冒充真出片）----
run("A17 passthrough_executor 的产出显式标记 placeholder=True",
    "placeholder=True" in
    EXEC_SRC.split("def passthrough_executor", 1)[1].split("\ndef ", 1)[0],
    "passthrough 写的只是 manifest 占位物，却不标出来 → 与真出片长得一样")


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
# 注：样本图含 gen_video / promo_fx 节点，需注入回调方能跑通全图
# （gen_video 未注入 video_fn 时会诚实抛 UnsupportedEditMode，阻塞 final）
# 回调必须真落盘：Wave A #6 的 validate_output 会校验「存在+非空+扩展名+在资产目录内」，
# 返回不存在的路径 → 该节点诚实 failed → final 被阻塞 → B7b 全 completed 崩。
root_b7 = tempfile.mkdtemp()
g_b7 = cg.build_sample_graph()


def _write_placeholder(path, payload=b"x"):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "wb") as f:
        f.write(payload)
    return path


def _b7_video(node, asset_root, src_image_path, prompt):
    return _write_placeholder(
        os.path.join(asset_root, "video", "%s_clip.mp4" % node.id),
        b"\x00\x00\x00\x18ftypmp42")


def _b7_motion(src_paths, out_path, params):
    return _write_placeholder(out_path + ".gif", b"GIF89a")


g_b7.use_real_executors(root_b7, video_fn=_b7_video, motion_fn=_b7_motion)
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
# B8 validate_output：执行器产出校验（Wave A #6 —— 宁可 failed，不登记假完成）
# --------------------------------------------------------------------------
root_v = tempfile.mkdtemp()


def _v_raises(fn, *a, **kw):
    """期待抛 UnsupportedEditMode，返回 (是否抛了, 异常文本)。"""
    try:
        fn(*a, **kw)
    except il.UnsupportedEditMode as e:
        return True, str(e)
    except Exception as e:  # noqa: BLE001
        return False, "抛错类型不对: %s: %s" % (type(e).__name__, e)
    return False, "未抛错"


# 合法产出：返回规整路径
p_ok = os.path.join(root_v, "image", "ok.png")
_write_placeholder(p_ok, b"PNG")
v_ok = ex.validate_output(p_ok, "image", root_v)
run("B8 validate_output 接受合法产出并返回规整路径", v_ok == p_ok, str(v_ok))

# 前后空白应被 strip
v_strip = ex.validate_output("  %s  " % p_ok, "image", root_v)
run("B8b validate_output 规整首尾空白", v_strip == p_ok, str(v_strip))

# 文件不存在
t, d = _v_raises(ex.validate_output,
                 os.path.join(root_v, "image", "nope.png"), "image", root_v)
run("B8c 输出文件不存在 → 抛 UnsupportedEditMode", t, d)

# 空文件
p_empty = os.path.join(root_v, "image", "empty.png")
open(p_empty, "wb").close()
t, d = _v_raises(ex.validate_output, p_empty, "image", root_v)
run("B8d 输出文件为空（0 字节）→ 抛 UnsupportedEditMode", t, d)

# 扩展名不符（clip 却给 .png）
t, d = _v_raises(ex.validate_output, p_ok, "clip", root_v)
run("B8e 扩展名与 kind 不符（.png 当 clip）→ 抛 UnsupportedEditMode", t, d)

# 越出资产目录
outside = os.path.join(tempfile.mkdtemp(), "outside.png")
_write_placeholder(outside, b"PNG")
t, d = _v_raises(ex.validate_output, outside, "image", root_v)
run("B8f 输出越出资产目录 → 抛 UnsupportedEditMode", t, d)

# 非字符串 / 空串 / None
bad_kinds = [(None, "None"), ("", "空串"), ("   ", "纯空白")]
all_bad = all(_v_raises(ex.validate_output, v, "image", root_v)[0]
              for v, _ in bad_kinds)
run("B8g 非字符串/空串/纯空白 → 抛 UnsupportedEditMode", all_bad,
    str([(lbl, _v_raises(ex.validate_output, v, "image", root_v)[1])
         for v, lbl in bad_kinds]))

# 不给 asset_root 时跳过目录围栏（仍校验存在+非空+扩展名）
v_noroot = ex.validate_output(p_ok, "image")
run("B8h 未给 asset_root 时跳过目录围栏但其余校验照做", v_noroot == p_ok, str(v_noroot))

# B9 端到端：video_fn 返回不存在的路径 → gen_video 节点诚实 failed（不冒充完成）
root_v9 = tempfile.mkdtemp()
g_v9 = cg.build_sample_graph()


def _v9_fake_video(node, asset_root, src_image_path, prompt):
    return os.path.join(asset_root, "video", "%s_clip.mp4" % node.id)  # 不落盘


def _v9_ok_motion(src_paths, out_path, params):
    return _write_placeholder(out_path + ".gif", b"GIF89a")


g_v9.use_real_executors(root_v9, video_fn=_v9_fake_video, motion_fn=_v9_ok_motion)
g_v9.run({})
run("B9 假路径（video_fn 不落盘）→ vid 节点诚实 failed",
    g_v9.nodes["vid"].status == "failed", g_v9.nodes["vid"].status)
run("B9b 下游 promo/final 因上游 failed 被阻塞（不冒充 completed）",
    g_v9.nodes["promo"].status != "completed"
    and g_v9.nodes["final"].status != "completed",
    "promo=%s final=%s" % (g_v9.nodes["promo"].status, g_v9.nodes["final"].status))

# B10 端到端：motion_fn 返回越出资产目录的路径 → promo_fx 诚实 failed
root_v10 = tempfile.mkdtemp()
outside10 = os.path.join(tempfile.mkdtemp(), "evil.gif")
g_v10 = cg.build_sample_graph()


def _v10_ok_video(node, asset_root, src_image_path, prompt):
    return _write_placeholder(
        os.path.join(asset_root, "video", "%s_clip.mp4" % node.id),
        b"\x00\x00\x00\x18ftypmp42")


def _v10_evil_motion(src_paths, out_path, params):
    return _write_placeholder(outside10, b"GIF89a")  # 越目录


g_v10.use_real_executors(root_v10, video_fn=_v10_ok_video, motion_fn=_v10_evil_motion)
g_v10.run({})
run("B10 越目录产出（motion_fn 写外部路径）→ promo 节点诚实 failed",
    g_v10.nodes["promo"].status == "failed", g_v10.nodes["promo"].status)


# --------------------------------------------------------------------------
# B11-B17 Wave D：#7 占位语义 + #8 上游选择语义
# --------------------------------------------------------------------------
# B11 stub（默认执行器）产出的东西磁盘上并不存在 —— 必须显式标成占位，
# 否则「registered=True + 绿点」会被读成「真出片」。
root_ph = tempfile.mkdtemp()
g_ph_stub = cg.CanvasGraph()
g_ph_stub.add_node(cg.CanvasNode("s", "source_prompt",
                                 outputs={"prompt": cg.Port("prompt", "prompt")}))
g_ph_stub.run({})
_ref_stub = g_ph_stub.nodes["s"].out_assets["prompt"]
run("B11 stub 产出标记为占位（AssetRef.placeholder=True）",
    _ref_stub is not None and _ref_stub.placeholder is True, str(_ref_stub))
run("B11b stub 产出仍照常登记（placeholder 与 registered 是两件事）",
    _ref_stub is not None and _ref_stub.registered is True,
    "不登记的话调度链/资产引用就断了")
run("B11c 跑完后节点自身也标记为占位节点（node.placeholder）",
    g_ph_stub.nodes["s"].placeholder is True)
run("B11d asset_registrations 同时报出 registered 与 placeholder",
    bool(g_ph_stub.asset_registrations())
    and g_ph_stub.asset_registrations()[0].get("placeholder") is True)

# B12 真执行器产出不标占位；同一张图里的 passthrough 节点标占位
root_ph2 = tempfile.mkdtemp()
g_ph = cg.build_sample_graph()
g_ph.use_real_executors(root_ph2, video_fn=_b7_video, motion_fn=_b7_motion)
g_ph.run({})
run("B12 真执行器节点（img/vid/promo）产出不标占位",
    g_ph.nodes["img"].placeholder is False
    and g_ph.nodes["vid"].placeholder is False
    and g_ph.nodes["promo"].placeholder is False,
    "img=%s vid=%s promo=%s" % (g_ph.nodes["img"].placeholder,
                                g_ph.nodes["vid"].placeholder,
                                g_ph.nodes["promo"].placeholder))
run("B12b 同图的 passthrough 节点（digital_twin / final_output）标占位",
    g_ph.nodes["twin"].placeholder is True
    and g_ph.nodes["final"].placeholder is True,
    "twin=%s final=%s" % (g_ph.nodes["twin"].placeholder,
                          g_ph.nodes["final"].placeholder))

# B13 多入节点按「输入端口声明顺序」选参考帧，与连线先后无关
gs = cg.CanvasGraph()
gs.add_node(cg.CanvasNode("p1", "gen_image",
                          outputs={"o": cg.Port("o", "image")}))
gs.add_node(cg.CanvasNode("p2", "gen_image",
                          outputs={"o": cg.Port("o", "image")}))
gs.add_node(cg.CanvasNode("v", "gen_video",
                          inputs={"first": cg.Port("first", "image"),
                                  "second": cg.Port("second", "image")},
                          outputs={"clip": cg.Port("clip", "clip")}))
gs.connect_data("p2", "o", "v", "second")     # 先连「第二个」端口
gs.connect_data("p1", "o", "v", "first")      # 后连「第一个」端口
gs.nodes["p1"].out_assets["o"] = cg.AssetRef(kind="image", name="p1", path="P1.png")
gs.nodes["p2"].out_assets["o"] = cg.AssetRef(kind="image", name="p2", path="P2.png")
_sel = ex.select_upstream(gs, gs.nodes["v"], "image")
run("B13 多入节点按输入端口声明顺序选参考帧（与连线先后无关）",
    _sel == ("P1.png", "p1.o"), str(_sel))
run("B13b 递归收集同样按端口声明顺序（不依赖连线时序）",
    [c["path"] for c in ex._upstream_assets(gs, gs.nodes["v"], "image")]
    == ["P1.png", "P2.png"],
    str([c["path"] for c in ex._upstream_assets(gs, gs.nodes["v"], "image")]))

# B14 显式指定 ref_source / ref_port 生效；指不到则诚实抛错（不静默回落）
gs.nodes["v"].config = {"ref_source": "p2.o"}
run("B14 ref_source 显式指定覆盖端口顺序",
    ex.select_upstream(gs, gs.nodes["v"], "image") == ("P2.png", "p2.o"))
gs.nodes["v"].config = {"ref_source": "nope.o"}
_t, _d = _v_raises(ex.select_upstream, gs, gs.nodes["v"], "image")
run("B14b ref_source 指不到 → 诚实抛 UnsupportedEditMode（不静默回落）", _t, _d)
gs.nodes["v"].config = {"ref_port": "second"}
run("B14c ref_port 指定端口生效",
    ex.select_upstream(gs, gs.nodes["v"], "image") == ("P2.png", "p2.o"))
gs.nodes["v"].config = {"ref_port": "nope"}
_t, _d = _v_raises(ex.select_upstream, gs, gs.nodes["v"], "image")
run("B14d ref_port 指不到 → 诚实抛 UnsupportedEditMode", _t, _d)

# B15 gen_video 结果里回显「实际用了哪一路上游」（出片不对时才查得下去）
gs.nodes["v"].config = {}
node_vid = g_ph.nodes["vid"]
_ex_fn = ex.gen_video_executor(node_vid, root_ph2, video_fn=_b7_video, graph=g_ph)
_res_vid = _ex_fn({})
run("B15 gen_video 结果回显实际选中的参考来源 src_from",
    _res_vid.get("src_from") == "img.image", str(_res_vid.get("src_from")))


# --------------------------------------------------------------------------
# C 结构
# --------------------------------------------------------------------------
print("== C 结构 ==")
run("C1 切片 gen_image_executor 含真实写盘（_array_to_png 调用）",
    "_array_to_png(out_arr, out_path)" in EXEC_SRC)
run("C2 切片 apply_real_executors 含 node.executor 赋值",
    "node.executor = build_executor" in EXEC_SRC)
run("C3 切片 use_real_executors 调用 executors 模块（透传 inpaint_fn/video_fn/motion_fn）",
    "apply_real_executors(self, asset_root, inpaint_fn, video_fn, motion_fn)" in GRAPH_SRC)


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
