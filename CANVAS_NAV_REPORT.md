# 节点画布 · 挂主窗口导航（集成步）

> 设计稿 §9 第 3 步的"挂进主窗口 nav"收尾。**最小侵入**：只动导航契约三件套 + 一个薄 builder，不动 app shell 其它逻辑、不串页、不增启动成本。

## 改动清单（仅 `ui.py` + `canvas_panel.py` 文档注释）

### `ui.py`
1. **`NAV_GROUPS`**：`工作` 组由 `("对话","编排","军团")` → `("对话","编排","画布","军团")`。
2. **`_NAV_ICONS`**：新增 `"画布"` 键，配 Feather 线性 SVG（两节点+连线），与现有图标同风格。
3. **`nav_defs`**：在 `("编排","编排")` 后插入 `("画布","画布")`（第 3 项，nav index 2 → main_stack 页 3）。
4. **`main_stack`**：在 `orchestrate_page` 与 `legion_page` 之间新增 `canvas_page`（`QWidget` 外壳），下游页面注释页码 +1 对齐（页4→11）。
5. **`_ensure_lazy_page`**：tuple 追加 `(self.canvas_page, self._build_canvas_page, "canvas")`；沿用"控件对象匹配 + `_lazy_built` 去重"，不写死下标。
6. **新增 `_build_canvas_page`**：首次切到「画布」时由 `_ensure_lazy_page` 调一次；懒导入 `canvas_panel`（Qt 依赖隔离到切页），`build_demo()` 建示例流并 run 到终态 → `CanvasPanel(g)` 渲染，挂进 `canvas_page` 布局。

### `canvas_panel.py`
- 顶部"挂载说明"更新：由"暂不挂进主窗口 nav"改为"已通过 `_build_canvas_page` 挂进导航「工作」分组「画布」项"。

## 为什么这样改（守住契约）
- `_switch_nav(idx) → main_stack.setCurrentIndex(idx+1)`，nav index 与页面栈是**同一序列**。`画布` 插在第 3 位（idx 2 → 页 3），紧跟编排、同属"工作"心智；军团等后续项整体 +1，但 `_legion_nav_idx` 是动态捕获、其余 `_switch_nav` 调用只硬编码 `(0)`（对话），故**零串页**。
- `test_nav_ia_198.py` 的 **A4** 钉死：`NAV_GROUPS` 展平必须与 `nav_defs` 逐项相等 → 两边同步加"画布"，A4 仍绿。

## 验证结果
| 项 | 结果 |
|---|---|
| `python -m py_compile ui.py` | 语法 OK |
| `tests/test_nav_ia_198.py`（A/B/C/D 组，AST 无 Qt） | **22/22 全绿**（A4 展平==nav_defs 通过） |
| `tests/test_canvas_panel.py`（第3步判据） | 30/30 全绿（无回归） |
| `tests/test_canvas_assetstore.py`（第2步判据） | 24/24 全绿 |
| `tests/test_canvas_model.py`（第1步判据） | 32/32 全绿 |
| `tests/test_probe_graphics.py`（三坑探针） | 63/63 全绿 |
| `_build_canvas_page` 数据路径（懒导入 + `build_demo`） | 与第3步预览入口同款，逻辑层已 30/30 覆盖 |

> 注：本沙箱对 Qt 部件实例化的进程创建有拦截策略（`python -c`/脚本触发 `decisionRecord missing`），故**实时开窗口的 offscreen 冒烟无法在此跑通**——这是环境限制非代码缺陷。`CanvasPanel` 在 §9 第3步已通过 `python canvas_panel.py` 独立预览验证可渲染；挂入主窗口走的是与 `_build_legion_page` 等 9 个已验证 builder 完全同款的懒导入模式，风险面极小。

## 提交
- 本地提交（未推；push 仍受 Security Center 程序黑名单拦 `reg.exe/sc.exe`，沿用"大哥手动推"）。
- 领先 `origin/main` 共 4 个提交：`3da2924`(第1步) + `a9a2f37`(第2步) + `c53fda9`(第3步) + 本次(挂nav)。

## 下一步（设计稿 §9）
- **第 4 步 渠道层** / **第 5 步 接真执行器**（Agnes 生图/生视频真正落盘，把 stub executor 换成 `legion_worker`/`video_pipeline`）/ **第 6 步 促销动效从零写** / **第 7 步 闸门 UI**。
- 当前画布页展示的是 `build_demo()` 示例流；接真执行器后，`_build_canvas_page` 只需把 `build_demo()` 换成"从工程文件/真实任务图加载 `CanvasGraph`"，渲染层无需改动。
