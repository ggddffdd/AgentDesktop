# 节点画布 · 外部复审修复报告（Wave A + Wave B）

> 时间：2026-10-03
> 范围：外部复审 8 条问题中，**先做已获批的两波**（A = #3 + #6，B = #2）
> 提交：`bb779ad`（Wave A）+ `3f0cac8`（Wave B）

---

## 一、外部复审 8 条的逐条实证结论

不照单全收：每条都读源码 + 跑 `py_compile` / 判据实测后才定性。

| # | 复审结论 | 实证 | 处置 |
|---|---|---|---|
| 1 | f-string 内同类引号嵌套是语法错 | **伪报**：项目锁 Python 3.12，PEP 701 允许同类引号复用；`py_compile` 通过 | 不做（记 L213） |
| 2 | `depend` 只增不减 | 真，且有**正在发生**的后果 | **Wave B 已修** |
| 3 | 单入端口被静默覆盖 | 真 | **Wave A 已修** |
| 4 | 导入缺校验（端口/边） | 真 | Wave C（未获批） |
| 5 | 导入非法 port_type 会炸 | 真 | Wave C（未获批） |
| 6 | 执行器产出不校验 | 真 | **Wave A 已修** |
| 7 | 占位节点语义不清 | 部分真 | Wave D（未获批） |
| 8 | 上游选择语义缺失 | 真 | Wave D（未获批） |

---

## 二、Wave A（`bb779ad`）：#3 单入端口 + #6 产出校验

### #3 单入端口禁止静默覆盖（`canvas_graph.py`）
- `connect_data`：`multi=False` 的输入端口已有连接时，**不同来源**的第二条数据边 → `ValueError`。
  旧实现被 `_incoming_assets` 后写覆盖，**静默丢弃在先输入**，用户完全无感知。
- 同一四元组重复连接 → 幂等 no-op（支持连线预校验 / 回滚后重连）。
- `_incoming_assets`：`multi=True` 端口收成 **list**（多入不再等于白标）。

### #6 执行器产出校验（`executors.py`）
- 新增 `_OUTPUT_EXTS` + `validate_output(path, expected_kind, asset_root)`：
  非空路径 / 文件存在 / 非空 / 扩展名相符 / 位于资产目录内 —— 任一不合法抛 `UnsupportedEditMode`。
  **宁可节点诚实 failed，也不登记假完成**（否则下游拿不到文件却继续跑）。
- 接入 `gen_image` / `gen_video` / `promo_fx` 共 4 处。

### 同波修掉的测试自身缺陷
- `test_canvas_panel.py` A8：由「整文件搜子串」改为**结构切片**断言六个状态键齐全（旧写法扰动打不红）。
- `test_canvas_edit.py` B6：改用**独立小图** —— 原用例假设的「新边 `vid.clip→final.main`」其实不成立
  （`final.main` 已被 `twin.video` 占用），它一直是**靠被 #3 修掉的静默覆盖 bug 才通过**。

---

## 三、Wave B（`3f0cac8`）：#2 依赖可撤销 + 与边集全量重算

### 为什么这不是理论问题
1. 画布 `_finish_link` 的连线是「**先 `connect_data` 探路 → `remove_data_edge` 回滚 →
   `ConnectCommand.redo` 正式连**」。旧 `depend` 每次 `append` →
   **每一次鼠标画线**都会在 `task_list()` 里留下 `blockedBy: [x, x]`。
2. 删边 / 撤销连线**不撤依赖** → 幽灵依赖（节点被一条已不存在的线绑住，画布上看不出来）。
3. 一旦误连成环，`run()` 抛「任务图死锁」后**再怎么删边也解不开，只能重启程序**。

### `task_graph.py`
- `depend`：**幂等**（同一对只登记一次，命中即提前 `return self`）+ **拒绝自依赖**
  （`promo`/`twin` 这类「video 进、video 出」节点自连是**类型兼容**的，端口校验拦不住）。
- 新增 `undepend`（`depend` 的逆操作）：`blocked_by` / `blocks` **双向清理**；
  依赖清空后把任务**恢复为入口**（`_entry_ids`），否则「唯一无依赖节点」被撤依赖后
  `run()` 会误报「任务图没有入口节点」。幂等；但任务不存在仍抛错（调用方状态已错，不吞）。

### `canvas_graph.py`：边集是唯一事实源
- 新增 `_sync_order_deps`：以 `data_edges ∪ order_edges` 的 `(from,to)` 去重集合为期望，
  与 `_order_seen` 求**差集** —— 多余的 `undepend`、缺的 `depend`、再回写镜像。
- `connect_data` / `connect_order` / `remove_data_edge` / `remove_order_edge` **四处统一接入**。
  全量重算天然幂等、与调用顺序无关，**不必给每条回滚路径单独补一次反向操作**（漏一处就是残留）——
  这正是旧实现的病根。
- `connect_data` / `connect_order` 拒绝自依赖；删除已被取代的 `_edge_pair_still_present`。

---

## 四、判据与扰动矩阵

| 套件 | 判据数 | 本轮新增 |
|---|---|---|
| model | **58**（原 41） | A17–A22 静态 + B15–B21 行为（含「成环→报死锁→删边→重跑全 completed」） |
| edit | **40**（原 38） | B6d/B6e：连线命令的 undo/redo 必须同步增删底层依赖 |
| export | **19**（原 18） | B10：**导入后底层依赖与导出前完全一致**（只还原边、不重建依赖的话，`run()` 会把该串行的流水线当散点跑 —— 画布上看不出差别，纯 `completed` 断言也抓不到） |
| agnes / assetstore / ciimage / executor / localedit / panel | 31 / 24 / 25 / 40 / 46 / 30 | — |
| **合计** | **313** | |

扰动：`_perturb_canvas_model.py` **11 → 18 条**（新增 `TG_PATH` 变异源，可打在 `task_graph.py` 上）。

**P14 打出一个弱判据**：A19 原来只搜条件行 `if blocked_by_id in t.blocked_by`，
而变异只是在该行尾加注释、`return` 被删掉 —— 判据**照样绿**。
已收紧为「条件行 + 紧邻动作行」精确匹配（记 L219）。

---

## 五、验证结果

| 项 | 结果 |
|---|---|
| 9 套画布判据 | **313 项全绿** |
| 9 个扰动脚本 | **全部命中期望红项 + 反向基线绿** |
| 非画布下游（`task_graph` 被 `agent.py` / `legion*` 共用） | `test_task_graph_cancel` 36/36、`test_task_graph_incomplete` 36/36、`test_frozen_smoke` 143/143 |
| 残留预检 `_perturb_guard.py` | 干净（无 `canvas_mut_*` / `taskgraph_mut_*`） |
| 配色门禁 `ui_hex_guard.py` | 通过（新增违规 0） |

---

## 六、残留发现（**未修**，等大哥点头）

1. **`_finish_link` 的「探路 + 回滚」已属多余**：Wave A 让 `connect_data` 幂等、Wave B 让重算对称，
   现在的探路回滚不会留脏状态；但它仍会**短暂删掉一条已存在的边再重建**。
   更干净的做法是加 `CanvasGraph.can_connect()` **纯校验 API**（只读不改），去掉探路/回滚。
2. **成环仍只在 `run()` 时暴露**（RuntimeError 死锁），不是连线时就拒。
   Wave B 保证「删边即解开」，但用户仍要跑一次才知道画了环。
   要连线上拒绝，需要「先校验、后变更」的原子化（比 Wave B 的改动大）。
3. **Wave C（#4+#5 导入校验）/ Wave D（#7+#8）** 未动。
