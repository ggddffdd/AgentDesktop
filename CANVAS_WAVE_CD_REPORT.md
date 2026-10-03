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
| export | **32**（19 → 32） | A8–A11 静态 + B11–B17（非法 port_type / 幽灵节点 / 缺 `节点.端口` / nodes 非对象 / 非 JSON / 边引不存在节点 / v1 兼容）+ C3/C4（multi 往返、version==2） |
| executor | **57**（40 → 57） | A14–A17 静态 + B11 系列（stub 占位但仍 registered）+ B12/B12b + B13/B13b（端口声明顺序 vs 连线顺序）+ B14 系列（ref_source / ref_port 生效与指不到抛错）+ B15（`src_from`） |
| panel | **38**（30 → 38） | A10–A14 静态（含**组合串**断言）+ B13（6 节点全占位）+ B14/B14b（NodeSpec 往返保留 placeholder） |
| assetstore / ciimage / edit / localedit / agnes | 24 / 25 / 40 / 46 / 40 | — |
| **画布合计** | **391** | |

扰动：`_perturb_canvas_model.py` **18 → 32**、`_perturb_canvas_export.py` **8 → 14**、
`_perturb_canvas_executor.py` **12 → 17**、`_perturb_canvas_panel.py` **8 → 14**；另 5 个未改动脚本照样全绿。

### 同波修掉的判据/扰动自身缺陷
1. **A25 误红**：`_check_connect` 定型后 `_reject_cycle` 的调用点挪了，原锚点失配 → 改断言 `CC`。
2. **A12 误红**：`_slice_func("CanvasNodeItem")` 对**类**会切在类体第一个 `def __init__` 处（切片不含类头）→
   改用 `SRC.find("class CanvasNodeItem")` 到下一个 `"\nclass "`。**这是 `_slice_func` 的通用坑**。
3. **P23/P24/P31 假绿**：B 组 `import canvas_graph` 拿的是仓库真文件，`CANVAS_PATH` 变异副本**只影响 A 组切片** →
   给 `tests/test_canvas_model.py` 加「若设 `CANVAS_PATH` 则 `spec_from_file_location` 加载并写 `sys.modules`」，
   并给 `run_judge` 加 `static=False` 以便跑 B 组。
4. **PL 未命中 A12**：原判据分别搜 `"spec.placeholder"` 与 `"Qt.NoBrush"`，把 `if spec.placeholder:` 改成 `if False:`
   后两个串都还在 → 收紧为**组合串** `"if spec.placeholder:\n            mark.setBrush(QBrush(Qt.NoBrush))"`。

---

## 八、验证结果

| 项 | 结果 |
|---|---|
| 9 套画布判据 | **391 项全绿** |
| 9 个扰动脚本 | **全部命中期望红项 + 反向基线绿** |
| 非画布下游（`task_graph` 被 `agent.py` / `legion*` 共用） | `test_task_graph_cancel` 36/36、`test_task_graph_incomplete` 36/36、`test_frozen_smoke` 143/143 |
| 残留预检 `_perturb_guard.py` | 干净（未发现残留变异标记） |
| 配色门禁 `ui_hex_guard.py` | **RESULT = True**（新增违规 0 / 存量 1 处已基线豁免） |

---

## 九、残留发现（**未修**，等大哥点头）

1. **`canvas_export.py:#666666` 建议改走 `THEME[key]`**。
   本轮我写的一句注释里含 `THEME` 字样，把 `canvas_export.py` **首次拉进 `ui_hex_guard` 扫描范围**
   （`_find_ui_files` 判据是 `'THEME' in txt`），于是暴露出一批 HEAD 既有颜色。
   其中 `#666666`（节点状态文字 `fill`）不在 THEME 里 → HARD 拦构建。
   THEME 最近的等价色是 `faint / placeholder / weak = #6B7280`。
   **改它会动实际色值**（102,102,102 → 107,114,128），按铁律必须大哥批准 —— 本轮先登记进 `ui_hex_baseline.txt`，零视觉变化。
2. **同批暴露的 6 处「THEME 内却写死」（SOFT，不阻塞）**：
   `#1A73E8`（数据边）、`#9AA4B2`（顺序边）、`#1E8E3E`（真出片点）、`#D93025`（局部编辑角标）、`#E37400`×2（占位圈/文字）。
   建议下一轮统一 `THEME[key]`，但要先打通 `canvas_export` 取 THEME 而不破「import 不触发 PySide6」的契约。
3. **护栏扫描范围靠子串隐式判定**：`_find_ui_files` 里 `'THEME' in txt` 决定文件是否被扫 ——
   **我加一句注释就改变了扫描集合**，这判据本身是脆的。建议改成显式白名单/黑名单。
4. **`_slice_func` 对 class 不可用**（见 §七.2）：它按 `def ` 切函数，类体会被切在第一个 `__init__`。测试工具里应加防护或改名说明。
5. **已知脆弱性（当前未触发）**：A30/P29 用 `_slice_func("asset_registrations")` 切片，结束边界是缩进 `<= 4` 的 `class`，
   而 `_make_edit_record`（缩进 8）落在切片内，其中也含字符串 `"target"`。将来若 `asset_registrations` 里出现同名串会误判。
6. **项目记忆与实际不符**：记忆写「本仓生产源码全 CRLF、tests 全 LF」，
   实测 `core.autocrlf=true`、**blob 全部 LF 存储**、工作区混杂（未改动源码 CRLF 36 / LF 105）。
   → 影响所有「按行尾做字节匹配」的脚本前提，已同步修正项目记忆。
