# 画布「运行」重跑语义 + 自动重绘 + 参数键 —— 修复验证报告（v4.211.8）

> 日期：2026-10-04 ｜ 触发：大哥实测否证上一轮结论（「运行我是点过的」）
> 涉及源码：`canvas_panel.py` / `canvas_graph.py` / `task_graph.py`
> 判据：`tests/test_canvas_rerun_refresh.py`（28 项）｜ 扰动：`_perturb_canvas_rerun_refresh.py`（10 变异）

---

## 一、起因：一个被推翻的结论

上一轮教程里我写过「必须点『运行』才能落盘」。大哥回「运行我是点过的」，
并给出截图：`- img: 跳过（无编辑/无原图）`、`促销动效预览已生成`、
`用了 0 帧真实图片；跳过：1 个占位产物`。

于是实测复核 —— 结论反转：**「运行」按钮从上线起就没生效过**，
它一直在打印一句假的「运行完成」。

---

## 二、缺陷①：画布「运行」按钮静默假成功（最严重）

### 现象

| 观察项 | 实测结果 |
|---|---|
| `dist/小臭玩AI/canvas_runtime/` 递归 | **只有** `promo_preview.gif`（2425 B），**没有 `image/`** |
| 全盘搜 `img_source.png` | 命中全在 `%TEMP%/tmp*/` —— 即测试套件 `mkdtemp` 造的，**真实运行从未产出过** |
| `debug.log` / `grep -c canvas` | 日志停在 22:02，canvas 相关 0 条 |
| 解 PYZ（2306 模块） | `executors` / `PIL` / `numpy` 全在 → **排除打包缺件** |

### 根因链

```
build_demo()  = build_sample_graph() + run({})      ← stub 预跑，6 节点全变 completed
      ↓
点「运行」→ graph.run() → TaskGraph.run()
      ↓
ready = [t for t in tasks if t.is_ready(tasks)]
      而 is_ready() 第一句就是  if self.status != "pending": return False
      ↓
ready == []  →  all_done（全 completed）→ break
      ↓
真执行器**一次都没被调用**；UI 照样打印
「运行完成，产物落在: …」+ 6 条 stub 编造的 canvas_stage\... 路径
```

**连带症状**：任何已完成的节点都**永远重跑不了** —— 改 prompt 后再点运行，同样没反应。
根子在 `TaskGraph` **只有 `is_ready` 没有 `reset`** —— 缺「重跑」这个原语。

### 对照实验（三组，证明修法是必要条件而非摆设）

| 组 | 操作 | 结果 |
|---|---|---|
| ① | 建图后**不预跑**，直接装真执行器 `run` | ✅ 落盘 |
| ② | stub **预跑**后装真执行器 `run` | ❌ **0 文件**（路径纹丝不动）—— 复现缺陷 |
| ③ | 预跑后 **reset 回 pending** 再 `run` | ✅ 落盘 |

### 修法

- `task_graph.py`：新增 `TaskGraph.reset()` —— 全部任务归 `pending` + 清 `result`（走 `self._lock`，与 `run` 并发语义一致）。
- `canvas_graph.py`：新增 `CanvasGraph.reset_for_rerun()` —— 底层 `_tg.reset()` + 节点状态/`result`/`placeholder` 归零
  + **清 `out_assets`**（旧引用留着会被 `_wrap` 判成 stale —— 而这次是从头跑，不是"没重跑"）。
- `canvas_panel.py::_run_graph`：**先 `reset_for_rerun()` 再 `run()`**。

---

## 三、缺陷②：画布不自动重绘（含一条完全无效的支路）

### 现象

`render_graph` 从前只在 `CanvasPanel.__init__` 调一次。之后连/删线、撤销、跑完改状态
**都只改数据层，画面纹丝不动**。实测：删一条数据边后场景仍是 (6 节点, 7 连线)，而图内 `data_edges` 已是 5。

用户据此判断"操作没生效"，其实数据是对的 —— **只是没重画**。

### 附带查出的第二个"点了没反应"

`RemoveEdgeCommand.redo()` 只调 `graph.remove_data_edge(...)`，而**顺序边走的是另一套 API**
（`remove_order_edge` / `connect_order`）→ 选中灰色虚线点「删除选中连线」，`order_edges` 1 → 1，数据层纹丝不动。

### 修法

- `CanvasScene` 新增类级信号 `graphChanged`；`_finish_link` 末尾 `emit()`。
- `CanvasPanel._refresh_view()`：全量重建（`fit=False`，不重置缩放）+ **还原视图变换与被选中节点**
  （否则"一操作视图就跳回去"）。
- `_undo` / `_redo` 包一层：撤销/重做后必须重画。
- `render_graph(..., refresh_detail=True)` 新增开关 —— 重绘**不该冲掉详情区**
  （那里显示的是上一次操作的输出，如「运行完成，产物落在…」）。
- `RemoveEdgeCommand` 支持 `kind` / `label`，顺序边走 `remove_order_edge` / `connect_order`。

---

## 四、缺陷③：局部编辑「锐化」参数框填了等于没填

对话框写 `factor`，而引擎 `image_local_edit._pil_filter` 对 sharpen 读的是 `amount`
→ 静默回落默认 1.5。

**修法**：`build_edit` 按引擎口径分派 —— `blur` → `sigma`、`sharpen` → `amount`、其余 → `factor`。

---

## 五、判据（28 项 / 5 组，全绿）

`tests/test_canvas_rerun_refresh.py`（支持 `TG_PATH` / `CG_PATH` / `CP_PATH` 覆盖，供扰动指向被变异文件）

| 组 | 内容 | 关键项 |
|---|---|---|
| A 重跑语义（8） | 静态契约 + 预跑终态复现 + reset 归 pending / 清 out_assets / 返回值 | **A7 端到端落盘**、**A8 对照实验（不 reset → 0 文件）** |
| B `_run_graph` 端到端（4） | offscreen 真造 `CanvasPanel`，`os.chdir` 到临时目录后点运行 | **B1 磁盘真出现 `img_source.png`** |
| C 自动重绘（6） | 删数据边 / 删**顺序边** / 撤销 / detail 不被冲掉 / 信号契约 | **C1**、**C3** |
| D 参数键（4） | **行为验证**：UI 造的 params 喂 `il.apply_op`，结果须与默认值不同；并反证"写错键 = 等于没写" | sharpen / blur / brightness |
| E 源码契约（6） | `ast.parse` 限定在 `_run_graph` 函数体内（防"忘了调"，见 L270） | **E1 reset 早于 run** |

---

## 六、扰动自证（10 变异，全部命中，哑弹清零）

`_perturb_canvas_rerun_refresh.py`：反向基线 ALL GREEN + 10 个变异各自把目标判据打红。

```
反向基线（无变异，全跑）: OK ALL GREEN
  [OK ] PR1_run_no_reset         目标 E1/B1 ✓
  [OK ] PR2_no_tg_reset          目标 A7 ✓
  [OK ] PR3_run_self_reset       目标 A8 ✓
  [OK ] PR4_no_clear_assets      目标 A5 ✓
  [OK ] PR5_delete_no_refresh    目标 C1 ✓
  [OK ] PR6_order_edge_revert    目标 C3 ✓
  [OK ] PR7_undo_no_refresh      目标 C2 ✓
  [OK ] PR8_refresh_kills_detail 目标 C5 ✓
  [OK ] PR9_no_graphchanged      目标 C6 ✓
  [OK ] PR10_sharpen_key         目标 D sharpen ✓
PERTURB PASS=10 FAIL=0
```

### 两处口径收紧（比既有扰动脚本更严）

1. **必须目标判据本身翻红**。既有的 `_perturb_canvas_panel.py` 把 `rc != 0` 一律视为有效 ——
   于是一个变异只要弄红**任意一条**判据（哪怕不是目标那条）也算"命中"，会把哑弹掩盖成命中。
   本脚本改为：必须 `[FAIL] <目标>` 真的出现在输出里；只有整套崩到连 `PASS=` 统计行都没有（导入/语法错）
   才退化为 `rc != 0`。
2. **加了子进程超时（120s）**。见下。

### ⚠️ 踩到并记录的一个坑：变异让被测代码失去终止性

PR3 第一稿写的是「`is_ready` 不再要求 pending」：

```python
if self.status != "pending":   →   if False:  # 扰动：不要求 pending
```

这**直接死循环**：completed 的任务重新满足 `is_ready` → `run()` 的 `while True` 每轮都能捞到"就绪"任务
→ 反复重执行 → 子进程永不返回。

后果链很脏：整轮扰动被超时强杀 → **Windows 上是 `TerminateProcess`，没有可捕获信号、`finally` 不执行**
→ 护栏来不及还原 → 磁盘上留下 `if False:  # 扰动` 残留 → **后续任何一次单跑判据都被拖死**
（表现为"命令莫名被 SIGTERM 且零输出"）。

**教训**：变异必须落在**判据能观测到的位置**，而不是让被测代码**失去终止性** ——
后者测出来的是"挂起"，不是"判据变红"，还会污染下一轮。改用等效但会终止的变异：
`run()` 入口自动把任务重置回 pending（= "不 reset 也照跑"）。

配套加固：扰动脚本内 `subprocess.run(..., timeout=120)`，超时记为**失败**（"挂起不算翻红"），
不再是"整轮被拖死"。

---

## 七、修好后的实测

offscreen 真造 `CanvasPanel`，`Agnes 回调用桩故意失败`（只验本地链路）：

```
--- 点「运行」之后 ---
   | 运行完成，产物落在: …\canvas_runtime
   |   - src.prompt → …\canvas_runtime\prompt\src_prompt.node-placeholder
   |   - img.image  → …\canvas_runtime\image\img_source.png
  状态: {src: completed, img: completed, vid: failed,
         twin: pending, promo: pending, final: pending}
  磁盘: [('image\img_source.png', 1470), ('prompt\src_prompt.node-placeholder', 66)]

--- 点「促销动效预览」之后 ---
   | 促销动效预览已生成 → …\canvas_runtime\promo_preview.gif
   |   用了 1 帧真实图片。
  gif 大小: 368195 B
```

两条结论：

1. **「运行」真落盘了**（`img_source.png` 1470 B）。
2. **「促销动效预览」现在能吃到真帧**：从 2425 B 的纯渐变 → **368195 B** 的真帧动效。
   文档里"只得到纯渐变"那条边界随之**有条件解除** —— 正确用法是**先点「运行」再点预览**。

---

## 八、边界与未做项（不藏）

- **上游失败后下游不跑**：没配 Agnes key 时「生视频」失败 → 数字人口播 / 促销 / 成片都停在「待办」。
  不死锁（`run()` 在 `any_failed` 时 break），但也不会跳过失败点硬跑。这是**有意**的：不假装成功。
- **「生图」仍不是 AI 生图**：`gen_image` 用 PIL 现画渐变图（480×270 + prompt 前 40 字）。未动。
- **仍无「新建节点」/「打开保存工程」入口**：只存在 Python API。未动。
- **详情区首行占位文案**（「点击节点查看详情」）在运行后仍留在列表顶部，运行结果追加在其下。
  属既有行为（本次 `_refresh_view` 刻意不清详情区），未改。
- **`render_graph` 仍是全量重建**（非增量 diff）。6 节点量级无感知；节点上百时需要改增量。
- 上一轮把「画布可以做什么」的教程结论写错（"必须点运行才能落盘"），本轮已按实测更正
  `CANVAS_USER_GUIDE.md` 的 §二 / §三-7 / §三-8 / §五 / §八 / §九 / §十一。
