# 节点画布 · 外部复审修复报告（Wave C + Wave D）

> 时间：2026-10-03
> 范围：Wave A/B 报告 §六 的 **3 条未修发现全做** + Wave C（#4/#5）+ Wave D（#7/#8）
> 提交：`934ac10`（源码 + 判据 + 扰动 + 基线条目）
> 说明：Agnes 凭据轮换不在本轮范围（大哥确认数据都在本地，无需处理）

---

## 一、本轮三块工作

| 块 | 内容 | 性质 |
|---|---|---|
| 发现① | `_finish_link` 的「探路 + 回滚」→ 纯校验 API | 消除多余副作用 |
| 发现② | 成环从「`run()` 时死锁」提前到「连线时即拒」 | 错误提前暴露 |
| Wave C | #4 导入缺校验 / #5 非法 port_type 会炸 | 报错可定位 |
| Wave D | #7 占位语义 / #8 上游选择语义 | 语义显式化 |

---

## 二、发现①：`_finish_link` 不再「探路 + 回滚」

**病根**：UI 连线靠「先 `connect_data` 探路 → `remove_data_edge` 回滚 → `ConnectCommand.redo` 正式连」。
Wave A/B 后探路不再留脏状态，但它仍会**短暂删掉一条已存在的边再重建** —— 属于"用真实副作用做可行性判断"。

**做法**：把 `connect_data` 的校验体整体抽成**只读** `_check_connect(from_node, from_port, to_node, to_port) -> "ok" / "noop"`；
对外暴露纯校验 API `can_connect(...) -> bool`；`connect_data` 改为先 `_check_connect` 判 `noop` 再建边。
`_finish_link` 用 `can_connect` 预校验，去掉探路/回滚两条路径。

**实证**：B21 系列用 snapshot 比对 `data_edges / order_edges / _order_seen / _pairs_of` 四组状态，
证明 `can_connect` 调用前后**零副作用**（P24 扰动「can_connect 偷偷建边」被这组判据抓住）。

---

## 三、发现②：成环 → 连线时即拒

**病根**：误连成环只在 `run()` 抛「任务图死锁」，用户要先跑一次才知道画了环。

**做法**（`canvas_graph.py`）：
- `_edge_pairs()`：`data_edges ∪ order_edges` 的 `(from,to)` 去重对集合（也是 `_sync_order_deps` 的唯一事实源）；
- `edge_path(from, to)`：BFS 最短路，返回路径列表；
- `would_cycle(from, to)`：若已存在 `to → from` 通路则返回该环路径；
- `_reject_cycle(from, to)`：成环即抛 `ValueError`，报错含 `' → '.join(cycle)`；
- `connect_data`（经 `_check_connect`）与 `connect_order` **两处都拦**。

**兜底**：`TaskGraph.run()` 的死锁检查保留 —— 依赖也可能被画布之外的代码改动。
B20e–B20h 用 `gb7._tg.depend("x","y")` **绕过画布**造环，验证「run() 报死锁 → `undepend` 解开 → 重跑全 completed」这条恢复路径仍然成立。

---

## 四、Wave C（#4 + #5）：导入校验可定位

**做法**（`canvas_export.py`）：
- 新增 `CanvasImportError(ValueError)`：把「用户给的工程文件不对」与「我们自己代码有 bug」分开 ——
  前者该弹提示让用户改文件，后者该上报崩溃。混成一个 `ValueError` 时 UI 只能一律说「导入失败，请重试」，用户重试一百次也没用。
- `_validate_project(gd, path)` 前置结构校验：顶层对象 / `nodes` 为对象 / 节点 id 与 type / 端口类型与 `multi` 布尔 /
  `config` / `pos` / 两个边数组 / 边端点存在 —— **每条错误带 `basename(path)` + 节点名或边下标**。
- `_split_endpoint(e, key, i, where)`：`data_edges` 要求 `节点.端口`，`order_edges` 只要求节点 id。
  旧实现直接 `e["from"].split(".", 1)` 解包，少一个点就抛「not enough values to unpack」，连是哪条边都不说。
- `_require_nodes(...)`：边引用了不存在的节点时，报错里列出文件内实际节点（前 8 个）。

### 顺带挖出更严重的：工程文件**真的丢数据**
`export_project_json` 的 v1 端口只写端口类型串：

```python
"inputs": {"img": "image"}          # multi 信息在存盘时就被丢掉
```

→ 多入端口（`multi=True`）**读回来退化成单入**。表现是：存盘 → 重开 → 第二条线一连就被「单入端口禁止覆盖」拦住，
而画布上**看不出任何差别**（端口本来就长得一样），只能运行时才炸。

**修**：`"version": 2`，端口写 `{"type": ..., "multi": ...}`；新增模块级 `port_from_spec(name, spec)`
（兼容 v2 dict 与 v1 裸字符串，非法形态抛 `ValueError`）；导入侧对 v1 保持向后兼容。
判据 C3（multi 端口导出→导入保留）、C4（`version == 2`）、B17（v1 裸字符串仍能读）。

---

## 五、Wave D #7：占位语义

**病根**：stub / passthrough 执行器会**写出一个占位文件**（内容占位，扩展名却起成 `.mp4` / `.png`），
但它和真生成物在画布上**长得完全一样**（都是实心绿点 = 已登记资产）。用户以为出片了。

**做法**：
- `AssetRef.placeholder: bool = False`（进 `to_dict()`）；`CanvasNode.placeholder`（进 `to_dict()`）；
- `_default_executor` 的 `_stub` 产出标 `placeholder=True`；`passthrough_executor` 同样；
- `_wrap`：**进入执行前** `node.placeholder = False`（重置），**跑完**按实际产出重算
  `node.placeholder = any(a.placeholder for a in node.out_assets.values() if isinstance(a, AssetRef))`
  —— 异常时不残留上一次的旧标记；
- `asset_registrations()` 每条带 `"placeholder": bool(a.placeholder)`；
- **UI**（`canvas_panel.py`）：状态文字 `status + " · 占位"`；资产标记三态 ——
  占位画**空心橙圈**、真出片画实心绿、未登记画灰；详情区单列「占位产出: N 个（未接真生成，仅流程占位）」；
- **SVG**（`canvas_export.py`）：`if n.placeholder:` 画空心橙圈（`#E37400`）+ 文字「占位」，
  `elif n.registered:` 才画实心绿点。

**核心是把两件事拆开讲**：`registered`（有产出物）与 `placeholder`（产出物是占位物）不是一回事。

---

## 六、Wave D #8：上游选择确定化

**病根**：多入端口节点取"第一个上游资产"时，旧实现**按连线时序**取 —— 先连哪条就用哪条。
用户改一下连线顺序，出片用的参考图就换了，且**不可复现、无提示**。

**做法**（`executors.py`）：
- `_direct_upstream(graph, node, want_kind)`：按 `node.inputs` **声明顺序**取直接上游；
- `_upstream_assets(...)`：直接上游按端口序 + 递归补齐，按 path 去重，返回 `[{"path","port","from"}]`；
- `select_upstream(graph, node, want_kind) -> (path, desc)`，优先级：
  `config["ref_source"]` → `config["ref_port"]` → 端口声明顺序第一个。
  **显式指定指不到时 `raise UnsupportedEditMode`** —— 诚实抛错，不静默回落（否则用户写了 `ref_source` 却被忽略，结果对不上还以为是自己写错了）。
- `gen_video_executor` 返回值新增 `"src_from"`；`promo_fx_executor` 新增 `"frames_from": [...]`，**把选了谁回显到执行结果里**。

---

## 七、判据与扰动矩阵

| 套件 | 判据数 | 本轮新增 |
|---|---|---|
| model | **89**（58 → 89） | A23–A31 静态 + B20 系列（成环即拒/无残留/报错含环/顺序边也拦/绕过画布的兜底恢复）+ B21 系列（can_connect 零副作用）+ B22（edge_path/would_cycle）+ B23（占位语义 + 重算 + 异常不残留）+ B24（端口 schema 往返 + v1 兼容 + 非法 spec） |
| export | **34**（19 → 34） | A8–A13 静态（含 A12/A13 护栏扫描边界，见 §十）+ B11–B17（非法 port_type / 幽灵节点 / 缺 `节点.端口` / nodes 非对象 / 非 JSON / 边引不存在节点 / v1 兼容）+ C3/C4（multi 往返、version==2） |
| executor | **57**（40 → 57） | A14–A17 静态 + B11 系列（stub 占位但仍 registered）+ B12/B12b + B13/B13b（端口声明顺序 vs 连线顺序）+ B14 系列（ref_source / ref_port 生效与指不到抛错）+ B15（`src_from`） |
| panel | **39**（30 → 39） | A10–A15 静态（含**组合串**断言、A15 切片器自证）+ B13（6 节点全占位）+ B14/B14b（NodeSpec 往返保留 placeholder） |
| assetstore / ciimage / edit / localedit / agnes | 24 / 25 / 40 / 46 / 40 | — |
| **画布合计** | **394** | |

扰动：`_perturb_canvas_model.py` **18 → 32**、`_perturb_canvas_export.py` **8 → 15**、
`_perturb_canvas_executor.py` **12 → 17**、`_perturb_canvas_panel.py` **8 → 15**；另 5 个未改动脚本照样全绿。
（收尾轮 export/panel 各 +1：U1 护栏扫描边界回退、PT 切片器回退，见 §十）

### 同波修掉的判据/扰动自身缺陷
1. **A25 误红**：`_check_connect` 定型后 `_reject_cycle` 的调用点挪了，原锚点失配 → 改断言 `CC`。
2. **A12 误红**：`_slice_func("CanvasNodeItem")` 对**类**会切在类体第一个 `def __init__` 处（切片不含类头）→
   当时用 `SRC.find("class CanvasNodeItem")` 绕开。**根因已在收尾轮修掉**（§十.4），A12 已改回用 `_slice_func`。
3. **P23/P24/P31 假绿**：B 组 `import canvas_graph` 拿的是仓库真文件，`CANVAS_PATH` 变异副本**只影响 A 组切片** →
   给 `tests/test_canvas_model.py` 加「若设 `CANVAS_PATH` 则 `spec_from_file_location` 加载并写 `sys.modules`」，
   并给 `run_judge` 加 `static=False` 以便跑 B 组。
4. **PL 未命中 A12**：原判据分别搜 `"spec.placeholder"` 与 `"Qt.NoBrush"`，把 `if spec.placeholder:` 改成 `if False:`
   后两个串都还在 → 收紧为**组合串** `"if spec.placeholder:\n            mark.setBrush(QBrush(Qt.NoBrush))"`。

---

## 八、验证结果

| 项 | 结果 |
|---|---|
| 9 套画布判据 | **394 项全绿**（收尾轮 +3） |
| 9 个扰动脚本 | **全部命中期望红项 + 反向基线绿**（32 / 10 / 15 / 15 / 8 / 7 / 6 / 17 / 8） |
| 非画布下游（`task_graph` 被 `agent.py` / `legion*` 共用） | `test_task_graph_cancel` 36/36、`test_task_graph_incomplete` 36/36、`test_frozen_smoke` 143/143 |
| 残留预检 `_perturb_guard.py` | 干净（未发现残留变异标记） |
| 配色门禁 `ui_hex_guard.py` | **RESULT = True**（扫描 50 文件 / 新增违规 0 / **存量 0** —— 收尾轮把导出层 7 处裸 hex 收编 THEME 后，基线回到全空） |

---

## 九、残留发现（6 条）—— 收尾轮已全部处理

| # | 原发现 | 处置 |
|---|---|---|
| 1 | `#666666` 不在 THEME → HARD 拦构建 | **已修**：THEME 新增 `canvas_status_text`，**取精确值 `#666666`** → 零视觉变化 |
| 2 | 6 处「THEME 内却写死」（SOFT，不阻塞） | **已修**：全部改走 `THEME[key]`（值相同，零视觉变化） |
| 3 | 护栏扫描范围靠子串隐式判定 | **已修**：判定改用**剥注释后的源码**（+ 2 条判据 + 1 个扰动） |
| 4 | `_slice_func` 切 class 会在第一个 `def` 截断 | **已修**：结束边界按缩进判定（+ 1 条判据 + 1 个扰动） |
| 5 | A30 切片含 `_make_edit_record` 的 `"target"` | **原判断有误，实测不存在**（见 §十.5） |
| 6 | 项目记忆的行尾说法与实际不符 | **已修**：项目记忆已同步实测结论 |

---

## 十、收尾轮：6 条残留全部处理（大哥「残留问题处理掉然后重新打包」）

### 10.1 导出层 7 处裸 hex 收编 THEME（残留①②，**零视觉变化**）

`canvas_export.py` 顶层加 `from ui import THEME` —— **不新增依赖层次**：
它本就 `from canvas_panel import layout_graph, region_svg_overlay`，而 `canvas_panel` 顶层已 `from ui import THEME`。

| 位置 | 原裸 hex | 改为 | 视觉 |
|---|---|---|---|
| 数据边描边 | `#1A73E8` | `THEME["accent"]` | 同值 |
| 顺序边描边 | `#9AA4B2` | `THEME["canvas_pending"]` | 同值 |
| 节点状态文字 | `#666666` | `THEME["canvas_status_text"]`（**THEME 新增键，取精确值**） | 同值 |
| 占位空心圈 ×2 | `#E37400` | `THEME["canvas_incomplete"]` | 同值 |
| 真出片实心点 | `#1E8E3E` | `THEME["canvas_completed"]` | 同值 |
| 局部编辑角标 | `#D93025` | `THEME["canvas_failed"]` | 同值 |

> ⚠️ 这里**没有**把 `#666666` 改成最近的 THEME 灰 `#6B7280`（那会动实际色值 102,102,102 → 107,114,128）。
> 做法是**在 THEME 里新增一个取精确值的键** —— 既满足「颜色必须走 THEME」的纪律，又做到零视觉变化。
> 这与之前画布 10 个语义色键的处理方式一致。

**硬证据（"零视觉变化"不是嘴上说的）**：用 `git show HEAD:canvas_export.py` 取改动前版本，
让**新旧两版对同一张图各导出一次 SVG，逐字节比对 → 3089 B vs 3089 B，完全一致**。

顺手修正一处**早就过时的 docstring**：`export_png` 原写「Qt 懒加载，import 本模块不触发 PySide6」——
实测 `import canvas_export` 已加载 **23 个 PySide6 模块**（经 canvas_panel → ui），该说法从来就不成立，已改准。

### 10.2 护栏扫描范围改由源码决定，不由注释（残留③）

`_find_ui_files` 的启发式判定从 `'THEME' in txt`（原文，含注释）改为 `'THEME' in probe`（**剥注释后的源码**）。

- **改动前先做对比实验**：原版扫描 51 文件 → 剥注释版 50 文件，**只掉出 `release_check.py`**
  （它仅在注释里提到这些词；且它当前 **0 处裸 hex 命中** → 零影响）。
- `canvas_export.py` 靠**真实的 `from ui import THEME`** 入选，不再依赖注释巧合。
- **新增判据 A12**：用探针文件断言「注释里的 THEME 不算数、真实 import 才算数」；
  **A13**：断言 `canvas_export.py` 必在扫描集合内（防止将来把顶层 import 改成函数内延迟 import 而静默掉出）。
- **新增扰动 U1**：把判定回退成按原文 → A12 翻红 ✓

### 10.3 基线条目清空

`#666666` 已走 THEME → 从 `ui_hex_baseline.txt` 移除该条目，基线**回到全空**（护栏：新增 0 / 存量 0）。

### 10.4 切片器切 class 的根因修复（残留④）

`tests/test_canvas_panel.py` 的 `_slice_func` 结束边界**只找「下一个 class/def」，没做缩进比较**
（`indent` 算出来完全是死代码）→ 目标本身是 class 时就切在类体第一个 `def __init__`。

修：结束边界改为「第一个缩进 ≤ 目标缩进的 class/def」。

> 顺带修掉一个**隐蔽偏差**：起始正则 `^\s*` 里的 `\s` **含换行** ——
> `m.group(0)` 会把前导空行也算进缩进，导致 indent 偏大（顶层 def 算出 2、方法算出 5，而非 0 / 4）。
> 当前恰好等价（Python 缩进只有 0/4/8），但**空行数一变就可能改切片边界**，属"埋着的雷"。
> 同一偏差在 `test_canvas_model.py` / `test_canvas_assetstore.py` 里也存在，已一并改成 `^[ \t]*`。

- A12 改回用 `_slice_func("CanvasNodeItem")`（不再绕道 `SRC.find`）；
- **新增判据 A15**：切 class 必须含类头 + 类体末尾方法（`mouseReleaseEvent`）；
- **新增扰动 PT**：把缩进判定回退成「找到就停」→ A15 翻红 ✓
- `_perturb_canvas_panel.py` 因此扩出「**变异测试脚本自身**」这一路（原来只能变异 `canvas_panel.py`），
  并顺带修了它统计口径（`扰动总数` 只算 `MUTATIONS`，漏了新加的 `TEST_MUTATIONS`）。

### 10.5 ⚠️ 更正：残留⑤ 是我**报错了**（实测不存在）

原报告写「A30/P29 的 `asset_registrations` 切片含缩进 8 的 `_make_edit_record`，其中也有 `"target"` 字符串」。

**实测**：`_make_edit_record` 在 **684 行**、`asset_registrations` 在 **756 行** ——
前者在**切片起点之前**，根本不在切片内；切片长度 719、**不含 `"target"`**、切片内无其它 def。
**前提搞反了**（把「文件里存在这个函数」当成了「它在切片内」）。该条**作废，无需修**。

> 教训与 §七.2 同源：**判据/结论必须用切片的实际内容验证**，
> 不能从「文件里有这个函数」倒推「它会落在切片里」。

### 10.6 收尾轮验证

| 项 | 结果 |
|---|---|
| 9 套画布判据 | **394 项全绿**（+3：panel A15、export A12/A13） |
| 9 个扰动脚本 | 全部命中 + 反向基线绿（panel 14→15、export 14→15） |
| 非画布下游 | `test_task_graph_cancel` 36/36、`test_task_graph_incomplete` 36/36、`test_frozen_smoke` 143/143 |
| 残留预检 `_perturb_guard.py` | 干净 |
| 配色门禁 `ui_hex_guard.py` | **RESULT = True**（扫描 50 文件 / 新增 0 / 存量 0） |
| SVG 产物比对 | 与改动前**逐字节一致**（3089 B） |
