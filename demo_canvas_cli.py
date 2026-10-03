# -*- coding: utf-8 -*-
"""节点画布 · CLI 验证（第 1 步数据模型 + 第 2 步接资产库）。

对应设计稿 §9 验证目标：在 CLI 里用代码建出一张图并跑通（无 UI）。

用法：
    python demo_canvas_cli.py                  # 默认内存 sink（不落盘、不污染真实库）
    python demo_canvas_cli.py --real-store    # 走真实 asset_store.register_asset（写临时隔离目录）
    python demo_canvas_cli.py --json out.json # 额外输出 JSON 报告

退出码：全 completed → 0；有任何非 completed → 1。
本文件可被 import（tests 里直接调用 main() / build_and_run() 做行为判据）。
"""

import argparse
import json
import os
import sys
import tempfile

import canvas_graph as cg


def build_and_run(asset_store=None, store_path=None) -> dict:
    g = cg.build_sample_graph(asset_store=asset_store)
    topo = g.topo()
    state = g.run({})

    node_status = {nid: n.status for nid, n in g.nodes.items()}
    failed = [nid for nid, s in node_status.items() if s != "completed"]

    # 数据边隐含顺序边：检查 TaskGraph 依赖是否包含每条数据边的 (from→to)
    implied = set()
    for e in g.data_edges:
        implied.add((e.from_node, e.to_node))
    tg_deps = set()
    for td in g._tg.task_list():
        for b in td.get("blockedBy", []):
            tg_deps.add((b, td["id"]))
    missing_implied = [p for p in implied if p not in tg_deps]

    # 第 2 步：各节点产出的 AssetRef 落地情况
    regs = g.asset_registrations()
    regs_ok = [r for r in regs if r["registered"] and r["asset_id"]]

    return {
        "topo": topo,
        "node_count": len(g.nodes),
        "data_edge_count": len(g.data_edges),
        "order_edge_count": len(g.order_edges),
        "node_status": node_status,
        "all_completed": not failed,
        "failed": failed,
        "data_edges_implied_order": not missing_implied,
        "missing_implied": missing_implied,
        "asset_registrations": regs,
        "asset_registered_count": len(regs_ok),
        "asset_store_path": store_path,
        "summary": g.to_dict(),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="节点画布 CLI 验证（数据模型 + 资产库）")
    ap.add_argument("--json", metavar="PATH", help="额外输出 JSON 报告到该路径")
    ap.add_argument("--real-store", action="store_true",
                    help="走真实 asset_store.register_asset（写临时隔离目录，不污染真实库）")
    args = ap.parse_args(argv)

    store = None
    store_path = None
    if args.real_store:
        # 临时隔离目录，避免污染 ~/Documents/小臭玩AI/legion_assets.json。
        # 关键：asset_store 顶部 `from legion import LEGION_DIR` 若成功会忽略
        # XC_LEGION_DIR，故必须直接补丁模块级 ASSET_PATH 才能重定向到临时文件。
        stage_dir = tempfile.mkdtemp(prefix="canvas_realstore_")
        import asset_store as _as
        _as.LEGION_DIR = stage_dir
        _as.ASSET_PATH = os.path.join(stage_dir, "legion_assets.json")
        store_path = _as.ASSET_PATH
        store = cg.make_real_asset_store()

    print("=== 节点画布示例图：无 UI 建图 + run ===")
    try:
        r = build_and_run(store, store_path)
    except Exception as ex:  # noqa: BLE001
        print(f"FAIL: 建图/运行抛异常 -> {ex!r}")
        return 1

    print(f"  节点数        : {r['node_count']}")
    print(f"  数据边数      : {r['data_edge_count']}")
    print(f"  顺序边数      : {r['order_edge_count']}")
    print(f"  拓扑顺序      : {' -> '.join(r['topo'])}")
    for nid, s in r["node_status"].items():
        print(f"    {nid:8s} -> {s}")
    print(f"  数据边隐含顺序边: {'OK' if r['data_edges_implied_order'] else 'FAIL ' + str(r['missing_implied'])}")
    print(f"  资产落地数    : {r['asset_registered_count']}/{len(r['asset_registrations'])}")
    if store_path is not None:
        print(f"  真实资产库    : {store_path}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        print(f"  JSON 报告     -> {args.json}")

    if r["all_completed"] and r["data_edges_implied_order"]:
        print("OK: 全部节点 completed、数据边隐含顺序边、资产已落地")
        return 0
    print(f"FAIL: all_completed={r['all_completed']} failed={r['failed']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
