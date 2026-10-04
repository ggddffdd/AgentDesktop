# 画布手感档验证报告（v4.211.9）

> 对应改动：滚轮缩放 / 框选多选 / 右键建删节点 / 参数控件 schema 化 / topo 真拓扑序根除
> 日期：2026-10-05 ｜ 前置：v4.211.8（三处硬伤修复，见 `CANVAS_RERUN_FIX_REPORT.md`）

---

## 一、动机

大哥实测后判定画布远未达到预期（对标 ComfyUI）。经核对（逐条对了代码，其中
「缺画布平移」一条说重了——`ScrollHandDrag` 一直存在），差距分三档：

| 档 | 内容 | 工程量 |
|---|---|---|
| ① 手感（本轮） | 滚轮缩放 / 框选多选 / 右键菜单建删节点 / 参数控件 | 1~2 轮迭代，不动架构 |
| ② 节点系统 | 注册式节点 + 参数 schema + 分类搜索 | 1~2 周 |
| ③ 执行引擎 | 参数哈希缓存 / 增量执行 / 队列取消 | 再 1~2 周 |

大哥拍板：**先做①**，做完再评估要不要往②③走。

## 二、改了什么（4 文件）

### 1. `task_graph.py`：新增 `TaskGraph.remove_task()`

删节点必须连带删底层任务，且**双向清依赖**（`blocked_by` / `blocks`）——
否则残余任务永远等一个已消失的上游。

### 2. `canvas_graph.py`（CRLF，脚本插入）：新增 `CanvasGraph.remove_node()`

- 先快照该节点参与的**数据边 + 顺序边**，逐条删（每条边删除自带依赖同步）；
- 再删节点本身 + 底层任务；
- 未知节点 id 抛 `KeyError`（诚实失败，不静默）。

**连带修掉一个既有 bug**：`topo()` 原实现**直接返回 `task_list()`**（= `_tasks`
的 dict 插入序，不是拓扑序！）。平时恰好按序插入所以没炸；**删节点再撤销恢复**
后 dict 序被重排 → `layout_graph` 的 `max()` 空序列崩溃。修法：`topo()` 改为
Kahn 算法基于边集真算拓扑序（既有判据的断言顺序 `src,img,vid,twin,promo,final`
不变）。

### 3. `canvas_panel.py`（核心）

| 改动 | 说明 |
|---|---|
| `CanvasView.wheelEvent` | 滚轮缩放，`AnchorUnderMouse`，钳制 `MIN_ZOOM=0.1` ~ `MAX_ZOOM=8.0` |
| dragMode | `ScrollHandDrag` → `RubberBandDrag`（左键框选多选；多选节点可一起拖） |
| 中键平移 | `ScrollHandDrag` 语义挪到中键（`midMouseButtonPress/Release`） |
| 右键菜单 | `CanvasScene.contextMenuEvent`：空白处 → 6 类节点菜单（`NODE_TYPES` 动态生成）→ 鼠标位置创建；节点上 → 删除。菜单动作拆成**可测的** `_apply_menu_choice(kind, payload, pos)`（offscreen 判据不能真弹菜单） |
| `Delete` 键 | `keyPressEvent` 选中节点即删 |
| `AddNodeCommand` / `RemoveNodeCommand` | 均入撤销栈；`RemoveNodeCommand` 撤销时**完整恢复节点+边+底层任务+端口类型** |
| `_undo`/`_redo` 重绘 | 探针抓到真 bug：`undo_stack.undo()` 直调路径不触发重绘（只有按钮路径刷）。统一改**信号驱动**：`undo_stack.indexChanged` → `_refresh_view`，`_undo`/`_redo` 里不再手动刷 |
| `PropertyPanel` 结构化参数 | `PARAM_SCHEMAS`（只登记执行器真读的键：`gen_video`/`promo_fx` 的 `ref_source`/`ref_port`）动态生成 `QLineEdit` 行，值写回 `node.config`。**不摆假输入框**——passthrough 类节点不读 config 就不生成行；`motion_params`/`local_edits` 嵌套结构留给 JSON 框 |

### 4. 文档：`CANVAS_USER_GUIDE.md`

第四节重写为「六个手势」（拖/连/缩放/平移/框选/右键菜单）；
FAQ「从零搭图」改为可行；边界表 2 行改 ✅ 已修 + 新增 1 行（参数控件覆盖范围如实标注）。

## 三、验证

### 判据 `tests/test_canvas_ops.py`：**46 项 / 8 组，PASS=46 FAIL=0**

| 组 | 覆盖 |
|---|---|
| A 缩放 | 滚轮上=放大/下=缩小（钳制内）、0.1x 下限、8x 上限、MIN/MAX_ZOOM 常量存在 |
| B 框选 | RubberBandDrag 已设、Ctrl 多选、多选拖动位置写回 |
| C 右键菜单 | 空白菜单含 6 类节点、节点菜单含删除、`_apply_menu_choice("add")` 真建节点（id 唯一化）、位置 = 鼠标位置 |
| D 删节点 | nodes-1、关联边连带清、底层任务删、**残余任务无悬空依赖**（直测防线：手动塞直连依赖再删）、撤销恢复节点+全部边+端口类型+任务、重做再删、未知 id 抛 KeyError |
| E Delete 键 | keyPressEvent 选中节点即删 |
| F 参数控件 | schema 行生成、值写回 config、无 schema 类型不生成行 |
| G topo 回归 | **删→撤后 topo 仍是真拓扑序**、同状态 `layout_graph` 不崩（原缺陷复现点）、覆盖全部节点、干净图顺序与既有判据一致 |
| H 源码契约 | wheelEvent 钳制引用 MIN/MAX_ZOOM 常量、dragMode=RubberBand、contextMenuEvent 存在、`_run_graph` 流程不变、topo 不再返回 task_list |

### 扰动 `_perturb_canvas_ops.py`：**12 变异全命中，哑弹清零**

（严口径沿用 v4.211.8 定版：**目标判据名必须出现在 FAIL 行**，rc≠0 一律不算命中；
子进程 `timeout=120`，挂起记失败。）

扰动调试中修掉三处判据自身的写法问题：
- D/G 组原走 UI 信号链，变异导致 `layout_graph` 崩溃时异常中断整组 → 判据红了但
  红的是「_d 组抛异常」而非目标名。改**数据层直调**（UI 链已由 C/E 组覆盖）。
- PO10 锚点只盖了 for 块前三行，残留 `blocks` 分支缩进悬空 → 语法错。锚点扩到整块。
- D4 改为直测防线（canvas 场景下 `remove_data_edge` 的依赖同步先跑，`remove_task`
  的清理循环不经边集不触发——单测要打到它得手动塞直连依赖）。

### 既有回归

- 画布族 4 套（含新套件）：164 项全绿。
- `py_compile` 全部改动文件通过。

## 四、边界（如实）

1. **参数控件覆盖面小**：只有 `ref_source`/`ref_port` 有控件，其余键仍走 JSON 框。
   （设计原则：不摆假输入框。扩 schema 是②节点系统的事。）
2. 右键菜单**没有搜索**——6 类节点直接列全。节点类型多了才需要搜索框（②的事）。
3. 框选只在空白处生效；从节点上按左键仍是拖节点（Qt RubberBand 语义）。
4. `topo()` 的 Kahn 实现：图有环时返回**部分序**（连成环在连线时就被拒，所以
   运行期不会出现环——防御性行为，不另加断言）。

## 五、下一步（等大哥拍板）

- ②节点系统：`@register_node` 注册式节点 + 参数 schema 全覆盖 + 分类搜索。
- ③执行引擎：参数哈希缓存 / 增量执行（只跑受影响节点）/ 队列取消。
- 或：①用一阵子，先验证手感再定。
