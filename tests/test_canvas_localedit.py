# -*- coding: utf-8 -*-
"""节点画布阶段 B（图片局部编辑）· 判据套件。

验证目标（设计稿 §9 阶段 B / CANVAS_RESEARCH_REPORT §4）：
  A 静态：数据层 local_edits 方法 / 三类撤销命令 / 遮罩编辑器 / 对话框 /
          属性面板按钮 / 工具栏入口 / export_svg 标记符号与契约片段存在。
  B 行为（无 GUI 实例化 widget，纯数据层 + 命令类 redo/undo + 纯几何）：
          1) add_local_edit 追加 + 校验（空 instruction 拒收）
          2) 导出→导入 local_edits 闭环保留（可编辑画布存盘/读回不丢编辑）
          3) Add/Remove/EditLocalEditCommand 增删改 + 撤销还原
          4) norm_rect 归一化几何正确（整图/rect）
          5) 多条编辑顺序保持 + id 递增
          6) update/remove/get 行为正确
          7) region_svg_overlay + export_svg 标记已局部编辑节点
  C 结构：切片命令类 / 数据层，确认确实绑定底层 set_local_edits / local_edits。

判据可在无 GUI 环境运行：命令类继承 QUndoCommand（QtGui）无需 QApplication；
Qt 部件（MaskEditorWidget / LocalEditDialog）仅运行时实例化，判据不创建。
支持 CX_PATH（注入 sys.path）与 CX_CHECK（按判据首 token 过滤）。
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

import canvas_graph as cg                       # noqa: E402
import canvas_panel as cp                        # noqa: E402
import canvas_export as ce                        # noqa: E402

PANEL_SRC = open(os.path.join(ROOT, "canvas_panel.py"), encoding="utf-8").read()
GRAPH_SRC = open(os.path.join(ROOT, "canvas_graph.py"), encoding="utf-8").read()
EXPORT_SRC = open(os.path.join(ROOT, "canvas_export.py"), encoding="utf-8").read()

results = []


def check(name, ok, detail=""):
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


# =========================================================================
# A 静态：符号与契约片段存在性
# =========================================================================
def section_a():
    print("\n===== A. 静态契约 =====")
    # 数据层方法
    for m in ("get_local_edits", "set_local_edits", "add_local_edit",
              "update_local_edit", "remove_local_edit", "_make_edit_record"):
        check("A1 数据层方法 %s 存在" % m, ("def %s" % m) in GRAPH_SRC)
    check("A2 数据层 VALID_LOCAL_EDIT_MODES 定义",
          "VALID_LOCAL_EDIT_MODES" in GRAPH_SRC)
    # 撤销命令类
    for sym in ("class AddLocalEditCommand", "class RemoveLocalEditCommand",
                "class EditLocalEditCommand"):
        check("A3 撤销命令 %s 存在" % sym, sym in PANEL_SRC)
    # 视图符号
    for sym in ("class MaskEditorWidget", "class LocalEditDialog",
                "def node_supports_local_edit", "def region_svg_overlay",
                "def norm_rect"):
        check("A4 视图符号 %s 存在" % sym, sym in PANEL_SRC)
    # 属性面板 + 工具栏接入
    check("A5 属性面板 btn_local + 回调 local_edit_callback",
          "btn_local" in PANEL_SRC and "local_edit_callback" in PANEL_SRC)
    check("A6 CanvasPanel 工具栏 _open_local_edit / _open_local_edit_for",
          "_open_local_edit" in PANEL_SRC and "_open_local_edit_for" in PANEL_SRC)
    check("A7 export_svg 引用 region_svg_overlay",
          "region_svg_overlay" in EXPORT_SRC)

    # 关键契约（_make_edit_record 为类内方法，切片工具只取顶层符号，
    # 故此处直接在校验源码中查契约字符串，等价证明校验逻辑落地）
    check("A8 _make_edit_record 校验 instruction 非空",
          "局部编辑指令 instruction 不能为空" in GRAPH_SRC)
    check("A9 _make_edit_record 校验 mode ∈ VALID_LOCAL_EDIT_MODES",
          "if mode not in self.VALID_LOCAL_EDIT_MODES" in GRAPH_SRC)
    addc = _slice_func(PANEL_SRC, "AddLocalEditCommand")
    check("A10 切片 AddLocalEditCommand 含 set_local_edits",
          "set_local_edits" in addc)


# =========================================================================
# B 行为：纯数据层 + 命令类 redo/undo + 纯几何 + 导出标记
# =========================================================================
def section_b():
    print("\n===== B. 行为（无 GUI）=====")
    g = cg.build_sample_graph()
    g.run({})

    # B1 add_local_edit 追加一条 + to_dict 含 local_edits + id 形如 le0001
    idx, eid = g.add_local_edit("img", {
        "target": "image", "mode": "transform",
        "region": {"type": "rect", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3},
        "instruction": "背景换成蓝天"})
    cfg = g.nodes["img"].to_dict()["config"]
    le = cfg.get("local_edits")
    check("B1 add_local_edit 写入 config.local_edits",
          isinstance(le, list) and len(le) == 1, "len=%d" % (len(le) if le else 0))
    check("B1b 记录 id 形如 le0001", le and le[0]["id"] == "le0001",
          "id=%r" % (le[0]["id"] if le else None))
    check("B1c region/instruction 保留",
          le and le[0]["region"]["type"] == "rect"
          and le[0]["instruction"] == "背景换成蓝天")

    # B2 add_local_edit 拒绝空 instruction（抛 ValueError）
    try:
        g.add_local_edit("img", {"instruction": ""})
        check("B2 空 instruction 被拒收", False, "未抛异常")
    except ValueError:
        check("B2 空 instruction 被拒收", True)
    except Exception as e:
        check("B2 空 instruction 被拒收", False, "异常类型错: %r" % e)

    # B3 export→import 保留 local_edits（可编辑画布存盘/读回不丢编辑）
    g2 = cg.build_sample_graph()
    g2.run({})
    g2.add_local_edit("img", {
        "target": "image", "mode": "inpaint",
        "region": {"type": "polygon", "points": [[0.2, 0.2], [0.8, 0.3], [0.5, 0.9]]},
        "instruction": "把人物换成西装", "params": {"strength": 0.5}})
    tmp = os.path.join(tempfile.gettempdir(), "canvas_localedit_roundtrip.json")
    ce.export_project_json(g2, tmp)
    g3 = ce.import_project_json(tmp)
    imp = g3.nodes["img"].config.get("local_edits")
    check("B3 导出→导入 local_edits 保留",
          isinstance(imp, list) and len(imp) == 1
          and imp[0]["instruction"] == "把人物换成西装"
          and imp[0]["mode"] == "inpaint"
          and imp[0]["region"]["type"] == "polygon",
          "imported=%r" % (imp,))
    os.remove(tmp)

    # B4 AddLocalEditCommand.redo 追加 / undo 还原
    g4 = cg.build_sample_graph()
    g4.run({})
    cmd = cp.AddLocalEditCommand(
        g4, None, "img",
        {"target": "image", "mode": "transform",
         "region": {"type": "rect", "x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
         "instruction": "调亮左上角"})
    cmd.redo()
    check("B4 AddLocalEditCommand.redo 追加一条",
          len(g4.get_local_edits("img")) == 1)
    cmd.undo()
    check("B4b .undo 还原为空", len(g4.get_local_edits("img")) == 0)

    # B5 RemoveLocalEditCommand.redo 删除 / undo 还原
    g5 = cg.build_sample_graph()
    g5.run({})
    g5.add_local_edit("img", {"instruction": "去掉水印"})
    before = len(g5.get_local_edits("img"))
    rcmd = cp.RemoveLocalEditCommand(g5, None, "img", 0)
    rcmd.redo()
    check("B5 RemoveLocalEditCommand.redo 删一条",
          len(g5.get_local_edits("img")) == before - 1)
    rcmd.undo()
    check("B5b .undo 还原", len(g5.get_local_edits("img")) == before)

    # B6 EditLocalEditCommand.redo 改 / undo 还原
    g6 = cg.build_sample_graph()
    g6.run({})
    g6.add_local_edit("img", {"instruction": "原指令", "mode": "transform"})
    ecmd = cp.EditLocalEditCommand(
        g6, None, "img", 0,
        {"instruction": "新指令", "mode": "inpaint",
         "region": {"type": "rect", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}})
    ecmd.redo()
    rec = g6.get_local_edits("img")[0]
    check("B6 EditLocalEditCommand.redo 改指令+模式",
          rec["instruction"] == "新指令" and rec["mode"] == "inpaint")
    ecmd.undo()
    rec2 = g6.get_local_edits("img")[0]
    check("B6b .undo 还原指令", rec2["instruction"] == "原指令")

    # B7 norm_rect 几何（整图 / rect）
    check("B7 norm_rect 整图=全幅",
          cp.norm_rect(None, 100, 80) == (0.0, 0.0, 100.0, 80.0))
    check("B7b norm_rect rect 归一化正确",
          cp.norm_rect({"type": "rect", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
                       100, 80) == (10.0, 16.0, 30.0, 32.0))

    # B8 多条编辑顺序保持 + id 递增
    g7 = cg.build_sample_graph()
    g7.run({})
    for i, txt in enumerate(("编辑一", "编辑二", "编辑三")):
        g7.add_local_edit("img", {"instruction": txt})
    les = g7.get_local_edits("img")
    check("B8 多条编辑顺序保持 + id 递增",
          [e["instruction"] for e in les] == ["编辑一", "编辑二", "编辑三"]
          and [e["id"] for e in les] == ["le0001", "le0002", "le0003"])

    # B9 get_local_edits 返回复制（改副本不影响节点）
    snap = g7.get_local_edits("img")
    snap.append({"id": "x", "instruction": "篡改"})
    check("B9 get_local_edits 返回复制（不被外部改污染）",
          len(g7.get_local_edits("img")) == 3)

    # B10 update / remove 行为
    g7.update_local_edit("img", 1, {"instruction": "编辑二改", "mode": "inpaint"})
    check("B10 update_local_edit 改第2条",
          g7.get_local_edits("img")[1]["instruction"] == "编辑二改")
    removed = g7.remove_local_edit("img", 0)
    check("B10b remove_local_edit 删第1条 + 返回记录",
          removed["instruction"] == "编辑一"
          and len(g7.get_local_edits("img")) == 2)

    # B11 region_svg_overlay + export_svg 标记
    ov = cp.region_svg_overlay({"type": "rect", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2},
                                0, 0, 100, 100)
    check("B11 region_svg_overlay 返回红色虚线遮罩", "#D93025" in ov)
    ov_whole = cp.region_svg_overlay(None, 0, 0, 100, 100)
    check("B11b 整图 region 也标记", "#D93025" in ov_whole)

    # B12 export_svg 含「局部编辑」角标 + 遮罩
    g8 = cg.build_sample_graph()
    g8.run({})
    g8.add_local_edit("img", {
        "target": "image", "mode": "transform",
        "region": {"type": "rect", "x": 0.2, "y": 0.2, "w": 0.3, "h": 0.3},
        "instruction": "局部调色"})
    tmp2 = os.path.join(tempfile.gettempdir(), "canvas_localedit_svg.svg")
    ce.export_svg(g8, tmp2)
    svg = open(tmp2, encoding="utf-8").read()
    check("B12 export_svg 标记局部编辑节点",
          "局部编辑" in svg and "#D93025" in svg)
    os.remove(tmp2)


# =========================================================================
# C 结构：命令类 / 数据层确实绑定底层方法（切片非空转）
# =========================================================================
def section_c():
    print("\n===== C. 结构绑定 =====")
    addc = _slice_func(PANEL_SRC, "AddLocalEditCommand")
    remc = _slice_func(PANEL_SRC, "RemoveLocalEditCommand")
    editc = _slice_func(PANEL_SRC, "EditLocalEditCommand")
    check("C1 切片 AddLocalEditCommand 含 set_local_edits", "set_local_edits" in addc)
    check("C2 切片 RemoveLocalEditCommand 含 set_local_edits", "set_local_edits" in remc)
    check("C3 切片 EditLocalEditCommand 含 set_local_edits", "set_local_edits" in editc)
    gslice = _slice_func(GRAPH_SRC, "CanvasGraph")
    check("C4 切片 CanvasGraph 含 local_edits 字段引用",
          "local_edits" in gslice)
    ns = _slice_func(PANEL_SRC, "node_supports_local_edit")
    check("C5 切片 node_supports_local_edit 含 _IMAGE_PORT_TYPES",
          "_IMAGE_PORT_TYPES" in ns)


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
    # CX_CHECK：按判据首 token 过滤（外部 harness 用）
    fp = os.environ.get("CX_CHECK")
    if fp:
        results = [(n, ok) for n, ok in results if n.split()[0].startswith(fp)]
    passed = sum(1 for _, ok in results if ok)
    failed = len(results) - passed
    print("\n===== 判据汇总 =====")
    print("总计 %d 项，通过 %d，失败 %d" % (len(results), passed, failed))
    # run_all 统计约定第 3 条：必须是**纯数字** PASS=/FAIL= 行，且要排在下方的
    # `FAIL=<名字列表>` 之前 —— 那种列表形态正则取不到数字，整条统计会被判 EMPTY。
    print("PASS=%d FAIL=%d" % (passed, failed))
    if failed:
        print("FAIL=" + ",".join(n for n, ok in results if not ok))
        sys.exit(1)
    print("ALL GREEN")
