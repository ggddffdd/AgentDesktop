# 画布阶段 B 交付报告：图片局部编辑（遮罩编辑器 + 指令写入 config）

> 承接阶段 A（编辑交互层），按 `CANVAS_RESEARCH_REPORT.md §4 阶段 B` 落地「图片局部编辑」。
> 目标：选中图片类节点 → 打开遮罩编辑器 → 填指令+模式 → 写入 `node.config["local_edits"]`，
> 并补齐撤销栈 + SVG 导出标记。`local_edits` 存于 `config`，已随第 4 步工程 JSON 自动持久化。

---

## 1. 交付内容

### 1.1 数据层（`canvas_graph.py` · `CanvasGraph`）
- `get_local_edits(node_id)` — 返回 local_edits 列表（复制，非 list 自动视为空）
- `set_local_edits(node_id, edits)` — 整份替换（底层写回，返回旧列表供 undo）
- `_make_edit_record(edit)` — 校验 `instruction` 非空 + `mode∈{transform,inpaint}`，规范成记录
- `add_local_edit` / `update_local_edit` / `remove_local_edit` — 增/改/删，返回索引/记录

记录字段：
```json
{
  "id": "le0001",
  "target": "image",
  "mode": "transform | inpaint",
  "region": {"type":"rect","x":0..1,"y":0..1,"w":0..1,"h":0..1}
            | {"type":"polygon","points":[[x,y],...]} | null(整图),
  "instruction": "背景换成蓝天",
  "params": {"strength":0.5}
}
```

### 1.2 撤销命令（`canvas_panel.py`）
- `AddLocalEditCommand` / `RemoveLocalEditCommand` / `EditLocalEditCommand`
- 均以 `set_local_edits` 整份快照做 **idempotent** 的 redo/undo（无 GUI 下可构造并测试）

### 1.3 视图层（`canvas_panel.py`）
- 纯几何辅助（无 Qt，判据可用）：`node_supports_local_edit`、`norm_rect`、
  `region_svg_overlay`
- `MaskEditorWidget`（`QGraphicsView`）：归一化 0..1 预览区，支持**矩形拖拽** + **多边形点选双击闭合**，
  选区存为归一化 region（与真实图像尺寸无关，执行器按原图尺寸还原）
- `LocalEditDialog`：目标资产 / 模式 / 指令 / 参数(JSON) / 遮罩编辑器，确认后产出 edit dict
- `PropertyPanel`：图片类节点显示「局部编辑(N)」按钮，点击经回调打开对话框
- `CanvasPanel`：工具栏加「局部编辑」按钮 + `_open_local_edit(_for)`；详情区列出已有编辑

### 1.4 导出标记（`canvas_export.py`）
- `export_svg` 对含 `local_edits` 的节点画**红色虚线遮罩叠加**（取首条 region）+「局部编辑 N」角标

---

## 2. 判据与扰动

| 套件 | 判据 | 结果 |
|---|---|---|
| `tests/test_canvas_localedit.py` | A 静态(10) + B 行为(26) + C 结构(5) = **46** | 46/46 ALL GREEN |
| `_perturb_canvas_localedit.py` | 7 个单点变异 + 反向基线 | **7/7 命中** + 基线绿 |

扰动命中目标：PG1→B1(不追加) / PG2→B2(不校验指令) / PG3→B4(Add.redo) /
PG4→B5(Remove.redo) / PG5→B6(Edit.redo) / PG6→B7b(norm_rect) / PG7→B12(export 不标记)。

### 全量回归（187 项，零回归）
model 32 · assetstore 24 · panel 30 · export 18 · edit 37 · localedit 46 —— 全部 ALL GREEN；
其余扰动脚本（export/edit/panel/assetstore/model）均全命中且基线绿。

---

## 3. 已留接口（后续步）
- **阶段 C（导出增强）**：把 `local_edits` 实际渲染成结果图（PNG），SVG 标记本步已完成。
- **第 5 步执行器**：`region` 经 `norm_rect(region, img_w, img_h)` 还原成像素遮罩；
  `transform` 走 Qt 区域处理，`inpaint` 调 Agnes/本地 SD-inpaint（指令+遮罩已就位，仅替换执行器）。

## 4. 未做（刻意收敛）
- 真实图像预览：stub 不产真图，遮罩编辑器用占位预览区；接真实产出后改 `MaskEditorWidget` 载图即可。
- 遮罩羽化/多遍精修：AI inpaint 接缝处理留第 5 步。
