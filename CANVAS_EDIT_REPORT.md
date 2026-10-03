# 画布阶段 A 交付报告：可编辑设计画布 · 编辑交互层

> 目标（CANVAS_RESEARCH_REPORT.md §4 阶段 A）：把流水线画布升级为**可编辑设计画布**的最小可用层。
> 仅做「编辑 + 撤销」，不碰发布（第 4 步已确认），图片局部编辑留作阶段 B。

## 1. 交付内容

### 1.1 数据层编辑方法（`canvas_graph.py`，纯数据、无 Qt）
- `set_node_pos(node_id, x, y)` — 写回节点手动位置，返回旧 pos（供撤销共轭）
- `set_node_config(node_id, config)` — 写回节点 config，返回旧 config
- `remove_data_edge(from, from_port, to, to_port)` — 删**一条**数据边，返回被删边
- `remove_order_edge(from, to, reason="")` — 删顺序边，返回被删边
- 删边时同步 `_order_seen`：若 (from,to) 这对不再有任何边，则移除去重键，使未来重连能重新 `depend`
- **未改 `task_graph` 本体**（30+ 套件保护）：底层 `depend` 只增不减（无 `undepend`），删除边不回退 `_tg` 依赖——冗余依赖对 `run()` 无害，注释已说明取舍，待 `task_graph` 支持 `undepend` 后可自管重算

### 1.2 视图层编辑能力（`canvas_panel.py`，Qt）
- **节点拖动写回**：`CanvasNodeItem` 持有 `node` 引用，`mouseReleaseEvent` 松手时经 `CanvasScene.on_node_moved` 写回 `node.pos` 并压撤销命令
- **端口拖拽连线**：新增 `PortItem`（输出端口 `R` 可发起）；`CanvasScene` 实现 `begin_link → mouseMove 临时贝塞尔 → mouseRelease 命中输入端口`；命中后预校验（复用 `connect_data` 的端口存在 + 类型兼容校验），通过则压 `ConnectCommand`
- **属性面板**：新增 `PropertyPanel`（右侧），绑定选中节点——显示 id/类型/状态，特例编辑 `prompt` 文本框 + 通用 config JSON 文本框，「应用更改」写回并压 `EditConfigCommand`
- **撤销 / 重做栈**：`CanvasPanel` 持 `QUndoStack`，工具栏「撤销 / 重做 / 删除选中连线」三按钮；`selectionChanged` 信号驱动属性面板刷新
- **四个撤销命令类**（均继承 `QUndoCommand`，`item` 可选、判据可传 None 纯测数据层）：`MoveNodeCommand` / `EditConfigCommand` / `ConnectCommand` / `RemoveEdgeCommand`

### 1.3 可编辑闭环已打通
`pos` 字段（第 4 步加）→ 拖动写回 → `layout_graph` 尊重 → `export_project_json` 落盘 → `import_project_json` 读回还原（第 4 步 `import` 已实现），编辑结果不丢。

## 2. 验证结论

| 套件 | 结果 |
|---|---|
| 阶段 A 判据 `test_canvas_edit.py` | **37/37 通过** |
| 阶段 A 扰动 `_perturb_canvas_edit.py` | **8/8 命中**（删/改核心契约 → 目标判据变红，反向基线全绿） |
| 回归 · 第1步 model | 32/32 |
| 回归 · 第2步 assetstore | 24/24 |
| 回归 · 第3步 panel | 30/30 |
| 回归 · 第4步 export | 18/18 |
| 回归 · 探针 probe | 63/63 |

**判据完全覆盖四能力**：A 静态（符号/代码片段）· B 行为（set_node_pos/set_node_config/remove_data_edge + 四个命令类 redo/undo + 导出→导入 pos 闭环）· C 结构（切片确认命令类绑定底层方法）。

## 3. 本步修掉的真实 bug
- `remove_data_edge` 原实现会删掉**所有**四元组匹配的边（不止一条）。当图里已存在相同四元组的边、又连一条相同边再撤销时，会误删两条。改为「只删第一条匹配，同键值重复边保留」，符合「撤销一次连线操作」语义。

## 4. 当前限制（环境，非代码）
本沙箱对 Qt 部件实例化（建 `QWidget`/`QApplication` 事件循环、开窗口）有拦截策略，`python canvas_panel.py` 实时拖拽/连线交互无法在此自动跑通。已用**纯数据层 + 命令类 redo/undo** 判据充分覆盖逻辑正确性；实时手感需大哥本机 `python canvas_panel.py` 联调确认。

## 5. 下一步（阶段 B：图片局部编辑）
1. 选中 `image/scene` 类节点 → 「局部编辑」按钮打开遮罩编辑器（`QGraphicsPixmapItem` 叠加）
2. 画笔圈选区域 → 生成同尺寸二值 mask
3. 填 prompt + 选模式（非 AI 变换 / AI 重绘）
4. 指令 + mask 存进 `node.config["local_edits"]`（本步 `set_node_config` 已能持久化）
5. 执行（阶段 C / 第5步）：非 AI 走 Qt 直接处理；AI 调 Agnes / 本地 SD-inpaint
