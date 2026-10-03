# 节点画布第 3 步 · 画布 UI（渲染层）交付报告

> 设计稿 §9 第 3 步：把第 1 步数据模型 + 第 2 步资产引用**画成可见画布**。
> 验证全绿：`tests/test_canvas_panel.py` 30/30 + 扰动 `_perturb_canvas_panel.py` 8/8 非空转；
> 回归：第 2 步 24/24、第 1 步 32/32、探针 63/63，零回归。

## 1. 范围与边界（本期做了什么 / 没做什么）

| 做 ✓ | 本期交付 |
|---|---|
| ✓ | 纯逻辑分层布局 `layout_graph(g) -> ScenePlan`（无 Qt 依赖，可被判据直接验证） |
| ✓ | Qt 视图层 `CanvasNodeItem / CanvasEdgeItem / CanvasScene / CanvasView / CanvasPanel`：渲染节点、数据边（蓝实线贝塞尔）、顺序边（灰虚线）、状态色、资产落地标记、端口圆点 |
| ✓ | 选中节点看详情（状态 / 产出资产 / 已落地数）、适配视图、快照（含 DPR 修正） |
| ✓ | 自带独立预览入口 `python canvas_panel.py`（真开窗口看示例图） |
| ✓ | 复用第 0 步三坑保护法（高 DPI / windowed 安全输出 / ffmpeg _NO_WINDOW 写法常量） |

| 没做（留作单独步，需你批准） | 原因 |
|---|---|
| ✗ 挂进主窗口 nav | nav_defs 被 `test_nav_ia_198.py` A4 **钉死**，动它 = 动 app shell 核心，违背「最小侵入 / 先 1 个改动」。集成步见 §5 |
| ✗ 真拖拽编排 / 连线交互 | 第 3 步只验证「渲染正确」，交互编排是第 4/5 步的活 |
| ✗ 真实执行器抽帧 | 第 0 步探针已验 ffmpeg 写法；正式抽帧归第 5 步执行器 |

## 2. 架构（分层，避免重蹈覆辙）

```
canvas_panel.py
├─ 纯逻辑层（无 Qt）
│   ├─ ScenePlan / NodeSpec / EdgeSpec  数据类 + to_dict/from_dict 往返
│   ├─ status_color(status)             六态 → 主题色
│   └─ layout_graph(g)                  拓扑分层 → 横向流程图几何（节点矩形 + 边 + 画布尺寸）
└─ 视图层（Qt，复用第 0 步三坑）
    ├─ CanvasNodeItem / CanvasEdgeItem  画节点 / 连线 / 端口 / 状态点 / 资产标记
    ├─ CanvasScene.build(plan)          把 ScenePlan 变成可见图元
    ├─ CanvasView                       渲染 + 适配 + 快照（setDevicePixelRatio 修正 DPR）
    └─ CanvasPanel                      工具栏 + 视图 + 选中详情（__main__ 可独立预览）
```

**为什么分层**：判据套件只测纯逻辑层（`layout_graph`），无需 GUI 环境即可真跑；
Qt 视图层的真实渲染由 `__main__` 预览入口人工/集成验证。这与第 0 步探针一致——
「只有把 Qt 真跑起来才知道答案」的事，单独用预览窗口验证，不塞进无显示的判据。

## 3. 关键文件

| 文件 | 改动 | 说明 |
|---|---|---|
| `canvas_panel.py` | **新增** | 画布渲染层（纯逻辑 + Qt 视图），约 380 行 |
| `tests/test_canvas_panel.py` | **新增** | 判据 30/30（A 静态 9 / B 行为 12 / C 结构 4），复用 `check`/`_slice_func` 风格 |
| `_perturb_canvas_panel.py` | **新增** | 扰动 8 项 + 反向基线，证明判据非空转 |
| `CANVAS_PANEL_REPORT.md` | **新增** | 本报告 |

> 第 1/2 步的 `canvas_graph.py` / `asset_store.py` **未改动**（第 3 步只消费它们）。

## 4. 验证结论

- **A 静态组（9）**：layout_graph 存在；三坑保护法（`setDevicePixelRatio` / `CREATE_NO_WINDOW` / `_emit`）落点；`registered` 资产标记属性；分层公式 `1+max(assigned)`；X 按拓扑层展开；状态色六态齐全；`from_dict` 往返。逐函数切片判定，不在整文件搜关键词。
- **B 行为组（12）**：示例图 6 节点 / 7 边（6 数据 + 1 顺序）；X 分层单调、final 在最右、节点矩形两两不重叠；六态颜色非空；`run` 后全 `completed`；资产登记 6 条且**全部已落地**；final 资产标记与 registered 一致。
- **C 结构组（4）**：ScenePlan 往返序列化一致；边 kind 合法；画布尺寸为正。
- **扰动（8/8 + 基线）**：每个变异都让目标判据从 OK 变 FAIL（含 PA 坐标反向同时让 B5/B6 红），反向基线全绿 → 判据非空转。
- **回归**：第 2 步 24/24、第 1 步 32/32、探针 63/63，零回归。

### 一处预期澄清（B11/B12）
初版判据我假设「stub 阶段 registered 全 False」，实测发现**第 2 步的 stub executor 已给 `AssetRef` 补了 `path=_stub_stage_path(...)` + meta**（见 `canvas_graph.py` `_default_executor`），所以内存 sink 下 `registered` 实为**全 True**。判据已改为验「全 True + final 落地 1 个」，与第 2 步「接资产库」真实行为一致。

## 5. 挂进主窗口 nav（需你批准的集成步）

`ui.py` 用 `nav_defs` + `main_stack`(QStackedWidget) 组织页面，导航展平顺序被
`tests/test_nav_ia_198.py` A4 钉死。集成第 3 步画布需：
1. 在 `nav_defs` 按序插入「节点画布」项；
2. 在对应分组成员元组加该名字；
3. `ChatWindow._init_ui` 里加 `_build_canvas_page`（薄 builder，**懒导入** `canvas_panel.CanvasPanel`，不把 380 行塞进 `ui.py`）；
4. `tests/test_nav_ia_198.py` A4 的展平顺序同步更新。

走的是 `xiaochou-panel-embed` skill 的「热插拔薄 builder」路子，不动 app shell 逻辑。
**这步会改 `ui.py` + 撞 nav 测试，属于动核心，需你点头再动手。**

## 6. 下一步（设计稿 §9 后续）

- 第 4 步 · 渠道层（导出/发布到抖音/视频号等，对应画布的成片节点下游）
- 第 5 步 · 接真实执行器（Agnes 生图/生视频、ffmpeg 抽帧、数字人口播真正落盘产出 path）
- 集成步 · 挂主窗口 nav（见 §5，需批准）

> 下一步建议：先把「挂进主窗口」做了（让画布在 app 里可见），还是先走第 4/5 步补全功能？等你定。
