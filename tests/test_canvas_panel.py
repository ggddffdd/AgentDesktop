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
    # A8：判据必须落在 STATUS_COLOR 字典本身，不能在整文件搜子串 ——
    # 状态色值改走 THEME["canvas_incomplete"] 后，整文件搜 "incomplete" 恒为真
    # （值里就含这个子串），判据永远绿、扰动打不红（假绿）。
    _sc = (SRC.split("STATUS_COLOR = {", 1)[1].split("}", 1)[0]
           if "STATUS_COLOR = {" in SRC else "")
    check("A8 状态色六态齐全(incomplete/cancelled)",
          all(('"%s"' % s) in _sc for s in
              ("pending", "in_progress", "completed", "failed",
               "cancelled", "incomplete")),
          "STATUS_COLOR 缺键: %r" % _sc[:120])
    check("A9 ScenePlan 往返序列化", "def from_dict" in SRC)

    # ---- Wave D（复审 #7）：占位语义要一路透传到画布 ----
    check("A10 NodeSpec 带 placeholder 字段且随 to_dict 序列化",
          "placeholder: bool = False" in SRC
          and '"placeholder": self.placeholder' in SRC,
          "不透传的话「跑通了 stub」和「真出片了」在界面上长得一模一样")
    check("A11 layout_graph 把节点占位标记透传给 NodeSpec",
          'placeholder=bool(getattr(n, "placeholder", False))' in lay)
    # 注：不能用 _slice_func("CanvasNodeItem") —— 它切到「下一个 def/class」就停，
    # 而类体里第一个 def 就是 __init__，会把真正要看的渲染代码切掉（假红/假绿）。
    # 这里按「下一个顶层 class」切，拿到整个类体。
    _cs = SRC.find("class CanvasNodeItem")
    _item = SRC[_cs:SRC.find("\nclass ", _cs)] if _cs >= 0 else ""
    # 断言「条件行 + 紧邻动作行」的组合，而不是分别搜两个串：
    # `spec.placeholder` 在状态文字那行也出现，只搜它会漏掉「圆点分支被摘掉」这种变异
    # （_perturb_canvas_panel.py 的 PL 就是这么把它打出来的）。
    check("A12 资产标记三态：占位画空心圈（Qt.NoBrush），不冒充实心绿点",
          "if spec.placeholder:\n            mark.setBrush(QBrush(Qt.NoBrush))" in _item,
          "占位物同样是「已登记」，画实心绿点等于告诉用户内容已经生成出来了")
    check("A13 节点状态文字带「占位」后缀（不只靠圆点颜色区分）",
          '" · 占位" if spec.placeholder else ""' in _item,
          "只靠颜色的话，色弱用户 / 截图里根本看不出这是占位")
    check("A14 详情面板单独报出占位产出条数（不与「已落地」混为一谈）",
          "占位产出: %d 个" in SRC)


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

    # ---- Wave D（复审 #7）：占位标记必须传到画布规格上 ----
    ph = [n.id for n in plan.nodes if n.placeholder]
    check("B13 stub 跑完的示例图：6 个节点全标为占位", len(ph) == 6, str(ph))
    check("B14 NodeSpec 往返保留 placeholder（to_dict/from_dict 不丢）",
          all(n.placeholder for n in
              cp.ScenePlan.from_dict(plan.to_dict()).nodes))
    check("B14b 占位归占位：资产仍报 registered（画布「已落地」语义不变）",
          all(n.registered for n in plan.nodes))


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
