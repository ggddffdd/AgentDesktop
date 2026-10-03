# -*- coding: utf-8 -*-
"""节点画布数据模型（设计稿 §9 第 1 步：数据模型）。

定义 Node / Port / AssetRef / Edge（数据边 + 顺序边）schema，并在底层
复用现有 `task_graph.TaskGraph` 做依赖调度 —— 数据边自动隐含一条顺序边
（边集一变就由 `_sync_order_deps` 全量重算、写进 task_graph），
所以 `TaskGraph.run()` 不用改。

节点状态沿用 `task_graph.Task.status` 枚举
（pending / in_progress / completed / failed / cancelled / incomplete），不自创。

本模块是**纯数据层**：无 Qt 依赖、无 UI，可在 CLI 里直接建图跑通
（验证目标见 §9 第 1 步）。第 2 步已接 asset_store：节点跑完把产出的
AssetRef 通过 `register_asset` 落地（id/path 回填），下游按引用读取；
第 5 步才接真实执行器（本模块默认用 stub executor 只搬运/合成 AssetRef）。
"""

from typing import Any, Callable, Dict, List, Optional
import os
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


# ----- 资产库 sink（第 2 步：节点产出 AssetRef 落地）-----
# sink 形如 fn(name, kind, path, tags=(), project="", task="", meta=None) -> (ok, asset_id)
# 默认 _MemStore（不落盘，避免污染真实 legion_assets.json）；生产接真实库用
# make_real_asset_store() 包裹 asset_store.register_asset。

_STUB_EXT = {
    "prompt": ".txt", "image": ".png", "clip": ".mp4", "video": ".mp4",
    "final": ".mp4", "script": ".txt", "audio": ".mp3", "data": ".json",
}


def _stub_stage_path(kind: str, node_id: str, port: str) -> str:
    """stub 产出的「阶段路径」——只作引用登记用，不真建文件
    （画布不直接管文件；真实文件由第 5 步执行器产出）。"""
    ext = _STUB_EXT.get(kind, ".bin")
    return os.path.join("canvas_stage", kind, f"{node_id}_{port}{ext}")


class _MemStore:
    """内存资产库（默认 sink）：记录每次登记，便于判据真验「写库 + 回填 id」。"""
    def __init__(self):
        self.records: list = []
        self._seq = 0

    def __call__(self, name, kind, path, tags=(), project="", task="", meta=None):
        self._seq += 1
        aid = "mem%06d" % self._seq
        self.records.append({
            "id": aid, "name": name, "kind": kind, "path": path,
            "tags": list(tags), "project": project, "task": task,
            "meta": dict(meta or {}),
        })
        return True, aid


def _resolve_store(asset_store):
    """None → 内存记录（安全默认，不污染真实库）；callable → 直接用。"""
    if asset_store is None:
        return _MemStore()
    if callable(asset_store):
        return asset_store
    raise TypeError("asset_store 必须是 callable 或 None")


def make_real_asset_store():
    """接真实 asset_store.register_asset（会真正写 legion_assets.json）。

    验证时用临时 XC_LEGION_DIR 指向隔离目录，避免污染大哥真实资产库。
    """
    import asset_store as _as
    def _real(name, kind, path, tags=(), project="", task="", meta=None):
        return _as.register_asset(name, kind, path, tags=tags,
                                  project=project, task=task, meta=meta)
    return _real


@dataclass
class AssetRef:
    """对一条资产的引用（第 2 步已写进 asset_store）。

    run 时由上游节点 executor 产出，并经 `CanvasGraph._register_asset`
    写库：asset_id 回填、registered 置 True。画布只持引用，不建真实文件。
    """
    kind: str                      # 对齐 asset_store kind：clip/image/scene/...
    asset_id: str = ""             # asset_store 的 8 位 id（写库后回填）
    path: str = ""                 # 阶段路径 / 绝对路径（写库后回填）
    name: str = ""                 # 人读名
    registered: bool = False       # 是否已成功写库落地
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "asset_id": self.asset_id,
            "path": self.path,
            "name": self.name,
            "registered": self.registered,
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
                 executor: Optional[Callable] = None,
                 pos: Optional[tuple] = None):
        if node_type not in NODE_TYPES:
            raise ValueError(
                f"未知节点类型: {node_type!r}，必须是 {sorted(NODE_TYPES)} 之一")
        self.id = node_id
        self.node_type = node_type
        self.inputs: Dict[str, Port] = dict(inputs or {})
        self.outputs: Dict[str, Port] = dict(outputs or {})
        self.config: dict = dict(config or {})
        # 第 4 步：可编辑画布的位置（x, y）。None = 交给 layout_graph 自动布局；
        # 一旦用户在画布上拖拽/手动定位，这里就记下坐标，导出/重渲染都尊重它。
        self.pos: Optional[tuple] = (float(pos[0]), float(pos[1])) if pos else None
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
        验证「调度 + 数据流」跑通（真实执行器第 5 步再接）。
        第 2 步起：产出的 AssetRef 带阶段路径 + 元数据，供 _register_asset 写库。"""
        ntype = self.node_type

        def _stub(state: dict) -> dict:
            out_kind = self._default_out_type()
            produced = AssetRef(
                kind=out_kind,
                name=f"{NODE_TYPES[ntype]}:{self.id}",
                path=_stub_stage_path(out_kind, self.id, "out"),
                meta={"tags": [ntype, out_kind], "project": "canvas_stage",
                      "task": f"节点 {self.id} 产出 {out_kind}（第1步 stub）"},
            )
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
            "pos": [self.pos[0], self.pos[1]] if self.pos else None,
        }


class CanvasGraph:
    """画布图：维护 Node / Edge，底层复用 TaskGraph 做调度。

    设计要点（§4.0）：
      - 数据边 connect_data：先校验端口存在 + 类型兼容 + 非自依赖，再建边。
      - 顺序边 connect_order：只规定先后，不传资产。
      - **依赖由边集唯一决定**（Wave B，审查 #2）：`data_edges ∪ order_edges`
        是唯一事实源，边集一变就调 `_sync_order_deps` 全量重算 TaskGraph 的
        依赖（该撤的 undepend、该加的 depend）。连线、删边、撤销、工程导入
        走的都是同一条路，不会出现「边删了依赖还在」的幽灵依赖。
      - 调度仍只靠 TaskGraph 一套依赖推进逻辑，run() 不用改。
    """

    def __init__(self, asset_store=None):
        self.nodes: Dict[str, CanvasNode] = {}
        self.data_edges: List[DataEdge] = []
        self.order_edges: List[OrderEdge] = []
        self._tg = TaskGraph()
        # 已登记到 TaskGraph 的依赖对 (from, to) 集合，即当前边集期望值的镜像。
        # _sync_order_deps 用它做差集；不要在别处直接增删它。
        self._order_seen = set()
        # 第 2 步：资产库 sink。None → 内存记录（不落盘、不污染真实库）；
        # 生产接真实库时显式传 make_real_asset_store()（register_asset）。
        self._asset_store = _resolve_store(asset_store)

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
            # 第 2 步：节点产出 AssetRef 落地到资产库，回填 asset_id
            for p, a in node.out_assets.items():
                if isinstance(a, AssetRef):
                    self._register_asset(a)
            return r
        return _exec

    def _incoming_assets(self, node_id: str) -> Dict[str, Optional[AssetRef]]:
        """上游产出的资产放在上游节点的 out_assets[port] 上，

        数据边只是连线关系，不持有资产本身。故这里从上游节点的
        out_assets 读回（上游已先跑完，见 connect_data 隐含的顺序边），
        下游 _wrap 在跑自己的 executor 前注入 in_assets。
        """
        out = {}
        tn = self.nodes.get(node_id)
        for e in self.data_edges:
            if e.to_node == node_id:
                up = self.nodes.get(e.from_node)
                if up is None:
                    continue
                a = up.out_assets.get(e.from_port)
                port = (tn.inputs or {}).get(e.to_port) if tn is not None else None
                if port is not None and getattr(port, "multi", False):
                    # 多入端口：收成列表，不覆盖（单入端口已由 connect_data 拒绝第二条）
                    out.setdefault(e.to_port, []).append(a)
                else:
                    out[e.to_port] = a
        return out

    # ---- 资产库（第 2 步）----
    def _register_asset(self, ref: AssetRef) -> str:
        """把一条 AssetRef 落地到资产库 sink，回填 asset_id / registered。

        画布只持引用：这里调用 asset_store.register_asset 做登记，
        不创建/管理真实文件（文件由第 5 步执行器产出）。
        缺 name/path 与真实库一致地拒收（registered=False）。
        """
        if not isinstance(ref, AssetRef) or not ref.kind:
            return ""
        name = (ref.name or "").strip()
        path = (ref.path or "").strip()
        if not name or not path:
            ref.registered = False
            return ""
        meta = ref.meta if isinstance(ref.meta, dict) else {}
        tags = tuple(meta.get("tags", ()) or ())
        project = str(meta.get("project", "") or "canvas_stage")
        task = str(meta.get("task", "") or "")
        ameta = {k: v for k, v in meta.items() if k not in ("tags", "project", "task")}
        try:
            ok, aid = self._asset_store(name, ref.kind, path,
                                        tags=tags, project=project,
                                        task=task, meta=ameta)
        except Exception:
            ok, aid = False, ""
        if ok and aid:
            ref.asset_id = aid
            ref.registered = True
            return aid
        ref.registered = False
        return ""

    # ---- 连线 ----
    def connect_data(self, from_node, from_port, to_node, to_port,
                     label="", asset=None):
        """数据边：校验端口存在 + 类型兼容，再建立；同时隐含一条顺序边。"""
        fn = self.nodes.get(from_node)
        tn = self.nodes.get(to_node)
        if not fn or not tn:
            raise ValueError(f"节点不存在: {from_node} 或 {to_node}")
        # 自依赖：节点等自己 = 永远等不到就绪（run 时死锁）。必须在这里拒，
        # 因为它是**类型兼容**的（如 promo.video→promo.video），端口校验拦不住。
        if from_node == to_node:
            raise ValueError(
                f"{from_node} 不能连到自己：自依赖会永远等不到就绪（run 时死锁）")
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
        # 单入端口（multi=False）：禁止第二条「不同来源」的数据边 —— 否则运行时
        # _incoming_assets 会因 dict 后写覆盖，静默丢弃在先的输入（用户无感知）。
        # 同一四元组重复连接视为幂等 no-op（支持重复调用 / UI 预校验回滚后重连）。
        existing = [(e.from_node, e.from_port) for e in self.data_edges
                    if e.to_node == to_node and e.to_port == to_port]
        if existing:
            if (from_node, from_port) in existing:
                return self
            if not tp.multi:
                raise ValueError(
                    f"{to_node}.{to_port} 是单入端口（multi=False），"
                    f"已有来自 {existing[0][0]}.{existing[0][1]} 的连接；"
                    f"如需多入请把该端口标为 multi=True（拒绝静默覆盖）")
        edge = DataEdge(from_node, from_port, to_node, to_port, label, asset)
        self.data_edges.append(edge)
        # 数据边自动隐含顺序边：上游先完成。去重与撤销统一交给重算
        # （同一对节点只 depend 一次；这条边被删掉时依赖也会同步撤销）
        self._sync_order_deps()
        return self

    def connect_order(self, from_node, to_node, reason=""):
        """顺序边：只规定先后，不传资产。"""
        if from_node not in self.nodes or to_node not in self.nodes:
            raise ValueError(f"节点不存在: {from_node} 或 {to_node}")
        if from_node == to_node:
            raise ValueError(
                f"{from_node} 不能连到自己：自依赖会永远等不到就绪（run 时死锁）")
        edge = OrderEdge(from_node, to_node, reason)
        self.order_edges.append(edge)
        self._sync_order_deps()
        return self

    def _sync_order_deps(self):
        """把 TaskGraph 的依赖**重算**成与当前边集完全一致（Wave B，审查 #2）。

        为什么不是「连线时 depend 一次、删边时不管」：

          1. 删边 / 撤销连线 / 连线预校验回滚都会留下**幽灵依赖** —— 边没了、
             依赖还在，本不该等的节点在等；更糟的是一旦误连成环，run() 抛
             「任务图死锁」之后再怎么删边都解不开，只能重启程序。
          2. 同一对节点被反复 depend（画布 `_finish_link` 会「先连一次探路 →
             删掉 → 再正式连」）→ task_list() 里出现 `blockedBy: [x, x]`。

        做法：**本图自己就是唯一事实源**（data_edges ∪ order_edges 的
        (from,to) 去重集合），每次边集变化后全量重算差集 —— 多余的 undepend 掉、
        新出现的 depend 上，再把 `_order_seen` 对齐成当前集合。

        全量重算天然幂等、与调用顺序无关，也不需要给每条回滚路径单独补一次反向
        操作（漏一处就是残留依赖）。节点/边都在画布量级（几十），成本可忽略。
        """
        desired = set()
        for e in self.data_edges:
            desired.add((e.from_node, e.to_node))
        for e in self.order_edges:
            desired.add((e.from_node, e.to_node))
        # 撤掉不再需要的依赖（删边 / 撤销连线 / 预校验回滚）
        for from_node, to_node in sorted(self._order_seen - desired):
            self._tg.undepend(to_node, from_node)
        # 补上新增的依赖（新连线 / 重做）
        for from_node, to_node in sorted(desired - self._order_seen):
            self._tg.depend(to_node, from_node)
        self._order_seen = desired
        return self

    # ---- 编辑（阶段 A：可编辑设计画布）----
    # 这些方法都是「纯数据写回」，返回值可用于 QUndoCommand 的 undo（重做/撤销共轭）。
    # 约定：成功返回被替换/删除的旧值；找不到目标抛 ValueError（调用方据此提示）。
    def set_node_pos(self, node_id: str, x, y):
        """写回节点手动位置（拖拽松手后调用）。返回旧 pos（None 或 (x,y)）。"""
        n = self.nodes.get(node_id)
        if not n:
            raise ValueError(f"节点不存在: {node_id}")
        old = n.pos
        n.pos = (float(x), float(y))
        return old

    def set_node_config(self, node_id: str, config: dict):
        """写回节点 config（属性面板编辑后调用）。返回旧 config（dict）。"""
        n = self.nodes.get(node_id)
        if not n:
            raise ValueError(f"节点不存在: {node_id}")
        old = dict(n.config)
        n.config = dict(config)
        return old

    def remove_data_edge(self, from_node, from_port, to_node, to_port):
        """删除**一条**数据边（撤销单条连线）。返回被删的 DataEdge，找不到返回 None。

        只删除第一条四元组匹配的边（同键值重复边保留），符合「撤销一次连线操作」语义，
        不会误删图中已存在的其它同键值边。

        删完调 `_sync_order_deps` 把底层依赖重算回与边集一致：这对节点若不再有
        任何数据边/顺序边，其顺序依赖会被**撤销**（Wave B：task_graph.undepend）。
        注意是「按当前边集重算」而不是「无条件撤这对依赖」—— 该对节点之间若还挂着
        别的边（例如同时还有一条顺序边），依赖原样保留。
        """
        target = None
        kept = []
        for e in self.data_edges:
            if (e.from_node == from_node and e.from_port == from_port
                    and e.to_node == to_node and e.to_port == to_port):
                if target is None:
                    target = e          # 仅删第一条匹配
                else:
                    kept.append(e)      # 其余同键值边保留
            else:
                kept.append(e)
        if target is None:
            return None
        self.data_edges = kept
        self._sync_order_deps()
        return target

    def remove_order_edge(self, from_node, to_node, reason=""):
        """删除一条顺序边（撤销顺序约束）。返回被删的 OrderEdge，找不到返回 None。

        与 remove_data_edge 同理，删完重算依赖：这对节点若不再有别的边（数据边或
        顺序边），这条顺序依赖随之撤销。
        """
        target = None
        kept = []
        for e in self.order_edges:
            if (e.from_node == from_node and e.to_node == to_node
                    and (not reason or e.reason == reason)):
                target = e
            else:
                kept.append(e)
        if target is None:
            return None
        self.order_edges = kept
        self._sync_order_deps()
        return target

    # ---- 编辑（阶段 B：图片局部编辑）----
    # local_edits 存于 node.config["local_edits"]（list[dict]）。第 4 步
    # export/import_project_json 已整份序列化 node.config，故 local_edits 自动随
    # 工程文件持久化，导出层无需单独处理。
    #
    # 一条局部编辑记录字段：
    #   id          局部唯一 id（le0001...）
    #   target      作用于哪个输出资产（端口名，默认 "out"/"image"）
    #   mode        "transform"（非AI区域变换） | "inpaint"（AI 局部重绘）
    #   region      遮罩区域（归一化 dict）：None=整图；
    #               rect     -> {"type":"rect","x":0..1,"y":0..1,"w":0..1,"h":0..1}
    #               polygon  -> {"type":"polygon","points":[[x,y],...]}（归一化）
    #   instruction 编辑指令文本（去水印 / 背景变蓝 / 换发型 ...）
    #   params      可选参数（inpaint 常用 strength 等）
    VALID_LOCAL_EDIT_MODES = ("transform", "inpaint")

    def get_local_edits(self, node_id: str) -> List[dict]:
        """返回该节点 local_edits 列表（复制，非引用；非 list 自动视为空）。"""
        n = self.nodes.get(node_id)
        if not n:
            raise ValueError(f"节点不存在: {node_id}")
        le = n.config.get("local_edits")
        return list(le) if isinstance(le, list) else []

    def set_local_edits(self, node_id: str, edits: list) -> list:
        """底层写回：整份替换 local_edits 列表，返回旧列表（供 undo 还原）。"""
        n = self.nodes.get(node_id)
        if not n:
            raise ValueError(f"节点不存在: {node_id}")
        old = list(n.config.get("local_edits") or [])
        cfg = dict(n.config)
        cfg["local_edits"] = list(edits)
        n.config = cfg
        return old

    def _make_edit_record(self, edit: dict) -> dict:
        """把调用方给的 edit(dict) 校验并规范成一条记录（不写库）。"""
        if not isinstance(edit, dict):
            raise ValueError("edit 必须是 dict")
        instruction = (edit.get("instruction") or "").strip()
        if not instruction:
            raise ValueError("局部编辑指令 instruction 不能为空")
        mode = edit.get("mode", "transform")
        if mode not in self.VALID_LOCAL_EDIT_MODES:
            raise ValueError(
                f"mode 必须是 {self.VALID_LOCAL_EDIT_MODES}，收到 {mode!r}")
        region = edit.get("region")
        if region is not None and not isinstance(region, dict):
            raise ValueError("region 必须是 dict 或 None")
        return {
            "id": "",                       # 由调用方/命令填充
            "target": edit.get("target", "out"),
            "mode": mode,
            "region": region,
            "instruction": instruction,
            "params": edit.get("params") or {},
        }

    def add_local_edit(self, node_id: str, edit: dict):
        """追加一条局部编辑指令。返回 (idx, eid)。"""
        le = self.get_local_edits(node_id)
        idx = len(le)
        rec = self._make_edit_record(edit)
        rec["id"] = "le%04d" % (idx + 1)
        self.set_local_edits(node_id, le + [rec])
        return idx, rec["id"]

    def update_local_edit(self, node_id: str, idx: int, edit: dict):
        """更新第 idx 条局部编辑（保留原 id）。返回 (old_record, new_record)。"""
        le = self.get_local_edits(node_id)
        if idx < 0 or idx >= len(le):
            raise IndexError(f"local_edit 索引越界: {idx}（共 {len(le)} 条）")
        old = dict(le[idx])
        rec = self._make_edit_record(edit)
        rec["id"] = old.get("id") or ("le%04d" % (idx + 1))
        new_list = list(le)
        new_list[idx] = rec
        self.set_local_edits(node_id, new_list)
        return old, rec

    def remove_local_edit(self, node_id: str, idx: int):
        """删除第 idx 条局部编辑。返回被删记录（dict）。"""
        le = self.get_local_edits(node_id)
        if idx < 0 or idx >= len(le):
            raise IndexError(f"local_edit 索引越界: {idx}（共 {len(le)} 条）")
        removed = le[idx]
        new_list = [e for i, e in enumerate(le) if i != idx]
        self.set_local_edits(node_id, new_list)
        return dict(removed)

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

    def asset_registrations(self) -> List[dict]:
        """汇总各节点产出的 AssetRef 落地情况（供报告 / 判据使用）。"""
        out = []
        for nid, n in self.nodes.items():
            for p, a in n.out_assets.items():
                if isinstance(a, AssetRef):
                    out.append({
                        "node": nid, "port": p, "kind": a.kind,
                        "name": a.name, "asset_id": a.asset_id,
                        "path": a.path, "registered": a.registered,
                    })
        return out

    # ---- 执行器（第 5 步：真正落盘）----
    def set_executor(self, node_id: str, executor: Callable) -> None:
        """替换某个节点的执行器（如挂上 gen_image 真实执行器）。"""
        n = self.nodes.get(node_id)
        if not n:
            raise ValueError(f"节点不存在: {node_id}")
        n.executor = executor

    def use_real_executors(self, asset_root: str, inpaint_fn=None, video_fn=None,
                           motion_fn=None) -> List[str]:
        """第 5 步：给所有节点装上真正落盘的执行器（run 前调用即可生效）。

        `_wrap` 在 run 时实时调 `node.executor`，故 run 前替换 executor 即生效，
        无需重建 TaskGraph。asset_root 为真实落盘根目录；inpaint_fn 透传给
        局部编辑引擎（未注入时 inpaint 模式诚实抛 UnsupportedEditMode）；
        video_fn 透传给 gen_video 真实视频生成（未注入时 gen_video 诚实抛
        UnsupportedEditMode）；motion_fn 透传给 promo_fx 促销动效（未注入时
        promo_fx 回落本地 PIL，零网络）。返回被替换执行器的节点 id 列表。
        """
        from executors import apply_real_executors
        return apply_real_executors(self, asset_root, inpaint_fn, video_fn, motion_fn)

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


def build_sample_graph(asset_store=None) -> CanvasGraph:
    """建一张设计稿 §2 的示例流（无 UI、无真实生成，仅验证数据模型与调度）：

    源·Prompt → 生图 → 生视频 →（分叉）数字人口播 / 促销动效 → 成片输出

    覆盖：多入多出（成片并合）、一出多（生视频喂两个下游）、
    标签（主轨 / 分场接力）、数据边隐含顺序边、纯顺序边演示。
    asset_store 透传给 CanvasGraph（第 2 步接资产库；None → 内存 sink）。
    """
    g = CanvasGraph(asset_store=asset_store)

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
