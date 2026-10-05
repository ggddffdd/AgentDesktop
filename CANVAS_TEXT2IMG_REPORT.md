# v4.211.11 验证报告：画布「生图」接通 Agnes 纯文生图

## 一、背景

用户实弹反馈（2026-10-05，v4.211.10 发布后首日用）：

> 用是能用了，会生成渐变图，可能是我不会用。

实地核查结论：**不是操作问题，是功能未接通**。

- `executors.py` `_make_source_image`：docstring 自述「用 PIL 生一张源图（水平渐变 + prompt 文字标注）」——480×270 占位图，色相由 prompt 哈希决定。
- `agnes_bridge.py` 只有 3 个回调：`inpaint`（图生图重绘，payload 带 `extra_body.image`）/ `video` / `promo_motion`。**没有文生图**。

## 二、本轮改动

| 文件 | 改动 |
|---|---|
| `agnes_bridge.py` | 新增 `get_agnes_text2img_fn()`：与 inpaint 同端点 `/images/generations`、同模型 `agnes-image-2.5-flash`，区别仅 **payload 不带 `extra_body.image`**。复用 `_resolve_cred`（key 不出门）/ `_http_json` / `_MAX_RETRY`。 |
| `executors.py` | `gen_image_executor` 加 `text2img_fn`：注入且 prompt 非空 → 真生图，**异常诚实 failed（不静默回落）**；未注入/空 prompt → PIL 占位兜底。prompt 解析扩为「自身 config → 上游 prompt 端口（沿数据边）」。 |
| `canvas_graph.py` | `use_real_executors` 透传 `text2img_fn`；示例图 `src` 出厂默认 prompt（雪山松树林）。 |
| `canvas_panel.py` | worker / `_run_graph` 注入链补 `text2img_fn`。 |

**语义定版**：网络错必须让人看见（节点 failed），离线兜底只服务「没配 key」场景。

## 三、端点实探（2026-10-05）

- `POST /images/generations`（api.agnes-ai.cn，国内站会员 key）不带 image：
  **8.6s 返回，1024×1024 真图**（雪地柯基，1.29MB）——payload 形状实证可行。

## 四、判据与扰动

- 新套件 `tests/test_canvas_text2img.py` **24/0**（A 注入生效 / B 兜底语义 / C 诚实失败 / D 源码契约含 payload 防退化成改图 / E UI 全链路）。
- 新扰动 `_perturb_canvas_text2img.py` **8/8 全命中**（分支关死 / 上游解析删除 / 兜底改抛错 / payload 塞回 image / 装配层·worker 层·UI 层三处断链 / demo 无 prompt）。
- 既有判据同步：`test_canvas_rerun_refresh.py` / `test_canvas_async_run.py` 补断网 mock；`test_canvas_executor.py` C3 透传锚点扩 text2img_fn。
- 画布族 **12 套全绿**；清单 102 套件 / 34 扰动。

## 五、发布列车

- bump：config.py（BOM 保持）/ README.md / CHANGELOG.md → **v4.211.11**
- 打包：`build_safe.py` BUILD_EXIT=0（约 10 分钟）
- 进包核验：`_verify_pyz_v421111.py`（由 v421110 git mv 改版：版本断言 + 3h 新钉子段——包内 `_text2img` 体必须真引用 `images/generations`）
- 密钥扫描：`_scan_exe_secrets.py`
- 全量回归：真仓套件 + `%TEMP%` 副本扰动（两阶段）

## 六、已知的边界（如实记录）

1. 文生图 v1 固定 `size="1K"`（1024×1024 方图）；9:16 竖图待后续加参数（视频首帧用会裁剪）。
2. 示例图默认 prompt 是雪山松树林——大哥在「源·Prompt」节点或生图节点参数里改成自己的话即可。
3. 生图典型 9 秒，属后台线程执行，界面不卡（v4.211.10 机制）。
