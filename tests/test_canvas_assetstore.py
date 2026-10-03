# -*- coding: utf-8 -*-
"""节点画布第 2 步 · 接资产库判据（canvas_graph.py / asset_store.py）。

对应设计稿 §9 第 2 步验证目标：AssetRef 真正写进 asset_store（register_asset +
legion_assets.json），节点跑完把产出 AssetRef 落地、回填 asset_id，下游按引用读取；
画布不直接管文件，只持引用。

三组判据（独立运行：python tests/test_canvas_assetstore.py）：
  A 静态组 —— 资产库接线的**契约必须落在源码里**（sink 抽象 / _register_asset 调用
               与回填 / _wrap 在 run 时触发写库 / stub 给 path / 默认内存 sink 不污染
               / 真实库接线存在 / AssetRef.registered 字段），逐切片判定；
  B 行为组 —— 真 import canvas_graph 建图 run：默认内存 sink 下每条产出都落地回填 id、
               下游 in_assets 拿到带 id 的引用、缺 name/path 的拒收不崩、
               真实 asset_store（临时隔离目录）真写入文件且 kind 归一化；
  C 结构组 —— 落地条数 / asset_registrations 结构 / demo --real-store 子进程真写文件。

扰动脚本 _perturb_canvas_assetstore.py 只验 A 组静态判据（CANVAS_PATH / CANVAS_STATIC）。
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

# 扰动脚本只验 A 组静态判据，用 CANVAS_STATIC=1 跳过 B/C。
STATIC_ONLY = os.environ.get("CANVAS_STATIC") == "1"


def _slice_func(name):
    r"""取某个 def/class 的源码切片（切到「缩进宽度 <= 本函数」的下一个 def/class）。

    结束边界用缩进宽度判定，而非简单的「下一个顶层 class/def」，否则会把同类
    兄弟方法的同名调用一起包进来，导致判据该红不红（假绿）。
    """
    m = re.search(r"^[ \t]*(?:class|def)\s+%s\b" % re.escape(name), SRC, re.M)
    if not m:
        return ""
    # 缩进用 [ \t]* 而非 \s*：\s 含换行会把前导空行算进 group(0)，indent 偏大。
    indent = len(m.group(0)) - len(m.group(0).lstrip())
    nxt = None
    for nm in re.finditer(r"^[ \t]*(?:class|def)\s+\w+", SRC[m.end():], re.M):
        ind = len(nm.group(0)) - len(nm.group(0).lstrip())
        if ind <= indent:
            nxt = nm
            break
    return SRC[m.end(): m.end() + nxt.start()] if nxt else SRC[m.end():]


# ==========================================================================
# A 组：静态 —— 资产库接线契约
# ==========================================================================
print("\n[A] 静态：资产库接线契约必须在源码里")

# 1) CanvasGraph.__init__ 接受 asset_store 并解析出 self._asset_store
check("A1 CanvasGraph 接受 asset_store 并落 self._asset_store",
      "self._asset_store = _resolve_store(asset_store)" in SRC)

# 2) _register_asset 真正调用 sink
ARS = _slice_func("_register_asset")
check("A2 _register_asset 调用 self._asset_store sink",
      "self._asset_store(" in ARS,
      "不调 sink 等于没接资产库")

# 3) 写库成功后回填 asset_id
check("A3 _register_asset 写库成功后回填 ref.asset_id",
      "ref.asset_id = aid" in ARS,
      "下游拿不到 id，引用断链")

# 4) registered 标志双态（成功 True / 拒收 False）
check("A4 _register_asset 置 registered 双态（True/False）",
      "ref.registered = True" in ARS and "ref.registered = False" in ARS)

# 5) _wrap 在 run 时（executor 之后）触发写库
AWR = _slice_func("_wrap")
check("A5 _wrap 在 executor 之后调用 _register_asset（run 时落地）",
      "self._register_asset(a)" in AWR,
      "否则节点跑完不落库，第 2 步白做")

# 6) stub executor 给 AssetRef 设 path（否则无路径可登记）
AEX = _slice_func("_default_executor")
check("A6 stub executor 给产出 AssetRef 设 path（供写库）",
      "path=_stub_stage_path(" in AEX)

# 7) 默认 sink 是内存记录（不落盘、不污染真实库）
ARE = _slice_func("_resolve_store")
check("A7 默认 asset_store=None → 内存 _MemStore（不污染真实库）",
      "def _resolve_store" in SRC and "if asset_store is None:" in ARE
      and "return _MemStore()" in ARE)

# 8) 真实库接线存在（wrap asset_store.register_asset）
AMA = _slice_func("make_real_asset_store")
check("A8 make_real_asset_store 接真实 asset_store.register_asset",
      "def make_real_asset_store" in SRC and "import asset_store" in AMA
      and "_as.register_asset" in AMA)

# 9) AssetRef 有 registered 字段
AST = _slice_func("AssetRef")
check("A9 AssetRef 带 registered 字段",
      "registered: bool = False" in AST)


# ==========================================================================
# B/C 组：行为 + 结构
# ==========================================================================
HAS_MOD = importlib.util.find_spec("canvas_graph") is not None

if STATIC_ONLY:
    print("\n[B][C] 行为/结构组：按 CANVAS_STATIC 跳过（扰动脚本只验静态判据）")

elif not HAS_MOD:
    print("\n[B] 行为：跳过（无法 import canvas_graph）")
    print("  [SKIP] 默认 sink 落地 / 下游引用带 id / 真实库写入")

else:
    import canvas_graph as cg

    print("\n[B] 行为：真 import canvas_graph 建图跑通 + 资产库落地")

    # ---- 默认（内存 sink）：每条产出都落地并回填 id ----
    try:
        g = cg.build_sample_graph()   # asset_store=None → 内存 _MemStore
        g.run({})
        statuses = {nid: n.status for nid, n in g.nodes.items()}
        check("B1 全部节点 completed（默认内存 sink）",
              all(s == "completed" for s in statuses.values()),
              str({k: v for k, v in statuses.items() if v != "completed"}))

        regs = g.asset_registrations()
        ok_regs = [r for r in regs if r["registered"] and r["asset_id"]]
        check("B2 每个节点产出都落地（6 条全 registered + 有 asset_id）",
              len(regs) == 6 and len(ok_regs) == 6,
              f"regs={len(regs)} ok={len(ok_regs)}")
        check("B3 内存 sink 回填的 asset_id 非空（mem 序列）",
              all(r["asset_id"].startswith("mem") for r in ok_regs))
    except Exception as e:  # noqa: BLE001
        check("B1-B3 默认 sink 落地", False, f"{type(e).__name__}: {e}")

    # ---- 下游按引用读取，且引用带 asset_id ----
    try:
        g2 = cg.build_sample_graph()
        g2.run({})
        fin = g2.nodes["final"]
        main_ref = fin.in_assets.get("main")
        promo_ref = fin.in_assets.get("promo")
        check("B4 下游 in_assets 拿到上游 AssetRef（引用传播）",
              isinstance(main_ref, cg.AssetRef) and isinstance(promo_ref, cg.AssetRef),
              f"main={main_ref} promo={promo_ref}")
        check("B5 下游引用的 AssetRef 已带 asset_id（落地回填）",
              bool(getattr(main_ref, "asset_id", ""))
              and bool(getattr(promo_ref, "asset_id", "")))
    except Exception as e:  # noqa: BLE001
        check("B4-B5 下游引用带 id", False, f"{type(e).__name__}: {e}")

    # ---- 缺 name/path 的产出拒收（registered=False），不崩、run 仍 completed ----
    try:
        g3 = cg.CanvasGraph()
        bad = cg.CanvasNode("bad", "gen_image",
                            inputs={}, outputs={"image": cg.Port("image", "image")},
                            executor=lambda state: (
                                setattr(bad, "out_assets",
                                        {"image": cg.AssetRef(
                                            kind="image", name="", path="")}),
                                {"ok": True})[1])
        g3.add_node(bad)
        g3.run({})
        bregs = g3.asset_registrations()
        check("B6 缺 name/path 的产出 registered=False 且节点仍 completed",
              bad.status == "completed"
              and bregs and bregs[0]["registered"] is False,
              f"status={bad.status} regs={bregs}")
    except Exception as e:  # noqa: BLE001
        check("B6 空 name/path 拒收不崩", False, f"{type(e).__name__}: {e}")

    # ---- 真实 asset_store 接入（临时隔离目录，不污染真实库）----
    try:
        import asset_store as _as_mod
        real_dir = tempfile.mkdtemp(prefix="canvas_realstore_")
        _as_mod.LEGION_DIR = real_dir
        _as_mod.ASSET_PATH = os.path.join(real_dir, "legion_assets.json")
        store = cg.make_real_asset_store()

        g4 = cg.build_sample_graph(asset_store=store)
        g4.run({})
        r4 = g4.asset_registrations()
        ok4 = [r for r in r4 if r["registered"]]
        check("B7 真实库：6 条全部落地", len(r4) == 6 and len(ok4) == 6,
              f"r4={len(r4)} ok={len(ok4)}")
        # 真实库 id 是 8 位 hex
        check("B8 真实库回填的 asset_id 是 8 位 hex",
              all(re.fullmatch(r"[0-9a-f]{8}", r["asset_id"] or "") for r in ok4))

        # 读真实落盘文件
        with open(_as_mod.ASSET_PATH, encoding="utf-8") as f:
            data = json.load(f)
        kinds = sorted({a["kind"] for a in data["assets"]})
        check("B9 真实 legion_assets.json 写入 6 条且 kind 正确",
              len(data["assets"]) == 6
              and set(kinds) == {"prompt", "image", "clip", "video", "final"},
              f"n={len(data['assets'])} kinds={kinds}")

        # kind 归一化：未知 kind 在 asset_store 里归 other
        ok, aid = store("怪名资产", "weird_kind_x", "canvas_stage/weird/weird_out.bin")
        with open(_as_mod.ASSET_PATH, encoding="utf-8") as f:
            data2 = json.load(f)
        weird = [a for a in data2["assets"] if a.get("id") == aid]
        check("B10 真实库对未知 kind 归一化为 other",
              bool(weird) and weird[0]["kind"] == "other",
              f"weird={weird}")
    except Exception as e:  # noqa: BLE001
        check("B7-B10 真实资产库接入", False, f"{type(e).__name__}: {e}")

    # ---------------- C 组：结构 ----------------
    print("\n[C] 结构：落地条数 / 结构 / demo 子进程")
    try:
        g5 = cg.build_sample_graph()
        g5.run({})
        cregs = g5.asset_registrations()
        check("C1 默认 sink 下落地条数 == 6（每节点产出一次）", len(cregs) == 6)
        # asset_registrations 结构：每条含 node/port/kind/asset_id/registered
        want_keys = {"node", "port", "kind", "asset_id", "registered"}
        check("C2 asset_registrations 每条含 node/port/kind/asset_id/registered",
              all(want_keys.issubset(set(r)) for r in cregs))
    except Exception as e:  # noqa: BLE001
        check("C1-C2 结构", False, f"{type(e).__name__}: {e}")

    # demo --real-store 子进程：真写文件，且 JSON 报告资产已落地
    DEMO = os.path.join(ROOT, "demo_canvas_cli.py")
    if os.path.isfile(DEMO):
        try:
            work = tempfile.mkdtemp(prefix="test_canvas_as_")
            jp = os.path.join(work, "demo.json")
            p = subprocess.run(
                [sys.executable, DEMO, "--real-store", "--json", jp],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, cwd=ROOT)
            m = json.load(open(jp, encoding="utf-8")) if os.path.isfile(jp) else {}
            sp = m.get("asset_store_path", "")
            file_ok = os.path.isfile(sp)
            n_in_file = 0
            if file_ok:
                with open(sp, encoding="utf-8") as f:
                    n_in_file = len(json.load(f).get("assets", []))
            check("C3 demo --real-store 退出码 0", p.returncode == 0,
                  (p.stderr or "").strip().splitlines()[-2:])
            check("C4 demo 报告资产落地 6 条 + 全 completed",
                  m.get("asset_registered_count") == 6
                  and bool(m.get("all_completed")), str(m))
            check("C5 demo 真实写入 legion_assets.json（6 条）",
                  file_ok and n_in_file == 6, f"file_ok={file_ok} n={n_in_file}")
        except Exception as e:  # noqa: BLE001
            check("C3-C5 demo --real-store 子进程", False, f"{type(e).__name__}: {e}")
    else:
        print("  [SKIP] demo_canvas_cli.py 不存在，跳过子进程双保险")

print(f"\nPASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("=== CANVAS_ASSETSTORE_OK ===")
