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

from typing import Any, Callable, Dict, List, Optional, Union
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


# --------------------------------------------------------------------------
# 资产有效性：统一判定入口
# （复审意见：执行器可以被外部替换，不能只靠执行器内部自查）
# --------------------------------------------------------------------------
class CanvasAssetError(RuntimeError):
    """执行器声称产出了资产，但产物不可用（不存在 / 为空 / 越出资产目录）。

    单独一个类型，是为了让「节点业务失败」与「产物根本没落地」在调用方能分开：
    前者是流程问题，后者是执行器/配置问题。task_graph 对两者都标 failed，
    但报告与 UI 需要说清是哪一种。
    """


# 资产有效性四态。UI 的「绿点」只该在 real 这一档给 ——
# `registered`（登记动作成功）与 `validity`（产物到底算不算数）是两件事：
# 占位物同样会被登记（否则调度链断），但它绝不该显示成「真出片了」。
ASSET_REAL = "real"                  # 真产物：文件在、非空、在资产目录内
ASSET_PLACEHOLDER = "placeholder"    # 占位物：流程占位，不承诺真实文件
ASSET_STALE = "stale"                # 历史产物：文件在，但属于上一轮
ASSET_INVALID = "invalid"            # 失效产物：不可用

ASSET_VALIDITIES = (ASSET_REAL, ASSET_PLACEHOLDER, ASSET_STALE, ASSET_INVALID)


def assess_asset(ref, asset_root=None):
    """**资产有效性的唯一判定入口** → (validity, reason)。

    为什么要有这一层：内置执行器（gen_image / gen_video / promo_fx）确实各自调了
    `validate_output()`，但执行器是可以被外部替换的（`CanvasGraph.set_executor`）——
    自定义执行器完全可能返回不存在的路径、空文件、或越出 `asset_root` 的路径，
    那时代码里就没有第二道防线了。所以判定放在**登记这一层**统一做，执行器内部的
    自查只当「尽早报错」的辅助。

    判定顺序（先问「这是什么」再问「文件在不在」）：
      1. 不是 AssetRef / 路径为空              → invalid
      2. `placeholder=True`                    → placeholder
         （**不参与 invalid 判定**：stub 的路径本来就是编出来的、磁盘上没有，
          若按「文件必须存在」判，纯 stub 的图会集体 failed）
      3. `stale=True`                          → stale（上一轮产物，本轮未重新产出）
      4. 不存在 / 为空 / 读不了 / 越出资产目录  → invalid
      5. 其余                                  → real
    """
    if not isinstance(ref, AssetRef):
        return ASSET_INVALID, "不是 AssetRef: %s" % type(ref).__name__
    if not (getattr(ref, "path", "") or "").strip():
        return ASSET_INVALID, "资产路径为空"
    if getattr(ref, "placeholder", False):
        return ASSET_PLACEHOLDER, ""
    if getattr(ref, "stale", False):
        return ASSET_STALE, "上一轮产物：本轮未重新产出"
    path = ref.path.strip()
    if not os.path.isfile(path):
        return ASSET_INVALID, "文件不存在: %s" % path
    try:
        if os.path.getsize(path) <= 0:
            return ASSET_INVALID, "文件为空: %s" % path
    except OSError as e:
        return ASSET_INVALID, "文件无法读取: %s (%s)" % (path, e)
    if asset_root:
        try:
            root = os.path.abspath(asset_root)
            if os.path.commonpath([os.path.abspath(path), root]) != root:
                return ASSET_INVALID, "越出资产目录: %s" % path
        except ValueError:
            return ASSET_INVALID, "越出资产目录: %s" % path
    return ASSET_REAL, ""


def is_usable_asset(ref, asset_root=None) -> bool:
    """这个产物引用能否被下游当「真实素材」读（占位 / 失效 / 历史一律不算）。

    为什么单独收一个布尔入口：`assess_asset` 返回的是带原因的 (validity, reason)，
    消费侧（促销动效收帧、结果图导出……）多数只关心「能不能用」这一个问题。
    没有这个函数时，每个消费点都自己写一遍 `== ASSET_REAL`，还可能各写各的
    （有人只判 `os.path.exists`、有人只判 kind）—— 那正是「占位物被静默当成真素材」
    的来源：占位物的路径是编出来的，磁盘上即使存在也只是一段 manifest 文本，
    喂给 PIL/ffmpeg 会以「解码失败」的形式炸在离真因很远的地方。

    判定仍走 `assess_asset`（唯一事实源），这里只是把结论收敛成布尔。
    """
    return assess_asset(ref, asset_root)[0] == ASSET_REAL


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
    # Wave D（复审 #7）：这条产出是不是「占位」——即由 stub / passthrough 造出来的
    # 流程占位物（没有接真生成）。registered 只说「登记动作成功了」，占位物同样会被
    # 登记（否则调度链断）；两个字段合起来才能回答「到底有没有真出片」。
    placeholder: bool = False
    # 统一资产校验（复审）：这条产物**到底算不算数**。由 CanvasGraph._register_asset
    # 统一调 assess_asset() 写入，取值见 ASSET_VALIDITIES。
    # 与 registered 的分工：registered 只说「写库这个动作成功了」，validity 才回答
    # 「这条资产能不能用」—— invalid 的产物**照样登记**（留证据便于排查），
    # 但 UI 不能给它绿点、下游不能拿它当素材。
    validity: str = ""
    invalid_reason: str = ""
    # 是否属于「上一轮运行留下的产出」：CanvasGraph.run 开跑前统一打标，
    # 本轮重新产出的对象是新建的（不带这个标），跑完仍带标的就是历史产物。
    stale: bool = False
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "asset_id": self.asset_id,
            "path": self.path,
            "name": self.name,
            "registered": self.registered,
            "placeholder": self.placeholder,
            "validity": self.validity,
            "invalid_reason": self.invalid_reason,
            "stale": self.stale,
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

    def to_dict(self) -> dict:
        """端口序列化。

        v2 起写成 {"type": ..., "multi": ...}。v1 只写裸 port_type 字符串，
        `multi` 直接丢失 —— 工程存盘再读回，多入端口会退化成单入（数据丢失，
        且画布上看不出差别，直到运行时被单入端口校验拦住才发现）。
        """
        return {"type": self.port_type, "multi": bool(self.multi)}


def port_from_spec(name: str, spec: Any) -> "Port":
    """从工程文件里的端口 spec 还原 Port（导出/导入共用，保证两侧同构）。

    兼容两种形态：
      * v2  dict  {"type": "image", "multi": true}
      * v1  str   "image"（旧工程文件，multi 恒 False）
    spec 形态非法 → ValueError（由 canvas_export 包成 CanvasImportError 并带定位）。
    """
    if isinstance(spec, dict):
        ptype = spec.get("type")
        multi = bool(spec.get("multi", False))
    elif isinstance(spec, str):
        ptype, multi = spec, False
    else:
        raise ValueError(
            f"端口 {name!r} 的 spec 必须是 dict 或字符串，收到 {type(spec).__name__}")
    return Port(name, ptype, multi=multi)


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
        # Wave D（复审 #7）：本节点当前产出的资产是不是「占位物」。
        # 由 CanvasGraph._wrap 在每次跑完后按产出 AssetRef.placeholder 重算，
        # 供 UI / SVG / 报告把「流程跑通了」和「真出片了」分开讲。
        self.placeholder = False
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
                # Wave D（复审 #7）：stub 的路径是**编出来的**、磁盘上并不存在，
                # 必须显式标成占位 —— 否则「已登记 + 绿点」会读成「真出片了」。
                placeholder=True,
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
            "inputs": {p: pt.to_dict() for p, pt in self.inputs.items()},
            "outputs": {p: pt.to_dict() for p, pt in self.outputs.items()},
            "config": self.config,
            "placeholder": self.placeholder,
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
        # 资产落盘根目录（由 use_real_executors(asset_root) 记下）。
        # _register_asset 用它做「越出资产目录」的围栏判定；None = 没接真实落盘，
        # 只做「文件存在 / 非空」校验，不做目录围栏。
        self.asset_root: Optional[str] = None

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
            # 本轮要重产了：先把该节点**上一轮的**产出标成「历史产物」（stale 档）。
            # 为什么标在这里而不是 run() 开头：只有真跑的节点才该被标 —— 任务图跑过
            # 一次后节点都是终态，再 run 不会重跑任何任务；若在 run 开头无脑标记，
            # 那些「根本没重跑」的产出会被错报成历史产物。
            # 本轮执行器给出的新对象不带这个标；若执行器没重建 out_assets（保留了
            # 旧引用），那它确实仍是上一轮的东西 → 保持 stale 是对的。
            for a in (node.out_assets or {}).values():
                if isinstance(a, AssetRef):
                    a.stale = True
            # 把上游通过数据边传来的 AssetRef 注入节点 in_assets
            for p, a in self._incoming_assets(node.id).items():
                node.in_assets[p] = a
            node.placeholder = False          # 每次重跑都重算，不沿用上一轮结论
            r = node.executor(state)
            # 第 2 步：节点产出 AssetRef 落地到资产库，回填 asset_id；
            # 同时在这一层统一判有效性 —— 执行器可能被外部替换（set_executor），
            # 不能只靠它自己的 validate_output 自查。
            bad = []
            for p, a in node.out_assets.items():
                if isinstance(a, AssetRef):
                    self._register_asset(a)
                    # 只有「给了路径、却不可用」才算假完成 —— 这个区分是刻意的：
                    #   * path **非空** + invalid → 执行器声称产出了、东西却不在
                    #     （不存在 / 空文件 / 越出 asset_root）→ 节点诚实 failed
                    #   * path **为空** + invalid → 执行器压根没给引用，更接近
                    #     「这一轮该端口没产出」→ 不登记、也不 failed，
                    #     让执行器自己的 incomplete 机制去表达
                    if a.validity == ASSET_INVALID and (a.path or "").strip():
                        bad.append((p, a.invalid_reason or "产物不可用"))
            # Wave D（复审 #7）：本节点这轮产出里只要有占位物，节点就是「占位节点」。
            # 记在节点上而不是让调用方自己去翻 out_assets —— UI / 报告都要问这个问题。
            node.placeholder = any(
                getattr(a, "placeholder", False)
                for a in node.out_assets.values() if isinstance(a, AssetRef))
            # 统一资产校验（复审）：**所有**产出都体检完再报错（不能第一条就 break，
            # 否则 UI 只看得到第一个问题）。有不可用产物 → 节点诚实 failed。
            # 只兜「执行器声称产出了、东西却不在」这一种；一个产物都没产出的情况，
            # 由执行器自己的 incomplete 机制表达，这里不替它编造失败。
            if bad:
                raise CanvasAssetError(
                    "节点 %s 产出的资产不可用: %s"
                    % (node.id, "; ".join("端口 %s: %s" % (p, w) for p, w in bad)))
            return r
        return _exec

    def _incoming_assets(self, node_id: str) -> Dict[str, Union[AssetRef, List[AssetRef], None]]:
        """上游产出的资产放在上游节点的 out_assets[port] 上，

        数据边只是连线关系，不持有资产本身。故这里从上游节点的
        out_assets 读回（上游已先跑完，见 connect_data 隐含的顺序边），
        下游 _wrap 在跑自己的 executor 前注入 in_assets。

        **返回值的类型随端口而变**（写在这里是因为自定义执行器最容易踩）：
          * 单入端口（multi=False）→ `AssetRef` 或 `None`（该上游还没产出）
          * 多入端口（multi=True） → `list[AssetRef]`（按边顺序收集，**可能含 None**，
            因为某条上游边可能还没产出资产）
        自定义执行器若按「上游是单个资产」处理，遇到 multi 端口会拿到 list ——
        取用前先判端口 spec 的 multi，别假定类型。
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
        """把一条 AssetRef 落地到资产库 sink，回填 asset_id / registered / validity。

        画布只持引用：这里调用 asset_store.register_asset 做登记，
        不创建/管理真实文件（文件由第 5 步执行器产出）。
        缺 name/path 与真实库一致地拒收（registered=False）。

        **有效性判定统一在这一层做**（复审要求）：执行器可以被外部替换
        （set_executor），它返回的路径不一定真的落盘 —— 只靠执行器内部自查会漏。
        invalid 的产物**照样登记**：资产库里留得下证据，便于事后查「这个节点当时
        为什么红」；但 validity 会写明它不可用，`_wrap` 据此把节点判 failed，
        UI 也不给它绿点。
        """
        if not isinstance(ref, AssetRef) or not ref.kind:
            return ""
        # 统一有效性入口（不依赖任何执行器内部校验）
        ref.validity, ref.invalid_reason = assess_asset(ref, self.asset_root)
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
        # 有效性随登记一起留痕：事后翻资产库能看出这条当时是不是本来就坏的
        ameta["validity"] = ref.validity
        if ref.invalid_reason:
            ameta["invalid_reason"] = ref.invalid_reason
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
    def _edge_pairs(self) -> set:
        """当前「依赖方向」的边集 {(上游, 下游)}：数据边与顺序边等价贡献。

        本图是依赖的唯一事实源，`_sync_order_deps` 的期望值、成环判定都读它 ——
        两处各写一遍循环迟早会走岔（一处忘加 order_edges，成环就漏判）。
        """
        pairs = {(e.from_node, e.to_node) for e in self.data_edges}
        pairs |= {(e.from_node, e.to_node) for e in self.order_edges}
        return pairs

    def edge_path(self, from_node: str, to_node: str) -> List[str]:
        """一条 from_node → to_node 的**有向路径**（含首尾）；不存在返回 []。

        只用来把「环长什么样」讲清楚（BFS 最短路；画布量级几十个节点，成本可忽略）。
        """
        if from_node == to_node:
            return [from_node]
        adj: Dict[str, List[str]] = {}
        for a, b in sorted(self._edge_pairs()):
            adj.setdefault(a, []).append(b)
        prev: Dict[str, Optional[str]] = {from_node: None}
        queue = [from_node]
        while queue:
            cur = queue.pop(0)
            for nxt in adj.get(cur, ()):
                if nxt in prev:
                    continue
                prev[nxt] = cur
                if nxt == to_node:
                    path = [nxt]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return list(reversed(path))
                queue.append(nxt)
        return []

    def would_cycle(self, from_node: str, to_node: str) -> List[str]:
        """若加入 from→to 这条依赖会不会成环？成环返回环路径（首尾同节点），否则 []。

        判法：这条边成环 ⟺ 现在的图里已经存在 to →…→ from 的通路。
        """
        if from_node == to_node:
            return [from_node, to_node]
        back = self.edge_path(to_node, from_node)
        return ([from_node] + back) if back else []

    def _reject_cycle(self, from_node: str, to_node: str) -> None:
        """成环的连线**在变更之前**就拒（复审残留发现②）。

        旧行为：环只在 `run()` 时以「任务图死锁」暴露 —— 用户得先跑一次才知道画错了，
        而且是全局失败、不是「这条线不能画」。TaskGraph.run() 的死锁检查保留作兜底
        （有人绕过本图直接改 _tg 时仍能拦住）。
        """
        cycle = self.would_cycle(from_node, to_node)
        if cycle:
            raise ValueError(
                f"连线会成环：{to_node} →…→ {from_node} 已存在，再加 "
                f"{from_node}→{to_node} 就形成环 {' → '.join(cycle)}；"
                f"任务图必须无环才能调度（连线时即拒绝）")

    def _check_connect(self, from_node, from_port, to_node, to_port) -> str:
        """数据边的前置校验：**只读**，不改图、不碰 TaskGraph。

        返回 'ok'（可以新建这条边）或 'noop'（同一四元组已连过，调用方直接返回）。
        不合法一律抛 ValueError，且保证「抛之前图一个字节都没动」——
        画布 `_finish_link` 就靠这个性质做无副作用预校验，不必再探路回滚。
        """
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
        # 同一四元组重复连接视为幂等 no-op（支持重复调用 / UI 预校验后重连）。
        existing = [(e.from_node, e.from_port) for e in self.data_edges
                    if e.to_node == to_node and e.to_port == to_port]
        if existing:
            if (from_node, from_port) in existing:
                return "noop"
            if not tp.multi:
                raise ValueError(
                    f"{to_node}.{to_port} 是单入端口（multi=False），"
                    f"已有来自 {existing[0][0]}.{existing[0][1]} 的连接；"
                    f"如需多入请把该端口标为 multi=True（拒绝静默覆盖）")
        self._reject_cycle(from_node, to_node)
        return "ok"

    def can_connect(self, from_node, from_port, to_node, to_port) -> bool:
        """**纯校验 API**：现在这条数据边能不能连？能连返回 True，不能连抛 ValueError。

        零副作用（不建边、不改依赖、不动撤销栈）。画布连线前用它预校验即可，
        不必再「先真连一次探路、再删掉回滚」—— 那种探路会把已存在的边短暂删掉
        再重建，还让「预校验」和「真连线」跑在同一段有副作用的代码上。
        """
        self._check_connect(from_node, from_port, to_node, to_port)
        return True

    def connect_data(self, from_node, from_port, to_node, to_port,
                     label="", asset=None):
        """数据边：校验端口存在 + 类型兼容 + 不成环，再建立；同时隐含一条顺序边。"""
        if self._check_connect(from_node, from_port, to_node, to_port) == "noop":
            return self
        edge = DataEdge(from_node, from_port, to_node, to_port, label, asset)
        self.data_edges.append(edge)
        # 数据边自动隐含顺序边：上游先完成。去重与撤销统一交给重算
        # （同一对节点只 depend 一次；这条边被删掉时依赖也会同步撤销）
        self._sync_order_deps()
        return self

    def connect_order(self, from_node, to_node, reason=""):
        """顺序边：只规定先后，不传资产。成环同样连线时即拒。"""
        if from_node not in self.nodes or to_node not in self.nodes:
            raise ValueError(f"节点不存在: {from_node} 或 {to_node}")
        if from_node == to_node:
            raise ValueError(
                f"{from_node} 不能连到自己：自依赖会永远等不到就绪（run 时死锁）")
        self._reject_cycle(from_node, to_node)   # 顺序边一样能把图连成环
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
        desired = self._edge_pairs()
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
                        # Wave D #7：registered = 「登记动作成功」，placeholder = 「是占位物」。
                        # 两个都要报出来，否则「占位流程跑通」会被读成「真出片」。
                        "placeholder": bool(a.placeholder),
                        # 统一资产校验：registered 只说「写库成功了」，validity 才说
                        # 「这条能不能用」—— 占位流程跑通 ≠ 真出片，
                        # 登记成功的坏路径也 ≠ 可用素材。两者都要报出来。
                        "validity": a.validity,
                        "invalid_reason": a.invalid_reason,
                        "stale": bool(a.stale),
                    })
        return out

    def audit_assets(self) -> dict:
        """资产体检报告：按四态汇总全图产出（供 UI / 报告 / 判据消费）。

        与 `asset_registrations()` 的分工：那个是**逐条明细**，这个是**分档计数** ——
        画布状态条只需要知道「几条真产物、几条占位、几条失效」。

        `unassessed` 是第五档：没走过 `_register_asset` 的 AssetRef（例如手工塞进
        out_assets、还没跑过 run）→ validity 为空。**不能把它算进 real**，
        但也不该冒充 invalid —— 它只是"还没体检"。
        """
        counts = {v: 0 for v in ASSET_VALIDITIES}
        counts["unassessed"] = 0
        by_validity = {v: [] for v in counts}
        for r in self.asset_registrations():
            v = r.get("validity") or "unassessed"
            if v not in counts:
                v = "unassessed"
            counts[v] += 1
            by_validity[v].append("%s.%s" % (r["node"], r["port"]))
        return {"counts": counts, "by_validity": by_validity}

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
        # 记下资产根目录：_register_asset 的统一校验要用它做「越出资产目录」围栏
        self.asset_root = asset_root
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
