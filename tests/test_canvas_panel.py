# -*- coding: utf-8 -*-
"""节点画布第 3 步 · 画布 UI 判据（canvas_panel.py）。

对应设计稿 §9 第 3 步验证目标「把数据模型 + 资产引用画成可见画布」。

三组判据（独立运行：python tests/test_canvas_panel.py）：
  A 静态组 —— 三坑保护法 + 布局/状态映射的**契约必须落在源码里**，逐函数切片判定；
  B 行为组 —— 真 import canvas_panel，对示例图跑 layout_graph，核对节点/边数量、
              分层展开、节点不重叠、状态色映射、run 终态、资产登记与落地标记；
  C 结构组 —— ScenePlan 往返序列化一致、边 kind 合法、画布尺寸为正。

为什么 B 用进程内 import 而不是 subprocess 起 GUI：layout_graph 是纯逻辑层、无 Qt
依赖，进程内 import 已经能验证「图→画布几何」映射正确；QGraphicsView 的真实渲染
由 canvas_panel.py 的 __main__ 预览入口人工/集成验证，不在这个无显示的判据里。
"""

import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def _hook(t, v, tb):
    print(f"\n[!] 未捕获异常：{t.__name__}: {v}")
    print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
    sys.exit(1)


sys.excepthook = _hook

SRC_PATH = os.environ.get("CANVAS_PATH") or os.path.join(ROOT, "canvas_panel.py")
SRC = open(SRC_PATH, encoding="utf-8").read()

# 扰动脚本（_perturb_canvas_panel.py）只验 A 组静态判据，用 CANVAS_STATIC=1 跳过 B/C。
STATIC_ONLY = os.environ.get("CANVAS_STATIC") == "1"


def _slice_func(name):
    r"""取某个 def/class 的源码切片（切到「缩进宽度 ≤ 本函数」的下一个 def/class）。

    结束边界用「缩进宽度」判定，而不是简单的「下一个顶层 class/def」——
    否则 layout_graph 的切片会被一直拉到文件末尾，把同级的 build_demo 一起包进来，
    导致 A6/A7 看的字符串永远存在、删了也不红（假绿）。

    做法：匹配行的前导空格数记为 indent，向后找第一个缩进宽度 <= indent
    的 class/def 作为终点——也就是「同层或外层」的下一个定义，刚好切到
    本函数的下个兄弟定义为止。
    """
    m = re.search(r"^\s*(?:class|def)\s+%s\b" % re.escape(name), SRC, re.M)
    if not m:
        return ""
    start = m.start()
    line_start = SRC.rfind("\n", 0, start) + 1
    indent = len(SRC[line_start:start]) - len(SRC[line_start:start].lstrip())
    rest = SRC[m.end():]
    nxt = re.search(r"\n[ \t]*(?:class|def)\s+\w+\b", rest)
    end = m.end() + (nxt.start() if nxt else len(rest))
    return SRC[start:end]


# =========================================================================
# A 静态组：契约必须落在源码里（逐函数切片判定，不在整文件搜关键词）
# =========================================================================
def _a():
    print("== A 静态组 ==")
    check("A1 layout_graph 函数存在", "def layout_graph" in SRC)
    check("A2 坑③高DPI setDevicePixelRatio 落点", "pix.setDevicePixelRatio(self._dpr)" in SRC)
    check("A3 坑②ffmpeg _NO_WINDOW 写法常量", "CREATE_NO_WINDOW" in SRC)
    check("A4 坑①安全输出 _emit", "def _emit" in SRC)
    check("A5 节点 registered 属性（资产落地标记）", "def registered" in SRC)

    lay = _slice_func("layout_graph")
    check("A6 分层公式 1+max(assigned)", "1 + max(assigned" in lay)
    check("A7 X 按拓扑层展开", "MARGIN + depth * col_gap" in SRC)
    check("A8 状态色六态齐全(incomplete/cancelled)",
          "incomplete" in SRC and "cancelled" in SRC)
    check("A9 ScenePlan 往返序列化", "def from_dict" in SRC)


# =========================================================================
# B 行为组：真 import canvas_panel，对示例图跑布局
# =========================================================================
def _no_overlap(nodes):
    for i in range(len(nodes)):
        a = nodes[i]
        for j in range(i + 1, len(nodes)):
            b = nodes[j]
            if (a.x < b.x + b.w and a.x + a.w > b.x and
                    a.y < b.y + b.h and a.y + a.h > b.y):
                return False
    return True


def _b():
    print("== B 行为组 ==")
    import canvas_panel as cp

    g = cp.build_demo()
    plan = cp.layout_graph(g)

    check("B1 节点数 == 6", len(plan.nodes) == 6, str(len(plan.nodes)))
    check("B2 边总数 == 7", len(plan.edges) == 7, str(len(plan.edges)))
    check("B3 数据边 == 6", sum(1 for e in plan.edges if e.kind == "data") == 6)
    check("B4 顺序边 == 1", sum(1 for e in plan.edges if e.kind == "order") == 1)

    xs = sorted(n.x for n in plan.nodes)
    check("B5 节点 X 分层单调不递减",
          all(xs[i] <= xs[i + 1] for i in range(len(xs) - 1)))

    final = [n for n in plan.nodes if n.id == "final"][0]
    check("B6 final 在最右（X 最大）", final.x == max(n.x for n in plan.nodes))

    check("B7 节点矩形两两不重叠", _no_overlap(plan.nodes))

    for s in ["pending", "in_progress", "completed", "failed", "cancelled", "incomplete"]:
        check("B8 状态色「%s」非空十六进制" % s,
              cp.status_color(s).startswith("#"))

    check("B9 run 后全 completed", all(n.status == "completed" for n in plan.nodes))
    check("B10 资产登记 6 条（每节点1产出）",
          len(g.asset_registrations()) == 6, str(len(g.asset_registrations())))

    # 第 2 步 stub 已带 stage path（_stub_stage_path）+ meta，内存 sink 下 registered 全 True
    regs = g.asset_registrations()
    check("B11 资产全部已落地(registered 全 True)",
          all(r.get("registered") for r in regs),
          str([r.get("registered") for r in regs]))
    check("B12 final 资产标记与 registered 一致", final.assets_registered == 1,
          "assets_registered=%d" % final.assets_registered)


# =========================================================================
# C 结构组
# =========================================================================
def _c():
    print("== C 结构组 ==")
    import canvas_panel as cp
    g = cp.build_demo()
    plan = cp.layout_graph(g)
    d = plan.to_dict()
    plan2 = cp.ScenePlan.from_dict(d)
    check("C1 往返一致：节点数", len(plan2.nodes) == len(plan.nodes))
    check("C2 往返一致：边数", len(plan2.edges) == len(plan.edges))
    check("C3 边 kind 合法(data/order)",
          all(e.kind in ("data", "order") for e in plan.edges))
    check("C4 画布尺寸为正", plan.width > 0 and plan.height > 0,
          "%.0f x %.0f" % (plan.width, plan.height))


if __name__ == "__main__":
    _a()
    if not STATIC_ONLY:
        _b()
        _c()
    print("\nPASS=%d FAIL=%d" % (CHECKED - len(FAIL), len(FAIL)))
    if FAIL:
        print("FAIL 项: " + "; ".join(FAIL))
        sys.exit(1)
    print("ALL GREEN")
    sys.exit(0)
