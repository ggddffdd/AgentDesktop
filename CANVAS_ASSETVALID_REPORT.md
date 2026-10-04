# 节点画布 · 资产有效性统一入口 交付报告（v4.211.0）

> 范围：把画布产物的「算不算数」从**散落在执行器内部**收拢成**唯一判定入口**，
> 并区分 **真实产物 / 占位产物 / 历史产物 / 失效产物** 四态；同时修掉全库旧版
> Python（3.10/3.11）语法不兼容与工程文件导入校验的缺口。
> 关联：`CANVAS_EXECUTOR_REPORT.md`（第 5 步执行器）· `CANVAS_EXPORT_REPORT.md`（导出）·
> `CANVAS_WAVE_CD_REPORT.md`（Wave C/D 语义显式化）。

---

## 1. 动因（复审 6 条 + 1 条自查）

复审给画布模块的结论是「已从功能原型进入可用版本」，同意主线 **A+B** 两档收口：

| 编号 | 问题 | 本报告处置 |
|---|---|---|
| A1 | `canvas_panel.py` 用了 Python 3.12 风格 f-string（PEP 701），与 README 声明的 3.10+ 矛盾 | **已修**（实际范围远超复审估计，见 §3.1） |
| A2 | `_incoming_assets()` 类型注释不准确（实际单入→`AssetRef`/`None`、多入→`list`） | **已修** |
| A3 | 导入校验可更严（`version` / `status` / `pos` / `local_edits` / 边重复 / `multi=False` 端口多连 / `placeholder` 布尔） | **已修**（7 项） |
| B | `_register_asset()` 对**自定义执行器**的输出没有统一做文件校验 | **已修**（统一入口 + 四态） |
| — | 占位文件扩展名仍像真实媒体文件（`.mp4`/`.png`） | **已修**（改名 + meta 标记） |
| — | 测试偏静态契约、缺真实回归 | **补强**（新增 42 项判据 + 25 项扰动） |

---

## 2. 交付物

### 2.1 A1 · 全库旧版 Python 语法兼容（PEP 701）

**生产源码共 13 处、4 个文件**：

| 文件 | 处数 | 形态 |
|---|---|---|
| `canvas_panel.py` | 3 | `stroke="{THEME['canvas_failed']}"` 单引号嵌在单引号 f-string 内 |
| `digital_twin_panel.py` | 4 | `THEME["danger_bg2"]` 双引号嵌在双引号 f-string 内 |
| `director_panel.py` | 5 | `THEME["live_green"]` / `THEME["danger_text"]` 同上 |
| `ui.py` | 1 | `THEME['font_micro']` 同上 |

修法两种：**提前取变量**（读起来更清楚，`canvas_panel.region_svg_overlay` 用这种）
或**换成异类引号**（改动最小）。另有 19 处位于未跟踪的备份副本
（`_bak_*` / `_cur_*` / `_rw_dry` / `_ui_199_keep.py`）—— `git ls-files` 为空、
打包 spec 也不收编，不修。

### 2.2 A2 · `_incoming_assets()` 类型注释

```python
Dict[str, Union[AssetRef, List[AssetRef], None]]
```
并补 docstring 说明「单入端口 → `AssetRef` 或 `None`；多入端口 → `list`（按端口声明顺序）」。

### 2.3 A3 · 导入校验 7 项

`canvas_export._validate_project(gd, path, version=None)`：

| # | 校验 | 不校验的后果 |
|---|---|---|
| 1 | `version` 必须在 `SUPPORTED_VERSIONS=(1,2)` | 未知版本被静默按旧结构读，新增字段无声消失 |
| 2 | `status` 必须在 `task_graph.VALID_STATUSES`（唯一事实源） | 拼错的状态落到「未知颜色」，看画布看不出异常 |
| 3 | `placeholder` 必须是布尔 | `bool("false") == True` → 语义静默反转 |
| 4 | `config.local_edits` 结构（`_validate_local_edits`） | 非法编辑指令留到运行时才炸 |
| 5 | `pos` 必须是 `[x, y]` 或 `null` | 位置莫名其妙 |
| 6 | `pos` 两个值都必须是数字（不是 `"10"`） | 字符串会在布局层当数字参与运算 |
| 7 | 边重复 / 多入端口被重复连（`_validate_data_edges`） | 同一端口两条边，运行时才拒绝 |

`task_graph.py` 新增 `VALID_STATUSES` 作为**状态枚举唯一事实源**（此前 `canvas_export`
要校验 `status` 却无处可引）。

### 2.4 B 档 · 统一资产有效性入口（核心）

`canvas_graph.py`：

| 符号 | 作用 |
|---|---|
| `CanvasAssetError(RuntimeError)` | 节点产出不可用时的诚实失败 |
| `ASSET_REAL / ASSET_PLACEHOLDER / ASSET_STALE / ASSET_INVALID` | 四态常量 |
| `ASSET_VALIDITIES` | 四态元组（顺序即判定优先级文档） |
| `assess_asset(ref, asset_root=None) -> (validity, reason)` | **唯一判定入口** |
| `AssetRef.validity / invalid_reason / stale` | 判定结果持久化（`to_dict()` 同步） |
| `CanvasGraph.asset_root` | 统一入口需要的目录围栏根 |
| `CanvasGraph.audit_assets()` | 按四态 + `unassessed` 分档计数 |

**判定顺序**（先问「这是什么」再问「文件在不在」）：

1. 不是 `AssetRef` / 路径为空 → `invalid`
2. `placeholder=True` → `placeholder`（**不参与 invalid 判定**，见 §3.2）
3. `stale=True` → `stale`
4. 不存在 / 为空 / 读不了 / 越出资产目录 → `invalid`
5. 其余 → `real`

`_register_asset()` 开头统一调 `assess_asset`，并把结果写进 `ameta` 留痕；
`_wrap()` 在节点真跑完后体检，**路径非空**且 `invalid` → 抛 `CanvasAssetError`（节点 failed）。

### 2.5 占位文件不再伪装成媒体文件

`executors.py`：

- `PLACEHOLDER_EXT = ".node-placeholder"`、`PLACEHOLDER_CONTENT_TYPE = "placeholder"`；
- `stage_path(..., ext=None)` 新增 `ext` 形参（默认仍按 kind 的 `_EXT`）；
- `passthrough_executor()` 落盘用 `.node-placeholder`，meta 加 `"content_type"`。

占位物的内容本来就是一段文本 manifest，起名成 `.mp4`/`.png` 会让系统与外部播放器
把它当真媒体打开，报一个与真实问题毫无关系的解码错误。

---

## 3. 设计决策与踩坑（本轮最值钱的部分）

### 3.1 「判据陷阱」：`ast.parse(feature_version=...)` 抓不到 PEP 701

想守 PEP 701 的第一反应是 `ast.parse(src, feature_version=(3,10))`。**实测它抓不到** ——
3.10 / 3.11 / 3.12 三个 `feature_version` 全部解析通过。
`feature_version` 影响的是语法特性集，**管不到 f-string 的 tokenizer 行为**。

最终用两条腿：

- **权威核验**：下载官方 embeddable `python-3.10.11-embed-amd64.zip` /
  `python-3.11.9-embed-amd64.zip`，用它们的 `python.exe` 对全库（326 个文件）
  逐个 `compile()` 体检 → 归零；
- **长期守门**：`tests/pep701_lint.py`（纯标准库）走 `tokenize` 的
  `FSTRING_START` / `FSTRING_MIDDLE` / `FSTRING_END` 做**词法级**扫描，
  私有 `_quote_of()` 剥前缀取引号形态，嵌套栈比对同类引号 + 检测表达式内反斜杠。

> 顺带确认：README 的「Python 3.10+」声明**现在名实相符**，保留不改。

### 3.2 `placeholder` **绝不参与** invalid 判定

stub 的产物路径是编出来的、**磁盘上根本没有这个文件**。若统一入口按「文件必须存在」
判，纯 stub 的图会**集体 failed** —— 而这恰恰是画布最常见的首次使用状态。
故判定顺序把 `placeholder` 放在「文件在不在」之前。

### 3.3 `invalid` 的语义边界：只在**路径非空**时才算「假完成」

`path` 为空 = 压根没给引用 → 归 `incomplete` 机制管，**不 failed**。
第一版没加这个条件，`test_canvas_assetstore.py` 的 B6 立刻翻红
（`path=""` 的节点也被判 failed）—— 判据抓对了，改实现。

### 3.4 历史产物标记（stale）必须在「真跑的节点」上

第一版把 stale 标记放在 `run()` 开头的 `_mark_stale_before_run()`。**错**：
任务跑完是终态，再 `run` 不重跑任何任务 —— 于是「根本没重跑」的产出被错报成历史产物。
已回退，改为在 `_wrap()` 里标记（只有真正执行的节点才标）。
`test_canvas_assetvalid` 的 B15 用 `g5._wrap(n5)({})` 模拟「重跑一个节点」来钉这条语义。

### 3.5 统一入口**不**接管「扩展名与 kind 相符」

`validate_output()` 还查一条：扩展名必须与 kind 相符（`_OUTPUT_EXTS`）。
统一入口**不带**这条 —— 它是 kind 契约，属执行器层；把它搬进 `canvas_graph`
需要让 `canvas_graph` 反向依赖 `executors._OUTPUT_EXTS`（而 `executors` 已经依赖
`canvas_graph` 取 `AssetRef`），要么复制一份常量造成**双事实源**。两者都比留一个
清楚的边界更糟。**故明确记为已知边界**：自定义执行器的产出**不做扩展名校验**，
只做「存在 / 非空 / 不越界」。

---

## 4. 判据与扰动（改坏必红）

### 4.1 新增判据 `tests/test_canvas_assetvalid.py`（42 项）

| 组 | 项数 | 覆盖 |
|---|---|---|
| A | 5 | PEP 701 检测器自证 + 生产源码零残留（扫描根可用 `PEP701_ROOT` 替换） |
| B | 15 | 四态判定 + 自定义执行器端到端 + `audit_assets` + stale 语义 |
| C | 15 | 导入校验 7 项（含「错误要能定位」） |
| D | 4 | 占位命名（`.node-placeholder` + `content_type`） |
| E | 3 | `multi` 端口收 `list` 的类型注释契约 |

### 4.2 新增扰动 `_perturb_canvas_assetvalid.py`（25 项全命中 + 8 条反向基线）

17 个变异（M1~M17）+ 8 条反向基线（A1/B2/B9/B10/C2/C9/D1/E1）。
统一走 `_perturb_guard.arm()`（快照 + SIGTERM/atexit 还原 + 残留预检）。

### 4.3 本轮扰动脚本**抓出的三个问题**（这就是「改坏必红」的价值）

1. **两个锚点漂移**：`_perturb_canvas_export.py` 的 M10/M11 因本轮给
   `_validate_project` 加 `version` 参数而失配 → 修正锚点。
2. **两个变异变成哑弹**：`_perturb_canvas_executor.py` 的 PG11/PG12
   （原为「删掉 gen_video/promo_fx 的 `validate_output` 调用」）不再翻红 ——
   因为「假完成」缺陷现在有**两道相互独立、各自充分**的防线（执行器自查 + 统一入口），
   单点变异**永远**不可能翻红。这不是判据变弱，但不处理就无法排除「断言已空转」。
   → 给扰动框架新增**双点变异** `_pg_multi(desc, check, edits)`：两道一起拆，
   判据必须翻红。改后 PG11/PG12 均命中（18/18）。
3. **一条判据被证实是「谓词空转」**（最严重的发现）：
   `tests/test_canvas_export.py` 的 **A9** 用 `re.search(r"_validate_project\(gd,\s*path", src)`
   证明「导入前先做结构预检」。但这个串**同样出现在函数定义行**
   `def _validate_project(gd, path: str, version=None) -> None:` 上 ——
   于是把调用**整行删掉**，A9 照样绿。M10 变异把它抓了出来。
   → 收紧为 `r"^\s+_validate_project\(gd,\s*path"`（行首缩进 + 函数名，定义行首是 `def`，
   天然被排除），既排除定义行又保留「签名长参数不误伤」的原意。

### 4.4 扰动框架的稳定性加固

`_pg` / `_pg_multi` 现在对「首跑绿」的 case **复跑一次**再判：
Windows 下刚写回的 `.py` 偶发被 Defender / 索引短暂占用，子进程会读到旧内容
（实测出现过一次**假哑弹**）。首跑绿、复跑红的情况单列成 `FLAKY` 打印出来，
而不是静默吞掉 —— 不改变判定结果，但不掩盖现象。

---

## 5. 回归基线（v4.211.0）

| 项 | 结果 |
|---|---|
| 全量回归 `tests/run_all.py` | **92 套件 / 3640 项 / FAIL=0** |
| 画布扰动 `--perturb-only canvas` | **10 个脚本 / 147 项全命中 / FAIL=0** |
| `_perturb_guard.py` 残留预检 | 干净 |
| `ui_hex_guard.py` 配色门禁 | 通过（扫描 50 个 UI 源文件，新增违规 0） |
| 生产源码 PEP 701 | 0 处（真 3.10/3.11 解释器体检 + 词法扫描双确认） |
| 进包核验 `_verify_pyz_v42110.py` | 见发布核验（字节码指纹 + 四态钉子） |
| 密钥扫描（源码侧） | 无真实密钥（命中项全为测试假 key 与扫描器词法误报） |

> 说明：一轮 `--perturb-only canvas` 曾出现 `test_video_director_fix_188.py` 偶发失败，
> 单跑 31/31、`run_all --only video_director` 连跑 3 次 31/31 全绿 ——
> 该套件的 E3 是 **0.5s 级时序断言**，在紧接重载之后的运行里会抖。非本轮引入。

---

## 6. 未做（明确留待下轮）

- **占位文件的旧工程兼容**：本轮把占位扩展名从 `.mp4`/`.png` 改成
  `.node-placeholder`。**旧工程文件里记的是老路径** —— 导入旧工程时那些占位引用
  仍会指向 `.mp4`。当前判定把 `placeholder=True` 排在「文件在不在」之前，
  所以不会误判成 invalid；但**UI 若按扩展名选预览器**，旧占位仍可能被当媒体打开。
- **扩展名与 kind 相符的校验未并入统一入口**（见 §3.5，属有意保留的边界）。
- 复审建议里剩余的测试补强：分别用 3.11 / 3.12 导入 `canvas_panel` 的冒烟、
  占位 `.mp4` 被媒体预览时的处理、删除边后确认无残留调度依赖。
