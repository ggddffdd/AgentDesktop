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

# ---- 可选：从扰动拷贝路径加载 canvas_export（默认正常 import）----
_cx_path = os.environ.get("CX_PATH")
if _cx_path and os.path.exists(_cx_path):
    _spec = importlib.util.spec_from_file_location("canvas_export_under_test", _cx_path)
    cx = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(cx)
else:
    import canvas_export as cx

import canvas_graph as cg


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
    check("A7 工程文件带 version 字段", '"version": 1' in src)

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

    # ---------------- C 结构：工程文件结构正确 ----------------
    on_disk = json.load(open(tmp, "r", encoding="utf-8"))
    check("C1 磁盘工程文件含 graph.nodes", "graph" in on_disk and "nodes" in on_disk["graph"])
    check("C2 磁盘文件节点 id 与内存一致",
          set(on_disk["graph"]["nodes"].keys()) == orig_ids)

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
