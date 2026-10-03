# 节点画布 · 阶段 C 交付报告：图片局部编辑「结果图」导出

> 前序：第 1-3 步（数据模型 / 资产库 / 渲染）+ 挂 nav（A4 守卫绿）+ 第 4 步渠道层（仅导出）
> + 阶段 A（可编辑设计画布·编辑交互）+ 阶段 B（图片局部编辑·遮罩编辑器 + 指令写入 config["local_edits"]）。
> 本步在阶段 B 的基础上，把"遮罩 + 指令"真正应用到像素，产出可落盘的**结果图**。

## 1. 目标与范围

阶段 B 已能把「遮罩区域 + 编辑指令」存进 `node.config["local_edits"]`（随工程 JSON 自动持久化）。
阶段 C 解决最后一公里：**把这些编辑真正渲染成图并导出**，让画布产物从"结构/状态图"升级为"带局部编辑的结果图"。

范围（与调研报告 §4 阶段 C 对齐）：
- ✅ 本地可执行的 **transform** 类区域变换（亮度/对比度/饱和度/灰度/反相/模糊/锐化），由 `image_local_edit` 引擎用 numpy + PIL 实现；
- ✅ **region 掩码**：矩形 / 多边形（归一化 0..1），区域外像素原样保留（"局部"语义）；
- ✅ **批量导出**：遍历所有图片类节点，把含 `local_edits` 的节点导出为结果图 PNG；
- ✅ GUI 闭环：工具栏「导出局部编辑图」按钮（选目录 → 调用导出）。
- ⏸️ **inpaint（AI 局部重绘）** 不在本步实现模型，但通过 `inpaint_fn` 回调**预留接口**；未注入时诚实抛 `UnsupportedEditMode`，不伪造结果（待第 5 步执行器接 Agnes-inpaint 等真实模型）。

## 2. 改动清单

### 2.1 新增 `image_local_edit.py`（核心引擎，纯 numpy + 延迟 PIL）
- `region_to_mask(shape, region)`：归一化 region → 像素 bool 掩码；`rect` / `polygon` / `None(整图)`；多边形用 PIL `ImageDraw` 画掩码（无头可用）。
- `apply_op(arr, op, params)`：单条 transform（亮度/对比度/饱和度/灰度/反相/模糊/锐化），约定输入 float 0..1。
- `VALID_TRANSFORM_OPS`：7 个操作常量。
- `apply_local_edits(arr, edits, inpaint_fn=None)`：逐条应用；transform 仅在掩码内生效，多条按序叠加；inpaint 调 `inpaint_fn` 或抛 `UnsupportedEditMode`。不改入参、返回新数组。

### 2.2 `canvas_export.py`（第 4 步导出层扩展）
- `_load_arr` / `_save_arr`：PNG ↔ numpy 适配（依赖 PIL，缺失仅降级文件 IO）。
- `export_node_local_edit_image(g, node_id, src_path, out_path, inpaint_fn=None)`：读原图 → 应用该节点 `local_edits` → 写结果图；返回摘要（applied 条数 / size / skipped）。
- `export_all_local_edit_images(g, base_dir, src_resolver=None, inpaint_fn=None)`：遍历图片类节点，仅对含 `local_edits` 且能解析到原图的节点导出 `{node_id}_edited.png`。

### 2.3 `canvas_panel.py`（UI 闭环）
- `LocalEditDialog` 增加「操作(op)」下拉（来自 `VALID_TRANSFORM_OPS_C`）+ 参数输入；`build_edit` 把操作与参数组装进 `params["op"]`（JSON 仍可选覆盖）。
- 工具栏增加「**导出局部编辑图**」按钮 → `_export_local_edited_images`（选目录 → `export_all_local_edit_images`，结果回显到详情区）。函数内懒加载 `canvas_export` 避免循环导入。

## 3. 设计要点

- **归一化解耦**：`region` 用 0..1 归一化，与真实图像尺寸解耦；`local_edits` 存于 `config`，随工程 JSON 自动存读往返，无需导出层单独处理。
- **引擎与 IO 分离**：`image_local_edit` 只做"数组→数组"，文件读写在 `canvas_export`；判据可在无 GUI / 无文件环境下纯内存验证。
- **诚实边界**：inpaint 必须显式注入模型回调，否则抛错——不假装"已重绘"，避免误导下游。
- **局部语义**：掩码外像素 `np.where` 原样保留，符合"局部编辑"直觉；多次编辑按列表顺序叠加。

## 4. 验证

| 套件 | 判据 | 结果 |
|---|---|---|
| `tests/test_canvas_ciimage.py` | 25 | ✅ ALL GREEN |
| `_perturb_canvas_ciimage.py` | 6/6 命中翻红 + 反向基线绿 | ✅ |
| 全量画布判据（model/assetstore/panel/export/edit/localedit/ciimage） | **212** | ✅ 零回归 |
| 其余扰动（export/edit/panel/assetstore/model） | 全命中 + 基线绿 | ✅ |

判据覆盖：rect 区域内变外不变(B1)、contrast(B2)、polygon 仅内变(B3)、PNG 真实读写往返(B4/B4b)、批量仅导出含 edits 节点(B5)、inpaint 默认抛错+注入回调(B6)、多编辑顺序叠加(B7)、整图(B8)、彩色图灰度(B9)；结构切片确认掩码分支与引擎调用链路(C1-C3)。

## 5. 文件产物
- 新增：`image_local_edit.py`、`tests/test_canvas_ciimage.py`、`_perturb_canvas_ciimage.py`、`CANVAS_IMAGE_EDIT_REPORT.md`
- 修改：`canvas_export.py`（结果图导出）、`canvas_panel.py`（操作下拉 + 导出按钮）、`tests/.suite_manifest.txt`（登记新套件）

## 6. 下一步
- **第 5 步执行器**：把 `local_edits` 的 `inpaint` 接 Agnes-inpaint / 其它 AI 模型（实现 `inpaint_fn` 回调并默认注入真模型），让"AI 局部重绘"真正可用；transform 也可在第 5 步由真实执行器产出资产后直接调用本引擎落盘。
- 可选增强：导出时把结果图回填到资产库（`register_asset`），使下游节点消费"已局部编辑"的图。
