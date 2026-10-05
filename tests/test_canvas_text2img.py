# -*- coding: utf-8 -*-
"""画布 gen_image 接 Agnes 纯文生图（v4.211.11）—— 判据套件。

背景（2026-10-05 用户反馈"会生成渐变图，可能是我不会用"）：
  不是不会用 —— gen_image 执行器压根没接 AI：`_make_source_image` 用 PIL 画
  水平渐变 + prompt 白字标注（executors.py:118），agnes_bridge 只有
  inpaint（图生图重绘，payload 带 extra_body.image）/ video / promo_motion
  三个回调，**没有文生图**。本轮补 `get_agnes_text2img_fn`（同端点
  /images/generations、同模型 agnes-image-2.5-flash，区别仅 **不带
  extra_body.image** —— 2026-10-05 实探 8.6s / 1024×1024 通过）。

语义约定（本轮定版）：
  * text2img_fn 注入且 prompt 非空 → 真·AI 文生图；fn 异常 → 节点诚实
    failed（**绝不静默回落占位图** —— 网络错必须让人看见）；
  * 未注入（None）或 prompt 为空 → 本地 PIL 渐变占位（离线兜底）。

五组判据（独立运行：QT_QPA_PLATFORM=offscreen python tests/test_canvas_text2img.py）：

  A 注入生效 —— mock fn 产物像素 = mock 值（走的是注入路径，不是 PIL）
  B 兜底语义 —— 未注入 → 产物是 PIL 渐变占位（480×270，离线可用）
  C 诚实失败 —— fn 抛异常 → 节点 failed（不静默回落）
  D 源码契约 —— fn 存在且 payload 不含 image 键（防退化成改图）+ 透传链四处
  E UI 全链路 —— offscreen 真 panel._run_graph，monkeypatch 后 mock 真被调
                 （UI → worker → use_real_executors → executor 注入不断链）

⚠️ 本套件全程 mock，零网络零额度；只在 %TEMP% 临时目录写产物，跑完恢复 cwd。
"""

import ast
import os
import re
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print("  [%s] %s%s" % (tag, name, ("  — " + str(extra)) if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def _hook(t, v, tb):
    print("\n[!] 未捕获异常：%s: %s" % (t.__name__, v))
    print("PASS=%d FAIL=%d" % (CHECKED - len(FAIL), len(FAIL) + 1))
    sys.exit(1)


sys.excepthook = _hook

AB_PATH = os.path.join(ROOT, "agnes_bridge.py")
EX_PATH = os.path.join(ROOT, "executors.py")
CG_PATH = os.path.join(ROOT, "canvas_graph.py")
CP_PATH = os.environ.get("CP_PATH") or os.path.join(ROOT, "canvas_panel.py")
AB_SRC = open(AB_PATH, encoding="utf-8").read()
EX_SRC = open(EX_PATH, encoding="utf-8").read()
CG_SRC = open(CG_PATH, encoding="utf-8").read()
CP_SRC = open(CP_PATH, encoding="utf-8").read()


def _run_part(fn):
    print()
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        check("%s 组抛异常" % fn.__name__.strip("_").upper(), False, repr(e))


# mock 文生图：返回 64×64 纯红（0-1 float64），并记录调用过的 prompt
def _red_fn(calls):
    def _fn(prompt):
        calls.append(prompt)
        return np.full((64, 64, 3), [1.0, 0.0, 0.0])
    return _fn


def _read_center(path):
    from PIL import Image
    img = Image.open(path).convert("RGB")
    return img.getpixel((img.width // 2, img.height // 2))


def _make_graph(prompt="一只柯基犬在雪地里奔跑"):
    """单 gen_image 节点的 CanvasGraph（产物落 asset_root/image/<id>_source.png）。"""
    import canvas_graph as cg
    g = cg.CanvasGraph()
    g.add_node(cg.CanvasNode("t2i", "gen_image", config={"prompt": prompt}))
    return g, cg


# ---------------------------------------------------------------- A 注入生效
def _a():
    print("A 组 · text2img_fn 注入生效（走注入路径，非 PIL）")
    import executors as ex
    calls = []
    work = tempfile.mkdtemp(prefix="t2i_a_")
    g, _ = _make_graph()
    node = g.nodes["t2i"]
    node.executor = ex.gen_image_executor(node, work, text2img_fn=_red_fn(calls))
    g.run({})
    check("A1 ★ mock fn 被调用且收到 prompt", calls == ["一只柯基犬在雪地里奔跑"], calls)
    out = node.out_assets.get("out")
    check("A2 产物 AssetRef 已登记且路径存在",
          out is not None and out.path and os.path.exists(out.path),
          "out=%r" % (out and out.path))
    if out and out.path and os.path.exists(out.path):
        check("A3 ★ 产物像素 = mock 红色（证明走的是注入 fn）",
              _read_center(out.path) == (255, 0, 0),
              "center=%r" % (_read_center(out.path),))
        check("A4 产物尺寸 = mock 尺寸（64×64，非 480×270 占位）",
              _img_size(out.path) == (64, 64), _img_size(out.path))

    # 上游 prompt 解析（v4.211.11 第二缺陷）：img 自身 config 无 prompt，
    # prompt 在上游 src 节点 —— 必须沿数据边 src.prompt → img.prompt 解析到。
    import canvas_graph as cg
    g2 = cg.CanvasGraph()
    g2.add_node(cg.CanvasNode("src", "source_prompt",
                              outputs={"prompt": cg.Port("prompt", "prompt")},
                              config={"prompt": "上游雪原prompt"}))
    g2.add_node(cg.CanvasNode("img2", "gen_image",
                              inputs={"prompt": cg.Port("prompt", "prompt")}))
    g2.connect_data("src", "prompt", "img2", "prompt")
    calls2 = []
    n2 = g2.nodes["img2"]
    n2.executor = ex.gen_image_executor(n2, work, text2img_fn=_red_fn(calls2), graph=g2)
    g2.run({})
    check("A5 ★ 上游 prompt 节点经数据边流入 gen_image（示例图形态）",
          calls2 == ["上游雪原prompt"], calls2)


def _img_size(path):
    from PIL import Image
    im = Image.open(path)
    return im.size


# ---------------------------------------------------------------- B 兜底语义
def _b():
    print("B 组 · 未注入 → PIL 渐变占位兜底（离线可用）")
    import executors as ex
    work = tempfile.mkdtemp(prefix="t2i_b_")
    g, _ = _make_graph()
    node = g.nodes["t2i"]
    node.executor = ex.gen_image_executor(node, work, text2img_fn=None)
    g.run({})
    out = node.out_assets.get("out")
    ok_path = out is not None and out.path and os.path.exists(out.path)
    check("B1 ★ 未注入时兜底出图（AssetRef 落盘）", ok_path,
          "out=%r status=%s" % (out and out.path, node.status))
    if ok_path:
        check("B2 ★ 兜底产物 = PIL 占位尺寸 480×270",
              _img_size(out.path) == (480, 270), _img_size(out.path))
    # prompt 为空：即便注入了也走兜底（没描述没法生图）
    g2, _ = _make_graph(prompt="")
    node2 = g2.nodes["t2i"]
    calls = []
    node2.executor = ex.gen_image_executor(node2, work, text2img_fn=_red_fn(calls))
    g2.run({})
    check("B3 ★ prompt 为空 → 不调 fn、走兜底", calls == [], calls)
    out2 = node2.out_assets.get("out")
    check("B4 兜底产物照常落盘",
          out2 is not None and out2.path and os.path.exists(out2.path),
          "status=%s" % node2.status)


# ---------------------------------------------------------------- C 诚实失败
def _c():
    print("C 组 · fn 抛异常 → 节点诚实 failed（不静默回落）")

    def _boom(prompt):
        raise RuntimeError("模拟网络故障")

    import executors as ex
    work = tempfile.mkdtemp(prefix="t2i_c_")
    g, _ = _make_graph()
    node = g.nodes["t2i"]
    node.executor = ex.gen_image_executor(node, work, text2img_fn=_boom)
    g.run({})
    check("C1 ★ fn 异常 → 节点 failed（吸收成节点级，不炸整图）",
          node.status == "failed", node.status)
    out = node.out_assets.get("out")
    empty = out is None or not out.path
    check("C2 ★ 失败节点不产出 AssetRef（没有占位图冒充真图）", empty,
          "out=%r" % (out and out.path))
    disk = [os.path.join(dp, f) for dp, _, fs in os.walk(work) for f in fs]
    check("C3 ★ 磁盘无任何图片产物", not disk, disk)


# ---------------------------------------------------------------- D 源码契约
def _d():
    print("D 组 · 源码契约（fn 存在 / payload 不含 image 键 / 透传链四处）")
    check("D1 agnes_bridge 定义了 get_agnes_text2img_fn",
          "def get_agnes_text2img_fn(" in AB_SRC)
    m = re.search(r"def get_agnes_text2img_fn\(.*?\n(?=\ndef |\Z)", AB_SRC, re.S)
    body = m.group(0) if m else ""
    check("D2 ★ 文生图 payload 不含 \"image\" 键（防止退化成图生图改图）",
          bool(body) and not re.search(r'"image"\s*:', body),
          "payload 段出现 image 键 = 会把首帧图传进去变成改图")
    check("D3 ★ 文生图与 inpaint 同端点 /images/generations",
          "/images/generations" in body)
    check("D4 ★ 未配 key 时 _resolve_cred 明确抛错（不静默假 key）",
          "_resolve_cred(api_key, base_url)" in body)

    def _params_of(src, fname):
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == fname:
                return [a.arg for a in n.args.args]
        return None

    check("D5 ★ executors.apply_real_executors 形参含 text2img_fn",
          "text2img_fn" in (_params_of(EX_SRC, "apply_real_executors") or []))
    check("D6 ★ executors.build_executor 形参含 text2img_fn 并透传给 factory",
          "text2img_fn" in (_params_of(EX_SRC, "build_executor") or [])
          and "text2img_fn" in EX_SRC)
    check("D7 ★ canvas_graph.use_real_executors 形参含 text2img_fn 并透传",
          "text2img_fn" in (_params_of(CG_SRC, "use_real_executors") or [])
          and "text2img_fn" in CG_SRC)
    # D8：必须定位 _GraphRunWorker 类体内的 __init__（全文件第一个 __init__
    # 是别的类的 —— 上轮就栽在这个粗心上）
    wk_params = None
    for n in ast.walk(ast.parse(CP_SRC)):
        if isinstance(n, ast.ClassDef) and n.name == "_GraphRunWorker":
            for m in n.body:
                if isinstance(m, ast.FunctionDef) and m.name == "__init__":
                    wk_params = [a.arg for a in m.args.args]
    check("D8 ★ canvas_panel._GraphRunWorker.__init__ 形参含 text2img_fn",
          wk_params is not None and "text2img_fn" in wk_params,
          "params=%r（worker 收不到 fn = UI 注入断链）" % wk_params)
    tree = ast.parse(CP_SRC)
    rg = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_run_graph"), None)
    ok_e1 = rg is not None and "get_agnes_text2img_fn" in ast.dump(rg)
    check("D9 ★ _run_graph 函数体内 import 并注入 get_agnes_text2img_fn", ok_e1)


# ---------------------------------------------------------------- E UI 全链路
def _e():
    print("E 组 · UI 全链路（offscreen 真 panel，注入从按钮到执行器不断链）")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
    except Exception as e:  # noqa: BLE001
        check("E0 PySide6 可用", False, repr(e))
        return

    import agnes_bridge
    import canvas_panel as cp

    calls = []
    mock = _red_fn(calls)
    agnes_bridge.get_agnes_text2img_fn = lambda: mock          # 注入生效
    agnes_bridge.get_agnes_video_fn = lambda: (
        lambda node, asset_root, src_path, prompt: None)       # 视频立即假完成
    agnes_bridge.get_agnes_inpaint_fn = lambda: None

    work = tempfile.mkdtemp(prefix="t2i_e_")
    orig = os.getcwd()
    try:
        os.chdir(work)
        g = cp.build_demo()
        panel = cp.CanvasPanel(g)
        panel._run_graph()
        loop = QEventLoop()
        panel._run_worker.finished.connect(loop.quit)
        QTimer.singleShot(15000, loop.quit)
        loop.exec()
        check("E1 ★ mock text2img 真被 UI 链路调用", bool(calls), calls)
        node = g.nodes.get("img")
        out = node.out_assets.get("image") if node else None  # 示例图 img 输出端口名 = image
        ok_img = out and out.path and os.path.exists(out.path)
        check("E2 ★ img 节点产物是 mock 红图（注入链到执行器全通）",
              ok_img and _read_center(out.path) == (255, 0, 0),
              "center=%r" % (_read_center(out.path) if ok_img else None,))
        check("E3 产物尺寸 = mock 尺寸（非 480×270 占位）",
              ok_img and _img_size(out.path) == (64, 64),
              _img_size(out.path) if ok_img else None)
    finally:
        os.chdir(orig)


if __name__ == "__main__":
    for part in (_a, _b, _c, _d, _e):
        _run_part(part)
    print("\nPASS=%d FAIL=%d" % (CHECKED - len(FAIL), len(FAIL)))
    sys.exit(1 if FAIL else 0)
