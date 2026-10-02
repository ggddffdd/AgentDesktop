# -*- coding: utf-8 -*-
"""节点画布第 1 步 · CLI 验证（无 UI 建图跑通）。

对应设计稿 §9 第 1 步验证目标：在 CLI 里用代码建出一张图并跑通（无 UI）。

用法：
    python demo_canvas_cli.py            # 建示例图 + run 到全 completed
    python demo_canvas_cli.py --json out.json   # 额外输出 JSON 报告

退出码：全 completed → 0；有任何非 completed → 1。
本文件可被 import（tests 里直接调用 main() 做行为判据）。
"""

import argparse
import json
import sys

import canvas_graph as cg


def build_and_run() -> dict:
    g = cg.build_sample_graph()
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
        "summary": g.to_dict(),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="节点画布数据模型 CLI 验证")
    ap.add_argument("--json", metavar="PATH", help="额外输出 JSON 报告到该路径")
    args = ap.parse_args(argv)

    print("=== 节点画布示例图：无 UI 建图 + run ===")
    try:
        r = build_and_run()
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

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        print(f"  JSON 报告     -> {args.json}")

    if r["all_completed"] and r["data_edges_implied_order"]:
        print("OK: 全部节点 completed 且数据边均已隐含顺序边")
        return 0
    print(f"FAIL: all_completed={r['all_completed']} failed={r['failed']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
