# -*- coding: utf-8 -*-
"""节点画布数据模型（设计稿 §9 第 1 步：数据模型）。

定义 Node / Port / AssetRef / Edge（数据边 + 顺序边）schema，并在底层
复用现有 `task_graph.TaskGraph` 做依赖调度 —— 数据边自动隐含一条顺序边
（connect_data 时内部调 task_graph.depend），所以 `TaskGraph.run()` 不用改。

节点状态沿用 `task_graph.Task.status` 枚举
（pending / in_progress / completed / failed / cancelled / incomplete），不自创。

本模块是**纯数据层**：无 Qt 依赖、无 UI，可在 CLI 里直接建图跑通
（验证目标见 §9 第 1 步）。第 2 步才接 asset_store 真实写入；第 5 步才接
真实执行器（本模块默认用 stub executor 只搬运/合成 AssetRef，验证调度与数据流）。
"""

from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

import task_graph
from task_graph import TaskGraph


# ----- 端口类型（连线校验用）-----
# 与 asset_store 的 kind 语义对齐但不绑定：画布端口关心的"能传什么资产"。
# 端口类型本身即资产 kind 的一个子集，外加 video 作为视频类汇聚端口。
PORT_TYPES = {
    "prompt": "结构化 prompt 资产（源·Prompt 产出）",
    "image": "图资产（场景图 / 主播图 / 参考图）",
    "clip": "视频片段资产（生视频产出）",
    "video": "视频类汇聚端口（数字人 / 促销 / 成片，接受 clip+video+final）",
    "audio": "音频（含 BGM）",
    "final": "最终成片资产",
    "script": "剧本 / 台词资产",
    "data": "通用数据",
}
VALID_PORT_TYPES = set(PORT_TYPES)

# 端口类型 → 可接受的「上游资产 kind」集合（连线时校验）。
# 设计稿 §4.1：in["video"] 接收 clip 类资产，clip→video 允许；image→image 允许；
# clip→image 直接拒绝（接错在连线时就报错，不等跑一半才炸）。
_PORT_ACCEPTS = {
    "prompt": {"prompt"},
    "image": {"image", "scene", "character_views", "keyframe"},
    "clip": {"clip"},
    "video": {"clip", "video", "final"},
    "audio": {"audio"},
    "final": {"final"},
    "script": {"script", "prompt"},
    "data": {"data"},
}


# 6 类节点类型（§3）+ 各自默认输出端口类型
NODE_TYPES = {
    "source_prompt": "源·Prompt",
    "gen_image": "生图",
    "gen_video": "生视频",
    "digital_twin": "数字人口播",
    "promo_fx": "促销动效",
    "final_output": "成片输出",
}
_NODE_DEFAULT_OUT = {
    "source_prompt": "prompt",
    "gen_image": "image",
    "gen_video": "clip",
    "digital_twin": "video",
    "promo_fx": "video",
    "final_output": "final",
}


@dataclass
class AssetRef:
    """对一条资产的引用（第 2 步才真正写进 asset_store）。

    第 1 步只持引用 + kind，不强制落地文件。run 时由上游节点产出填入。
    """
    kind: str                      # 对齐 asset_store kind：clip/image/scene/...
    asset_id: str = ""             # asset_store 的 8 位 id（写库后回填）
    path: str = ""                 # 绝对路径（写库后回填）
    name: str = ""                 # 人读名
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "asset_id": self.asset_id,
            "path": self.path,
            "name": self.name,
            "meta": self.meta,
        }


@dataclass
class Port:
    """一个节点的输入 / 输出端口，带类型。"""
    name: str
    port_type: str                 # 必须是 VALID_PORT_TYPES 之一
    multi: bool = False            # 是否允许多入多出（如成片节点并合多个上游）

    def __post_init__(self):
        if self.port_type not in VALID_PORT_TYPES:
            raise ValueError(
                f"非法端口类型: {self.port_type!r}，必须是 {sorted(VALID_PORT_TYPES)} 之一")


@dataclass
class DataEdge:
    """数据边：上游某 output 端口 → 下游某 input 端口，携带资产。"""
    from_node: str
    from_port: str
    to_node: str
    to_port: str
    label: str = ""                # 可选标签：首帧 / 主轨 / BGM轨 ...
    asset: Optional[AssetRef] = None


@dataclass
class OrderEdge:
    """顺序边：只规定先后，不传资产（如 合规校验 → 生图）。"""
    from_node: str
    to_node: str
    reason: str = ""


class CanvasNode:
    """画布节点：包装一个 TaskGraph 任务，附加端口与资产引用。

    节点状态沿用 Task.status（pending/in_progress/completed/failed/
    cancelled/incomplete），由 CanvasGraph.run 同步。
    """

    def __init__(self, node_id: str, node_type: str,
                 inputs: Optional[Dict[str, Port]] = None,
                 outputs: Optional[Dict[str, Port]] = None,
                 config: Optional[dict] = None,
                 executor: Optional[Callable] = None):
        if node_type not in NODE_TYPES:
            raise ValueError(
                f"未知节点类型: {node_type!r}，必须是 {sorted(NODE_TYPES)} 之一")
        self.id = node_id
        self.node_type = node_type
        self.inputs: Dict[str, Port] = dict(inputs or {})
        self.outputs: Dict[str, Port] = dict(outputs or {})
        self.config: dict = dict(config or {})
        # 未显式给 outputs 时，按节点类型给一个默认主输出端口
        if not self.outputs:
            self.outputs = {"out": Port("out", self._default_out_type())}
        self.in_assets: Dict[str, Optional[AssetRef]] = {p: None for p in self.inputs}
        self.out_assets: Dict[str, Optional[AssetRef]] = {p: None for p in self.outputs}
        self.status = "pending"
        self.result = None
        # executor 占位（第 5 步替换真执行器）；默认 stub 产出空 AssetRef
        self.executor = executor or self._default_executor()

    def _default_out_type(self) -> str:
        return _NODE_DEFAULT_OUT[self.node_type]

    def _default_executor(self) -> Callable:
        """第 1 步 stub：不接真实生成，仅产出该节点类型的默认 AssetRef，
        验证「调度 + 数据流」跑通（真实执行器第 5 步再接）。"""
        ntype = self.node_type

        def _stub(state: dict) -> dict:
            out_kind = self._default_out_type()
            produced = AssetRef(kind=out_kind,
                                name=f"{NODE_TYPES[ntype]}:{self.id}")
            self.out_assets = {p: produced for p in self.outputs}
            return {
                "node": self.id,
                "type": ntype,
                "out_kind": out_kind,
                "produced": produced.to_dict(),
            }
        return _stub

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.node_type,
            "status": self.status,
            "inputs": {p: pt.port_type for p, pt in self.inputs.items()},
            "outputs": {p: pt.port_type for p, pt in self.outputs.items()},
            "config": self.config,
        }


class CanvasGraph:
    """画布图：维护 Node / Edge，底层复用 TaskGraph 做调度。

    设计要点（§4.0）：
      - 数据边 connect_data：先校验端口存在 + 类型兼容，再建边，并自动
        隐含一条顺序边（内部调 task_graph.depend），去重避免重复依赖。
      - 顺序边 connect_order：只规定先后，不传资产。
      - 调度仍只靠 TaskGraph 一套依赖推进逻辑，run() 不用改。
    """

    def __init__(self):
        self.nodes: Dict[str, CanvasNode] = {}
        self.data_edges: List[DataEdge] = []
        self.order_edges: List[OrderEdge] = []
        self._tg = TaskGraph()
        # 隐含/显式顺序边去重键集合，避免对同一对节点重复 depend
        self._order_seen = set()

    # ---- 节点 ----
    def add_node(self, node: CanvasNode):
        if node.id in self.nodes:
            raise ValueError(f"节点已存在: {node.id}")
        self.nodes[node.id] = node
        # 注册成 TaskGraph 任务；executor 包装：先收上游资产再跑节点 executor
        self._tg.create(node.id, node.node_type, self._wrap(node), node.id)
        return self

    def _wrap(self, node: CanvasNode) -> Callable:
        def _exec(state: dict) -> dict:
            # 把上游通过数据边传来的 AssetRef 注入节点 in_assets
            for p, a in self._incoming_assets(node.id).items():
                node.in_assets[p] = a
            r = node.executor(state)
            return r
        return _exec

    def _incoming_assets(self, node_id: str) -> Dict[str, Optional[AssetRef]]:
        """上游产出的资产放在上游节点的 out_assets[port] 上，

        数据边只是连线关系，不持有资产本身。故这里从上游节点的
        out_assets 读回（上游已先跑完，见 connect_data 隐含的顺序边），
        下游 _wrap 在跑自己的 executor 前注入 in_assets。
        """
        out = {}
        for e in self.data_edges:
            if e.to_node == node_id:
                up = self.nodes.get(e.from_node)
                if up is not None:
                    out[e.to_port] = up.out_assets.get(e.from_port)
        return out

    # ---- 连线 ----
    def connect_data(self, from_node, from_port, to_node, to_port,
                     label="", asset=None):
        """数据边：校验端口存在 + 类型兼容，再建立；同时隐含一条顺序边。"""
        fn = self.nodes.get(from_node)
        tn = self.nodes.get(to_node)
        if not fn or not tn:
            raise ValueError(f"节点不存在: {from_node} 或 {to_node}")
        fp = fn.outputs.get(from_port)
        tp = tn.inputs.get(to_port)
        if fp is None:
            raise ValueError(
                f"{from_node} 不存在输出端口 {from_port!r}（现有: {list(fn.outputs)}）")
        if tp is None:
            raise ValueError(
                f"{to_node} 不存在输入端口 {to_port!r}（现有: {list(tn.inputs)}）")
        if fp.port_type not in _PORT_ACCEPTS[tp.port_type]:
            raise ValueError(
                f"端口类型不兼容：{from_node}.{from_port}({fp.port_type}) → "
                f"{to_node}.{to_port}({tp.port_type})，连线时即拒绝"
                f"（{tp.port_type} 端口只接受 {sorted(_PORT_ACCEPTS[tp.port_type])}）")
        edge = DataEdge(from_node, from_port, to_node, to_port, label, asset)
        self.data_edges.append(edge)
        # 数据边自动隐含顺序边：上游先完成（同一对节点只 depend 一次）
        key = (from_node, to_node)
        if key not in self._order_seen:
            self._tg.depend(to_node, from_node)
            self._order_seen.add(key)
        return self

    def connect_order(self, from_node, to_node, reason=""):
        """顺序边：只规定先后，不传资产。"""
        if from_node not in self.nodes or to_node not in self.nodes:
            raise ValueError(f"节点不存在: {from_node} 或 {to_node}")
        edge = OrderEdge(from_node, to_node, reason)
        self.order_edges.append(edge)
        key = (from_node, to_node)
        if key not in self._order_seen:
            self._tg.depend(to_node, from_node)
            self._order_seen.add(key)
        return self

    # ---- 查询 ----
    def topo(self) -> List[str]:
        """返回节点拓扑顺序（依赖先后）。"""
        return [t["id"] for t in self._tg.task_list()]

    def to_dict(self) -> dict:
        return {
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "data_edges": [
                {"from": f"{e.from_node}.{e.from_port}",
                 "to": f"{e.to_node}.{e.to_port}",
                 "label": e.label} for e in self.data_edges],
            "order_edges": [
                {"from": e.from_node, "to": e.to_node, "reason": e.reason}
                for e in self.order_edges],
        }

    # ---- 执行 ----
    def run(self, state: Optional[dict] = None, token=None) -> dict:
        state = self._tg.run(state or {}, token)
        # 同步节点终态（task_list 返回 id/subject/status/blockedBy）
        for td in self._tg.task_list():
            nid = td["id"]
            if nid in self.nodes:
                self.nodes[nid].status = td["status"]
        return state

    # ---- 事件（§7.1：每条操作落账本；第 1 步仅占位，第 7 步接 gate UI）----
    def emit_event(self, kind: str, payload: dict):
        # 无 UI 阶段不写 jsonl（避免副作用）；接口预留给第 7 步接 legion_auth
        pass


def build_sample_graph() -> CanvasGraph:
    """建一张设计稿 §2 的示例流（无 UI、无真实生成，仅验证数据模型与调度）：

    源·Prompt → 生图 → 生视频 →（分叉）数字人口播 / 促销动效 → 成片输出

    覆盖：多入多出（成片并合）、一出多（生视频喂两个下游）、
    标签（主轨 / 分场接力）、数据边隐含顺序边、纯顺序边演示。
    """
    g = CanvasGraph()

    src = CanvasNode("src", "source_prompt",
                     outputs={"prompt": Port("prompt", "prompt")})
    img = CanvasNode("img", "gen_image",
                     inputs={"prompt": Port("prompt", "prompt")},
                     outputs={"image": Port("image", "image")})
    vid = CanvasNode("vid", "gen_video",
                     inputs={"image": Port("image", "image")},
                     outputs={"clip": Port("clip", "clip")})
    twin = CanvasNode("twin", "digital_twin",
                      inputs={"video": Port("video", "video")},
                      outputs={"video": Port("video", "video")})
    promo = CanvasNode("promo", "promo_fx",
                       inputs={"video": Port("video", "video")},
                       outputs={"video": Port("video", "video")})
    # 成片节点：多入（main + promo 两个 video 端口并合）
    final = CanvasNode("final", "final_output",
                       inputs={"main": Port("main", "video"),
                               "promo": Port("promo", "video")},
                       outputs={"final": Port("final", "final")})

    for n in (src, img, vid, twin, promo, final):
        g.add_node(n)

    # 数据边（含标签；生视频一出二：同一 clip 端口喂 twin 与 promo）
    g.connect_data("src", "prompt", "img", "prompt")
    g.connect_data("img", "image", "vid", "image")
    g.connect_data("vid", "clip", "twin", "video", label="主轨")
    g.connect_data("vid", "clip", "promo", "video", label="分场接力")
    g.connect_data("twin", "video", "final", "main", label="数字人口播")
    g.connect_data("promo", "video", "final", "promo", label="促销动效")
    # 纯顺序边演示：合规 / 资源准备先于促销动效（不传资产）
    g.connect_order("src", "promo", reason="合规校验先于促销动效")

    return g


if __name__ == "__main__":
    import json
    import sys
    g = build_sample_graph()
    print("=== 示例图拓扑 ===")
    print(json.dumps(g.to_dict(), ensure_ascii=False, indent=1))
    print("=== 运行（无 UI）===")
    g.run({})
    for nid, n in g.nodes.items():
        print(f"  {nid} [{n.node_type}] -> {n.status}")
    failed = [nid for nid, n in g.nodes.items() if n.status != "completed"]
    print("OK" if not failed else f"FAIL: {failed}")
    sys.exit(1 if failed else 0)
