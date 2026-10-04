# 画布「运行」后台线程化 — 验证报告（v4.211.10）

> 2026-10-05 · 响应用户实弹反馈："我点击生图，点运行程序就卡死了"

## 一、缺陷定位（证据链）

**用户现象**：选中「生图」节点 → 点工具栏「运行」→ 弹出「小臭玩AI.exe 未响应」。

**根因**（三事实连证）：

1. `_run_graph()` 是按钮槽函数，`self.graph.run({})` 在**GUI 线程上同步逐节点执行**——
   事件循环被占住，Windows 5 秒无消息即判「未响应」。
2. 示例流的 `gen_video` 节点会真调 Agnes 视频 API（用户已配 key）：
   提交（timeout 120s）→ **轮询最多 60 次 × 每 8 秒**（agnes_bridge.py:249-260）→
   下载（timeout 300s）。**典型 1~5 分钟，最坏 30+ 分钟**。
3. **为什么 v4.211.8 之前没发现**：那时「运行」是假成功（一个节点都不执行），
   从不联网。v4.211.8 修好假成功后按钮真的开始跑图 → 结构性问题随之暴露。
   用户点的是「生图」（PIL 本地秒级），但「运行」跑的是**整张图**——
   卡住的是排在后面的「生视频」。

## 二、修法（结构性：进程内拆分）

```
运行按钮 ─→ _run_graph()（GUI 线程：按钮态切换 + 防编辑锁定）
                │ 创建
                ▼
        _GraphRunWorker(QThread)（后台线程）
                │ reset_for_rerun → use_real_executors → graph.run({}, token)
                │ 信号（自动排队回 GUI 线程）
                ▼
        succeeded/failed/cancelled → 详情区 + 列产物
        finished → 统一收口：按钮恢复 + 解锁编辑 + _refresh_view()
```

| 防线 | 实现 |
|---|---|
| 不卡界面 | QThread 后台执行；实测运行期间 QTimer/缩放/平移照常 |
| 可停止 | 「停止」按钮 + `CancellationToken`（TaskGraph 原生 3 检查点：派发前/submit 前/执行器开头）；下游绝不冒充 completed |
| 防数据竞争 | `scene.run_active` 守卫：`_apply_menu_choice` / `keyPressEvent` / `_finish_link` / `_delete_selected` 四入口 + 撤销重做/预览按钮禁用 |
| 重复运行防护 | `worker.isRunning()` 检查，运行中再点提示而非二次起线程 |
| 退出保护 | `aboutToQuit → cancel + wait(5000)`，防 QThread 带线程销毁崩溃 |
| 失败吸收 | 执行器异常 → 节点级 failed（TaskGraph 既有语义），线程不崩 |

**诚实边界**：停止是协作式的——已在跑的节点（正在轮询的视频）会做完当前步骤才停，
不半途杀线程（强杀留半截产物与脏状态）。

## 三、验证

| 项 | 结果 |
|---|---|
| 行为探针（offscreen，慢 video_fn 模拟联网） | 23/23：非阻塞/事件循环活/落盘/停止/失败吸收 |
| 判据 `tests/test_canvas_async_run.py`（A–E 5 组） | **PASS=34 FAIL=0**（含 ★ 端到端：运行期间 QTimer 照常触发） |
| 扰动 `_perturb_canvas_async_run.py`（10 变异） | **10/10 全命中 + 反向基线 ALL GREEN** |
| 既有判据更新 | `test_canvas_rerun_refresh.py` B/E 组随异步化重锚定（29/0，含新 E1b「防退回同步跑」） |
| 画布族全量（11 套） | 全部通过 |
| 进包核验 `_verify_pyz_v421110.py` | 见 CHANGELOG（本轮发布列车） |

### 扰动过程中抓到的两个判据缺陷（已修）

1. **PA1 超时**：`worker.start()` 一处变异影响 A/B/C/D 四组（都走 `_run_graph`），
   30s 兜底 × 4 = 超时被记「挂起」而非「翻红」→ 兜底收至 8s（正常 ≤3s，2.5 倍裕量）。
2. **PA8 空断言**：D3 判据没先选中边，`_delete_selected` 循环空转 → 守卫拆了也绿；
   且首条边可能是顺序边（删了动 `order_edges` 而非 `data_edges`）。
   修法：先真选中一条**数据边** + 断言两类边都不变。

## 四、遗留与边界

- 「生视频」轮询不可中断（agnes_bridge 不感知 token）——停止语义止于节点边界。
  Web 画布（已立项）的后端任务管理会覆盖此边界。
- 运行期间节点仍可拖动（只改 pos 标量，无结构竞争，可接受）。
- PySide6 画布已定格为原型，后续投入转向 **Web 画布**（FastAPI 复用
  canvas_graph/task_graph/executors 核心层 + React Flow 前端，先本地后云端）。
