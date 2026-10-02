# 节点画布 · 第 1 步数据模型 · 交付报告

> 对应设计稿 §9 第 1 步：「Node / Edge（数据边 + 顺序边）/ AssetRef schema；给 task_graph 加端口概念。验证目标：在 CLI 里用代码建出一张图并跑通（无 UI）。」
> 第 0 步三坑探针已完成并推送（`probe_graphics.py` / `tests/test_probe_graphics.py` / `_perturb_probe_graphics.py`）。

## 交付物

| 文件 | 作用 |
|---|---|
| `canvas_graph.py` | 数据模型本体（纯数据层，无 Qt 依赖）：`AssetRef` / `Port` / `DataEdge` / `OrderEdge` / `CanvasNode` / `CanvasGraph` + `build_sample_graph()` |
| `demo_canvas_cli.py` | CLI 验证：无 UI 建示例图并 run 到全 completed，退出码 0/1 |
| `tests/test_canvas_model.py` | 判据套件 **32/32 全绿**（A 静态 13 / B 行为 10 / C 结构 9） |
| `_perturb_canvas_model.py` | 扰动验证 **9/9**（8 个变异各命中目标 A 判据 + 1 反向基线） |

## 设计决策（保守、最小改动）

- **不改动 `task_graph.py` 本体**：新建独立 `canvas_graph.py`，内部持有一个 `TaskGraph` 复用其调度。避免回归既有 30+ 判据套件。
- **数据边自动隐含顺序边**：`connect_data` 时内部调 `task_graph.depend`，所以 `TaskGraph.run()` 真不用改。去重键 `(from,to)` 保证同一对节点只 depend 一次。
- **节点状态沿用 `Task.status`**：`pending / in_progress / completed / failed / cancelled / incomplete`，不自创枚举。
- **第 1 步只持引用不写库**：`AssetRef` 带 kind（对齐 `asset_store` 白名单），路径/id 留待第 2 步接 `asset_store` 时回填。
- **默认 stub executor**：第 1 步不接真实生成，仅产出该节点类型的默认 `AssetRef`，验证「调度 + 数据流」跑通；真实执行器第 5 步再接。

## 验证结论（均真跑，非嘴上结论）

```
示例图拓扑：src -> img -> vid -> twin -> promo -> final
节点数=6  数据边=6  顺序边=1
全部节点 completed；数据边均已隐含为 TaskGraph 顺序依赖；数据边把上游资产注入下游 in_assets
```

跨档位/类型校验：
- `clip→video` ✅ / `image→image` ✅ / `video→final`(成片输入实为 video 端口) ✅
- `clip→image` 连线时即抛 `ValueError` ✅（接错不等跑一半才炸）
- 成片节点多入（`main`+`promo` 两个 video 端口并合）✅；`vid.clip` 一出二（`twin`+`promo`）✅

## 判据套件结构（防回归）

- **A 静态（13）**：端口类型校验、节点类型校验、端口存在性校验、类型兼容校验、数据边隐含 `depend`、去重 `_order_seen`、状态枚举沿用、多入多出 `multi`、兼容映射 `_PORT_ACCEPTS`、工厂 `build_sample_graph`。
- **B 行为（10）**：真 import 建图 run 全 completed、拓扑正确、数据边隐含顺序边、类型不匹配即拒、多入结构、上游资产注入、`in_assets`、纯顺序边。
- **C 结构（9）**：节点/边计数、标签集合、一出多、重复边去重、兼容组合、subprocess 跑 `demo_canvas_cli` 双保险。
- 扰动：删掉任一契约写法 → 对应 A 判据必转红（含反向：未变异源码全绿），证明判据非空转。

## 已知边界（诚实标注）

- 第 1 步 stub executor 不真正生成资产，只验证数据流；真实生图/生视频/数字人/促销第 5 步接 `legion_worker` / `video_pipeline` 后才有实际产物。
- `final_output` 节点的输入端口类型是 `video`（接受 clip/video/final 汇聚），不是 `final`；`final` 类型只用于它的**输出**。这点与「成片」直觉不同，已在 `_PORT_ACCEPTS` 里写死并判据锁定。

## 下一步（设计稿 §9 第 2 步）

**第 2 步 · 接资产库**：让 `AssetRef` 在第 2 步真正写进 `asset_store.py`（`register_asset` + `legion_assets.json`），节点跑完把产出 `AssetRef` 落地，下游按引用读取。画布不直接管文件，只持引用。
