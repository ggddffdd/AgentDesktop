# -*- coding: utf-8 -*-
"""节点画布第 1 步 · 数据模型判据（canvas_graph.py / demo_canvas_cli.py）。

对应设计稿 §9 第 1 步验证目标「在 CLI 里用代码建出一张图并跑通（无 UI）」。

三组判据（独立运行：python tests/test_canvas_model.py）：
  A 静态组 —— 数据模型的**契约必须落在源码里**（端口校验 / 隐含顺序边 / 状态枚举
                沿用 / 类型兼容映射 / 多入多出），逐函数切片判定，不在整文件搜关键词；
  B 行为组 —— 真 import canvas_graph + demo_canvas_cli，建图 run 到全 completed，
                核对拓扑、数据边隐含顺序边、上游资产注入、类型不匹配即拒；
  C 结构组 —— 示例图结构断言（节点数 / 边数 / 标签 / 去重 / 类型兼容具体组合）。

为什么 B 用进程内 import 而不是 subprocess 起 demo_cli：本模块纯数据层、无 Qt，
进程内 import 已经能验证「无 UI 建图跑通」；subprocess 版 demo_cli 作为 B 末项双保险。
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

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

SRC_PATH = os.environ.get("CANVAS_PATH") or os.path.join(ROOT, "canvas_graph.py")
SRC = open(SRC_PATH, encoding="utf-8").read()

# 扰动脚本（_perturb_canvas_model.py）只验 A 组静态判据，用 CANVAS_STATIC=1 跳过 B/C。
STATIC_ONLY = os.environ.get("CANVAS_STATIC") == "1"


def _slice_func(name):
    r"""取某个 def/class 的源码切片（切到「缩进宽度 ≤ 本函数」的下一个 def/class）。

    结束边界用「缩进宽度」判定，而不是简单的「下一个顶层 class/def」——
    否则 connect_data 的切片会被一直拉到文件末尾的 build_sample_graph，
    把同类的 connect_order（里面也有 self._tg.depend / _order_seen）一起包进来，
    导致 A6/A7 看的字符串永远存在、删了也不红（假绿）。

    做法：匹配行的前导空格数记为 indent，向后找第一个缩进宽度 <= indent
    的 class/def 作为终点——也就是「同层或外层」的下一个定义，刚好切到
    本方法的下个兄弟方法为止。
    """
    m = re.search(r"^\s*(?:class|def)\s+%s\b" % re.escape(name), SRC, re.M)
    if not m:
        return ""
    # 起始正则把前导缩进也吃进了 m.group(0)，故缩进宽度直接由 group(0) 的前导空白算，
    # 不能再用 SRC[line_start:m.start()]（那里 m.start() 已落到行首空格，算出来恒为 0）。
    indent = len(m.group(0)) - len(m.group(0).lstrip())
    nxt = None
    for nm in re.finditer(r"^[ \t]*(?:class|def)\s+\w+", SRC[m.end():], re.M):
        ind = len(nm.group(0)) - len(nm.group(0).lstrip())
        if ind <= indent:
            nxt = nm
            break
    return SRC[m.end(): m.end() + nxt.start()] if nxt else SRC[m.end():]



# ==========================================================================
# A 组：静态 —— 数据模型契约
# ==========================================================================
print("\n[A] 静态：数据模型契约必须在源码里")

# 端口类型校验：Port.__post_init__ 拦截非法 port_type
PP = _slice_func("Port")
check("A1 Port 拦截非法 port_type", "__post_init__" in PP
      and "not in VALID_PORT_TYPES" in PP,
      "连错类型不拦，等跑一半才炸就晚了")

# 节点类型校验：CanvasNode.__init__ 拦截未知 node_type
CN = _slice_func("CanvasNode")
check("A2 CanvasNode 拦截未知 node_type", "node_type not in NODE_TYPES" in CN)

# connect_data 校验端口存在（from_port / to_port 必须存在）
CD = _slice_func("connect_data")
check("A3 connect_data 校验上游输出端口存在",
      'fn.outputs.get(from_port)' in CD and "不存在输出端口" in CD)
check("A4 connect_data 校验下游输入端口存在",
      'tn.inputs.get(to_port)' in CD and "不存在输入端口" in CD)

# 类型兼容校验：连线时即拒
check("A5 connect_data 做类型兼容校验",
      "fp.port_type not in _PORT_ACCEPTS[tp.port_type]" in CD,
      "clip→image 这类错接必须连线时就报错")

# 数据边自动隐含顺序边：connect_data 内部调 self._tg.depend
check("A6 数据边自动隐含顺序边（内部调 _tg.depend）",
      "self._tg.depend(to_node, from_node)" in CD,
      "否则 TaskGraph.run 不认数据边、调度断链")

# 去重：同一对节点只 depend 一次（_order_seen）
check("A7 隐含顺序边按 (from,to) 去重（_order_seen）",
      "self._order_seen" in CD and "if key not in self._order_seen" in CD,
      "否则同一对节点被 depend 两次")

# 节点状态枚举沿用 Task.status（不自创）
check("A8 节点 status 初值用 pending（沿用 Task.status 枚举）",
      'self.status = "pending"' in CN)
check("A9 文档声明沿用 Task.status 六态",
      "pending / in_progress / completed / failed / cancelled / incomplete"
      in SRC)

# 多入多出支持：Port.multi 标志存在
check("A10 Port 有多入多出标志 multi", "multi: bool = False" in PP)

# 类型兼容映射是显式 dict（不是写死在 if 里），clip→video 允许、clip→image 拒绝
check("A11 类型兼容映射是显式 _PORT_ACCEPTS dict",
      "_PORT_ACCEPTS" in SRC and '"video": {"clip", "video", "final"}' in SRC)
check("A12 image 端口接受 scene/character_views/keyframe（接资产库引用）",
      '"image": {"image", "scene", "character_views", "keyframe"}' in SRC)

# 入口：build_sample_graph 与 demo 用的 build_and_run
check("A13 提供 build_sample_graph 工厂（无 UI 建示例图）",
      "def build_sample_graph" in SRC)

# 单入端口禁止静默覆盖（Wave A #3）：connect_data 必须拦第二条「不同来源」的数据边
check("A14 connect_data 拒绝单入端口（multi=False）接第二条不同来源的数据边",
      "if not tp.multi:" in CD and "拒绝静默覆盖" in CD,
      "否则 _incoming_assets 后写覆盖，静默丢弃在先输入（用户无感知）")
check("A15 connect_data 对同一四元组重复连接幂等（不重复建边）",
      "(from_node, from_port) in existing" in CD and "existing = [" in CD)
check("A16 _incoming_assets 对 multi 端口收成列表（不覆盖）",
      'getattr(port, "multi", False)' in _slice_func("_incoming_assets")
      and ".append(a)" in _slice_func("_incoming_assets"),
      "multi=True 端口必须累积成 list，否则多入等于白标")


# ==========================================================================
# B/C 组：行为 + 结构
# ==========================================================================
HAS_MOD = importlib.util.find_spec("canvas_graph") is not None

if STATIC_ONLY:
    print("\n[B][C] 行为/结构组：按 CANVAS_STATIC 跳过（扰动脚本只验静态判据）")

elif not HAS_MOD:
    print("\n[B] 行为：跳过（无法 import canvas_graph）")
    print("  [SKIP] 建图 / run / 拓扑 / 类型不匹配拒绝")

else:
    import canvas_graph as cg

    print("\n[B] 行为：真 import canvas_graph 建图跑通（无 UI）")
    try:
        g = cg.build_sample_graph()
        check("B1 示例图可建（6 节点）", len(g.nodes) == 6, f"实际 {len(g.nodes)}")
        g.run({})
        statuses = {nid: n.status for nid, n in g.nodes.items()}
        check("B2 全部节点 completed", all(s == "completed" for s in statuses.values()),
              str({k: v for k, v in statuses.items() if v != "completed"}))
        check("B3 拓扑顺序正确（src→img→vid→twin→promo→final）",
              g.topo() == ["src", "img", "vid", "twin", "promo", "final"],
              str(g.topo()))
    except Exception as e:  # noqa: BLE001
        check("B1-B3 建图+run 不抛异常", False, f"{type(e).__name__}: {e}")

    # 数据边隐含顺序边：TaskGraph 依赖里应包含每条数据边的 (from→to)
    try:
        g2 = cg.build_sample_graph()
        implied = {(e.from_node, e.to_node) for e in g2.data_edges}
        tg_deps = set()
        for td in g2._tg.task_list():
            for b in td.get("blockedBy", []):
                tg_deps.add((b, td["id"]))
        missing = [p for p in implied if p not in tg_deps]
        check("B4 每条数据边都已隐含为 TaskGraph 顺序依赖", not missing, str(missing))
    except Exception as e:  # noqa: BLE001
        check("B4 数据边隐含顺序边核对", False, f"{type(e).__name__}: {e}")

    # 类型不匹配即拒：clip 输出接 image 输入应 ValueError
    try:
        bad = cg.CanvasGraph()
        a = cg.CanvasNode("a", "gen_video",
                          outputs={"clip": cg.Port("clip", "clip")})
        b = cg.CanvasNode("b", "gen_image",
                          inputs={"image": cg.Port("image", "image")})
        bad.add_node(a); bad.add_node(b)
        raised = False
        try:
            bad.connect_data("a", "clip", "b", "image")
        except ValueError:
            raised = True
        check("B5 类型不兼容连线即拒（clip→image 抛 ValueError）", raised)
    except Exception as e:  # noqa: BLE001
        check("B5 类型不兼容连线即拒", False, f"{type(e).__name__}: {e}")

    # 多入：成片节点 main+promo 都接到 video
    try:
        g3 = cg.build_sample_graph()
        final_node = g3.nodes["final"]
        check("B6 成片节点支持多入（main+promo 两个 video 端口）",
              set(final_node.inputs) == {"main", "promo"}
              and all(p.port_type == "video" for p in final_node.inputs.values()))
        # 数据边把 twin/main 与 promo/promo 都接到 final
        to_final = [e for e in g3.data_edges if e.to_node == "final"]
        check("B7 成片节点两入都接到了数据边",
              {e.to_port for e in to_final} == {"main", "promo"})
    except Exception as e:  # noqa: BLE001
        check("B6-B7 多入结构", False, f"{type(e).__name__}: {e}")

    # 上游资产注入：数据边把上游 out_assets 注入下游 in_assets
    try:
        g4 = cg.build_sample_graph()
        g4.run({})
        img_node = g4.nodes["img"]
        check("B8 上游节点产出了 out_assets",
              any(a is not None for a in img_node.out_assets.values()))
        vid_in = g4.nodes["vid"].in_assets.get("image")
        check("B9 数据边把上游资产注入下游 in_assets", vid_in is not None,
              f"vid.image = {vid_in}")
    except Exception as e:  # noqa: BLE001
        check("B8-B9 资产注入", False, f"{type(e).__name__}: {e}")

    # 纯顺序边（不传资产）也能建
    try:
        g5 = cg.CanvasGraph()
        s = cg.CanvasNode("s", "source_prompt",
                          outputs={"prompt": cg.Port("prompt", "prompt")})
        p = cg.CanvasNode("p", "promo_fx",
                          inputs={"video": cg.Port("video", "video")})
        g5.add_node(s); g5.add_node(p)
        g5.connect_order("s", "p", reason="合规校验先于促销")
        check("B10 纯顺序边可建（不传资产）", len(g5.order_edges) == 1)
    except Exception as e:  # noqa: BLE001
        check("B10 纯顺序边", False, f"{type(e).__name__}: {e}")

    # ---- Wave A #3：单入端口禁止静默覆盖 ----
    try:
        gm = cg.CanvasGraph()
        s1 = cg.CanvasNode("s1", "source_prompt",
                           outputs={"prompt": cg.Port("prompt", "prompt")})
        s2 = cg.CanvasNode("s2", "source_prompt",
                           outputs={"prompt": cg.Port("prompt", "prompt")})
        d = cg.CanvasNode("d", "gen_image",
                          inputs={"prompt": cg.Port("prompt", "prompt")})
        gm.add_node(s1); gm.add_node(s2); gm.add_node(d)
        gm.connect_data("s1", "prompt", "d", "prompt")
        n1 = len(gm.data_edges)
        # 同一四元组重复连接 → 幂等 no-op
        gm.connect_data("s1", "prompt", "d", "prompt")
        check("B11 同一四元组重复连接幂等（不新增边、不抛错）",
              len(gm.data_edges) == n1, f"{n1} → {len(gm.data_edges)}")
        # 不同来源接同一单入端口 → 必拒
        raised_single = False
        try:
            gm.connect_data("s2", "prompt", "d", "prompt")
        except ValueError:
            raised_single = True
        check("B12 单入端口接第二条不同来源 → 抛 ValueError（拒绝静默覆盖）",
              raised_single)
        check("B12b 拒绝后边数不变（不留半条边）",
              len(gm.data_edges) == n1, f"实际 {len(gm.data_edges)}")
        check("B12c 拒绝后生效的仍是第一个来源",
              (gm.data_edges[0].from_node, gm.data_edges[0].from_port)
              == ("s1", "prompt"),
              f"{gm.data_edges[0].from_node}.{gm.data_edges[0].from_port}")
    except Exception as e:  # noqa: BLE001
        check("B11-B12 单入端口禁止静默覆盖", False, f"{type(e).__name__}: {e}")

    # ---- Wave A #3：multi=True 端口允许多入且收成列表 ----
    try:
        gx = cg.CanvasGraph()
        a1 = cg.CanvasNode("a1", "gen_image",
                           outputs={"image": cg.Port("image", "image")})
        a2 = cg.CanvasNode("a2", "gen_image",
                           outputs={"image": cg.Port("image", "image")})
        acc = cg.CanvasNode("acc", "promo_fx",
                            inputs={"frames": cg.Port("frames", "image", multi=True)})
        gx.add_node(a1); gx.add_node(a2); gx.add_node(acc)
        gx.connect_data("a1", "image", "acc", "frames")
        gx.connect_data("a2", "image", "acc", "frames")
        check("B13 multi=True 端口允许两条不同来源的数据边（不拒）",
              len(gx.data_edges) == 2, f"实际 {len(gx.data_edges)}")
        a1.out_assets["image"] = cg.AssetRef(kind="image", name="a1", path="P1")
        a2.out_assets["image"] = cg.AssetRef(kind="image", name="a2", path="P2")
        got = gx._incoming_assets("acc").get("frames")
        check("B14 multi 端口 _incoming_assets 收成列表（两条都在，无覆盖）",
              isinstance(got, list) and len(got) == 2
              and [r.path for r in got] == ["P1", "P2"], str(got))
    except Exception as e:  # noqa: BLE001
        check("B13-B14 multi 端口收成列表", False, f"{type(e).__name__}: {e}")

    # ---------------- C 组：结构 ----------------
    print("\n[C] 结构：示例图断言")
    g6 = cg.build_sample_graph()
    check("C1 节点数 == 6", len(g6.nodes) == 6)
    check("C2 数据边数 == 6", len(g6.data_edges) == 6, f"实际 {len(g6.data_edges)}")
    check("C3 顺序边数 == 1", len(g6.order_edges) == 1, f"实际 {len(g6.order_edges)}")
    labels = {e.label for e in g6.data_edges}
    check("C4 数据边带标签（主轨/分场接力/数字人口播/促销动效）",
          {"主轨", "分场接力", "数字人口播", "促销动效"}.issubset(labels),
          str(labels))
    # 一去多：vid.clip 同时喂 twin 与 promo
    vid_out = [e for e in g6.data_edges if e.from_node == "vid"]
    check("C5 一出多：vid.clip 同时喂两个下游", len(vid_out) == 2, str(vid_out))
    # 去重：重新 connect_data 同一对不重复 depend（_order_seen 已含）
    try:
        before = len(g6._order_seen)
        # 已存在的数据边不再新增顺序边（同一 key）
        g6.connect_data("src", "prompt", "img", "prompt")
        after = len(g6._order_seen)
        check("C6 重复数据边不重复注册顺序依赖", after == before,
              f"before={before} after={after}")
    except Exception as e:  # noqa: BLE001
        check("C6 重复数据边去重", False, f"{type(e).__name__}: {e}")

    # 类型兼容具体组合（与设计稿 §4.1 一致：clip→video 允许、image→image 允许）
    try:
        ok_combos = [("clip", "video", "digital_twin"),
                     ("image", "image", "gen_image")]
        for ft, tt, btype in ok_combos:
            gc = cg.CanvasGraph()
            atype = "gen_video" if ft == "clip" else "gen_image"
            a = cg.CanvasNode("a", atype, outputs={"o": cg.Port("o", ft)})
            b = cg.CanvasNode("b", btype, inputs={"i": cg.Port("i", tt)})
            gc.add_node(a); gc.add_node(b)
            gc.connect_data("a", "o", "b", "i")
        check("C7 兼容组合可连（clip→video / image→image）", True)
    except Exception as e:  # noqa: BLE001
        check("C7 兼容组合可连", False, f"{type(e).__name__}: {e}")

    # subprocess 双保险：真起一个独立进程跑 demo_cli
    DEMO = os.path.join(ROOT, "demo_canvas_cli.py")
    if os.path.isfile(DEMO):
        try:
            work = tempfile.mkdtemp(prefix="test_canvas_")
            jp = os.path.join(work, "demo.json")
            p = subprocess.run(
                [sys.executable, DEMO, "--json", jp],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, cwd=ROOT)
            ok_json = os.path.isfile(jp)
            m = json.load(open(jp, encoding="utf-8")) if ok_json else {}
            check("C8 demo_cli 独立进程退出码 0", p.returncode == 0,
                  (p.stderr or "").strip().splitlines()[-2:])
            check("C9 demo_cli 报告全 completed 且隐含顺序边",
                  bool(m.get("all_completed")) and bool(m.get("data_edges_implied_order")),
                  str({k: m.get(k) for k in ("all_completed", "data_edges_implied_order")}))
        except Exception as e:  # noqa: BLE001
            check("C8-C9 demo_cli 子进程", False, f"{type(e).__name__}: {e}")
    else:
        print("  [SKIP] demo_canvas_cli.py 不存在，跳过子进程双保险")

print(f"\nPASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("=== CANVAS_MODEL_OK ===")
