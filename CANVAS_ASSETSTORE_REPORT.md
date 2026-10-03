# 节点画布 · 第 2 步接资产库 · 交付报告

> 对应设计稿 §9 第 2 步：「让 AssetRef 真正写进 asset_store.py（register_asset + legion_assets.json），节点跑完把产出 AssetRef 落地，下游按引用读取。画布不直接管文件，只持引用。」
> 第 0 步三坑探针（`probe_graphics.py`）与第 1 步数据模型（`canvas_graph.py`）已完成并验证。

## 交付物

| 文件 | 作用 |
|---|---|
| `canvas_graph.py` | **第 2 步改造**：`CanvasGraph` 增资产库 sink 抽象（`asset_store` 入参 + `_asset_store` + `_register_asset`）；节点 run 完把产出 `AssetRef` 写库并回填 `asset_id`；`AssetRef` 增 `registered` 字段；新增 `_MemStore` / `_resolve_store` / `make_real_asset_store` / `_stub_stage_path` |
| `asset_store.py` | **补 kind 词表**：新增 `prompt` / `video` / `audio` 三个 kind（见下方「关键修复」）—— 让画布产出的资产 1:1 正确入库，不再被归一化成 `other` |
| `demo_canvas_cli.py` | 新增 `--real-store`：走真实 `register_asset`（写**临时隔离目录**，不污染真实库）；报告补充资产落地情况 |
| `tests/test_canvas_assetstore.py` | 判据套件 **24/24 全绿**（A 静态 9 / B 行为 10 / C 结构 5） |
| `_perturb_canvas_assetstore.py` | 扰动验证 **10/10**（9 个变异各命中目标 A 判据 + 1 反向基线） |
| `CANVAS_ASSETSTORE_REPORT.md` | 本文件 |

## 关键修复（诚实记录一个真 bug）

第 2 步首跑时判据 **B9 直接抓到一个集成缺陷**：`asset_store` 的合法 kind 只有
`character_views/keyframe/scene/clip/final/image/script/data/other`，**缺 `prompt` 和 `video`**。
画布节点产出的 `prompt`（源提示词）和 `video`（数字人/促销视频）被 `register_asset`
归一化成 `other` —— 等于"接资产库"没接对桶，丢信息。

设计稿 §9 第 1 步原话是「端口类型本身是资产 kind 的一个子集」。此前这条**并不成立**
（asset_store 缺 prompt/video/audio）。修法：在 `asset_store.py` 的 `KIND_LABELS` 里
**加性补进** `prompt: "Prompt 提示词"` / `video: "视频"` / `audio: "音频"`。
这是加性改动（`_VALID_KINDS` 自动包含），无任何既有逻辑被改；grep 确认没有测试死磕
kind 集合。修后 B9 通过：6 条资产落盘，kind 集合正好是
`{prompt, image, clip, video, final}`。

## 设计决策（保守、最小改动、不污染真实库）

- **资产库 sink 抽象**：`CanvasGraph(asset_store=None)`。`None` → 默认 `_MemStore`（纯内存、
  不落盘、不污染 `~/Documents/小臭玩AI/legion_assets.json`）；生产接真实库时显式传
  `make_real_asset_store()`（包裹 `asset_store.register_asset`）。画布与资产库之间只隔着
  一个 `fn(name, kind, path, **kw) -> (ok, asset_id)` 接口，可注入、可测试。
- **默认不落盘、不污染**：所有自动化判据（含 demo 默认运行）走内存 sink 或临时隔离目录，
  大哥真实资产库零写入。要真写真实库需主动 `--real-store` 且仍重定向到临时目录。
- **写库时机在 run 时**：`CanvasGraph._wrap` 在节点 executor 跑完、产出 `out_assets` 后，
  对每个 `AssetRef` 调 `_register_asset` 落地并回填 `asset_id` / `registered`。
- **下游按引用读取（带 id）**：上游节点先跑完、注册回填 id，下游 `_wrap` 在跑自己 executor
  前从上游 `out_assets` 取回**同一个** `AssetRef` 对象（已带 `asset_id`），故 `in_assets`
  引用天然携带落地 id。判据 B4/B5 验到。
- **缺 name/path 与真实库一致拒收**：`register_asset` 本就拒空 name/path；`_register_asset`
  在缺字段时置 `registered=False` 且不崩，节点状态仍 `completed`（判据 B6）。
- **画布不直接管文件**：stub executor 只给「阶段路径」字符串（`canvas_stage/<kind>/<node>_out.<ext>`），
  不真正建文件；真实文件由第 5 步执行器产出。

## 验证结论（均真跑，非嘴上结论）

```
默认内存 sink：6 节点全 completed；6 条 AssetRef 全 registered + 有 asset_id（mem 序列）
真实库（临时隔离目录）：6 条全 registered；asset_id 为 8 位 hex；legion_assets.json 写 6 条
  kind 集合 = {prompt, image, clip, video, final}（B9 抓到的 other 问题已修）
未知 kind（weird_kind_x）→ 真实库归一化为 other（B10）
下游 in_assets 拿到的 AssetRef 已带 asset_id（引用传播 + 落地回填，B4/B5）
demo --real-store：退出码 0，报告落地 6 条，真实 legion_assets.json 写 6 条（C3-C5）
```

扰动（逐个删掉第 2 步契约写法 → 对应 A 判据必转红，反向基线未变异全绿）：
P01→A1 / P02→A2 / P03→A3 / P04→A4 / P05→A5 / P06→A6 / P07→A7 / P08→A8 / P09→A9。

## 判据套件结构（防回归）

- **A 静态（9）**：sink 入参落地、调用 sink、回填 asset_id、registered 双态、`_wrap` run 时触发、
  stub 给 path、默认内存 sink、真实库接线、AssetRef.registered 字段。
- **B 行为（10）**：默认 sink 落地回填、下游引用带 id、缺字段拒收不崩、真实库写入 6 条 +
  hex id + kind 正确 + 未知 kind 归一化。
- **C 结构（5）**：落地条数、asset_registrations 结构、demo `--real-store` 子进程真写文件。

## 回归核对

- 第 1 步判据 `tests/test_canvas_model.py`：**32/32**（无回归）
- 第 0 步探针 `tests/test_probe_graphics.py`：**63/63**（无回归）

## 下一步（设计稿 §9）

第 0/1/2 步已闭环：三坑探针 → 数据模型 → 接资产库。设计稿 §9 不在本仓库内（用户手持），
后续步骤（第 3 步起，预期含接真实执行器 / UI 画布）待设计稿定位后继续。当前画布已具备
「数据模型 + 资产落地」能力，第 5 步只需把 stub executor 换成 `legion_worker` / `video_pipeline`
的真实执行器，并把阶段路径替换为真实产出文件路径即可，资产库接线无需再动。
