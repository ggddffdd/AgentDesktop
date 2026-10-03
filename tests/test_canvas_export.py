# -*- coding: utf-8 -*-
"""节点画布第 4 步 · 导出判据套件（纯逻辑、无 Qt）。

沿用第 1/2/3 步 A/B/C + _slice_func 模式。环境约定：
  * 默认 import canvas_export；若设 CX_PATH 则从该路径加载（供扰动脚本用）。
  * 若设 CX_CHECK=<name>，只按该判据的成败返回 exit code（供扰动脚本断言翻转）。
判据分布：A 静态（导出契约存在） / B 行为（导出-导入闭环真可用） / C 结构（工程文件结构正确）。
"""

import os
import sys
import json
import tempfile
import importlib.util

# 把项目根加入 sys.path（tests/ 下直接跑脚本时也能 import 同级模块）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---- 可选：从扰动拷贝路径加载 canvas_graph（CG_PATH，供 Port schema 变异用）----
# 必须在 import canvas_export 之前塞进 sys.modules：canvas_export 里是
# `import canvas_graph as cg`，先占位才能让两边看到同一份（被变异的）源码。
_cg_path = os.environ.get("CG_PATH")
if _cg_path and os.path.exists(_cg_path):
    _cg_spec = importlib.util.spec_from_file_location("canvas_graph", _cg_path)
    _cg_mod = importlib.util.module_from_spec(_cg_spec)
    sys.modules["canvas_graph"] = _cg_mod
    _cg_spec.loader.exec_module(_cg_mod)

# ---- 可选：从扰动拷贝路径加载 canvas_export（默认正常 import）----
_cx_path = os.environ.get("CX_PATH")
if _cx_path and os.path.exists(_cx_path):
    _spec = importlib.util.spec_from_file_location("canvas_export_under_test", _cx_path)
    cx = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(cx)
else:
    import canvas_export as cx

import canvas_graph as cg


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return path


def _tmp(name):
    return os.path.join(tempfile.gettempdir(), name)


def _expect_import_error(path, *must_contain):
    """导入该文件必须抛 CanvasImportError，且报错文本要含指定定位片段。

    返回 (是否抛对类型, 异常文本)。
    """
    try:
        cx.import_project_json(path)
    except cx.CanvasImportError as e:
        msg = str(e)
        missing = [m for m in must_contain if m not in msg]
        return (not missing), (msg if not missing else "缺定位 %s :: %s" % (missing, msg))
    except Exception as e:  # noqa: BLE001
        return False, "抛错类型不对: %s: %s" % (type(e).__name__, e)
    return False, "未抛错"


def _ok_graph_json(path, nid="a", ntype="gen_image",
                   inputs=None, outputs=None, extra=None):
    """造一份最小可用工程文件（默认单节点无输入 + 一个 image 输出）。"""
    obj = {
        "version": 2,
        "generator": "test",
        "graph": {
            "nodes": {nid: {
                "id": nid, "type": ntype,
                "status": "pending",
                "inputs": inputs if inputs is not None else {},
                "outputs": outputs if outputs is not None else
                {"o": {"type": "image", "multi": False}},
                "config": {}, "placeholder": False, "pos": None}},
            "data_edges": [], "order_edges": [],
        },
    }
    if extra:
        obj["graph"].update(extra)
    return _write_json(path, obj)


_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    ok = "OK " if cond else "FAIL"
    _detail = f" :: {detail}" if (detail and not cond) else ""
    print(f"  [{ok}] {name}{_detail}")


def _slice_func(src, start_pat, end_pat=None):
    """取 src 中某函数/类的源码切片（对齐第 1 步判据写法）。"""
    import re
    out, started = [], False
    for ln in src.splitlines():
        if not started:
            if re.match(start_pat, ln):
                started = True
                out.append(ln)
        else:
            if end_pat and re.match(end_pat, ln):
                break
            out.append(ln)
    return "\n".join(out)


def _src():
    return open(cx.__file__, "r", encoding="utf-8").read()


def main():
    # ---------------- A 静态：导出契约存在 ----------------
    src = _src()
    check("A1 导出工程 JSON 函数存在", hasattr(cx, "export_project_json"))
    check("A2 导入工程 JSON 函数存在", hasattr(cx, "import_project_json"))
    check("A3 渲染 SVG 函数存在", hasattr(cx, "export_svg"))
    check("A4 渲染 PNG 函数存在", hasattr(cx, "export_png"))
    check("A5 工程文件写入 graph 字段", '"graph": g.to_dict()' in src)
    check("A6 export_svg 复用 layout_graph",
          "layout_graph" in _slice_func(src, r"^def export_svg\(", r"^def "))
    check("A7 工程文件带 version 字段（v2：端口带 multi）", '"version": 2' in src)

    # ---- Wave C：#4 导入缺校验 / #5 非法 port_type 会炸 ----
    check("A8 提供专门的导入异常类型 CanvasImportError（是 ValueError 子类）",
          hasattr(cx, "CanvasImportError")
          and issubclass(cx.CanvasImportError, ValueError),
          "跟「自己代码 bug」混成同一个 ValueError，UI 只能一律说「导入失败请重试」")
    check("A9 导入前先做结构预检 _validate_project（错误带文件+节点/边定位）",
          hasattr(cx, "_validate_project")
          and "_validate_project(gd, path)" in src
          and "def _split_endpoint" in src and "def _require_nodes" in src,
          "不预检的话，非法 port_type 会以「非法端口类型: 'imgae'」直接炸，说不出是哪个文件哪个节点")
    check("A10 导入失败不返回半成品图（构建过程包成 CanvasImportError）",
          "raise CanvasImportError" in src
          and src.count("raise CanvasImportError") >= 4,
          "半成品图流出去，用户以为导入成功了")
    _graph_src = open(cg.__file__, "r", encoding="utf-8").read()
    check("A11 端口 schema 带 multi（Port.to_dict / port_from_spec 成对存在）",
          '"multi": bool(self.multi)' in _graph_src
          and "def port_from_spec" in _graph_src,
          "v1 只存端口类型字符串 → multi 在多入端口上静默丢失")

    # ---------------- B 行为：导出-导入闭环真可用 ----------------
    g = cg.build_sample_graph()
    g.run({})
    orig_ids = set(g.nodes.keys())
    orig_data_edges = len(g.data_edges)

    tmp = os.path.join(tempfile.gettempdir(), "canvas_export_test.json")
    data = cx.export_project_json(g, tmp)
    check("B1 export_project_json 返回含 graph 的 dict", isinstance(data, dict) and "graph" in data)

    g2 = cx.import_project_json(tmp)
    check("B2 导入还原全部节点 id", set(g2.nodes.keys()) == orig_ids)
    check("B3 导入还原数据边数量", len(g2.data_edges) == orig_data_edges)
    # 导入后能再 run 到 completed（stub 执行器可用）
    g2.run({})
    check("B4 导入图可重跑至 completed",
          all(n.status == "completed" for n in g2.nodes.values()))

    # B10（Wave B #2）：导入必须把**底层依赖**一并重建。
    # 只还原边、不重建依赖的话，run() 会把「该串行的流水线」当成可并行的散点 ——
    # 画布上完全看不出差别，但调度已经错了（B4 只看「都 completed」，抓不到这个）。
    def _pairs(gg):
        return {(b, td["id"]) for td in gg._tg.task_list()
                for b in td.get("blockedBy", [])}

    check("B10 导入后底层依赖与示例图完全一致（边集即事实源）",
          _pairs(g2) == _pairs(cg.build_sample_graph()),
          "%s vs %s" % (sorted(_pairs(g2)), sorted(_pairs(cg.build_sample_graph()))))

    # 手动位置经 导出→导入 持久化（可编辑画布的关键）
    g.nodes["img"].pos = (500.0, 300.0)
    tmp2 = os.path.join(tempfile.gettempdir(), "canvas_export_pos.json")
    cx.export_project_json(g, tmp2)
    g3 = cx.import_project_json(tmp2)
    check("B5 手动位置经导出-导入保留",
          g3.nodes["img"].pos == (500.0, 300.0))

    # SVG 渲染：合法矢量图 + 含节点标签
    tmp_svg = os.path.join(tempfile.gettempdir(), "canvas_export_test.svg")
    plan = cx.export_svg(g, tmp_svg)
    svg_txt = open(tmp_svg, "r", encoding="utf-8").read()
    check("B6 SVG 以 <svg 开头、</svg> 结尾",
          svg_txt.strip().startswith("<svg") and svg_txt.strip().endswith("</svg>"))
    check("B7 SVG 含节点标签文本", "源·Prompt" in svg_txt or "生图" in svg_txt)
    # 手动位置被 layout_graph 尊重 → SVG 出现该 x 坐标
    check("B8 SVG 含手动位置 x=500", 'x="500.0"' in svg_txt)
    check("B9 渲染 ScenePlan 尺寸 > 0", plan.width > 0 and plan.height > 0)

    # ------------------------------------------------------------------
    # Wave C（复审 #4 / #5）：导入必须校验，且报错要指得出「哪个文件哪一处」
    # ------------------------------------------------------------------
    # B11 非法 port_type（旧行为：直接炸一句「非法端口类型: 'imgae'」，不说是谁）
    p_bad_port = _ok_graph_json(_tmp("cx_bad_port.json"),
                                inputs={"i": {"type": "imgae", "multi": False}})
    ok11, d11 = _expect_import_error(p_bad_port, "cx_bad_port.json", "a", "i", "imgae")
    check("B11 非法 port_type → CanvasImportError，且报错含文件+节点+端口名",
          ok11, d11)

    # B12 边引用了不存在的节点
    p_ghost = _write_json(_tmp("cx_ghost.json"), {
        "graph": {
            "nodes": {"a": {"id": "a", "type": "gen_image", "status": "pending",
                            "inputs": {}, "outputs": {"o": "image"},
                            "config": {}, "pos": None}},
            "data_edges": [{"from": "a.o", "to": "ghost.i", "label": ""}],
            "order_edges": [],
        }})
    ok12, d12 = _expect_import_error(p_ghost, "cx_ghost.json", "data_edges[0]", "ghost")
    check("B12 边引用不存在的节点 → CanvasImportError（含边下标 + 缺失节点名）",
          ok12, d12)

    # B13 边的端点没写「节点.端口」（旧行为：unpack 崩溃 "not enough values to unpack"）
    p_nodot = _write_json(_tmp("cx_nodot.json"), {
        "graph": {
            "nodes": {"a": {"id": "a", "type": "gen_image", "status": "pending",
                            "inputs": {}, "outputs": {"o": "image"},
                            "config": {}, "pos": None}},
            "data_edges": [{"from": "a", "to": "a.o", "label": ""}],
            "order_edges": [],
        }})
    ok13, d13 = _expect_import_error(p_nodot, "cx_nodot.json", "节点.端口")
    check("B13 边端点缺「节点.端口」格式 → CanvasImportError（不是 unpack 崩溃）",
          ok13, d13)

    # B14 结构本身就是错的（nodes 是数组）
    p_badshape = _write_json(_tmp("cx_badshape.json"),
                             {"graph": {"nodes": ["a"], "data_edges": [],
                                        "order_edges": []}})
    ok14, d14 = _expect_import_error(p_badshape, "cx_badshape.json", "nodes")
    check("B14 nodes 不是对象 → CanvasImportError（含文件名）", ok14, d14)

    # B15 文件根本不是 JSON
    p_notjson = _tmp("cx_notjson.json")
    with open(p_notjson, "w", encoding="utf-8") as f:
        f.write("{这不是 JSON")
    ok15, d15 = _expect_import_error(p_notjson, "cx_notjson.json", "JSON")
    check("B15 非 JSON 文件 → CanvasImportError（带文件名，不是裸 JSONDecodeError）",
          ok15, d15)

    # B16 顺序边端点缺失
    p_order = _write_json(_tmp("cx_order.json"), {
        "graph": {
            "nodes": {"a": {"id": "a", "type": "gen_image", "status": "pending",
                            "inputs": {}, "outputs": {"o": "image"},
                            "config": {}, "pos": None}},
            "data_edges": [],
            "order_edges": [{"from": "a", "to": "nope", "reason": ""}],
        }})
    ok16, d16 = _expect_import_error(p_order, "cx_order.json", "order_edges[0]", "nope")
    check("B16 顺序边引用不存在节点 → CanvasImportError（含边下标）", ok16, d16)

    # B17 向后兼容：v1 旧工程文件（端口是裸字符串）照样能导入
    p_v1 = _write_json(_tmp("cx_legacy_v1.json"), {
        "version": 1, "generator": "old",
        "graph": {
            "nodes": {"m": {"id": "m", "type": "promo_fx", "status": "pending",
                            "inputs": {"frames": "image"},
                            "outputs": {"video": "video"},
                            "config": {}, "pos": None}},
            "data_edges": [], "order_edges": [],
        }})
    try:
        g_v1 = cx.import_project_json(p_v1)
        check("B17 v1 旧工程文件（端口为裸字符串）仍可导入，multi 默认 False",
              g_v1.nodes["m"].inputs["frames"].multi is False
              and g_v1.nodes["m"].outputs["video"].port_type == "video")
    except Exception as e:  # noqa: BLE001
        check("B17 v1 旧工程文件向后兼容", False, "%s: %s" % (type(e).__name__, e))

    # ---------------- C 结构：工程文件结构正确 ----------------
    on_disk = json.load(open(tmp, "r", encoding="utf-8"))
    check("C1 磁盘工程文件含 graph.nodes", "graph" in on_disk and "nodes" in on_disk["graph"])
    check("C2 磁盘文件节点 id 与内存一致",
          set(on_disk["graph"]["nodes"].keys()) == orig_ids)
    check("C4 磁盘工程文件版本为 2（端口带 multi）", on_disk.get("version") == 2,
          str(on_disk.get("version")))

    # C3 multi 端口经「导出 → 导入」必须保住（v1 只存端口类型串，multi 会静默丢）
    gm = cg.CanvasGraph()
    gm.add_node(cg.CanvasNode("p1", "gen_image",
                              outputs={"o": cg.Port("o", "image")}))
    gm.add_node(cg.CanvasNode("acc", "promo_fx",
                              inputs={"frames": cg.Port("frames", "image",
                                                        multi=True)}))
    gm.connect_data("p1", "o", "acc", "frames")
    tmp_multi = _tmp("cx_multi.json")
    cx.export_project_json(gm, tmp_multi)
    gm2 = cx.import_project_json(tmp_multi)
    check("C3 multi 端口经导出→导入保留（否则多入退化成单入，画布上看不出来）",
          gm2.nodes["acc"].inputs["frames"].multi is True
          and len(gm2.data_edges) == 1,
          str(gm2.nodes["acc"].inputs["frames"]))

    return _results


if __name__ == "__main__":
    main()
    target = os.environ.get("CX_CHECK")
    if target:
        for name, ok in _results:
            if name.startswith(target):
                sys.exit(0 if ok else 1)
        sys.exit(2)  # 目标判据未找到
    failed = [n for n, ok in _results if not ok]
    total = len(_results)
    print("\n" + "=" * 56)
    print(f"  导出判据套件：{total - len(failed)}/{total} 通过"
          + ("  ALL GREEN" if not failed else f"  FAIL={failed}"))
    print("=" * 56)
    sys.exit(1 if failed else 0)
