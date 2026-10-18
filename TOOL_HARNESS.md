# TOOL_HARNESS.md — 新工具接入规范（v4.286.0）

> 面向「给小臭加/改一个工具」的完整检查单。方法论骨架借鉴 HKUDS/CLI-Anything
> 的 7-phase harness 思路，四层测试设计（unit / 判据 / 扰动 / **真后端探针**）
> 是照小臭自己的事故史长的肉——**库存不借，方法论借**。
>
> 立档缘起：彻查「判断不准 / 工具调用准确性」双问题（2026-10-16/17）时发现，
> 事故从来不是「某个 bug」，而是「接入流程缺层」：edge-tts 403（缺真后端层）、
> Agnes 端点迁移（缺真后端层）、工具重复（缺描述分工判据）、日志只记工具名
> （缺参数/成败记录）。本文把修复沉淀成**下次接入不再犯**的流程。

---

## 1. 七阶段总览

| 阶段 | 产出 | 不满足不放行的硬条件 |
|---|---|---|
| ① Analyze | 注册点 + 风险分级 + 是否外部依赖的定性 | 说清「这工具挂了用户看到什么」 |
| ② Design | description（含分工边界）+ 参数 schema | 与易混邻居**互点分工**（见 §3） |
| ③ Implement | handler + 权限闸门接线 | 内部消息/异常路径都有兜底 |
| ④ Test | **四层测试**（见 §2） | 真外部依赖必须配探针，否则不许标「完成」 |
| ⑤ Document | CHANGELOG 条目 + 必要时 DESIGN.md | 事故教训写进注释（版本号+日期） |
| ⑥ Publish | 版本 bump（11 处真源）+ 打包 + CI 绿 | `test_version_single_source_235` 兜底 |
| ⑦ Verify | frozen_smoke 对**新 exe** 复验 | 311 项对齐新版本号 |

---

## 2. 四层测试（本规范的 core）

| 层 | 位置 | 测什么 | 不测什么 |
|---|---|---|---|
| L1 unit | 判据套件内 monkeypatch | 分支逻辑、边界、异常路径 | 外部后端真实行为 |
| L2 判据 | `tests/test_*.py` | 行为断言（改了坏行为就红） | 源码关键词在场（L190/L201 教训） |
| L3 扰动 | `_perturb/_perturb_*.py` | **改坏源码 → 判据必须红** | 正向功能（那是 L2 的事） |
| L4 真后端 | `probe_real_backend.py` + nightly | 真调外部 API，验「通 + 结构」 | 内容质量（另一层的事） |

**L4 铁律**（v4.286.0 立，源自 edge-tts 403 / Agnes 401 两次事故；v4.287.0 补第三起——**secret 配了但无消费者**）：

1. **真外部依赖（HTTP 出网的 tool）必须进探针清单**——没配 key 时 SKIP，
   **SKIP 不冒充通过**（横幅 + rc=0，永不静默变绿）；
2. 前提齐（key/依赖都在）而真调失败 → **FAIL，fail-closed 撞红**，
   任何「额度豁免」「降级 WARNING」的豁免逻辑都不许进探针层
   （v4.283.1：edge-tts 403 曾被 `tts_failure_is_quota` 降级成 WARNING，
   「日志干净但没声」就是那么来的）；
3. 探针自身崩了也算 FAIL——裸异常不许穿透成「看似没跑」。

---

## 3. 易混工具互点分工（v4.285.0 立）

模型选错工具，九成不是模型笨，是 description 没给足判别信息。
彻查实测：27 个工具曾是一句话描述（`db_delete` 仅 5 字符「删除记录。」）。

**规矩**：名字/功能相邻的工具（≥2 个同族），description 必须包含三要素——

1. **用途**：这个工具干什么（一句话）；
2. **参数语义**：关键参数怎么填、默认行为（含「先清空再输入」这类坑）；
3. **分工边界**：明写「**何时用邻居 X，何时用本工具**」。

机检判据（`tests/test_tool_desc_285.py`）：长度下限 60 + 20 个易混工具逐一
断言「description 点名了分工邻居」。新工具入族时同步加进该判据的点名清单。

---

## 4. 日志纪律（v4.284.0 立）

旧日志 9852 条只记「执行工具: X」，「传错参数 / 是否重复执行 / 成没成功」
全部无法定论。现在 `exec_tool` 两分支（registry / MCP）口径统一：

- **入口行**：`执行工具: {name} | args={摘要}`——截断 300 字符，
  过 `sanitize_for_model`（sk-/cpk- 类凭据不进日志）；
- **出口行**：`工具完成: {name} | ok={bool} | {单行结果摘要}`——截断 120。

新工具**自动继承**这两行（在 `exec_tool` 统一出口），无需自己记。

---

## 5. 新工具接入检查单（逐项打勾才许标完成）

```text
□ ① 注册：@register_tool("name", risk=...)；风险分级如实（read/write/dangerous）
□ ① 定性：是否 HTTP 出网？——是则必须进 probe_real_backend.py 清单
□ ② description 三要素：用途 + 参数语义 + 分工边界（点名邻居）
□ ② 若与既有工具同族：tests/test_tool_desc_285.py 点名清单加它
□ ③ handler 异常路径有兜底；内部消息不回写 session（_is_sync_writable）
□ ④ L1/L2 判据：tests/test_<name>_<ver>.py，PASS=/FAIL= 统计 + 非 0 退出
□ ④ L3 扰动：_perturb/_perturb_<name>_<ver>.py，改坏必红（SKIP 算哑弹）
□ ④ L4 探针（仅外部依赖）：probe_* 函数 + 三态语义（PASS/FAIL/SKIP）
□ ⑤ CHANGELOG 条目；事故教训写注释（版本号+日期+根因一句）
□ ⑥ 版本 bump 11 处常量 + README；全量回归 tests/run_all.py 全绿
□ ⑥ 打包 build_safe.py（app 运行时先关，WinError 32）
□ ⑦ frozen_smoke 对新 exe 复验；密钥扫描 _scan_exe_secrets.py
```

---

## 6. 参照实现（照抄这几份的形）

| 需求 | 照抄谁 |
|---|---|
| 判据套件（结构+行为两组） | `tests/test_tool_desc_285.py`（机检点名）、`tests/test_tool_log_284.py` |
| 真后端探针 + 三态语义 | `probe_real_backend.py` + `tests/test_probe_real_backend_286.py` |
| 扰动脚本（改坏必红） | `_perturb/_perturb_tool_log_284.py`（模板最简） |
| nightly workflow（真跑+secrets） | `.github/workflows/real-backend-nightly.yml` |
| 工具分工描述范例 | `system_control_tools.py` 的 mouse/keyboard 族（v4.285.0 重写） |
