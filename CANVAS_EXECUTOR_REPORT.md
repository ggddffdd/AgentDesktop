# 节点画布 · 第 5 步执行器 交付报告

> 范围：把画布节点从「stub 只登记阶段路径」升级为「真正产出文件并落盘」的执行器，
> 并让阶段 B/C 写入 `config["local_edits"]` 的局部编辑（transform / inpaint）在运行时真正生效。
> 关联：第 4 步（导出）· 阶段 A（编辑交互）· 阶段 B（局部编辑）· 阶段 C（结果图引擎）。

---

## 1. 目标与边界

画布此前已具备：数据模型 + 编辑交互（A）+ 局部编辑指令（B）+ 结果图引擎（C，纯数组变换）。
但节点 `executor` 仍是第 1 步的 stub——只产出**阶段路径字符串**、不建真实文件，局部编辑也只停在「指令层」。

第 5 步执行器要补的，正是把这条链路真正跑通：

- `gen_image` 节点：**真实生图并落盘**，且其 `local_edits` 在产图后**真实渲染到像素**并落盘结果图；
- 其余节点（gen_video / digital_twin / promo_fx / final_output / source_prompt）：落盘占位文件，保持 run 链路完整、资产可登记；
- `inpaint`（AI 局部重绘）：默认**诚实抛 `UnsupportedEditMode`**（与阶段 C 一致），运行时由外部注入 `inpaint_fn` 接入真模型（如 Agnes 图生图 / OpenHuman 重绘）。

设计原则延续前几步：纯本地、无外部网络/API 依赖，判据可在无头沙箱运行；不伪造结果。

---

## 2. 交付物

### 2.1 `executors.py`（新增，纯逻辑、无 Qt 依赖）

| 符号 | 作用 |
|---|---|
| `gen_image_executor(node, asset_root, inpaint_fn=None)` | gen_image 真实执行器（含局部编辑落盘） |
| `passthrough_executor(node, asset_root)` | 非图片节点落盘占位执行器 |
| `build_executor(node, asset_root, inpaint_fn)` | 按 `node_type` 选执行器工厂（注册表 `DEFAULT_EXECUTORS`） |
| `apply_real_executors(graph, asset_root, inpaint_fn)` | 遍历图给所有节点装配真实执行器 |
| `stage_path / _png_to_array / _array_to_png / _make_source_image` | 文件/路径/源图生成辅助 |

`gen_image_executor` 产出流程：
1. PIL 本地生成源图（按 prompt 稳定的色相 + 水平渐变 + 文字标注）→ 落盘 `{asset_root}/image/{node}_image.png`；
2. 读源图 → numpy，取 `node.config["local_edits"]`；
3. 非空则 `image_local_edit.apply_local_edits(arr, edits, inpaint_fn=inpaint_fn)` → 落盘 `{node}_image_edited.png`（inpaint 经 `inpaint_fn`）；
4. 把编辑图（或源图）作为该 image 端口产出的 `AssetRef.path`。

> 闭包捕获 `node`，run 时实时读 `node.config["local_edits"]`（最新值），与 `_wrap` 实时调 `node.executor` 的机制配合，run 前替换 executor 即生效。

### 2.2 `canvas_graph.py`（编辑）

- `set_executor(node_id, executor)`：替换单节点执行器；
- `use_real_executors(asset_root, inpaint_fn=None)`：按 `node_type` 装配真实执行器，返回被替换的节点 id 列表（`from executors import apply_real_executors` 函数内延迟导入，避免循环依赖）。

### 2.3 `canvas_panel.py`（UI）

- 工具栏新增「运行」按钮 → `_run_graph`：`use_real_executors(os.getcwd()/canvas_runtime)` → `graph.run({})` → 详情区列出各节点落地文件路径。

---

## 3. inpaint（AI 局部重绘）策略

- **未注入 `inpaint_fn`**：`apply_local_edits` 在 inpaint 分支抛 `UnsupportedEditMode`，执行器闭包原样上抛 → 经 `TaskGraph.run` 捕获后该节点 `status=failed`（诚实，不冒充成功）。
- **注入 `inpaint_fn`**：回调签名 `(arr, region, instruction, params) -> arr`，由外部真模型实现（Agnes / OpenHuman 等），执行器把它透传给引擎。
- 判据同时覆盖「未注入诚实抛错」与「注入后生效」，留好接真模型的口子。

---

## 4. 验证

- **判据** `tests/test_canvas_executor.py`：**26/26 ALL GREEN**
  - A 静态（10）：模块/函数存在、`use_real_executor`/`set_executor` 接口、切片含 `apply_local_edits` 与真实写盘；
  - B 行为（14）：真实产合法 PNG 且非空、灰度/多编辑顺序/矩形局部（掩码内变·外不变）、inpaint 未注入诚实抛错 + 经 run 链路 failed、注入回调生效、passthrough 落盘且全部 completed 且资产登记；
  - C 结构（3）：切片 `gen_image_executor` 真实写盘、`apply_real_executors` 含 `node.executor=`、`use_real_executors` 调用 executors 模块。
- **扰动** `_perturb_canvas_executor.py`：**6/6 命中翻红 + 反向基线绿**（非空转）：
  PG1 跳过 `apply_local_edits` → B2 红；PG2 源图不写盘 → B1 红；PG3 不替换 executor → B1 红；
  PG4 跳过 edits 分支 → B2 红；PG5 inpaint 缺失不诚实抛错 → B5 红；PG6 强制回落 passthrough → B1 红。
- **全量画布判据：218 项零回归**
  `model 32 / assetstore 24 / panel 30 / export 18 / edit 37 / localedit 46 / ciimage 25 / executor 26`
- **其余扰动脚本全命中**：export 8/8、edit 8/8、panel 8、assetstore/model OK、ciimage 6/6。

---

## 5. 下一步

- 接真 `inpaint`：在运行时给 `use_real_executors(asset_root, inpaint_fn=agnes_inpaint)` 注入 Agnes 图生图回调（需 API key，运行时提供，不写入仓库）；
- 把 gen_video / digital_twin / promo_fx 的 passthrough 升级为真实生成（依赖外部 API，届时同样走「可插拔 executor + inpaint_fn 思路」）；
- 第 6 步可考虑执行器与第 5 步执行日志 / 资产库 `make_real_asset_store()` 的正式写库对接。
